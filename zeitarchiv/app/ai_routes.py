"""KI-Lesezugriff: POST /api/ai/<tool> und GET /api/ai/config (ROADMAP 1.31).

Eigenes Modul, aber von api_routes.py eingehängt: Die Authentifizierung
(check_auth) ist dort eine Closure über Token und Fehlerzähler und wird
hereingereicht, statt dupliziert zu werden.

Jede Werkzeug-Route prüft zuerst den Token (401), dann den Schalter
"KI-Zugriff" (403, siehe ai_settings), dann die Last (429). /api/ai/config
ist bewusst auch bei ausgeschaltetem Zugriff erreichbar: Die Integration muss
erfahren können, dass er aus ist, um ihre API abzumelden.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ai_settings, ai_tools
from .i18n import active_language
from .route_support import storage_locked
from .storage.coordinator import StorageCoordinator
from .storage.index import Index

logger = logging.getLogger(__name__)

#: Modelle feuern Tool-Aufrufe in Serie. Zwei gleichzeitig und etwa eine pro
#: Sekunde im Schnitt reichen jedem echten Gespräch; /api/write hat Vorrang und
#: wird nie angehalten, KI-Anfragen laufen nie über exclusive().
MAX_CONCURRENT_REQUESTS = 2
MAX_REQUESTS_PER_MINUTE = 60

Period = Literal["hour", "day", "week", "month", "year", "decade"]


class SearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    type: str | None = Field(default=None, max_length=20)
    unit: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=ai_tools.DEFAULT_SEARCH_LIMIT, ge=1, le=ai_tools.MAX_SEARCH_LIMIT)


class SummaryRequest(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=ai_tools.MAX_SUMMARY_ENTITIES)
    period: Period
    date: str | None = Field(default=None, max_length=10)
    rolling: bool = False


class SeriesRequest(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=ai_tools.MAX_SERIES_ENTITIES)
    period: Period
    date: str | None = Field(default=None, max_length=10)
    rolling: bool = False
    metric: str | None = Field(default=None, max_length=10)


class CompareRequest(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=ai_tools.MAX_SUMMARY_ENTITIES)
    period: Period
    date: str | None = Field(default=None, max_length=10)
    compare_to: str = Field(default="previous_year", max_length=16)
    same_elapsed: bool | None = None


class EnergyRequest(BaseModel):
    period: Period
    date: str | None = Field(default=None, max_length=10)


class NoticesRequest(BaseModel):
    pass


class RequestLimiter:
    """Begrenzt gleichzeitige und je Minute zugelassene KI-Anfragen."""

    def __init__(self, concurrent: int = MAX_CONCURRENT_REQUESTS, per_minute: int = MAX_REQUESTS_PER_MINUTE) -> None:
        self._concurrent = concurrent
        self._per_minute = per_minute
        self._lock = threading.Lock()
        self._running = 0
        self._started: deque[float] = deque()

    def acquire(self) -> str | None:
        """None = zugelassen, sonst der Grund für die Ablehnung."""
        now = time.monotonic()
        with self._lock:
            while self._started and now - self._started[0] > 60:
                self._started.popleft()
            if self._running >= self._concurrent:
                return "busy"
            if len(self._started) >= self._per_minute:
                return "rate_limited"
            self._running += 1
            self._started.append(now)
        return None

    def release(self) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)


@dataclass(frozen=True)
class AiDependencies:
    data_dir: Path
    index: Index
    tz: ZoneInfo
    coordinator: StorageCoordinator
    check_auth: Callable[[str | None, str, str | None], None]
    collect_notices: Callable[[], list[dict]]
    latest_backup: Callable[[], dict | None]


def create_ai_router(deps: AiDependencies) -> APIRouter:
    router = APIRouter()
    limiter = RequestLimiter()

    def energy_service():
        # Der Dienst braucht für compute_flow() weder Templates noch Seitenkontext;
        # ihn hier zu bauen erspart, ihn aus main.py durch die Router zu reichen
        # (main.py steht an der Zeilengrenze, siehe test_route_modules.py).
        from . import energiedashboard_routes as energy  # noqa: PLC0415

        return energy.EnergieDashboardService(energy.EnergieDashboardDependencies(
            data_dir=deps.data_dir, index=deps.index, tz=deps.tz, templates=None, app_root_context=None,
        ))

    def context(access: ai_settings.AiAccess) -> ai_tools.AiToolContext:
        from . import currency  # noqa: PLC0415

        return ai_tools.AiToolContext(
            data_dir=deps.data_dir, index=deps.index, tz=deps.tz, language=active_language(), access=access,
            deadline=time.monotonic() + ai_tools.REQUEST_TIMEOUT_SECONDS,
            collect_notices=deps.collect_notices, latest_backup=deps.latest_backup,
            energy_service=energy_service(), currency=currency._currency_for(deps.index)[1],  # noqa: SLF001
        )

    def authenticate(request: Request, authorization: str | None, integration_version: str | None) -> None:
        deps.check_auth(authorization, getattr(request.state, "request_id", "-"), integration_version)

    def error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
        return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)

    def run(
        request: Request, authorization: str | None, integration_version: str | None,
        tool: str, entity_count: int, call: Callable[[ai_tools.AiToolContext], dict],
    ) -> JSONResponse:
        authenticate(request, authorization, integration_version)
        access = ai_settings.load(deps.index)
        if not access.enabled:
            return error(403, "ai_disabled", "AI access is switched off in Zeitarchiv.")
        if (reason := limiter.acquire()) is not None:
            return error(
                429, reason,
                "Too many requests at once; wait a few seconds and ask again, preferably with fewer calls.",
                headers={"Retry-After": "5"},
            )
        started = time.monotonic()
        try:
            status, body = 200, call(context(access))
        except ai_tools.AiToolError as exc:
            status, body = exc.status, {"error": exc.code, "message": exc.message}
        finally:
            limiter.release()
        logger.info(
            "KI-Werkzeug aufgerufen · event=ai_tool_call tool=%s status=%d entities=%d duration_ms=%d",
            tool, status, entity_count, (time.monotonic() - started) * 1000,
        )
        return JSONResponse(body, status_code=status)

    @router.get("/api/ai/config")
    def ai_config(
        request: Request,
        api_id: str | None = Query(default=None, max_length=80),
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Was die Integration zum Anmelden ihrer LLM-API braucht: ob der Zugriff
        an ist, welche Werkzeuge mit welchen Texten, und den API-Prompt. Die
        Texte stehen hier, damit sie ohne neue Integrationsversion besser
        werden können."""
        authenticate(request, authorization, x_zeitarchiv_integration_version)
        ai_settings.remember_api_id(deps.index, api_id)
        access = ai_settings.load(deps.index)
        ctx = context(access)
        return {
            "enabled": access.enabled,
            "config_rev": ai_settings.config_rev(deps.index),
            "energy_configured": ai_tools._energy_configured(ctx),  # noqa: SLF001
            "api_prompt": ai_tools.API_PROMPT,
            "tools": ai_tools.tool_config(ctx) if access.enabled else [],
        }

    @router.post("/api/ai/search_entities")
    def search_entities(
        body: SearchRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        return run(request, authorization, x_zeitarchiv_integration_version, "search_entities", 0,
                   lambda ctx: ai_tools.search_entities(ctx, body.query, body.type, body.unit, body.limit))

    @router.post("/api/ai/get_summary")
    @storage_locked(deps.coordinator, lambda args: args["body"].entity_ids)
    def get_summary(
        body: SummaryRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        return run(request, authorization, x_zeitarchiv_integration_version, "get_summary", len(body.entity_ids),
                   lambda ctx: ai_tools.get_summary(ctx, body.entity_ids, body.period, body.date, body.rolling))

    @router.post("/api/ai/get_series")
    @storage_locked(deps.coordinator, lambda args: args["body"].entity_ids)
    def get_series(
        body: SeriesRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        return run(request, authorization, x_zeitarchiv_integration_version, "get_series", len(body.entity_ids),
                   lambda ctx: ai_tools.get_series(
                       ctx, body.entity_ids, body.period, body.date, body.rolling, body.metric))

    @router.post("/api/ai/compare_periods")
    @storage_locked(deps.coordinator, lambda args: args["body"].entity_ids)
    def compare_periods(
        body: CompareRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        return run(request, authorization, x_zeitarchiv_integration_version, "compare_periods", len(body.entity_ids),
                   lambda ctx: ai_tools.compare_periods(
                       ctx, body.entity_ids, body.period, body.date, body.compare_to, body.same_elapsed))

    @router.post("/api/ai/get_energy_overview")
    def get_energy_overview(
        body: EnergyRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        return run(request, authorization, x_zeitarchiv_integration_version, "get_energy_overview", 0,
                   lambda ctx: ai_tools.get_energy_overview(ctx, body.period, body.date))

    @router.post("/api/ai/get_notices")
    def get_notices(
        body: NoticesRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ):
        return run(request, authorization, x_zeitarchiv_integration_version, "get_notices", 0,
                   lambda ctx: ai_tools.get_notices(ctx))

    return router
