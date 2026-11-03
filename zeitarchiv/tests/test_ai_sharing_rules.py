"""Code-Review Phase 8: Die Freigabe des Besitzers gilt in jedem KI-Werkzeug, und Konten ohne Administratorrechte
fragen keine einzelnen Entitäten ab (wie bei der Karte)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app import ai_settings, ai_tools, ai_usage, api_routes, logging_setup
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index
from app.storage.ingestion import IngestionService

TZ = ZoneInfo("Europe/Berlin")
AUTH = {"Authorization": "Bearer test-token"}


class _Energy:
    """Ein Energiedashboard mit zwei Erzeugern, zwei Verbrauchern, einer Gruppe und der Grundlast."""

    def compute_flow(self, config, range_key, offset, read_cache=None, **_):
        return {
            "kpi": {"erzeugung": 10.0, "verbrauch": 20.0},
            "green_ratio": 0.5, "quality": {"plausible": True},
            "window_start_ts": 1_700_000_000.0, "window_end_ts": 1_700_086_400.0,
            "erzeuger_breakdown": [
                {"name": "Dach-PV", "value": 9.0, "entity_id": "sensor.pv"},
                {"name": "Balkonkraftwerk", "value": 1.0, "entity_id": "sensor.bkw"},
            ],
            "verbraucher_breakdown": [
                {"name": "Sauna", "value": 6.0, "level": 0, "kosten": 1.5, "entity_id": "sensor.sauna"},
                {"name": "Wallbox", "value": 4.0, "level": 0, "kosten": 1.0, "entity_id": "sensor.wallbox"},
                {"name": "Kühlschrank", "value": 3.0, "level": 0, "kosten": 0.75, "entity_id": "sensor.kuehl"},
                {"name": "Haushalt", "value": 5.0, "level": 0, "kosten": 1.25, "kind": "group"},
                {"name": "Grundlast", "value": 2.0, "level": 0},
            ],
        }

    def compute_period_comparison(self, config, range_key, offset, current, read_cache):
        return {}, {}, {}


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    from app import energiedashboard_routes as energy

    monkeypatch.setattr(energy, "_is_configured", lambda config: True)
    index = Index(tmp_path / "index.sqlite")
    yield ai_tools.AiToolContext(
        data_dir=tmp_path, index=index, tz=TZ, language="de", energy_service=_Energy(), currency="EUR",
    )
    index.close()


def _scoped(ctx, scope: str, entities: list[str]):
    access = ai_settings.AiAccess(enabled=True, scope=scope, entities=frozenset(entities), exclude_presence=False)
    return dataclasses.replace(ctx, access=access)


def test_unshared_consumers_and_generators_are_summed_into_one_line(ctx) -> None:
    out = ai_tools.get_energy_overview(_scoped(ctx, "include", ["sensor.pv", "sensor.kuehl"]), "day")

    assert out["generators"] == [
        {"name": "Dach-PV", "kwh": 9.0},
        {"name": ai_tools.UNSHARED_LABEL, "kwh": 1.0},
    ]
    names = [row["name"] for row in out["top_consumers"]]
    assert "Sauna" not in names and "Wallbox" not in names
    assert {"name": "Kühlschrank", "kwh": 3.0, "cost": 0.75} in out["top_consumers"]
    # Gruppe und Grundlast tragen keinen Entitätsnamen und bleiben stehen.
    assert {"name": "Haushalt", "kwh": 5.0, "cost": 1.25} in out["top_consumers"]
    assert {"name": "Grundlast", "kwh": 2.0, "cost": None} in out["top_consumers"]
    assert {"name": ai_tools.UNSHARED_LABEL, "kwh": 10.0, "cost": 2.5} in out["top_consumers"]
    # Die Summen bleiben vollständig: das Modell sieht nicht weniger Energie, nur weniger Namen.
    assert out["kpi"]["consumption_kwh"] == 20.0
    assert "sensor." not in repr(out)


def test_an_exclusion_list_hides_just_the_listed_devices(ctx) -> None:
    out = ai_tools.get_energy_overview(_scoped(ctx, "exclude", ["sensor.sauna"]), "day")
    names = [row["name"] for row in out["top_consumers"]]
    assert "Sauna" not in names and "Wallbox" in names and ai_tools.UNSHARED_LABEL in names
    assert [g["name"] for g in out["generators"]] == ["Dach-PV", "Balkonkraftwerk"]


def test_with_everything_shared_nothing_is_collapsed(ctx) -> None:
    out = ai_tools.get_energy_overview(ctx, "day")
    assert [row["name"] for row in out["top_consumers"]] == ["Sauna", "Wallbox", "Kühlschrank", "Haushalt", "Grundlast"]
    assert [g["name"] for g in out["generators"]] == ["Dach-PV", "Balkonkraftwerk"]
    assert ai_tools.UNSHARED_LABEL not in repr(out)


NOTICES = [
    {"id": "backup.job_failed", "severity": "warn", "title": "Backup fehlgeschlagen", "detail": "x"},
    {"id": "entities.outlier_rate_high", "severity": "warn", "title": "Ausreißer", "detail": "Bei „Sauna“ …"},
    {"id": "housekeeping.counter_decreases", "severity": "warn", "title": "Zählerrückgang", "detail": "Bei „Wallbox“ …"},
    {"id": "housekeeping.inactive_entities_30", "severity": "info", "title": "Inaktiv", "detail": "sensor.alt"},
    {"id": "system.storage_reconcile_mismatches", "severity": "info", "title": "Abgleich", "detail": "3 Entitäten"},
]


@pytest.mark.parametrize("scope", ["include", "exclude"])
def test_notices_that_name_entities_stay_with_the_owner_when_the_scope_is_restricted(ctx, scope: str) -> None:
    out = ai_tools.get_notices(dataclasses.replace(_scoped(ctx, scope, ["sensor.pv"]), collect_notices=lambda: NOTICES))
    assert [n["id"] for n in out["notices"]] == ["backup.job_failed", "system.storage_reconcile_mismatches"]
    assert out["healthy"] is False  # aus allen Meldungen, nicht aus den gezeigten


def test_all_notices_are_shown_when_the_scope_is_open(ctx) -> None:
    out = ai_tools.get_notices(dataclasses.replace(ctx, collect_notices=lambda: NOTICES))
    assert len(out["notices"]) == len(NOTICES)


# ---------------------------------------------------------------- Konten ohne Administratorrechte


@pytest.fixture
def client(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.temperatur", "sensor", "measurement", "°C")
    index.set_setting("ai_enabled", "1")
    coordinator = StorageCoordinator()
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path, index=index, tz=TZ, coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator),
        api_token=lambda: "test-token", app_version="0.0.0-test",
        collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    ai_usage.USAGE.reset()
    with TestClient(app) as test_client:
        yield test_client, index
    ai_usage.USAGE.reset()
    logging_setup._RATE_LIMIT_STATE.pop("api_auth_failure", None)
    for key in [k for k in logging_setup._RATE_LIMIT_STATE if k.startswith("ai_tool_rejected:")]:
        logging_setup._RATE_LIMIT_STATE.pop(key, None)
    index.close()


ENTITY_CALLS = {
    "search_entities": {},
    "get_summary": {"entity_ids": ["sensor.temperatur"], "period": "day"},
    "get_series": {"entity_ids": ["sensor.temperatur"], "period": "day"},
    "compare_periods": {"entity_ids": ["sensor.temperatur"], "period": "month"},
}


def test_the_entity_tools_list_matches_the_tools_that_take_entities() -> None:
    assert ai_tools.ENTITY_TOOLS == set(ENTITY_CALLS)
    assert ai_tools.ENTITY_TOOLS <= set(ai_tools.TOOL_TEXTS)


@pytest.mark.parametrize("tool", sorted(ENTITY_CALLS))
def test_a_user_account_cannot_call_the_entity_tools(client, tool: str) -> None:
    test_client, index = client
    response = test_client.post(f"/api/ai/{tool}", json=ENTITY_CALLS[tool], headers={**AUTH, "X-Zeitarchiv-Ai-User": "user"})
    assert response.status_code == 403
    assert response.json()["error"] == "admin_only"
    snapshot = ai_usage.USAGE.snapshot(index, TZ)
    assert snapshot["rejected"].get("admin_only", 0) >= 1 and snapshot["total"] == 0  # zählt nicht als Nutzung


@pytest.mark.parametrize("account", ["admin", None])
def test_administrators_and_calls_without_an_account_are_not_restricted(client, account) -> None:
    test_client, _index = client
    headers = AUTH if account is None else {**AUTH, "X-Zeitarchiv-Ai-User": account}
    assert test_client.post("/api/ai/search_entities", json={}, headers=headers).status_code == 200


def test_a_user_account_can_still_fetch_the_notices(client) -> None:
    test_client, _index = client
    response = test_client.post("/api/ai/get_notices", json={}, headers={**AUTH, "X-Zeitarchiv-Ai-User": "user"})
    assert response.status_code == 200
