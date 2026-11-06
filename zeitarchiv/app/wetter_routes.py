"""Wetterdashboard: Rollen (Außentemperatur, Niederschlag, …) zuordnen, daraus Kacheln
und eine Vergleichstabelle (Heute, Gestern, Monat, Vormonat, Jahr — jeweils mit Vorjahr).

Modul-Muster wie energiedashboard_routes.py: Konfiguration als JSON-Blob in der
``settings``-Tabelle mit ``schema_version``, ein Aktivierungsschalter, Routen im
Dienstobjekt. Die Rolle legt die Aggregation fest, nicht der Nutzer; das Setup prüft den
Typ der Entität und warnt, statt falsch zu rechnen (ROADMAP 1.43).

Die Tabelle rechnet ihre Kennzahlen selbst aus den Stundenzeilen (Messwerte, Schalter) bzw.
Tageszeilen (Zähler) und nimmt, anders als ``/api/query-table``, keinen Randpunkt in
Ø/Min/Max auf. Das Datum eines Extremums leitet sich aus ``bucket_start`` ab (stundengenau).

Private Funktionen aus ``storage.query`` (``_window``, ``_shift_year``,
``_query_completed_plus_live_fine``) werden wie im Energiedashboard bewusst direkt genutzt.
"""

from __future__ import annotations

from .i18n import N_, tr

import bisect
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import period_columns, selection_catalog
from . import wetter_analysis as analysis
from . import wetter_autofill
from . import wetter_conditions as conditions
from . import wetter_daylight as daylight
from . import wetter_pv as pv_mod
from .chart_period import calendar_bounds
from .formatting import decimals_to_int, entity_display_name
from .period_columns import MODES, parse_stichtag
from .storage import query as query_mod
from .storage import rollup as rollup_mod
from .storage.index import Index

SETTING_ENABLED = "wetterdashboard_enabled"
SETTING_CONFIG = "wetterdashboard_config"
CONFIG_SCHEMA_VERSION = 1

#: Rollen mit festem Typ: (Schlüssel, Beschriftung, erlaubte Aggregationstypen). Die
#: Außentemperatur ist Pflicht, alles andere optional. ``extra_temperatures`` (weitere
#: Thermometer als eigene Zeilen) steht daneben, weil es eine Liste ist.
ROLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("temperature", N_("Außentemperatur"), ("standard",)),
    ("precip_mm", N_("Niederschlag (Menge)"), ("counter",)),
    ("precip_dur", N_("Regen (ein/aus)"), ("switch",)),
    ("humidity", N_("Luftfeuchte"), ("standard",)),
    ("pressure", N_("Luftdruck"), ("standard",)),
    ("wind", N_("Wind"), ("standard",)),
    ("gust", N_("Böe"), ("standard",)),
    ("radiation", N_("Strahlung"), ("standard",)),
    ("illuminance", N_("Helligkeit"), ("standard",)),
    ("illuminance_dawn", N_("Helligkeit Dämmerung"), ("standard",)),
    ("wind_direction", N_("Windrichtung"), ("standard",)),
    ("sun_elevation", N_("Sonnenhöhe"), ("standard",)),
    ("pv_power", N_("PV-Leistung"), ("standard",)),
)
#: Erwartete Einheiten je Rolle: Entitäten damit stehen in der Auswahl zuerst (kein harter Filter,
#: weil eine Einheit fehlen oder ungewöhnlich geschrieben sein kann).
ROLE_UNITS = {
    "temperature": ["°C", "°F", "K"],
    "precip_mm": ["mm", "in"],
    "humidity": ["%"],
    "pressure": ["hPa", "mbar", "Pa", "kPa", "inHg"],
    "wind": ["km/h", "m/s", "mph", "kn"],
    "gust": ["km/h", "m/s", "mph", "kn"],
    "radiation": ["W/m²"],
    "illuminance": ["lx"],
    "illuminance_dawn": ["lx"],
    "wind_direction": ["°"],
    "sun_elevation": ["°"],
    "pv_power": ["kW", "W", "MW"],
    "extra": ["°C", "°F", "K", "%"],
    "heating": ["kWh", "Wh", "MWh"],
}
ROLE_KEYS = tuple(key for key, _label, _types in ROLES)
ROLE_TYPES = {key: types for key, _label, types in ROLES}
ROLE_LABELS = {key: label for key, label, _types in ROLES}

MAX_EXTRA_TEMPERATURES = 5
#: Blöcke der Vergleichstabelle, die sich in der Einrichtung ausblenden lassen (die Außentemperatur
#: und Zusatzsensoren bleiben immer sichtbar).
#: Zeiträume (Tage) des Temperaturvergleichs auf dem Reiter „Temperatur & Klima“.
TEMPERATURE_DAYS = (7, 30, 90)
BLOCK_KEYS = ("kenntage", "precip_mm", "precip_dur", "humidity", "pressure", "wind", "gust", "radiation", "illuminance")
#: Karten, die sich in der Einrichtung ein- und ausblenden lassen: (Schlüssel, Name, Rollen, von denen mindestens
#: eine zugeordnet sein muss; „@extras“ und „@heating“ stehen für die Listen weiterer Sensoren und Heizzähler).
CARD_OPTIONS = (
    ("conditions", N_("Wetterlage"), ("illuminance", "illuminance_dawn", "radiation", "wind", "precip_dur", "precip_mm")),
    ("basis", N_("Heute und Vergleichstabelle"), ("temperature", "precip_mm", "precip_dur", "humidity", "pressure", "wind", "gust", "radiation", "illuminance")),
    ("monthly", N_("Monatsvergleich"), ("temperature", "precip_mm", "precip_dur")),
    ("precip", N_("Niederschlag"), ("precip_mm", "precip_dur")),
    ("klima", N_("Temperatur & Klima"), ("@extras",)),
    ("wind", N_("Wind & Sonne"), ("wind", "gust", "radiation", "illuminance")),
    ("sunhours", N_("Sonnenstunden"), ("illuminance",)),
    ("daylight", N_("Tageslicht"), ("illuminance_dawn",)),
    ("dayimage", N_("Tagesbild"), ("illuminance",)),
    ("pvlight", N_("PV und Helligkeit"), ("@pv",)),
    ("longterm", N_("Langzeit"), ("temperature", "precip_mm", "precip_dur")),
    ("heating", N_("Heizen"), ("@heating",)),
)
CARD_KEYS = tuple(key for key, _label, _needs in CARD_OPTIONS)
#: Gliederung der Kartenauswahl nach Reiter des Dashboards (Reitername, Karten). „heating“ fehlt hier
#: absichtlich: Der Reiter „Heizen“ ist experimentell und steht in der Einrichtung in einem eigenen Block.
CARD_GROUPS = (
    (N_("Übersicht"), ("conditions", "basis", "dayimage", "monthly")),
    (N_("Niederschlag"), ("precip",)),
    (N_("Temperatur & Klima"), ("klima",)),
    (N_("Wind & Sonne"), ("wind", "sunhours", "daylight")),
    (N_("Langzeit"), ("longterm",)),
)
#: Nachkommastellen, wenn die Entität „automatisch“ eingestellt ist: Strahlung in ganzen W/m².
ROLE_DEFAULT_DECIMALS = {"radiation": 0}
#: Kurze Namen für die Kacheln (in der Tabelle stehen die ausführlichen Rollennamen).
TILE_LABELS = {
    "temperature": N_("Temperatur"), "precip_mm": N_("Niederschlag"), "precip_dur": N_("Regen"),
    "illuminance": N_("Sonnenschein"),
}
#: PV und Helligkeit: Leistungseinheiten (Faktor nach kW) und Anzahl der verglichenen Tage.
PV_UNIT_KW = {"W": 0.001, "kW": 1.0, "MW": 1000.0}
PV_DAYS = 14
#: Tagesbild: Anzahl Tage im Streifen; Tage mit weniger hellen Stunden bekommen keine Klasse.
DAYIMAGE_DAYS = 14
DAYIMAGE_MIN_BRIGHT_HOURS = 3
MAX_HEATING_COUNTERS = 6
DEFAULT_HEATING_ROOM = 20.0  # Raumtemperatur der Gradtagzahl (G20/15, VDI 3807)
DEFAULT_HEATING_LIMIT = 15.0  # Heizgrenze: darüber wird nicht geheizt
COLUMN_KEYS = ("heute", "gestern", "monat", "vormonat", "jahr")
#: Spalte -> (Zeitraum für query_mod._window, Versatz)
COLUMN_WINDOWS = {
    "heute": ("day", 0),
    "gestern": ("day", -1),
    "monat": ("month", 0),
    "vormonat": ("month", -1),
    "jahr": ("year", 0),
}
#: Sonnenschein zählt, wenn die Süd-Helligkeit im Stundenmittel über dieser Schwelle liegt (lx). Kein
#: offizieller DWD-Wert (der zählt ab 120 W/m² direkter Strahlung): die Station trennt direktes und diffuses
#: Licht nicht. Startwert aus den Oktoberdaten des Nutzers: trübe Tage mittags bis etwa 15.000 lx, klare ab 50.000 lx.
DEFAULT_SUN_THRESHOLD_LX = 25000.0
SUN_THRESHOLD_RANGE_LX = (1000.0, 100000.0)
#: Tageslichtkurve: Der Dämmerungssensor der Eltako-Station misst 0 bis 1.000 lx. „Nacht“ liegt unter der unteren
#: Schwelle (ungefähr das Ende der bürgerlichen Dämmerung), „Tag“ ab der oberen (ungefähr Sonnenauf- und -untergang).
DEFAULT_DAYLIGHT_NIGHT_LX = 5.0
DEFAULT_DAYLIGHT_DAY_LX = 400.0
DAYLIGHT_NIGHT_RANGE_LX = (1.0, 200.0)
DAYLIGHT_DAY_RANGE_LX = (50.0, 990.0)
DAYLIGHT_COMPARE_DAYS = 7  # Tage in der Vergleichstabelle unter der Kurve
DAYLENGTH_MIN_HOURS = 12  # Stunden mit Werten, ab denen ein Tag für den Jahresverlauf zählt
DAYLENGTH_MAX_DAYS = 800  # so weit reicht der Jahresverlauf höchstens zurück (laufendes Jahr, Vorjahr und etwas mehr)
#: Süd-Helligkeit der Eltako-Station sättigt hier; darüber steht „ab“ statt eines Messwerts.
SUN_SENSOR_SATURATION_LX = 98800
SUN_MAX_GAP_HOURS = 6  # längste Lücke ohne Zeilen, die zwischen zwei sonnigen Stunden als sonnig gilt
#: Darstellung der Karte Wetterlage: ``large`` (zentrierter Kopf, vier Kacheln), ``compact`` (Kopf in einer Zeile,
#: niedrigere Kacheln), ``strip`` (Kopf in einer Zeile, Kacheln als eine Leiste). Dieselben Angaben, nur anders gesetzt.
LAGE_LAYOUTS = ("large", "compact", "strip")
DEFAULT_LAGE_LAYOUT = "strip"
DEFAULT_RAIN_DAY_MM = 1.0  # DWD: Niederschlagstag ab 1 mm
DEFAULT_RAIN_DAY_MINUTES = 10


def _empty_config() -> dict:
    return {
        **{key: "" for key in ROLE_KEYS},
        "extra_temperatures": [],
        "heating": [],
        "heating_room": DEFAULT_HEATING_ROOM,
        "heating_limit": DEFAULT_HEATING_LIMIT,
        "rain_day_mm": DEFAULT_RAIN_DAY_MM,
        "rain_day_minutes": DEFAULT_RAIN_DAY_MINUTES,
        "sun_threshold_lx": DEFAULT_SUN_THRESHOLD_LX,
        "daylight_night_lx": DEFAULT_DAYLIGHT_NIGHT_LX,
        "daylight_day_lx": DEFAULT_DAYLIGHT_DAY_LX,
        "sky_cuts_lx": [],
        "hidden_blocks": [],
        "hidden_cards": [],
        "lage_layout": DEFAULT_LAGE_LAYOUT,
    }


def card_available(key: str, config: dict) -> bool:
    """Ob die Karte etwas zeigen kann: mindestens eine ihrer Rollen (oder Listen) ist belegt."""
    needs = next((roles for card, _label, roles in CARD_OPTIONS if card == key), ())
    for token in needs:
        if token == "@extras":
            if config["extra_temperatures"]:
                return True
        elif token == "@heating":
            if config["heating"]:
                return True
        elif token == "@pv":
            if config["pv_power"] and config["illuminance"]:
                return True
        elif config.get(token):
            return True
    return False


