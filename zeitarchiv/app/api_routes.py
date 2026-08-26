"""Externe Zeitarchiv-API: Authentifizierung, Aufnahme und Abfragen."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .formatting import decimals_to_int
from .limits import MAX_MULTI_QUERY_ENTITIES, MAX_WRITE_EVENTS
from .route_support import storage_locked
from .storage import query as query_mod
from .storage.coordinator import StorageCoordinator
from .storage.index import Index
from .storage.ingestion import IngestEvent, IngestionService, legacy_event_id
from .storage.paths import ENTITY_ID_MAX_LENGTH, ENTITY_ID_PATTERN, validate_entity_id


logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("zeitarchiv.trace")

EntityId = Annotated[
    str,
    Field(min_length=3, max_length=ENTITY_ID_MAX_LENGTH, pattern=ENTITY_ID_PATTERN),
]


class EventIn(BaseModel):
    event_id: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    )
    entity_id: EntityId
    domain: str
    ts: float
    value: float
    state_class: str | None = None
    unit: str | None = None
    friendly_name: str | None = None


class WriteRequest(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=MAX_WRITE_EVENTS)


@dataclass
class ApiState:
    """Prozesslokaler Diagnosezustand, den auch die Settings-Routen anzeigen."""

    server_started_at: float = field(default_factory=time.time)
    connection_stats: dict = field(default_factory=lambda: {
        "write_requests_ok": 0,
        "auth_failures": 0,
        "last_auth_failure_ts": None,
    })
    write_capture_lock: threading.Lock = field(default_factory=threading.Lock)
    write_capture: dict = field(default_factory=lambda: {
        "armed": False, "captured_at": None, "payload": None,
    })
    entity_trace_lock: threading.Lock = field(default_factory=threading.Lock)
    entity_trace: dict = field(default_factory=lambda: {
        "entity_id": None, "started_at": None, "expires_at": None,
    })


@dataclass(frozen=True)
class ApiDependencies:
    data_dir: Path
    index: Index
    tz: ZoneInfo
    coordinator: StorageCoordinator
    ingestion: IngestionService
    api_token: Callable[[], str]
    app_version: str


def _validate_entity_id_or_400(entity_id: str) -> None:
    try:
        validate_entity_id(entity_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_api_router(deps: ApiDependencies, state: ApiState) -> APIRouter:
    router = APIRouter()
    locked = lambda getter: storage_locked(deps.coordinator, getter)

    def check_auth(authorization: str | None) -> None:
        expected = f"Bearer {deps.api_token()}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            state.connection_stats["auth_failures"] += 1
            state.connection_stats["last_auth_failure_ts"] = time.time()
            raise HTTPException(status_code=401, detail="Ungültiger oder fehlender API-Token")

    def limited_multi_entity_ids(args: dict) -> list[str]:
        ids = [item.strip() for item in args["entity_ids"].split(",") if item.strip()]
        if len(ids) > MAX_MULTI_QUERY_ENTITIES:
            raise HTTPException(
                status_code=413,
                detail=f"Maximal {MAX_MULTI_QUERY_ENTITIES} Entitäten pro Abfrage",
            )
        return ids

    @router.get("/api/health")
    def health(authorization: str | None = Header(default=None)) -> dict:
        check_auth(authorization)
        return {"status": "ok", "version": deps.app_version}

    @router.post("/api/write")
    def write(payload: WriteRequest, authorization: str | None = Header(default=None)) -> dict:
        check_auth(authorization)
        state.connection_stats["write_requests_ok"] += 1
        with state.write_capture_lock:
            if state.write_capture["armed"]:
                state.write_capture.update(
                    armed=False, captured_at=time.time(), payload=payload.model_dump()
                )

        now = time.time()
        with state.entity_trace_lock:
            trace_entity = state.entity_trace["entity_id"]
            trace_active = bool(trace_entity) and (state.entity_trace["expires_at"] or 0) > now
        if trace_active:
            for event in payload.events:
                if event.entity_id == trace_entity:
                    trace_logger.debug(
                        "Trace %s · ts=%s · value=%s · unit=%s · domain=%s",
                        event.entity_id, event.ts, event.value, event.unit or "—", event.domain,
                    )

        counts = {"written": 0, "skipped": 0, "filtered": 0, "duplicate": 0, "recovered": 0}
        for event in payload.events:
            event_data = event.model_dump()
            event_id = event.event_id or legacy_event_id(
                {key: value for key, value in event_data.items() if key != "event_id"}
            )
            result = deps.ingestion.ingest(IngestEvent(event_id=event_id, **{
                key: value for key, value in event_data.items() if key != "event_id"
            }))
            counts[result] += 1
        logger.debug("Schreibbatch verarbeitet · Events=%d · Ergebnis=%s", len(payload.events), counts)
        return counts

    @router.get("/api/query")
    @locked(lambda args: args["entity_id"])
    def api_query(
        entity_id: str,
        range: str = Query("day", alias="range"),
        offset: int = 0,
        continuous: bool = False,
        compare: bool = False,
        compare_mode: str = "previous",
        raw: bool = False,
        chart_type: str | None = None,
    ) -> dict:
        _validate_entity_id_or_400(entity_id)
        if chart_type not in (None, "line", "bar"):
            raise HTTPException(status_code=400, detail="Ungültiger Diagrammtyp")
        now = datetime.now(deps.tz)
        if raw:
            return query_mod.query_raw_series(
                deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                offset=offset, continuous=continuous,
            )
        result = query_mod.query_series(
            deps.data_dir, deps.index, entity_id, range, deps.tz, now,
            offset=offset, continuous=continuous, chart_type=chart_type,
        )
        if compare:
            compare_result = query_mod.query_series(
                deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                offset=offset if compare_mode == "year" else offset - 1,
                continuous=continuous,
                year_over_year=compare_mode == "year",
                chart_type=chart_type,
            )
            result.update(
                compare_points=compare_result["points"],
                compare_window_start=compare_result["window_start"],
                compare_window_end=compare_result["window_end"],
            )
        return result

    @router.get("/api/query-multi")
    @locked(limited_multi_entity_ids)
    def api_query_multi(
        entity_ids: str,
        range: str = Query("day", alias="range"),
        offset: int = 0,
        continuous: bool = False,
        year_over_year: bool = False,
        compare: bool = False,
        compare_mode: str = "previous",
        raw: bool = False,
    ) -> dict:
        now = datetime.now(deps.tz)
        ids = limited_multi_entity_ids({"entity_ids": entity_ids})
        for entity_id in ids:
            _validate_entity_id_or_400(entity_id)
        series = []
        window_start = window_end = period_end = None
        is_current = True
        for entity_id in ids:
            entity = deps.index.get_entity(entity_id)
            if raw:
                result = query_mod.query_raw_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    offset=offset, continuous=continuous,
                )
            else:
                result = query_mod.query_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    offset=offset, continuous=continuous, year_over_year=year_over_year,
                )
            entry = {
                "entity_id": entity_id,
                "friendly_name": (entity["friendly_name"] if entity else None) or entity_id,
                "unit": (entity["unit"] if entity else None) or "",
                "decimals": decimals_to_int(entity["decimals"]) if entity else None,
                "display_mode": (entity["display_mode"] if entity else None) or "onoff",
                "aggregation_type": result["aggregation_type"],
                "chart_type": result["chart_type"],
                "points": result["points"],
            }
            if compare and not raw:
                compare_result = query_mod.query_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    offset=offset if compare_mode == "year" else offset - 1,
                    continuous=continuous,
                    year_over_year=compare_mode == "year",
                )
                entry.update(
                    compare_points=compare_result["points"],
                    compare_window_start=compare_result["window_start"],
                    compare_window_end=compare_result["window_end"],
                )
            series.append(entry)
            if window_start is None:
                window_start, window_end = result["window_start"], result["window_end"]
                period_end, is_current = result["period_end"], result["is_current"]
        return {
            "series": series, "window_start": window_start, "window_end": window_end,
            "period_end": period_end, "is_current": is_current,
        }

    return router
