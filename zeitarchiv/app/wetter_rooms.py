"""Raum (und Etage) der weiteren Sensoren im Wetterdashboard (Roadmap 1.51).

Der Raum eines Eintrags ist entweder von Hand gesetzt (``room``) oder die Area der Entität aus Home Assistant.
Die Areas kennt die App aus dem Katalog der Integration (``selection_catalog``); fehlt der Katalog, bleibt
nur die Handeingabe. Die Gruppierung ändert nur die Darstellung: Reihenfolge und Schlüssel der Einträge
(``temperature:N``) bleiben, weil Karten und Tests sie als Position verwenden.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import selection_catalog

MAX_ROOM_LENGTH = 60
#: Einheiten, bei denen ein weiterer Sensor eine Temperatur ist. „%“ zählt nur ohne anderes Gerät-Klasse
#: (Akku, Helligkeit …) als Feuchte, siehe ``is_climate_sensor``.
TEMPERATURE_UNITS = ("°C", "°F", "K")
_cache: dict[str, Any] = {"key": None, "places": {}}


def places(data_dir: Path) -> dict[str, dict[str, str | None]]:
    """``{entity_id: {"area", "floor", "device_class"}}`` aus dem Katalog der Integration, sonst leer.

    Der Katalog kann groß sein (alle Entitäten); er wird nur neu gelesen, wenn sich die Datei ändert."""
    path = data_dir / selection_catalog.FILENAME
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["key"] == key:
        return _cache["places"]
    catalog = selection_catalog.load(data_dir)
    found: dict[str, dict[str, str | None]] = {}
    for row in (catalog or {}).get("entities", ()):
        entity_id = row.get("entity_id")
        if entity_id:
            found[entity_id] = {
                "area": row.get("area"), "floor": row.get("floor"), "device_class": row.get("device_class"),
            }
    _cache["key"], _cache["places"] = key, found
    return found


def entry(entity_id: str, name: str, room: str = "") -> dict[str, str]:
    """Ein gespeicherter Eintrag der weiteren Sensoren; ``room`` steht nur da, wenn er gesetzt ist
    (ältere Einstellungen und ihre Tests bleiben unverändert)."""
    row = {"entity_id": entity_id, "name": name}
    if room.strip():
        row["room"] = room.strip()[:MAX_ROOM_LENGTH]
    return row


def room_of(extra: dict, known: dict[str, dict[str, str | None]]) -> str:
    """Raum eines Eintrags: Handeingabe vor Area aus Home Assistant, sonst leer („Ohne Raum“)."""
    return (extra.get("room") or "").strip() or (known.get(extra.get("entity_id") or "", {}).get("area") or "")


def floor_of(extra: dict, known: dict[str, dict[str, str | None]]) -> str:
    """Etage der Area des Eintrags. Die Etage gehört zur Area in Home Assistant und ist hier nicht änderbar;
    ein von Hand gesetzter Raum, der von der Area abweicht, hat keine Etage."""
    place = known.get(extra.get("entity_id") or "", {})
    manual = (extra.get("room") or "").strip()
    if manual and manual != (place.get("area") or ""):
        return ""
    return place.get("floor") or ""


def is_climate_sensor(unit: str | None, device_class: str | None) -> bool:
    """Ob ein Messwert als weiterer Sensor taugt: Temperatur oder Luftfeuchte.

    Mit bekannter Geräteklasse zählt sie (Temperatur, Feuchte); ohne sie entscheidet die Einheit."""
    if device_class in ("temperature", "humidity"):
        return True
    if unit in TEMPERATURE_UNITS:
        return device_class is None
    return unit == "%" and device_class is None