def _load_config(index: Index) -> dict:
    config = _empty_config()
    raw = index.get_setting(SETTING_CONFIG, "")
    if not raw:
        return config
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return config
    if not isinstance(data, dict):
        return config
    for key in ROLE_KEYS:
        if isinstance(data.get(key), str):
            config[key] = data[key]
    extras = data.get("extra_temperatures")
    if isinstance(extras, list):
        config["extra_temperatures"] = [
            {"entity_id": str(row.get("entity_id") or ""), "name": str(row.get("name") or "")}
            for row in extras
            if isinstance(row, dict) and row.get("entity_id")
        ][:MAX_EXTRA_TEMPERATURES]
    heating = data.get("heating")
    if isinstance(heating, list):
        config["heating"] = [
            {"entity_id": str(row.get("entity_id") or ""), "name": str(row.get("name") or "")}
            for row in heating
            if isinstance(row, dict) and row.get("entity_id")
        ][:MAX_HEATING_COUNTERS]
    hidden = data.get("hidden_blocks")
    if isinstance(hidden, list):
        config["hidden_blocks"] = [key for key in BLOCK_KEYS if key in hidden]
    hidden_cards = data.get("hidden_cards")
    if isinstance(hidden_cards, list):
        config["hidden_cards"] = [key for key in CARD_KEYS if key in hidden_cards]
    if data.get("lage_layout") in LAGE_LAYOUTS:
        config["lage_layout"] = data["lage_layout"]
    for key in ("heating_room", "heating_limit"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 40:
            config[key] = float(value)
    for key in ("rain_day_mm", "rain_day_minutes"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            config[key] = value
    value = data.get("sun_threshold_lx")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and SUN_THRESHOLD_RANGE_LX[0] <= value <= SUN_THRESHOLD_RANGE_LX[1]:
        config["sun_threshold_lx"] = float(value)
    cuts = data.get("sky_cuts_lx")
    if (
        isinstance(cuts, list) and len(cuts) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cuts)
        and all(SUN_THRESHOLD_RANGE_LX[0] <= v <= SUN_THRESHOLD_RANGE_LX[1] for v in cuts)
        and cuts[0] < cuts[1] < cuts[2]
    ):
        config["sky_cuts_lx"] = [float(v) for v in cuts]
    night, day = data.get("daylight_night_lx"), data.get("daylight_day_lx")
    if (
        all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (night, day))
        and DAYLIGHT_NIGHT_RANGE_LX[0] <= night <= DAYLIGHT_NIGHT_RANGE_LX[1]
        and DAYLIGHT_DAY_RANGE_LX[0] <= day <= DAYLIGHT_DAY_RANGE_LX[1]
        and night < day
    ):
        config["daylight_night_lx"], config["daylight_day_lx"] = float(night), float(day)
    return config


def sky_cuts(config: dict) -> tuple[float, float, float]:
    """Untergrenzen von bewölkt, heiter und sonnig in lx: die eigenen, sonst Vielfache der Sonnen-Schwelle."""
    own = config.get("sky_cuts_lx")
    if own:
        return (own[0], own[1], own[2])
    return conditions.lx_cuts(config["sun_threshold_lx"])


def _save_config(index: Index, config: dict) -> None:
    index.set_setting(
        SETTING_CONFIG,
        json.dumps({**config, "schema_version": CONFIG_SCHEMA_VERSION}, ensure_ascii=False),
    )


def _is_enabled(index: Index) -> bool:
    return index.get_setting(SETTING_ENABLED, "0") == "1"


def _is_configured(config: dict) -> bool:
    return bool(config.get("temperature"))


def wetter_role_count(index: Index) -> int:
    """Anzahl zugeordneter Rollen (Kachel auf der Dashboards-Seite)."""
    config = _load_config(index)
    return (
        sum(1 for key in ROLE_KEYS if config.get(key))
        + len(config.get("extra_temperatures") or []) + len(config.get("heating") or [])
    )


def radiation_entity(index: Index) -> str:
    """Entität der Strahlung (W/m²) oder leer; das Energiedashboard braucht sie für den Ertrag je Einstrahlung."""
    return _load_config(index).get("radiation") or ""


def is_wetter_configured(index: Index) -> bool:
    return _is_configured(_load_config(index))


def _role_warning(index: Index, role_key: str, entity_id: str) -> str | None:
    """Passt die Entität zum Typ der Rolle? None = in Ordnung."""
    if not entity_id:
        return None
    entity = index.get_entity(entity_id)
    if entity is None:
        return tr("Entität ist nicht im Archiv.")
    if entity["aggregation_type"] not in ROLE_TYPES.get(role_key, ("standard",)):
        return tr("Typ „{type}“ passt nicht zu dieser Rolle.", type=entity["aggregation_type"])
    return None


def _parse_bounded(raw: str, default: float, bounds: tuple[float, float]) -> float:
    """Zahl aus einem Formularfeld (leer = Standard); außerhalb der Grenzen oder keine Zahl: HTTP 400."""
    try:
        value = float(raw.replace(",", ".").replace(" ", "")) if raw.strip() else default
    except ValueError:
        raise HTTPException(status_code=400, detail=tr("Ungültige Zahl: {value}", value=raw)) from None
    low, high = bounds
    if not low <= value <= high:
        raise HTTPException(
            status_code=400,
            detail=tr("Wert außerhalb von {low} bis {high}: {value}", low=f"{low:g}", high=f"{high:g}", value=raw),
        )
    return value


def _decimals(entity, role_key: str) -> int | None:
    """Einstellung der Entität; bei „automatisch“ der Standard der Rolle (None = der Browser wählt)."""
    configured = decimals_to_int(entity["decimals"])
    return configured if configured is not None else ROLE_DEFAULT_DECIMALS.get(role_key.split(":")[0])


class TableRequest(BaseModel):
    columns: list[str] = Field(default_factory=lambda: list(COLUMN_KEYS))
    vorjahr: bool = True
    mode: str = "bis_jetzt"
    #: Stichtag (JJJJ-MM-TT): die Tabelle zeigt dann die Zeiträume zu diesem Tag statt zu „jetzt“.
    at: str | None = None


@dataclass
class WetterDependencies:
    data_dir: Path
    index: Index
    tz: ZoneInfo
    templates: Jinja2Templates
    app_root_context: Callable[[Request], dict]


