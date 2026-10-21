"""Verhaltenstests für /api/ai/* — den KI-Lesezugriff (ROADMAP 1.31, Durchstich).

Aufbau wie test_entity_stats_endpoint.py: echter Index, Werte über
hotbuffer.append(), feste Uhr, Router in einer eigenen FastAPI-App.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import ai_tools, api_routes, logging_setup
    from app.storage import hotbuffer
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne fastapi/starlette
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")

TZ = ZoneInfo("Europe/Berlin")
NOW = _dt.datetime(2024, 3, 15, 12, 0, 0, tzinfo=TZ)
AUTH = {"Authorization": "Bearer test-token"}


def _ts(stunde: float) -> float:
    return _dt.datetime(2024, 3, 15, tzinfo=TZ).timestamp() + stunde * 3600


class _FesteUhr(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW.astimezone(tz) if tz else NOW


@pytest.fixture
def anlage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.temperatur", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.zaehler", "sensor", "total_increasing", "kWh")
    index.get_or_create_entity("person.anna", "person", "measurement", None)
    temperaturen = [11.0, 10.0, 10.5, 12.0, 14.0, 16.0, 18.0, 19.5, 21.0, 22.0, 23.0, 20.0]
    for stunde, wert in enumerate(temperaturen):
        hotbuffer.append(tmp_path, "sensor.temperatur", _ts(stunde), wert, TZ)
        hotbuffer.append(tmp_path, "sensor.zaehler", _ts(stunde), 100.0 + stunde * 2, TZ)

    coordinator = StorageCoordinator()
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path, index=index, tz=TZ, coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator),
        api_token=lambda: "test-token", app_version="0.0.0-test",
        collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    monkeypatch.setattr(ai_tools, "datetime", _FesteUhr)
    with TestClient(app) as client:
        yield client, index
    # Der 401-Test löst "api_auth_failure" aus; log_rate_limited() hielte die
    # Meldung sonst für test_api_observability.py zurück (modulweiter Zustand).
    logging_setup._RATE_LIMIT_STATE.pop("api_auth_failure", None)


def _enable(index: Index) -> None:
    index.set_setting("ai_enabled", "1")


def test_requires_token_before_switch(anlage) -> None:
    client, _index = anlage
    assert client.post("/api/ai/search_entities", json={}).status_code == 401


def test_switched_off_by_default(anlage) -> None:
    client, _index = anlage
    response = client.post("/api/ai/search_entities", json={}, headers=AUTH)
    assert response.status_code == 403
    assert response.json()["error"] == "ai_disabled"


def test_health_announces_capability(anlage) -> None:
    client, _index = anlage
    assert "ai" in client.get("/api/health", headers=AUTH).json()["capabilities"]


def test_search_hides_presence_and_carries_orientation(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post("/api/ai/search_entities", json={}, headers=AUTH).json()
    ids = [m["entity_id"] for m in body["matches"]]
    assert ids == ["sensor.temperatur", "sensor.zaehler"]
    assert "person.anna" not in str(body)
    assert body["archive"]["entities"] == 2
    assert "Zeitarchiv" in body["orientation"]
    assert body["as_of"]["timezone"] == "Europe/Berlin"
    assert body["as_of"]["now"].startswith("2024-03-15T12:00:00")


def test_search_matches_every_word(anlage) -> None:
    client, index = anlage
    _enable(index)
    hit = client.post("/api/ai/search_entities", json={"query": "sensor temp"}, headers=AUTH).json()
    assert [m["entity_id"] for m in hit["matches"]] == ["sensor.temperatur"]
    miss = client.post("/api/ai/search_entities", json={"query": "temp zaehler"}, headers=AUTH).json()
    assert miss["matches"] == []


def test_summary_standard_gives_extremes_with_time(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post(
        "/api/ai/get_summary",
        json={"entity_ids": ["sensor.temperatur"], "period": "day"},
        headers=AUTH,
    ).json()
    assert body["period_label"] == "2024-03-15"
    assert body["period_running"] is True
    [entry] = body["results"]
    values = entry["values"]
    assert values["min"] == 10.0 and values["min_at"].startswith("2024-03-15T01:00")
    assert values["max"] == 23.0 and values["max_at"].startswith("2024-03-15T10:00")
    assert "total" not in values
    assert entry["unit"] == "°C"


def test_summary_counter_gives_total_not_average(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post(
        "/api/ai/get_summary",
        json={"entity_ids": ["sensor.zaehler"], "period": "day"},
        headers=AUTH,
    ).json()
    [entry] = body["results"]
    assert set(entry["values"]) == {"total"}
    assert entry["values"]["total"] == pytest.approx(22.0, abs=2.01)
    assert 0 < entry["coverage"] <= 1


def test_summary_reports_unknown_and_hidden_entities(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post(
        "/api/ai/get_summary",
        json={"entity_ids": ["sensor.gibt_es_nicht", "person.anna"], "period": "day"},
        headers=AUTH,
    ).json()
    assert [r["error"] for r in body["results"]] == ["entity_not_found", "entity_not_found"]
    assert "zeitarchiv_search_entities" in body["results"][0]["message"]


def test_summary_past_month_label_and_date_validation(anlage) -> None:
    client, index = anlage
    _enable(index)
    ok = client.post(
        "/api/ai/get_summary",
        json={"entity_ids": ["sensor.temperatur"], "period": "month", "date": "2024-01-10"},
        headers=AUTH,
    ).json()
    assert ok["period_label"].endswith("2024")
    assert ok["period_running"] is False
    assert ok["start"].startswith("2024-01-01")
    bad = client.post(
        "/api/ai/get_summary",
        json={"entity_ids": ["sensor.temperatur"], "period": "day", "date": "gestern"},
        headers=AUTH,
    )
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_date"


def test_summary_rejects_unknown_period_and_too_many_entities(anlage) -> None:
    client, index = anlage
    _enable(index)
    assert client.post(
        "/api/ai/get_summary", json={"entity_ids": ["sensor.zaehler"], "period": "fortnight"}, headers=AUTH,
    ).status_code == 422
    ids = [f"sensor.s{i}" for i in range(ai_tools.MAX_SUMMARY_ENTITIES + 1)]
    assert client.post(
        "/api/ai/get_summary", json={"entity_ids": ids, "period": "day"}, headers=AUTH,
    ).status_code == 422


def test_text_is_cleaned_of_control_characters_and_length(anlage) -> None:
    assert ai_tools.clean_text("a\nb\x07c") == "a b c"
    assert len(ai_tools.clean_text("x" * 500)) == ai_tools.MAX_TEXT_LENGTH
