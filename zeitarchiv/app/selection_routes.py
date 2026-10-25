"""Auswahl-Assistent: Katalog und Aufträge über den Token-Port, Seiten für die
Oberfläche (ROADMAP 1.33).

Zwei Router, weil sie an zwei Stellen hängen: ``create_catalog_router`` nimmt den
Katalog der Integration entgegen und gibt ihr die Aufträge (``GET
/api/selection/jobs``, ``POST /api/selection/ack``); er wird von
``api_routes.create_api_router`` eingehängt (dort liegt ``check_auth``).
``make_router`` liefert die Seiten für die Oberfläche (Ingress) und wird in
main.py eingehängt; die schreibenden Formulare liegen in selection_ui_routes.py und
(Phase 2: Ausschließen, Wieder aufnehmen, Muster) selection_change_routes.py.

Besitzer: Die App merkt sich im Katalog die ``entry_id`` des zuletzt sendenden
Eintrags (Header ``X-Zeitarchiv-Entry-Id``). Nur er bekommt Aufträge; ein Aufruf
ohne Header (ältere Integration) gilt als Besitzer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import selection_catalog, selection_jobs, selection_patterns, selection_search, selection_suggest
from .formatting import format_time, format_timestamp
from .i18n import N_
from .selection_change_routes import make_change_router
from .selection_topics import TOPIC_INFO
from .selection_ui_routes import make_ui_router, view_result
from .storage.index import Index

logger = logging.getLogger(__name__)


class CatalogRequest(BaseModel):
    entities: list[dict[str, Any]] = Field(max_length=selection_catalog.MAX_ENTITIES)
    # Phase 1 (alle optional, die alte Form bleibt gültig). Bewusst lose typisiert:
    # selection_catalog bereinigt sie Feld für Feld, statt den ganzen Katalog wegen
    # eines kaputten Verlaufseintrags abzulehnen.
    label: Any = None
    log: list[Any] | None = Field(default=None, max_length=1000)
    ledger_count: Any = None
    # Phase 2: die Include-Muster der Integration. Wer das Feld schickt, kennt auch die
    # neuen Aufträge (Ausschließen, Wieder aufnehmen, Muster).
    patterns: list[Any] | None = Field(default=None, max_length=1000)


class AckRequest(BaseModel):
    job_id: str = Field(max_length=64)
    status: str = Field(max_length=20)
    results: dict[str, Any] | None = Field(default=None, max_length=selection_jobs.MAX_IDS)
    message: str | None = Field(default=None, max_length=2000)


_INVALID = object()


def _requester(header: str | None):
    """``entry_id`` des Aufrufers, None ohne Header; ``_INVALID`` bei kaputtem Header
    (bekommt nie Aufträge und darf keine quittieren)."""
    if header is None:
        return None
    return selection_catalog.clean_entry_id(header) or _INVALID


def create_catalog_router(
    data_dir: Path,
    check_auth: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/catalog")
    def post_catalog(
        body: CatalogRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
        x_zeitarchiv_entry_id: str | None = Header(default=None),
    ) -> dict:
        """Nimmt den Entitäten-Katalog der Integration an und ersetzt den vorigen."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        phase1 = "log" in body.model_fields_set or "ledger_count" in body.model_fields_set
        phase2 = "patterns" in body.model_fields_set and body.patterns is not None
        count = selection_catalog.save(
            data_dir, body.entities, label=body.label, log=body.log, ledger_count=body.ledger_count,
            entry_id=x_zeitarchiv_entry_id, phase1=phase1 or phase2, patterns=body.patterns, phase2=phase2,
        )
        logger.info("Entitäten-Katalog empfangen · event=catalog_received entities=%d", count)
        return {"ok": True, "count": count}

    @router.get("/api/selection/jobs")
    def get_jobs(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
        x_zeitarchiv_entry_id: str | None = Header(default=None),
    ) -> dict:
        """Offene Aufträge für die Integration, nur für den Besitzer des Katalogs."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        requester = _requester(x_zeitarchiv_entry_id)
        if requester is _INVALID:
            return {"jobs": []}
        return {"jobs": selection_jobs.pending_for(data_dir, requester, selection_catalog.owner(data_dir))}

    @router.post("/api/selection/ack")
    def post_ack(
        body: AckRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
        x_zeitarchiv_entry_id: str | None = Header(default=None),
    ) -> dict:
        """Quittung je Auftrag. Unbekannte oder abgelaufene Aufträge: ``{"ok": false}``
        mit Status 200, damit die Integration ohne Sonderfall weitermacht."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        requester = _requester(x_zeitarchiv_entry_id)
        ok = requester is not _INVALID and selection_jobs.ack(
            data_dir, body.job_id, body.status, body.results, body.message,
            requester=requester, owner=selection_catalog.owner(data_dir),
        )
        logger.info("Auswahl-Auftrag quittiert · event=selection_ack ok=%s status=%s", ok, body.status)
        return {"ok": ok}

    return router