class WetterService:
    def __init__(self, deps: WetterDependencies) -> None:
        self.deps = deps

    # -- Zeilen ------------------------------------------------------------------------

    def _row_specs(self, config: dict) -> list[dict]:
        """Zeilengruppen der Tabelle in fester Reihenfolge; nur zugeordnete Rollen."""
        index = self.deps.index
        specs: list[dict] = []

        def add(key: str, entity_id: str, label: str, kind: str) -> None:
            entity = index.get_entity(entity_id)
            if entity is None or entity["aggregation_type"] not in ROLE_TYPES.get(key.split(":")[0], ("standard",)):
                return
            specs.append({
                "key": key,
                "entity_id": entity_id,
                "label": label,
                "kind": kind,
                "unit": entity["unit"] or "",
                "decimals": 0 if kind == "switch" else _decimals(entity, key),
            })

        def role_label(key: str) -> str:
            return tr(ROLE_LABELS[key])

        # Anzeigename statt Rollenname: Kacheln und Tabellengruppen stehen in Großbuchstaben, und aus
        # „Außentemperatur“ würde „AUSSENTEMPERATUR“, was wie ein Tippfehler aussieht.
        add("temperature", config["temperature"], tr("Temperatur außen"), "standard")
        for position, extra in enumerate(config["extra_temperatures"]):
            entity = index.get_entity(extra["entity_id"])
            name = extra["name"] or (
                entity_display_name(extra["entity_id"], entity["friendly_name"], entity["custom_name"])
                if entity else extra["entity_id"]
            )
            add(f"temperature:{position}", extra["entity_id"], name, "standard")
        add("precip_mm", config["precip_mm"], role_label("precip_mm"), "counter")
        add("precip_dur", config["precip_dur"], role_label("precip_dur"), "switch")
        for key in ("humidity", "pressure", "wind", "gust", "radiation"):
            add(key, config[key], role_label(key), "standard")
        # Die Helligkeit erscheint als Sonnenscheindauer (Stunden), nicht als lx-Kennzahlen.
        add("illuminance", config["illuminance"], tr("Sonnenscheindauer"), "sunshine")
        return specs

    def _rows(
        self, entity_id: str, aggregation_type: str, start: datetime, end: datetime,
        now_local: datetime, read_cache: query_mod.QueryReadCache,
    ) -> list[rollup_mod.FineRow]:
        if end <= start:
            return []
        return query_mod._query_completed_plus_live_fine(  # noqa: SLF001 — siehe Modul-Docstring
            self.deps.data_dir, self.deps.index, entity_id, aggregation_type, "line",
            start, end, self.deps.tz, now_local, read_cache,
        )

    def summarize(
        self, kind: str, rows: list[rollup_mod.FineRow], config: dict, held_off: bool = False,
        unit: str | None = None, until: float | None = None,
    ) -> dict | None:
        """Kennzahlen einer Zeilenmenge; None, wenn es keine Werte gibt (nie 0 vortäuschen).

        held_off (nur Schalter): Das Fenster liegt hinter dem ersten Wert und enthält keine Zeile, weil
        kein Wechsel stattfand. Ein Schalter hält seinen Zustand, hier „aus“: 0 Minuten statt „–“."""
        if kind == "standard":
            values = [row.value for row in rows if row.value is not None]
            if not values:
                return None
            highs = [
                (row.max_value if row.max_value is not None else row.value, row.bucket_start)
                for row in rows if row.value is not None
            ]
            lows = [
                (row.min_value if row.min_value is not None else row.value, row.bucket_start)
                for row in rows if row.value is not None
            ]
            # Gleichstand: erstes Auftreten (min/max über (Wert, Zeit) nimmt die frühere Zeit).
            high = max(highs, key=lambda pair: (pair[0], -pair[1]))
            low = min(lows, key=lambda pair: (pair[0], pair[1]))
            return {
                "avg": sum(values) / len(values),
                "max": high[0], "max_ts": high[1],
                "min": low[0], "min_ts": low[1],
            }
        if kind == "sunshine":
            return self._summarize_sunshine(rows, config, until)
        if kind == "counter":
            days = [(row.value or 0.0, row.bucket_start) for row in rows]
            if not days:
                return None
            # Die Schwelle steht in mm; ein Zähler in Zoll braucht sie in Zoll.
            wet = analysis.mm_to_unit(float(config["rain_day_mm"]), unit)
            top = max(days, key=lambda pair: (pair[0], -pair[1]))
            return {
                "sum": sum(value for value, _ts in days),
                "days": sum(1 for value, _ts in days if value >= wet),
                "top": top[0] if top[0] > 0 else None, "top_ts": top[1] if top[0] > 0 else None,
            }
        # Schalter: Einschaltdauer je Stunde -> Minuten je lokalem Kalendertag
        tz = self.deps.tz
        if held_off and not rows:
            return {"minutes": 0.0, "days": 0, "top": None, "top_ts": None}
        per_day: dict[date, float] = {}
        first_ts: dict[date, float] = {}
        for row in rows:
            day = datetime.fromtimestamp(row.bucket_start, tz).date()
            per_day[day] = per_day.get(day, 0.0) + (row.on_seconds or 0.0) / 60.0
            first_ts.setdefault(day, row.bucket_start)
        if not per_day:
            return None
        wet = float(config["rain_day_minutes"])
        top_day = max(per_day, key=lambda day: (per_day[day], -day.toordinal()))
        return {
            "minutes": sum(per_day.values()),
            "days": sum(1 for minutes in per_day.values() if minutes >= wet),
            "top": per_day[top_day] if per_day[top_day] > 0 else None,
            "top_ts": first_ts[top_day] if per_day[top_day] > 0 else None,
        }

    def _sunshine_per_day(
        self, values: list[rollup_mod.FineRow], threshold: float, until: float | None,
        held: list[tuple[float, float]] | None = None,
    ) -> dict[date, float]:
        """Sonnensekunden je lokalem Kalendertag aus den nach Zeit sortierten Stundenzeilen (Regeln siehe
        ``_summarize_sunshine``)."""
        tz = self.deps.tz
        per_day: dict[date, float] = {}

        def count(bucket_start: float, bright: bool) -> None:
            day = datetime.fromtimestamp(bucket_start, tz).date()
            seconds = 3600.0
            if until is not None:
                seconds = max(0.0, min(3600.0, until - bucket_start))
            per_day[day] = per_day.get(day, 0.0) + (seconds if bright else 0.0)

        for position, row in enumerate(values):
            count(row.bucket_start, row.value >= threshold)
            following = values[position + 1] if position + 1 < len(values) else None
            if following is None or row.value < threshold or following.value < threshold:
                continue
            missing = round((following.bucket_start - row.bucket_start) / 3600.0) - 1
            if 0 < missing <= SUN_MAX_GAP_HOURS:
                for step in range(1, missing + 1):
                    count(row.bucket_start + step * 3600.0, True)
                    if held is not None:
                        held.append((row.bucket_start + step * 3600.0, row.value))
        return per_day

    def _summarize_sunshine(self, rows: list[rollup_mod.FineRow], config: dict, until: float | None) -> dict | None:
        """Sonnenstunden: Stundenzeilen, deren Mittel die Schwelle erreicht, je lokalem Kalendertag.

        Eine angeschnittene Stunde (laufende Stunde, Ende des Fensters) zählt nur mit ihrem Anteil bis
        ``until``, sonst würde „bis 14:32“ bis zu eine Stunde zu viel zeigen. Ohne Werte ``None``, nie 0:
        ein Tag ohne Daten hatte nicht null Sonne. Genauigkeit: eine Stunde (die Mittel der Stunden entscheiden).

        Lücken: Ein Sensor, der stundenlang denselben Wert hält (die Süd-Helligkeit klebt mittags bei
        98.800 lx), löst in HA kein neues Ereignis aus, dann fehlen genau an den sonnigsten Stunden die
        Zeilen. Eine Lücke von höchstens ``SUN_MAX_GAP_HOURS`` Stunden zählt als sonnig, wenn die Zeile davor
        und die danach die Schwelle erreichen; an Rändern und neben dunklen Stunden zählt sie nicht."""
        values = sorted((row for row in rows if row.value is not None), key=lambda row: row.bucket_start)
        if not values:
            return None
        tz = self.deps.tz
        per_day = self._sunshine_per_day(values, float(config["sun_threshold_lx"]), until)
        hours = {day: seconds / 3600.0 for day, seconds in per_day.items()}
        top_day = max(hours, key=lambda day: (hours[day], -day.toordinal()))
        top_start = min(row.bucket_start for row in values if datetime.fromtimestamp(row.bucket_start, tz).date() == top_day)
        return {
            "hours": sum(hours.values()),
            "top": hours[top_day] if hours[top_day] > 0 else None,
            "top_ts": top_start if hours[top_day] > 0 else None,
        }

    # -- Spalten -----------------------------------------------------------------------

    def column_windows(self, key: str, now_local: datetime, mode: str, anchor: datetime | None = None) -> dict:
        """Zeitfenster einer Spalte und ihres Vorjahrespartners (Rechnung in period_columns)."""
        return period_columns.column_windows(COLUMN_WINDOWS, key, now_local, mode, anchor)

    def compute_table(self, body: TableRequest, now_local: datetime | None = None) -> dict:
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        columns = [key for key in COLUMN_KEYS if key in body.columns]
        mode = body.mode if body.mode in MODES else "bis_jetzt"
        stichtag = parse_stichtag(body.at, now_local.date())
        anchor = datetime(stichtag.year, stichtag.month, stichtag.day, 12, tzinfo=self.deps.tz) if stichtag else None
        windows = [self.column_windows(key, now_local, mode, anchor) for key in columns]
        read_cache = query_mod.QueryReadCache()
        rows_out = []
        hidden = set(config["hidden_blocks"])
        for spec in self._row_specs(config):
            if spec["key"] in hidden:
                continue
            entity = self.deps.index.get_entity(spec["entity_id"])
            first_ts = entity["first_ts"] if entity else None
            cells: dict[str, dict] = {}
            for win in windows:
                cell: dict = {}
                for side, start, end in (
                    ("cur", win["start"], win["end"]),
                    *((("vj", win["vj_start"], win["vj_end"]),) if body.vorjahr else ()),
                ):
                    # Die Sonnenscheindauer liest die Stundenmittel eines Messwerts (kind „sunshine“ ist nur die Anzeigeart).
                    aggregation = "standard" if spec["kind"] == "sunshine" else spec["kind"]
                    rows = self._rows(spec["entity_id"], aggregation, start, end, now_local, read_cache)
                    cell[side] = self.summarize(
                        spec["kind"], rows, config, held_off=bool(first_ts and first_ts < start.timestamp()),
                        unit=spec["unit"], until=min(end.timestamp(), now_local.timestamp()),
                    )
                    # Fehlende Daten sind kein 0: Beginn des Archivs mitgeben, wenn er im Fenster liegt.
                    if cell[side] is None and first_ts and first_ts > start.timestamp():
                        cell[f"{side}_from"] = first_ts
                cells[win["key"]] = cell
            rows_out.append({**spec, "cells": cells})
            if spec["key"] == "temperature" and "kenntage" not in hidden:
                rows_out.append(self._kenntage_row(spec, windows, body.vorjahr, now_local, read_cache))
        return {
            "tz": str(self.deps.tz),
            "now": now_local.timestamp(),
            "mode": mode,
            "at": stichtag.isoformat() if stichtag else None,
            "today": now_local.date().isoformat(),
            "vorjahr": body.vorjahr,
            "rain_day_mm": config["rain_day_mm"],
            "rain_day_minutes": config["rain_day_minutes"],
            "sun_threshold_lx": config["sun_threshold_lx"],
            "columns": [
                {
                    "key": win["key"], "range": win["range"], "partial": win["partial"],
                    "cur": {"start": win["start"].timestamp(), "end": win["end"].timestamp()},
                    "vj": {"start": win["vj_start"].timestamp(), "end": win["vj_end"].timestamp()},
                }
                for win in windows
            ],
            "rows": rows_out,
        }

    def _kenntage_row(
        self, spec: dict, windows: list[dict], with_vorjahr: bool, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> dict:
        """Frost-, Eis-, Sommer- und heiße Tage als Zeilen unter der Außentemperatur. Nur Monats- und
        Jahresspalten (an einem einzelnen Tag ist das keine Kennzahl); der laufende Tag zählt nie."""
        tz, today = self.deps.tz, now_local.date()
        cells: dict[str, dict] = {}
        for win in windows:
            if win["range"] == "day":
                cells[win["key"]] = {}
                continue
            cell: dict = {}
            for side, start, end in (
                ("cur", win["start"], win["end"]),
                *((("vj", win["vj_start"], win["vj_end"]),) if with_vorjahr else ()),
            ):
                points = query_mod.query_daily_series(
                    self.deps.data_dir, self.deps.index, spec["entity_id"], start, end, tz, now_local, read_cache,
                )
                cell[side] = analysis.count_kenntage(
                    points, tz, start.timestamp(), end.timestamp(), today, unit=spec["unit"],
                )
            cells[win["key"]] = cell
        return {
            "key": "kenntage", "entity_id": spec["entity_id"], "label": tr("Kenntage"), "kind": "kenntage",
            "unit": "", "decimals": 0, "cells": cells,
        }

    # -- Auswertungen über viele Jahre (Phase 2) ----------------------------------------

    def _precip_role(self, config: dict, mode: str) -> tuple[str, str] | None:
        """(Entität, Art) der Niederschlagsrolle; ``mm`` = Zähler, ``dur`` = Regensensor. Fehlt die
        gewünschte Art, gilt die andere; ohne beide None."""
        roles = {"mm": "precip_mm", "dur": "precip_dur"}
        for kind in (mode, "dur" if mode == "mm" else "mm"):
            entity_id = config.get(roles[kind])
            entity = self.deps.index.get_entity(entity_id) if entity_id else None
            if entity is not None and entity["aggregation_type"] in ROLE_TYPES[roles[kind]]:
                return entity_id, kind
        return None

    def _daily(self, entity_id: str, now_local: datetime) -> list:
        entity = self.deps.index.get_entity(entity_id)
        first = entity["first_ts"] if entity else None
        if first is None:
            return []
        start = datetime.fromtimestamp(first, self.deps.tz).replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return query_mod.query_daily_series(
            self.deps.data_dir, self.deps.index, entity_id, start, now_local, self.deps.tz, now_local,
            query_mod.QueryReadCache(),
        )

    def available_precip(self) -> list[str]:
        """Zugeordnete Niederschlagsarten in fester Reihenfolge (Menge zuerst)."""
        config = _load_config(self.deps.index)
        return [kind for kind in ("mm", "dur") if (self._precip_role(config, kind) or ("", ""))[1] == kind]

    def compute_precip(self, mode: str, now_local: datetime | None = None) -> dict | None:
        config = _load_config(self.deps.index)
        role = self._precip_role(config, mode)
        if role is None:
            return None
        entity_id, kind = role
        now_local = now_local or datetime.now(self.deps.tz)
        factor = 1.0 / 60.0 if kind == "dur" else 1.0  # Schalter liefern Sekunden, gezeigt werden Minuten
        daily = {
            analysis.day_of(point, self.deps.tz): (point.value or 0.0) * factor
            for point in self._daily(entity_id, now_local)
        }
        threshold = float(config["rain_day_minutes"] if kind == "dur" else config["rain_day_mm"])
        entity = self.deps.index.get_entity(entity_id)
        # Verglichen wird in der Einheit der Entität; angezeigt (threshold unten) bleibt der eingestellte Wert in mm.
        wet_threshold = threshold if kind == "dur" else analysis.mm_to_unit(threshold, entity["unit"] if entity else None)
        archive_start = (
            datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date() if entity and entity["first_ts"] else None
        )
        result = analysis.precipitation(daily, now_local.date(), wet_threshold, hold_to_end=kind == "dur", archive_start=archive_start)
        return {
            **result, "mode": kind, "unit": "min" if kind == "dur" else (entity["unit"] or "mm"),
            "threshold": threshold, "entity_id": entity_id, "tz": str(self.deps.tz),
            "available": self.available_precip(),
        }

    def compute_monthly(self, metric: str, mode: str = "mm", now_local: datetime | None = None) -> dict | None:
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        if metric == "sun":
            return self._monthly_sun(config, now_local)
        if metric == "temperature":
            entity_id, kind, unit = config["temperature"], "mean", ""
        else:
            role = self._precip_role(config, mode)
            if role is None:
                return None
            entity_id, mode_kind = role
            kind, unit = "sum", ("min" if mode_kind == "dur" else "mm")
        entity = self.deps.index.get_entity(entity_id) if entity_id else None
        if entity is None:
            return None
        points = self._daily(entity_id, now_local)
        if metric != "temperature" and unit == "min":
            points = [replace(point, value=(point.value or 0.0) / 60.0) for point in points]
        today = now_local.date()
        dry_start = None
        if metric != "temperature" and unit == "min" and entity["first_ts"]:
            dry_start = datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date()
        result = analysis.monthly(points, self.deps.tz, today, kind, dry_start)
        result.update(metric=metric, unit=unit or entity["unit"] or "", entity_id=entity_id, tz=str(self.deps.tz))
        result["headline"] = analysis.headline(points, self.deps.tz, today) if metric == "temperature" else None
        return result

    def _monthly_sun(self, config: dict, now_local: datetime, days: list[dict] | None = None) -> dict | None:
        """Sonnenstunden je Monat und Jahr (Form wie beim Monatsvergleich), Summe der Tage mit Werten."""
        entity_id = config["illuminance"]
        if not entity_id or self.deps.index.get_entity(entity_id) is None:
            return None
        days = self.sun_history(config, now_local) if days is None else days
        result = analysis.monthly(analysis.sun_points(days, self.deps.tz, "hours"), self.deps.tz, now_local.date(), "sum")
        result.update(metric="sun", unit="h", entity_id=entity_id, tz=str(self.deps.tz), headline=None)
        return result

    def heating_counters(self, config: dict) -> list[dict]:
        """Heizzähler der Konfiguration, die es im Archiv als Zähler gibt (Name, Einheit, Faktor nach kWh)."""
        counters = []
        for row in config.get("heating") or []:
            entity = self.deps.index.get_entity(row["entity_id"])
            if entity is None or entity["aggregation_type"] != "counter":
                continue
            unit = entity["unit"] or "kWh"
            counters.append({
                "entity_id": row["entity_id"], "unit": unit,
                "name": row["name"] or entity_display_name(row["entity_id"], entity["friendly_name"], entity["custom_name"]),
                "scale": {"Wh": 0.001, "MWh": 1000.0}.get(unit, 1.0),
            })
        return counters

    def compute_heating(self, now_local: datetime | None = None) -> dict | None:
        """Tage der Heizperiode mit Außentemperatur und kWh je Heizzähler. Gradtagzahl, Ausgleichsgerade
        und Monatstabelle rechnet der Browser daraus, damit Heizgrenze und Raumtemperatur ohne Rundlauf
        änderbar sind (siehe static/js/wetter-heating.js)."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        counters = self.heating_counters(config)
        temperature = config["temperature"]
        if not counters or not temperature or self.deps.index.get_entity(temperature) is None:
            return None
        days = analysis.heating_days(
            self._daily(temperature, now_local), [self._daily(c["entity_id"], now_local) for c in counters],
            [c["scale"] for c in counters], self.deps.tz, now_local.date(),
        )
        return {
            "counters": [{"entity_id": c["entity_id"], "name": c["name"]} for c in counters],
            "days": days, "room": config["heating_room"], "limit": config["heating_limit"],
            "sun": self._heating_sun(days, config, now_local),
            "today": now_local.date().isoformat(), "tz": str(self.deps.tz),
        }

    def _heating_sun(self, days: list[list], config: dict, now_local: datetime) -> dict[str, str]:
        """Tagesklasse (``sunny``/``mixed``/``overcast``) der Heiztage, soweit die Helligkeit sie hergibt.

        Dieselbe Regel wie beim Tagesbild (``_sun_days``); Tage ohne Klasse fehlen. Ohne Rolle Helligkeit oder
        ohne Heiztage ist das Ergebnis leer, der Reiter Heizen bleibt dann unverändert."""
        entity = self.deps.index.get_entity(config["illuminance"]) if config["illuminance"] else None
        if entity is None or not entity["first_ts"] or not days:
            return {}
        archive_start = datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date()
        first = max(date.fromisoformat(days[0][0]), archive_start)
        last = date.fromisoformat(days[-1][0])
        if first > last:
            return {}
        wanted = {day[0] for day in days}
        return {
            entry["date"].isoformat(): entry["class"]
            for entry in self._sun_days(first, last, config, now_local)
            if entry["class"] and entry["date"].isoformat() in wanted
        }

    def compute_matrix(self, metric: str, mode: str = "mm", now_local: datetime | None = None) -> dict | None:
        """Jahr×Tag-Matrix. metric: ``avg``/``max``/``min`` (Außentemperatur) oder ``precip``."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        today = now_local.date()
        if metric == "sun":
            return self._matrix_sun(config, now_local)
        if metric == "precip":
            role = self._precip_role(config, mode)
            if role is None:
                return None
            entity_id, kind = role
            entity = self.deps.index.get_entity(entity_id)
            start = datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date() if entity["first_ts"] else None
            result = analysis.matrix(
                self._daily(entity_id, now_local), self.deps.tz, today, "value",
                scale=1.0 / 60.0 if kind == "dur" else 1.0, archive_start=start,
                dry_fill=kind == "dur", require_coverage=False,
            )
            unit = "min" if kind == "dur" else (entity["unit"] or "mm")
        else:
            entity_id = config["temperature"]
            entity = self.deps.index.get_entity(entity_id) if entity_id else None
            if entity is None:
                return None
            field = {"avg": "value", "max": "max_value", "min": "min_value"}.get(metric)
            if field is None:
                raise ValueError(metric)
            start = datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date() if entity["first_ts"] else None
            result = analysis.matrix(
                self._daily(entity_id, now_local), self.deps.tz, today, field, archive_start=start,
            )
            unit = entity["unit"] or "°C"
        return {**result, "metric": metric, "unit": unit, "entity_id": entity_id, "today": today.isoformat(), "tz": str(self.deps.tz)}

    def _matrix_sun(self, config: dict, now_local: datetime) -> dict | None:
        """Jahr×Tag-Matrix der Tagesklassen (0 trüb, 1 wechselhaft, 2 sonnig); ``hours`` nennt die Sonnenstunden je Tag."""
        entity_id = config["illuminance"]
        entity = self.deps.index.get_entity(entity_id) if entity_id else None
        if entity is None:
            return None
        days = self.sun_history(config, now_local)
        today = now_local.date()
        start = datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date() if entity["first_ts"] else None
        result = analysis.matrix(
            analysis.sun_points(days, self.deps.tz, "class"), self.deps.tz, today, "value", archive_start=start,
        )
        hours = {entry["date"].isoformat(): round(entry["hours"], 1) for entry in days if entry["class"]}
        return {
            **result, "metric": "sun", "unit": "", "entity_id": entity_id, "today": today.isoformat(),
            "tz": str(self.deps.tz), "hours": hours,
        }

    def compute_temperatures(self, days: int, now_local: datetime | None = None) -> dict | None:
        """Tagesmittel der Außentemperatur und aller weiteren Sensoren für die letzten ``days`` Tage."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        all_specs = self._row_specs(config)
        # Ein weiterer Sensor mit „%“ ist ein Feuchtesensor: Er bekommt eine eigene Achse, nicht die der Temperaturen.
        specs = [spec for spec in all_specs if spec["key"].split(":")[0] == "temperature" and spec["unit"] != "%"]
        humidity_specs = [
            spec for spec in all_specs
            if (spec["key"].split(":")[0] == "temperature" and spec["unit"] == "%")
        ]
        if not specs and not humidity_specs:
            return None
        if humidity_specs:
            humidity_specs = [spec for spec in all_specs if spec["key"] == "humidity"] + humidity_specs
        today = now_local.date()
        dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]

        def build(chosen: list[dict], first_key: str) -> list[dict]:
            series = []
            for spec in chosen:
                by_day = {
                    analysis.day_of(point, self.deps.tz): point.value
                    for point in self._daily(spec["entity_id"], now_local) if point.covered and point.value is not None
                }
                series.append({
                    "key": spec["key"], "entity_id": spec["entity_id"],
                    "label": tr(TILE_LABELS[first_key]) if spec["key"] == first_key and first_key in TILE_LABELS else spec["label"],
                    "unit": spec["unit"],
                    "values": [round(by_day[day], 1) if day in by_day else None for day in dates],
                })
            return series

        return {
            "days": days, "dates": [day.isoformat() for day in dates], "series": build(specs, "temperature"),
            "humidity": build(humidity_specs, "humidity") if humidity_specs else [],
        }

    def compute_longterm(self, now_local: datetime | None = None) -> dict | None:
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        if not config["temperature"] or self.deps.index.get_entity(config["temperature"]) is None:
            return None
        today = now_local.date()
        points = self._daily(config["temperature"], now_local)
        temperature_unit = self.deps.index.get_entity(config["temperature"])["unit"]
        found = analysis.records(points, self.deps.tz, today)
        entries: list[dict] = []
        if found:
            for key in ("warmest_day", "coldest_day", "highest", "lowest"):
                entries.append({"key": key, "entity_id": config["temperature"], **found[key]})
            gust_id = config.get("gust")
            if gust_id and self.deps.index.get_entity(gust_id) is not None:
                gusts = [
                    p for p in self._daily(gust_id, now_local)
                    if p.covered and p.max_value is not None and analysis.day_of(p, self.deps.tz) < today
                ]
                if gusts:
                    top = max(gusts, key=lambda p: (p.max_value, -p.day_start))
                    entries.append({
                        "key": "strongest_gust", "entity_id": gust_id,
                        "value": round(top.max_value, 2), "date": analysis.day_of(top, self.deps.tz).isoformat(),
                    })
            precip = self._precip_role(config, "mm")
            if precip is not None:
                factor = 1.0 / 60.0 if precip[1] == "dur" else 1.0
                days = [p for p in self._daily(precip[0], now_local) if analysis.day_of(p, self.deps.tz) < today]
                if days:
                    top = max(days, key=lambda p: ((p.value or 0.0), -p.day_start))
                    entries.append({
                        "key": "wettest_day" if precip[1] == "mm" else "rainiest_day", "entity_id": precip[0],
                        "unit": "mm" if precip[1] == "mm" else "min",
                        "value": round((top.value or 0.0) * factor, 2), "date": analysis.day_of(top, self.deps.tz).isoformat(),
                    })
            for entry in entries:
                entry["offset_days"] = (date.fromisoformat(entry["date"]) - today).days
                if "unit" not in entry:
                    source = self.deps.index.get_entity(entry["entity_id"])
                    entry["unit"] = (source["unit"] if source else "") or ""
        entity = self.deps.index.get_entity(config["temperature"])
        sun_days = self.sun_history(config, now_local)
        sun_kenntage = analysis.sun_kenntage(sun_days) if sun_days else None
        if sun_days:
            found_sun = analysis.sun_records(sun_days, self._monthly_sun(config, now_local, sun_days) or {"years": {}}, today)
            if found_sun:
                for key in ("sunniest_day", "sunniest_month", "longest_sunny_streak", "longest_overcast_streak"):
                    if key in found_sun:
                        entry = {
                            "key": key, "entity_id": config["illuminance"], **found_sun[key],
                            "unit": {"sunniest_day": "h", "sunniest_month": "h"}.get(key, ""),
                        }
                        entry["offset_days"] = (date.fromisoformat(entry["date"]) - today).days
                        # Die Tage reichen bis gestern: endet die Folge dort, läuft sie noch.
                        entry["ongoing"] = key.endswith("_streak") and entry.get("end") == (today - timedelta(days=1)).isoformat()
                        entries.append(entry)
        return {
            "kenntage": analysis.kenntage(points, self.deps.tz, today, temperature_unit),
            "sun_kenntage": sun_kenntage,
            "records": entries, "records_since": found["since"] if found else None,
            "records_days": found["days"] if found else 0,
            "unit": entity["unit"] or "°C", "tz": str(self.deps.tz), "today": today.isoformat(),
            "current_year": today.year,
        }

    # -- Kacheln -----------------------------------------------------------------------

    def compute_tiles(self, now_local: datetime | None = None, at: str | None = None) -> list[dict]:
        """Kacheln für heute oder, mit ``at`` (Stichtag vor heute), für diesen Tag: Wert am Tagesende, Tiefst-
        und Höchstwert des Tages, bei Zählern der Tag und der Vortag."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        stichtag = parse_stichtag(at, now_local.date())
        anchor = datetime(stichtag.year, stichtag.month, stichtag.day, 12, tzinfo=self.deps.tz) if stichtag else None
        read_cache = query_mod.QueryReadCache()
        today = self.column_windows("heute", now_local, "bis_jetzt", anchor)
        yesterday = self.column_windows("gestern", now_local, "bis_jetzt", anchor)
        tiles = []
        for spec in self._row_specs(config):
            entity = self.deps.index.get_entity(spec["entity_id"])
            # Die Sonnenscheindauer liest die Stundenmittel eines Messwerts („sunshine“ ist nur die Anzeigeart).
            aggregation_type = "standard" if spec["kind"] == "sunshine" else spec["kind"]  # "standard"/"counter"/"switch" sind zugleich die Aggregationstypen
            now_summary = self.summarize(
                spec["kind"],
                self._rows(spec["entity_id"], aggregation_type, today["start"], today["end"], now_local, read_cache),
                config, held_off=bool(entity and entity["first_ts"] and entity["first_ts"] < today["start"].timestamp()),
                unit=spec["unit"], until=min(today["end"].timestamp(), now_local.timestamp()),
            )
            yesterday_summary = None
            if spec["kind"] != "standard":
                yesterday_summary = self.summarize(
                    spec["kind"],
                    self._rows(spec["entity_id"], aggregation_type, yesterday["start"], yesterday["end"], now_local, read_cache),
                    config, held_off=bool(entity and entity["first_ts"] and entity["first_ts"] < yesterday["start"].timestamp()),
                    unit=spec["unit"], until=min(yesterday["end"].timestamp(), now_local.timestamp()),
                )
            tiles.append({
                "key": spec["key"], "entity_id": spec["entity_id"], "kind": spec["kind"],
                "label": tr(TILE_LABELS[spec["key"]]) if spec["key"] in TILE_LABELS else spec["label"],
                "unit": spec["unit"], "decimals": spec["decimals"],
                # Nicht entities.last_value: das ist bei vielen Sensoren leer (siehe Energiedashboard).
                "last": query_mod._boundary_value(  # noqa: SLF001 — siehe Modul-Docstring
                    self.deps.data_dir, spec["entity_id"], today["end"].timestamp(), self.deps.tz, read_cache
                ),
                "last_ts": entity["last_ts"] if entity else None,
                "today": now_summary, "yesterday": yesterday_summary,
            })
        return tiles

    # -- Sonnenstunden -------------------------------------------------------------------

    def compute_sunhours(self, at: str | None = None, now_local: datetime | None = None) -> dict | None:
        """Tagesverlauf der Helligkeit (Stundenmittel) mit den Stunden über der Sonnen-Schwelle; None ohne Rolle."""
        config = _load_config(self.deps.index)
        entity_id = config["illuminance"]
        if not entity_id or self.deps.index.get_entity(entity_id) is None:
            return None
        now_local = now_local or datetime.now(self.deps.tz)
        tz = self.deps.tz
        stichtag = parse_stichtag(at, now_local.date())
        day = stichtag or now_local.date()
        start = datetime(day.year, day.month, day.day, tzinfo=tz)
        following = day + timedelta(days=1)
        end = datetime(following.year, following.month, following.day, tzinfo=tz)
        until = min(end, now_local)
        threshold = float(config["sun_threshold_lx"])
        base = {
            "tz": str(tz), "date": day.isoformat(), "start": start.timestamp(), "end": end.timestamp(),
            "live": start <= now_local < end, "threshold": threshold,
            "unit": (self.deps.index.get_entity(entity_id)["unit"] or "lx"), "saturation": SUN_SENSOR_SATURATION_LX,
        }
        if until <= start:
            return {**base, "hours": [], "sun_hours": None, "peak": None}
        rows = sorted(
            (row for row in self._rows(entity_id, "standard", start, until, now_local, query_mod.QueryReadCache()) if row.value is not None),
            key=lambda row: row.bucket_start,
        )
        if not rows:
            return {**base, "hours": [], "sun_hours": None, "peak": None}
        held: list[tuple[float, float]] = []
        per_day = self._sunshine_per_day(rows, threshold, until.timestamp(), held)
        by_hour: dict[float, list] = {
            row.bucket_start: [row.bucket_start, row.value, row.max_value if row.max_value is not None else row.value, False]
            for row in rows
        }
        for stamp, value in held:
            by_hour.setdefault(stamp, [stamp, value, value, True])
        hours = [by_hour[stamp] for stamp in sorted(by_hour)]
        top = max(hours, key=lambda row: (row[1], -row[0]))
        return {
            **base, "hours": hours, "sun_hours": per_day.get(day, 0.0) / 3600.0,
            "peak": {"start": top[0], "value": top[2]},
        }

    # -- Tagesbild -----------------------------------------------------------------------

    def _sun_days(self, first: date, last: date, config: dict, now_local: datetime) -> list[dict]:
        """Sonnentage ``first`` bis ``last`` (einschließlich): je Tag ``class`` (sunny, mixed, overcast oder None),
        ``share`` (Sonnenanteil), ``hours`` (Sonnenstunden, auch ohne Klasse; None ohne Werte) und ``bright``
        (helle Stunden). Der Sonnenanteil ist Sonnenstunden geteilt durch die hellen Stunden; hell ist eine Stunde laut
        Dämmerungssensor (sonst nach der Helligkeit selbst) ab der Tag-Schwelle. Ein Tag mit weniger als
        ``DAYIMAGE_MIN_BRIGHT_HOURS`` hellen Stunden (oder ohne Werte) bekommt keine Klasse. Gemeinsame Grundlage von
        Tagesbild, Langzeit-Kenntagen, Monatsvergleich, Jahresmatrix und Rekorden."""
        entity_id = config["illuminance"]
        tz = self.deps.tz
        read_cache = query_mod.QueryReadCache()
        start = datetime(first.year, first.month, first.day, tzinfo=tz)
        main_rows = sorted(
            (row for row in self._rows(entity_id, "standard", start, now_local, now_local, read_cache) if row.value is not None),
            key=lambda row: row.bucket_start,
        )
        sun_seconds = self._sunshine_per_day(main_rows, float(config["sun_threshold_lx"]), now_local.timestamp())
        night_lx, day_lx = config["daylight_night_lx"], config["daylight_day_lx"]
        light_rows = main_rows
        dawn_id = config["illuminance_dawn"]
        if dawn_id and self.deps.index.get_entity(dawn_id) is not None:
            light_rows = [
                row for row in self._rows(dawn_id, "standard", start, now_local, now_local, read_cache) if row.value is not None
            ]

        def stats_of(rows: list) -> dict[float, tuple[float, float]]:
            return {row.bucket_start: (row.value, row.max_value if row.max_value is not None else row.value) for row in rows}

        stats = stats_of(light_rows)
        main_stats = stats_of(main_rows)
        dawn_days = {datetime.fromtimestamp(stamp, tz).date() for stamp in stats}
        out = []
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
            following = day + timedelta(days=1)
            end = min(datetime(following.year, following.month, following.day, tzinfo=tz), now_local)
            hour_starts = []
            moment = midnight.timestamp()
            while moment < end.timestamp():
                hour_starts.append(moment)
                moment += 3600.0
            # Hat der Dämmerungssensor an diesem Tag keine Zeilen (erst später im Archiv), zählt die Helligkeit selbst.
            classes = daylight.hour_classes(hour_starts, stats if day in dawn_days else main_stats, night_lx, day_lx)
            bright = sum(1 for kind in classes if kind == daylight.DAY)
            kind = None
            share = None
            hours = sun_seconds[day] / 3600.0 if day in sun_seconds else None
            if bright >= DAYIMAGE_MIN_BRIGHT_HOURS and hours is not None:
                share = min(1.0, hours / bright)
                kind = "sunny" if share >= 0.5 else "mixed" if share >= 0.2 else "overcast"
            out.append({"date": day, "class": kind, "share": share, "hours": hours, "bright": bright})
        return out

    def compute_dayimage(self, now_local: datetime | None = None, days: int = DAYIMAGE_DAYS) -> dict | None:
        """Klasse je Tag (sonnig, wechselhaft, trüb) der letzten Tage, Regeln siehe ``_sun_days``."""
        config = _load_config(self.deps.index)
        entity_id = config["illuminance"]
        if not entity_id or self.deps.index.get_entity(entity_id) is None:
            return None
        now_local = now_local or datetime.now(self.deps.tz)
        today = now_local.date()
        counts = {"sunny": 0, "mixed": 0, "overcast": 0, "none": 0}
        out = []
        for entry in self._sun_days(today - timedelta(days=days - 1), today, config, now_local):
            kind = entry["class"]
            counts[kind or "none"] += 1
            out.append({
                "date": entry["date"].isoformat(), "class": kind, "share": entry["share"],
                "sun_hours": entry["hours"] if kind else None, "bright_hours": float(entry["bright"]) if kind else None,
                "today": entry["date"] == today,
            })
        if counts["none"] == days:
            return None
        return {"tz": str(self.deps.tz), "days": out, "counts": counts, "threshold": config["sun_threshold_lx"]}

    def compute_dayimage_detail(self, at: str, now_local: datetime | None = None) -> dict | None:
        """Einzelner Tag aus dem Tagesbild für den Dialog: Helligkeitsverlauf mit den Stunden über der Sonnen-Schwelle
        (wie „Sonnenstunden“) und die Eckwerte des Tages (Temperatur, Regen, Wind, soweit Rollen belegt sind)."""
        now_local = now_local or datetime.now(self.deps.tz)
        sun = self.compute_sunhours(at, now_local)
        if sun is None:
            return None
        conditions = self.compute_conditions_day(sun["date"], now_local, include_today=True)
        return {"sun": sun, "conditions": conditions}

    def compute_pv(self, now_local: datetime | None = None, days: int = PV_DAYS) -> dict | None:
        """PV-Ertrag gegen Helligkeit der letzten abgeschlossenen Tage (Regeln siehe ``wetter_pv``); ``None`` ohne beide Rollen.

        Ein Süd-Sensor, der sättigt, schreibt bei unverändertem Wert keine Zeile; genau die sonnigsten Stunden
        fehlen dann. Eine Lücke bis ``SUN_MAX_GAP_HOURS`` füllt deshalb der niedrigere der beiden Nachbarwerte
        (wie bei der Sonnenscheindauer, aber vorsichtiger: ein Lückenwert wird nie höher als beide Ränder)."""
        config = _load_config(self.deps.index)
        lux_id, pv_id = config["illuminance"], config["pv_power"]
        lux_entity = self.deps.index.get_entity(lux_id) if lux_id else None
        pv_entity = self.deps.index.get_entity(pv_id) if pv_id else None
        if lux_entity is None or pv_entity is None:
            return None
        scale = PV_UNIT_KW.get(pv_entity["unit"] or "")
        if scale is None:
            return None
        tz = self.deps.tz
        now_local = now_local or datetime.now(tz)
        today = now_local.date()
        first = today - timedelta(days=days + pv_mod.WINDOW_DAYS)
        start = datetime(first.year, first.month, first.day, tzinfo=tz)
        read_cache = query_mod.QueryReadCache()
        lux_rows = sorted(
            (row for row in self._rows(lux_id, "standard", start, now_local, now_local, read_cache) if row.value is not None),
            key=lambda row: row.bucket_start,
        )
        pv_rows = [row for row in self._rows(pv_id, "standard", start, now_local, now_local, read_cache) if row.value is not None]
        stamps = [row.bucket_start for row in lux_rows]
        lux_at = {row.bucket_start: row.value for row in lux_rows}
        pairs = []
        for row in pv_rows:
            moment = datetime.fromtimestamp(row.bucket_start, tz)
            if moment.date() >= today:
                continue  # nur abgeschlossene Tage
            lux = lux_at.get(row.bucket_start)
            if lux is None:
                after = bisect.bisect_left(stamps, row.bucket_start)
                if 0 < after < len(stamps) and (stamps[after] - stamps[after - 1]) / 3600.0 <= SUN_MAX_GAP_HOURS:
                    lux = min(lux_rows[after - 1].value, lux_rows[after].value)
            if lux is None:
                continue
            pairs.append((moment.date(), moment.hour, float(lux), max(0.0, row.value * scale)))
        shown = [today - timedelta(days=offset) for offset in range(days, 0, -1)]
        result = pv_mod.evaluate(pairs, shown)
        if result is None:
            return None
        return {
            "tz": str(tz), **result, "window": pv_mod.WINDOW_DAYS, "limit": round(pv_mod.DEVIATION_LIMIT * 100),
            "unit": "kWh", "pv_entity_id": pv_id,
        }

    def sun_history(self, config: dict, now_local: datetime) -> list[dict]:
        """Alle abgeschlossenen Tage seit Archivbeginn der Helligkeit (Einträge wie ``_sun_days``); leer ohne Rolle."""
        entity = self.deps.index.get_entity(config["illuminance"]) if config["illuminance"] else None
        if entity is None or not entity["first_ts"]:
            return []
        first = datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date()
        yesterday = now_local.date() - timedelta(days=1)
        return self._sun_days(first, yesterday, config, now_local) if first <= yesterday else []

    # -- Tageslicht ----------------------------------------------------------------------

    def _daylight_day(
        self, entity_id: str, day: date, config: dict, now_local: datetime, read_cache: query_mod.QueryReadCache,
    ) -> dict:
        """Auswertung eines Tages: feine Kurve aus den Rohdaten, sonst aus den Stundenzeilen."""
        tz = self.deps.tz
        night_lx, day_lx = config["daylight_night_lx"], config["daylight_day_lx"]
        start = datetime(day.year, day.month, day.day, tzinfo=tz)
        following = day + timedelta(days=1)
        end = datetime(following.year, following.month, following.day, tzinfo=tz)
        live = start <= now_local < end
        until = min(end, now_local)
        base: dict = {"date": day.isoformat(), "start": start.timestamp(), "end": end.timestamp(), "live": live}
        if until <= start:
            return {**base, "resolution": None, "points": [], **daylight.summarize_hours([], [])}
        fine = query_mod._query_live_fine(  # noqa: SLF001 — siehe Modul-Docstring
            self.deps.data_dir, self.deps.index, entity_id, "standard", "day", "line",
            start, until, tz, now_local, read_cache,
        )
        points = [(row.bucket_start + 150, row.value) for row in fine if row.value is not None]
        if points:
            held = daylight.hold_points(points, start.timestamp(), until.timestamp(), night_lx)
            summary = daylight.summarize_fine(held, night_lx, day_lx, until.timestamp(), live)
            return {**base, "resolution": "fine", "points": [[round(t), round(v, 1)] for t, v in held], **summary}
        rows = self._rows(entity_id, "standard", start, until, now_local, read_cache)
        hour_starts = [start.timestamp() + 3600.0 * i for i in range(round((end - start).total_seconds() / 3600))]
        by_hour = {
            round(row.bucket_start): (row.value, row.max_value if row.max_value is not None else row.value)
            for row in rows if row.value is not None
        }
        measured = {hour: by_hour[round(hour)] for hour in hour_starts if round(hour) in by_hour}
        if not measured:
            return {**base, "resolution": None, "points": [], **daylight.summarize_hours(hour_starts, [None] * len(hour_starts))}
        classes = daylight.hour_classes(hour_starts, measured, night_lx, day_lx)
        summary = daylight.summarize_hours(hour_starts, classes)
        return {
            **base, "resolution": "hour",
            "points": [[round(hour + 1800), round(mean, 1)] for hour, (mean, _peak) in measured.items()],
            "hours": [[round(hour), round(mean, 1), round(peak, 1)] for hour, (mean, peak) in measured.items()],
            **summary,
        }

    def compute_daylight(self, at: str | None = None, now_local: datetime | None = None) -> dict | None:
        """Kurve und Kennzahlen für einen Tag (Standard heute) plus ein Vergleich der letzten Tage; None ohne Rolle."""
        config = _load_config(self.deps.index)
        entity_id = config["illuminance_dawn"]
        if not entity_id or self.deps.index.get_entity(entity_id) is None:
            return None
        now_local = now_local or datetime.now(self.deps.tz)
        selected = parse_stichtag(at, now_local.date()) or now_local.date()
        read_cache = query_mod.QueryReadCache()
        days = [selected - timedelta(days=offset) for offset in range(DAYLIGHT_COMPARE_DAYS)]
        results = [self._daylight_day(entity_id, day, config, now_local, read_cache) for day in days]
        compare_keys = ("date", "resolution", "from", "to", "length", "morning", "evening", "approx", "ongoing")
        compare = [{key: row[key] for key in compare_keys} for row in results]
        has_elevation = self._add_sun_elevation(compare, config, days[-1], now_local, read_cache)
        return {
            "tz": str(self.deps.tz),
            "unit": "lx",
            "night_lx": config["daylight_night_lx"],
            "day_lx": config["daylight_day_lx"],
            "now": now_local.timestamp(),
            "day": results[0],
            "compare": compare,
            "has_elevation": has_elevation,
        }

    def _add_sun_elevation(
        self, compare: list[dict], config: dict, first: date, now_local: datetime, read_cache: query_mod.QueryReadCache,
    ) -> bool:
        """Sonnenhöhe (Grad) zum Tagesbeginn und -ende jedes Vergleichstags, wenn die Rolle ``sun_elevation`` belegt ist.

        Sie zeigt, was der Sensor „Tag“ nennt, gemessen am Sonnenstand: Steht die Sonne beim Tagesbeginn erst hoch, ist
        der Sensor beschattet oder wolkenverhangen; ungefähr null Grad wäre Sonnenaufgang. Zwischen den Stundenmitteln
        wird linear interpoliert (die Höhe ändert sich am Horizont um rund 10 Grad je Stunde)."""
        entity_id = config["sun_elevation"]
        if not entity_id or self.deps.index.get_entity(entity_id) is None:
            return False
        tz = self.deps.tz
        start = datetime(first.year, first.month, first.day, tzinfo=tz) - timedelta(hours=2)
        rows = sorted(
            (row for row in self._rows(entity_id, "standard", start, now_local, now_local, read_cache) if row.value is not None),
            key=lambda row: row.bucket_start,
        )
        points = [(row.bucket_start + 1800.0, row.value) for row in rows]

        def elevation_at(moment: float | None) -> float | None:
            if moment is None or len(points) < 2:
                return None
            after = bisect.bisect_left([point[0] for point in points], moment)
            if after == 0 or after >= len(points):
                return None
            (t0, v0), (t1, v1) = points[after - 1], points[after]
            if t1 - t0 > 3 * 3600:
                return None  # Lücke in den Werten: nichts raten
            return round(v0 + (v1 - v0) * (moment - t0) / (t1 - t0), 1)

        for row in compare:
            row["sun_from"] = elevation_at(row["from"])
            row["sun_to"] = elevation_at(None if row["ongoing"] else row["to"])
        return True

    def compute_daylength(self, now_local: datetime | None = None) -> dict | None:
        """Tageslänge je abgeschlossenem Tag laut Dämmerungssensor (Stunden), für den Jahresverlauf.

        Die Archivwerte sind Stundenmittel; der Übergang über die Tag-Schwelle wird zwischen zwei Stunden
        interpoliert. Fehlt ein Übergang (der Sensor beginnt mitten am Tag), zählt die Zahl heller Stunden.

        Ein Tag zählt nur mit mindestens ``DAYLENGTH_MIN_HOURS`` Stunden Werten; die Archive der Dämmerungssensoren
        sind oft jung, Tage davor fehlen einfach. ``None`` ohne Rolle oder ohne auswertbaren Tag."""
        config = _load_config(self.deps.index)
        entity_id = config["illuminance_dawn"]
        entity = self.deps.index.get_entity(entity_id) if entity_id else None
        if entity is None or not entity["first_ts"]:
            return None
        tz = self.deps.tz
        now_local = now_local or datetime.now(tz)
        today = now_local.date()
        first = max(datetime.fromtimestamp(entity["first_ts"], tz).date(), today - timedelta(days=DAYLENGTH_MAX_DAYS))
        start = datetime(first.year, first.month, first.day, tzinfo=tz)
        rows = self._rows(entity_id, "standard", start, now_local, now_local, query_mod.QueryReadCache())
        by_hour = {
            round(row.bucket_start): (row.value, row.max_value if row.max_value is not None else row.value)
            for row in rows if row.value is not None
        }
        night_lx, day_lx = config["daylight_night_lx"], config["daylight_day_lx"]
        out = []
        for offset in range((today - first).days):
            day = first + timedelta(days=offset)
            following = day + timedelta(days=1)
            midnight = datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()
            end = datetime(following.year, following.month, following.day, tzinfo=tz).timestamp()
            hour_starts = [midnight + 3600.0 * i for i in range(round((end - midnight) / 3600))]
            measured = {hour: by_hour[round(hour)] for hour in hour_starts if round(hour) in by_hour}
            if len(measured) < DAYLENGTH_MIN_HOURS:
                continue
            summary = daylight.summarize_hours(hour_starts, daylight.hour_classes(hour_starts, measured, night_lx, day_lx))
            if summary["length"] is not None:
                # Stundenmittel als Punkte in der Stundenmitte; der Schwellenübergang wird zwischen zwei Stunden
                # interpoliert, statt auf volle Stunden zu springen (sonst bildet die Kurve Treppen).
                points = [(hour + 1800.0, measured[hour][0]) for hour in hour_starts if hour in measured]
                up, down = daylight.crossings(points, day_lx)
                length = down - up if up is not None and down is not None and down > up else summary["length"]
                out.append([day.isoformat(), round(length / 3600.0, 2)])
        if not out:
            return None
        return {"tz": str(tz), "days": out, "today": today.isoformat(), "approx": True}

    # -- Wetterlage ----------------------------------------------------------------------

    def _recent(
        self, entity_id: str, now_local: datetime, read_cache: query_mod.QueryReadCache, minutes: int = 10,
    ) -> dict | None:
        """Mittel, Maximum und letzter Wert der letzten Minuten; ein Sensor ohne neue Zeile hält seinen Wert."""
        if not entity_id or self.deps.index.get_entity(entity_id) is None:
            return None
        rows = query_mod._query_live_fine(  # noqa: SLF001 — siehe Modul-Docstring
            self.deps.data_dir, self.deps.index, entity_id, "standard", "hour", "line",
            now_local - timedelta(minutes=minutes), now_local, self.deps.tz, now_local, read_cache,
        )
        rows = [row for row in rows if row.value is not None]
        if not rows:
            held = query_mod._boundary_value(  # noqa: SLF001
                self.deps.data_dir, entity_id, now_local.timestamp(), self.deps.tz, read_cache,
            )
            return None if held is None else {"mean": held, "max": held, "last": held}
        highs = [row.max_value if row.max_value is not None else row.value for row in rows]
        return {"mean": sum(row.value for row in rows) / len(rows), "max": max(highs), "last": rows[-1].value}

    def _rain_timeline(
        self, entity_id: str, now_local: datetime, read_cache: query_mod.QueryReadCache,
    ) -> list[tuple[float, bool]]:
        start = (now_local - timedelta(hours=conditions.RAIN_LOOKBACK_HOURS)).timestamp()
        rows = list(query_mod.cleanup.iter_raw_rows(
            self.deps.data_dir, self.deps.index, entity_id, start, now_local.timestamp(), self.deps.tz,
            now=now_local, max_rows=query_mod.MAX_RAW_QUERY_POINTS,
            hot_rows_loader=read_cache.read_hot_rows,
        ))
        timeline = [(ts, value >= 0.5) for ts, value in rows]
        initial = query_mod._boundary_value(self.deps.data_dir, entity_id, start, self.deps.tz, read_cache)  # noqa: SLF001
        if initial is not None:
            timeline.insert(0, (start, initial >= 0.5))
        elif timeline:
            # Die erste Zeile des Sensors überhaupt: davor gab es keinen Regen, den wir kennen.
            timeline.insert(0, (start, False))
        return timeline

    def compute_conditions(self, now_local: datetime | None = None) -> dict | None:
        """Wetterlage jetzt: Himmelsklasse, Wind, Regen und Temperatur; fehlt eine Rolle, fehlt ihr Teil (nie „unbekannt“)."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        read_cache = query_mod.QueryReadCache()
        index = self.deps.index
        night_lx, day_lx = config["daylight_night_lx"], config["daylight_day_lx"]

        def unit_of(entity_id: str) -> str:
            entity = index.get_entity(entity_id)
            return (entity["unit"] or "") if entity else ""

        sky = None
        # Quelle der Klasse: die Helligkeit, sonst die Strahlung.
        main_role = "illuminance"
        main = self._recent(config["illuminance"], now_local, read_cache)
        if main is None:
            main_role, main = "radiation", self._recent(config["radiation"], now_local, read_cache)
        dawn = self._recent(config["illuminance_dawn"], now_local, read_cache)
        if main is not None or dawn is not None:
            if main_role == "illuminance" and main is not None:
                cuts, unit = sky_cuts(config), "lx"
            else:
                cuts, unit = conditions.CLASS_CUTS_WM2, unit_of(config["radiation"]) or "W/m²"
            if dawn is not None:
                kind = conditions.NIGHT if dawn["mean"] < night_lx else conditions.DUSK if dawn["mean"] < day_lx else None
            elif main is not None and unit == "lx":
                kind = conditions.NIGHT if main["mean"] < night_lx else conditions.DUSK if main["mean"] < day_lx else None
            else:
                kind = conditions.NIGHT if main is not None and main["mean"] < conditions.NIGHT_WM2 else None
            if kind is None:
                kind = conditions.sky_class(main["mean"], cuts) if main is not None else conditions.DAY
            sky = {
                "class": kind, "value": main["mean"] if main else None, "unit": unit,
                "source": main_role if main else None, "dawn": dawn["mean"] if dawn else None,
            }

        wind = None
        recent = self._recent(config["wind"], now_local, read_cache)
        if recent is not None:
            kmh = conditions.to_kmh(recent["max"], unit_of(config["wind"]))
            if kmh is not None:
                wind = {"kmh": kmh, "bft": conditions.beaufort(kmh)}
                gust = self._recent(config["gust"], now_local, read_cache)
                gust_kmh = conditions.to_kmh(gust["max"], unit_of(config["gust"])) if gust is not None else None
                if gust_kmh is not None and gust_kmh > kmh:
                    wind["gust_kmh"] = gust_kmh
                direction = self._recent(config["wind_direction"], now_local, read_cache) if config["wind_direction"] else None
                if direction is not None and direction["last"] is not None and kmh >= 1:
                    wind["dir_deg"] = float(direction["last"]) % 360.0  # bei Flaute sagt die Richtung nichts

        rain = None
        if config["precip_dur"] and index.get_entity(config["precip_dur"]) is not None:
            rain = conditions.rain_state(self._rain_timeline(config["precip_dur"], now_local, read_cache), now_local.timestamp())
        if config["precip_mm"] and index.get_entity(config["precip_mm"]) is not None:
            midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today = self.summarize(
                "counter", self._rows(config["precip_mm"], "counter", midnight, now_local, now_local, read_cache),
                config, unit=unit_of(config["precip_mm"]),
            )
            mm = {"mm_today": today["sum"] if today else None, "mm_unit": unit_of(config["precip_mm"])}
            rain = {**(rain or {"raining": None, "since": None, "last": None, "minutes": None}), **mm}

        temp = None
        recent = self._recent(config["temperature"], now_local, read_cache)
        entity = index.get_entity(config["temperature"]) if config["temperature"] else None
        if recent is not None and entity is not None:
            temp = {"value": recent["last"], "unit": entity["unit"] or "", "decimals": _decimals(entity, "temperature")}
            midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            span = self.summarize("standard", self._rows(config["temperature"], "standard", midnight, now_local, now_local, read_cache), config)
            if span is not None:
                temp.update(min=span["min"], max=span["max"])

        # Beiwerk für die Zeile unter dem Wort: Sonnenstunden heute und Tagesbeginn laut Dämmerungssensor.
        if sky is not None and config["illuminance"] and index.get_entity(config["illuminance"]) is not None:
            midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            lux_rows = [
                row for row in self._rows(config["illuminance"], "standard", midnight, now_local, now_local, read_cache)
                if row.value is not None
            ]
            sun = self._summarize_sunshine(lux_rows, config, now_local.timestamp()) if lux_rows else None
            if sun is not None:
                sky["sun_hours"] = sun["hours"]
        light = None
        if config["illuminance_dawn"] and index.get_entity(config["illuminance_dawn"]) is not None:
            today = self._daylight_day(config["illuminance_dawn"], now_local.date(), config, now_local, read_cache)
            if today.get("from") is not None:
                light = {"from": today["from"], "to": None if today.get("ongoing") else today.get("to")}
            # Das Tagesende des laufenden Tages kennt der Sensor erst danach; gestern ist die beste Näherung.
            yesterday = self._daylight_day(
                config["illuminance_dawn"], now_local.date() - timedelta(days=1), config, now_local, read_cache,
            )
            if light is not None and yesterday.get("to") is not None and not yesterday.get("ongoing"):
                light["yesterday_to"] = yesterday["to"]

        if sky is None and wind is None and rain is None:
            return None
        return {
            "tz": str(self.deps.tz), "now": now_local.timestamp(), "sky": sky, "wind": wind, "rain": rain, "temp": temp,
            "light": light,
        }

    def compute_conditions_day(self, at: str, now_local: datetime | None = None, include_today: bool = False) -> dict | None:
        """Wetter eines vergangenen Tages als Zusammenfassung: häufigste Himmelsklasse und Sonnenstunden, stärkster
        Wind, Regenmenge und -dauer, Tiefst- und Höchstwert. Ein „Jetzt“ gibt es dort nicht (am Tagesende ist
        immer Nacht); fehlt eine Rolle oder haben ihre Werte keine Zeilen, fehlt ihr Teil. Mit ``include_today`` gilt
        auch der heutige Tag (bisher) als Tag statt als „jetzt“."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        stichtag = parse_stichtag(at, now_local.date() + timedelta(days=1) if include_today else now_local.date())
        if stichtag is None:
            return None
        tz = self.deps.tz
        start = datetime(stichtag.year, stichtag.month, stichtag.day, tzinfo=tz)
        following = stichtag + timedelta(days=1)
        end = min(datetime(following.year, following.month, following.day, tzinfo=tz), now_local)
        if end <= start:
            return None
        read_cache = query_mod.QueryReadCache()
        index = self.deps.index
        night_lx, day_lx = config["daylight_night_lx"], config["daylight_day_lx"]

        def unit_of(entity_id: str) -> str:
            entity = index.get_entity(entity_id)
            return (entity["unit"] or "") if entity else ""

        def rows_of(entity_id: str, kind: str = "standard") -> list:
            if not entity_id or index.get_entity(entity_id) is None:
                return []
            return self._rows(entity_id, kind, start, end, now_local, read_cache)

        sky = None
        main_role = "illuminance" if config["illuminance"] else "radiation"
        main_rows = [row for row in rows_of(config[main_role]) if row.value is not None]
        if main_rows:
            lux = main_role == "illuminance"
            cuts = sky_cuts(config) if lux else conditions.CLASS_CUTS_WM2
            dawn = {row.bucket_start: row.value for row in rows_of(config["illuminance_dawn"]) if row.value is not None}
            counts: dict[str, int] = {}
            for row in main_rows:
                if dawn:
                    bright = dawn.get(row.bucket_start, -1.0) >= day_lx
                else:
                    bright = row.value >= (night_lx if lux else conditions.NIGHT_WM2)
                if bright:
                    kind = conditions.sky_class(row.value, cuts)
                    counts[kind] = counts.get(kind, 0) + 1
            order = (conditions.OVERCAST, conditions.CLOUDY, conditions.FAIR, conditions.SUNNY)
            dominant = max(counts, key=lambda kind: (counts[kind], order.index(kind))) if counts else None
            sun = self._summarize_sunshine(main_rows, config, None) if lux else None
            sky = {
                "class": dominant, "day_hours": sum(counts.values()),
                "sun_hours": sun["hours"] if sun else None,
            }

        wind = None
        wind_rows = [row for row in rows_of(config["wind"]) if row.value is not None]
        if wind_rows:
            peak = max(row.max_value if row.max_value is not None else row.value for row in wind_rows)
            kmh = conditions.to_kmh(peak, unit_of(config["wind"]))
            if kmh is not None:
                wind = {"kmh": kmh, "bft": conditions.beaufort(kmh)}

        rain = None
        if config["precip_mm"] and index.get_entity(config["precip_mm"]) is not None:
            total = self.summarize("counter", rows_of(config["precip_mm"], "counter"), config, unit=unit_of(config["precip_mm"]))
            if total is not None:
                rain = {"mm": total["sum"], "mm_unit": unit_of(config["precip_mm"]), "minutes": None}
        if config["precip_dur"] and index.get_entity(config["precip_dur"]) is not None:
            entity = index.get_entity(config["precip_dur"])
            wet = self.summarize(
                "switch", rows_of(config["precip_dur"], "switch"), config,
                held_off=bool(entity and entity["first_ts"] and entity["first_ts"] < start.timestamp()),
            )
            if wet is not None:
                rain = {**(rain or {"mm": None, "mm_unit": ""}), "minutes": wet["minutes"]}

        temp = None
        entity = index.get_entity(config["temperature"]) if config["temperature"] else None
        if entity is not None:
            summary = self.summarize("standard", rows_of(config["temperature"]), config)
            if summary is not None:
                temp = {
                    "min": summary["min"], "max": summary["max"], "unit": entity["unit"] or "",
                    "decimals": _decimals(entity, "temperature"),
                }

        if sky is None and wind is None and rain is None and temp is None:
            return None
        return {"mode": "day", "date": stichtag.isoformat(), "tz": str(tz), "sky": sky, "wind": wind, "rain": rain, "temp": temp}

    # -- Seite -------------------------------------------------------------------------

    def _page_context(self, request: Request) -> dict:
        config = _load_config(self.deps.index)
        return {
            "configured": _is_configured(config),
            "precip_modes": self.available_precip(),
            "has_heating": bool(self.heating_counters(config)),
            "has_windtiles": any(config[key] for key in ("wind", "gust", "radiation", "illuminance")),
            "has_windsun": any(config[key] for key in ("wind", "gust", "radiation", "illuminance", "illuminance_dawn")),
            "has_daylight": bool(config["illuminance_dawn"]),
            "has_dayimage": bool(config["illuminance"]),
            "has_pv": card_available("pvlight", config),
            "hidden_cards": config["hidden_cards"],
            "has_conditions": any(
                config[key] for key in ("illuminance", "illuminance_dawn", "radiation", "wind", "precip_dur", "precip_mm")
            ),
            "has_klima": any(extra["entity_id"] for extra in config["extra_temperatures"]),
            **calendar_bounds(self.deps.index.list_entities(), self.deps.tz),
            "enabled": _is_enabled(self.deps.index),
            "config": config,
            **self.deps.app_root_context(request),
        }

    def _entity_options(self) -> list[dict]:
        return [
            {
                "entity_id": row["entity_id"],
                "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
                "ha_name": row["friendly_name"] or row["entity_id"],
                "is_custom": bool(row["custom_name"]),
                "unit": row["unit"],
                "aggregation_type": row["aggregation_type"],
                "last_value": row["last_value"],
            }
            for row in self.deps.index.list_entities()
        ]

    def router(self) -> APIRouter:
        router = APIRouter()
        deps = self.deps

        @router.get("/wetterdashboard", response_class=HTMLResponse)
        def wetterdashboard_page(request: Request) -> HTMLResponse:
            return deps.templates.TemplateResponse(request, "wetterdashboard.html", self._page_context(request))

        @router.post("/wetterdashboard/enable")
        def wetterdashboard_enable() -> dict:
            deps.index.set_setting(SETTING_ENABLED, "1")
            return {"enabled": True}

        @router.post("/wetterdashboard/disable")
        def wetterdashboard_disable() -> dict:
            # Konfiguration bleibt erhalten, Deaktivieren blendet nur aus.
            deps.index.set_setting(SETTING_ENABLED, "0")
            return {"enabled": False}

        @router.get("/wetterdashboard/setup", response_class=HTMLResponse)
        def wetterdashboard_setup_form(request: Request) -> HTMLResponse:
            config = _load_config(deps.index)
            warnings = {key: _role_warning(deps.index, key, config[key]) for key in ROLE_KEYS}
            return deps.templates.TemplateResponse(
                request, "_wetter_setup.html",
                {
                    "config": config, "entity_options": self._entity_options(),
                    "roles": [(key, label, types) for key, label, types in ROLES],
                    "role_types": {key: list(types) for key, _label, types in ROLES},
                    "role_units": ROLE_UNITS,
                    "card_groups": [
                        (tr(tab), [(key, tr(labels[key])) for key in keys])
                        for tab, keys in CARD_GROUPS
                        for labels in [{key: label for key, label, _needs in CARD_OPTIONS}]
                    ],
                    "card_needs": {key: list(needs) for key, _label, needs in CARD_OPTIONS},
                    "block_options": [("kenntage", tr("Kenntage"))] + [
                        (key, tr("Sonnenscheindauer") if key == "illuminance" else tr(ROLE_LABELS[key]))
                        for key in BLOCK_KEYS if key != "kenntage"
                    ],
                    "counter_options": [o for o in self._entity_options() if o["aggregation_type"] == "counter"],
                    "max_heating": MAX_HEATING_COUNTERS,
                    "warnings": warnings, "max_extra": MAX_EXTRA_TEMPERATURES,
                    **deps.app_root_context(request),
                },
            )

        @router.get("/wetterdashboard/setup/vorschlaege")
        def wetterdashboard_setup_suggestions() -> dict:
            """Rollenvorschläge für die Einrichtung (nichts wird gespeichert)."""
            catalog = selection_catalog.load(deps.data_dir)
            labels = {
                row["entity_id"]: entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"])
                for row in deps.index.list_entities()
            }
            return wetter_autofill.suggest(
                [dict(row) for row in deps.index.list_entities()], labels, catalog["entities"] if catalog else None,
            )

        @router.post("/wetterdashboard/setup", response_class=HTMLResponse)
        def wetterdashboard_setup_save(
            request: Request,
            temperature: str = Form(""),
            precip_mm: str = Form(""),
            precip_dur: str = Form(""),
            humidity: str = Form(""),
            pressure: str = Form(""),
            wind: str = Form(""),
            gust: str = Form(""),
            radiation: str = Form(""),
            illuminance: str = Form(""),
            illuminance_dawn: str = Form(""),
            pv_power: str = Form(""),
            wind_direction: str = Form(""),
            sun_elevation: str = Form(""),
            extra_entity_id: list[str] = Form([]),
            extra_name: list[str] = Form([]),
            blocks_submitted: str = Form(""),
            show_block: list[str] = Form([]),
            cards_submitted: str = Form(""),
            show_card: list[str] = Form([]),
            heating_entity_id: list[str] = Form([]),
            heating_name: list[str] = Form([]),
            heating_room: str = Form(""),
            heating_limit: str = Form(""),
            rain_day_mm: str = Form(""),
            rain_day_minutes: str = Form(""),
            sun_threshold_lx: str = Form(""),
            daylight_night_lx: str = Form(""),
            daylight_day_lx: str = Form(""),
            sky_cloudy_lx: str = Form(""),
            sky_fair_lx: str = Form(""),
            sky_sunny_lx: str = Form(""),
            lage_layout: str = Form(""),
        ) -> HTMLResponse:
            submitted = {
                "temperature": temperature, "precip_mm": precip_mm, "precip_dur": precip_dur,
                "humidity": humidity, "pressure": pressure, "wind": wind, "gust": gust, "radiation": radiation,
                "illuminance": illuminance, "illuminance_dawn": illuminance_dawn, "pv_power": pv_power, "wind_direction": wind_direction, "sun_elevation": sun_elevation,
            }
            config = _empty_config()
            for key, value in submitted.items():
                entity_id = value.strip()
                if entity_id:
                    entity = deps.index.get_entity(entity_id)
                    if entity is None:
                        raise HTTPException(status_code=400, detail=tr("Unbekannte Entität: {entity_id}", entity_id=entity_id))
                    if entity["aggregation_type"] not in ROLE_TYPES[key]:
                        raise HTTPException(
                            status_code=400,
                            detail=tr("{entity_id}: Typ „{type}“ passt nicht zur Rolle „{role}“.",
                                      entity_id=entity_id, type=entity["aggregation_type"], role=tr(ROLE_LABELS[key])),
                        )
                config[key] = entity_id
            extras = []
            for position, entity_id in enumerate(extra_entity_id):
                entity_id = entity_id.strip()
                if not entity_id:
                    continue
                entity = deps.index.get_entity(entity_id)
                if entity is None or entity["aggregation_type"] != "standard":
                    raise HTTPException(status_code=400, detail=tr("Unbekannte Entität: {entity_id}", entity_id=entity_id))
                name = extra_name[position].strip() if position < len(extra_name) else ""
                extras.append({"entity_id": entity_id, "name": name[:60]})
            config["extra_temperatures"] = extras[:MAX_EXTRA_TEMPERATURES]
            # Ohne das Marker-Feld (ältere Aufrufer) bleibt die bisherige Auswahl erhalten.
            if blocks_submitted:
                config["hidden_blocks"] = [key for key in BLOCK_KEYS if key not in show_block]
            else:
                config["hidden_blocks"] = _load_config(deps.index)["hidden_blocks"]
            # Karten, die nach den gesendeten Rollen nichts zeigen könnten, bieten die Einrichtung nicht an; ihre
            # frühere Wahl bleibt erhalten, bis eine Rolle sie wieder aktiviert.
            previous_cards = _load_config(deps.index)["hidden_cards"]
            heating_rows = [entity_id.strip() for entity_id in heating_entity_id if entity_id.strip()]
            probe = {**config, "heating": heating_rows}
            if cards_submitted:
                config["hidden_cards"] = [
                    key for key in CARD_KEYS
                    if (key not in show_card if card_available(key, probe) else key in previous_cards)
                ]
            else:
                config["hidden_cards"] = previous_cards
            # Ohne Angabe (ältere Aufrufer) bleibt die bisherige Darstellung.
            layout = lage_layout.strip()
            if layout and layout not in LAGE_LAYOUTS:
                raise HTTPException(status_code=400, detail=tr("Ungültige Darstellung: {value}", value=layout))
            config["lage_layout"] = layout or _load_config(deps.index)["lage_layout"]
            heating = []
            for position, entity_id in enumerate(heating_entity_id):
                entity_id = entity_id.strip()
                if not entity_id:
                    continue
                entity = deps.index.get_entity(entity_id)
                if entity is None or entity["aggregation_type"] != "counter":
                    raise HTTPException(
                        status_code=400,
                        detail=tr("{entity_id}: Heizenergie braucht einen Zähler.", entity_id=entity_id),
                    )
                name = heating_name[position].strip() if position < len(heating_name) else ""
                heating.append({"entity_id": entity_id, "name": name[:60]})
            config["heating"] = heating[:MAX_HEATING_COUNTERS]
            for key, raw, default, low, high in (
                ("heating_room", heating_room, DEFAULT_HEATING_ROOM, 15.0, 25.0),
                ("heating_limit", heating_limit, DEFAULT_HEATING_LIMIT, 5.0, 22.0),
            ):
                try:
                    number = float(raw.replace(",", ".")) if raw.strip() else default
                except ValueError:
                    raise HTTPException(status_code=400, detail=tr("Ungültige Zahl: {value}", value=raw)) from None
                if not low <= number <= high:
                    raise HTTPException(
                        status_code=400,
                        detail=tr("Wert außerhalb von {low} bis {high}: {value}", low=f"{low:g}", high=f"{high:g}", value=raw),
                    )
                config[key] = number
            for key, raw, default in (
                ("rain_day_mm", rain_day_mm, DEFAULT_RAIN_DAY_MM),
                ("rain_day_minutes", rain_day_minutes, DEFAULT_RAIN_DAY_MINUTES),
            ):
                try:
                    value = float(raw.replace(",", ".")) if raw.strip() else default
                except ValueError:
                    raise HTTPException(status_code=400, detail=tr("Ungültige Zahl: {value}", value=raw)) from None
                config[key] = value if value > 0 else default
            try:
                threshold = float(sun_threshold_lx.replace(",", ".").replace(" ", "")) if sun_threshold_lx.strip() else DEFAULT_SUN_THRESHOLD_LX
            except ValueError:
                raise HTTPException(status_code=400, detail=tr("Ungültige Zahl: {value}", value=sun_threshold_lx)) from None
            low, high = SUN_THRESHOLD_RANGE_LX
            if not low <= threshold <= high:
                raise HTTPException(
                    status_code=400,
                    detail=tr("Wert außerhalb von {low} bis {high}: {value}", low=f"{low:g}", high=f"{high:g}", value=sun_threshold_lx),
                )
            config["sun_threshold_lx"] = threshold
            night = _parse_bounded(daylight_night_lx, DEFAULT_DAYLIGHT_NIGHT_LX, DAYLIGHT_NIGHT_RANGE_LX)
            day = _parse_bounded(daylight_day_lx, DEFAULT_DAYLIGHT_DAY_LX, DAYLIGHT_DAY_RANGE_LX)
            if night >= day:
                raise HTTPException(
                    status_code=400,
                    detail=tr("Die Nacht-Schwelle muss unter der Tag-Schwelle liegen."),
                )
            config["daylight_night_lx"], config["daylight_day_lx"] = night, day
            own = [sky_cloudy_lx, sky_fair_lx, sky_sunny_lx]
            if any(raw.strip() for raw in own):
                automatic = conditions.lx_cuts(threshold)
                cuts = [_parse_bounded(raw, automatic[i], SUN_THRESHOLD_RANGE_LX) for i, raw in enumerate(own)]
                if not cuts[0] < cuts[1] < cuts[2]:
                    raise HTTPException(status_code=400, detail=tr("Die Grenzen der Himmelsklassen müssen von unten nach oben ansteigen."))
                config["sky_cuts_lx"] = cuts
            else:
                config["sky_cuts_lx"] = []
            _save_config(deps.index, config)
            context = self._page_context(request)
            return deps.templates.TemplateResponse(request, "_wetter_view.html", context)

        @router.post("/wetterdashboard/table")
        def wetterdashboard_table(body: TableRequest) -> dict:
            if not _is_configured(_load_config(deps.index)):
                raise HTTPException(status_code=409, detail=tr("Noch nicht eingerichtet"))
            return self.compute_table(body)

        def _require_configured() -> None:
            if not _is_configured(_load_config(deps.index)):
                raise HTTPException(status_code=409, detail=tr("Noch nicht eingerichtet"))

        @router.get("/wetterdashboard/niederschlag")
        def wetterdashboard_precip(mode: str = "mm") -> dict:
            _require_configured()
            result = self.compute_precip(mode)
            if result is None:
                raise HTTPException(status_code=404, detail=tr("Kein Niederschlag zugeordnet."))
            return result

        @router.get("/wetterdashboard/monatsvergleich")
        def wetterdashboard_monthly(metric: str = "temperature", mode: str = "mm") -> dict:
            _require_configured()
            if metric not in ("temperature", "precip", "sun"):
                raise HTTPException(status_code=400, detail=tr("Ungültige Kennzahl."))
            result = self.compute_monthly(metric, mode)
            if result is None:
                raise HTTPException(status_code=404, detail=tr("Kein Niederschlag zugeordnet."))
            return result

        @router.get("/wetterdashboard/heizen")
        def wetterdashboard_heating() -> dict:
            _require_configured()
            result = self.compute_heating()
            if result is None:
                raise HTTPException(status_code=404, detail=tr("Keine Heizzähler zugeordnet."))
            return result

        @router.get("/wetterdashboard/matrix")
        def wetterdashboard_matrix(metric: str = "avg", mode: str = "mm") -> dict:
            _require_configured()
            if metric not in ("avg", "max", "min", "precip", "sun"):
                raise HTTPException(status_code=400, detail=tr("Ungültige Kennzahl."))
            result = self.compute_matrix(metric, mode)
            if result is None:
                raise HTTPException(status_code=404, detail=tr("Kein Niederschlag zugeordnet."))
            return result

        @router.get("/wetterdashboard/temperaturen")
        def wetterdashboard_temperatures(days: int = 30) -> dict:
            _require_configured()
            if days not in TEMPERATURE_DAYS:
                raise HTTPException(status_code=400, detail=tr("Ungültiger Zeitraum."))
            result = self.compute_temperatures(days)
            if result is None:
                raise HTTPException(status_code=404, detail=tr("Noch nicht eingerichtet"))
            return result

        @router.get("/wetterdashboard/langzeit")
        def wetterdashboard_longterm() -> dict:
            _require_configured()
            result = self.compute_longterm()
            if result is None:
                raise HTTPException(status_code=404, detail=tr("Noch nicht eingerichtet"))
            return result

        @router.get("/wetterdashboard/tageslicht")
        def wetterdashboard_daylight(date: str | None = None) -> dict:
            _require_configured()
            data = self.compute_daylight(date)
            if data is None:
                raise HTTPException(status_code=409, detail=tr("Keine Entität für „Helligkeit Dämmerung“ zugeordnet."))
            return data

        @router.get("/wetterdashboard/sonnenstunden")
        def wetterdashboard_sunhours(date: str | None = None) -> dict:
            _require_configured()
            data = self.compute_sunhours(date)
            if data is None:
                raise HTTPException(status_code=409, detail=tr("Keine Entität für „Helligkeit“ zugeordnet."))
            return data

        @router.get("/wetterdashboard/tagesbild")
        def wetterdashboard_dayimage() -> dict:
            _require_configured()
            data = self.compute_dayimage()
            if data is None:
                raise HTTPException(status_code=409, detail=tr("Keine Helligkeitswerte für das Tagesbild vorhanden."))
            return data

        @router.get("/wetterdashboard/tagesbild/tag")
        def wetterdashboard_dayimage_day(date: str) -> dict:
            _require_configured()
            data = self.compute_dayimage_detail(date)
            if data is None:
                raise HTTPException(status_code=409, detail=tr("Keine Helligkeitswerte für das Tagesbild vorhanden."))
            return data

        @router.get("/wetterdashboard/tageslaenge")
        def wetterdashboard_daylength() -> dict:
            _require_configured()
            data = self.compute_daylength()
            if data is None:
                raise HTTPException(status_code=409, detail=tr("Noch keine Tageslänge aus dem Dämmerungssensor vorhanden."))
            return data

        @router.get("/wetterdashboard/pv")
        def wetterdashboard_pv() -> dict:
            _require_configured()
            data = self.compute_pv()
            if data is None:
                raise HTTPException(status_code=409, detail=tr("Noch keine PV-Leistung mit Helligkeit zum Vergleichen vorhanden."))
            return data

        @router.get("/wetterdashboard/wetterlage")
        def wetterdashboard_conditions(date: str | None = None) -> dict:
            _require_configured()
            stichtag = parse_stichtag(date, datetime.now(deps.tz).date())
            data = self.compute_conditions_day(date) if stichtag else self.compute_conditions()
            return data or {"sky": None, "wind": None, "rain": None, "temp": None}

        @router.get("/wetterdashboard/tiles")
        def wetterdashboard_tiles(date: str | None = None) -> dict:
            if not _is_configured(_load_config(deps.index)):
                raise HTTPException(status_code=409, detail=tr("Noch nicht eingerichtet"))
            stichtag = parse_stichtag(date, datetime.now(deps.tz).date())
            return {"tz": str(deps.tz), "at": stichtag.isoformat() if stichtag else None, "tiles": self.compute_tiles(at=date)}

        return router
