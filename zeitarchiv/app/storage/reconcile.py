"""Prüft und repariert den abgeleiteten SQLite-Speicherindex.

Parquet-Dateien und Hot Buffer sind die Quelle der Wahrheit. ``entities`` hält
deren Kennzahlen nur als schnellen Cache für Listen und Statistik. Diese
Prüfung rekonstruiert den Cache ohne Rohwerte oder Rollups zu verändern.
"""

from __future__ import annotations

import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from . import hotbuffer
from .index import Index
from .paths import entity_dir, storage_area_dir


def _parquet_bounds(parquet_file: pq.ParquetFile) -> tuple[float | None, float | None]:
    """Liest Zeitgrenzen bevorzugt aus Metadaten, mit sicherem Spalten-Fallback."""
    metadata = parquet_file.metadata
    ts_index = parquet_file.schema_arrow.names.index("ts")
    minima: list[float] = []
    maxima: list[float] = []
    for group_index in range(metadata.num_row_groups):
        stats = metadata.row_group(group_index).column(ts_index).statistics
        if stats is None or not stats.has_min_max:
            column = parquet_file.read_row_group(group_index, columns=["ts"]).column("ts")
            values = column.to_pylist()
            if values:
                minima.append(float(min(values)))
                maxima.append(float(max(values)))
        else:
            minima.append(float(stats.min))
            maxima.append(float(stats.max))
    return (min(minima), max(maxima)) if minima else (None, None)


def _entity_storage_stats(data_dir: Path, entity_id: str) -> dict:
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    archive_files = sorted(archive_dir.glob("*.parquet")) if archive_dir.exists() else []
    row_count = 0
    size_bytes = 0
    first_values: list[float] = []
    last_values: list[float] = []

    for path in archive_files:
        parquet_file = pq.ParquetFile(path)
        row_count += parquet_file.metadata.num_rows
        size_bytes += path.stat().st_size
        first_ts, last_ts = _parquet_bounds(parquet_file)
        if first_ts is not None:
            first_values.append(first_ts)
            last_values.append(last_ts)

    hot_dir = storage_area_dir(data_dir, "hot")
    hot_files = sorted(hot_dir.glob(f"{entity_id}-*.csv")) if hot_dir.exists() else []
    for path in hot_files:
        rows = hotbuffer.read_rows(path)
        row_count += len(rows)
        if rows:
            timestamps = [row[0] for row in rows]
            first_values.append(min(timestamps))
            last_values.append(max(timestamps))

    return {
        "row_count": row_count,
        # Bestehende Semantik: size_bytes beschreibt das komprimierte Archiv;
        # der laufende, unkomprimierte Hot Buffer wird separat ausgewiesen.
        "size_bytes": size_bytes,
        "first_ts": min(first_values) if first_values else None,
        "last_ts": max(last_values) if last_values else None,
        "archive_files": len(archive_files),
        "hot_files": len(hot_files),
    }


def _timestamps_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) < 0.001


def audit_storage_metadata(
    data_dir: Path,
    index: Index,
    tz: ZoneInfo,
    *,
    entity_ids: list[str] | None = None,
    repair: bool = False,
) -> dict:
    """Prüft alle oder ausgewählte Entitäten und repariert optional atomar.

    ``tz`` gehört bewusst zur Signatur der zentralen Speicherprüfung, auch wenn
    die Dateinamen bereits Monatsangaben tragen. Dadurch bleibt der Aufruf an
    allen Storage-Lebenszykluspunkten eindeutig und kann später ohne API-Bruch
    auch kalendarische Validierungen ergänzen.
    """
    del tz
    wanted = set(entity_ids) if entity_ids is not None else None
    entities = [
        entity for entity in index.list_entities()
        if wanted is None or entity["entity_id"] in wanted
    ]
    mismatches: list[dict] = []
    errors: list[dict] = []

    for entity in entities:
        entity_id = entity["entity_id"]
        try:
            actual = _entity_storage_stats(data_dir, entity_id)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            errors.append({"entity_id": entity_id, "error": str(exc) or exc.__class__.__name__})
            continue

        deleted_count = int(entity["deleted_count"] or 0)
        indexed_rows = int(entity["row_count"] or 0)
        actual_rows = int(actual["row_count"])
        changed_fields = []
        if indexed_rows != actual_rows:
            changed_fields.append("row_count")
        if int(entity["size_bytes"] or 0) != int(actual["size_bytes"]):
            changed_fields.append("size_bytes")
        if not _timestamps_equal(entity["first_ts"], actual["first_ts"]):
            changed_fields.append("first_ts")
        if not _timestamps_equal(entity["last_ts"], actual["last_ts"]):
            changed_fields.append("last_ts")
        if not changed_fields:
            continue

        mismatches.append({
            "entity_id": entity_id,
            "friendly_name": entity["friendly_name"],
            "deleted_count": deleted_count,
            "indexed_row_count": indexed_rows,
            "actual_row_count": actual_rows,
            "indexed_visible_rows": max(0, indexed_rows - deleted_count),
            "actual_visible_rows": max(0, actual_rows - deleted_count),
            "indexed_size_bytes": int(entity["size_bytes"] or 0),
            "actual_size_bytes": int(actual["size_bytes"]),
            "indexed_first_ts": entity["first_ts"],
            "actual_first_ts": actual["first_ts"],
            "indexed_last_ts": entity["last_ts"],
            "actual_last_ts": actual["last_ts"],
            "changed_fields": changed_fields,
        })

    if repair and mismatches:
        index.replace_entity_storage_stats(mismatches)

    return {
        "checked_at": time.time(),
        "entities_checked": len(entities),
        "mismatches": mismatches,
        "errors": errors,
        "repaired": bool(repair and mismatches),
    }
