"""Schreibende Formulare und „Auswahl & Verlauf“ (ROADMAP 1.33, Phase 1 und 2).

Jede Änderung läuft in zwei Schritten: der erste POST zeigt eine Bestätigung
(serverseitig gerendert, ohne JavaScript nutzbar), erst der zweite mit
``confirmed=1`` legt einen Auftrag an (selection_jobs.py) und leitet auf den
Verlauf weiter (Post/Redirect/Get, ein Neuladen legt nichts doppelt an).
Geprüft wird serverseitig gegen den Katalog — was die Oberfläche sperrt, wird
hier nicht doch angenommen.

Hier: Vorschläge übernehmen (auch aus der Suche und mit „Ausschluss aufheben“),
die Übersicht, Entfernen, Rückgängig und die Statusseite eines Auftrags. Die
Aufträge aus Phase 2 (Ausschließen, Wieder aufnehmen, Muster) liegen in
selection_change_routes.py und nutzen dieselben Helfer (``Pages``).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import selection_catalog, selection_jobs, selection_overview, selection_search, selection_suggest
from .formatting import format_time, format_timestamp
from .i18n import tr
from .selection_topics import TOPIC_INFO, rule_anwesenheit, topic_label
from .storage.index import Index

#: Namen in einer Bestätigung; der Rest erscheint als Zahl.
CONFIRM_LIST = 30
#: Nach diesen Aufträgen lohnt sich der Verlauf aus Home Assistant (Statusseite).
IMPORT_HINT_ACTIONS = ("add", "add_patterns", "include")


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


def view_url(request: Request, topic: str, query: str = "") -> str:
    """Zurück zur Vorschlagsseite: zur Suche (``q``) oder zum Thema."""
    if topic == selection_search.SEARCH_TOPIC:
        return f"{_app_root(request)}/selection?{urlencode({'q': query})}"
    return f"{_app_root(request)}/selection?topic={topic if topic in selection_suggest.TOPICS else 'energie'}"


def view_result(topic: str, query: str, entities: list[dict[str, Any]], archived: set[str]) -> dict[str, Any]:
    """Ergebnis einer Ansicht: Suche oder Thema (gleiche Form)."""
    if topic == selection_search.SEARCH_TOPIC:
        return selection_search.search(query, entities, archived)
    return selection_suggest.suggest(topic, entities, archived)


class Pages:
    """Gemeinsame Helfer der schreibenden Routen: Fehlerseite, Bestätigung, Weiterleitung,
    Katalog mit Versionsprüfung, archivierte Entitäten, Zeitangaben."""

    def __init__(self, get_index: Callable[[], Index], templates: Jinja2Templates, data_dir: Path, tz) -> None:
        self.get_index, self.templates, self.data_dir, self.tz = get_index, templates, data_dir, tz

    def when(self, ts: float | None) -> str:
        return f"{format_timestamp(ts, self.tz)} {format_time(ts, self.tz)}" if ts else ""

    def archived(self) -> set[str]:
        return {entity["entity_id"] for entity in self.get_index().list_entities()}

    def error(self, request: Request, message: str, back: str, status: int = 400) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request, "selection_confirm.html", {"kind": "error", "message": message, "back": back}, status_code=status,
        )

    def confirm(self, request: Request, context: dict[str, Any], status: int = 200) -> HTMLResponse:
        return self.templates.TemplateResponse(request, "selection_confirm.html", context, status_code=status)

    def done(self, request: Request, job: dict[str, Any]) -> RedirectResponse:
        return RedirectResponse(url=f"{_app_root(request)}/selection/overview?job={job['id']}", status_code=303)

    def ready_catalog(self, *, phase2: bool = False) -> tuple[dict[str, Any] | None, str | None]:
        """Der Katalog, wenn die Integration die Aufträge versteht; sonst ein Grund.
        ``phase2``: Ausschließen, Wieder aufnehmen und Muster brauchen Phase 2."""
        catalog = selection_catalog.load(self.data_dir)
        if catalog is None:
            return None, tr("Noch kein Katalog empfangen.")
        if not catalog.get("phase1") or (phase2 and not catalog.get("phase2")):
            return None, tr("Zum Übernehmen braucht es eine neuere Version der Zeitarchiv-Integration.")
        return catalog, None

    def create(self, request: Request, back: str, **job: Any) -> Response:
        try:
            created = selection_jobs.create(self.data_dir, **job)
        except selection_jobs.JobError as exc:
            return self.error(request, str(exc), back)
        return self.done(request, created)


def pick_rows(
    requested: list[str], rows: dict[str, dict[str, Any]], fits: Callable[[dict[str, Any]], bool], *, single: bool,
) -> tuple[list[str], int, str | None]:
    """Prüft markierte IDs für eine Aktion. Eine einzelne Zeile (Zeilenmenü) muss passen;
    bei einer Massenaktion fallen unpassende Zeilen weg und werden gezählt.
    Ergebnis: (passende IDs, Zahl übersprungener, Fehlertext oder None)."""
    if not requested:
        return [], 0, tr("Keine Entität ausgewählt.")
    if len(requested) > selection_jobs.MAX_IDS:
        return [], 0, tr("Höchstens {max} Entitäten je Auftrag. Bitte weniger markieren.", max=selection_jobs.MAX_IDS)
    unknown = next((eid for eid in requested if eid not in rows), None)
    if unknown is not None:
        return [], 0, tr("Nicht im Katalog: {entity_id}", entity_id=unknown[:80])
    fitting = [eid for eid in requested if fits(rows[eid])]
    if single and not fitting:
        return [], 0, ""  # der Aufrufer nennt den Grund für genau diese Zeile
    if not fitting:
        return [], 0, tr("Keine der markierten Entitäten passt zu dieser Aktion.")
    return fitting, len(requested) - len(fitting), None


def make_ui_router(get_index: Callable[[], Index], templates: Jinja2Templates, data_dir: Path, tz) -> APIRouter:
    router = APIRouter()
    pages = Pages(get_index, templates, data_dir, tz)
    when, archived, error, confirm = pages.when, pages.archived, pages.error, pages.confirm

    # ---- Vorschläge übernehmen ---------------------------------------------------------

    @router.post("/selection/apply", response_class=HTMLResponse)
    def apply(
        request: Request,
        topic: str = Form(...),
        q: str = Form(""),
        mode: str = Form("label"),
        entity_id: list[str] = Form(default=[]),
        unexclude_id: list[str] = Form(default=[]),
        confirmed: str = Form(""),
    ) -> Response:
        is_search = topic == selection_search.SEARCH_TOPIC
        query = selection_search.clean_query(q)
        back = view_url(request, topic, query)
        if not is_search and topic not in selection_suggest.TOPICS:
            return error(request, tr("Unbekanntes Thema."), back)
        if mode not in selection_jobs.ADD_MODES:
            return error(request, tr("Unbekannter Modus für das Hinzufügen."), back)
        catalog, problem = pages.ready_catalog()
        if catalog is None:
            return error(request, problem or "", back)
        requested = list(dict.fromkeys(entity_id))
        requested_unexclude = [eid for eid in dict.fromkeys(unexclude_id) if eid not in requested]
        if not requested and not requested_unexclude:
            return error(request, tr("Keine Entität ausgewählt."), back)
        if len(requested) + len(requested_unexclude) > selection_jobs.MAX_IDS:
            return error(request, tr("Höchstens {max} Entitäten je Auftrag.", max=selection_jobs.MAX_IDS), back)
        rows = {row["entity_id"]: row for row in catalog["entities"]}
        unknown = [eid for eid in (*requested, *requested_unexclude) if eid not in rows]
        if unknown:
            return error(request, tr("Nicht im Katalog: {entity_id}", entity_id=unknown[0][:80]), back)
        suggested = view_result(topic, query, catalog["entities"], archived())
        candidates = {
            row["entity_id"]: row for section in ("recommended", "more")
            for group in suggested[section] for row in group["rows"]
        }
        foreign = [eid for eid in (*requested, *requested_unexclude) if eid not in candidates]
        if foreign:
            text = tr("Kein Treffer dieser Suche: {entity_id}", entity_id=foreign[0][:80]) if is_search \
                else tr("Gehört nicht zu diesem Thema: {entity_id}", entity_id=foreign[0][:80])
            return error(request, text, back)
        if requested_unexclude and not catalog.get("phase2"):
            return error(request, tr("Zum Aufheben eines Ausschlusses braucht es eine neuere Version der Zeitarchiv-Integration."), back)
        locked = next((eid for eid in requested_unexclude if not candidates[eid]["unexcludable"]), None)
        if locked is not None:
            return error(request, tr("{entity_id} ist über ein Muster ausgeschlossen. Das lässt sich nur in Home Assistant ändern.", entity_id=locked[:80]), back)
        new_ids = [eid for eid in requested if candidates[eid]["status"] == "new"]
        skipped = len(requested) - len(new_ids)
        ids = [*new_ids, *requested_unexclude]
        if not ids:
            return error(request, tr("Alle gewählten Entitäten sind schon archiviert, ausgewählt oder ausgeschlossen."), back)
        force_ids = [eid for eid in ids if rows[eid].get("text")]
        if confirmed != "1":
            sensitive = (
                any(rule_anwesenheit(rows[eid]) is not None for eid in ids) if is_search
                else TOPIC_INFO[topic]["sensitive"]
            )
            return confirm(request, {
                "kind": "apply", "topic": topic, "q": query, "is_search": is_search,
                "topic_text": tr("Suche") if is_search else topic_label(topic), "mode": mode,
                "ids": new_ids, "unexclude": requested_unexclude, "count": len(ids),
                "names": [candidates[eid]["name"] for eid in new_ids[:CONFIRM_LIST]],
                "more": max(0, len(new_ids) - CONFIRM_LIST),
                "unexclude_names": [candidates[eid]["name"] for eid in requested_unexclude[:CONFIRM_LIST]],
                "skipped": skipped, "text_count": len(force_ids),
                "high_rate": sum(1 for eid in ids if candidates[eid]["high_rate"]),
                "sensitive": sensitive, "back": back,
                "note": None if is_search else TOPIC_INFO[topic].get("note"),
            })
        return pages.create(
            request, back, source="assistant", action="add", mode=mode, topic=None if is_search else topic,
            entity_ids=ids, force_ids=force_ids, unexclude_ids=requested_unexclude, entry_id=catalog.get("entry_id"),
        )

    # ---- Auswahl & Verlauf ---------------------------------------------------------------

    @router.get("/selection/overview", response_class=HTMLResponse)
    def overview(request: Request, filter: str = "alle", q: str = "", page: int = 1, job: str = "") -> HTMLResponse:
        catalog = selection_catalog.load(data_dir)
        jobs = selection_jobs.all_jobs(data_dir)
        data = None
        patterns: list[dict[str, Any]] = []
        if catalog is not None:
            data = selection_overview.table(
                catalog["entities"], archived(), filter_key=filter, query=q, page=page,
                managed_label=(catalog.get("label") or {}).get("name"),
            )
            for row in data["rows"]:
                row["since"] = when(row["added_ts"])
            patterns = [
                {**item, "since": when(item.get("added_ts")),
                 "topic_text": topic_label(item["topic"]) if item.get("topic") else ""}
                for item in catalog.get("patterns") or ()
            ]
        timeline = selection_overview.timeline(catalog, jobs)
        for item in timeline:
            item["when"] = when(item.get("ts"))
        current = next((item for item in jobs if item["id"] == job), None) if job else None
        return templates.TemplateResponse(request, "selection_overview.html", {
            "catalog": catalog, "table": data, "timeline": timeline, "filters": selection_overview.FILTERS,
            "job": current, "job_status_text": tr(str(selection_overview.STATUS_TEXT.get(current["status"], "")))
            if current else "", "can_apply": bool(catalog and catalog.get("phase1")),
            "can_change": bool(catalog and catalog.get("phase1") and catalog.get("phase2")),
            "received": when(catalog["received_ts"]) if catalog else "",
            "label": (catalog or {}).get("label"), "patterns": patterns,
        })

    @router.get("/selection/jobs/{job_id}/status")
    def job_status(request: Request, job_id: str) -> Response:
        """Stand eines Auftrags: JSON für die kleine Statusabfrage nach dem Übernehmen
        (selection_overview.js), als Seite für den Browser (Accept: text/html). Nach einem
        übernommenen Hinzufügen nennt die Seite den Import aus Home Assistant."""
        job = selection_jobs.get(data_dir, job_id[:64])
        accept = request.headers.get("accept", "")
        if "text/html" not in accept or "application/json" in accept:
            if job is None:
                return JSONResponse({"status": None}, status_code=404)
            return JSONResponse({"status": job["status"]})
        if job is None:
            return error(request, tr("Diesen Auftrag gibt es nicht (mehr)."), f"{_app_root(request)}/selection/overview", 404)
        keys = job.get("patterns") if job.get("action") in selection_jobs.PATTERN_ACTIONS else job.get("entity_ids")
        results = job.get("results") or {}
        details = [
            {"key": key, "result": tr(str(selection_overview.RESULT_TEXT[results[key]["result"]]))
             if (results.get(key) or {}).get("result") in selection_overview.RESULT_TEXT else ""}
            for key in (keys or [])[:CONFIRM_LIST * 4]
        ]
        return templates.TemplateResponse(request, "selection_job.html", {
            "job": job, "details": details, "pattern_job": job.get("action") in selection_jobs.PATTERN_ACTIONS,
            "action_text": tr(str(selection_overview.ACTION_TEXT.get(job.get("action"), job.get("action") or ""))),
            "status_text": tr(str(selection_overview.STATUS_TEXT.get(job.get("status"), job.get("status") or ""))),
            "created": when(job.get("created_ts")), "finished": when(job.get("finished_ts")),
            "topic_text": topic_label(job["topic"]) if job.get("topic") else "",
            "import_hint": job.get("status") in {"applied", "partial"} and job.get("action") in IMPORT_HINT_ACTIONS,
        })

    @router.post("/selection/remove", response_class=HTMLResponse)
    def remove(
        request: Request,
        ids: list[str] = Form(default=[]),
        one: str = Form(""),
        confirmed: str = Form(""),
        confirm_manual: str = Form(""),
    ) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = pages.ready_catalog()
        if catalog is None:
            return error(request, problem or "", back)
        rows = {row["entity_id"]: row for row in catalog["entities"]}
        requested = [one] if one else list(dict.fromkeys(ids))
        picked, skipped, problem = pick_rows(requested, rows, selection_overview.removable, single=bool(one))
        if problem == "":
            message = tr("{entity_id} ist nur über Muster, Bereich, Gerät oder Label ausgewählt. Das lässt sich nur in Home Assistant ändern (Zeitarchiv-Integration, Optionen).", entity_id=one[:80])
            return error(request, message, back)
        if problem:
            return error(request, problem, back)
        manual = [eid for eid in picked if selection_overview.manual_single_id(rows[eid])]
        still = [eid for eid in picked if any(
            way.get("type") in {"pattern", "area", "device", "label"} for way in rows[eid].get("via") or ()
        )]
        context = {
            "kind": "remove", "ids": picked, "one": one, "back": back, "skipped": skipped,
            "assistant_names": [rows[eid].get("name") or eid for eid in picked if eid not in manual][:CONFIRM_LIST],
            "manual_names": [rows[eid].get("name") or eid for eid in manual][:CONFIRM_LIST],
            "assistant_count": len(picked) - len(manual), "manual_count": len(manual), "still_count": len(still),
        }
        if confirmed != "1":
            return confirm(request, context)
        if manual and confirm_manual != "1":
            return confirm(request, {**context, "manual_missing": True}, status=400)
        return pages.create(
            request, back, source="manual_ui", action="remove", entity_ids=picked, entry_id=catalog.get("entry_id"),
        )

    @router.post("/selection/undo", response_class=HTMLResponse)
    def undo(request: Request, target: str = Form(...), confirmed: str = Form("")) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = pages.ready_catalog()
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
        if plan["action"] not in {"add", "remove"} and not catalog.get("phase2"):
            return error(request, tr("Zum Übernehmen braucht es eine neuere Version der Zeitarchiv-Integration."), back)
        items = plan["patterns"] or plan["entity_ids"]
        if confirmed != "1":
            return confirm(request, {
                "kind": "undo", "target": entry["id"], "entry": entry, "plan": plan, "back": back,
                "names": items[:CONFIRM_LIST], "more": max(0, len(items) - CONFIRM_LIST),
                "count": len(items),
            })
        return pages.create(
            request, back, source="undo", action=plan["action"], mode=plan["mode"] if plan["action"] == "add" else None,
            topic=plan["topic"], entity_ids=plan["entity_ids"], force_ids=plan["force_ids"], patterns=plan["patterns"],
            undo_of=entry["id"], entry_id=catalog.get("entry_id"),
        )

    return router
