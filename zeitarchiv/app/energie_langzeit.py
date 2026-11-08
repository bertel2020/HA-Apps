"""Energiedashboard, Reiter „Langzeit“: Vergleichstabelle Zeiträume gegen das Vorjahr (Roadmap 1.45).

Die Tabelle summiert die Zählerrollen (Erzeugung, Einspeisung, Netzbezug, Speicher) je Zeitfenster
direkt aus den Tageszeilen der Zähler und leitet den Verbrauch daraus ab. Sie ruft bewusst nicht
``compute_flow`` auf: Das würde je Spalte und Vorjahr den ganzen Fluss samt Verbrauchern rechnen.

Der Verbrauch ist hier die Bilanz Erzeugung + Bezug + Entladung − Einspeisung − Ladung, dieselbe
Rechnung wie in Trends und Monatsverlauf. Die Live-Ansicht rechnet bei einer rechnerisch negativen
Grundlast anders (Summe der gemessenen Verbraucher), siehe die Tests in test_energiedashboard_flow.py.

Die Monatswerte über alle Jahre (Monatsvergleich und Matrix „Jahre × Monate“) kommen aus dem
Monatsbucket-Pfad des Energiedashboards (``entity_series``, dieselben Werte wie Trends und
Monatsverlauf des Berichts). Messlücken und Zählersprünge je Monat stammen aus den Tageszählern
des Gesamt-Caches (``data_quality.monthly_findings``), ohne Rohdaten zu lesen.

Die Zeitfenster rechnet period_columns (wie beim Wetterdashboard); die Zeilen und ihre Summen sind
Sache dieses Moduls. Private Funktionen aus ``storage.query`` werden wie im Energiedashboard bewusst
direkt genutzt.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from . import data_quality, period_columns
from .formatting import entity_display_name
from .i18n import N_, tr
from .storage import query as query_mod
from .storage import rollup as rollup_mod
from .storage.index import Index

COLUMN_KEYS = ("monat", "vormonat", "jahr")
#: Zeiträume, die die Tabelle „Verbraucher × Jahre“ zusätzlich zu den Jahren anbietet. „Dieses Jahr“ fehlt: Es ist das
#: laufende Jahr, das als Jahresspalte schon bis heute zählt (dasselbe Fenster, dieselben Werte).
CONSUMER_PERIODS = ("monat", "vormonat")
#: Spalte -> (Zeitraum für query_mod._window, Versatz)
COLUMN_WINDOWS = {
    "vormonat": ("month", -1),
    "monat": ("month", 0),
    "jahr": ("year", 0),
}
#: Zeilen: Schlüssel, Gruppe, Beschriftung, Art (energy = kWh, pct = Prozent mit Differenz in Prozentpunkten, spec =
#: kWh je kWp) und ob mehr davon günstig ("up"), ungünstig ("down") oder neutral (None) ist.
#: Ausblendbare Bereiche des Reiters „Langzeit“ (Setup, `langzeit_ausblenden`): Blöcke der Vergleichstabelle und Karten.
#: Der Block „energie“ bleibt immer, sonst wäre die Tabelle leer.
TABLE_BLOCKS = ("energie", "quoten", "speicher", "pv")
CARD_BLOCKS = ("monatsvergleich", "matrix", "veraenderungen", "rekorde", "kenntage")
BLOCK_BY_GROUP = {"Energie": "energie", "Quoten": "quoten", "Speicher": "speicher", "PV-Anlage, wetterbereinigt": "pv"}


def hidden_blocks(config: dict) -> set[str]:
    """Im Setup abgewählte Bereiche (nur bekannte Schlüssel, „energie“ nie)."""
    stored = config.get("langzeit_ausblenden")
    allowed = set(TABLE_BLOCKS[1:]) | set(CARD_BLOCKS)
    return {key for key in stored if key in allowed} if isinstance(stored, list) else set()


ROWS: tuple[dict, ...] = (
    {"key": "erzeugung", "group": N_("Energie"), "label": N_("Erzeugung"), "better": "up", "kind": "energy"},
    {"key": "einspeisung", "group": N_("Energie"), "label": N_("Einspeisung"), "better": "up", "kind": "energy"},
    {"key": "netzbezug", "group": N_("Energie"), "label": N_("Netzbezug"), "better": "down", "kind": "energy"},
    {"key": "verbrauch", "group": N_("Energie"), "label": N_("Verbrauch"), "better": "down", "kind": "energy"},
    {"key": "eigenverbrauch", "group": N_("Quoten"), "label": N_("Eigenverbrauch"), "better": "up", "kind": "pct"},
    {"key": "autarkie", "group": N_("Quoten"), "label": N_("Autarkie"), "better": "up", "kind": "pct"},
    {"key": "ladung", "group": N_("Speicher"), "label": N_("Ladung"), "better": None, "kind": "energy"},
    {"key": "entladung", "group": N_("Speicher"), "label": N_("Entladung"), "better": None, "kind": "energy"},
    {"key": "spezifisch", "group": N_("PV-Anlage, wetterbereinigt"), "label": N_("Spezifischer Ertrag"),
     "better": "up", "kind": "spec"},
    {"key": "einstrahlung", "group": N_("PV-Anlage, wetterbereinigt"), "label": N_("Ertrag je Einstrahlung"),
     "better": "up", "kind": "pct"},
)
#: Anteil der Stunden mit Einstrahlungswert, ab dem ein Zeitraum für den Vergleich zählt (Lücken des Sensors
#: ließen die Einstrahlung sonst zu klein und den Ertrag je Einstrahlung zu groß erscheinen).
MIN_RADIATION_COVERAGE = 0.8


#: Höchstzahl Jahre in Monatsvergleich und Matrix (je Jahr und Entität eine Abfrage).
MAX_YEARS = 15
MONTH_ROLES = ("erzeugung", "einspeisung", "netzbezug", "verbrauch")
#: Entitäten einer Rolle, die zusammen die Rolle „Verbrauch“ bilden (Bilanz).
BALANCE_ROLES = ("erzeugung", "netzbezug", "einspeisung", "entladung", "ladung")
#: Messlücken sind je Entität oft Dauerzustand (ein Zähler, der stündlich meldet, hat bei 15 Minuten Schwelle in jedem
#: Monat Hunderte). Markiert wird ein Monat deshalb nur, wenn er deutlich über dem üblichen Wert dieser Rolle liegt:
#: mindestens GAP_MIN und GAP_FACTOR mal der Median aller Monate. Zählerrückgänge werden immer markiert.
GAP_FACTOR = 3
GAP_MIN = 10
#: Rekorde erscheinen erst, wenn das Archiv der Rollen mindestens so viele Tage umfasst.
RECORD_MIN_DAYS = 365
#: Verbrauch, ab dem ein Tag für „fast autark“ zählt (bei weniger ist der Anteil Zufall).
CONSUMPTION_MIN = 0.5
#: Schwellen der Kenntage (Setup des Energiedashboards, leer = Vorgabe): Autarkie ab … %, Sonnentage ab … kWh je kWp,
#: trübe Tage unter … % des Tagesmittels, Speicher stark genutzt ab … kWh Entladung, kaum genutzt unter … kWh.
#: 100 % Autarkie kommt ohne großen Speicher praktisch nie vor, deshalb Vorgabe 90.
KENNTAGE_DEFAULTS = {"autark": 90.0, "sonnig": 4.0, "truebe": 10.0, "speicher_stark": 80.0, "speicher_schwach": 10.0}
KENNTAGE_LIMITS = {
    "autark": (50.0, 100.0), "sonnig": (1.0, 8.0), "truebe": (1.0, 50.0),
    "speicher_stark": (10.0, 300.0), "speicher_schwach": (1.0, 50.0),
}
#: Kenntage in Anzeigereihenfolge: Schlüssel, Gruppe, Rollen, deren Wert der Tag braucht. Die Felder bilden Paare.
KENNTAGE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("fast_autark", "eigenversorgung", ("netzbezug",)),
    ("ueberschuss", "eigenversorgung", ("einspeisung",)),
    ("sonnig", "erzeugung", ("erzeugung",)),
    ("truebe", "erzeugung", ("erzeugung",)),
    ("speicher_stark", "speicher", ("entladung",)),
    ("speicher_schwach", "speicher", ("entladung",)),
)
#: Lebensdauer des Tage-Zwischenspeichers (der Kalender liest dieselben Tage noch einmal).
DAYS_CACHE_SECONDS = 300
#: Verbraucher der Veränderungsliste (nach Betrag der Änderung).
CHANGES_LIMIT = 8
#: Rekordzeilen: Schlüssel, Rolle (Verbrauch rechnet sich aus der Bilanz), Beschriftung.
RECORDS: tuple[tuple[str, str, str], ...] = (
    ("erzeugung", "erzeugung", N_("Stärkster Erzeugungstag")),
    ("einspeisung", "einspeisung", N_("Höchste Einspeisung")),
    ("verbrauch", "verbrauch", N_("Höchster Verbrauch")),
    ("netzbezug", "netzbezug", N_("Höchster Netzbezug")),
)


class TableRequest(BaseModel):
    columns: list[str] = Field(default_factory=lambda: list(COLUMN_KEYS))
    vorjahr: bool = True
    mode: str = "bis_jetzt"
    #: Stichtag (JJJJ-MM-TT): die Tabelle zeigt dann die Zeiträume zu diesem Tag statt zu „jetzt“.
    at: str | None = None


class ConsumersRequest(BaseModel):
    #: Stichtag (JJJJ-MM-TT) der Rangliste „Verbraucher im Jahr“, wie in der Vergleichstabelle.
    at: str | None = None
    #: Ohne Monatswerte nur das Fenster der Rangliste: Die Stichtag-Leiste lädt dann nicht alle Jahre neu.
    months: bool = True
    #: Zeiträume (aus CONSUMER_PERIODS) für die Tabelle „Verbraucher × Jahre“: je Verbraucher Wert und Vorjahreswert.
    periods: list[str] = Field(default_factory=lambda: list(CONSUMER_PERIODS))


def role_entities(config: dict) -> dict[str, list[str]]:
    """Entitäten je Zählerrolle aus der Konfiguration des Energiedashboards."""
    speicher = config.get("speicher") or []
    return {
        "erzeugung": [e["entity_id"] for e in config.get("erzeuger") or [] if e.get("entity_id")],
        "einspeisung": [config["einspeisung"]] if config.get("einspeisung") else [],
        "netzbezug": [config["netzbezug"]] if config.get("netzbezug") else [],
        "entladung": [s["entladen_entity_id"] for s in speicher if s.get("entladen_entity_id")],
        "ladung": [s["laden_entity_id"] for s in speicher if s.get("laden_entity_id")],
    }


def pv_entities(config: dict) -> list[tuple[str, float]]:
    """Erzeuger mit Anlagenleistung (Entität, kWp). Nur sie gehen in die wetterbereinigten Zeilen ein: Ertrag und
    Leistung müssen aus denselben Erzeugern stammen, sonst stimmt das Verhältnis nicht."""
    return [
        (e["entity_id"], float(e["kwp"]))
        for e in config.get("erzeuger") or []
        if e.get("entity_id") and e.get("kwp") and float(e["kwp"]) > 0
    ]


def is_configured(config: dict) -> bool:
    roles = role_entities(config)
    return bool(roles["erzeugung"] or roles["netzbezug"])


def kenntage_thresholds(config: dict) -> dict[str, float]:
    """Schwellen der Kenntage: Setup-Werte (leer = Vorgabe), auf sinnvolle Grenzen gebracht."""
    stored = config.get("kenntage") if isinstance(config.get("kenntage"), dict) else {}
    out: dict[str, float] = {}
    for key, default in KENNTAGE_DEFAULTS.items():
        try:
            value = float(stored.get(key, default))
        except (TypeError, ValueError):
            value = default
        low, high = KENNTAGE_LIMITS[key]
        out[key] = min(max(value, low), high)
    return out


def total_kwp(config: dict) -> float | None:
    """Summe der Anlagenleistung, nur wenn jeder Erzeuger eine hat (sonst passen Ertrag und Leistung nicht zusammen)."""
    erzeuger = [e for e in config.get("erzeuger") or [] if e.get("entity_id")]
    if not erzeuger or len(pv_entities(config)) != len(erzeuger):
        return None
    return sum(kwp for _entity, kwp in pv_entities(config))


def kenntage_available(config: dict, capacity: float | None = None) -> tuple[str, ...]:
    """Kenntage, für die die Rollen da sind: Sonnentage brauchen die Anlagenleistung, das Speicherpaar einen Speicher mit
    bekannter Kapazität (die Schwellen sind Prozent der Kapazität)."""
    roles = role_entities(config)
    out = []
    for key, _group, needs in KENNTAGE:
        if not all(roles.get(role) for role in needs):
            continue
        if key == "sonnig" and total_kwp(config) is None:
            continue
        if key.startswith("speicher") and not capacity:
            continue
        out.append(key)
    return tuple(out)


def kenntag_hit(
    key: str, day: dict, thresholds: dict[str, float], average: float | None, kwp: float | None,
    capacity: float | None = None,
) -> bool | None:
    """Ob der Tag den Kenntag trifft; None, wenn ein dafür nötiger Wert fehlt (der Tag zählt dann nicht mit)."""
    if key == "fast_autark":
        if day["netzbezug"] is None:
            return None
        return day["verbrauch"] >= CONSUMPTION_MIN and day["netzbezug"] <= day["verbrauch"] * (100 - thresholds["autark"]) / 100 + 1e-9
    if key == "ueberschuss":
        return None if day["einspeisung"] is None else day["einspeisung"] > day["verbrauch"] > 0
    if key == "sonnig":
        return None if day["erzeugung"] is None or not kwp else day["erzeugung"] / kwp >= thresholds["sonnig"]
    if key == "truebe":
        return None if day["erzeugung"] is None or average is None else day["erzeugung"] < average * thresholds["truebe"] / 100
    if key == "speicher_stark":
        return None if day.get("entladung") is None or not capacity else day["entladung"] / capacity * 100 >= thresholds["speicher_stark"]
    if key == "speicher_schwach":
        return None if day.get("entladung") is None or not capacity else day["entladung"] / capacity * 100 < thresholds["speicher_schwach"]
    return None


def top_consumers(config: dict) -> list[dict]:
    """Verbraucher der obersten Ebene (ohne Zwischenzähler darüber). Geräte hinter einem Zwischenzähler fehlen:
    ihr Wert steckt im Zwischenzähler und zählte sonst doppelt."""
    return [c for c in config.get("verbraucher") or [] if c.get("entity_id") and not c.get("parent")]


def merge_findings(
    findings: dict[str, dict], entity_ids, value_months: set[str],
) -> tuple[dict[str, dict[str, int]], int]:
    """Befunde mehrerer Entitäten je Monat zusammengefasst (``data_quality.monthly_findings``) plus die Zahl der
    noch nicht geprüften Entitäten. Lücken bleiben nur in Monaten stehen, die deutlich über dem üblichen Wert der
    Monate mit Werten (value_months, „JJJJ-MM“) liegen, siehe GAP_FACTOR; Zählerrückgänge bleiben immer."""
    months: dict[str, dict[str, int]] = {}
    unchecked = 0
    for entity_id in sorted(set(entity_ids)):
        info = findings.get(entity_id)
        if info is None or not info["measured"]:
            unchecked += 1
            continue
        for month_key, counts in info["months"].items():
            bucket = months.setdefault(month_key, {})
            for kind, amount in counts.items():
                bucket[kind] = bucket.get(kind, 0) + amount
    typical = statistics.median([months.get(key, {}).get("gaps", 0) for key in value_months] or [0])
    limit = max(GAP_MIN, GAP_FACTOR * typical)
    for key in list(months):
        counts = months[key]
        if "gaps" in counts:
            if counts["gaps"] < limit:
                del counts["gaps"]
            else:
                counts["gaps_typical"] = int(typical)
        if not counts:
            del months[key]
    return months, unchecked


def balance(parts: dict[str, float | None]) -> float | None:
    """Verbrauch als Bilanz; None, wenn weder Erzeugung noch Netzbezug Werte haben (nie 0 vortäuschen).

    Bezug und Einspeisung zählen nicht negativ, wie in den Kacheln und im Monatsverlauf."""
    if parts["erzeugung"] is None and parts["netzbezug"] is None:
        return None
    return (
        (parts["erzeugung"] or 0.0)
        + max(parts["netzbezug"] or 0.0, 0.0)
        + (parts["entladung"] or 0.0)
        - max(parts["einspeisung"] or 0.0, 0.0)
        - (parts["ladung"] or 0.0)
    )


class EnergieLangzeitService:
    def _consumer_name(self, consumer: dict) -> str:
        """Eigener Name, sonst Friendly-Name der Entität, die rohe ID nur als letzter Ausweg."""
        given = (consumer.get("name") or "").strip()
        if given:
            return given
        entity = self.index.get_entity(consumer["entity_id"])
        if entity is None:
            return consumer["entity_id"]
        return entity_display_name(consumer["entity_id"], entity["friendly_name"], entity["custom_name"])

    def __init__(
        self, data_dir: Path, index: Index, tz: ZoneInfo,
        entity_series: Callable[..., tuple[dict[float, float], bool]] | None = None,
        radiation_entity: Callable[[], str] | None = None,
        storage_capacity: Callable[[dict], float | None] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.index = index
        self.tz = tz
        #: (entity_id, range_key, offset, now, read_cache) -> (Bucket-Zeitstempel -> Wert, veraltet?); die
        #: Monatsbuckets des Energiedashboards. Ohne Angabe sind Monatswerte nicht verfügbar.
        self.entity_series = entity_series
        #: Entität der Strahlung (W/m²) aus dem Wetterdashboard; ohne sie entfallen die wetterbereinigten Zeilen.
        self.radiation_entity = radiation_entity
        #: config -> Summe der Speicherkapazität in kWh (None, wenn sie nicht bei jedem Speicher bekannt ist). Ohne sie entfällt
        #: das Speicherpaar der Kenntage.
        self.storage_capacity = storage_capacity
        #: (Schlüssel, Zeitpunkt, Tage): die Tageswerte, die der Jahreskalender der Kenntage nach den Kenntagen noch einmal braucht.
        self._days_cache: tuple[tuple, float, list[dict]] | None = None

    def _rows(
        self, entity_id: str, start: datetime, end: datetime, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> list[rollup_mod.FineRow]:
        if end <= start:
            return []
        return query_mod._query_completed_plus_live_fine(  # noqa: SLF001 — siehe Modul-Docstring
            self.data_dir, self.index, entity_id, "counter", "line",
            start, end, self.tz, now_local, read_cache,
        )

    def window_total(
        self, entity_ids: list[str], start: datetime, end: datetime, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> tuple[float | None, float | None]:
        """(Summe über alle Entitäten im Fenster, Beginn des Archivs). Ohne Werte ist die Summe None:
        fehlende Daten sind kein 0. Der Beginn des Archivs steht dabei, wenn er im Fenster liegt."""
        total: float | None = None
        first_in_window: float | None = None
        for entity_id in entity_ids:
            entity = self.index.get_entity(entity_id)
            if entity is None:
                continue
            rows = self._rows(entity_id, start, end, now_local, read_cache)
            if rows:
                total = (total or 0.0) + sum(row.value or 0.0 for row in rows)
            elif entity["first_ts"] and entity["first_ts"] > start.timestamp():
                first_in_window = min(first_in_window or entity["first_ts"], entity["first_ts"])
        return (round(total, 3) if total is not None else None), (first_in_window if total is None else None)

    def side_values(
        self, roles: dict[str, list[str]], start: datetime, end: datetime, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> tuple[dict[str, float | None], dict[str, float | None]]:
        """Summe je Rolle für ein Fenster plus Archivbeginn je Rolle."""
        sums: dict[str, float | None] = {}
        froms: dict[str, float | None] = {}
        for role, entity_ids in roles.items():
            sums[role], froms[role] = self.window_total(entity_ids, start, end, now_local, read_cache)
        return sums, froms

    def window_irradiation(
        self, entity_id: str, start: datetime, end: datetime, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> float | None:
        """Einstrahlung im Fenster in kWh/m² (Summe der Stundenmittel in W/m² mal 1 h); None, wenn weniger als
        MIN_RADIATION_COVERAGE der vergangenen Stunden einen Wert haben."""
        end = min(end, now_local)
        if end <= start:
            return None
        rows = query_mod._query_completed_plus_live_fine(  # noqa: SLF001 — siehe Modul-Docstring
            self.data_dir, self.index, entity_id, "standard", "line", start, end, self.tz, now_local, read_cache,
        )
        values = [row.value for row in rows if row.value is not None]
        expected_hours = (end - start).total_seconds() / 3600
        if not values or len(values) < MIN_RADIATION_COVERAGE * expected_hours:
            return None
        return sum(values) / 1000.0

    def side_metrics(
        self, config: dict, roles: dict[str, list[str]], start: datetime, end: datetime, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> tuple[dict[str, float | None], dict[str, float | None]]:
        """Alle Kennzahlen eines Fensters (Zeilenschlüssel -> Wert) plus Archivbeginn je Rolle."""
        sums, froms = self.side_values(roles, start, end, now_local, read_cache)
        metrics: dict[str, float | None] = {
            "erzeugung": sums["erzeugung"],
            "einspeisung": max(sums["einspeisung"], 0.0) if sums["einspeisung"] is not None else None,
            "netzbezug": max(sums["netzbezug"], 0.0) if sums["netzbezug"] is not None else None,
            "ladung": sums["ladung"],
            "entladung": sums["entladung"],
        }
        verbrauch = balance(sums)
        metrics["verbrauch"] = round(verbrauch, 3) if verbrauch is not None else None
        # Dieselben Formeln wie in compute_flow, auf 0 bis 100 begrenzt (Rundungsrauschen bei kleinen Summen).
        metrics["autarkie"] = None
        if metrics["verbrauch"] and metrics["verbrauch"] > 0 and metrics["netzbezug"] is not None:
            metrics["autarkie"] = round(max(0.0, min(1.0, 1 - metrics["netzbezug"] / metrics["verbrauch"])) * 100, 1)
        metrics["eigenverbrauch"] = None
        if metrics["erzeugung"] and metrics["erzeugung"] > 0:
            export = metrics["einspeisung"] or 0.0
            metrics["eigenverbrauch"] = round(max(0.0, min(1.0, (metrics["erzeugung"] - export) / metrics["erzeugung"])) * 100, 1)
        metrics["spezifisch"] = None
        metrics["einstrahlung"] = None
        pv = pv_entities(config)
        if pv:
            kwp = sum(power for _eid, power in pv)
            yield_kwh, _first = self.window_total([eid for eid, _power in pv], start, end, now_local, read_cache)
            if yield_kwh is not None:
                metrics["spezifisch"] = round(yield_kwh / kwp, 3)
                radiation = self.radiation_entity() if self.radiation_entity else ""
                if radiation:
                    irradiation = self.window_irradiation(radiation, start, end, now_local, read_cache)
                    if irradiation:
                        metrics["einstrahlung"] = round(yield_kwh / (kwp * irradiation) * 100, 1)
        return metrics, froms

    def visible_rows(self, config: dict, roles: dict[str, list[str]]) -> list[dict]:
        """Zeilen, für die die Einrichtung ausreicht; fehlende Rollen blenden ihre Zeilen aus."""
        have_bus = bool(roles["erzeugung"] or roles["netzbezug"])
        radiation = bool(self.radiation_entity and self.radiation_entity())
        pv = bool(pv_entities(config))
        shown = {
            "verbrauch": have_bus, "autarkie": have_bus, "eigenverbrauch": bool(roles["erzeugung"]),
            "spezifisch": pv, "einstrahlung": pv and radiation,
        }
        hidden = hidden_blocks(config)
        return [
            row for row in ROWS
            if shown.get(row["key"], bool(roles.get(row["key"]))) and BLOCK_BY_GROUP[row["group"]] not in hidden
        ]

    def compute_table(self, config: dict, body: TableRequest, now_local: datetime | None = None) -> dict:
        now_local = now_local or datetime.now(self.tz)
        columns = [key for key in COLUMN_KEYS if key in body.columns]
        mode = body.mode if body.mode in period_columns.MODES else "bis_jetzt"
        stichtag = period_columns.parse_stichtag(body.at, now_local.date())
        anchor = period_columns.stichtag_anchor(stichtag, self.tz)
        windows = [
            period_columns.column_windows(COLUMN_WINDOWS, key, now_local, mode, anchor) for key in columns
        ]
        roles = role_entities(config)
        read_cache = query_mod.QueryReadCache()
        visible = self.visible_rows(config, roles)
        cells: dict[str, dict[str, dict]] = {row["key"]: {} for row in visible}
        for win in windows:
            sides = [("cur", win["start"], win["end"])]
            if body.vorjahr:
                sides.append(("vj", win["vj_start"], win["vj_end"]))
            side_data = {
                side: self.side_metrics(config, roles, start, end, now_local, read_cache) for side, start, end in sides
            }
            for row in visible:
                key = row["key"]
                cell: dict = {}
                for side, _start, _end in sides:
                    metrics, froms = side_data[side]
                    cell[side] = metrics[key]
                    # Fehlende Daten sind kein 0: Archivbeginn mitgeben, wenn er im Fenster liegt (nur Zählerrollen).
                    if cell[side] is None and froms.get(key):
                        cell[f"{side}_from"] = froms[key]
                cells[key][win["key"]] = cell
        return {
            "tz": str(self.tz),
            "now": now_local.timestamp(),
            "mode": mode,
            "at": stichtag.isoformat() if stichtag else None,
            "today": now_local.date().isoformat(),
            "vorjahr": body.vorjahr,
            "unit": "kWh",
            "columns": [
                {
                    "key": win["key"], "range": win["range"], "partial": win["partial"],
                    "cur": {"start": win["start"].timestamp(), "end": win["end"].timestamp()},
                    "vj": {"start": win["vj_start"].timestamp(), "end": win["vj_end"].timestamp()},
                }
                for win in windows
            ],
            "rows": [
                {
                    "key": row["key"], "group": tr(row["group"]), "label": tr(row["label"]),
                    "better": row["better"], "kind": row["kind"], "cells": cells[row["key"]],
                }
                for row in visible
            ],
        }

    # -- Monatswerte über alle Jahre -----------------------------------------------------

    def _entity_months(
        self, entity_id: str, years: list[int], now_local: datetime, read_cache: query_mod.QueryReadCache,
    ) -> dict[tuple[int, int], float]:
        """(Jahr, Monat) -> Wert für alle Monate, in denen die Entität archiviert wurde. Monate vor dem
        Archivbeginn und in der Zukunft fehlen, ein Monat ohne Verbrauch steht mit 0 darin."""
        entity = self.index.get_entity(entity_id)
        if entity is None or not entity["first_ts"] or self.entity_series is None:
            return {}
        values: dict[tuple[int, int], float] = {}
        now_ts = now_local.timestamp()
        for year in years:
            series, _stale = self.entity_series(entity_id, "year", year - now_local.year, now_local, read_cache)
            by_month: dict[int, float] = {}
            for ts, value in series.items():
                moment = datetime.fromtimestamp(ts, self.tz)
                if moment.year == year:
                    by_month[moment.month] = by_month.get(moment.month, 0.0) + value
            for month in range(1, 13):
                start = datetime(year, month, 1, tzinfo=self.tz)
                end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=self.tz)
                if end.timestamp() > entity["first_ts"] and start.timestamp() <= now_ts:
                    values[(year, month)] = by_month.get(month, 0.0)
        return values

    def _latest_start(self, entity_ids) -> float | None:
        """Beginn des Archivs der zuletzt begonnenen Entität (erst ab da sind alle zusammen erfasst)."""
        starts = [
            entity["first_ts"] for entity_id in set(entity_ids)
            if (entity := self.index.get_entity(entity_id)) is not None and entity["first_ts"]
        ]
        return max(starts) if starts else None

    def _partial_start(self, entity_ids) -> list[int] | None:
        """Monat, in dem das Archiv beginnt, wenn er nicht am 1. anfängt: er ist nur zum Teil erfasst und taugt
        nicht für einen Vergleich. Bei mehreren Entitäten gilt die zuletzt begonnene."""
        latest = self._latest_start(entity_ids)
        if latest is None:
            return None
        moment = datetime.fromtimestamp(latest, self.tz)
        return [moment.year, moment.month] if moment.day > 1 else None

    def _years(self, entity_ids, now_local: datetime) -> list[int]:
        """Jahre ab dem frühesten Archivbeginn der Entitäten bis heute, neuestes zuerst, höchstens MAX_YEARS."""
        first_years = [
            datetime.fromtimestamp(entity["first_ts"], self.tz).year
            for entity_id in set(entity_ids)
            if (entity := self.index.get_entity(entity_id)) is not None and entity["first_ts"]
        ]
        first_year = max(min(first_years, default=now_local.year), now_local.year - MAX_YEARS + 1)
        return list(range(now_local.year, first_year - 1, -1))

    def _role_month_sums(
        self, roles: dict[str, list[str]], years: list[int], now_local: datetime, read_cache: query_mod.QueryReadCache,
    ) -> dict[str, dict[tuple[int, int], float]]:
        """Summe je Rolle und Monat; ein Monat fehlt, wenn keine Entität der Rolle in ihm archiviert war."""
        sums: dict[str, dict[tuple[int, int], float]] = {}
        for role, entity_ids in roles.items():
            merged: dict[tuple[int, int], float] = {}
            for entity_id in entity_ids:
                for key, value in self._entity_months(entity_id, years, now_local, read_cache).items():
                    merged[key] = merged.get(key, 0.0) + value
            sums[role] = merged
        return sums

    def compute_months(self, config: dict, now_local: datetime | None = None) -> dict:
        """Monatswerte je Rolle über alle Jahre des Archivs (neuestes Jahr zuerst), dazu Messlücken und
        Zählersprünge je Monat. Ein Monat ohne Daten ist None, nie 0."""
        now_local = now_local or datetime.now(self.tz)
        roles = role_entities(config)
        years = self._years({e for ids in roles.values() for e in ids}, now_local)
        read_cache = query_mod.QueryReadCache()

        # Summe je Rolle und Monat; None, wenn keine Entität der Rolle in diesem Monat Daten hatte.
        sums = self._role_month_sums(roles, years, now_local, read_cache)

        def month_values(role: str, year: int) -> list[float | None]:
            out: list[float | None] = []
            for month in range(1, 13):
                if role == "verbrauch":
                    parts = {part: sums[part].get((year, month)) for part in BALANCE_ROLES}
                    value = balance(parts)
                else:
                    value = sums[role].get((year, month))
                    if value is not None and role in ("einspeisung", "netzbezug"):
                        value = max(value, 0.0)
                out.append(round(value, 3) if value is not None else None)
            return out

        every_entity = sorted({e for ids in roles.values() for e in ids})
        findings = data_quality.monthly_findings(self.index, every_entity)

        def month_keys(role_keys: tuple[str, ...]) -> set[str]:
            return {f"{year}-{month:02d}" for key in role_keys for (year, month) in sums[key]}

        def partial_start(role_keys: tuple[str, ...]) -> list[int] | None:
            return self._partial_start({e for key in role_keys for e in roles[key]})

        def role_findings(role_keys: tuple[str, ...]) -> tuple[dict[str, dict[str, int]], int]:
            # Nur auffällige Lücken stehen drin, siehe GAP_FACTOR.
            return merge_findings(findings, {e for key in role_keys for e in roles[key]}, month_keys(role_keys))

        out_roles = []
        for row in ROWS:
            key = row["key"]
            if key not in MONTH_ROLES:
                continue
            keys = BALANCE_ROLES if key == "verbrauch" else (key,)
            if not any(roles[k] for k in keys if k != "verbrauch"):
                continue
            if key == "verbrauch" and not (roles["erzeugung"] or roles["netzbezug"]):
                continue
            flags, unchecked = role_findings(keys)
            single = roles[key][0] if key != "verbrauch" and len(roles[key]) == 1 else None
            out_roles.append({
                "key": key, "label": tr(row["label"]), "better": row["better"],
                "entity_id": single,
                "values": {str(year): month_values(key, year) for year in years},
                "partial_start": partial_start(keys),
                "flags": flags, "unchecked": unchecked,
            })
        return {
            "tz": str(self.tz), "now": now_local.timestamp(), "unit": "kWh",
            "current": {"year": now_local.year, "month": now_local.month, "day": now_local.day},
            "years": years, "roles": out_roles,
        }

    # -- Tageswerte: Rekorde und Kenntage -------------------------------------------------

    def _daily_roles(
        self, roles: dict[str, list[str]], start: datetime, end: datetime, now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> dict[str, dict[date, float]]:
        """Tagessumme je Rolle (lokales Datum -> kWh). Ein Tag zählt für eine Rolle nur, wenn jede ihrer Entitäten,
        die an diesem Tag schon archiviert hat, einen Wert hat; sonst fehlt er."""
        result: dict[str, dict[date, float]] = {}
        for role, entity_ids in roles.items():
            per_entity: list[tuple[float, dict[date, float]]] = []
            for entity_id in entity_ids:
                entity = self.index.get_entity(entity_id)
                if entity is None or not entity["first_ts"]:
                    continue
                points = query_mod.query_daily_series(
                    self.data_dir, self.index, entity_id, start, end, self.tz, now_local, read_cache,
                )
                per_entity.append((
                    entity["first_ts"],
                    {datetime.fromtimestamp(p.day_start, self.tz).date(): (p.value or 0.0) for p in points},
                ))
            days: dict[date, float] = {}
            for day in {d for _first, series in per_entity for d in series}:
                midnight = datetime(day.year, day.month, day.day, tzinfo=self.tz).timestamp()
                expected = [series for first, series in per_entity if first < midnight + 86400]
                if expected and all(day in series for series in expected):
                    days[day] = sum(series[day] for series in expected)
            result[role] = days
        return result

    def usable_days(self, config: dict, now_local: datetime | None = None) -> list[dict]:
        """usable_days_uncached, mit kurzem Zwischenspeicher (gleiche Rollen, gleicher Tag)."""
        now_local = now_local or datetime.now(self.tz)
        roles = role_entities(config)
        key = (tuple((role, tuple(ids)) for role, ids in sorted(roles.items())), now_local.date())
        cached = self._days_cache
        if cached and cached[0] == key and time.monotonic() - cached[1] < DAYS_CACHE_SECONDS:
            return cached[2]
        days = self.usable_days_uncached(config, now_local)
        self._days_cache = (key, time.monotonic(), days)
        return days

    def usable_days_uncached(self, config: dict, now_local: datetime | None = None) -> list[dict]:
        """Tage mit allen Werten, ohne den Tag nach einem fehlenden Tag. Fehlt ein Tag ganz, bekäme der nächste
        Messwert die ganze Differenz zugeschlagen und gäbe einen Schein-Rekord. Der heutige Tag fehlt (unvollständig).

        Je Tag: Datum und Erzeugung, Einspeisung, Netzbezug, Verbrauch (Bilanz) in kWh; Rollen ohne Entität bleiben None."""
        now_local = now_local or datetime.now(self.tz)
        roles = role_entities(config)
        used = {role: ids for role, ids in roles.items() if ids}
        firsts = [
            entity["first_ts"]
            for ids in used.values() for entity_id in ids
            if (entity := self.index.get_entity(entity_id)) is not None and entity["first_ts"]
        ]
        if not firsts:
            return []
        start = datetime.fromtimestamp(min(firsts), self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if end <= start:
            return []
        daily = self._daily_roles(used, start, end, now_local, query_mod.QueryReadCache())
        # Eine Rolle, die erst später dazukam (z. B. ein nachgerüsteter Speicher), gilt für frühere Tage nicht.
        role_start = {
            role: min(
                entity["first_ts"] for entity_id in ids
                if (entity := self.index.get_entity(entity_id)) is not None and entity["first_ts"]
            )
            for role, ids in used.items()
        }
        complete: dict[date, dict[str, float | None]] = {}
        for day in sorted({d for series in daily.values() for d in series}):
            midnight = datetime(day.year, day.month, day.day, tzinfo=self.tz).timestamp()
            expected = [role for role in used if role_start[role] < midnight + 86400]
            if not expected or not all(day in daily[role] for role in expected):
                continue
            complete[day] = {role: (daily[role][day] if role in expected else None) for role in BALANCE_ROLES}
        first_day = start.date()
        out: list[dict] = []
        for day, parts in complete.items():
            if day > first_day and (day - timedelta(days=1)) not in complete:
                continue
            verbrauch = balance(parts)
            if verbrauch is None:
                continue
            out.append({
                "date": day,
                "erzeugung": parts["erzeugung"],
                "einspeisung": max(parts["einspeisung"], 0.0) if parts["einspeisung"] is not None else None,
                "netzbezug": max(parts["netzbezug"], 0.0) if parts["netzbezug"] is not None else None,
                "verbrauch": verbrauch,
                "entladung": parts["entladung"],
            })
        return out

    def compute_records(self, config: dict, now_local: datetime | None = None) -> dict:
        """Rekordtage und Kenntage je Jahr aus den Tageswerten. Rekorde gibt es erst ab RECORD_MIN_DAYS Tagen Archiv."""
        now_local = now_local or datetime.now(self.tz)
        roles = role_entities(config)
        days = self.usable_days(config, now_local)
        today = now_local.date()
        span = (days[-1]["date"] - days[0]["date"]).days + 1 if days else 0
        records = []
        if span >= RECORD_MIN_DAYS:
            for key, role, label in RECORDS:
                candidates = [d for d in days if d[key] is not None]
                if not candidates:
                    continue
                # Gleichstand: der frühere Tag.
                top = max(candidates, key=lambda d: (d[key], -d["date"].toordinal()))
                single = roles[role][0] if role != "verbrauch" and len(roles.get(role, [])) == 1 else None
                records.append({
                    "key": key, "label": tr(label), "value": round(top[key], 3), "date": top["date"].isoformat(),
                    "entity_id": single, "offset": (top["date"] - today).days,
                })
        kenntage = self.compute_kenntage(config, days, today)
        return {
            "unit": "kWh",
            "since": days[0]["date"].isoformat() if days else None,
            "days": len(days), "span": span, "enough": span >= RECORD_MIN_DAYS,
            "records": records,
            "kenntage": kenntage,
        }

    def compute_kenntage(self, config: dict, days: list[dict], today: date) -> dict:
        """Kenntage je Jahr: Tage mit einer Eigenschaft (Schwellen aus dem Setup) und die Tage, an denen sie sich prüfen
        ließ. „Bis heute“ zählt in jedem Jahr nur bis zum Kalendertag von heute, damit das laufende Jahr vergleichbar ist."""
        thresholds = kenntage_thresholds(config)
        capacity = self.storage_capacity(config) if self.storage_capacity else None
        available = kenntage_available(config, capacity)
        cutoff = (today.month, today.day)
        years: dict[int, dict] = {}
        averages: dict[int, float] = {}
        for year in {d["date"].year for d in days}:
            produced = [d["erzeugung"] for d in days if d["date"].year == year and d["erzeugung"] is not None]
            if produced:
                averages[year] = sum(produced) / len(produced)
        kwp = total_kwp(config)
        for d in days:
            year = d["date"].year
            entry = years.setdefault(year, {"year": year, "fields": {
                key: {"days": 0, "hit": 0, "days_ytd": 0, "hit_ytd": 0} for key in available
            }})
            ytd = (d["date"].month, d["date"].day) <= cutoff
            for key in available:
                hit = kenntag_hit(key, d, thresholds, averages.get(year), kwp, capacity)
                if hit is None:
                    continue
                counts = entry["fields"][key]
                counts["days"] += 1
                counts["hit"] += hit
                if ytd:
                    counts["days_ytd"] += 1
                    counts["hit_ytd"] += hit
        return {
            "available": list(available),
            "groups": {key: group for key, group, _roles in KENNTAGE},
            "thresholds": thresholds, "kwp": kwp, "capacity": capacity,
            "today": today.isoformat(),
            "years": sorted(years.values(), key=lambda e: -e["year"]),
        }

    def compute_kenntage_days(self, config: dict, field: str, year: int, now_local: datetime | None = None) -> dict:
        """Die Tage eines Jahres, an denen sich der Kenntag prüfen ließ, mit Treffer und den Werten dazu (Jahreskalender)."""
        now_local = now_local or datetime.now(self.tz)
        thresholds = kenntage_thresholds(config)
        capacity = self.storage_capacity(config) if self.storage_capacity else None
        if field not in kenntage_available(config, capacity):
            return {"field": field, "year": year, "days": [], "average": None}
        days = [d for d in self.usable_days(config, now_local) if d["date"].year == year]
        produced = [d["erzeugung"] for d in days if d["erzeugung"] is not None]
        average = sum(produced) / len(produced) if produced else None
        kwp = total_kwp(config)
        out = []
        for d in days:
            hit = kenntag_hit(field, d, thresholds, average, kwp, capacity)
            if hit is None:
                continue
            out.append({
                "date": d["date"].isoformat(), "hit": bool(hit),
                "values": {k: round(d[k], 2) for k in ("erzeugung", "einspeisung", "netzbezug", "verbrauch", "entladung") if d.get(k) is not None},
            })
        return {"field": field, "year": year, "days": out, "average": round(average, 2) if average is not None else None}

    # -- Was hat sich verändert? -----------------------------------------------------------

    def compute_changes(self, config: dict, body: TableRequest, now_local: datetime | None = None, limit: int = CHANGES_LIMIT) -> dict:
        """Verbraucher mit der größten Änderung gegenüber dem Vorjahr im Zeitraum „Dieses Jahr“ (gleiche Fenster
        und gleicher Stichtag wie die Vergleichstabelle). Geräte hinter einem Zwischenzähler fehlen: ihr Wert
        steckt im Zwischenzähler. Der Rest bis zum Verbrauch laut Bilanz erscheint als „Übrige (Grundlast)“."""
        now_local = now_local or datetime.now(self.tz)
        mode = body.mode if body.mode in period_columns.MODES else "bis_jetzt"
        stichtag = period_columns.parse_stichtag(body.at, now_local.date())
        anchor = period_columns.stichtag_anchor(stichtag, self.tz)
        win = period_columns.column_windows(COLUMN_WINDOWS, "jahr", now_local, mode, anchor)
        roles = role_entities(config)
        read_cache = query_mod.QueryReadCache()
        consumers = top_consumers(config)
        items: list[dict] = []
        measured = {"cur": 0.0, "vj": 0.0}
        for consumer in consumers:
            values: dict[str, float | None] = {}
            for side, start, end in (("cur", win["start"], win["end"]), ("vj", win["vj_start"], win["vj_end"])):
                values[side], _first = self.window_total([consumer["entity_id"]], start, end, now_local, read_cache)
                measured[side] += values[side] or 0.0
            if values["cur"] is None and values["vj"] is None:
                continue
            items.append({
                "name": self._consumer_name(consumer), "entity_id": consumer["entity_id"],
                "cur": values["cur"], "vj": values["vj"],
            })
        total: dict[str, float | None] = {}
        for side, start, end in (("cur", win["start"], win["end"]), ("vj", win["vj_start"], win["vj_end"])):
            sums, _froms = self.side_values(roles, start, end, now_local, read_cache)
            total[side] = balance(sums)
        if consumers and total["cur"] is not None and total["vj"] is not None:
            rest = {side: max(total[side] - measured[side], 0.0) for side in ("cur", "vj")}
            items.append({"name": tr("Übrige (Grundlast)"), "entity_id": None, "cur": round(rest["cur"], 3), "vj": round(rest["vj"], 3)})
        for item in items:
            both = item["cur"] is not None and item["vj"] is not None
            item["diff"] = round(item["cur"] - item["vj"], 3) if both else None
            item["percent"] = round(item["diff"] / item["vj"] * 100, 1) if both and item["vj"] else None
        ranked = sorted((i for i in items if i["diff"] is not None), key=lambda i: -abs(i["diff"]))[:limit]
        return {
            "unit": "kWh", "mode": mode, "at": stichtag.isoformat() if stichtag else None,
            "cur": {"start": win["start"].timestamp(), "end": win["end"].timestamp()},
            "vj": {"start": win["vj_start"].timestamp(), "end": win["vj_end"].timestamp()},
            "items": ranked, "consumers": len(consumers),
        }

    # -- Verbraucher über die Jahre ------------------------------------------------------

    def compute_consumers(self, config: dict, body: ConsumersRequest, now_local: datetime | None = None) -> dict:
        """Verbraucher der obersten Ebene über die Jahre (Reiter „Langzeit“, Ansicht „Verbraucher“).

        Fenster: „Dieses Jahr“ bis zum Stichtag gegen dasselbe Stück des Vorjahres (wie compute_changes, immer
        „bis jetzt“), je Verbraucher und für den Verbrauch laut Bilanz. „Übrige (Grundlast)“ ist der Rest bis zur
        Bilanz, nie negativ. Mit ``months`` dazu je Verbraucher die Monatswerte aller Jahre, die Jahressummen,
        Befunde je Monat (Messlücken, Zählerrückgänge) und der Teilmonat am Archivbeginn.

        Ein gelöschter Verbraucher fehlt auch in den Vorjahren; sein Verbrauch steckt dann dort in „Übrige“.
        ``late`` markiert Verbraucher, deren Archiv erst nach dem der Bilanz beginnt: davor steckt ihr Verbrauch in
        „Übrige“, ein Vergleich über diesen Zeitpunkt hinweg ist dort verzerrt."""
        now_local = now_local or datetime.now(self.tz)
        stichtag = period_columns.parse_stichtag(body.at, now_local.date())
        anchor = period_columns.stichtag_anchor(stichtag, self.tz)
        win = period_columns.column_windows(COLUMN_WINDOWS, "jahr", now_local, "bis_jetzt", anchor)
        roles = role_entities(config)
        consumers = top_consumers(config)
        read_cache = query_mod.QueryReadCache()
        balance_ids = sorted({e for role in BALANCE_ROLES for e in roles[role]})
        balance_start = self._latest_start(balance_ids)
        entity_ids = [consumer["entity_id"] for consumer in consumers]
        values, measured, total, rest_values = self._consumer_window(win, entity_ids, roles, now_local, read_cache)

        items: list[dict] = []
        for consumer in consumers:
            entity_id = consumer["entity_id"]
            entity = self.index.get_entity(entity_id)
            first_ts = entity["first_ts"] if entity is not None and entity["first_ts"] else None
            items.append({
                "key": entity_id, "name": self._consumer_name(consumer), "entity_id": entity_id,
                "first_ts": first_ts, "partial_start": self._partial_start([entity_id]),
                # Ein Tag Spielraum: Zähler, die am selben Tag wie die Bilanz begonnen haben, verzerren nichts.
                "late": bool(first_ts and balance_start and first_ts > balance_start + 86400),
                **values[entity_id],
            })
        rest: dict = {
            "key": "rest", "name": tr("Übrige (Grundlast)"), "entity_id": None,
            "first_ts": balance_start, "partial_start": self._partial_start(balance_ids), "late": False,
            **rest_values,
        }

        out: dict = {
            "tz": str(self.tz), "now": now_local.timestamp(), "unit": "kWh",
            "at": stichtag.isoformat() if stichtag else None, "today": now_local.date().isoformat(),
            "current": {"year": now_local.year, "month": now_local.month, "day": now_local.day},
            "window": {
                "year": win["start"].year,
                "cur": {"start": win["start"].timestamp(), "end": win["end"].timestamp()},
                "vj": {"start": win["vj_start"].timestamp(), "end": win["vj_end"].timestamp()},
                "total": total,
                "measured": {side: round(value, 3) for side, value in measured.items()},
            },
            "consumers": items, "rest": rest,
        }
        periods = [key for key in CONSUMER_PERIODS if key in body.periods]
        if periods:
            out["periods"] = self._consumer_periods(periods, items, rest, roles, now_local, read_cache)
        if body.months:
            self._consumer_months(out, roles, balance_ids, now_local, read_cache)
        return out

    def _consumer_window(
        self, win: dict, entity_ids: list[str], roles: dict[str, list[str]], now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> tuple[dict[str, dict], dict[str, float], dict[str, float | None], dict[str, float | None]]:
        """Werte der Verbraucher für ein Fenster und dessen Vorjahr: (je Verbraucher {cur, vj}, Summe der Verbraucher,
        Verbrauch laut Bilanz, „Übrige“ = Rest bis zur Bilanz, nie negativ). Je Seite, „cur“ und „vj“."""
        sides = (("cur", win["start"], win["end"]), ("vj", win["vj_start"], win["vj_end"]))
        values: dict[str, dict] = {entity_id: {} for entity_id in entity_ids}
        measured = {"cur": 0.0, "vj": 0.0}
        total: dict[str, float | None] = {}
        for side, start, end in sides:
            for entity_id in entity_ids:
                values[entity_id][side], _first = self.window_total([entity_id], start, end, now_local, read_cache)
                measured[side] += values[entity_id][side] or 0.0
            sums, _froms = self.side_values(roles, start, end, now_local, read_cache)
            balanced = balance(sums)
            total[side] = round(balanced, 3) if balanced is not None else None
        rest = {
            side: round(max(total[side] - measured[side], 0.0), 3) if total[side] is not None else None
            for side in ("cur", "vj")
        }
        return values, measured, total, rest

    def _consumer_periods(
        self, keys: list[str], items: list[dict], rest: dict, roles: dict[str, list[str]], now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> list[dict]:
        """Monat und Vormonat je Verbraucher gegen denselben Zeitraum des Vorjahres („bis jetzt“ wie die Jahre):
        trägt je Verbraucher und für „Übrige“ ``periods[key] = {cur, vj}`` ein und liefert die Fenster und Summen."""
        entity_ids = [item["entity_id"] for item in items]
        for item in (*items, rest):
            item["periods"] = {}
        out: list[dict] = []
        for key in keys:
            win = period_columns.column_windows(COLUMN_WINDOWS, key, now_local, "bis_jetzt")
            values, measured, total, rest_values = self._consumer_window(win, entity_ids, roles, now_local, read_cache)
            for item in items:
                item["periods"][key] = values[item["entity_id"]]
            rest["periods"][key] = rest_values
            out.append({
                "key": key, "partial": win["partial"],
                "cur": {"start": win["start"].timestamp(), "end": win["end"].timestamp()},
                "vj": {"start": win["vj_start"].timestamp(), "end": win["vj_end"].timestamp()},
                "total": total, "measured": {side: round(value, 3) for side, value in measured.items()},
            })
        return out

    def _consumer_months(
        self, out: dict, roles: dict[str, list[str]], balance_ids: list[str], now_local: datetime,
        read_cache: query_mod.QueryReadCache,
    ) -> None:
        """Monatswerte, Jahressummen und Befunde je Verbraucher, für „Übrige“ und den Verbrauch laut Bilanz."""
        items, rest = out["consumers"], out["rest"]
        years = self._years(set(balance_ids) | {item["entity_id"] for item in items}, now_local)
        sums = self._role_month_sums({role: roles[role] for role in BALANCE_ROLES}, years, now_local, read_cache)

        def present_sum(values: list[float | None]) -> float | None:
            present = [value for value in values if value is not None]
            return round(sum(present), 3) if present else None

        total_values: dict[str, list[float | None]] = {}
        for year in years:
            row: list[float | None] = []
            for month in range(1, 13):
                value = balance({part: sums[part].get((year, month)) for part in BALANCE_ROLES})
                row.append(round(value, 3) if value is not None else None)
            total_values[str(year)] = row
        findings = data_quality.monthly_findings(self.index, sorted(set(balance_ids) | {i["entity_id"] for i in items}))

        measured_months = {str(year): [0.0] * 12 for year in years}
        for item in items:
            months = self._entity_months(item["entity_id"], years, now_local, read_cache)
            item["values"] = {
                str(year): [round(months[(year, m)], 3) if (year, m) in months else None for m in range(1, 13)]
                for year in years
            }
            item["year_sums"] = {year: present_sum(values) for year, values in item["values"].items()}
            for (year, month), value in months.items():
                measured_months[str(year)][month - 1] += value
            item["flags"], item["unchecked"] = merge_findings(
                findings, [item["entity_id"]], {f"{year}-{month:02d}" for (year, month) in months},
            )

        # „Übrige“ je Monat ist der Rest dieses Monats, je Jahr der Rest der Jahressummen (ein Monat mit
        # rechnerisch negativem Rest zieht das Jahr nicht unter die Summe der Verbraucher).
        rest["values"] = {
            year: [round(max(t - measured_months[year][i], 0.0), 3) if t is not None else None for i, t in enumerate(row)]
            for year, row in total_values.items()
        }
        total_sums = {year: present_sum(row) for year, row in total_values.items()}
        rest["year_sums"] = {
            year: (
                round(max(value - sum(item["year_sums"][year] or 0.0 for item in items), 0.0), 3)
                if value is not None else None
            )
            for year, value in total_sums.items()
        }
        balance_months = {f"{year}-{month:02d}" for part in BALANCE_ROLES for (year, month) in sums[part]}
        rest["flags"], rest["unchecked"] = merge_findings(findings, balance_ids, balance_months)
        out["years"] = years
        out["total"] = {"values": total_values, "year_sums": total_sums}
