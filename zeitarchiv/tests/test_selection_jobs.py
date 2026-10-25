"""Aufträge des Auswahl-Assistenten: Speicher und Token-Routen (ROADMAP 1.33, Phase 1)."""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import selection_catalog, selection_jobs

try:
    from fastapi import FastAPI, HTTPException
    from starlette.testclient import TestClient

    from app import api_routes
    from app.selection_routes import create_catalog_router
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne fastapi/starlette
    _AVAILABLE = False

needs_fastapi = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")
AUTH = {"Authorization": "Bearer geheim"}


def _add(tmp_path: Path, ids=("sensor.a",), **kwargs):
    return selection_jobs.create(tmp_path, source="assistant", action="add", mode="label", entity_ids=list(ids), **kwargs)


# ---- Speicher ----------------------------------------------------------------------------

def test_create_stores_the_contract_fields_and_counts_the_revision(tmp_path: Path) -> None:
    assert selection_jobs.rev(tmp_path) == 0
    job = _add(tmp_path, ("sensor.a", "weather.home", "sensor.a"), topic="wetter", force_ids=["weather.home", "x.y"], now=1000.0)
    assert job["entity_ids"] == ["sensor.a", "weather.home"]
    assert job["force_ids"] == ["weather.home"]
    assert job["expires_ts"] == 1000.0 + 24 * 3600
    assert set(selection_jobs.public(job)) == set(selection_jobs.PUBLIC_FIELDS)
    assert selection_jobs.rev(tmp_path, now=1001.0) == 1
    stored = json.loads((tmp_path / selection_jobs.FILENAME).read_text(encoding="utf-8"))
    assert stored["jobs"][0]["status"] == "pending"


def test_remove_jobs_always_use_mode_auto_and_never_force(tmp_path: Path) -> None:
    job = selection_jobs.create(tmp_path, source="manual_ui", action="remove", entity_ids=["sensor.a"], force_ids=["sensor.a"])
    assert job["mode"] == "auto" and job["force_ids"] == []


@pytest.mark.parametrize("kwargs", [
    {"entity_ids": []},
    {"entity_ids": [f"sensor.s{i}" for i in range(201)]},
    {"entity_ids": ["Kein.Gültig"]},
    {"entity_ids": ["sensor.a"], "mode": "alles"},
    {"entity_ids": ["sensor.a"], "source": "mcp"},
    {"entity_ids": ["sensor.a"], "action": "change"},
])
def test_invalid_jobs_are_rejected(tmp_path: Path, kwargs) -> None:
    args = {"source": "assistant", "action": "add", "mode": "label", **kwargs}
    with pytest.raises(selection_jobs.JobError):
        selection_jobs.create(tmp_path, **args)
    assert selection_jobs.rev(tmp_path) == 0


def test_exactly_200_ids_are_accepted(tmp_path: Path) -> None:
    assert len(_add(tmp_path, [f"sensor.s{i}" for i in range(200)])["entity_ids"]) == 200


def test_jobs_expire_after_24_hours_and_count_as_a_change(tmp_path: Path) -> None:
    job = _add(tmp_path, now=1000.0)
    assert selection_jobs.pending_for(tmp_path, None, None, now=1000.0 + 3600) != []
    assert selection_jobs.pending_for(tmp_path, None, None, now=1000.0 + 24 * 3600) == []
    assert selection_jobs.get(tmp_path, job["id"])["status"] == "expired"
    assert selection_jobs.rev(tmp_path) == 2
    assert selection_jobs.ack(tmp_path, job["id"], "applied") is False


def test_ack_is_idempotent_and_cleans_the_results(tmp_path: Path) -> None:
    job = _add(tmp_path, ("sensor.a", "sensor.b"))
    results = {"sensor.a": {"result": "ok", "mode": "label"}, "sensor.b": {"result": "quatsch"}, "sensor.z": {"result": "ok"}}
    assert selection_jobs.ack(tmp_path, job["id"], "partial", results, " Teilweise ") is True
    rev = selection_jobs.rev(tmp_path)
    stored = selection_jobs.get(tmp_path, job["id"])
    assert stored["status"] == "partial" and stored["message"] == "Teilweise"
    assert stored["results"] == {"sensor.a": {"result": "ok", "mode": "label"}}
    assert selection_jobs.ack(tmp_path, job["id"], "failed") is True
    assert selection_jobs.get(tmp_path, job["id"])["status"] == "partial"
    assert selection_jobs.rev(tmp_path) == rev
    assert selection_jobs.pending_for(tmp_path, None, None) == []


def test_ack_of_unknown_jobs_or_with_an_unknown_status_fails(tmp_path: Path) -> None:
    job = _add(tmp_path)
    assert selection_jobs.ack(tmp_path, "gibtsnicht", "applied") is False
    assert selection_jobs.ack(tmp_path, job["id"], "pending") is False
    assert selection_jobs.get(tmp_path, job["id"])["status"] == "pending"


