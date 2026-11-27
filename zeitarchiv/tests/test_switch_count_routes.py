"""Statusanzeige und Nachlauf der Einschaltungs-Anzahl in der Konfiguration (ROADMAP 1.74, Schritt 6)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app.main import templates
from app.storage import rollup
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index
from app.switch_count_routes import SwitchCountDependencies, create_switch_count_router

TZ = ZoneInfo("Europe/Berlin")
ENTITY = "binary_sensor.defrost"


def _ts(d, h) -> float:
    return datetime(2024, 6, d, h, tzinfo=TZ).timestamp()


@pytest.fixture
def setup(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(ENTITY, "binary_sensor", None, None)
    index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")
    folder = tmp_path / "archive" / ENTITY
    folder.mkdir(parents=True)
    pq.write_table(pa.table({"ts": [_ts(3, 8), _ts(3, 9)], "value": [1.0, 0.0]}), folder / "2024-06.parquet")
    for level in ("stunde", "monat"):  # Rollup aus der Zeit vor der Zählung
        target = tmp_path / "rollup" / ENTITY / f"{level}.parquet"
        target.mkdir(parents=True)
        pq.write_table(pa.table({"bucket_start": [_ts(1, 0)], "on_seconds": [3600.0]}), target / "2024-06.parquet")

    def require_entity(entity_id: str):
        entity = index.get_entity(entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="x")
        return entity

    app = FastAPI()
    app.include_router(create_switch_count_router(SwitchCountDependencies(
        data_dir=tmp_path, tz=TZ, coordinator=StorageCoordinator(), templates=templates, require_entity=require_entity,
    )))
    with TestClient(app) as client:
        client.data_dir = tmp_path  # type: ignore[attr-defined]
        yield client
    index.close()


def test_the_status_names_the_months_without_a_count_and_offers_the_rebuild(setup) -> None:
    html = setup.get(f"/entities/{ENTITY}/switch-counts").text
    assert "Für 1 von 1 archivierten Monaten noch nicht erfasst" in html
    assert f"/entities/{ENTITY}/switch-counts/rebuild" in html


def test_the_rebuild_fills_the_month_and_reloads_the_page(setup) -> None:
    response = setup.post(f"/entities/{ENTITY}/switch-counts/rebuild")
    assert response.status_code == 200 and response.headers["HX-Refresh"] == "true"
    assert rollup.switch_count_months(setup.data_dir, ENTITY)[1] == []
    html = setup.get(f"/entities/{ENTITY}/switch-counts").text
    assert "Für alle archivierten Monate erfasst." in html and "rebuild" not in html


def test_only_switches_have_it(setup) -> None:
    assert setup.get("/entities/sensor.temp/switch-counts").status_code == 400
    assert setup.post("/entities/sensor.temp/switch-counts/rebuild").status_code == 400
    assert setup.get("/entities/ghost.one/switch-counts").status_code == 404
