"""Hinweis „Rolle im Wetterdashboard“ an den Zeilen des Auswahl-Assistenten (ROADMAP 1.54, Schritt 2).

Passt eine Entität der angezeigten Liste in eine **leere** Rolle des Wetterdashboards, steht das an der Zeile.
Gerechnet wird mit denselben Metadaten wie bei „Vorschlagen“ (``wetter_autofill.suggest_for_entities``);
belegte Rollen und ein ausgeschaltetes Dashboard bleiben ohne Hinweis. Es wird nichts gesetzt.
"""

from __future__ import annotations

import time
from typing import Any

from . import energiedashboard_routes, wetter_autofill, wetter_routes
from .i18n import N_, tr
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


def _recent_ids(timeline: list[dict[str, Any]], archived: set[str], now: float | None) -> list[str]:
    """Entitäten, die der Assistent in den letzten ``OFFER_WINDOW`` Sekunden übernommen hat und die schon archiviert sind."""
    cutoff = (now if now is not None else time.time()) - OFFER_WINDOW
    ids: list[str] = []
    for entry in timeline:
        if entry.get("action") != "add" or entry.get("source") != "assistant" or entry.get("undone_by"):
            continue
        if entry.get("status") not in {"applied", "partial"} or float(entry.get("ts") or 0) < cutoff:
            continue
        ids.extend(eid for eid in undo_ids(entry) if eid in archived and eid not in ids)
    return ids


def wetter_offer(
    index: Index, entities: list[dict[str, Any]], timeline: list[dict[str, Any]], archived: set[str],
    now: float | None = None,
) -> list[dict[str, str]]:
    """Zeilen für „Passt in das Wetterdashboard“: kürzlich im Assistenten übernommene Entitäten, die schon archiviert
    sind (vorher zeigte die Rolle ins Leere) und in eine leere Rolle passen. ``[]``, wenn nichts zu tun ist."""
    ids = _recent_ids(timeline, archived, now)
    hints = wetter_hints(index, entities, ids)
    names = {row["entity_id"]: row.get("name") or row["entity_id"] for row in entities}
    order = list(wetter_routes.ROLE_KEYS)
    rows = [{"entity_id": eid, "name": names.get(eid, eid), **hint} for eid, hint in hints.items()]
    rows.sort(key=lambda row: order.index(row["role"]))
    return rows[:MAX_OFFER]


#: Rollen des HA-Energie-Dashboards (``energy_role`` im Katalog) und ihr Ziel im Energiedashboard der App.
_ENERGY_TARGET = {"grid_import": "netzbezug", "grid_export": "einspeisung", "solar": "erzeuger", "consumer": "verbraucher"}
ENERGY_ORDER = ("netzbezug", "einspeisung", "erzeuger", "speicher", "verbraucher")
ENERGY_LABELS = {
    "netzbezug": N_("Netzbezug"), "einspeisung": N_("Einspeisung"), "erzeuger": N_("Erzeuger (neue Zeile)"),
    "speicher": N_("Speicher (neue Zeile)"), "verbraucher": N_("Verbraucher (neue Zeile)"),
}


def energie_proposals(config: dict[str, Any], entities: list[dict[str, Any]], entity_ids: list[str]) -> list[dict[str, str]]:
    """Zuordnungen für das Energiedashboard aus den Rollen, die der Nutzer im Energie-Dashboard von Home Assistant
    eingetragen hat (``energy_role``), nur für die übergebenen Entitäten und nur dort, wo noch nichts steht.

    ``assign`` ist der Wert für ``/energiedashboard?assign=``. Ein Speicher entsteht, wenn Laden und Entladen am selben Gerät
    gefunden werden (``ok``); eine einzelne Seite oder mehrere Kandidaten für ein Einzelfeld gelten als ``check``."""
    used = energiedashboard_routes.configured_entity_ids(config)
    by_id = {row["entity_id"]: row for row in entities}
    rows = [by_id[eid] for eid in dict.fromkeys(entity_ids) if eid in by_id and eid not in used]
    name = {row["entity_id"]: row.get("name") or row["entity_id"] for row in rows}
    out: list[dict[str, str]] = []

    def add(target: str, ids: tuple[str, ...], assign: str, confidence: str) -> None:
        out.append({
            "target": target, "label": tr(ENERGY_LABELS[target]), "assign": f"{target}:{assign}", "confidence": confidence,
            "name": " + ".join(name[eid] for eid in ids if eid), "entity_id": ", ".join(eid for eid in ids if eid),
        })

    for role, target in _ENERGY_TARGET.items():
        found = [row["entity_id"] for row in rows if row.get("energy_role") == role]
        if target in ("netzbezug", "einspeisung") and config.get(target):
            continue
        sure = "ok" if len(found) == 1 or target not in ("netzbezug", "einspeisung") else "check"
        for eid in found:
            add(target, (eid,), eid, sure)

    outs = [row for row in rows if row.get("energy_role") == "battery_out"]
    ins = [row for row in rows if row.get("energy_role") == "battery_in"]
    left_in = {row["entity_id"] for row in ins}
    for out_row in outs:
        partners = [row for row in ins if row.get("device") and row.get("device") == out_row.get("device")]
        if len(partners) == 1 and sum(1 for o in outs if o.get("device") == out_row.get("device")) == 1:
            partner = partners[0]["entity_id"]
            left_in.discard(partner)
            add("speicher", (out_row["entity_id"], partner), f"{out_row['entity_id']},{partner}", "ok")
        else:
            add("speicher", (out_row["entity_id"],), f"{out_row['entity_id']},", "check")
    for eid in (row["entity_id"] for row in ins if row["entity_id"] in left_in):
        add("speicher", (eid,), f",{eid}", "check")
    out.sort(key=lambda row: (ENERGY_ORDER.index(row["target"]), row["name"].lower()))
    return out


def energie_offer(
    index: Index, entities: list[dict[str, Any]], timeline: list[dict[str, Any]], archived: set[str],
    now: float | None = None,
) -> list[dict[str, str]]:
    """Zeilen für „Passt in das Energiedashboard“ (wie ``wetter_offer``). ``[]``, wenn das Dashboard aus ist."""
    config = energiedashboard_routes.active_role_config(index)
    if config is None:
        return []
    return energie_proposals(config, entities, _recent_ids(timeline, archived, now))[:MAX_OFFER]
