"""Zentrale Validierung und sichere Pfade für entitätsbezogene Archivdaten."""

from __future__ import annotations

from ..i18n import tr

import logging
import re
from pathlib import Path

ENTITY_ID_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$"
ENTITY_ID_MAX_LENGTH = 240
_ENTITY_ID_RE = re.compile(ENTITY_ID_PATTERN)
_STORAGE_AREAS = {"hot", "archive", "rollup"}

logger = logging.getLogger(__name__)
_EMPTY_WARNED: set[str] = set()


def is_empty_file(path: Path) -> bool:
    """True für eine Datei mit 0 Bytes (z. B. nach hartem Abbruch beim Schreiben); die ist kein gültiges Parquet."""
    try:
        return path.stat().st_size == 0
    except OSError:
        return False


def parquet_files(directory: Path, pattern: str = "*.parquet", *, reverse: bool = False) -> list[Path]:
    """Sortierte Parquet-Dateien eines Verzeichnisses ohne leere (0-Byte-)Dateien.

    Eine leere Datei lässt sich nicht öffnen und würde jeden lesenden Schritt dauerhaft abbrechen. Sie zählt wie eine
    fehlende Datei; gewarnt wird je Pfad einmal pro Prozess, damit das Log nicht volläuft."""
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.glob(pattern), reverse=reverse):
        if is_empty_file(path):
            key = str(path)
            if key not in _EMPTY_WARNED:
                _EMPTY_WARNED.add(key)
                logger.warning(
                    "Leere Parquet-Datei (0 Bytes) wird übersprungen · event=empty_parquet_skipped path=%s", path,
                )
            continue
        result.append(path)
    return result


def _resolve_safely(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as err:
        raise ValueError(tr("Unsicherer oder nicht auflösbarer Speicherpfad: {path}", path=path)) from err


class InvalidEntityIdError(ValueError):
    """Eigene Unterklasse, damit die Web-Schicht eine fremde ID als 400 beantworten kann
    (jedes ValueError zentral abzufangen würde auch interne Fehler als Eingabefehler tarnen)."""


def validate_entity_id(entity_id: str) -> str:
    """Validiert das Home-Assistant-Format und schließt Pfadbestandteile aus."""
    if (
        not isinstance(entity_id, str)
        or len(entity_id) > ENTITY_ID_MAX_LENGTH
        or _ENTITY_ID_RE.fullmatch(entity_id) is None
    ):
        raise InvalidEntityIdError(tr("Ungültige Home-Assistant-Entitäts-ID: {entity_id!r}", entity_id=entity_id))
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
        raise ValueError(tr("Entitätspfad liegt außerhalb des Datenverzeichnisses"))
    return candidate


def storage_area_dir(data_dir: Path, area: str) -> Path:
    """Validiert auch den Storage-Bereich selbst gegen Symlink-Ausbrüche."""
    if area not in _STORAGE_AREAS:
        raise ValueError(tr("Unbekannter Storage-Bereich: {area!r}", area=area))
    base = _resolve_safely(data_dir)
    root = _resolve_safely(base / area)
    if not root.is_relative_to(base) or root == base:
        raise ValueError(tr("Storage-Bereich liegt außerhalb des Datenverzeichnisses"))
    return root


def hot_file_path(data_dir: Path, entity_id: str, month: str) -> Path:
    """Sicherer Dateipfad des Hot Buffers; month wird nur intern erzeugt."""
    validate_entity_id(entity_id)
    root = storage_area_dir(data_dir, "hot")
    candidate = root / f"{entity_id}-{month}.csv"
    resolved = _resolve_safely(candidate)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError(tr("Hot-Buffer-Pfad liegt außerhalb des Datenverzeichnisses"))
    return candidate
