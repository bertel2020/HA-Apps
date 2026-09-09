"""Verhaltenstests für /api/health' "demo_mode"-Feld (DEMO_MODUS_REAUTH_PLAN.md)
— bewusst auch im 401-Fall vorhanden: der einzige Endpunkt, den eine
Integration mit einem gerade abgelehnten Token noch erreichen kann, und
damit die Grundlage dafür, dass sie "Ziel ist im Demo-Modus" von "Token
wirklich ungültig" unterscheiden kann, bevor sie einen Reauth auslöst
(siehe custom_components/zeitarchiv/queue_writer.py dort). Aufbau wie
test_notices_route_latest_backup.py: eigener FastAPI-Router statt der
ganzen app.main, echter Index.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo


try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import api_routes
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne fastapi/starlette
    _AVAILABLE = False

import pytest

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")

TZ = ZoneInfo("Europe/Berlin")


def _client(index: "Index", tmp_path: Path, *, demo_mode_active: bool = False) -> "TestClient":
    coordinator = StorageCoordinator()
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path,
        index=index,
        tz=TZ,
        coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator),
        api_token=lambda: "test-token",
        app_version="0.0.0-test",
        collect_notices=lambda: [],
        latest_backup=lambda: None,
        demo_mode_active=demo_mode_active,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    return TestClient(app)


def test_health_reports_demo_mode_off_by_default(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with _client(index, tmp_path) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json()["demo_mode"] is False
    index.close()


def test_health_reports_demo_mode_when_active(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with _client(index, tmp_path, demo_mode_active=True) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json()["demo_mode"] is True
    index.close()


def test_health_reports_demo_mode_even_with_a_rejected_token(tmp_path: Path) -> None:
    """Der eigentliche Zweck des Felds: ohne gültigen Token lässt sich sonst
    nirgends unterscheiden, ob ein 401 an einem wirklich falschen Token
    liegt oder daran, dass das Ziel gerade im Demo-Modus läuft."""
    index = Index(tmp_path / "index.sqlite")
    with _client(index, tmp_path, demo_mode_active=True) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert response.json()["detail"]["demo_mode"] is True
    index.close()


def test_health_reports_demo_mode_false_with_a_rejected_token_outside_demo_mode(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with _client(index, tmp_path, demo_mode_active=False) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert response.json()["detail"]["demo_mode"] is False
    index.close()
