"""Ablage des Entitäten-Katalogs, den die Integration schickt (ROADMAP 1.33).

Der Katalog besteht nur aus Metadaten (Name, Geräteklasse, Einheit, Gerät,
Bereich, Energie-Rolle …), nie aus Messwerten, und ersetzt jeweils den vorigen
vollständig. Er liegt als eine JSON-Datei im Datenverzeichnis und nicht in
SQLite: er ist ein Cache der Integration, im Zweifel schickt sie ihn neu, und er
soll weder Index noch Backups aufblähen.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .storage.paths import ENTITY_ID_PATTERN

FILENAME = "selection_catalog.json"

#: Mehr Zeilen nimmt die App je Katalog nicht an.
MAX_ENTITIES = 20_000

_ENTITY_ID_RE = re.compile(ENTITY_ID_PATTERN)

_TEXT_FIELDS = {
    "name": 200, "device_class": 40, "state_class": 40, "unit": 40, "platform": 80,
    "device": 200, "area": 200, "entity_category": 20, "energy_role": 40,
}
_FLAG_FIELDS = ("hidden", "selected", "excluded")


def clean_entities(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Übernimmt nur bekannte Felder in der erwarteten Form. Zeilen mit ungültiger
    Entitäts-ID fallen weg, Texte werden gekürzt: der Inhalt kommt von außen und
    landet später in Seiten."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_ENTITIES]:
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not isinstance(entity_id, str) or not _ENTITY_ID_RE.match(entity_id) or entity_id in seen:
            continue
        seen.add(entity_id)
        row: dict[str, Any] = {"entity_id": entity_id, "domain": entity_id.split(".", 1)[0]}
        for field, limit in _TEXT_FIELDS.items():
            value = item.get(field)
            row[field] = value.strip()[:limit] or None if isinstance(value, str) else None
        for field in _FLAG_FIELDS:
            row[field] = bool(item.get(field))
        rows.append(row)
    return rows


def save(data_dir: Path, entities: list[dict[str, Any]]) -> int:
    """Ersetzt den Katalog atomar und gibt die Zahl der übernommenen Zeilen zurück."""
    rows = clean_entities(entities)
    payload = json.dumps({"received_ts": time.time(), "entities": rows}, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=FILENAME, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, data_dir / FILENAME)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return len(rows)


def load(data_dir: Path) -> dict[str, Any] | None:
    """Der zuletzt empfangene Katalog (``received_ts``, ``entities``) oder None, wenn
    noch keiner da oder die Datei unlesbar ist."""
    try:
        data = json.loads((data_dir / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("entities"), list):
        return None
    return data
