"""Restore-Assistent: geführte Wiederherstellung aus einem Backup.

Die Seite ``/backup/restore/<datei>`` fragt zuerst, WAS wiederhergestellt werden soll:

- alles (wie bisher: beim nächsten Add-on-Start eingespielt, Neustart nötig),
- einzelne Entitäten (zur Laufzeit, siehe storage/restore_selective.py),
- Einstellungen (Dashboards, Charts, Tabellen, Feldgruppen der Entitäten, Werte).

Die Schritte laufen auf einer Seite in einem Formular (``#rw``). htmx lädt nur die
Teile nach: Auswahl (``…/options``), Prüfung (``…/review``), Ergebnis (``…/apply``).
Die Prüfung und die Anwendung rechnen denselben Plan (``make_plan``), damit die
Zusammenfassung nie etwas anderes verspricht, als danach passiert.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .formatting import format_size, format_time, format_timestamp
from .i18n import tr
from .storage import backup, restore_selective as rs
from .storage.coordinator import StorageCoordinator
from .storage.index import Index

MODES = ("all", "entities", "settings")


@dataclass(frozen=True)
class RestoreDependencies:
    index: Index
    templates: Jinja2Templates
    data_dir: Path
    backups_dir: Path
    coordinator: StorageCoordinator
    tz: ZoneInfo


def create_restore_router(deps: RestoreDependencies) -> APIRouter:
    router = APIRouter()
    index, templates, tz = deps.index, deps.templates, deps.tz

    def backup_path(filename: str) -> Path:
        path = backup.resolve_backup_path(deps.backups_dir, filename)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail=tr("Backup nicht gefunden"))
        return path

    def stamp(ts: float | None) -> str:
        return f"{format_timestamp(ts, tz)} {format_time(ts, tz)}" if ts else "—"

    def read_form(form) -> dict:
        strategies = {key[len("strat:"):]: value for key, value in form.multi_items() if key.startswith("strat:")}
        return {
            "mode": form.get("mode", ""),
            "entities": [v for v in form.getlist("ent")],
            "selected": set(form.getlist("sel")),
            "strategies": strategies,
            "confirm": form.get("confirm") == "1",
        }

    def fragment(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    # ------------------------------------------------------------------ Seite

    @router.get("/backup/restore/{filename}", response_class=HTMLResponse)
    def restore_page(request: Request, filename: str) -> HTMLResponse:
        path = backup_path(filename)
        created = next((b["created_at"] for b in backup.list_backups(deps.backups_dir) if b["filename"] == filename), None)
        facts: dict = {"size": format_size(path.stat().st_size), "created_at": stamp(created or path.stat().st_mtime)}
        error = None
        try:
            with rs.BackupSource(path) as source:
                row = source.db.execute(
                    "SELECT COUNT(*) AS n, MIN(first_ts) AS first, MAX(last_ts) AS last FROM entities").fetchone()
                members = rs.group_members(source.names)
                facts["entities"] = len(members)
                facts["range"] = (
                    f"{rs._fmt_month(row['first'], tz)} – {rs._fmt_month(row['last'], tz)}" if row["first"] else "—")
                facts["format"] = tr("Format v{v}", v=source.manifest.get("format_version", 0)) if source.manifest \
                    else tr("Älteres Format ohne Prüfsummen")
        except ValueError as exc:
            error = str(exc)
        return fragment(request, "backup_restore.html", filename=filename, facts=facts, rw_error=error)

    # ------------------------------------------------------------------ Auswahl

    @router.get("/backup/restore/{filename}/options", response_class=HTMLResponse)
    def restore_options(request: Request, filename: str, mode: str) -> HTMLResponse:
        path = backup_path(filename)
        if mode not in ("entities", "settings"):
            raise HTTPException(status_code=400, detail=tr("Unbekannte Art der Wiederherstellung"))
        try:
            with rs.BackupSource(path) as source:
                if mode == "entities":
                    rows = rs.list_entity_diff(source, index, tz)
                    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("new", "diff", "same")}
                    return fragment(request, "_restore_entities.html", rows=rows, counts=counts, total=len(rows))
                overview = rs.load_settings_overview(source, index, deps.data_dir)
        except ValueError as exc:
            return fragment(request, "_restore_error.html", message=str(exc))
        return fragment(request, "_restore_settings.html", ov=overview, groups=rs.ENTITY_GROUP_LABELS,
                        entity_group_order=list(rs.ENTITY_GROUPS))

    # ------------------------------------------------------------------ Prüfen

    @router.post("/backup/restore/{filename}/review", response_class=HTMLResponse)
    async def restore_review(request: Request, filename: str) -> HTMLResponse:
        path = backup_path(filename)
        form = read_form(await request.form())
        mode = form["mode"]
        if mode not in MODES:
            return fragment(request, "_restore_error.html", message=tr("Bitte zuerst eine Art wählen."))

        def build() -> dict:
            if mode == "all":
                return {"estimate": format_size(backup.estimate_size_bytes(deps.data_dir))}
            with rs.BackupSource(path) as source:
                if mode == "entities":
                    if not form["entities"]:
                        raise ValueError(tr("Keine Entität ausgewählt"))
                    diff = {r["entity_id"]: r for r in rs.list_entity_diff(source, index, tz)}
                    chosen = [diff[e] for e in form["entities"] if e in diff]
                    if not chosen:
                        raise ValueError(tr("Keine Entität ausgewählt"))
                    return {
                        "chosen": chosen,
                        "rows": sum(r["bak"]["rows"] for r in chosen),
                        "created": [r for r in chosen if r["status"] == "new"],
                        "estimate": format_size(rs.estimate_rollback_bytes(deps.data_dir, [r["entity_id"] for r in chosen])),
                    }
                overview = rs.load_settings_overview(source, index, deps.data_dir)
                plan = rs.make_plan(overview, form["selected"], form["strategies"])
                if not plan["total"]:
                    raise ValueError(tr("Nichts ausgewählt"))
                return {"plan": plan}

        try:
            ctx = await run_in_threadpool(build)
        except ValueError as exc:
            return fragment(request, "_restore_error.html", message=str(exc))
        return fragment(request, "_restore_review.html", mode=mode, filename=filename, **ctx)

    # ------------------------------------------------------------------ Anwenden

    @router.post("/backup/restore/{filename}/apply", response_class=HTMLResponse)
    async def restore_apply(request: Request, filename: str) -> HTMLResponse:
        path = backup_path(filename)
        form = read_form(await request.form())
        mode = form["mode"]
        if mode not in MODES or not form["confirm"]:
            return fragment(request, "_restore_error.html", message=tr("Bitte die Bestätigung ankreuzen."))

        def run() -> dict:
            if mode == "all":
                backup.prepare_restore(deps.data_dir, deps.backups_dir, filename)
                return {"kind": "all"}
            with rs.BackupSource(path) as source:
                if mode == "entities":
                    return rs.restore_entities(deps.data_dir, index, deps.coordinator, source, form["entities"], tz,
                                               source_label=filename)
                overview = rs.load_settings_overview(source, index, deps.data_dir)
                plan = rs.make_plan(overview, form["selected"], form["strategies"])
                return rs.apply_settings(deps.data_dir, index, source, overview, plan, source_label=filename) | {
                    "plan_n": plan["n"], "entity_count": plan["entity_count"]}

        try:
            result = await run_in_threadpool(run)
        except ValueError as exc:
            return fragment(request, "_restore_error.html", message=str(exc))
        return fragment(request, "_restore_result.html", result=result, filename=filename)

    # ------------------------------------------------------------------ Rollbacks

    def rollback_rows() -> list[dict]:
        return [{**r, "created": stamp(r["created_at"]), "size": format_size(r["size_bytes"])} for r in rs.list_rollbacks(deps.data_dir)]

    @router.get("/backup/selective/list", response_class=HTMLResponse)
    def selective_list(request: Request) -> HTMLResponse:
        return fragment(request, "_selective_rollbacks.html", rollbacks=rollback_rows(), message=None)

    @router.post("/backup/selective/undo/{name}", response_class=HTMLResponse)
    def selective_undo(request: Request, name: str) -> HTMLResponse:
        try:
            rs.undo(deps.data_dir, index, deps.coordinator, name, tz)
            message = tr("Rückgängig gemacht. Der Stand vor der selektiven Wiederherstellung ist wieder aktiv.")
        except ValueError as exc:
            message = str(exc)
        return fragment(request, "_selective_rollbacks.html", rollbacks=rollback_rows(), message=message)

    @router.post("/backup/selective/delete/{name}", response_class=HTMLResponse)
    def selective_delete(request: Request, name: str) -> HTMLResponse:
        if not rs.delete_rollback(deps.data_dir, name):
            raise HTTPException(status_code=404, detail=tr("Rollback nicht gefunden"))
        return fragment(request, "_selective_rollbacks.html", rollbacks=rollback_rows(),
                        message=tr("Rollback-Daten wurden gelöscht."))

    return router
