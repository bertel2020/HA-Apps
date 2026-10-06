"""Dashboard-Kachel „Bereinigung“ (item_type='cleanup'): die offenen Punkte aus Housekeeping auf einen
Blick — Zählerrückgänge, doppelte Zeitstempel, markierte Werte — mit Link in den jeweiligen Abschnitt.

Die Zahlen kommen aus denselben Schnappschüssen wie Housekeeping und die Glocke (stündlich neu
berechnet), die Kachel scannt also nichts selbst. Zähler mit erlaubten Rückgängen stehen dort nicht
drin und zählen deshalb nicht mit."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from . import purge_auto
from .formatting import format_int, format_timestamp
from .i18n import tr


def _plural(count: int, one: str, many: str) -> str:
    return tr(one) if count == 1 else tr(many, count=count)


def items(index, tz: ZoneInfo) -> list[dict]:
    """Die drei Zeilen der Kachel: ``count`` zählt, ``warn`` färbt Auffälliges, ``link`` führt in Housekeeping."""
    counter_rows = (index.get_counter_decrease_snapshot() or {}).get("rows", [])
    decreases = sum(row["count"] for row in counter_rows)
    duplicate_rows = (index.get_duplicate_snapshot() or {}).get("rows", [])
    duplicates = sum(row["count"] for row in duplicate_rows)
    marked = index.count_marked_values()
    marked_sub = ""
    state = purge_auto.settings(index)
    if marked and state["enabled"]:
        oldest = index.oldest_deleted_at()
        if oldest is not None:
            marked_sub = tr("nächster Lauf ab {date}", date=format_timestamp(oldest + state["min_age_days"] * 86400, tz)[:6])
    return [
        {
            "label": tr("Zählerrückgänge"), "count": decreases, "warn": bool(decreases),
            "sub": _plural(len(counter_rows), "1 Zähler", "{count} Zähler") if decreases else "",
            "link": "housekeeping#zaehlerrueckgaenge",
        },
        {
            "label": tr("Doppelte Zeitstempel"), "count": duplicates, "warn": bool(duplicates),
            "sub": tr("letzte 30 Tage") if duplicates else "", "link": "housekeeping#duplikate",
        },
        {
            "label": tr("Markierte Werte"), "count": marked, "warn": False,
            "sub": marked_sub, "link": "housekeeping#speicherplatz",
        },
    ]


def build(index, tz: ZoneInfo, pin: dict) -> dict:
    """Kachel-Daten für _dashboard_tiles.html. ``open_total`` ist die Summe der auffälligen Zeilen
    (Rückgänge + Duplikate); markierte Werte sind erledigt, nur noch nicht entfernt, und zählen nicht."""
    rows = items(index, tz)
    open_total = sum(row["count"] for row in rows if row["warn"])
    return {
        "kind": "cleanup", "pin_id": pin["item_id"], "name": tr("Bereinigung"),
        "grid_cols": pin["grid_cols"], "grid_rows": pin["grid_rows"],
        "lines": [dict(row, count_label=format_int(row["count"])) for row in rows],
        "open_total": open_total, "open_label": format_int(open_total),
        "all_clear": open_total == 0,
    }
