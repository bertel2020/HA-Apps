"""POST /api/catalog und die Seite /selection (ROADMAP 1.33)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

try:
    from fastapi import FastAPI, HTTPException
    from starlette.testclient import TestClient

    from app import selection_catalog
    from app.selection_routes import create_catalog_router

    _AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne fastapi/starlette
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")


def _catalog_client(tmp_path: Path) -> TestClient:
    def check_auth(authorization, request_id="-", integration_version=None):
        if authorization != "Bearer geheim":
            raise HTTPException(status_code=401, detail="nein")

    app = FastAPI()
    app.include_router(create_catalog_router(tmp_path, check_auth))
    return TestClient(app)


def test_catalog_requires_the_token(tmp_path: Path) -> None:
    client = _catalog_client(tmp_path)
    assert client.post("/api/catalog", json={"entities": []}).status_code == 401
    assert selection_catalog.load(tmp_path) is None


def test_catalog_is_stored_and_cleaned(tmp_path: Path) -> None:
    client = _catalog_client(tmp_path)
    response = client.post(
        "/api/catalog",
        headers={"Authorization": "Bearer geheim"},
        json={"entities": [{"entity_id": "sensor.a", "name": "A"}, {"entity_id": "Ungültig"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "count": 1}
    assert [row["entity_id"] for row in selection_catalog.load(tmp_path)["entities"]] == ["sensor.a"]


def test_catalog_rejects_a_malformed_body(tmp_path: Path) -> None:
    client = _catalog_client(tmp_path)
    assert client.post("/api/catalog", headers={"Authorization": "Bearer geheim"}, json={"entities": "x"}).status_code == 422


def test_selection_page_explains_a_missing_catalog_and_lists_suggestions(client) -> None:
    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    target = data_dir / selection_catalog.FILENAME
    target.unlink(missing_ok=True)
    try:
        assert "Noch kein Katalog empfangen" in client.get("/selection").text

        selection_catalog.save(data_dir, [
            {"entity_id": "sensor.netz_bezug", "name": "Netzbezug Testanlage", "device_class": "energy",
             "state_class": "total_increasing", "unit": "kWh", "energy_role": "grid_import", "device": "Zähler"},
        ])
        page = client.get("/selection")
        assert page.status_code == 200
        assert "Netzbezug Testanlage" in page.text
        assert "Im Energie-Dashboard als Netzbezug" in page.text
        assert client.get("/selection?topic=gibtesnicht").status_code == 200
    finally:
        target.unlink(missing_ok=True)


def test_health_announces_the_catalog_capability(client) -> None:
    from app.api_routes import API_CAPABILITIES

    assert "catalog" in API_CAPABILITIES
