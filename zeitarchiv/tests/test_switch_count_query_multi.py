"""/api/query-multi für Schalter mit Einschaltungen (ROADMAP 1.74, Schritt 5): ausdrückliche Wahl, Standard aus dem
Anzeigemodus der Entität, Rohwerte. Aufbau wie test_entity_stats_endpoint.py: echter Index, Werte über
hotbuffer.append(), feste Uhr, Router in einer eigenen FastAPI-App."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app import api_routes
from app.storage import hotbuffer
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index
from app.storage.ingestion import IngestionService

TZ = ZoneInfo("Europe/Berlin")
NOW = _dt.datetime(2024, 3, 15, 12, 0, 0, tzinfo=TZ)
ENTITY = "binary_sensor.defrost"


def _ts(hour: float) -> float:
    return _dt.datetime(2024, 3, 15, tzinfo=TZ).timestamp() + hour * 3600


class _FixedClock(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW.astimezone(tz) if tz else NOW


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(ENTITY, "binary_sensor", None, None, friendly_name="Abtauvorgänge")
    # 01:00 an bis 01:05, 01:30 bis 01:40, 04:00 bis 04:04: zwei Einschaltungen um eins, eine um vier.
    for hours, value in ((1, 1.0), (1 + 5 / 60, 0.0), (1.5, 1.0), (1.5 + 10 / 60, 0.0), (4, 1.0), (4 + 4 / 60, 0.0)):
        hotbuffer.append(tmp_path, ENTITY, _ts(hours), value, TZ)
        index.record_write(ENTITY, _ts(hours))
    coordinator = StorageCoordinator()
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path, index=index, tz=TZ, coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator), api_token=lambda: "test-token",
        app_version="0.0.0-test", collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    monkeypatch.setattr(api_routes, "datetime", _FixedClock)
    with TestClient(app) as client:
        client.index = index  # type: ignore[attr-defined]
        yield client
    index.close()


def _query(client, extra: str = "") -> list[dict]:
    return client.get(f"/api/query-multi?entity_ids={ENTITY}&range=day{extra}").json()["series"]


def test_without_a_choice_the_series_stays_the_duration(setup) -> None:
    (series,) = _query(setup)
    assert (series["aggregation_type"], series["default_metric"]) == ("switch", "duration")
    assert [p["value"] for p in series["points"]] == [900.0, 240.0]
    assert [p["count"] for p in series["points"]] == [2, 1]  # die Zahl steht auch bei der Dauer in den Punkten


def test_count_comes_as_a_counter_series_with_a_unit(setup) -> None:
    (series,) = _query(setup, f"&metrics={ENTITY}:count")
    assert (series["aggregation_type"], series["unit"], series["source_type"]) == ("counter", "Einschaltungen", "switch")
    assert [p["value"] for p in series["points"]] == [2, 1]


def test_both_adds_the_count_as_a_second_series(setup) -> None:
    duration, count = _query(setup, f"&metrics={ENTITY}:both")
    assert (duration["entity_id"], duration["aggregation_type"]) == (ENTITY, "switch")
    assert (count["entity_id"], count["base_entity_id"], count["chart_type"]) == (ENTITY + "#count", ENTITY, "line")
    assert count["friendly_name"] == "Abtauvorgänge · Anzahl"


def test_the_display_mode_of_the_entity_is_the_default(setup) -> None:
    setup.index.set_config(ENTITY, display_mode="count")
    (series,) = _query(setup)
    assert (series["aggregation_type"], series["default_metric"]) == ("counter", "count")


def test_an_explicit_duration_overrides_the_default_of_the_entity(setup) -> None:
    setup.index.set_config(ENTITY, display_mode="both")
    assert len(_query(setup)) == 2
    (series,) = _query(setup, f"&metrics={ENTITY}:duration")
    assert series["aggregation_type"] == "switch" and series["default_metric"] == "both"


def test_raw_values_are_never_counted(setup) -> None:
    setup.index.set_config(ENTITY, display_mode="count")
    (series,) = _query(setup, "&raw=true")
    assert series["aggregation_type"] == "switch"
