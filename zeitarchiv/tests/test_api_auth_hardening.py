"""Token-Prüfung auf dem öffentlichen Port (Code-Review Phase 3).

Den Authorization-Header kann dort jeder Client frei wählen. Ein ungültiger Wert muss immer zu 401 führen — nie zu
500 — und gezählt werden; der Token kommt vor der Validierung des Bodys; die Request-ID im Log ist die der App.
"""

from __future__ import annotations

import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import api_routes, logging_setup
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")

TZ = ZoneInfo("Europe/Berlin")
GOOD = {"Authorization": "Bearer test-token"}
EVENT = {"event_id": "event-auth-123456", "entity_id": "sensor.temp", "domain": "sensor", "ts": 1_700_000_000.0, "value": 21.5}


@pytest.fixture
def client_state(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    coordinator = StorageCoordinator()
    state = api_routes.ApiState()
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path, index=index, tz=TZ, coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator),
        api_token=lambda: "test-token", app_version="0.0.0-test",
        collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request, call_next):  # wie die Middleware in main.py: die ID vergibt die App
        request.state.request_id = "server-id"
        return await call_next(request)

    app.include_router(api_routes.create_api_router(deps, state))
    logging_setup._RATE_LIMIT_STATE.pop("api_auth_failure", None)
    with TestClient(app) as client:
        yield client, state
    logging_setup._RATE_LIMIT_STATE.pop("api_auth_failure", None)
    index.close()


@pytest.mark.parametrize(
    "path", ["/api/health", "/api/notices", "/api/card/items", "/api/ai/config"],
)
def test_non_ascii_token_is_rejected_with_401_and_counted(client_state, path: str) -> None:
    client, state = client_state
    before = state.connection_stats["auth_failures"]
    response = client.get(path, headers={"Authorization": "Bearer é€".encode("latin-1", "replace")})
    assert response.status_code == 401
    assert state.connection_stats["auth_failures"] == before + 1


def test_non_ascii_token_on_write_is_rejected_with_401(client_state) -> None:
    client, _state = client_state
    response = client.post(
        "/api/write", json={"events": [EVENT]}, headers={"Authorization": "Bearer ü".encode("latin-1")},
    )
    assert response.status_code == 401


def test_correct_token_still_works_and_wrong_ascii_token_is_rejected(client_state) -> None:
    client, state = client_state
    assert client.get("/api/health", headers=GOOD).status_code == 200
    assert client.get("/api/health", headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert state.connection_stats["auth_failures"] == 1


def test_write_checks_the_token_before_the_body_schema(client_state) -> None:
    """Ein Client ohne gültigen Token bekommt 401, nicht eine 422 mit den Feldern des Schemas."""
    client, state = client_state
    invalid_schema = client.post("/api/write", json={"events": [{"nope": 1}]})
    assert invalid_schema.status_code == 401
    assert "events" not in invalid_schema.text
    assert client.post("/api/write", json={"events": [EVENT]}).status_code == 401
    assert state.connection_stats["auth_failures"] == 2


def test_write_with_valid_token_still_validates_and_stores(client_state) -> None:
    client, _state = client_state
    assert client.post("/api/write", json={"events": [{"nope": 1}]}, headers=GOOD).status_code == 422
    ok = client.post("/api/write", json={"events": [EVENT]}, headers=GOOD)
    assert ok.status_code == 200
    assert ok.json()["written"] == 1


def test_card_routes_log_the_request_id_of_the_app_not_the_one_the_client_sends(
    client_state, caplog: pytest.LogCaptureFixture
) -> None:
    client, _state = client_state
    with caplog.at_level(logging.WARNING):
        response = client.get(
            "/api/card/items", headers={"Authorization": "Bearer falsch", "X-Request-ID": "forged-by-client"}
        )
    assert response.status_code == 401
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "request_id=server-id" in text
    assert "forged-by-client" not in text
