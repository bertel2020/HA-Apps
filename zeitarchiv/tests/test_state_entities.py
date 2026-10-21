"""Zustands-Entitäten (Typ "state", ROADMAP 1.29 Phase 1).

Text-Zustände wie "Heizen"/"Saugen" werden als Ganzzahl-Index im
unveränderten Wert-Feld gespeichert; die Zuordnung Index → Text steht in
entity_states. Geprüft wird, was davon dauerhaft gelten muss:

- Indizes sind stabil und werden nie wiederverwendet (sonst würden schon
  archivierte Monate umgedeutet).
- Der erste Wert legt den Typ fest; ein Wechsel zwischen Zahl und Text wird
  verworfen, nicht migriert.
- "unavailable"/"unknown" zählen nur bei einer bestehenden Zustands-Entität.
- Bis zur Woche liefern Abfragen die Rohwechsel (die Anteile ab dem Monat
  prüft test_state_shares.py).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

import _paths  # noqa: F401
from app.storage import ha_import, query, rotate
from app.storage.index import (
    MAX_STATES_PER_ENTITY,
    STATE_UNAVAILABLE_INDEX,
    Index,
    StateTypeMismatch,
)
from app.storage.ingestion import IngestEvent, IngestionService
from app.storage.paths import entity_dir

TZ = ZoneInfo("Europe/Berlin")
BASE_TS = datetime(2026, 10, 20, 8, 0, tzinfo=TZ).timestamp()
ENTITY = "sensor.k11_betriebszustand"


def _state(state: str, ts: float, *, label: str | None = None, entity_id: str = ENTITY) -> IngestEvent:
    return IngestEvent(
        event_id=uuid.uuid4().hex,
        entity_id=entity_id,
        domain="sensor",
        ts=ts,
        value=None,
        state=state,
        state_label=label,
    )


def _number(value: float, ts: float, entity_id: str = ENTITY) -> IngestEvent:
    return IngestEvent(
        event_id=uuid.uuid4().hex,
        entity_id=entity_id,
        domain="sensor",
        ts=ts,
        value=value,
        state_class="measurement",
    )


@pytest.fixture
def setup(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    try:
        yield tmp_path, index, IngestionService(tmp_path, index, TZ)
    finally:
        index.close()


# --- Zuordnung -----------------------------------------------------------


def test_indexes_are_assigned_in_order_and_stay_stable(setup) -> None:
    _, index, _ = setup
    assert index.state_index(ENTITY, "charging", None, BASE_TS) == 0
    assert index.state_index(ENTITY, "cleaning", None, BASE_TS) == 1
    assert index.state_index(ENTITY, "charging", None, BASE_TS) == 0
    assert [row["text"] for row in index.get_state_map(ENTITY)] == ["charging", "cleaning"]


def test_label_follows_the_latest_translation_without_new_index(setup) -> None:
    _, index, _ = setup
    index.state_index(ENTITY, "heat", "Heizen", BASE_TS)
    assert index.state_index(ENTITY, "heat", "Heating", BASE_TS) == 0
    assert index.get_state_map(ENTITY) == [{"idx": 0, "text": "heat", "label": "Heating"}]


def test_new_states_beyond_the_limit_are_refused(setup) -> None:
    _, index, _ = setup
    for n in range(MAX_STATES_PER_ENTITY):
        assert index.state_index(ENTITY, f"s{n}", None, BASE_TS) == n
    assert index.state_index(ENTITY, "one-too-many", None, BASE_TS) is None
    # Bekannte Zustände gehen weiter.
    assert index.state_index(ENTITY, "s3", None, BASE_TS) == 3


# --- Schreibpfad ---------------------------------------------------------


def test_text_states_create_a_state_entity_with_index_values(setup) -> None:
    _, index, service = setup
    assert service.ingest(_state("charging", BASE_TS, label="Laden")) == "written"
    assert service.ingest(_state("cleaning", BASE_TS + 60, label="Saugen")) == "written"
    assert service.ingest(_state("charging", BASE_TS + 120)) == "written"

    entity = index.get_entity(ENTITY)
    assert entity["aggregation_type"] == "state"
    assert entity["resolution"] == "raw"
    assert entity["compact_target"] == "off"
    assert entity["last_value"] == 0.0
    assert entity["row_count"] == 3


def test_unavailable_only_counts_for_an_existing_state_entity(setup) -> None:
    _, index, service = setup
    # Beginnt mit einem Ausfall: noch kein Typ, nichts anlegen.
    assert service.ingest(_state("unavailable", BASE_TS)) == "skipped"
    assert index.get_entity(ENTITY) is None

    service.ingest(_state("charging", BASE_TS + 60))
    assert service.ingest(_state("unknown", BASE_TS + 120)) == "written"
    assert index.get_entity(ENTITY)["last_value"] == float(STATE_UNAVAILABLE_INDEX)
    # Zählt nicht als Zustand in der Zuordnung.
    assert [row["idx"] for row in index.get_state_map(ENTITY)] == [0]


def test_placeholder_texts_are_skipped(setup) -> None:
    _, index, service = setup
    assert service.ingest(_state("none", BASE_TS)) == "skipped"
    assert service.ingest(_state("  ", BASE_TS)) == "skipped"
    assert index.get_entity(ENTITY) is None


def test_number_after_text_is_refused(setup) -> None:
    _, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    assert service.ingest(_number(21.5, BASE_TS + 60)) == "skipped"
    entity = index.get_entity(ENTITY)
    assert entity["aggregation_type"] == "state"
    assert entity["row_count"] == 1


def test_text_after_number_is_refused_without_a_mapping(setup) -> None:
    _, index, service = setup
    service.ingest(_number(21.5, BASE_TS))
    assert service.ingest(_state("Heating", BASE_TS + 60)) == "skipped"
    assert index.get_entity(ENTITY)["aggregation_type"] == "standard"
    assert index.get_state_map(ENTITY) == []


def test_index_rejects_a_type_switch_directly(setup) -> None:
    _, index, _ = setup
    index.get_or_create_entity(ENTITY, "sensor", None, None, state=True)
    with pytest.raises(StateTypeMismatch):
        index.get_or_create_entity(ENTITY, "sensor", "measurement", None)


def test_states_beyond_the_limit_are_skipped_on_ingest(setup) -> None:
    _, index, service = setup
    for n in range(MAX_STATES_PER_ENTITY):
        assert service.ingest(_state(f"s{n}", BASE_TS + n)) == "written"
    assert service.ingest(_state("address 51", BASE_TS + 100)) == "skipped"
    assert service.ingest(_state("s7", BASE_TS + 101)) == "written"


def test_deleting_the_entity_removes_its_mapping(setup) -> None:
    _, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    index.delete_entity(ENTITY)
    assert index.get_state_map(ENTITY) == []


# --- Abfrage, Archiv, Rollups -------------------------------------------


def test_query_returns_raw_changes_with_the_legend(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", BASE_TS, label="Laden"))
    service.ingest(_state("cleaning", BASE_TS + 3600, label="Saugen"))
    now = datetime(2026, 10, 20, 12, 0, tzinfo=TZ)

    for range_key in ("day", "week"):
        result = query.query_series(tmp, index, ENTITY, range_key, TZ, now)
        assert result["chart_type"] == "timeline"
        assert [p["value"] for p in result["points"]][-2:] == [0.0, 1.0]
    assert result["states"] == [
        {"idx": 0, "text": "charging", "label": "Laden"},
        {"idx": 1, "text": "cleaning", "label": "Saugen"},
        {"idx": STATE_UNAVAILABLE_INDEX, "text": "unavailable", "label": None},
    ]


def test_month_rotation_archives_with_mapping_and_writes_rollups(setup) -> None:
    tmp, index, service = setup
    september = datetime(2026, 9, 30, 22, 0, tzinfo=TZ).timestamp()
    service.ingest(_state("charging", september))
    service.ingest(_state("cleaning", september + 60))
    # Erstes Event im Oktober rotiert den September ins Archiv.
    service.ingest(_state("charging", BASE_TS))

    archive = entity_dir(tmp, "archive", ENTITY) / "2026-09.parquet"
    assert archive.exists()
    metadata = pq.read_schema(archive).metadata
    mapping = json.loads(metadata[rotate.STATE_MAP_METADATA_KEY.encode()])
    assert mapping == {"0": "charging", "1": "cleaning", "-1": "unavailable"}
    assert entity_dir(tmp, "rollup", ENTITY).exists()


# --- Recorder-Import ------------------------------------------------------


def test_history_import_maps_text_for_state_entities(setup, monkeypatch) -> None:
    _, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    entries = [
        {"state": "charging", "last_changed": "2026-10-01T06:00:00+00:00"},
        {"state": "mopping", "last_changed": "2026-10-01T07:00:00+00:00"},
        {"state": "unavailable", "last_changed": "2026-10-01T08:00:00+00:00"},
        {"state": "", "last_changed": "2026-10-01T09:00:00+00:00"},
    ]
    monkeypatch.setattr(ha_import, "_get", lambda *_a, **_k: [entries])
    mapper = ha_import.state_mapper_for(index, ENTITY)
    result = ha_import.fetch_history_rows(
        ENTITY, "sensor",
        datetime(2026, 10, 1, tzinfo=TZ), datetime(2026, 10, 1, 12, tzinfo=TZ),
        state_mapper=mapper,
    )
    assert [value for _ts, value in result.rows] == [0.0, 1.0, float(STATE_UNAVAILABLE_INDEX)]
    assert result.skipped == 1
    assert ha_import.state_mapper_for(index, "sensor.unknown_entity") is None


# --- CSV, Symcon, Export ---------------------------------------------------


def _csv(tmp: Path, text: str) -> Path:
    path = tmp / "zustaende.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_number_imports_refuse_a_state_entity_but_the_text_sources_accept_it(setup) -> None:
    from app.storage import symcon_import

    tmp, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    rows = [(BASE_TS + 3600, 7.5)]
    with pytest.raises(ValueError):
        symcon_import.plan_import_rows(tmp, index, rows, ENTITY, TZ)
    with pytest.raises(ValueError):
        symcon_import.import_rows(tmp, index, rows, ENTITY, TZ)
    assert symcon_import.plan_import_rows(tmp, index, rows, ENTITY, TZ, accepts_state=True).entity_id == ENTITY


def test_csv_text_is_mapped_to_state_indexes_and_repeats_collapse(setup) -> None:
    from app.storage import csv_import

    tmp, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    path = _csv(tmp, "ts,state\n"
                "2026-10-01 06:00:00,charging\n2026-10-01 06:01:00,charging\n"
                "2026-10-01 07:00:00,mopping\n2026-10-01 08:00:00,unavailable\n2026-10-01 09:00:00,\n")
    parsed = csv_import.parse_rows(
        path, ",", True, 0, 1, "iso", "", TZ, state_mapper=ha_import.state_mapper_for(index, ENTITY)
    )
    assert [v for _t, v in parsed.rows] == [0.0, 1.0, float(STATE_UNAVAILABLE_INDEX)]
    assert parsed.skipped == 1
    assert [row["text"] for row in index.get_state_map(ENTITY)] == ["charging", "mopping"]


def test_csv_dry_run_creates_no_states_and_reports_new_ones(setup) -> None:
    from app.storage import csv_import

    tmp, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    path = _csv(tmp, "ts,state\n2026-10-01 06:00:00,charging\n2026-10-01 07:00:00,mopping\n")
    mapper = ha_import.dry_state_mapper_for(index, ENTITY)
    parsed = csv_import.parse_rows(path, ",", True, 0, 1, "iso", "", TZ, state_mapper=mapper)
    assert [v for _t, v in parsed.rows] == [0.0, 1.0]
    assert mapper.new_states == ["mopping"] and mapper.refused == 0
    assert [row["text"] for row in index.get_state_map(ENTITY)] == ["charging"]
    assert ha_import.dry_state_mapper_for(index, "sensor.unknown_entity") is None


def test_csv_dry_run_counts_rows_beyond_the_state_limit(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("s0", BASE_TS))
    for n in range(1, MAX_STATES_PER_ENTITY):
        index.state_index(ENTITY, f"s{n}", None, BASE_TS)
    mapper = ha_import.dry_state_mapper_for(index, ENTITY)
    assert mapper("s3", BASE_TS) == 3.0
    assert mapper("one-too-many", BASE_TS) is None
    assert mapper.refused == 1 and mapper.new_states == []


def test_symcon_profile_names_become_states_of_integer_variables() -> None:
    from app.storage import symcon_import

    settings = json.dumps({
        "objects": {
            "ID1": {"name": "Betriebsart", "parentID": 0, "type": 2,
                    "data": {"type": 1, "profile": "", "customProfile": "Mode"}},
            "ID2": {"name": "Temperatur", "parentID": 0, "type": 2,
                    "data": {"type": 2, "profile": "~Temperature", "customProfile": ""}},
            "ID3": {"name": "Zähler", "parentID": 0, "type": 2,
                    "data": {"type": 1, "profile": "Plain", "customProfile": ""}},
        },
        "profiles": {
            "Mode": {"associations": [{"name": "Aus", "value": 0}, {"name": "Komfort", "value": 3}, {"name": "", "value": 9}]},
            "Plain": {"associations": []},
        },
    })
    names = symcon_import.extract_variable_names(settings)
    assert names["1"]["states"] == {"0": "Aus", "3": "Komfort"}
    assert "states" not in names["2"] and "states" not in names["3"]


def _symcon_variable(tmp: Path, lines: list[str]):
    from app.storage import symcon_import

    path = tmp / "4711.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return symcon_import.SymconVariable(variable_id="4711", files=[path], row_count=len(lines))


def test_symcon_integer_codes_go_through_the_profile_into_a_state_entity(setup) -> None:
    from app.storage import symcon_import

    tmp, index, service = setup
    service.ingest(_state("off", BASE_TS, label="Aus"))
    t = int(BASE_TS) - 40 * 86400
    variable = _symcon_variable(tmp, [f"{t},0", f"{t + 60},0", f"{t + 120},3", f"{t + 180},5", f"{t + 240},2"])
    associations = {0: "Aus", 2: "Winter", 3: "Komfort"}

    plan = symcon_import.plan_import(tmp, index, variable, ENTITY, TZ, associations=associations)
    # Aus passt über die Übersetzung auf "off"; 5 hat keinen Namen im Profil; die
    # Wiederholung von 0 fällt zusammen. Die Vorschau legt nichts an.
    assert plan.rows_to_import == 3 and plan.unmapped_rows == 1
    assert plan.new_states == ["Komfort", "Winter"] or plan.new_states == ["Winter", "Komfort"]
    assert [row["text"] for row in index.get_state_map(ENTITY)] == ["off"]

    symcon_import.import_variable(tmp, index, variable, ENTITY, TZ, associations=associations)
    assert sorted(row["text"] for row in index.get_state_map(ENTITY)) == ["Komfort", "Winter", "off"]


def test_symcon_variable_without_profile_names_cannot_fill_a_state_entity(setup) -> None:
    from app.storage import symcon_import

    tmp, index, service = setup
    service.ingest(_state("off", BASE_TS))
    variable = _symcon_variable(tmp, [f"{int(BASE_TS) - 86400},1"])
    with pytest.raises(ValueError):
        symcon_import.plan_import(tmp, index, variable, ENTITY, TZ)
    with pytest.raises(ValueError):
        symcon_import.import_variable(tmp, index, variable, ENTITY, TZ)


def test_export_writes_the_raw_text_of_a_state_entity(setup) -> None:
    from app import export_data
    from app.storage.coordinator import StorageCoordinator

    tmp, index, service = setup
    service.ingest(_state("charging", BASE_TS))
    service.ingest(_state('say "hi", ok', BASE_TS + 60))
    service.ingest(_state("unavailable", BASE_TS + 120))
    out = "".join(export_data.csv_chunks(
        tmp, index, index.get_entity(ENTITY), None, None, "hour", TZ, StorageCoordinator(), 1000,
    ))
    lines = out.strip().split("\r\n")
    assert lines[0] == "timestamp,unix_ts,state"
    assert [line.split(",", 2)[2] for line in lines[1:]] == ["charging", '"say ""hi"", ok"', "unavailable"]


# --- API -----------------------------------------------------------------


def _client(index: Index, tmp_path: Path):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import api_routes
    from app.storage.coordinator import StorageCoordinator

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
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    return TestClient(app)


AUTH = {"Authorization": "Bearer test-token"}


def test_health_announces_the_state_capability(setup) -> None:
    tmp, index, _ = setup
    with _client(index, tmp) as client:
        body = client.get("/api/health", headers=AUTH).json()
    assert "state" in body["capabilities"]


def test_write_accepts_state_and_requires_value_or_state(setup) -> None:
    tmp, index, _ = setup
    base = {"entity_id": ENTITY, "domain": "sensor", "ts": BASE_TS}
    with _client(index, tmp) as client:
        ok = client.post("/api/write", headers=AUTH, json={"events": [
            {**base, "event_id": "a1", "state": "heat", "state_label": "Heizen"},
        ]})
        missing = client.post("/api/write", headers=AUTH, json={"events": [
            {**base, "event_id": "a2"},
        ]})
    assert ok.status_code == 200 and ok.json()["written"] == 1
    assert index.get_state_map(ENTITY) == [{"idx": 0, "text": "heat", "label": "Heizen"}]
    assert missing.status_code == 422
