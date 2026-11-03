"""Energiedashboard, Reiter „Langzeit“: Vergleichstabelle Zeiträume gegen das Vorjahr (Roadmap 1.45).

Die Tabelle summiert die Zählerrollen (Erzeugung, Einspeisung, Netzbezug, Speicher) je Zeitfenster
direkt aus den Tageszeilen der Zähler und leitet den Verbrauch daraus ab. Sie ruft bewusst nicht
``compute_flow`` auf: Das würde je Spalte und Vorjahr den ganzen Fluss samt Verbrauchern rechnen.

Der Verbrauch ist hier die Bilanz Erzeugung + Bezug + Entladung − Einspeisung − Ladung, dieselbe
Rechnung wie in Trends und Monatsverlauf. Die Live-Ansicht rechnet bei einer rechnerisch negativen
Grundlast anders (Summe der gemessenen Verbraucher), siehe die Tests in test_energiedashboard_flow.py.

Die Zeitfenster rechnet period_columns (wie beim Wetterdashboard); die Zeilen und ihre Summen sind
Sache dieses Moduls. Private Funktionen aus ``storage.query`` werden wie im Energiedashboard bewusst
direkt genutzt.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from . import period_columns
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
    def __init__(self, data_dir: Path, index: Index, tz: ZoneInfo) -> None:
        self.data_dir = data_dir
        self.index = index
        self.tz = tz

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
