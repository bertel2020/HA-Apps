"""Kleinster und größter Wert einer Entität über den gesamten Zeitraum — für
die Kennzahlen-Leiste im Kopf der Entitätsseiten.

Der Index kennt weder Minimum noch Maximum: die Rohwerte liegen in Parquet-
Monatsdateien und im Hot-Buffer, ein Wert über alle Monate heißt also alle
Dateien lesen. Das geschieht deshalb nicht beim Aufbau der Seite, sondern auf
Anforderung des Browsers (/entities/{id}/value-range) und wird im Index
zwischengespeichert, wie die Gesamtzählungen der Bereinigungsseite auch.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import cleanup
from .storage.index import Index

_KEY_PREFIX = "value_range:"
# Ein Minimum/Maximum altert langsam; eine Stunde ist für „gesamter Zeitraum“
# ehrlich genug. Wer Werte löscht, sieht die Änderung trotzdem sofort — siehe
# deleted_count in cached().
MAX_AGE_SECONDS = 3600


def cached(index: Index, entity) -> dict | None:
    """Der gespeicherte Eintrag, solange er noch gilt — sonst None.

    Ungültig wird er durch Alter und durch eine geänderte Zahl der zur
    Löschung markierten Werte: ein Ausreißer, den man gerade markiert hat, soll
    nicht noch eine Stunde als Maximum dastehen."""
    raw = index.get_setting(_KEY_PREFIX + entity["entity_id"])
    if raw is None:
        return None
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if time.time() - entry.get("computed_at", 0) >= MAX_AGE_SECONDS:
        return None
    if entry.get("deleted_count") != int(entity["deleted_count"] or 0):
        return None
    return entry


def compute(data_dir: Path, index: Index, tz: ZoneInfo, entity, now: datetime) -> dict:
    """Liest alle Rohwerte (ohne Soft-Deletes) und speichert Minimum/Maximum.
    Eine leere Entität liefert min/max = None."""
    low: float | None = None
    high: float | None = None
    if entity["first_ts"] is not None:
        for _ts, value in cleanup.iter_raw_rows(
            data_dir, index, entity["entity_id"], entity["first_ts"], now.timestamp() + 1, tz, now=now
        ):
            if low is None or value < low:
                low = value
            if high is None or value > high:
                high = value
    entry = {
        "computed_at": time.time(),
        "deleted_count": int(entity["deleted_count"] or 0),
        "min": low,
        "max": high,
    }
    index.set_setting(_KEY_PREFIX + entity["entity_id"], json.dumps(entry))
    return entry
