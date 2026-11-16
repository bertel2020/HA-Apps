"""Räume (Bereiche) der Entitäten für die Entitätsliste und die Kennzahlen-Leiste.

Die App kennt Räume nicht selbst: Die Integration schickt sie im Katalog
(``selection_catalog``, Felder ``area`` und ``floor``). Hier wird der zuletzt
empfangene Katalog nur gelesen, nie geändert — der Raum wird in Home Assistant
gepflegt. Ohne Katalog (Integration noch nicht verbunden, Standalone) gibt es
keine Räume; Spalte und Filter bleiben dann ganz weg.

Der Katalog ist eine JSON-Datei mit allen archivierbaren Entitäten, also zu
groß, um sie bei jedem Tastendruck der Suche neu zu parsen. Die Zuordnung wird
deshalb nach Änderungszeit und Größe der Datei zwischengespeichert."""

from __future__ import annotations

import threading
from pathlib import Path

from . import selection_catalog

_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], dict[str, dict[str, str | None]]]] = {}


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def area_map(data_dir: Path) -> dict[str, dict[str, str | None]]:
    """entity_id → ``{"area": Name, "floor": Name oder None}``, nur für Entitäten mit Raum.
    Leer, solange kein Katalog vorliegt."""
    path = data_dir / selection_catalog.FILENAME
    signature = _signature(path)
    if signature is None:
        return {}
    key = str(path)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]
    catalog = selection_catalog.load(data_dir)
    rows = catalog["entities"] if catalog else []
    mapping = {
        row["entity_id"]: {"area": row["area"], "floor": row.get("floor")}
        for row in rows
        if row.get("area")
    }
    with _lock:
        _cache[key] = (signature, mapping)
    return mapping


def area_names(data_dir: Path) -> list[str]:
    """Alle vorkommenden Raumnamen, alphabetisch ohne Rücksicht auf Groß-/Kleinschreibung."""
    return sorted({entry["area"] for entry in area_map(data_dir).values()}, key=str.casefold)


def area_filter(data_dir: Path, values: list[str] | None) -> tuple[list[str], list[str] | None] | None:
    """Übersetzt die Auswahl des Raum-Filters in das, was ``Index.list_entities`` erwartet:
    ``(Entitäten in den gewählten Räumen, Entitäten mit irgendeinem Raum oder None)``.
    Das Zweite ist nur gesetzt, wenn „Ohne Raum“ gewählt ist. ``None``: kein Filter
    („all“, leer, nichts Brauchbares)."""
    values = values or []
    names = {v for v in values if v not in ("all", "none")}
    without = "none" in values
    if not names and not without:
        return None
    mapping = area_map(data_dir)
    include = [entity_id for entity_id, entry in mapping.items() if entry["area"] in names]
    return include, (list(mapping) if without else None)
