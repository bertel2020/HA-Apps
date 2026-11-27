"""Nachlauf der Einschaltungs-Anzahl eines Schalters (ROADMAP 1.74).

Die Anzahl wird seit dem Zählen im Rollup mitgeschrieben; Monate aus der Zeit davor haben sie nicht und zeigen
"nicht erfasst". Die Konfiguration der Entität zeigt, wie viele archivierte Monate betroffen sind, und rechnet sie
auf Klick aus dem Archiv nach (``rollup.backfill_switch_counts``). Gerechnet wird je Monat, nicht der ganze Rollup
neu: so bleiben Monate, deren Rohdaten es nicht mehr gibt, unangetastet."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .i18n import N_, tr
from .progress import JobProgress
from .route_support import storage_locked
from .storage import rollup
from .storage.coordinator import StorageCoordinator
from .storage.index import Index

#: Fortschritt in der Kopfleiste, solange nachgerechnet wird.
_progress = JobProgress("switch-count-backfill", unit=N_("Monate"), label=N_("Einschaltungen nachrechnen"))


#: Warteschlange des automatischen Nachlaufs (JSON-Liste von Entitäten). Fehlt der Eintrag, ist die Suche noch nicht
#: gelaufen. Eine leere Liste heißt "fertig": neue Monate bekommen die Anzahl schon beim Archivieren, es kommt nichts nach.
SETTING_BACKFILL_PENDING = "switch_count_backfill_pending"


def process_pending_backfill(data_dir: Path, index: Index, tz: ZoneInfo, coordinator: StorageCoordinator) -> None:
    """Rechnet die Einschaltungs-Anzahl archivierter Monate nach dem Update von selbst nach (Wartungstakt, alle 30 s).

    Der erste Lauf sucht einmal die Schalter mit Monaten ohne Anzahl und legt sie in eine Warteschlange; jeder weitere
    Takt arbeitet höchstens EINE Entität ab, damit ein Takt auch bei langer Historie nicht spürbar blockiert (wie beim
    Stunden-Rollup). Die Entität wird vor dem Rechnen aus der Warteschlange genommen: scheitert sie, versucht es kein
    Takt endlos erneut; der Knopf in der Konfiguration bleibt der Rückweg."""
    raw = index.get_setting(SETTING_BACKFILL_PENDING)
    if raw is None:
        pending = [
            row["entity_id"] for row in index.list_entities()
            if row["aggregation_type"] == "switch" and rollup.switch_count_months(data_dir, row["entity_id"])[1]
        ]
        index.set_setting(SETTING_BACKFILL_PENDING, json.dumps(pending))
        return
    try:
        pending = json.loads(raw)
    except ValueError:
        pending = []
    if not isinstance(pending, list) or not pending:
        return
    entity_id, *remaining = pending
    index.set_setting(SETTING_BACKFILL_PENDING, json.dumps(remaining))
    with coordinator.entity(entity_id):
        entity = index.get_entity(entity_id)
        if entity is None or entity["aggregation_type"] != "switch":
            return
        with _progress.track():
            _progress.set_phase(tr("Einschaltungen werden nachgerechnet"), total=len(rollup.switch_count_months(data_dir, entity_id)[1]))
            _progress.set_detail(entity_id)
            rollup.backfill_switch_counts(data_dir, entity_id, tz, lambda done, _total, _year, _month: _progress.advance(done))


@dataclass(frozen=True)
class SwitchCountDependencies:
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    templates: Jinja2Templates
    require_entity: Callable  # entity_id -> Zeile, sonst HTTPException (404/400)


def create_switch_count_router(deps: SwitchCountDependencies) -> APIRouter:
    router = APIRouter()

    def switch_entity(entity_id: str):
        entity = deps.require_entity(entity_id)
        if entity["aggregation_type"] != "switch":
            raise HTTPException(status_code=400, detail=tr("Das gilt nur für Schalter"))
        return entity

    @router.get("/entities/{entity_id}/switch-counts", response_class=HTMLResponse)
    def switch_counts_status(request: Request, entity_id: str) -> HTMLResponse:
        switch_entity(entity_id)
        months, missing = rollup.switch_count_months(deps.data_dir, entity_id)
        return deps.templates.TemplateResponse(
            request, "_switch_counts.html",
            {"entity_id": entity_id, "total": len(months), "missing": len(missing)},
        )

    @router.post("/entities/{entity_id}/switch-counts/rebuild", response_class=HTMLResponse)
    @storage_locked(deps.coordinator, lambda args: args["entity_id"])
    def switch_counts_rebuild(entity_id: str) -> HTMLResponse:
        switch_entity(entity_id)
        _, missing = rollup.switch_count_months(deps.data_dir, entity_id)
        with _progress.track():
            _progress.set_phase(tr("Einschaltungen werden nachgerechnet"), total=len(missing))
            _progress.set_detail(entity_id)
            rollup.backfill_switch_counts(
                deps.data_dir, entity_id, deps.tz, lambda done, _total, _year, _month: _progress.advance(done),
            )
        # htmx lädt die Seite neu: Statusanzeige und Diagramme zeigen danach den neuen Stand.
        return HTMLResponse("", headers={"HX-Refresh": "true"})

    return router
