"""Auswahl-Assistent: Katalog entgegennehmen und Vorschläge zeigen (ROADMAP 1.33).

Zwei Router, weil sie an zwei Stellen hängen: ``create_catalog_router`` nimmt den
Katalog der Integration über den Token-Port entgegen und wird von
``api_routes.create_api_router`` eingehängt (dort liegt ``check_auth``);
``make_router`` liefert die Seite für die Oberfläche (Ingress) und wird in
main.py eingehängt.

Phase 0 ist rein lesend: die Seite zeigt Vorschläge, übernehmen lässt sich noch
nichts.
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

from . import selection_catalog, selection_suggest
from .formatting import format_time, format_timestamp
from .storage.index import Index

logger = logging.getLogger(__name__)


class CatalogRequest(BaseModel):
    entities: list[dict[str, Any]] = Field(max_length=selection_catalog.MAX_ENTITIES)


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
    ) -> dict:
        """Nimmt den Entitäten-Katalog der Integration an und ersetzt den vorigen."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        count = selection_catalog.save(data_dir, body.entities)
        logger.info("Entitäten-Katalog empfangen · event=catalog_received entities=%d", count)
        return {"ok": True, "count": count}

    return router


def make_router(get_index: Callable[[], Index], templates: Jinja2Templates, data_dir: Path, tz) -> APIRouter:
    router = APIRouter()

    @router.get("/selection", response_class=HTMLResponse)
    def selection_page(request: Request, topic: str = "energie") -> HTMLResponse:
        """Vorschläge zu einem Thema, berechnet aus dem zuletzt empfangenen Katalog."""
        if topic not in selection_suggest.TOPICS:
            topic = selection_suggest.TOPICS[0]
        catalog = selection_catalog.load(data_dir)
        result = None
        received = None
        if catalog is not None:
            archived = {entity["entity_id"] for entity in get_index().list_entities()}
            result = selection_suggest.suggest(topic, catalog["entities"], archived)
            received = f"{format_timestamp(catalog['received_ts'], tz)} {format_time(catalog['received_ts'], tz)}"
        return templates.TemplateResponse(
            request, "selection.html", {"topic": topic, "result": result, "received": received}
        )

    return router
