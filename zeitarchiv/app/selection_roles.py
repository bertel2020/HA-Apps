"""Hinweis „Rolle im Wetterdashboard“ an den Zeilen des Auswahl-Assistenten (ROADMAP 1.54, Schritt 2).

Passt eine Entität der angezeigten Liste in eine **leere** Rolle des Wetterdashboards, steht das an der Zeile.
Gerechnet wird mit denselben Metadaten wie bei „Vorschlagen“ (``wetter_autofill.suggest_for_entities``);
belegte Rollen und ein ausgeschaltetes Dashboard bleiben ohne Hinweis. Es wird nichts gesetzt.
"""

from __future__ import annotations

from typing import Any

from . import wetter_autofill, wetter_routes
from .i18n import tr
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
