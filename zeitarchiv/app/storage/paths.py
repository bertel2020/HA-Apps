"""Zentrale Validierung und sichere Pfade für entitätsbezogene Archivdaten."""

from __future__ import annotations

import re
from pathlib import Path

ENTITY_ID_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$"
ENTITY_ID_MAX_LENGTH = 240
_ENTITY_ID_RE = re.compile(ENTITY_ID_PATTERN)
_STORAGE_AREAS = {"hot", "archive", "rollup"}


def _resolve_safely(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as err:
        raise ValueError(f"Unsicherer oder nicht auflösbarer Speicherpfad: {path}") from err


def validate_entity_id(entity_id: str) -> str:
    """Validiert das Home-Assistant-Format und schließt Pfadbestandteile aus."""
    if (
        not isinstance(entity_id, str)
        or len(entity_id) > ENTITY_ID_MAX_LENGTH
        or _ENTITY_ID_RE.fullmatch(entity_id) is None
    ):
        raise ValueError(f"Ungültige Home-Assistant-Entitäts-ID: {entity_id!r}")
    return entity_id


def entity_dir(data_dir: Path, area: str, entity_id: str) -> Path:
    """Liefert ein garantiert innerhalb des jeweiligen Storage-Bereichs liegendes Verzeichnis.

    Die zusätzliche resolve()-Prüfung schützt auch gegen einen bereits auf
    Platte vorhandenen symbolischen Link, der aus /data herauszeigen würde.
    """
    root = storage_area_dir(data_dir, area)
    validate_entity_id(entity_id)
    candidate = root / entity_id
    resolved = _resolve_safely(candidate)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("Entitätspfad liegt außerhalb des Datenverzeichnisses")
    return candidate


def storage_area_dir(data_dir: Path, area: str) -> Path:
    """Validiert auch den Storage-Bereich selbst gegen Symlink-Ausbrüche."""
    if area not in _STORAGE_AREAS:
        raise ValueError(f"Unbekannter Storage-Bereich: {area!r}")
    base = _resolve_safely(data_dir)
    root = _resolve_safely(base / area)
    if not root.is_relative_to(base) or root == base:
        raise ValueError("Storage-Bereich liegt außerhalb des Datenverzeichnisses")
    return root


def hot_file_path(data_dir: Path, entity_id: str, month: str) -> Path:
    """Sicherer Dateipfad des Hot Buffers; month wird nur intern erzeugt."""
    validate_entity_id(entity_id)
    root = storage_area_dir(data_dir, "hot")
    candidate = root / f"{entity_id}-{month}.csv"
    resolved = _resolve_safely(candidate)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("Hot-Buffer-Pfad liegt außerhalb des Datenverzeichnisses")
    return candidate
