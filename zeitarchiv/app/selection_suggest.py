"""Vorschläge für den Auswahl-Assistenten (ROADMAP 1.33).

Reine Funktionen über dem Katalog der Integration, ohne Zugriff auf Index oder
Dateien: sie sind so ohne App testbar und später auch für ein MCP-Werkzeug
wiederverwendbar. Phase 0 kennt nur das Thema „Energie“.

Rangfolge für Energie:

* **empfohlen** — Quellen und Verbraucher aus dem Energie-Dashboard von Home
  Assistant, sowie Leistungssensoren am selben Gerät wie eine solche Quelle;
* **weitere Treffer** — übrige Energiezähler und Leistungssensoren;
* **abgeleitet** — Tages-, Monats- und Jahreszähler (Plattform ``utility_meter``
  oder ein entsprechender Name): sie lassen sich aus dem Gesamtzähler
  ableiten und werden deshalb nicht vorgeschlagen.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from typing import Any

from .i18n import tr

TOPICS = ("energie",)

#: Rollen aus dem Energie-Dashboard, die zum Thema „Energie“ gehören
#: (Gas und Wasser bekommen später ein eigenes Thema).
_ENERGY_ROLES = {
    "grid_import": "Im Energie-Dashboard als Netzbezug",
    "grid_export": "Im Energie-Dashboard als Einspeisung",
    "grid_power": "Im Energie-Dashboard als Netzleistung",
    "solar": "Im Energie-Dashboard als Solarerzeugung",
    "battery_in": "Im Energie-Dashboard als Batterie-Ladung",
    "battery_out": "Im Energie-Dashboard als Batterie-Entladung",
    "consumer": "Im Energie-Dashboard als Verbraucher",
}

_DERIVED_NAME = re.compile(
    r"\b(t[äa]glich|tag|tages|daily|day|w[öo]chentlich|woche|weekly|week|monat|monatlich|monthly|month|jahr|j[äa]hrlich|yearly|year|heute|today|gestern|yesterday)\w*",
    re.IGNORECASE,
)
_TOTAL_CLASSES = {"total", "total_increasing"}


def is_derived_counter(row: dict[str, Any]) -> bool:
    """Ein Zähler, der nur einen Zeitraum abbildet (Tag, Monat, Jahr) und sich aus dem
    Gesamtzähler ableiten lässt."""
    if row.get("platform") == "utility_meter":
        return True
    text = f"{row.get('name') or ''} {row['entity_id'].split('.', 1)[1].replace('_', ' ')}"
    return bool(_DERIVED_NAME.search(text))


def _status(row: dict[str, Any], archived: Collection[str]) -> str:
    if row.get("excluded"):
        return "excluded"
    if row["entity_id"] in archived:
        return "archived"
    if row.get("selected"):
        return "selected"
    return "new"


def _kind(row: dict[str, Any]) -> str:
    return tr("Zähler") if row.get("state_class") in _TOTAL_CLASSES else tr("Standard")


def _group_key(row: dict[str, Any]) -> str:
    return row.get("device") or row.get("platform") or tr("Ohne Gerät")


def suggest_energy(entities: Iterable[dict[str, Any]], archived: Collection[str] = ()) -> dict[str, Any]:
    """Teilt die Sensoren des Katalogs in empfohlen / weitere Treffer / abgeleitet."""
    rows = [
        row for row in entities
        if row.get("domain") == "sensor" and not row.get("hidden") and row.get("entity_category") != "config"
    ]
    dashboard_devices = {
        _group_key(row) for row in rows if row.get("energy_role") in _ENERGY_ROLES and row.get("device")
    }
    recommended: list[dict[str, Any]] = []
    more: list[dict[str, Any]] = []
    derived = 0
    for row in rows:
        role = row.get("energy_role")
        device_class = row.get("device_class")
        counter = device_class == "energy" and row.get("state_class") in _TOTAL_CLASSES
        power = device_class == "power"
        if role in _ENERGY_ROLES:
            reason, bucket = tr(_ENERGY_ROLES[role]), recommended
        elif counter and is_derived_counter(row):
            derived += 1
            continue
        elif power and row.get("device") and _group_key(row) in dashboard_devices:
            reason, bucket = tr("Leistung am selben Gerät wie eine Quelle aus dem Energie-Dashboard"), recommended
        elif counter:
            reason, bucket = tr("Zählerstand in kWh ({state_class})", state_class=row["state_class"]), more
        elif power:
            reason, bucket = tr("Leistung, ändert sich sehr oft"), more
        else:
            continue
        if row.get("entity_category") == "diagnostic" and bucket is recommended and role not in _ENERGY_ROLES:
            bucket = more
        bucket.append({
            "entity_id": row["entity_id"],
            "name": row.get("name") or row["entity_id"],
            "unit": row.get("unit"),
            "kind": _kind(row),
            "reason": reason,
            "group": _group_key(row),
            "status": _status(row, archived),
            "high_rate": power or role == "grid_power",
        })
    return {
        "topic": "energie",
        "recommended": _grouped(recommended),
        "more": _grouped(more),
        "counts": {
            "recommended": len(recommended),
            "recommended_new": sum(1 for r in recommended if r["status"] == "new"),
            "more": len(more),
            "derived": derived,
            "found": len(recommended) + len(more) + derived,
        },
    }


def _grouped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nach Gerät gruppiert; Gruppen mit den meisten neuen Treffern zuerst."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: (r["name"].lower(), r["entity_id"])):
        groups.setdefault(row["group"], []).append(row)
    ordered = sorted(
        groups.items(),
        key=lambda item: (-sum(1 for r in item[1] if r["status"] == "new"), item[0].lower()),
    )
    return [{"name": name, "rows": items} for name, items in ordered]


def suggest(topic: str, entities: Iterable[dict[str, Any]], archived: Collection[str] = ()) -> dict[str, Any]:
    if topic == "energie":
        return suggest_energy(entities, archived)
    raise ValueError(f"Unbekanntes Thema: {topic}")
