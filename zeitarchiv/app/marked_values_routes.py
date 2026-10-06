"""Reiter „Markiert“ im Bearbeitungsbereich einer Entität: alle zur Löschung
markierten, noch nicht endgültig entfernten Werte, nach Charge gruppiert, und
ihr Zurücknehmen — einzeln, je Charge oder komplett.

Vorher ließ sich nur die jüngste Charge zurücknehmen („Rückgängig (letzte
Löschung)“); ein Fehlgriff vor ein paar Chargen war nicht mehr erreichbar. Die
Housekeeping-Ansicht „Markierte Datensätze“ zeigt dieselben Zeilen, aber nur
lesend und für das endgültige Entfernen gedacht.

Zurücknehmen ändert nur deleted_points: Parquet und Rollups wurden durch das
Markieren nie angefasst, es gibt also nichts neu zu berechnen.

Am anderen Ende steht das ENDGÜLTIGE Entfernen genau dieser Entität: dieselbe
Bereinigung wie unter Housekeeping → Speicherplatz, nur auf eine Entität
beschränkt. Sie läuft unter der Entitätssperre statt der globalen — das Korrigieren
und Hinzufügen in archivierten Monaten schreibt dieselben Parquet-Dateien und
Rollups um und tut es genauso. Es teilt sich den Fortschrittsauftrag mit der
globalen Bereinigung, es läuft also höchstens eine gleichzeitig."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates

from . import entity_history
from .formatting import decimals_to_int, format_time, format_timestamp, format_value
from .housekeeping_routes import purge_progress
from .i18n import N_, tr
from .notices import pending_purge_for
from .progress import JobBusy
from .storage import cleanup
from .storage.coordinator import StorageCoordinator
from .storage.index import Index

logger = logging.getLogger(__name__)

PAGE_SIZES = (10, 20, 50, 100, 200)
DEFAULT_PAGE_SIZE = 50
MODES = ("selection", "batch", "all")


@dataclass(frozen=True)
class MarkedValuesDependencies:
    index: Index
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    templates: Jinja2Templates
    invalidate_purge_preview: Callable[[], None]
    load_purge_rows: Callable[[], list[dict]]


def _purge_result_text(total_rows: int, months: int) -> str:
    """Ergebnistext des Laufs — dieselben Sätze wie die Bereinigung unter
    Housekeeping, damit beide Wege dasselbe sagen."""
    if total_rows == 0:
        return tr("Nichts zu bereinigen — aktuell keine entfernbaren Datensätze gefunden.")
    if months == 0:
        return (
            tr("{total_rows} Zeile physisch entfernt.", total_rows=total_rows)
            if total_rows == 1
            else tr("{total_rows} Zeilen physisch entfernt.", total_rows=total_rows)
        )
    return tr(
        "{rows} physisch entfernt, davon {months} neu berechnet.",
        rows=(tr("{count} Zeile", count=total_rows) if total_rows == 1 else tr("{count} Zeilen", count=total_rows)),
        months=(
            tr("{count} bereits archivierter Monat", count=months)
            if months == 1
            else tr("{count} bereits archivierte Monate", count=months)
        ),
    )


def create_marked_values_router(deps: MarkedValuesDependencies) -> APIRouter:
    router = APIRouter()

    def entity_or_404(entity_id: str):
        entity = deps.index.get_entity(entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail=tr("Unbekannte Entität"))
        return entity

    def clean_page_size(value: int) -> int:
        return value if value in PAGE_SIZES else DEFAULT_PAGE_SIZE

    def panel(
        request: Request, entity_id: str, page: int, page_size: int, *,
        notice: str = "", notice_is_error: bool = False,
    ) -> HTMLResponse:
        """Die ganze Ansicht (Liste, Aktionen, Banner und Reiter-Zähler per
        hx-swap-oob) — nach jedem Zurücknehmen frisch, weil sich Zahlen und
        Chargengrößen ändern. ``notice`` ist das Ergebnis eines endgültigen
        Entfernens, das über der Liste stehen bleibt."""
        entity = entity_or_404(entity_id)
        result = deps.index.list_deleted_points_for_entity(entity_id, page=page, page_size=page_size)
        timestamps = [row["ts"] for row in result["rows"]]
        values = cleanup.read_values_for_timestamps(deps.data_dir, entity_id, timestamps, deps.tz)
        batch_sizes = deps.index.count_deleted_by_batch(
            entity_id, list({row["deleted_at"] for row in result["rows"]})
        )
        decimals_int = decimals_to_int(entity["decimals"])
        groups: list[dict] = []
        for row in result["rows"]:
            if not groups or groups[-1]["deleted_at"] != row["deleted_at"]:
                groups.append({
                    "deleted_at": row["deleted_at"],
                    "label": f"{format_timestamp(row['deleted_at'], deps.tz)} {format_time(row['deleted_at'], deps.tz)}",
                    "size": batch_sizes.get(row["deleted_at"], 0),
                    "rows": [],
                })
            groups[-1]["rows"].append({
                "id": row["id"],
                "measured_at": f"{format_timestamp(row['ts'], deps.tz)} {format_time(row['ts'], deps.tz)}",
                "value_label": format_value(values[row["ts"]], decimals_int) if row["ts"] in values else "—",
            })
        return deps.templates.TemplateResponse(
            request,
            "_marked_values.html",
            {
                "entity_id": entity_id,
                "unit": entity["unit"],
                "groups": groups,
                "pagination": result["pagination"],
                "page_sizes": PAGE_SIZES,
                "total": result["pagination"]["total"],
                "notice": notice,
                "notice_is_error": notice_is_error,
                "pending_purge": pending_purge_for(entity, deps.load_purge_rows()),
            },
        )

    @router.get("/entities/{entity_id}/marked", response_class=HTMLResponse)
    async def marked_values(
        request: Request, entity_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=DEFAULT_PAGE_SIZE),
    ) -> HTMLResponse:
        return await run_in_threadpool(panel, request, entity_id, page, clean_page_size(page_size))

    async def read_form(request: Request) -> tuple[str, list[int], float | None, int, int]:
        form = await request.form()
        mode = str(form.get("mode", ""))
        if mode not in MODES:
            raise HTTPException(status_code=400, detail=tr("Ungültige Aktion"))
        try:
            ids = [int(value) for key, value in form.multi_items() if key == "id"]
            batch = float(form["batch"]) if form.get("batch") not in (None, "") else None
            page = max(1, int(form.get("page", 1)))
            page_size = clean_page_size(int(form.get("page_size", DEFAULT_PAGE_SIZE)))
        except ValueError:
            raise HTTPException(status_code=400, detail=tr("Ungültige Eingabe")) from None
        return mode, ids, batch, page, page_size

    @router.post("/entities/{entity_id}/marked/confirm", response_class=HTMLResponse)
    async def marked_values_confirm(request: Request, entity_id: str) -> HTMLResponse:
        """Rückfrage mit der Zahl der betroffenen Markierungen, bevor etwas
        zurückgenommen wird. Nur dieses kleine Teilstück kommt zurück — die Liste
        und die darin angekreuzten Zeilen bleiben unberührt."""
        mode, ids, batch, page, page_size = await read_form(request)
        entity = entity_or_404(entity_id)

        def build() -> dict:
            label = None
            if mode == "selection":
                count = len(ids)
            elif mode == "batch":
                count = deps.index.count_deleted_by_batch(entity_id, [batch]).get(batch, 0) if batch is not None else 0
                if batch is not None:
                    label = f"{format_timestamp(batch, deps.tz)} {format_time(batch, deps.tz)}"
            else:
                count = int(entity["deleted_count"] or 0)
            return {
                "mode": mode, "ids": ids, "batch": batch, "page": page, "page_size": page_size,
                "count": count, "label": label,
            }

        confirm = await run_in_threadpool(build)
        return deps.templates.TemplateResponse(
            request, "_marked_confirm.html", {"entity_id": entity_id, "confirm": confirm}
        )

    @router.post("/entities/{entity_id}/marked/undo", response_class=HTMLResponse)
    async def marked_values_undo(request: Request, entity_id: str) -> HTMLResponse:
        mode, ids, batch, page, page_size = await read_form(request)
        entity_or_404(entity_id)

        def undo() -> HTMLResponse:
            started_at = time.time()
            with deps.coordinator.entity(entity_id):
                if mode == "selection":
                    count = deps.index.undo_deleted_ids(entity_id, ids)
                elif mode == "batch":
                    if batch is None:
                        raise HTTPException(status_code=400, detail=tr("Ungültige Eingabe"))
                    count = deps.index.undo_deleted_batch(entity_id, batch)
                else:
                    count = deps.index.undo_all_deleted(entity_id)
            if count:
                deps.index.log_entity_action(
                    entity_id, "undo", "manual", started_at, time.time(), "success",
                    rows_affected=count, detail=json.dumps({"mode": mode}),
                )
            deps.invalidate_purge_preview()
            return panel(request, entity_id, page, page_size)

        return await run_in_threadpool(undo)

    # -- Verlauf -------------------------------------------------------------

    @router.get("/entities/{entity_id}/history", response_class=HTMLResponse)
    async def entity_history_panel(
        request: Request, entity_id: str, filter: str = "all", days: int = entity_history.DEFAULT_DAYS,
    ) -> HTMLResponse:
        entity = entity_or_404(entity_id)
        filter_key = filter if filter in entity_history.FILTERS else "all"
        days_key = days if days in entity_history.DAYS_OPTIONS else entity_history.DEFAULT_DAYS

        def build() -> dict:
            rows = deps.index.list_entity_actions_for_entity(
                entity_id,
                since_ts=time.time() - days_key * 86400 if days_key else None,
                actions=entity_history.FILTERS[filter_key],
                limit=entity_history.LIMIT,
            )
            return {
                "days_list": entity_history.build_days(rows, deps.tz, decimals_to_int(entity["decimals"]), entity["unit"]),
                "truncated": len(rows) >= entity_history.LIMIT,
            }

        context = await run_in_threadpool(build)
        return deps.templates.TemplateResponse(
            request,
            "_entity_history.html",
            {
                "entity_id": entity_id,
                "filter_key": filter_key,
                "days_key": days_key,
                "filters": [(key, entity_history.FILTER_LABELS[key]) for key in entity_history.FILTERS],
                "days_options": entity_history.DAYS_OPTIONS,
                "limit": entity_history.LIMIT,
                **context,
            },
        )

    # -- Endgültiges Entfernen genau dieser Entität --------------------------

    @router.post("/entities/{entity_id}/marked/purge-preview", response_class=HTMLResponse)
    async def marked_values_purge_preview(request: Request, entity_id: str) -> HTMLResponse:
        """Rückfrage vor dem endgültigen Entfernen, mit frisch gerechneten Zahlen
        (nicht aus der bis zu einer Stunde alten Vorschau): wie viele Werte, in wie
        vielen Archivmonaten, und wie viele Markierungen keine Zeile mehr haben."""
        entity_or_404(entity_id)

        def build() -> dict:
            with deps.coordinator.entity(entity_id):
                preview = cleanup.preview_purge(deps.data_dir, deps.index, deps.tz, entity_ids=[entity_id])
            return preview["totals"]

        totals = await run_in_threadpool(build)
        return deps.templates.TemplateResponse(
            request, "_marked_purge_confirm.html", {"entity_id": entity_id, "totals": totals}
        )

    def purge_worker(entity_id: str) -> str:
        """Der Lauf im Hintergrund-Thread. Rückgabewert ist der Ergebnistext."""
        started_at = time.time()
        with deps.coordinator.entity(entity_id):
            preview = cleanup.preview_purge(deps.data_dir, deps.index, deps.tz, entity_ids=[entity_id])
            purge_progress.set_phase(
                N_("Bereinigung läuft…"), int(preview["totals"].get("archive_months", 0) or 0)
            )
            hot_purged = cleanup.purge_hot_buffer(deps.data_dir, deps.index, deps.tz, entity_ids=[entity_id])
            archive_result = cleanup.purge_archived_months(
                deps.data_dir, deps.index, deps.tz, entity_ids=[entity_id],
                on_month=lambda count, ident: purge_progress.advance(count, ident),
            )
        total_rows = hot_purged + archive_result["rows_purged"]
        months = archive_result["months_purged"]
        if total_rows:
            deps.index.log_entity_action(
                entity_id, "purge", "manual", started_at, time.time(), "success", rows_affected=total_rows
            )
        deps.invalidate_purge_preview()
        logger.info(
            "Bereinigung einer Entität abgeschlossen · event=entity_cleanup_completed entity=%s rows=%d months=%d",
            entity_id, total_rows, months,
        )
        return _purge_result_text(total_rows, months)

    def progress_response(request: Request, entity_id: str) -> HTMLResponse:
        return deps.templates.TemplateResponse(
            request, "_marked_purge_progress.html", {"entity_id": entity_id, **purge_progress.snapshot()}
        )

    @router.post("/entities/{entity_id}/marked/purge", response_class=HTMLResponse)
    async def marked_values_purge(request: Request, entity_id: str) -> HTMLResponse:
        """Startet das endgültige Entfernen im Hintergrund und antwortet sofort mit
        der Fortschrittsanzeige. Ein zweiter Klick (oder ein laufender globaler
        Lauf) startet nichts Neues, er bekommt die laufende Anzeige."""
        entity_or_404(entity_id)
        try:
            purge_progress.start(lambda: purge_worker(entity_id), logger, kind=entity_id)
        except JobBusy:
            logger.info("Bereinigung bereits aktiv · event=entity_cleanup_already_running entity=%s", entity_id)
        return await run_in_threadpool(progress_response, request, entity_id)

    @router.get("/entities/{entity_id}/marked/purge/progress", response_class=HTMLResponse)
    async def marked_values_purge_progress(request: Request, entity_id: str) -> HTMLResponse:
        """Poll-Ziel der Fortschrittsanzeige: solange der Lauf geht wieder die Anzeige,
        danach die Liste mit dem Ergebnis darüber (ohne hx-trigger — das beendet das
        Polling von selbst)."""
        stand = purge_progress.snapshot()
        if not stand["started"]:
            return await run_in_threadpool(panel, request, entity_id, 1, DEFAULT_PAGE_SIZE)
        if stand["running"]:
            return await run_in_threadpool(progress_response, request, entity_id)
        if stand["error"]:
            notice, is_error = tr("Bereinigung fehlgeschlagen: {error}", error=stand["error"]), True
        else:
            notice, is_error = stand["result"] or "", False
        return await run_in_threadpool(
            lambda: panel(request, entity_id, 1, DEFAULT_PAGE_SIZE, notice=notice, notice_is_error=is_error)
        )

    return router
