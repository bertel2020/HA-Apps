"""Letzter Import je Entität (Spalten „Letzter Import“ und „Importmodus“ in der Import-Tabelle).

Die Berichte (``import_reports``) enthalten diese Information bereits, würden aber
bei jedem Seitenaufruf komplett gelesen und gehen verloren, sobald jemand sie
löscht. Deshalb führt ``import_reports.create()`` zusätzlich diese kleine Datei
nach: ``{entity_id: {ts, mode, source_type, rows}}``. Sie überlebt gelöschte
Berichte — der Import hat ja stattgefunden.

Beim ersten Lesen ohne Datei wird sie einmalig aus den vorhandenen Berichten
aufgebaut."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

FILENAME = "import_last.json"
_FORMAT_VERSION = 1
_lock = threading.Lock()


def _rows(result: dict) -> int:
    return sum(int(result.get(key, 0) or 0) for key in ("rows_imported", "rows_merged", "rows_updated", "rows_recovered"))


def _mode(report: dict) -> str:
    """Beim Home-Assistant-Import der gewählte Modus (full/raw/stats), sonst die Quelle."""
    source_type = report.get("source_type") or ""
    if source_type == "ha":
        mode = (report.get("configuration") or {}).get("history_source")
        if mode in ("full", "raw", "stats"):
            return mode
    return source_type


def _merge(entities: dict, report: dict) -> bool:
    """Trägt die Entitäten eines Berichts ein, wenn er neuer ist als der vorhandene Eintrag."""
    finished = report.get("finished_at")
    if not isinstance(finished, str):
        return False
    changed = False
    for result in report.get("results") or []:
        entity_id = result.get("entity_id") if isinstance(result, dict) else None
        if not entity_id:
            continue
        current = entities.get(entity_id)
        if current is not None and current.get("ts", "") >= finished:
            continue
        entities[entity_id] = {
            "ts": finished, "mode": _mode(report),
            "source_type": report.get("source_type"), "rows": _rows(result),
        }
        changed = True
    return changed


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entities = data.get("entities") if isinstance(data, dict) else None
    return entities if isinstance(entities, dict) else None


def _write(path: Path, entities: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=FILENAME, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": _FORMAT_VERSION, "entities": entities}, handle, ensure_ascii=False)
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _rebuild(data_dir: Path) -> dict:
    from . import import_reports

    entities: dict = {}
    # Älteste zuerst, damit _merge den jeweils neuesten Bericht behält.
    for report in sorted(import_reports.list_all(data_dir), key=lambda item: item.get("finished_at", "")):
        _merge(entities, report)
    return entities


def record(data_dir: Path, report: dict) -> None:
    """Vom frisch geschriebenen Bericht übernehmen. Ein Fehler hier darf den Import nie kippen."""
    try:
        path = data_dir / FILENAME
        with _lock:
            entities = _read(path)
            if entities is None:
                entities = _rebuild(data_dir)  # enthält den neuen Bericht bereits
            elif not _merge(entities, report):
                return
            _write(path, entities)
    except Exception:
        logger.exception("Letzter Import je Entität konnte nicht gespeichert werden")


def load(data_dir: Path) -> dict[str, dict]:
    """entity_id → ``{ts, mode, source_type, rows}``; baut die Datei beim ersten Aufruf aus den Berichten auf."""
    path = data_dir / FILENAME
    with _lock:
        entities = _read(path)
        if entities is not None:
            return entities
        try:
            entities = _rebuild(data_dir)
            _write(path, entities)
        except Exception:
            logger.exception("Letzter Import je Entität konnte nicht aufgebaut werden")
            return {}
        return entities