def test_only_the_owner_gets_and_acks_jobs(tmp_path: Path) -> None:
    job = _add(tmp_path, entry_id="EINS")
    assert [j["id"] for j in selection_jobs.pending_for(tmp_path, "EINS", "EINS")] == [job["id"]]
    assert selection_jobs.pending_for(tmp_path, "ZWEI", "EINS") == []
    # Ohne Header gilt der Aufruf als Besitzer (ältere Integration).
    assert len(selection_jobs.pending_for(tmp_path, None, "EINS")) == 1
    # Ein neuer Besitzer bekommt die Aufträge des alten nicht.
    assert selection_jobs.pending_for(tmp_path, "ZWEI", "ZWEI") == []
    assert selection_jobs.ack(tmp_path, job["id"], "applied", requester="ZWEI", owner="EINS") is False
    assert selection_jobs.ack(tmp_path, job["id"], "applied", requester="EINS", owner="EINS") is True


def test_only_200_finished_jobs_are_kept(tmp_path: Path) -> None:
    for i in range(205):
        job = _add(tmp_path, now=1000.0 + i)
        selection_jobs.ack(tmp_path, job["id"], "applied", now=1000.0 + i)
    open_job = _add(tmp_path, now=2000.0)
    jobs = selection_jobs.all_jobs(tmp_path, now=2000.0)
    assert len(jobs) == 201
    assert open_job["id"] in {j["id"] for j in jobs}
    assert min(j["created_ts"] for j in jobs if j["status"] == "applied") == 1005.0


def test_an_unreadable_file_starts_over(tmp_path: Path) -> None:
    (tmp_path / selection_jobs.FILENAME).write_text("{kaputt", encoding="utf-8")
    assert selection_jobs.rev(tmp_path) == 0 and selection_jobs.all_jobs(tmp_path) == []


# ---- Token-Routen -------------------------------------------------------------------------

def _router_client(tmp_path: Path) -> "TestClient":
    def check_auth(authorization, request_id="-", integration_version=None):
        if authorization != "Bearer geheim":
            raise HTTPException(status_code=401, detail="nein")

    app = FastAPI()
    app.include_router(create_catalog_router(tmp_path, check_auth))
    return TestClient(app)


@needs_fastapi
def test_job_routes_require_the_token(tmp_path: Path) -> None:
    client = _router_client(tmp_path)
    assert client.get("/api/selection/jobs").status_code == 401
    assert client.post("/api/selection/ack", json={"job_id": "x", "status": "applied"}).status_code == 401


@needs_fastapi
def test_catalog_remembers_the_owner_and_jobs_go_only_to_it(tmp_path: Path) -> None:
    client = _router_client(tmp_path)
    assert client.post("/api/catalog", headers={**AUTH, "X-Zeitarchiv-Entry-Id": "EINS"},
                       json={"entities": [], "log": [], "ledger_count": 0}).status_code == 200
    assert selection_catalog.owner(tmp_path) == "EINS"
    job = _add(tmp_path, entry_id="EINS")
    own = client.get("/api/selection/jobs", headers={**AUTH, "X-Zeitarchiv-Entry-Id": "EINS"}).json()
    assert [j["id"] for j in own["jobs"]] == [job["id"]]
    assert set(own["jobs"][0]) == set(selection_jobs.PUBLIC_FIELDS)
    assert client.get("/api/selection/jobs", headers={**AUTH, "X-Zeitarchiv-Entry-Id": "ZWEI"}).json() == {"jobs": []}
    assert client.get("/api/selection/jobs", headers={**AUTH, "X-Zeitarchiv-Entry-Id": "<kaputt>"}).json() == {"jobs": []}
    assert len(client.get("/api/selection/jobs", headers=AUTH).json()["jobs"]) == 1
    bad = {**AUTH, "X-Zeitarchiv-Entry-Id": "<kaputt>"}
    assert client.post("/api/selection/ack", headers=bad, json={"job_id": job["id"], "status": "applied"}).json() == {"ok": False}


@needs_fastapi
def test_an_invalid_entry_header_gets_nothing_even_without_a_known_owner(tmp_path: Path) -> None:
    client = _router_client(tmp_path)
    _add(tmp_path)
    assert client.get("/api/selection/jobs", headers={**AUTH, "X-Zeitarchiv-Entry-Id": "a b"}).json() == {"jobs": []}


