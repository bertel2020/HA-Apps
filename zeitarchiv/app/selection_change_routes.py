"""Ausschließen, Wieder aufnehmen und Muster (ROADMAP 1.33, Phase 2).

Dieselben zwei Schritte wie in selection_ui_routes.py: erst eine Bestätigung,
dann mit ``confirmed=1`` ein Auftrag und die Weiterleitung auf den Verlauf. Alle
Aktionen brauchen eine Integration, die schon Phase 2 spricht (Katalogfeld
``patterns``), sonst kennt sie diese Aufträge nicht.

* **Ausschließen** (``exclude``) geht für jede ausgewählte Zeile, auch wenn sie über
  Muster, Bereich, Gerät oder Label drin ist; die Bestätigung nennt die Quelle.
  Hand-Einträge brauchen wie beim Entfernen eine Zusatzbestätigung.
* **Wieder aufnehmen** (``include``) hebt nur einen Ausschluss als Einzel-ID auf. Ein
  Ausschlussmuster ändert man in Home Assistant.
* **Muster übernehmen** (``add_patterns``) nur für Kandidaten der aktuellen Ansicht
  (selection_patterns.py), damit hier kein beliebiges Muster wie ``*`` durchgeht;
  **Muster entfernen** (``remove_patterns``) nur für Muster des Assistenten.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from . import selection_overview, selection_patterns, selection_search, selection_suggest
from .i18n import tr
from .selection_topics import topic_label
from .selection_ui_routes import CONFIRM_LIST, Pages, _app_root, pick_rows, view_result, view_url
from .storage.index import Index


def make_change_router(get_index: Callable[[], Index], templates: Jinja2Templates, data_dir: Path, tz) -> APIRouter:
    router = APIRouter()
    pages = Pages(get_index, templates, data_dir, tz)
    error, confirm = pages.error, pages.confirm

    def names(rows: dict[str, dict[str, Any]], ids: list[str]) -> list[str]:
        return [rows[eid].get("name") or eid for eid in ids[:CONFIRM_LIST]]

    # ---- Ausschließen / Wieder aufnehmen ---------------------------------------------------

    @router.post("/selection/exclude", response_class=HTMLResponse)
    def exclude(
        request: Request,
        ids: list[str] = Form(default=[]),
        one: str = Form(""),
        confirmed: str = Form(""),
        confirm_manual: str = Form(""),
    ) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = pages.ready_catalog(phase2=True)
        if catalog is None:
            return error(request, problem or "", back)
        rows = {row["entity_id"]: row for row in catalog["entities"]}
        requested = [one] if one else list(dict.fromkeys(ids))
        picked, skipped, problem = pick_rows(requested, rows, selection_overview.excludable, single=bool(one))
        if problem == "":
            return error(request, tr("{entity_id} ist nicht ausgewählt oder schon ausgeschlossen.", entity_id=one[:80]), back)
        if problem:
            return error(request, problem, back)
        managed = (catalog.get("label") or {}).get("name")
        manual = [eid for eid in picked if rows[eid].get("origin") == "manual"]
        held = [eid for eid in picked if selection_overview.held_by_ha(rows[eid], managed)]
        context = {
            "kind": "exclude", "ids": picked, "one": one, "back": back, "skipped": skipped,
            "names": names(rows, picked), "more": max(0, len(picked) - CONFIRM_LIST),
            "manual_count": len(manual), "manual_names": names(rows, manual), "held_count": len(held),
        }
        if confirmed != "1":
            return confirm(request, context)
        if manual and confirm_manual != "1":
            return confirm(request, {**context, "manual_missing": True}, status=400)
        return pages.create(
            request, back, source="manual_ui", action="exclude", entity_ids=picked, entry_id=catalog.get("entry_id"),
        )

    @router.post("/selection/include", response_class=HTMLResponse)
    def include(
        request: Request,
        ids: list[str] = Form(default=[]),
        one: str = Form(""),
        confirmed: str = Form(""),
    ) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = pages.ready_catalog(phase2=True)
        if catalog is None:
            return error(request, problem or "", back)
        rows = {row["entity_id"]: row for row in catalog["entities"]}
        requested = [one] if one else list(dict.fromkeys(ids))
        picked, skipped, problem = pick_rows(requested, rows, selection_overview.includable, single=bool(one))
        if problem == "":
            patterns = selection_overview.excluded_patterns(rows[one])
            if patterns:
                message = tr("Der Ausschluss von {entity_id} kommt vom Muster „{pattern}“. Das lässt sich nur in Home Assistant ändern (Zeitarchiv-Integration, Optionen).", entity_id=one[:80], pattern=patterns[0])
            else:
                message = tr("{entity_id} ist nicht als Einzel-ID ausgeschlossen.", entity_id=one[:80])
            return error(request, message, back)
        if problem:
            return error(request, problem, back)
        blocked = [eid for eid in picked if selection_overview.excluded_patterns(rows[eid])]
        context = {
            "kind": "include", "ids": picked, "one": one, "back": back, "skipped": skipped,
            "names": names(rows, picked), "more": max(0, len(picked) - CONFIRM_LIST), "blocked_count": len(blocked),
        }
        if confirmed != "1":
            return confirm(request, context)
        return pages.create(
            request, back, source="manual_ui", action="include", entity_ids=picked, entry_id=catalog.get("entry_id"),
        )

    # ---- Muster ---------------------------------------------------------------------------

    @router.post("/selection/patterns", response_class=HTMLResponse)
    def add_pattern(
        request: Request,
        pattern: str = Form(...),
        topic: str = Form(...),
        q: str = Form(""),
        confirmed: str = Form(""),
    ) -> Response:
        is_search = topic == selection_search.SEARCH_TOPIC
        query = selection_search.clean_query(q)
        back = view_url(request, topic, query)
        if not is_search and topic not in selection_suggest.TOPICS:
            return error(request, tr("Unbekanntes Thema."), back)
        catalog, problem = pages.ready_catalog(phase2=True)
        if catalog is None:
            return error(request, problem or "", back)
        archived = pages.archived()
        result = view_result(topic, query, catalog["entities"], archived)
        active = {item["pattern"] for item in catalog.get("patterns") or ()}
        candidate = next((
            item for item in selection_patterns.candidates(
                selection_patterns.view_rows(result), catalog["entities"], archived, active,
            ) if item["pattern"] == pattern
        ), None)
        if candidate is None:
            return error(request, tr("Dieses Muster gehört nicht zu den Vorschlägen dieser Ansicht."), back)
        if candidate["active"]:
            return error(request, tr("Das Muster „{pattern}“ ist schon aktiv.", pattern=pattern), back)
        if confirmed != "1":
            return confirm(request, {
                "kind": "pattern_add", "pattern": pattern, "preview": candidate, "topic": topic, "q": query,
                "topic_text": tr("Suche") if is_search else topic_label(topic), "back": back,
            })
        return pages.create(
            request, back, source="assistant", action="add_patterns", patterns=[pattern],
            topic=None if is_search else topic, entity_ids=[], entry_id=catalog.get("entry_id"),
        )

    @router.post("/selection/patterns/remove", response_class=HTMLResponse)
    def remove_pattern(request: Request, pattern: str = Form(...), confirmed: str = Form("")) -> Response:
        back = f"{_app_root(request)}/selection/overview"
        catalog, problem = pages.ready_catalog(phase2=True)
        if catalog is None:
            return error(request, problem or "", back)
        item = next((entry for entry in catalog.get("patterns") or () if entry["pattern"] == pattern), None)
        if item is None:
            return error(request, tr("Dieses Muster steht nicht (mehr) in der Auswahl."), back)
        if item["origin"] != "assistant":
            return error(request, tr("Das Muster „{pattern}“ wurde in Home Assistant eingetragen. Ändern lässt es sich nur dort (Zeitarchiv-Integration, Optionen).", pattern=pattern), back)
        if confirmed != "1":
            return confirm(request, {"kind": "pattern_remove", "pattern": pattern, "item": item, "back": back})
        return pages.create(
            request, back, source="manual_ui", action="remove_patterns", patterns=[pattern], entity_ids=[],
            topic=item.get("topic"), entry_id=catalog.get("entry_id"),
        )

    return router
