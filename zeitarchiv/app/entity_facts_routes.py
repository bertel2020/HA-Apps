"""Kennzahlen-Leiste der Entitätsseiten (Verlauf, Werte bearbeiten, Konfiguration)
und die beiden Nachlade-Endpunkte, die sie braucht: ``/entities/{id}/value-range``
und ``/entities/{id}/facts``.

Aus main.py herausgelöst (dessen Längengrenze, siehe test_route_modules.py).
Die Seiten selbst bleiben in main.py und rufen ``EntityFacts.facts`` auf; die
Pfade haben je ein Segment mehr als ``GET /entities/{entity_id}`` und stehen
deshalb dieser Seite nicht im Weg."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from . import value_range
from .formatting import (
    decimals_to_int,
    format_int,
    format_size,
    format_time,
    format_timestamp,
    format_type,
    format_uptime,
    format_value,
)
from .i18n import tr
from .route_support import storage_locked
from .storage.coordinator import StorageCoordinator
from .storage.index import Index


@dataclass(frozen=True)
class EntityFactsDependencies:
    index: Index
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    require_entity: Callable  # entity_id -> Zeile, sonst HTTPException (404/400)


def visible_row_count(entity) -> int:
    """Logische Rohwerte: physischer Indexzähler abzüglich Soft-Deletes."""
    deleted_count = int(entity["deleted_count"] or 0)
    return max(0, int(entity["row_count"] or 0) - deleted_count)


def _with_unit(text: str, entity) -> str:
    """Wert mit Einheit, damit die Zelle für sich lesbar ist (die Einheit steht
    zusätzlich in der Typ-Zelle, dort aber weit weg vom Wert)."""
    return f"{text} {entity['unit']}" if entity["unit"] else text


def _value_range_text(entry: dict, decimals: int | None, entity) -> str:
    if entry["min"] is None or entry["max"] is None:
        return "—"
    return _with_unit(f"{format_value(entry['min'], decimals)} / {format_value(entry['max'], decimals)}", entity)


class EntityFacts:
    def __init__(self, deps: EntityFactsDependencies) -> None:
        self.deps = deps

    def facts(self, entity) -> dict:
        """Kennzahlen im Kopf der drei Entitätsseiten (Verlauf, Werte bearbeiten,
        Konfiguration) — dieselben Felder wie die Spalten der Entitätenliste, damit
        man sie nicht erst dort nachschlagen muss. Ein gemeinsamer Aufbau, weil der
        Kopf auf allen drei Seiten gleich ist (_entity_facts.html) und die Werte
        sonst dreimal leicht verschieden zusammengesetzt würden."""
        tz = self.deps.tz
        last_ts = entity["last_ts"]
        return {
            "type_label": format_type(entity["aggregation_type"]),
            "state_class": entity["state_class"] or "",
            "unit": entity["unit"] or "",
            "row_count": format_int(visible_row_count(entity)),
            "size": format_size(entity["size_bytes"]),
            "first_ts": format_timestamp(entity["first_ts"], tz),
            "first_ts_time": format_time(entity["first_ts"], tz),
            "last_ts": format_timestamp(last_ts, tz),
            "last_ts_time": format_time(last_ts, tz),
            # Nur die größte Einheit („vor 1 Std.“, nicht „vor 1 Std. 16 Min.“): so passt
            # sie hinter die Uhrzeit in dieselbe Zeile, ohne dass die Zelle höher wird
            # oder mitten im Satz umbricht. Jede Einheit besteht aus Zahl und Wort.
            "last_ago": tr("vor {dauer}", dauer=" ".join(format_uptime(max(0.0, time.time() - last_ts)).split()[:2])) if last_ts else "",
            "extra": self._extra(entity),
            "category": self._category(entity["entity_id"]),
        }

    def _category(self, entity_id: str) -> dict | None:
        """Die Kategorie der Entität für den Chip unter dem Typ, oder None ohne."""
        index = self.deps.index
        category_id = index.get_entity_category_id(entity_id)
        if category_id is None:
            return None
        return next((c for c in index.list_entity_categories() if c["id"] == category_id), None)

    def _extra(self, entity) -> dict | None:
        """Die typabhängige letzte Zelle der Kennzahlen-Leiste, oder None, wo es
        keinen sinnvollen Wert gibt (Schalter).

        Standard: Minimum/Maximum über den gesamten Zeitraum. Der Index kennt sie
        nicht, ein Vollscan gehört nicht in den Seitenaufbau — liegt kein gültiger
        Zwischenspeicher vor, bleibt `text` leer und der Browser holt sie über
        /entities/{id}/value-range nach (static/js/entity-facts.js).
        Zähler: der aktuelle Zählerstand (Min/Max wären hier nur Anfangs- und
        Endstand). Zustand: Zahl der bekannten Zustände."""
        index, data_dir, tz = self.deps.index, self.deps.data_dir, self.deps.tz
        kind = entity["aggregation_type"]
        decimals = decimals_to_int(entity["decimals"])
        if kind == "standard":
            entry = value_range.cached(index, entity)
            return {
                "label": tr("Min / Max"), "note": tr("gesamter Zeitraum"), "pending": entry is None,
                "text": _value_range_text(entry, decimals, entity) if entry else "",
            }
        if kind == "counter":
            # last_value fehlt nach Import/Restore/Abgleich — dann aus den Daten lesen,
            # statt die Zelle je nach Herkunft der Entität verschwinden zu lassen.
            last = entity["last_value"]
            if last is None:
                last = value_range.last_raw_value(data_dir, index, tz, entity, datetime.now(tz))
            return {"label": tr("Zählerstand"), "note": "", "pending": False,
                    "text": _with_unit(format_value(last, decimals), entity) if last is not None else "—"}
        if kind == "state":
            return {"label": tr("Zustände"), "note": tr("verschieden"), "pending": False,
                    "text": format_int(len(index.get_state_map(entity["entity_id"])))}
        return None


def create_entity_facts_router(facts: EntityFacts) -> APIRouter:
    deps = facts.deps
    router = APIRouter()

    def locked(getter):
        return storage_locked(deps.coordinator, getter)

    @router.get("/entities/{entity_id}/value-range")
    @locked(lambda args: args["entity_id"])
    def entity_value_range(entity_id: str) -> dict:
        """Minimum/Maximum der Entität über den gesamten Zeitraum, als Text für die
        Kennzahlen-Leiste. Rechnet bei Bedarf einen Vollscan (siehe value_range.py)
        — deshalb ein eigener Aufruf, den die Seite erst nach dem Laden stellt."""
        entity = deps.require_entity(entity_id)
        entry = value_range.cached(deps.index, entity) or value_range.compute(
            deps.data_dir, deps.index, deps.tz, entity, datetime.now(deps.tz)
        )
        return {"text": _value_range_text(entry, decimals_to_int(entity["decimals"]), entity)}

    @router.get("/entities/{entity_id}/facts")
    @locked(lambda args: args["entity_id"])
    def entity_facts_values(entity_id: str) -> dict:
        """Bewegliche Felder der Kennzahlen-Leiste für das Nachladen des Verlaufs (entity_detail.js, refreshFacts()); Min/Max nur aus dem Zwischenspeicher."""
        values = facts.facts(deps.require_entity(entity_id))
        return {key: values[key] for key in ("last_ts", "last_ts_time", "last_ago", "row_count", "size", "extra")}

    return router
