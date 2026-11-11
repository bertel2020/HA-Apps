"""Hinweis „Rolle im Wetterdashboard“ an den Zeilen des Auswahl-Assistenten (ROADMAP 1.54, Schritt 2).

Passt eine Entität der angezeigten Liste in eine **leere** Rolle des Wetterdashboards, steht das an der Zeile.
Gerechnet wird mit denselben Metadaten wie bei „Vorschlagen“ (``wetter_autofill.suggest_for_entities``);
belegte Rollen und ein ausgeschaltetes Dashboard bleiben ohne Hinweis. Es wird nichts gesetzt.
"""

from __future__ import annotations

import time
from typing import Any

from . import wetter_autofill, wetter_routes
from .i18n import tr
from .selection_overview import undo_ids
from .storage.index import Index


def wetter_hints(index: Index, entities: list[dict[str, Any]], entity_ids: list[str]) -> dict[str, dict[str, str]]:
    """``{entity_id: {"role", "label", "confidence"}}`` für die Entitäten, die in eine leere Rolle passen.

    ``confidence``: „ok“ bei klarem Vorsprung, „check“, wenn mehrere Kandidaten der Liste ähnlich gut passen.
    Eine Entität bekommt höchstens eine Rolle (die erste in der Reihenfolge der Einrichtung)."""
    config = wetter_routes.active_role_config(index)
    if config is None or not entity_ids:
        return {}
    filled = {role for role in wetter_routes.ROLE_KEYS if config.get(role)}
    picks = wetter_autofill.suggest_for_entities(entity_ids, entities, filled, config.get("temperature") or None)
    hints: dict[str, dict[str, str]] = {}
    for role, pick in picks.items():
        hints.setdefault(pick["entity_id"], {
            "role": role, "label": tr(wetter_routes.ROLE_LABELS[role]), "confidence": pick["confidence"],
        })
    return hints


def attach(result: dict[str, Any], hints: dict[str, dict[str, str]]) -> None:
    """Trägt den Hinweis als ``role_hint`` in die Zeilen eines Ergebnisses (``suggest``/``search``) ein."""
    if not hints:
        return
    for section in ("recommended", "more"):
        for group in result[section]:
            for row in group["rows"]:
                hint = hints.get(row["entity_id"])
                if hint:
                    row["role_hint"] = hint


#: So lange nach dem Übernehmen bietet „Auswahl & Verlauf“ die Zuordnung an. Der Block verschwindet früher von selbst,
#: sobald die Rolle belegt ist.
OFFER_WINDOW = 2 * 24 * 3600
MAX_OFFER = 12


def wetter_offer(
    index: Index, entities: list[dict[str, Any]], timeline: list[dict[str, Any]], archived: set[str],
    now: float | None = None,
) -> list[dict[str, str]]:
    """Zeilen für „Passt in das Wetterdashboard“: kürzlich im Assistenten übernommene Entitäten, die schon archiviert
    sind (vorher zeigte die Rolle ins Leere) und in eine leere Rolle passen. ``[]``, wenn nichts zu tun ist."""
    cutoff = (now if now is not None else time.time()) - OFFER_WINDOW
    ids: list[str] = []
    for entry in timeline:
        if entry.get("action") != "add" or entry.get("source") != "assistant" or entry.get("undone_by"):
            continue
        if entry.get("status") not in {"applied", "partial"} or float(entry.get("ts") or 0) < cutoff:
            continue
        ids.extend(eid for eid in undo_ids(entry) if eid in archived and eid not in ids)
    hints = wetter_hints(index, entities, ids)
    names = {row["entity_id"]: row.get("name") or row["entity_id"] for row in entities}
    order = list(wetter_routes.ROLE_KEYS)
    rows = [
        {"entity_id": eid, "name": names.get(eid, eid), **hint}
        for eid, hint in hints.items()
    ]
    rows.sort(key=lambda row: order.index(row["role"]))
    return rows[:MAX_OFFER]