def topic_tiles(entities: list[dict[str, Any]], archived: set[str]) -> list[dict[str, Any]]:
    """Kacheln mit Trefferzahlen je Thema."""
    tiles = []
    for topic in selection_suggest.TOPICS:
        counts = selection_suggest.suggest(topic, entities, archived)["counts"]
        tiles.append({"topic": topic, "label": TOPIC_INFO[topic]["label"], "counts": counts,
                      "sensitive": TOPIC_INFO[topic]["sensitive"]})
    return tiles


#: Das Pseudo-Thema der Suche: nie „sensibel“, ein Hinweis kommt aus den Treffern.
SEARCH_INFO = {"label": N_("Suche"), "sensitive": False}


def take_unexcludable(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Nimmt die Zeilen, deren Ausschluss sich aufheben lässt, aus den Gruppen heraus:
    sie stehen auf der Seite als eigene Gruppe „Ausschluss aufheben und übernehmen“."""
    taken: list[dict[str, Any]] = []
    for section in ("recommended", "more"):
        for group in result[section]:
            taken.extend(row for row in group["rows"] if row["unexcludable"])
            group["rows"] = [row for row in group["rows"] if not row["unexcludable"]]
        result[section] = [group for group in result[section] if group["rows"]]
    for row in taken:
        row["checked"] = False
    return sorted(taken, key=lambda row: (row["name"].lower(), row["entity_id"]))


def make_router(get_index: Callable[[], Index], templates: Jinja2Templates, data_dir: Path, tz) -> APIRouter:
    router = APIRouter()

    @router.get("/selection", response_class=HTMLResponse)
    def selection_page(request: Request, topic: str = "energie", q: str = "") -> HTMLResponse:
        """Vorschläge zu einem Thema oder zu einer Freitext-Eingabe (``q``), berechnet aus
        dem zuletzt empfangenen Katalog. Trifft die Eingabe ein Synonym, zeigt die Seite
        das Thema („Verstanden als …“), sonst die Treffer der Volltextsuche."""
        query = selection_search.clean_query(q)
        understood = selection_search.understood_topic(query) if query else None
        if understood:
            topic = understood
        elif query:
            topic = selection_search.SEARCH_TOPIC
        elif topic not in selection_suggest.TOPICS:
            topic = selection_suggest.TOPICS[0]
        is_search = topic == selection_search.SEARCH_TOPIC
        catalog = selection_catalog.load(data_dir)
        result = tiles = received = None
        patterns: list[dict[str, Any]] = []
        unexclude: list[dict[str, Any]] = []
        counts = {"new": 0, "archived": 0, "selected": 0, "excluded": 0, "text": 0}
        can_change = bool(catalog and catalog.get("phase1") and catalog.get("phase2"))
        if catalog is not None:
            archived = {entity["entity_id"] for entity in get_index().list_entities()}
            result = view_result(topic, query, catalog["entities"], archived)
            tiles = topic_tiles(catalog["entities"], archived)
            received = f"{format_timestamp(catalog['received_ts'], tz)} {format_time(catalog['received_ts'], tz)}"
            for row in selection_patterns.view_rows(result):
                counts[row["status"]] += 1
                counts["text"] += row["status"] == "new" and row["text"]
            patterns = selection_patterns.candidates(
                selection_patterns.view_rows(result), catalog["entities"], archived,
                {item["pattern"] for item in catalog.get("patterns") or ()},
            )
            if can_change:
                unexclude = take_unexcludable(result)
        return templates.TemplateResponse(request, "selection.html", {
            "topic": topic, "topic_info": SEARCH_INFO if is_search else TOPIC_INFO[topic], "result": result,
            "received": received, "tiles": tiles, "counts": counts, "can_apply": bool(catalog and catalog.get("phase1")),
            "can_change": can_change, "max_ids": selection_jobs.MAX_IDS, "query": query, "is_search": is_search,
            "understood": understood, "patterns": patterns, "unexclude": unexclude,
        })

    router.include_router(make_ui_router(get_index, templates, data_dir, tz))
    router.include_router(make_change_router(get_index, templates, data_dir, tz))
    return router
