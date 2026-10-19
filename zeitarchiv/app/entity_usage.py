"""Verwendungsprüfung für Entitäten: wo wird eine Entität außerhalb des Archivs noch gebraucht?

Grundlage für das Mehrfach-Löschen in der Entitäten-Übersicht. Gelöscht wird endgültig; die
Prüfung zeigt vorher, welche gespeicherten Diagramme, Vergleichstabellen, Dashboard-Kacheln und
Energiedashboard-Rollen die Entität noch nennen. Nur lesend — geändert wird hier nichts.

``auto_cleaned`` unterscheidet, was ``Index.delete_entity()`` selbst mit aufräumt (Wert-Kacheln)
von dem, was nach dem Löschen ins Leere zeigt (Diagramme, Tabellen, Energiedashboard)."""

from __future__ import annotations

from collections.abc import Iterable

from .i18n import tr
from .storage.index import Index

KIND_CHART = "chart"
KIND_TABLE = "table"
KIND_TILE = "tile"
KIND_ENERGY = "energy"


def _walk_strings(node: object, path: str = ""):
    """Liefert (Pfad, Text) für jeden String in einer verschachtelten Konfiguration."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_strings(value, path)


def _energy_config(index: Index) -> dict:
    # Spät importiert: energiedashboard_routes ist groß und zieht seinerseits viel nach.
    from .energiedashboard_routes import _load_config

    return _load_config(index)


def find_usages(index: Index, entity_ids: Iterable[str]) -> dict[str, list[dict]]:
    """``{entity_id: [Verwendung, …]}`` für jede angefragte Entität (leere Liste = ungenutzt).

    Eine Verwendung ist ``{"kind", "id", "name", "detail", "auto_cleaned"}``; ``id`` ist die
    Diagramm-/Tabellen-/Dashboard-ID, beim Energiedashboard ``None``."""
    wanted = list(dict.fromkeys(entity_ids))
    result: dict[str, list[dict]] = {entity_id: [] for entity_id in wanted}
    if not wanted:
        return result
    wanted_set = set(wanted)

    for chart in index.list_saved_charts():
        for entity_id in wanted_set.intersection(chart["entity_ids"]):
            result[entity_id].append({
                "kind": KIND_CHART, "id": chart["id"], "name": chart["name"],
                "detail": "", "auto_cleaned": False,
            })

    for table in index.list_saved_tables():
        full = index.get_saved_table(table["id"])
        if full is None:
            continue
        # Eine Tabelle kann dieselbe Entität in mehreren Zeilen führen — pro Tabelle einmal melden.
        seen: dict[str, list[str]] = {}
        for row in full["rows"]:
            for entity_id in wanted_set.intersection(row["entity_ids"]):
                seen.setdefault(entity_id, []).append(row["label"])
        for entity_id, labels in seen.items():
            result[entity_id].append({
                "kind": KIND_TABLE, "id": table["id"], "name": table["name"],
                "detail": ", ".join(label for label in labels if label), "auto_cleaned": False,
            })

    for dashboard in index.list_dashboards():
        for pin in index.list_dashboard_pins(dashboard["id"]):
            entity_id = pin.get("item_entity_id")
            if pin["item_type"] == "entity" and entity_id in wanted_set:
                result[entity_id].append({
                    "kind": KIND_TILE, "id": dashboard["id"], "name": dashboard["name"],
                    "detail": pin.get("title") or "", "auto_cleaned": True,
                })

    config = _energy_config(index)
    for path, value in _walk_strings(config):
        if value in wanted_set:
            result[value].append({
                "kind": KIND_ENERGY, "id": None, "name": tr("Energiedashboard"),
                "detail": path, "auto_cleaned": False,
            })

    return result


def existing_entity_ids(index: Index) -> set[str]:
    """Alle Entitäten, die das Archiv kennt — Vergleichsbasis für ``missing_entity_ids``."""
    return {row["entity_id"] for row in index.list_entities()}


def missing_entity_ids(entity_ids: Iterable[str], existing: set[str]) -> list[str]:
    """Die genannten Entitäten, die es nicht (mehr) gibt, in Reihenfolge und ohne Doppelte.

    Gespeicherte Diagramme und Tabellen behalten ihre Verweise, wenn eine Entität gelöscht wird
    (die Integration legt sie wieder an, sobald sie weitersendet) — hier wird nur gemeldet."""
    return [entity_id for entity_id in dict.fromkeys(entity_ids) if entity_id not in existing]


def table_entity_ids(table: dict) -> list[str]:
    """Alle Entitäten aus den Zeilen einer gespeicherten Tabelle."""
    return [entity_id for row in table["rows"] for entity_id in row["entity_ids"]]


def summarize(usages: dict[str, list[dict]]) -> dict:
    """Kurzfassung für die Oberfläche: wie viele Entitäten sind betroffen, wie viele blockieren."""
    in_use = [entity_id for entity_id, items in usages.items() if items]
    needs_attention = [
        entity_id for entity_id, items in usages.items() if any(not item["auto_cleaned"] for item in items)
    ]
    return {"requested": len(usages), "in_use": len(in_use), "needs_attention": len(needs_attention)}
