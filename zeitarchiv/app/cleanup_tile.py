"""Dashboard-Kachel „Bereinigung“ (item_type='cleanup'): die offenen Punkte aus Housekeeping auf einen
Blick — Zählerrückgänge, doppelte Zeitstempel, markierte Werte — mit Link in den jeweiligen Abschnitt.

Die Zahlen kommen aus denselben Schnappschüssen wie Housekeeping und die Glocke (stündlich neu
berechnet), die Kachel scannt also nichts selbst. Zähler mit erlaubten Rückgängen stehen dort nicht
drin und zählen deshalb nicht mit."""

from __future__ import annotations

import re
import time
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


#: Ab so vielen Tagen ohne neuen Wert gilt eine Entität als inaktiv — die Voreinstellung von Housekeeping →
#: Inaktive Entitäten (nie empfangene zählen immer mit).
INACTIVE_AFTER_DAYS = 3


def inactive_entities(index, now: float | None = None) -> int:
    now = time.time() if now is None else now
    limit = INACTIVE_AFTER_DAYS * 86400
    return sum(1 for entity in index.list_entities() if entity["last_ts"] is None or now - entity["last_ts"] >= limit)


def status(index, tz: ZoneInfo) -> dict:
    """Vierte Kachel der Übersicht („Status“, entities.html): Hauptwert sind die inaktiven Entitäten, darunter
    in einer Zeile dieselben drei Zahlen wie in der Dashboard-Kachel. Zahl und erstes Wort verbindet ein
    geschütztes Leerzeichen, damit ein Zeilenumbruch sie nicht trennt."""
    rows = items(index, tz)
    decreases, duplicates, marked = (format_int(row["count"]) for row in rows)
    inactive = inactive_entities(index)
    return {
        "value": (tr("{count} inaktive Entität", count=format_int(inactive)) if inactive == 1
                  else tr("{count} inaktive Entitäten", count=format_int(inactive))).replace(" ", "\u00a0", 1),
        # Jede Zahl bleibt mit ihrem Wort zusammen (geschütztes Leerzeichen), umbrochen wird nur an den Punkten.
        "sub": re.sub(r"(\d) (?=[^\d\s·])", "\\1\u00a0", tr("{decreases} Rückgänge · {duplicates} Duplikate · {marked} markiert", decreases=decreases, duplicates=duplicates, marked=marked)),
        "link": "housekeeping",
    }


def build(index, tz: ZoneInfo, pin: dict) -> dict:
    """Kachel-Daten für _dashboard_tiles.html (Kachel „Status“). Hauptwert sind die inaktiven Entitäten
    (``inactive``), darunter die drei Bereinigungs-Zahlen (``lines``: Rückgänge, Duplikate, markierte Werte)."""
    rows = items(index, tz)
    inactive = inactive_entities(index)
    inactive_line = {
        "label": tr("Inaktive Entitäten"), "count": inactive, "warn": bool(inactive), "count_label": format_int(inactive),
        "sub": tr("ab {days} Tagen", days=INACTIVE_AFTER_DAYS) if inactive else "", "link": "housekeeping#entitaeten",
    }
    return {
        "kind": "cleanup", "pin_id": pin["item_id"], "name": tr("Status"),
        "grid_cols": pin["grid_cols"], "grid_rows": pin["grid_rows"],
        "lines": [dict(row, count_label=format_int(row["count"])) for row in rows],
        "inactive": inactive, "inactive_label": format_int(inactive),
        "inactive_text": tr("inaktive Entität") if inactive == 1 else tr("inaktive Entitäten"),
        "inactive_line": inactive_line,
    }
