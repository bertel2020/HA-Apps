"""Status und manuelle Kompaktierung der SQLite-Indexdatei."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path

from .formatting import format_int, format_size

logger = logging.getLogger(__name__)

INDEX_VACUUM_MIN_FILE_BYTES = 50 * 1024 * 1024
INDEX_VACUUM_MIN_RECLAIMABLE_BYTES = 10 * 1024 * 1024
INDEX_VACUUM_MIN_RECLAIMABLE_RATIO = 0.25
INDEX_VACUUM_DISK_MARGIN_BYTES = 16 * 1024 * 1024


def get_index_optimization_state(index, index_path: Path) -> dict:
    """Ermittelt einen konservativen VACUUM-Status aus freien Seiten."""
    file_bytes = index_path.stat().st_size if index_path.exists() else 0
    maintenance = index.get_database_maintenance_stats()
    reclaimable_bytes = min(
        file_bytes, int(maintenance.get("reclaimable_bytes", 0) or 0)
    )
    reclaimable_ratio = reclaimable_bytes / file_bytes if file_bytes else 0.0
    recommended = (
        file_bytes >= INDEX_VACUUM_MIN_FILE_BYTES
        and reclaimable_bytes >= INDEX_VACUUM_MIN_RECLAIMABLE_BYTES
        and reclaimable_ratio >= INDEX_VACUUM_MIN_RECLAIMABLE_RATIO
    )
    return {
        **maintenance,
        "file_bytes": file_bytes,
        "reclaimable_bytes": reclaimable_bytes,
        "reclaimable_ratio": reclaimable_ratio,
        "estimated_after_bytes": max(0, file_bytes - reclaimable_bytes),
        "recommended": recommended,
        "can_optimize": reclaimable_bytes > 0,
    }


def optimize_index(index, index_path: Path, storage_coordinator) -> dict:
    """Führt ein abgesichertes VACUUM aus und liefert eine UI-Meldung."""
    state = get_index_optimization_state(index, index_path)
    if not state["can_optimize"]:
        return {
            "success": False,
            "message": "Keine vollständig freien SQLite-Seiten vorhanden.",
        }

    required_free = state["file_bytes"] * 2 + INDEX_VACUUM_DISK_MARGIN_BYTES
    available_free = shutil.disk_usage(index_path.parent).free
    if available_free < required_free:
        return {
            "success": False,
            "message": (
                "Nicht genug freier Speicher für die sichere Optimierung: "
                f"benötigt {format_size(required_free)}, verfügbar "
                f"{format_size(available_free)}."
            ),
        }

    started_at = time.monotonic()
    try:
        with storage_coordinator.exclusive():
            # Unter der Wartungssperre erneut messen, da sich der Zustand seit
            # dem Seitenaufruf geändert haben kann.
            latest = get_index_optimization_state(index, index_path)
            if not latest["can_optimize"]:
                raise ValueError(
                    "Keine vollständig freien SQLite-Seiten mehr vorhanden."
                )
            vacuum_result = index.vacuum_database()
        before_bytes = int(vacuum_result["before"]["database_bytes"])
        after_bytes = int(vacuum_result["after"]["database_bytes"])
        freed_bytes = max(0, before_bytes - after_bytes)
        logger.info(
            "SQLite-Index optimiert · vorher=%d · nachher=%d · freigegeben=%d",
            before_bytes,
            after_bytes,
            freed_bytes,
        )
        return {
            "success": True,
            "message": (
                f"Index optimiert · {format_size(freed_bytes)} freigegeben "
                f"· Integritätsprüfung erfolgreich · "
                f"{time.monotonic() - started_at:.2f} Sekunden"
            ),
        }
    except (sqlite3.DatabaseError, OSError, ValueError) as exc:
        logger.exception("SQLite-Index konnte nicht optimiert werden")
        return {
            "success": False,
            "message": f"Optimierung fehlgeschlagen: {exc}",
        }


def build_index_detail_context(
    index,
    index_path: Path,
    group_definitions: list[dict],
    optimization_result: dict | None = None,
) -> dict:
    """Bereitet Tabellenbelegung und Wartungszustand für die Detailseite auf."""
    file_bytes = index_path.stat().st_size if index_path.exists() else 0
    optimization = get_index_optimization_state(index, index_path)
    table_stats = {row["table"]: row for row in index.get_database_table_stats()}
    groups = []
    assigned_tables: set[str] = set()
    for definition in group_definitions:
        tables = []
        for table_name in definition["tables"]:
            row = table_stats.get(table_name, {
                "table": table_name, "rows": 0, "data_bytes": None,
                "index_bytes": None, "bytes": None, "index_count": 0,
            })
            assigned_tables.add(table_name)
            tables.append({
                **row,
                "rows_label": format_int(row["rows"]),
                "data_size_label": (
                    format_size(row["data_bytes"])
                    if row["data_bytes"] is not None else "—"
                ),
                "index_size_label": (
                    format_size(row["index_bytes"])
                    if row["index_bytes"] is not None else "—"
                ),
                "size_label": (
                    format_size(row["bytes"]) if row["bytes"] is not None else "—"
                ),
            })
        data_bytes = sum(int(row["data_bytes"] or 0) for row in tables)
        index_bytes = sum(int(row["index_bytes"] or 0) for row in tables)
        sizes_available = any(row["bytes"] is not None for row in tables)
        row_count = sum(row["rows"] for row in tables)
        groups.append({
            **definition,
            "tables": tables,
            "rows": row_count,
            "rows_label": format_int(row_count),
            "bytes": sum(int(row["bytes"] or 0) for row in tables),
            "data_size_label": format_size(data_bytes) if sizes_available else "—",
            "index_size_label": format_size(index_bytes) if sizes_available else "—",
            "size_label": (
                format_size(data_bytes + index_bytes) if sizes_available else "—"
            ),
        })
    unassigned = [
        row for name, row in table_stats.items() if name not in assigned_tables
    ]
    if unassigned:
        groups.append(_unassigned_group(unassigned))

    sizes_available = any(row["bytes"] is not None for row in table_stats.values())
    allocated_bytes = sum(int(row["bytes"] or 0) for row in table_stats.values())
    overhead_bytes = max(0, file_bytes - allocated_bytes) if sizes_available else None
    entry_count = sum(row["rows"] for row in table_stats.values())
    return {
        "base": "..",
        "index_file_size": format_size(file_bytes),
        "index_table_count": format_int(len(table_stats)),
        "index_entry_count": format_int(entry_count),
        "index_allocated_size": (
            format_size(allocated_bytes) if sizes_available else None
        ),
        "index_groups": groups,
        "index_overhead_size": (
            format_size(overhead_bytes) if overhead_bytes is not None else None
        ),
        "index_sizes_available": sizes_available,
        "index_optimization": {
            **optimization,
            "status_label": (
                "Optimierung empfohlen"
                if optimization["recommended"] else "Optimierung nicht nötig"
            ),
            "reclaimable_size": format_size(optimization["reclaimable_bytes"]),
            "estimated_after_size": format_size(
                optimization["estimated_after_bytes"]
            ),
            "reclaimable_percent": round(
                optimization["reclaimable_ratio"] * 100, 1
            ),
        },
        "index_optimization_result": optimization_result,
    }


def _unassigned_group(rows: list[dict]) -> dict:
    tables = [{
        **row,
        "rows_label": format_int(row["rows"]),
        "data_size_label": (
            format_size(row["data_bytes"]) if row["data_bytes"] is not None else "—"
        ),
        "index_size_label": (
            format_size(row["index_bytes"])
            if row["index_bytes"] is not None else "—"
        ),
        "size_label": format_size(row["bytes"]) if row["bytes"] is not None else "—",
    } for row in rows]
    sizes_available = any(row["bytes"] is not None for row in tables)
    data_bytes = sum(int(row["data_bytes"] or 0) for row in tables)
    index_bytes = sum(int(row["index_bytes"] or 0) for row in tables)
    row_count = sum(row["rows"] for row in tables)
    return {
        "label": "Weitere Fachtabellen",
        "description": "Noch keiner fachlichen Gruppe zugeordnete Tabellen.",
        "tables": tables,
        "rows": row_count,
        "rows_label": format_int(row_count),
        "bytes": sum(int(row["bytes"] or 0) for row in tables),
        "data_size_label": format_size(data_bytes) if sizes_available else "—",
        "index_size_label": format_size(index_bytes) if sizes_available else "—",
        "size_label": (
            format_size(data_bytes + index_bytes) if sizes_available else "—"
        ),
    }
