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

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .formatting import decimals_to_int, entity_display_name
from .limits import MAX_MULTI_QUERY_ENTITIES, MAX_WRITE_EVENTS
from .logging_setup import log_rate_limited
from .route_support import storage_locked
from .storage import query as query_mod
from .storage.coordinator import StorageCoordinator
from .storage.index import Index
from .storage.ingestion import IngestEvent, IngestionService, legacy_event_id
from .storage.paths import ENTITY_ID_MAX_LENGTH, ENTITY_ID_PATTERN, validate_entity_id


logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("zeitarchiv.trace")

WRITE_CAPTURE_TTL_SECONDS = 60 * 60
INGEST_WARNING_MIN_EVENTS = 20
INGEST_DUPLICATE_WARNING_RATIO = 0.50
INGEST_DISCARDED_WARNING_RATIO = 0.95
INGEST_SLOW_BATCH_MS = 1_000.0

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


class TableQueryColumn(BaseModel):
    range_key: str
    offset: int = 0
    year_over_year: bool = False
    # "Gleicher Zeitpunkt"-Vergleich (Konzept-Erweiterung Vergleichstabelle):
    # nur sinnvoll/wirksam für offset<0 — siehe query.query_series().
    same_elapsed: bool = False


class TableQueryRequest(BaseModel):
    """Alle Zeitfenster einer Vergleichstabelle in einem Storage-Snapshot."""

    entity_ids: list[EntityId] = Field(min_length=1, max_length=MAX_MULTI_QUERY_ENTITIES)
    columns: list[TableQueryColumn] = Field(min_length=1, max_length=100)


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
        "armed": False, "captured_at": None, "expires_at": None, "payload": None,
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


def expire_write_capture(capture: dict, now: float | None = None) -> bool:
    """Entfernt einen abgelaufenen scharfen oder bereits gefüllten Capture."""
    now = time.time() if now is None else now
    expires_at = capture.get("expires_at")
    if expires_at is None or expires_at > now:
        return False
    capture.update(armed=False, captured_at=None, expires_at=None, payload=None)
    return True


def expire_entity_trace(trace: dict, now: float | None = None) -> bool:
    """Setzt einen abgelaufenen Entity-Trace vollständig zurück."""
    now = time.time() if now is None else now
    expires_at = trace.get("expires_at")
    if expires_at is None or expires_at > now:
        return False
    trace.update(entity_id=None, started_at=None, expires_at=None)
    return True


def schedule_write_capture_expiry(state: ApiState) -> None:
    """Löscht einen Capture zum gesetzten Ablaufzeitpunkt auch ohne UI-Poll."""
    with state.write_capture_lock:
        expected_expires_at = state.write_capture.get("expires_at")
    if expected_expires_at is None:
        return

    def expire_if_current() -> None:
        with state.write_capture_lock:
            if state.write_capture.get("expires_at") == expected_expires_at:
                expire_write_capture(state.write_capture, expected_expires_at + 0.001)

    timer = threading.Timer(
        max(0.0, float(expected_expires_at) - time.time()) + 0.01,
        expire_if_current,
    )
    timer.daemon = True
    timer.start()


