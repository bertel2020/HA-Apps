"""KI-Lesezugriff: POST /api/ai/<tool> (ROADMAP 1.31, Durchstich).

Eigenes Modul, aber von api_routes.py eingehängt: Die Authentifizierung
(check_auth) ist dort eine Closure über Token und Fehlerzähler und wird
hereingereicht, statt dupliziert zu werden.

Ein-/Ausschalten geschieht im Durchstich über die Einstellung ``ai_enabled``
("1" = an). Ist sie aus, antworten alle Routen mit 403 — nach der
Token-Prüfung, damit ein falscher Token weiter 401 bekommt. Eine
Einstellungsseite folgt mit V1.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ai_tools
from .i18n import active_language
from .route_support import storage_locked
from .storage.coordinator import StorageCoordinator
from .storage.index import Index

logger = logging.getLogger(__name__)

SETTING_ENABLED = "ai_enabled"


class SearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    type: str | None = Field(default=None, max_length=20)
    unit: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=ai_tools.DEFAULT_SEARCH_LIMIT, ge=1, le=ai_tools.MAX_SEARCH_LIMIT)


class SummaryRequest(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=ai_tools.MAX_SUMMARY_ENTITIES)
    period: Literal["hour", "day", "week", "month", "year", "decade"]
    date: str | None = Field(default=None, max_length=10)
    rolling: bool = False


@dataclass(frozen=True)
class AiDependencies:
    data_dir: Path
    index: Index
    tz: ZoneInfo
    coordinator: StorageCoordinator
    check_auth: Callable[[str | None, str, str | None], None]


def create_ai_router(deps: AiDependencies) -> APIRouter:
    router = APIRouter()

    def context() -> ai_tools.AiToolContext:
        return ai_tools.AiToolContext(
            data_dir=deps.data_dir, index=deps.index, tz=deps.tz, language=active_language(),
        )

    def gate(request: Request, authorization: str | None, integration_version: str | None) -> JSONResponse | None:
        deps.check_auth(authorization, getattr(request.state, "request_id", "-"), integration_version)
        if deps.index.get_setting(SETTING_ENABLED, "0") != "1":
            return JSONResponse(
                {"error": "ai_disabled", "message": "AI access is switched off in Zeitarchiv."},
                status_code=403,
            )
        return None

    def run(tool: str, entity_count: int, call: Callable[[], dict]) -> JSONResponse:
        started = time.monotonic()
        try:
            status, body = 200, call()
        except ai_tools.AiToolError as exc:
            status, body = exc.status, {"error": exc.code, "message": exc.message}
        logger.info(
            "KI-Werkzeug aufgerufen · event=ai_tool_call tool=%s status=%d entities=%d duration_ms=%d",
            tool, status, entity_count, (time.monotonic() - started) * 1000,
        )
        return JSONResponse(body, status_code=status)

    @router.post("/api/ai/search_entities")
    def search_entities(
        body: SearchRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        if (denied := gate(request, authorization, x_zeitarchiv_integration_version)) is not None:
            return denied
        return run("search_entities", 0, lambda: ai_tools.search_entities(
            context(), body.query, body.type, body.unit, body.limit,
        ))

    @router.post("/api/ai/get_summary")
    @storage_locked(deps.coordinator, lambda args: args["body"].entity_ids)
    def get_summary(
        body: SummaryRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        if (denied := gate(request, authorization, x_zeitarchiv_integration_version)) is not None:
            return denied
        return run("get_summary", len(body.entity_ids), lambda: ai_tools.get_summary(
            context(), body.entity_ids, body.period, body.date, body.rolling,
        ))

    return router
