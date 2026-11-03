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
from datetime import datetime
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
#: Zeilen: Schlüssel, Gruppe, Beschriftung und ob mehr davon günstig ("up") oder ungünstig ("down") ist.
ROWS: tuple[dict, ...] = (
    {"key": "erzeugung", "group": N_("Energie"), "label": N_("Erzeugung"), "better": "up"},
    {"key": "einspeisung", "group": N_("Energie"), "label": N_("Einspeisung"), "better": "up"},
    {"key": "netzbezug", "group": N_("Energie"), "label": N_("Netzbezug"), "better": "down"},
    {"key": "verbrauch", "group": N_("Energie"), "label": N_("Verbrauch"), "better": "down"},
)


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
    ) -> None:
        self.data_dir = data_dir
        self.index = index
        self.tz = tz
        #: (entity_id, range_key, offset, now, read_cache) -> (Bucket-Zeitstempel -> Wert, veraltet?); die
        #: Monatsbuckets des Energiedashboards. Ohne Angabe sind Monatswerte nicht verfügbar.
        self.entity_series = entity_series

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
        visible = [
            row for row in ROWS
            if (roles[row["key"]] if row["key"] != "verbrauch" else (roles["erzeugung"] or roles["netzbezug"]))
        ]
        cells: dict[str, dict[str, dict]] = {row["key"]: {} for row in visible}
        for win in windows:
            sides = [("cur", win["start"], win["end"])]
            if body.vorjahr:
                sides.append(("vj", win["vj_start"], win["vj_end"]))
            side_data = {
                side: self.side_values(roles, start, end, now_local, read_cache) for side, start, end in sides
            }
            for row in visible:
                key = row["key"]
                cell: dict = {}
                for side, _start, _end in sides:
                    sums, froms = side_data[side]
                    if key == "verbrauch":
                        value = balance(sums)
                        cell[side] = round(value, 3) if value is not None else None
                    elif key in ("einspeisung", "netzbezug"):
                        cell[side] = max(sums[key], 0.0) if sums[key] is not None else None
                    else:
                        cell[side] = sums[key]
                    if cell[side] is None and key != "verbrauch" and froms[key]:
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
                    "better": row["better"], "cells": cells[row["key"]],
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
