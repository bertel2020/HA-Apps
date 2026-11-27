"""Nachlauf der Einschaltungs-Anzahl eines Schalters (ROADMAP 1.74).

Die Anzahl wird seit dem Zählen im Rollup mitgeschrieben; Monate aus der Zeit davor haben sie nicht und zeigen
"nicht erfasst". Die Konfiguration der Entität zeigt, wie viele archivierte Monate betroffen sind, und rechnet sie
auf Klick aus dem Archiv nach (``rollup.backfill_switch_counts``). Gerechnet wird je Monat, nicht der ganze Rollup
neu: so bleiben Monate, deren Rohdaten es nicht mehr gibt, unangetastet."""

from __future__ import annotations

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

#: Fortschritt in der Kopfleiste, solange nachgerechnet wird.
_progress = JobProgress("switch-count-backfill", unit=N_("Monate"), label=N_("Einschaltungen nachrechnen"))


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
