"""Schreibende Formulare und „Auswahl & Verlauf“ (ROADMAP 1.33, Phase 1).

Jede Änderung läuft in zwei Schritten: der erste POST zeigt eine Bestätigung
(serverseitig gerendert, ohne JavaScript nutzbar), erst der zweite mit
``confirmed=1`` legt einen Auftrag an (selection_jobs.py) und leitet auf den
Verlauf weiter (Post/Redirect/Get, ein Neuladen legt nichts doppelt an).
Geprüft wird serverseitig gegen den Katalog — was die Oberfläche sperrt, wird
hier nicht doch angenommen.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import selection_catalog, selection_jobs, selection_overview, selection_suggest
from .formatting import format_time, format_timestamp
from .i18n import tr
from .selection_topics import TOPIC_INFO, topic_label
from .storage.index import Index

#: Namen in einer Bestätigung; der Rest erscheint als Zahl.
CONFIRM_LIST = 30


def _app_root(request: Request) -> str:
    """Ingress-Präfix für die Weiterleitung, nach derselben Regel wie
    ``main._app_root_context`` (dort nicht importierbar, main.py hängt diesen Router ein)."""
    ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
    if (
        ingress_path
        and ingress_path.startswith("/")
        and ".." not in ingress_path
        and all(char.isalnum() or char in "/_-" for char in ingress_path)
    ):
        return ingress_path
    return str(request.scope.get("root_path", "")).rstrip("/")


def make_ui_router(get_index: Callable[[], Index], templates: Jinja2Templates, data_dir: Path, tz) -> APIRouter:
    router = APIRouter()

    def when(ts: float | None) -> str:
        return f"{format_timestamp(ts, tz)} {format_time(ts, tz)}" if ts else ""

    def archived() -> set[str]:
        return {entity["entity_id"] for entity in get_index().list_entities()}

    def error(request: Request, message: str, back: str, status: int = 400) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "selection_confirm.html", {"kind": "error", "message": message, "back": back}, status_code=status,
        )

    def confirm(request: Request, context: dict[str, Any], status: int = 200) -> HTMLResponse:
        return templates.TemplateResponse(request, "selection_confirm.html", context, status_code=status)

    def done(request: Request, job: dict[str, Any]) -> RedirectResponse:
        return RedirectResponse(url=f"{_app_root(request)}/selection/overview?job={job['id']}", status_code=303)

    def ready_catalog() -> tuple[dict[str, Any] | None, str | None]:
        catalog = selection_catalog.load(data_dir)
        if catalog is None:
            return None, tr("Noch kein Katalog empfangen.")
        if not catalog.get("phase1"):
            return None, tr("Zum Übernehmen braucht es eine neuere Version der Zeitarchiv-Integration.")
        return catalog, None

    # ---- Vorschläge übernehmen ---------------------------------------------------------

    @router.post("/selection/apply", response_class=HTMLResponse)
    def apply(
        request: Request,
        topic: str = Form(...),
        mode: str = Form("label"),
        entity_id: list[str] = Form(default=[]),
        confirmed: str = Form(""),
    ) -> Response:
        back = f"{_app_root(request)}/selection?topic={topic if topic in selection_suggest.TOPICS else 'energie'}"
        if topic not in selection_suggest.TOPICS:
            return error(request, tr("Unbekanntes Thema."), back)
        if mode not in selection_jobs.ADD_MODES:
            return error(request, tr("Unbekannter Modus für das Hinzufügen."), back)
        catalog, problem = ready_catalog()
        if catalog is None:
            return error(request, problem or "", back)
        requested = list(dict.fromkeys(entity_id))
        if not requested:
            return error(request, tr("Keine Entität ausgewählt."), back)
        if len(requested) > selection_jobs.MAX_IDS:
            return error(request, tr("Höchstens {max} Entitäten je Auftrag.", max=selection_jobs.MAX_IDS), back)
        rows = {row["entity_id"]: row for row in catalog["entities"]}
        unknown = [eid for eid in requested if eid not in rows]
        if unknown:
            return error(request, tr("Nicht im Katalog: {entity_id}", entity_id=unknown[0][:80]), back)
        suggested = selection_suggest.suggest(topic, catalog["entities"], archived())
        candidates = {
            row["entity_id"]: row for section in ("recommended", "more")
            for group in suggested[section] for row in group["rows"]
        }
        foreign = [eid for eid in requested if eid not in candidates]
        if foreign:
            return error(request, tr("Gehört nicht zu diesem Thema: {entity_id}", entity_id=foreign[0][:80]), back)
        ids = [eid for eid in requested if candidates[eid]["status"] == "new"]
        skipped = len(requested) - len(ids)
        if not ids:
            return error(request, tr("Alle gewählten Entitäten sind schon archiviert, ausgewählt oder ausgeschlossen."), back)
        force_ids = [eid for eid in ids if rows[eid].get("text")]
        if confirmed != "1":
            return confirm(request, {
                "kind": "apply", "topic": topic, "topic_text": topic_label(topic), "mode": mode, "ids": ids,
                "names": [candidates[eid]["name"] for eid in ids[:CONFIRM_LIST]], "more": max(0, len(ids) - CONFIRM_LIST),
                "skipped": skipped, "text_count": len(force_ids),
                "high_rate": sum(1 for eid in ids if candidates[eid]["high_rate"]),
                "sensitive": TOPIC_INFO[topic]["sensitive"], "back": back,
            })
        try:
            job = selection_jobs.create(
                data_dir, source="assistant", action="add", mode=mode, topic=topic, entity_ids=ids,
                force_ids=force_ids, entry_id=catalog.get("entry_id"),
            )
        except selection_jobs.JobError as exc:
            return error(request, str(exc), back)
        return done(request, job)

    # ---- Auswahl & Verlauf ---------------------------------------------------------------

    @router.get("/selection/overview", response_class=HTMLResponse)
    def overview(request: Request, filter: str = "alle", q: str = "", page: int = 1, job: str = "") -> HTMLResponse:
        catalog = selection_catalog.load(data_dir)
        jobs = selection_jobs.all_jobs(data_dir)
        data = None
        if catalog is not None:
            data = selection_overview.table(
                catalog["entities"], archived(), filter_key=filter, query=q, page=page,
                managed_label=(catalog.get("label") or {}).get("name"),
            )
            for row in data["rows"]:
                row["since"] = when(row["added_ts"])
        timeline = selection_overview.timeline(catalog, jobs)
        for item in timeline:
            item["when"] = when(item.get("ts"))
        current = next((item for item in jobs if item["id"] == job), None) if job else None
        return templates.TemplateResponse(request, "selection_overview.html", {
            "catalog": catalog, "table": data, "timeline": timeline, "filters": selection_overview.FILTERS,
            "job": current, "job_status_text": tr(str(selection_overview.STATUS_TEXT.get(current["status"], "")))
            if current else "", "can_apply": bool(catalog and catalog.get("phase1")),
            "received": when(catalog["received_ts"]) if catalog else "",
            "label": (catalog or {}).get("label"),
        })

    @router.get("/selection/jobs/{job_id}/status")
    def job_status(job_id: str) -> JSONResponse:
        """Für die kleine Statusabfrage nach dem Übernehmen (selection_overview.js)."""
        job = selection_jobs.get(data_dir, job_id[:64])
        if job is None:
            return JSONResponse({"status": None}, status_code=404)
        return JSONResponse({"status": job["status"]})

    @router.post("/selection/remove", response_class=HTMLResponse)
    def remove(
        request: Request,
        ids: list[str] = Form(default=[]),
        one: str = Form(""),
        confirmed: str = Form(""),
        confirm_manual: str = Form(""),
    ) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = ready_catalog()
        if catalog is None:
            return error(request, problem or "", back)
        requested = [one] if one else list(dict.fromkeys(ids))
        if not requested:
            return error(request, tr("Keine Entität ausgewählt."), back)
        if len(requested) > selection_jobs.MAX_IDS:
            return error(request, tr("Höchstens {max} Entitäten je Auftrag.", max=selection_jobs.MAX_IDS), back)
        rows = {row["entity_id"]: row for row in catalog["entities"]}
        for eid in requested:
            row = rows.get(eid)
            if row is None:
                return error(request, tr("Nicht im Katalog: {entity_id}", entity_id=eid[:80]), back)
            if not selection_overview.removable(row):
                message = tr("{entity_id} ist nur über Muster, Bereich, Gerät oder Label ausgewählt. Das lässt sich nur in Home Assistant ändern (Zeitarchiv-Integration, Optionen).", entity_id=eid[:80])
                return error(request, message, back)
        manual = [eid for eid in requested if selection_overview.manual_single_id(rows[eid])]
        still = [eid for eid in requested if any(
            way.get("type") in {"pattern", "area", "device", "label"} for way in rows[eid].get("via") or ()
        )]
        context = {
            "kind": "remove", "ids": requested, "one": one, "back": back,
            "assistant_names": [rows[eid].get("name") or eid for eid in requested if eid not in manual][:CONFIRM_LIST],
            "manual_names": [rows[eid].get("name") or eid for eid in manual][:CONFIRM_LIST],
            "assistant_count": len(requested) - len(manual), "manual_count": len(manual), "still_count": len(still),
        }
        if confirmed != "1":
            return confirm(request, context)
        if manual and confirm_manual != "1":
            return confirm(request, {**context, "manual_missing": True}, status=400)
        try:
            job = selection_jobs.create(
                data_dir, source="manual_ui", action="remove", entity_ids=requested, entry_id=catalog.get("entry_id"),
            )
        except selection_jobs.JobError as exc:
            return error(request, str(exc), back)
        return done(request, job)

    @router.post("/selection/undo", response_class=HTMLResponse)
    def undo(request: Request, target: str = Form(...), confirmed: str = Form("")) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = ready_catalog()
        if catalog is None:
            return error(request, problem or "", back)
        entry = selection_overview.find_undo_source(catalog, selection_jobs.all_jobs(data_dir), target[:64])
        if entry is None:
            return error(request, tr("Diesen Eintrag gibt es im Verlauf nicht (mehr)."), back)
        if not entry["undoable"]:
            if entry.get("source") == "manual_ha":
                return error(request, tr("Änderungen in Home Assistant lassen sich hier nicht rückgängig machen."), back)
            return error(request, tr("Dieser Eintrag lässt sich nicht (mehr) rückgängig machen."), back)
        plan = selection_overview.undo_plan(entry)
        if confirmed != "1":
            return confirm(request, {
                "kind": "undo", "target": entry["id"], "entry": entry, "plan": plan, "back": back,
                "names": plan["entity_ids"][:CONFIRM_LIST], "more": max(0, len(plan["entity_ids"]) - CONFIRM_LIST),
            })
        try:
            job = selection_jobs.create(
                data_dir, source="undo", action=plan["action"], mode=plan["mode"] if plan["action"] == "add" else None,
                topic=plan["topic"], entity_ids=plan["entity_ids"], force_ids=plan["force_ids"],
                undo_of=entry["id"], entry_id=catalog.get("entry_id"),
            )
        except selection_jobs.JobError as exc:
            return error(request, str(exc), back)
        return done(request, job)

    return router
