"""Ansicht „Dashboards“ im Auswahl-Assistenten (Wetter- und Energiedashboard).

Zeigt je eingeschaltetem Dashboard, welche Rollen belegt sind, wo der Katalog einen Vorschlag hat und wo nichts passt.
Die Vorschläge stehen zusätzlich als normale Zeilen in der Liste (``result``), sodass sie wie jedes andere Thema
markiert und übernommen werden. Gerechnet wird mit denselben Metadaten wie die Zeilenhinweise (``selection_roles``);
es wird nichts gesetzt.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from . import energiedashboard_routes, wetter_autofill, wetter_routes
from .i18n import N_, tr
from .selection_roles import ENERGY_ORDER, energie_proposals
from .selection_suggest import result as topic_result
from .selection_suggest import suggestion
from .storage.index import Index

#: Pseudo-Thema der Ansicht (kein Eintrag in ``selection_suggest.TOPICS``, wie die Suche).
TOPIC = "dashboards"
INFO = {"label": N_("Dashboards"), "sensitive": False}

_ENERGY_TITLES = {
    "netzbezug": N_("Netzbezug"), "einspeisung": N_("Einspeisung"), "erzeuger": N_("Erzeuger"),
    "speicher": N_("Speicher"), "verbraucher": N_("Verbraucher"),
}
#: Pflichtfelder des Energiedashboards; die Listen (Erzeuger, Speicher, Verbraucher) erscheinen nur, wenn es etwas gibt.
_ENERGY_FIELDS = ("netzbezug", "einspeisung")


def _item(label: str, ids: list[str], names: dict[str, str], state: str, archived: Collection[str], **extra: Any) -> dict[str, Any]:
    return {
        "label": label, "state": state, "ids": ids, "name": extra.pop("name", None) or " + ".join(names.get(i, i) for i in ids),
        "archived": bool(ids) and all(i in archived for i in ids), **extra,
    }


def _card(key: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": key, "title": title, "items": items,
        "set": sum(1 for item in items if item["state"] == "set"), "total": len(items),
    }


def wetter(index: Index, entities: list[dict[str, Any]], archived: Collection[str]) -> dict[str, Any] | None:
    config = wetter_routes.active_role_config(index)
    if config is None:
        return None
    names = {row["entity_id"]: row.get("name") or row["entity_id"] for row in entities}
    filled = {role for role in wetter_routes.ROLE_KEYS if config.get(role)}
    picks = wetter_autofill.suggest_for_entities(list(names), entities, filled, config.get("temperature") or None)
    items = []
    for role in wetter_routes.ROLE_KEYS:
        label = tr(wetter_routes.ROLE_LABELS[role])
        if role in filled:
            items.append(_item(label, [str(config[role])], names, "set", archived))
        elif role in picks:
            items.append(_item(label, [picks[role]["entity_id"]], names, "suggest", archived, confidence=picks[role]["confidence"]))
        else:
            items.append(_item(label, [], names, "open", archived))
    return _card("wetter", tr("Wetterdashboard"), items)


def energie(index: Index, entities: list[dict[str, Any]], archived: Collection[str]) -> dict[str, Any] | None:
    config = energiedashboard_routes.active_role_config(index)
    if config is None:
        return None
    names = {row["entity_id"]: row.get("name") or row["entity_id"] for row in entities}
    proposals = energie_proposals(config, entities, [row["entity_id"] for row in entities if row.get("energy_role")])
    items = []
    for target in ENERGY_ORDER:
        label = tr(_ENERGY_TITLES[target])
        found = [p for p in proposals if p["target"] == target]
        if target in _ENERGY_FIELDS:
            have = [str(config[target])] if config.get(target) else []
        else:
            have = list(config.get(target) or ())
        if have:
            shown = have[:1] if target in _ENERGY_FIELDS else []
            items.append(_item(label, shown, names, "set", archived, count=len(have)))
        elif target in _ENERGY_FIELDS and not found:
            items.append(_item(label, [], names, "open", archived))
        for proposal in found:  # ein Eintrag je Vorschlag, nie mehrere Quellen in einer Zeile
            ids = [i for i in proposal["entity_id"].split(", ") if i]
            items.append(_item(label, ids, names, "suggest", archived, name=proposal["name"], confidence=proposal["confidence"]))
    return _card("energie", tr("Energiedashboard"), items)


def cards(index: Index, entities: list[dict[str, Any]], archived: Collection[str]) -> list[dict[str, Any]]:
    return [card for card in (wetter(index, entities, archived), energie(index, entities, archived)) if card]


def result(index: Index, entities: list[dict[str, Any]], archived: Collection[str]) -> dict[str, Any]:
    """Die Vorschläge beider Dashboards als Ergebnis wie bei einem Thema: eine Gruppe je Dashboard, die Rolle als Begründung."""
    by_id = {row["entity_id"]: row for row in entities}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards(index, entities, archived):
        for item in card["items"]:
            if item["state"] != "suggest":
                continue
            for entity_id in item["ids"]:
                if entity_id in seen or entity_id not in by_id:
                    continue
                seen.add(entity_id)
                row = suggestion(by_id[entity_id], tr("Rolle: {role}", role=item["label"]), archived,
                                 high_rate=by_id[entity_id].get("device_class") == "power")
                row["group"] = card["title"]
                rows.append(row)
    return topic_result(TOPIC, rows, [], 0)
