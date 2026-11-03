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
BLOCK_KEYS = ("kenntage", "precip_mm", "precip_dur", "humidity", "pressure", "wind", "gust", "radiation")
#: Nachkommastellen, wenn die Entität „automatisch“ eingestellt ist: Strahlung in ganzen W/m².
ROLE_DEFAULT_DECIMALS = {"radiation": 0}
#: Kurze Namen für die Kacheln (in der Tabelle stehen die ausführlichen Rollennamen).
TILE_LABELS = {"temperature": N_("Temperatur"), "precip_mm": N_("Niederschlag"), "precip_dur": N_("Regen")}
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
        "hidden_blocks": [],
    }


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
    for key in ("heating_room", "heating_limit"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 40:
            config[key] = float(value)
    for key in ("rain_day_mm", "rain_day_minutes"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            config[key] = value
    return config


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
        self, kind: str, rows: list[rollup_mod.FineRow], config: dict, held_off: bool = False
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
        if kind == "counter":
            days = [(row.value or 0.0, row.bucket_start) for row in rows]
            if not days:
                return None
            wet = float(config["rain_day_mm"])
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
                    rows = self._rows(spec["entity_id"], spec["kind"], start, end, now_local, read_cache)
                    cell[side] = self.summarize(
                        spec["kind"], rows, config, held_off=bool(first_ts and first_ts < start.timestamp()),
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
                cell[side] = analysis.count_kenntage(points, tz, start.timestamp(), end.timestamp(), today)
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
        archive_start = (
            datetime.fromtimestamp(entity["first_ts"], self.deps.tz).date() if entity and entity["first_ts"] else None
        )
        result = analysis.precipitation(daily, now_local.date(), threshold, hold_to_end=kind == "dur", archive_start=archive_start)
        return {
            **result, "mode": kind, "unit": "min" if kind == "dur" else (entity["unit"] or "mm"),
            "threshold": threshold, "entity_id": entity_id, "tz": str(self.deps.tz),
            "available": self.available_precip(),
        }

    def compute_monthly(self, metric: str, mode: str = "mm", now_local: datetime | None = None) -> dict | None:
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
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
            "today": now_local.date().isoformat(), "tz": str(self.deps.tz),
        }

    def compute_matrix(self, metric: str, mode: str = "mm", now_local: datetime | None = None) -> dict | None:
        """Jahr×Tag-Matrix. metric: ``avg``/``max``/``min`` (Außentemperatur) oder ``precip``."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        today = now_local.date()
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

    def compute_temperatures(self, days: int, now_local: datetime | None = None) -> dict | None:
        """Tagesmittel der Außentemperatur und aller weiteren Sensoren für die letzten ``days`` Tage."""
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        specs = [spec for spec in self._row_specs(config) if spec["key"].split(":")[0] == "temperature"]
        if not specs:
            return None
        today = now_local.date()
        dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
        series = []
        for spec in specs:
            by_day = {
                analysis.day_of(point, self.deps.tz): point.value
                for point in self._daily(spec["entity_id"], now_local) if point.covered and point.value is not None
            }
            series.append({
                "key": spec["key"], "entity_id": spec["entity_id"],
                "label": tr(TILE_LABELS["temperature"]) if spec["key"] == "temperature" else spec["label"],
                "unit": spec["unit"],
                "values": [round(by_day[day], 1) if day in by_day else None for day in dates],
            })
        return {"days": days, "dates": [day.isoformat() for day in dates], "series": series}

    def compute_longterm(self, now_local: datetime | None = None) -> dict | None:
        config = _load_config(self.deps.index)
        now_local = now_local or datetime.now(self.deps.tz)
        if not config["temperature"] or self.deps.index.get_entity(config["temperature"]) is None:
            return None
        today = now_local.date()
        points = self._daily(config["temperature"], now_local)
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
        return {
            "kenntage": analysis.kenntage(points, self.deps.tz, today),
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
            aggregation_type = spec["kind"]  # "standard"/"counter"/"switch" sind zugleich die Aggregationstypen
            now_summary = self.summarize(
                spec["kind"],
                self._rows(spec["entity_id"], aggregation_type, today["start"], today["end"], now_local, read_cache),
                config, held_off=bool(entity and entity["first_ts"] and entity["first_ts"] < today["start"].timestamp()),
            )
            yesterday_summary = None
            if spec["kind"] != "standard":
                yesterday_summary = self.summarize(
                    spec["kind"],
                    self._rows(spec["entity_id"], aggregation_type, yesterday["start"], yesterday["end"], now_local, read_cache),
                    config, held_off=bool(entity and entity["first_ts"] and entity["first_ts"] < yesterday["start"].timestamp()),
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

    # -- Seite -------------------------------------------------------------------------

    def _page_context(self, request: Request) -> dict:
        config = _load_config(self.deps.index)
        return {
            "configured": _is_configured(config),
            "precip_modes": self.available_precip(),
            "has_heating": bool(self.heating_counters(config)),
            "has_windsun": any(config[key] for key in ("wind", "gust", "radiation")),
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
                    "block_options": [("kenntage", tr("Kenntage"))] + [
                        (key, tr(ROLE_LABELS[key])) for key in BLOCK_KEYS if key != "kenntage"
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
            extra_entity_id: list[str] = Form([]),
            extra_name: list[str] = Form([]),
            blocks_submitted: str = Form(""),
            show_block: list[str] = Form([]),
            heating_entity_id: list[str] = Form([]),
            heating_name: list[str] = Form([]),
            heating_room: str = Form(""),
            heating_limit: str = Form(""),
            rain_day_mm: str = Form(""),
            rain_day_minutes: str = Form(""),
        ) -> HTMLResponse:
            submitted = {
                "temperature": temperature, "precip_mm": precip_mm, "precip_dur": precip_dur,
                "humidity": humidity, "pressure": pressure, "wind": wind, "gust": gust, "radiation": radiation,
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
            if metric not in ("temperature", "precip"):
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
            if metric not in ("avg", "max", "min", "precip"):
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

        @router.get("/wetterdashboard/tiles")
        def wetterdashboard_tiles(date: str | None = None) -> dict:
            if not _is_configured(_load_config(deps.index)):
                raise HTTPException(status_code=409, detail=tr("Noch nicht eingerichtet"))
            stichtag = parse_stichtag(date, datetime.now(deps.tz).date())
            return {"tz": str(deps.tz), "at": stichtag.isoformat() if stichtag else None, "tiles": self.compute_tiles(at=date)}

        return router
