"""Verhaltenstests für /api/notices' "latest_backup"-Feld — die Grundlage
für sensor.zeitarchiv_latest_backup in der HA-Integration (siehe
Roadmap 1.4). Aufbau wie test_entity_stats_endpoint.py: eigener FastAPI-
Router statt der ganzen app.main, echter Index.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo


try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import api_routes
    from app.notices import latest_backup_info
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne fastapi/starlette
    _AVAILABLE = False

import pytest

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")

TZ = ZoneInfo("Europe/Berlin")


def _client(index: "Index", tmp_path: Path) -> "TestClient":
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
        latest_backup=lambda: latest_backup_info(index),
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    return TestClient(app)


def test_notices_route_reports_null_latest_backup_without_any_success(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with _client(index, tmp_path) as client:
        body = client.get("/api/notices", headers={"Authorization": "Bearer test-token"}).json()
    assert body["latest_backup"] is None
    index.close()


def test_notices_route_reports_last_successful_backup(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    job_id = index.create_backup_job("manual")
    index.update_backup_job(job_id, status="success", finished_at=200.0, filename="x.zip", size_bytes=99)
    with _client(index, tmp_path) as client:
        body = client.get("/api/notices", headers={"Authorization": "Bearer test-token"}).json()
    assert body["latest_backup"] == {"filename": "x.zip", "size_bytes": 99, "finished_at": 200.0}
    index.close()
