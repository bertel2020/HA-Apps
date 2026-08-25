"""Gezieltes Löschen aller Dateien und Indexdaten genau einer Entität."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .index import Index
from .paths import entity_dir, storage_area_dir, validate_entity_id


def _remove_path(path: Path) -> None:
    """Entfernt Datei, Symlink oder Verzeichnis ohne einem Symlink zu folgen."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def delete_entity_files(data_dir: Path, entity_id: str) -> None:
    """Entfernt Hot Buffer, Monatsarchive und Rollups einer Entität."""
    validate_entity_id(entity_id)

    hot_root = storage_area_dir(data_dir, "hot")
    hot_name = re.compile(rf"^{re.escape(entity_id)}-\d{{4}}-\d{{2}}\.csv$")
    hot_paths: list[Path] = []
    if hot_root.exists():
        hot_paths = [
            path for path in hot_root.iterdir() if hot_name.fullmatch(path.name)
        ]

    # Alle Verzeichnisse zuerst über den zentralen Pfadwächter auflösen. So
    # wird bei einem unsicheren Symlink abgebrochen, bevor bereits eine andere
    # Datei derselben Entität entfernt wurde.
    entity_paths = [
        entity_dir(data_dir, area, entity_id) for area in ("archive", "rollup")
    ]

    for path in hot_paths:
        _remove_path(path)

    for path in entity_paths:
        if path.exists() or path.is_symlink():
            _remove_path(path)


def delete_all_values(data_dir: Path, index: Index, entity_id: str) -> None:
    """Löscht alle Messwerte, behält aber Entität und deren Konfiguration."""
    delete_entity_files(data_dir, entity_id)
    index.clear_entity_data(entity_id)


def delete_entity(data_dir: Path, index: Index, entity_id: str) -> None:
    """Löscht Messwerte und den vollständigen Entitätseintrag."""
    delete_entity_files(data_dir, entity_id)
    index.delete_entity(entity_id)
