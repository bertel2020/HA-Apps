"""Regressionstests für Ingest-Korrelation, Trace-Ergebnis und Capture-TTL."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api_routes import (  # noqa: E402
    ApiDependencies,
    ApiState,
    EventIn,
    WriteRequest,
    create_api_router,
    expire_entity_trace,
    expire_write_capture,
    schedule_write_capture_expiry,
)
from app.logging_setup import configure_logging, local_log_lines  # noqa: E402
from app.storage.coordinator import StorageCoordinator  # noqa: E402


class _FakeIngestion:
    def __init__(self, result: str = "written") -> None:
        self.result = result

    def ingest(self, _event) -> str:
        return self.result


class _FakeIndex:
    """Minimaler get_setting/set_setting-Stub für ha_integration.record_seen()
    — der Endpunkt wird hier direkt als Python-Funktion aufgerufen statt über
    FastAPI, wodurch x_zeitarchiv_integration_version nicht None sondern das
    unaufgelöste Header(default=None)-Sentinel ist (truthy) und check_auth()
    daher immer record_seen(deps.index, ...) auslöst."""

    def __init__(self) -> None:
        self._settings: dict[str, str] = {}

    def get_setting(self, key: str) -> str | None:
        return self._settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self._settings[key] = value


def _write_endpoint(state: ApiState, result: str = "written"):
    router = create_api_router(
        ApiDependencies(
            data_dir=Path("/tmp"),
            index=_FakeIndex(),
            tz=ZoneInfo("Europe/Berlin"),
            coordinator=StorageCoordinator(),
            ingestion=_FakeIngestion(result),
            api_token=lambda: "test-token",
            app_version="test",
            collect_notices=lambda: [],
        ),
        state,
    )
    raw = next(route.endpoint for route in router.routes if route.path == "/api/write")
    # x_zeitarchiv_integration_version fehlt beim Direktaufruf (kein
    # FastAPI-Request-Parsing hier) sonst als unaufgelöstes
    # Header(default=None)-Sentinel statt echtem None — siehe _FakeIndex.
    return lambda payload, request, authorization: raw(
        payload, request, authorization, x_zeitarchiv_integration_version=None,
    )


def _request(request_id: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/api/write", "headers": []})
    request.state.request_id = request_id
    return request


def _payload() -> WriteRequest:
    return WriteRequest(events=[EventIn(
        event_id="event-observe-123456",
        entity_id="sensor.temp",
        domain="sensor",
        ts=1_700_000_000.0,
        value=21.5,
        unit="°C",
    )])


def test_entity_trace_logs_final_ingest_result_and_correlation() -> None:
    configure_logging("debug", "off")
    state = ApiState()
    state.entity_trace.update(
        entity_id="sensor.temp", started_at=time.time(), expires_at=time.time() + 60
    )
    result = _write_endpoint(state, "filtered")(
        _payload(), _request("request-observe"), "Bearer test-token"
    )
    assert result["filtered"] == 1
    lines = local_log_lines(search="request-observe", limit=50)
    assert any("event=entity_trace" in line and "result=filtered" in line for line in lines)
    assert any("event=ingest_batch_completed" in line and "duration_ms=" in line for line in lines)


def test_expired_capture_and_trace_are_fully_cleared() -> None:
    capture = {
        "armed": False,
        "captured_at": 1.0,
        "expires_at": time.time() - 1,
        "payload": {"events": []},
    }
    trace = {"entity_id": "sensor.temp", "started_at": 1.0, "expires_at": time.time() - 1}
    assert expire_write_capture(capture)
    assert capture == {"armed": False, "captured_at": None, "expires_at": None, "payload": None}
    assert expire_entity_trace(trace)
    assert trace == {"entity_id": None, "started_at": None, "expires_at": None}


def test_armed_capture_expires_instead_of_recording_late_request() -> None:
    state = ApiState()
    state.write_capture.update(armed=True, expires_at=time.time() - 1)
    _write_endpoint(state)(_payload(), _request("expired-capture"), "Bearer test-token")
    assert state.write_capture["payload"] is None
    assert state.write_capture["armed"] is False


def test_capture_is_deleted_by_timer_without_followup_request() -> None:
    state = ApiState()
    state.write_capture.update(
        armed=False,
        captured_at=time.time(),
        expires_at=time.time() + 0.03,
        payload={"events": [{"entity_id": "sensor.sensitive"}]},
    )
    schedule_write_capture_expiry(state)
    deadline = time.monotonic() + 0.5
    while state.write_capture["payload"] is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert state.write_capture == {
        "armed": False,
        "captured_at": None,
        "expires_at": None,
        "payload": None,
    }


def test_auth_failure_is_correlated_without_logging_token() -> None:
    configure_logging("debug", "off")
    state = ApiState()
    try:
        _write_endpoint(state)(_payload(), _request("bad-auth-request"), "Bearer top-secret")
        raise AssertionError("Ungültiger Token wurde akzeptiert")
    except HTTPException as exc:
        assert exc.status_code == 401
    lines = local_log_lines(search="bad-auth-request", limit=50)
    assert any("event=api_auth_failure" in line for line in lines)
    assert not any("top-secret" in line for line in lines)
