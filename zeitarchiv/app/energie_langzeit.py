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
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from . import data_quality, period_columns
from .i18n import N_, tr
from .storage import query as query_mod
from .storage import rollup as rollup_mod
from .storage.index import Index

COLUMN_KEYS = ("vormonat", "monat", "jahr")
#: Spalte -> (Zeitraum für query_mod._window, Versatz)
COLUMN_WINDOWS = {
    "vormonat": ("month", -1),
    "monat": ("month", 0),
    "jahr": ("year", 0),
}
#: Zeilen: Schlüssel, Gruppe, Beschriftung, Art (energy = kWh, pct = Prozent mit Differenz in Prozentpunkten, spec =
#: kWh je kWp) und ob mehr davon günstig ("up"), ungünstig ("down") oder neutral (None) ist.
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
#: Kenntage: Autarkie 100 % heißt höchstens so viel Netzbezug (kWh) bei Verbrauch von mindestens CONSUMPTION_MIN;
#: „kaum PV“ heißt weniger als LOW_PV_KWH Erzeugung.
AUTARKY_EPSILON = 0.05
CONSUMPTION_MIN = 0.5
LOW_PV_KWH = 1.0
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
    def __init__(
        self, data_dir: Path, index: Index, tz: ZoneInfo,
        entity_series: Callable[..., tuple[dict[float, float], bool]] | None = None,
        radiation_entity: Callable[[], str] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.index = index
        self.tz = tz
        #: (entity_id, range_key, offset, now, read_cache) -> (Bucket-Zeitstempel -> Wert, veraltet?); die
        #: Monatsbuckets des Energiedashboards. Ohne Angabe sind Monatswerte nicht verfügbar.
        self.entity_series = entity_series
        #: Entität der Strahlung (W/m²) aus dem Wetterdashboard; ohne sie entfallen die wetterbereinigten Zeilen.
        self.radiation_entity = radiation_entity

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
        return [row for row in ROWS if shown.get(row["key"], bool(roles.get(row["key"])))]

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

    def compute_months(self, config: dict, now_local: datetime | None = None) -> dict:
        """Monatswerte je Rolle über alle Jahre des Archivs (neuestes Jahr zuerst), dazu Messlücken und
        Zählersprünge je Monat. Ein Monat ohne Daten ist None, nie 0."""
        now_local = now_local or datetime.now(self.tz)
        roles = role_entities(config)
        first_years = [
            datetime.fromtimestamp(entity["first_ts"], self.tz).year
            for entity_id in {e for ids in roles.values() for e in ids}
            if (entity := self.index.get_entity(entity_id)) is not None and entity["first_ts"]
        ]
        first_year = max(min(first_years, default=now_local.year), now_local.year - MAX_YEARS + 1)
        years = list(range(now_local.year, first_year - 1, -1))
        read_cache = query_mod.QueryReadCache()

        # Summe je Rolle und Monat; None, wenn keine Entität der Rolle in diesem Monat Daten hatte.
        sums: dict[str, dict[tuple[int, int], float]] = {}
        for role, entity_ids in roles.items():
            merged: dict[tuple[int, int], float] = {}
            for entity_id in entity_ids:
                for key, value in self._entity_months(entity_id, years, now_local, read_cache).items():
                    merged[key] = merged.get(key, 0.0) + value
            sums[role] = merged

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
            """Monat, in dem das Archiv der Rolle beginnt, wenn er nicht am 1. anfängt: er ist nur zum Teil
            erfasst und taugt nicht für einen Vergleich. Bei mehreren Entitäten gilt die zuletzt begonnene."""
            starts = [
                datetime.fromtimestamp(entity["first_ts"], self.tz)
                for entity_id in {e for key in role_keys for e in roles[key]}
                if (entity := self.index.get_entity(entity_id)) is not None and entity["first_ts"]
            ]
            if not starts:
                return None
            latest = max(starts)
            return [latest.year, latest.month] if latest.day > 1 else None

        def role_findings(role_keys: tuple[str, ...]) -> tuple[dict[str, dict[str, int]], int]:
            months: dict[str, dict[str, int]] = {}
            unchecked = 0
            for entity_id in sorted({e for key in role_keys for e in roles[key]}):
                info = findings.get(entity_id)
                if info is None or not info["measured"]:
                    unchecked += 1
                    continue
                for month_key, counts in info["months"].items():
                    bucket = months.setdefault(month_key, {})
                    for kind, amount in counts.items():
                        bucket[kind] = bucket.get(kind, 0) + amount
            # Nur auffällige Lücken stehen drin, siehe GAP_FACTOR.
            typical = statistics.median([months.get(key, {}).get("gaps", 0) for key in month_keys(role_keys)] or [0])
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
        years: dict[int, dict] = {}
        for d in days:
            entry = years.setdefault(d["date"].year, {
                "year": d["date"].year, "days": 0, "autarkie": 0, "einspeisung_ueber_verbrauch": 0, "kaum_pv": 0,
            })
            entry["days"] += 1
            if d["netzbezug"] is not None and d["verbrauch"] >= CONSUMPTION_MIN and d["netzbezug"] <= AUTARKY_EPSILON:
                entry["autarkie"] += 1
            if d["einspeisung"] is not None and d["einspeisung"] > d["verbrauch"] > 0:
                entry["einspeisung_ueber_verbrauch"] += 1
            if d["erzeugung"] is not None and d["erzeugung"] < LOW_PV_KWH:
                entry["kaum_pv"] += 1
        return {
            "unit": "kWh",
            "since": days[0]["date"].isoformat() if days else None,
            "days": len(days), "span": span, "enough": span >= RECORD_MIN_DAYS,
            "records": records,
            "kenntage": sorted(years.values(), key=lambda e: -e["year"]),
            "has_pv": bool(roles["erzeugung"]), "has_export": bool(roles["einspeisung"]),
            "has_import": bool(roles["netzbezug"]),
        }

    # -- Was hat sich verändert? -----------------------------------------------------------

    def compute_changes(self, config: dict, body: TableRequest, now_local: datetime | None = None) -> dict:
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
        consumers = [
            c for c in config.get("verbraucher") or [] if c.get("entity_id") and not c.get("parent")
        ]
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
                "name": consumer.get("name") or consumer["entity_id"], "entity_id": consumer["entity_id"],
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
        ranked = sorted((i for i in items if i["diff"] is not None), key=lambda i: -abs(i["diff"]))[:CHANGES_LIMIT]
        return {
            "unit": "kWh", "mode": mode, "at": stichtag.isoformat() if stichtag else None,
            "cur": {"start": win["start"].timestamp(), "end": win["end"].timestamp()},
            "vj": {"start": win["vj_start"].timestamp(), "end": win["vj_end"].timestamp()},
            "items": ranked, "consumers": len(consumers),
        }
