"""Verhaltenstests für /api/ai/* — den KI-Lesezugriff (ROADMAP 1.31, Durchstich).

Aufbau wie test_entity_stats_endpoint.py: echter Index, Werte über
hotbuffer.append(), feste Uhr, Router in einer eigenen FastAPI-App.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import ai_settings, ai_tools, ai_usage, api_routes, logging_setup
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
    # Vortag: 24 Stunden, Zähler +1 je Stunde (endet bei 99, der Tag beginnt bei 100), Temperatur konstant 8 °C.
    for stunde in range(24):
        hotbuffer.append(tmp_path, "sensor.temperatur", _ts(stunde - 24), 8.0, TZ)
        hotbuffer.append(tmp_path, "sensor.zaehler", _ts(stunde - 24), 76.0 + stunde, TZ)

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


@pytest.fixture(autouse=True)
def _fresh_usage():
    ai_usage.USAGE.reset()
    yield
    ai_usage.USAGE.reset()


@pytest.fixture
def ctx(anlage, tmp_path: Path):
    _client, index = anlage
    return ai_tools.AiToolContext(data_dir=tmp_path, index=index, tz=TZ, language="de")


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


# ----------------------------------------------------------- V1: Auswahl


def test_scope_exclude_hides_entities_everywhere(anlage) -> None:
    client, index = anlage
    ai_settings.save(index, enabled=True, scope="exclude", entities=["sensor.zaehler"], exclude_presence=True)
    found = client.post("/api/ai/search_entities", json={}, headers=AUTH).json()
    assert [m["entity_id"] for m in found["matches"]] == ["sensor.temperatur"]
    summary = client.post(
        "/api/ai/get_summary", json={"entity_ids": ["sensor.zaehler"], "period": "day"}, headers=AUTH,
    ).json()
    assert summary["results"][0]["error"] == "entity_not_found"


def test_scope_include_and_presence_toggle(anlage) -> None:
    client, index = anlage
    ai_settings.save(index, enabled=True, scope="include", entities=["sensor.zaehler", "person.anna"], exclude_presence=True)
    ids = [m["entity_id"] for m in client.post("/api/ai/search_entities", json={}, headers=AUTH).json()["matches"]]
    assert ids == ["sensor.zaehler"]
    ai_settings.save(index, enabled=True, scope="include", entities=["sensor.zaehler", "person.anna"], exclude_presence=False)
    ids = [m["entity_id"] for m in client.post("/api/ai/search_entities", json={}, headers=AUTH).json()["matches"]]
    assert ids == ["person.anna", "sensor.zaehler"]


def test_config_endpoint_reports_state_and_remembers_api_id(anlage) -> None:
    client, index = anlage
    off = client.get("/api/ai/config?api_id=zeitarchiv_abc", headers=AUTH).json()
    assert off["enabled"] is False and off["tools"] == []
    rev = off["config_rev"]
    ai_settings.save(index, enabled=True, scope="all", entities=[], exclude_presence=True)
    on = client.get("/api/ai/config", headers=AUTH).json()
    assert on["config_rev"] == rev + 1
    names = [tool["name"] for tool in on["tools"]]
    assert "zeitarchiv_get_energy_overview" not in names and len(names) == 5
    assert on["energy_configured"] is False
    assert on["tools"][0]["title"]["de"].startswith("Zeitarchiv")
    assert ai_settings.known_api_ids(index) == ["zeitarchiv_abc"]
    assert client.get("/api/ai/config", headers={}).status_code == 401


def test_config_rev_travels_with_notices(anlage) -> None:
    client, index = anlage
    before = client.get("/api/notices", headers=AUTH).json()["ai_config_rev"]
    ai_settings.save(index, enabled=True, scope="all", entities=[], exclude_presence=True)
    assert client.get("/api/notices", headers=AUTH).json()["ai_config_rev"] == before + 1


# ----------------------------------------------------------- V1: Werkzeuge


def test_series_rows_and_extremes_by_metric(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post(
        "/api/ai/get_series",
        json={"entity_ids": ["sensor.temperatur"], "period": "day", "metric": "max"},
        headers=AUTH,
    ).json()
    [entry] = body["results"]
    assert entry["columns"] == ["bucket", "max"]
    assert entry["highest"][1] == 23.0 and entry["highest"][0].startswith("2024-03-15 10:00")
    assert entry["lowest"][1] == 10.0
    assert len(entry["rows"]) <= ai_tools.MAX_SERIES_POINTS


def test_series_metric_validation(anlage) -> None:
    client, index = anlage
    _enable(index)
    unknown = client.post(
        "/api/ai/get_series", json={"entity_ids": ["sensor.temperatur"], "period": "day", "metric": "median"},
        headers=AUTH,
    )
    assert unknown.status_code == 400 and unknown.json()["error"] == "invalid_metric"
    wrong_type = client.post(
        "/api/ai/get_series", json={"entity_ids": ["sensor.zaehler"], "period": "day", "metric": "max"},
        headers=AUTH,
    ).json()
    assert wrong_type["results"][0]["error"] == "metric_not_available"
    assert client.post(
        "/api/ai/get_series", json={"entity_ids": [f"sensor.s{i}" for i in range(4)], "period": "day"}, headers=AUTH,
    ).status_code == 422


def test_compare_previous_period_is_cut_to_the_same_elapsed_time(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post(
        "/api/ai/compare_periods",
        json={"entity_ids": ["sensor.temperatur"], "period": "day", "compare_to": "previous_period"},
        headers=AUTH,
    ).json()
    [entry] = body["results"]
    assert body["a"]["period_running"] is True and "same elapsed" in body["basis"]
    assert entry["b"]["value"] == 8.0
    assert entry["delta"] == pytest.approx(entry["a"]["value"] - 8.0, abs=0.001)
    assert entry["delta_pct"] == pytest.approx(entry["delta"] / 8.0 * 100, abs=0.1)


def test_compare_accepts_a_date_and_rejects_nonsense(anlage) -> None:
    client, index = anlage
    _enable(index)
    ok = client.post(
        "/api/ai/compare_periods",
        json={"entity_ids": ["sensor.zaehler"], "period": "day", "compare_to": "2024-03-14"},
        headers=AUTH,
    ).json()
    assert ok["b"]["label"] == "2024-03-14"
    bad = client.post(
        "/api/ai/compare_periods",
        json={"entity_ids": ["sensor.zaehler"], "period": "day", "compare_to": "last tuesday"},
        headers=AUTH,
    )
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_compare_to"
    hour = client.post(
        "/api/ai/compare_periods", json={"entity_ids": ["sensor.zaehler"], "period": "hour"}, headers=AUTH,
    )
    assert hour.json()["error"] == "invalid_period"


def test_energy_overview_needs_a_configured_dashboard(anlage) -> None:
    client, index = anlage
    _enable(index)
    response = client.post("/api/ai/get_energy_overview", json={"period": "month"}, headers=AUTH)
    assert response.status_code == 409 and response.json()["error"] == "energy_not_configured"


class _FakeEnergy:
    def __init__(self, kpi: dict) -> None:
        self.kpi = kpi

    def compute_flow(self, config, range_key, offset, read_cache=None, **_):
        return {
            "kpi": self.kpi, "green_ratio": 0.5, "quality": {"plausible": True},
            "window_start_ts": _ts(0), "window_end_ts": _ts(24),
            "verbraucher_breakdown": [
                {"name": "Wärmepumpe", "value": 12.5, "level": 0, "kosten": 3.1},
                {"name": "Kind", "value": 1.0, "level": 1},
            ],
        }

    def compute_period_comparison(self, config, range_key, offset, current, read_cache):
        return {"verbrauch": {"pct": 4.0, "abs": None}}, {}, {}


def test_energy_overview_shapes_costs_and_never_invents_zero(ctx, monkeypatch) -> None:
    from app import energiedashboard_routes as energy

    monkeypatch.setattr(energy, "_is_configured", lambda config: True)
    ctx.currency = "EUR"
    priced = ai_tools.get_energy_overview(
        _with(ctx, energy_service=_FakeEnergy({"erzeugung": 10.0, "verbrauch": 8.0, "net_cost": 2.5, "netzbezug_cost": 3.0})),
        "day",
    )
    assert priced["kpi"]["consumption_kwh"] == 8.0
    assert priced["costs"]["net_cost"] == 2.5 and priced["costs"]["currency"] == "EUR"
    assert priced["costs"]["note"] is None
    assert priced["top_consumers"] == [{"name": "Wärmepumpe", "kwh": 12.5, "cost": 3.1}]
    assert priced["change_vs_previous"] == {"consumption_kwh": {"pct": 4.0, "abs": None}}

    unpriced = ai_tools.get_energy_overview(_with(ctx, energy_service=_FakeEnergy({"verbrauch": 8.0})), "day")
    assert unpriced["costs"]["net_cost"] is None and "not zero" in unpriced["costs"]["note"]

    with pytest.raises(ai_tools.AiToolError) as week:
        ai_tools.get_energy_overview(_with(ctx, energy_service=_FakeEnergy({})), "week")
    assert week.value.code == "invalid_period"


def _with(ctx, **changes):
    import dataclasses

    return dataclasses.replace(ctx, **changes)


def test_notices_tool_cleans_text_and_reports_health(ctx) -> None:
    notices = [{"id": "system.x", "severity": "warn", "title": "Lücke\nbei Zähler", "detail": "d" * 300}]
    out = ai_tools.get_notices(_with(ctx, collect_notices=lambda: notices, latest_backup=lambda: {"finished_at": _ts(1), "size_bytes": 5}))
    assert out["healthy"] is False
    assert out["notices"][0]["title"] == "Lücke bei Zähler" and len(out["notices"][0]["detail"]) == ai_tools.MAX_TEXT_LENGTH
    assert out["latest_backup"]["size_bytes"] == 5
    assert ai_tools.get_notices(ctx)["healthy"] is True


# --------------------------------------------- V1: Grenzen und Schutz


def test_deadline_aborts_with_a_helpful_timeout(ctx) -> None:
    expired = _with(ctx, deadline=0.0)
    with pytest.raises(ai_tools.AiToolError) as caught:
        ai_tools.get_summary(expired, ["sensor.temperatur"], "day")
    assert caught.value.code == "timeout" and caught.value.status == 504
    assert "shorter period" in caught.value.message


def test_response_is_capped_and_says_so(ctx) -> None:
    for number in range(150):
        ctx.index.get_or_create_entity(
            f"sensor.lang_{number}", "sensor", "measurement", "W", friendly_name="Name " + "x" * 70,
        )
    out = ai_tools.search_entities(ctx, limit=50)
    assert len(json.dumps(out, ensure_ascii=False)) <= ai_tools.MAX_RESPONSE_CHARS
    assert out["truncated"] is True and "shortened" in out["truncated_note"]
    assert 0 < len(out["matches"]) < 50


def test_units_note_when_units_differ(anlage) -> None:
    client, index = anlage
    _enable(index)
    body = client.post(
        "/api/ai/get_summary", json={"entity_ids": ["sensor.temperatur", "sensor.zaehler"], "period": "day"}, headers=AUTH,
    ).json()
    assert "different units" in body["units_note"]


def test_rate_limiter_limits_concurrent_and_per_minute() -> None:
    from app.ai_routes import RequestLimiter

    limiter = RequestLimiter(concurrent=2, per_minute=3)
    assert limiter.acquire() is None and limiter.acquire() is None
    assert limiter.acquire() == "busy"
    limiter.release()
    assert limiter.acquire() is None
    limiter.release()
    limiter.release()
    assert limiter.acquire() == "rate_limited"


def test_busy_gets_429_with_retry_hint(anlage, monkeypatch) -> None:
    client, index = anlage
    _enable(index)
    from app import ai_routes

    monkeypatch.setattr(ai_routes.RequestLimiter, "acquire", lambda self: "busy")
    response = client.post("/api/ai/search_entities", json={}, headers=AUTH)
    assert response.status_code == 429 and response.headers["retry-after"] == "5"
    assert response.json()["error"] == "busy"


# ------------------------------------------------ V1: Einstellungsseite


def test_settings_page_has_the_ai_section_and_nav_entry(client) -> None:
    page = client.get("/settings").text
    assert 'id="ki-zugriff"' in page and 'hx-get="settings/ai"' in page
    assert "settings#ki-zugriff" in page


def test_settings_form_saves_and_unchecked_means_off(client) -> None:
    from app.main import index as app_index

    try:
        form = client.get("/settings/ai")
        assert form.status_code == 200 and 'name="enabled"' in form.text
        saved = client.post("/settings/ai", data={
            "enabled": "1", "scope": "include", "entity": ["sensor.a", "sensor.b"], "exclude_presence": "1",
        })
        assert saved.status_code == 200 and "Gespeichert" in saved.text
        access = ai_settings.load(app_index)
        assert access.enabled and access.scope == "include" and access.entities == {"sensor.a", "sensor.b"}
        assert access.exclude_presence is True
        # Ein nicht angekreuztes Kästchen fehlt im Formular: ausgeschaltet.
        client.post("/settings/ai", data={"scope": "bogus"})
        access = ai_settings.load(app_index)
        assert not access.enabled and access.scope == "all" and not access.exclude_presence
        # Auswahl, die es nicht (mehr) gibt, bleibt sichtbar.
        client.post("/settings/ai", data={"scope": "exclude", "entity": ["sensor.weg"]})
        assert "sensor.weg" in client.get("/settings/ai").text
    finally:
        ai_settings.save(app_index, enabled=False, scope="all", entities=[], exclude_presence=True)


# ------------------------------------------------------------ V1.1


def test_disabled_tool_is_refused_and_left_out_of_the_config(anlage) -> None:
    client, index = anlage
    ai_settings.save(
        index, enabled=True, scope="all", entities=[], exclude_presence=True, tools_disabled=["get_series"],
    )
    refused = client.post(
        "/api/ai/get_series", json={"entity_ids": ["sensor.temperatur"], "period": "day"}, headers=AUTH,
    )
    assert refused.status_code == 403 and refused.json()["error"] == "tool_disabled"
    assert client.post("/api/ai/get_summary", json={"entity_ids": ["sensor.temperatur"], "period": "day"}, headers=AUTH).status_code == 200
    names = [tool["app_tool"] for tool in client.get("/api/ai/config", headers=AUTH).json()["tools"]]
    assert "get_series" not in names and "get_summary" in names


def test_saving_the_switch_alone_keeps_tools_and_notes(anlage) -> None:
    _client, index = anlage
    ai_settings.save(
        index, enabled=True, scope="all", entities=[], exclude_presence=True,
        tools_disabled=["get_notices"], instructions="Die Wärmepumpe heißt WP.",
    )
    access = ai_settings.save(index, enabled=False, scope="all", entities=[], exclude_presence=True)
    assert access.tools_disabled == {"get_notices"} and access.instructions == "Die Wärmepumpe heißt WP."


def test_owner_notes_reach_the_model_in_search_and_prompt(anlage) -> None:
    client, index = anlage
    ai_settings.save(
        index, enabled=True, scope="all", entities=[], exclude_presence=True,
        instructions="Strompreis 30 ct/kWh.\x07",
    )
    found = client.post("/api/ai/search_entities", json={}, headers=AUTH).json()
    assert found["owner_notes"] == "Strompreis 30 ct/kWh."
    prompt = client.get("/api/ai/config", headers=AUTH).json()["api_prompt"]
    assert prompt.startswith(ai_tools.API_PROMPT) and "Strompreis 30 ct/kWh." in prompt
    ai_settings.save(index, enabled=True, scope="all", entities=[], exclude_presence=True, instructions="")
    assert "owner_notes" not in client.post("/api/ai/search_entities", json={}, headers=AUTH).json()


def test_notes_are_cleaned_and_capped() -> None:
    assert ai_settings.clean_instructions("a\r\nb\x00c\u2028d") == "a\nbcd"
    assert len(ai_settings.clean_instructions("x" * 5000)) == ai_settings.MAX_INSTRUCTIONS_LENGTH


def test_usage_counts_calls_and_the_account_kind(anlage) -> None:
    client, index = anlage
    _enable(index)
    for header in ({"X-Zeitarchiv-Ai-User": "admin"}, {"X-Zeitarchiv-Ai-User": "user"}, {}):
        client.post("/api/ai/search_entities", json={}, headers={**AUTH, **header})
    client.post("/api/ai/get_notices", json={}, headers=AUTH)
    snapshot = ai_usage.USAGE.snapshot(index)
    assert snapshot["calls"] == {"search_entities": 3, "get_notices": 1}
    assert snapshot["by_account"] == {"admin": 1, "user": 1, "unknown": 2}
    assert snapshot["last_used"] is not None
    assert index.get_setting("ai_last_used") is not None
    # Abgewiesene Anfragen zählen nicht als Nutzung.
    ai_usage.USAGE.reset()
    index.set_setting("ai_enabled", "0")
    client.post("/api/ai/search_entities", json={}, headers=AUTH)
    assert ai_usage.USAGE.snapshot(index)["total"] == 0


def test_settings_form_saves_tools_and_notes_and_shows_usage(client) -> None:
    from app.main import index as app_index

    try:
        saved = client.post("/settings/ai", data={
            "enabled": "1", "scope": "all", "exclude_presence": "1",
            "tool": ["search_entities", "get_summary"], "instructions": "Wärmepumpe = WP",
        })
        assert saved.status_code == 200 and "Wärmepumpe = WP" in saved.text
        access = ai_settings.load(app_index)
        assert access.tools_disabled == {"get_series", "compare_periods", "get_energy_overview", "get_notices"}
        assert access.instructions == "Wärmepumpe = WP"
        ai_usage.USAGE.record(app_index, "get_summary", "admin")
        page = client.get("/settings/ai").text
        assert "1 Aufruf" in page and "Mit Administratorkonto" in page
    finally:
        ai_settings.save(
            app_index, enabled=False, scope="all", entities=[], exclude_presence=True,
            tools_disabled=[], instructions="",
        )
