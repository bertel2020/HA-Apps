"""Muster-Vorschläge für den Auswahl-Assistenten (ROADMAP 1.33, Phase 2).

Aus den Treffern der aktuellen Ansicht (ein Thema oder die Suche) entstehen
Kandidaten der Form ``<domain>.<präfix>_*`` über gemeinsame Anfänge der
Object-IDs, geschnitten an Unterstrich-Grenzen (``sensor.shelly_plug_power`` →
``sensor.shelly_*``, ``sensor.shelly_plug_*``). Ein Kandidat braucht mindestens
``MIN_HITS`` Treffer der Ansicht und einen Präfix von mindestens ``MIN_PREFIX``
Zeichen; ein kürzerer Präfix, der keine zusätzlichen Treffer bringt, fällt zugunsten
des längeren weg (er wäre nur breiter).

Die Vorschau rechnet über den ganzen Katalog und mit derselben Regel wie die
Integration (``filtering.matches_entity_pattern``): ``fnmatchcase``, ein Muster ohne
Punkt gilt für die Object-ID, eines mit Punkt für die ganze ID. Textentitäten zählen
nie mit, Muster erfassen sie in der Integration nicht (``_text_allowed``).

Reine Funktionen über dem Katalog, wie ``selection_suggest``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from fnmatch import fnmatchcase
from typing import Any

from .selection_catalog import valid_pattern

MIN_HITS = 5
MAX_CANDIDATES = 8
MIN_PREFIX = 3
EXAMPLES = 10

#: Geräteklassen, deren Werte sich sehr oft ändern (wie in den Themenregeln, dazu Leistung).
_HIGH_RATE = {"power", "wind_speed", "irradiance", "volume_flow_rate", "data_rate", "illuminance"}


def matches(entity_id: str, pattern: str) -> bool:
    """Wie ``filtering.matches_entity_pattern`` der Integration, für ein Muster."""
    object_id = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
    return fnmatchcase(entity_id if "." in pattern else object_id, pattern)


def _high_rate(row: dict[str, Any]) -> bool:
    return row.get("device_class") in _HIGH_RATE or (
        row.get("domain") == "binary_sensor" and row.get("device_class") == "motion"
    )


def preview(
    pattern: str, entities: Iterable[dict[str, Any]], archived: Collection[str], view_ids: Collection[str] = (),
) -> dict[str, Any]:
    """Was ein Muster heute im Katalog erfassen würde: Gesamtzahl (ohne Textentitäten),
    davon neu / schon archiviert oder ausgewählt / ausgeschlossen, wie viele nicht zur
    Ansicht gehören (``outside``), Textentitäten, die es nicht erfasst, und Beispiele."""
    matched = [row for row in entities if matches(row["entity_id"], pattern)]
    rows = [row for row in matched if not row.get("text")]
    excluded = [row for row in rows if row.get("excluded")]
    known = [
        row for row in rows
        if not row.get("excluded") and (row["entity_id"] in archived or row.get("selected"))
    ]
    rows.sort(key=lambda row: ((row.get("name") or row["entity_id"]).lower(), row["entity_id"]))
    return {
        "pattern": pattern,
        "total": len(rows),
        "new": len(rows) - len(excluded) - len(known),
        "archived": len(known),
        "excluded": len(excluded),
        "outside": sum(1 for row in rows if row["entity_id"] not in view_ids),
        "text": len(matched) - len(rows),
        "high_rate": sum(1 for row in rows if _high_rate(row)),
        "examples": [
            {"entity_id": row["entity_id"], "name": row.get("name") or row["entity_id"],
             "in_view": row["entity_id"] in view_ids}
            for row in rows[:EXAMPLES]
        ],
    }


def _prefix_hits(entity_ids: Iterable[str]) -> dict[str, set[str]]:
    """Muster → Treffer der Ansicht, für jeden Präfix an einer Unterstrich-Grenze."""
    hits: dict[str, set[str]] = {}
    for entity_id in entity_ids:
        domain, object_id = entity_id.split(".", 1)
        parts = object_id.split("_")
        for end in range(1, len(parts)):
            prefix = "_".join(parts[:end])
            if len(prefix.strip("_")) < MIN_PREFIX:
                continue
            pattern = f"{domain}.{prefix}_*"
            if valid_pattern(pattern):
                hits.setdefault(pattern, set()).add(entity_id)
    return hits


def candidates(
    view_rows: Iterable[dict[str, Any]],
    entities: list[dict[str, Any]],
    archived: Collection[str],
    active: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Bis zu ``MAX_CANDIDATES`` Muster aus den Treffern einer Ansicht, je mit Vorschau.

    ``view_rows`` sind Zeilen der Ansicht (``entity_id``, ``text``), ``entities`` der ganze
    Katalog, ``active`` die Include-Muster, die die Integration schon hat (erscheinen mit
    ``active``). Sortiert nach Treffern in der Ansicht, dann nach wenig Fremdtreffern."""
    view_ids = {row["entity_id"] for row in view_rows if not row.get("text")}
    hits = {pattern: ids for pattern, ids in _prefix_hits(view_ids).items() if len(ids) >= MIN_HITS}
    # Ein kürzerer Präfix ohne zusätzliche Treffer ist nur breiter: der längere bleibt.
    kept = {
        pattern: ids for pattern, ids in hits.items()
        if not any(
            other != pattern and other.startswith(pattern[:-1]) and hits[other] == ids for other in hits
        )
    }
    previews = [preview(pattern, entities, archived, view_ids) for pattern in kept]
    for item in previews:
        item["hits"] = len(kept[item["pattern"]])
        item["active"] = item["pattern"] in active
    previews.sort(key=lambda item: (-item["hits"], item["outside"], item["pattern"]))
    return previews[:MAX_CANDIDATES]


def view_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Alle Zeilen eines Vorschlags- oder Suchergebnisses (ohne Gruppen)."""
    return [
        row for section in ("recommended", "more") for group in result.get(section) or () for row in group["rows"]
    ]
