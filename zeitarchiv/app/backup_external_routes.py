"""Karte „Externes Ziel“ auf der Backup-Seite (BACKUP_EXTERN_KONZEPT.md).

Die Auswahl kommt aus den von Home Assistant eingebundenen Netzwerkspeichern
unter /share und /media (storage/backup_external.py) — es gibt bewusst kein
freies Pfadfeld. Kopieren und Aufräumen laufen im Backup-Worker
(background.py: external_transfer()); hier nur Einstellungen, Test und
„Erneut übertragen“."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .background import BackgroundService
from .formatting import format_size, format_time, format_timestamp
from .i18n import tr
from .storage import backup_external
from .storage.index import Index

TEST_STALL_SECONDS = 15.0
EXTERNAL_KEEP_OPTIONS = ("same", "never")


@dataclass(frozen=True)
class BackupExternalDependencies:
    index: Index
    templates: Jinja2Templates
    background: BackgroundService
    tz: object
    backup_table_context: Callable[[], dict]


def create_backup_external_router(deps: BackupExternalDependencies) -> APIRouter:
    router = APIRouter()
    index = deps.index

    def mount_rows(selected: str) -> list[dict]:
        rows = []
        for entry in backup_external.list_mounts():
            used = None
            if entry.free_bytes is not None and entry.total_bytes:
                used = round(100 * (1 - entry.free_bytes / entry.total_bytes))
            rows.append({
                "path": str(entry.path),
                "kind": tr("Share") if entry.kind == "share" else tr("Medien"),
                "mounted": entry.mounted,
                "source": entry.source or "",
                "free": format_size(entry.free_bytes) if entry.free_bytes is not None else "",
                "total": format_size(entry.total_bytes) if entry.total_bytes is not None else "",
                "used_percent": used,
                "selected": str(entry.path) == selected,
            })
        return rows

    def last_transfer() -> dict | None:
        for row in index.list_backup_jobs(20):
            if row["external_status"] in ("ok", "failed"):
                ts = row["external_finished_at"]
                return {
                    "status": row["external_status"],
                    "when": f"{format_timestamp(ts, deps.tz)} {format_time(ts, deps.tz)}" if ts else "—",
                    "size": format_size(row["size_bytes"]) if row["size_bytes"] else "",
                    "target": row["external_target"] or "",
                    "error": row["external_error"] or "",
                }
        return None

    def card_context(message: str | None = None, error: str | None = None) -> dict:
        config = backup_external.load_config(index)
        mounts = mount_rows(config.mount)
        return {
            "ext": {
                "type": config.type if config.type in ("off", "dir") else "off",
                "mount": config.mount,
                "subfolder": config.subfolder,
                "keep": config.keep if config.keep in EXTERNAL_KEEP_OPTIONS else "same",
                "mounts": mounts,
                "usable": [m for m in mounts if m["mounted"]],
                "last": last_transfer(),
                "message": message,
                "error": error,
            },
        }

    def card(request: Request, **kwargs) -> HTMLResponse:
        return deps.templates.TemplateResponse(request, "_backup_external.html", card_context(**kwargs))

    @router.get("/backup/external", response_class=HTMLResponse)
    def external_card(request: Request) -> HTMLResponse:
        return card(request)

    @router.post("/backup/external", response_class=HTMLResponse)
    def external_save(
        request: Request,
        external_type: str = Form("off"),
        external_mount: str = Form(""),
        external_subfolder: str = Form(""),
        external_keep: str = Form("same"),
    ) -> HTMLResponse:
        if external_type not in ("off", "dir"):
            raise HTTPException(status_code=400, detail=tr("Ungültiges Ziel"))
        if external_keep not in EXTERNAL_KEEP_OPTIONS:
            raise HTTPException(status_code=400, detail=tr("Ungültige Aufbewahrung"))
        if external_type == "dir":
            try:
                subfolder = backup_external.validate_subfolder(external_subfolder)
                backup_external.resolve_target(external_mount, subfolder)
            except (ValueError, backup_external.ExternalTargetError) as exc:
                return card(request, error=str(exc))
            index.set_setting("backup_external_mount", external_mount)
            index.set_setting("backup_external_subfolder", subfolder)
        index.set_setting("backup_external_type", external_type)
        index.set_setting("backup_external_keep", external_keep)
        return card(request, message=tr("Gespeichert."))

    @router.post("/backup/external/test", response_class=HTMLResponse)
    def external_test(
        external_mount: str = Form(""),
        external_subfolder: str = Form(""),
    ) -> HTMLResponse:
        """Schreibt, liest und löscht eine Testdatei auf dem gewählten Ziel."""
        try:
            subfolder = backup_external.validate_subfolder(external_subfolder)

            def work(beat):
                target = backup_external.resolve_target(external_mount, subfolder)
                return backup_external.check_target(target, beat)

            result = backup_external.run_with_watchdog(work, stall_seconds=TEST_STALL_SECONDS, poll_seconds=0.1)
        except (ValueError, backup_external.ExternalTargetError, OSError) as exc:
            return HTMLResponse(
                f'<div class="ext-result bad" role="alert">{_escape(str(exc))}</div>'
            )
        free = result.get("free_bytes")
        text = tr("Testdatei geschrieben, gelesen und gelöscht.")
        if free is not None:
            text += " " + tr("{free} frei.", free=format_size(free))
        return HTMLResponse(f'<div class="ext-result ok" role="status">{_escape(text)}</div>')

    @router.post("/backup/external/retry/{filename}", response_class=HTMLResponse)
    def external_retry(request: Request, filename: str) -> HTMLResponse:
        started = deps.background.retry_external(filename)
        if not started:
            raise HTTPException(status_code=409, detail=tr("Übertragung konnte nicht gestartet werden"))
        return deps.templates.TemplateResponse(request, "_backup_table.html", deps.backup_table_context())

    return router


def _escape(text: str) -> str:
    from markupsafe import escape

    return str(escape(text))