@needs_fastapi
def test_ack_route_is_idempotent_and_answers_200_for_unknown_jobs(tmp_path: Path) -> None:
    client = _router_client(tmp_path)
    job = _add(tmp_path)
    body = {"job_id": job["id"], "status": "applied", "results": {"sensor.a": {"result": "ok", "mode": "label"}}}
    assert client.post("/api/selection/ack", headers=AUTH, json=body).json() == {"ok": True}
    assert client.post("/api/selection/ack", headers=AUTH, json=body).json() == {"ok": True}
    unknown = client.post("/api/selection/ack", headers=AUTH, json={**body, "job_id": "unbekannt"})
    assert unknown.status_code == 200 and unknown.json() == {"ok": False}
    assert client.get("/api/selection/jobs", headers=AUTH).json() == {"jobs": []}


@needs_fastapi
def test_extended_catalog_is_cleaned_strictly(tmp_path: Path) -> None:
    client = _router_client(tmp_path)
    response = client.post("/api/catalog", headers={**AUTH, "X-Zeitarchiv-Entry-Id": "EINS"}, json={
        "entities": [{
            "entity_id": "sensor.a", "text": 1, "origin": "assistant", "drifted": True, "topic": "energie",
            "added_ts": 12.5, "via": [{"type": "assistant", "topic": "energie", "mode": "label"},
                                      {"type": "label", "value": "Zeitarchiv", "level": "irgendwo"},
                                      {"type": "pattern"}, {"type": "hack", "value": "x"}, "kaputt"],
            "excluded_by": [{"type": "pattern", "value": "sensor.*_tag"}, {"type": "area", "value": "x"}],
        }, {"entity_id": "sensor.b", "origin": "fremd", "added_ts": "gestern"}],
        "label": {"id": "zeitarchiv", "name": "Zeitarchiv"},
        "ledger_count": 3,
        "log": [
            {"id": "l1", "ts": 10, "source": "assistant", "action": "add", "mode": "label", "entity_ids": ["sensor.a", "Böse"],
             "status": "applied", "results": {"sensor.a": {"result": "ok", "mode": "label"}, "sensor.x": {"result": "ok"}},
             "geheim": "weg"},
            {"id": "l2", "ts": 20, "source": "manual_ha", "action": "change", "entity_ids": [], "count": 5,
             "status": "applied", "ha_detail": {"kind": "options", "added_entities": 5, "patterns": "+1", "areas": "<script>",
                                                "summary": "Muster hinzugefügt, +5"}},
            {"id": "l3", "ts": 30, "source": "jemand", "action": "add"},
            {"id": "l1", "ts": 40, "source": "assistant", "action": "add"},
        ],
    })
    assert response.json() == {"ok": True, "count": 2}
    catalog = selection_catalog.load(tmp_path)
    a, b = catalog["entities"]
    assert a["text"] is True and a["origin"] == "assistant" and a["drifted"] is True and a["added_ts"] == 12.5
    assert a["via"] == [{"type": "assistant", "topic": "energie", "mode": "label"},
                        {"type": "label", "value": "Zeitarchiv", "level": "entity"}]
    assert a["excluded_by"] == [{"type": "pattern", "value": "sensor.*_tag"}]
    assert b["origin"] is None and b["added_ts"] is None
    assert catalog["label"] == {"id": "zeitarchiv", "name": "Zeitarchiv"}
    assert catalog["ledger_count"] == 3 and catalog["entry_id"] == "EINS" and catalog["phase1"] is True
    assert [entry["id"] for entry in catalog["log"]] == ["l2", "l1"]
    l1 = catalog["log"][1]
    assert l1["entity_ids"] == ["sensor.a"] and l1["results"] == {"sensor.a": {"result": "ok", "mode": "label"}}
    assert "geheim" not in l1
    assert catalog["log"][0]["ha_detail"]["areas"] is None and catalog["log"][0]["ha_detail"]["patterns"] == "+1"


@needs_fastapi
def test_old_catalog_form_stays_valid_but_is_not_phase1(tmp_path: Path) -> None:
    client = _router_client(tmp_path)
    assert client.post("/api/catalog", headers=AUTH, json={"entities": [{"entity_id": "sensor.a"}]}).status_code == 200
    catalog = selection_catalog.load(tmp_path)
    assert catalog["phase1"] is False and catalog["entry_id"] is None and catalog["log"] == []


@needs_fastapi
def test_notices_carry_selection_rev_and_health_the_capability(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    coordinator = StorageCoordinator()
    tz = ZoneInfo("Europe/Berlin")
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path, index=index, tz=tz, coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, tz, coordinator), api_token=lambda: "test-token",
        app_version="0.0.0-test", collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    auth = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        assert client.get("/api/notices", headers=auth).json()["selection_rev"] == 0
        job = _add(tmp_path)
        assert client.get("/api/notices", headers=auth).json()["selection_rev"] == 1
        client.post("/api/selection/ack", headers=auth, json={"job_id": job["id"], "status": "applied"})
        assert client.get("/api/notices", headers=auth).json()["selection_rev"] == 2
        assert "selection" in client.get("/api/health", headers=auth).json()["capabilities"]
    index.close()
