"""Mehrere Entitäten auf einmal löschen (Auswahl in der Entitäten-Übersicht)
samt dem, was der Löschdialog vorher braucht: wo die Entitäten noch verwendet
werden und ein Backup vor dem Löschen.

Aus main.py herausgelöst (dessen Längengrenze, siehe test_route_modules.py).
Wird in main.py VOR der Entitätsseite ``GET /entities/{entity_id}``
eingehängt — sonst fiele ``GET /entities/backup-status`` dort hinein."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import entity_usage
from .background import BackgroundService
from .formatting import format_time, format_timestamp, format_uptime
from .i18n import tr
from .route_support import storage_locked
from .storage import entity_removal
from .storage.coordinator import StorageCoordinator
from .storage.index import Index
from .storage.paths import validate_entity_id

logger = logging.getLogger(__name__)

_BACKUP_FINAL_STATES = ("success", "failed", "interrupted", "skipped")


@dataclass(frozen=True)
class EntityDeleteDependencies:
    index: Index
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    background: BackgroundService


def create_entity_delete_router(deps: EntityDeleteDependencies) -> APIRouter:
    router = APIRouter()

    def locked(getter):
        return storage_locked(deps.coordinator, getter)

    @locked(lambda args: args["entity_ids"])
    def _delete_entities_locked(entity_ids: list[str]) -> dict:
        deleted: list[str] = []
        missing: list[str] = []
        failed: list[dict] = []
        for entity_id in entity_ids:
            try:
                if deps.index.get_entity(entity_id) is None:
                    missing.append(entity_id)
                    continue
                entity_removal.delete_entity(deps.data_dir, deps.index, entity_id)
                deleted.append(entity_id)
            except Exception as exc:  # eine fehlerhafte Entität bricht die übrigen nicht ab
                logger.exception("Entität %s konnte nicht gelöscht werden", entity_id)
                failed.append({"entity_id": entity_id, "error": str(exc)})
        if deleted:
            deps.background.invalidate_retention_overview()
        return {"deleted": deleted, "missing": missing, "failed": failed}

    @router.post("/entities/delete-selected")
    async def entities_delete_selected(request: Request) -> dict:
        """Löscht mehrere Entitäten endgültig (Auswahl in der Entitäten-Übersicht)."""
        entity_ids = list(dict.fromkeys(
            str(v) for k, v in (await request.form()).multi_items() if k == "entity_id"
        ))
        if not entity_ids:
            raise HTTPException(status_code=400, detail=tr("Keine Entität gewählt"))
        # Ungültige IDs vorab aussortieren: die Sperre prüft das Format und würde sonst die ganze Anfrage abbrechen.
        valid: list[str] = []
        invalid: list[dict] = []
        for entity_id in entity_ids:
            try:
                validate_entity_id(entity_id)
                valid.append(entity_id)
            except ValueError as exc:
                invalid.append({"entity_id": entity_id, "error": str(exc)})
        result = await run_in_threadpool(_delete_entities_locked, valid) if valid else {"deleted": [], "missing": [], "failed": []}
        result["failed"] = invalid + result["failed"]
        return result

    @router.post("/entities/usage-check")
    async def entities_usage_check(request: Request) -> dict:
        """Prüft vor dem Löschen, wo die gewählten Entitäten noch verwendet werden (nur lesend)."""
        entity_ids = [str(v) for k, v in (await request.form()).multi_items() if k == "entity_id"]
        usages = await run_in_threadpool(entity_usage.find_usages, deps.index, entity_ids)
        return {"usages": usages, "summary": entity_usage.summarize(usages)}

    @router.get("/entities/backup-status")
    def entities_backup_status(job_id: int | None = None) -> dict:
        """Stand des Backups für den Löschdialog: Alter des letzten erfolgreichen Laufs und, mit
        ``job_id``, Fortschritt und Ergebnis genau dieses Laufs."""
        with deps.background.backup_progress.lock:
            running = deps.background.backup_progress.running
            done = deps.background.backup_progress.done
            total = deps.background.backup_progress.total
        last = deps.index.get_last_successful_backup_job()
        last_ts = last["finished_at"] if last is not None else None
        result: dict = {
            "running": running,
            "percent": int(done / total * 100) if total else 0,
            "last_success_ts": last_ts,
            "last_success_text": (
                f"{format_timestamp(last_ts, deps.tz)} {format_time(last_ts, deps.tz)} "
                f"({tr('vor {format_uptime}', format_uptime=format_uptime(time.time() - last_ts))})"
                if last_ts else None
            ),
            "job": None,
        }
        if job_id is not None:
            for job in deps.index.list_backup_jobs(20):
                if job["id"] == job_id:
                    result["job"] = {
                        "status": job["status"], "final": job["status"] in _BACKUP_FINAL_STATES,
                        "error": job["error"],
                    }
                    break
        return result

    @router.post("/entities/backup-start")
    def entities_backup_start() -> dict:
        """Startet ein Backup vor dem Löschen (oder hängt sich an ein bereits laufendes). Die
        ``job_id`` gehört dem Lauf, auf dessen Ende der Dialog dann wartet."""
        deps.background.run_backup()
        with deps.background.backup_progress.lock:
            job_id = deps.background.backup_progress.job_id
        return {"job_id": job_id}

    return router