def _validate_entity_id_or_400(entity_id: str) -> None:
    try:
        validate_entity_id(entity_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _table_aggregates(result: dict) -> dict[str, float | None]:
    """Verdichtet eine Chart-Serie auf die fünf Tabellen-Aggregationen."""
    points = result["points"]
    total = sum((point["value"] or 0.0) for point in points)
    average = total / len(points) if points else 0.0
    minima = [
        point["min"] if point["min"] is not None else point["value"]
        for point in points
        if point["min"] is not None or point["value"] is not None
    ]
    maxima = [
        point["max"] if point["max"] is not None else point["value"]
        for point in points
        if point["max"] is not None or point["value"] is not None
    ]
    automatic = total if result["aggregation_type"] in ("counter", "switch") else average
    return {
        "auto": automatic,
        "avg": average,
        "min": min(minima) if minima else None,
        "max": max(maxima) if maxima else None,
        "sum": total,
    }


def create_api_router(deps: ApiDependencies, state: ApiState) -> APIRouter:
    router = APIRouter()
    locked = lambda getter: storage_locked(deps.coordinator, getter)

    def check_auth(authorization: str | None, request_id: str = "-") -> None:
        expected = f"Bearer {deps.api_token()}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            state.connection_stats["auth_failures"] += 1
            state.connection_stats["last_auth_failure_ts"] = time.time()
            log_rate_limited(
                logger,
                logging.WARNING,
                "api_auth_failure",
                "API-Authentifizierung fehlgeschlagen · event=api_auth_failure request_id=%s gesamt_seit_start=%d",
                request_id,
                state.connection_stats["auth_failures"],
                interval_seconds=300,
            )
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
    def health(request: Request, authorization: str | None = Header(default=None)) -> dict:
        check_auth(authorization, getattr(request.state, "request_id", "-"))
        return {"status": "ok", "version": deps.app_version}

    @router.post("/api/write")
    def write(
        payload: WriteRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        request_id = getattr(request.state, "request_id", "-")
        check_auth(authorization, request_id)
        state.connection_stats["write_requests_ok"] += 1
        now = time.time()
        captured_now = False
        with state.write_capture_lock:
            expire_write_capture(state.write_capture, now)
            if state.write_capture["armed"]:
                state.write_capture.update(
                    armed=False,
                    captured_at=now,
                    expires_at=now + WRITE_CAPTURE_TTL_SECONDS,
                    payload=payload.model_dump(),
                )
                captured_now = True
        if captured_now:
            schedule_write_capture_expiry(state)

        with state.entity_trace_lock:
            expire_entity_trace(state.entity_trace, now)
            trace_entity = state.entity_trace["entity_id"]
            trace_active = bool(trace_entity) and (state.entity_trace["expires_at"] or 0) > now

        counts = {"written": 0, "skipped": 0, "filtered": 0, "duplicate": 0, "recovered": 0}
        started = time.perf_counter()
        try:
            for event in payload.events:
                event_data = event.model_dump()
                event_id = event.event_id or legacy_event_id(
                    {key: value for key, value in event_data.items() if key != "event_id"}
                )
                result = deps.ingestion.ingest(IngestEvent(event_id=event_id, **{
                    key: value for key, value in event_data.items() if key != "event_id"
                }))
                counts[result] += 1
                if trace_active and event.entity_id == trace_entity:
                    trace_logger.debug(
                        "Entity-Trace · event=entity_trace request_id=%s entity_id=%s "
                        "event_id=%s ts=%s time=%s value=%s unit=%s domain=%s result=%s",
                        request_id,
                        event.entity_id,
                        event_id[:12],
                        event.ts,
                        datetime.fromtimestamp(event.ts, deps.tz).isoformat(timespec="milliseconds"),
                        event.value,
                        event.unit or "—",
                        event.domain,
                        result,
                    )
        except Exception:
            logger.exception(
                "Ingest-Batch fehlgeschlagen · event=ingest_batch_failed request_id=%s events=%d",
                request_id,
                len(payload.events),
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        throughput = len(payload.events) / max(duration_ms / 1000, 0.001)
        logger.debug(
            "Schreibbatch verarbeitet · event=ingest_batch_completed request_id=%s "
            "events=%d written=%d skipped=%d filtered=%d duplicate=%d recovered=%d "
            "duration_ms=%.1f events_per_second=%.1f",
            request_id,
            len(payload.events),
            counts["written"],
            counts["skipped"],
            counts["filtered"],
            counts["duplicate"],
            counts["recovered"],
            duration_ms,
            throughput,
        )
        event_count = len(payload.events)
        if duration_ms >= INGEST_SLOW_BATCH_MS:
            log_rate_limited(
                logger,
                logging.WARNING,
                "ingest_slow_batch",
                "Langsamer Ingest-Batch · event=ingest_batch_slow request_id=%s events=%d duration_ms=%.1f",
                request_id,
                event_count,
                duration_ms,
                interval_seconds=300,
            )
        if event_count >= INGEST_WARNING_MIN_EVENTS:
            duplicate_ratio = counts["duplicate"] / event_count
            discarded_ratio = (counts["filtered"] + counts["skipped"]) / event_count
            if duplicate_ratio >= INGEST_DUPLICATE_WARNING_RATIO:
                log_rate_limited(
                    logger,
                    logging.WARNING,
                    "ingest_duplicate_ratio",
                    "Hohe Duplikatquote im Ingest · event=ingest_duplicate_ratio request_id=%s "
                    "events=%d duplicate=%d ratio=%.3f",
                    request_id,
                    event_count,
                    counts["duplicate"],
                    duplicate_ratio,
                    interval_seconds=300,
                )
            if discarded_ratio >= INGEST_DISCARDED_WARNING_RATIO:
                log_rate_limited(
                    logger,
                    logging.WARNING,
                    "ingest_discarded_ratio",
                    "Hohe Filterquote im Ingest · event=ingest_discarded_ratio request_id=%s "
                    "events=%d filtered=%d skipped=%d ratio=%.3f",
                    request_id,
                    event_count,
                    counts["filtered"],
                    counts["skipped"],
                    discarded_ratio,
                    interval_seconds=300,
                )
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
                "friendly_name": entity_display_name(
                    entity_id,
                    entity["friendly_name"] if entity else None,
                    entity["custom_name"] if entity else None,
                ),
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

    @router.post("/api/query-table")
    @locked(lambda args: args["body"].entity_ids)
    def api_query_table(body: TableQueryRequest) -> dict:
        """Lädt eine komplette Vergleichstabelle mit nur einer Sperr-/HTTP-Runde.

        Alle Spalten teilen denselben Zeitpunkt und denselben request-lokalen
        Lese-Cache. Besonders die laufende Monats-CSV jeder Entität wird damit
        nur einmal geparst, auch wenn Tag, Monat und Jahr nebeneinander stehen.
        """
        ids = list(dict.fromkeys(body.entity_ids))
        for entity_id in ids:
            _validate_entity_id_or_400(entity_id)
        for column in body.columns:
            if column.range_key not in query_mod.RANGE_KEYS:
                raise HTTPException(status_code=400, detail="Ungültiger Tabellenzeitraum")

        now = datetime.now(deps.tz)
        read_cache = query_mod.QueryReadCache()
        column_results = []
        for column in body.columns:
            series = []
            window_start = window_end = period_end = None
            is_current = True
            for entity_id in ids:
                entity = deps.index.get_entity(entity_id)
                result = query_mod.query_series(
                    deps.data_dir,
                    deps.index,
                    entity_id,
                    column.range_key,
                    deps.tz,
                    now,
                    offset=column.offset,
                    year_over_year=column.year_over_year,
                    read_cache=read_cache,
                    same_elapsed=column.same_elapsed,
                )
                series.append({
                    "entity_id": entity_id,
                    "friendly_name": entity_display_name(
                        entity_id,
                        entity["friendly_name"] if entity else None,
                        entity["custom_name"] if entity else None,
                    ),
                    "unit": (entity["unit"] if entity else None) or "",
                    "decimals": decimals_to_int(entity["decimals"]) if entity else None,
                    "display_mode": (entity["display_mode"] if entity else None) or "onoff",
                    "aggregation_type": result["aggregation_type"],
                    # Tabellen brauchen nur einen Skalar je Zelle. Die teils
                    # tausenden Chart-Punkte nicht als JSON zum Browser zu
                    # schicken spart Transfer und dortige Reduktion.
                    "aggregates": _table_aggregates(result),
                })
                if window_start is None and "window_start" in result:
                    window_start = result["window_start"]
                    window_end = result["window_end"]
                    period_end = result["period_end"]
                    is_current = result["is_current"]
            column_results.append({
                "series": series,
                "window_start": window_start,
                "window_end": window_end,
                "period_end": period_end,
                "is_current": is_current,
            })
        return {"columns": column_results}

    return router
