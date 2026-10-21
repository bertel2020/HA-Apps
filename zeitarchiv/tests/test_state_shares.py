"""Zustands-Entitäten, Auswertung (ROADMAP 1.29 Phase 2): Rollups mit der
Dauer je Zustand und die Anteile ab dem Zeitraum "Monat".

Geprüft wird, was die Zahlen richtig macht:

- Der Zustand vom Monatsende läuft in den nächsten Monat weiter — ohne das
  fehlte jedem Monat die Zeit bis zu seinem ersten Wechsel.
- Als Beginn zählt nur ein echter Wechsel, nicht der übernommene Zustand und
  nicht ein doppelt gemeldeter gleicher.
- Ein Monat ganz ohne Wechsel (kein Archivmonat) bekommt den Zustand, der
  vorher galt, statt leer zu bleiben.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

import _paths  # noqa: F401
from app.storage import query, rollup
from app.storage.index import STATE_UNAVAILABLE_INDEX, Index
from app.storage.ingestion import IngestEvent, IngestionService

TZ = ZoneInfo("Europe/Berlin")
ENTITY = "sensor.k11_betriebszustand"
HOUR = 3600.0


def _ts(*args: int) -> float:
    return datetime(*args, tzinfo=TZ).timestamp()


def _state(state: str, ts: float) -> IngestEvent:
    return IngestEvent(
        event_id=uuid.uuid4().hex, entity_id=ENTITY, domain="sensor", ts=ts, value=None, state=state,
    )


@pytest.fixture
def setup(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    try:
        yield tmp_path, index, IngestionService(tmp_path, index, TZ)
    finally:
        index.close()


def _shares(point: dict) -> dict[int, tuple[float, int]]:
    return {idx: (seconds, entries) for idx, seconds, entries in point["states"]}


# --- Verdichtung -----------------------------------------------------------


def test_entries_count_only_real_changes() -> None:
    start = _ts(2026, 9, 1)
    rows = [(start + HOUR, 1.0), (start + 2 * HOUR, 1.0), (start + 3 * HOUR, 0.0)]
    fine, last = rollup.compute_fine_rollup(
        rows, "state", "tag", TZ, boundary_value=0.0, window_end_ts=start + 4 * HOUR, window_start_ts=start,
    )
    assert last == 0.0
    assert fine[0].states == {0: (2 * HOUR, 1), 1: (2 * HOUR, 1)}


def test_a_state_spanning_buckets_is_split_at_the_boundary() -> None:
    start = _ts(2026, 9, 1, 22)
    fine, _ = rollup.compute_fine_rollup(
        [(start, 2.0)], "state", "tag", TZ, boundary_value=None, window_end_ts=start + 4 * HOUR,
    )
    assert [row.states for row in fine] == [{2: (2 * HOUR, 1)}, {2: (2 * HOUR, 0)}]


# --- Rollups beim Monatswechsel ---------------------------------------------


def test_rotation_writes_long_format_rollups_with_carried_state(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 8, 31, 23)))
    service.ingest(_state("cleaning", _ts(2026, 9, 1, 2)))
    service.ingest(_state("charging", _ts(2026, 9, 1, 3)))
    # Je ein Event im Oktober und November rotiert August und September.
    service.ingest(_state("charging", _ts(2026, 10, 2)))
    service.ingest(_state("charging", _ts(2026, 11, 2)))

    table = pq.read_table(rollup.rollup_path(tmp, ENTITY, "stunde"))
    assert table.column_names == ["bucket_start", "idx", "seconds", "entries"]

    september = query._read_rollup_rows(tmp, ENTITY, "monat", _ts(2026, 9, 1), _ts(2026, 10, 1))
    assert len(september) == 1
    states = september[0].states
    # Laden vom 31. August läuft bis 2 Uhr weiter, dann eine Stunde Saugen.
    assert states[1] == (HOUR, 1)
    assert states[0][0] == pytest.approx(30 * 24 * HOUR - HOUR)
    assert states[0][1] == 1  # nur der Wechsel um 3 Uhr, nicht der Übertrag

    # Oktober: ein doppelt gemeldetes "charging" ist kein Beginn.
    october = query._read_rollup_rows(tmp, ENTITY, "monat", _ts(2026, 10, 1), _ts(2026, 11, 1))
    assert october[0].states == {0: (pytest.approx(31 * 24 * HOUR + HOUR), 0)}


# --- Abfrage --------------------------------------------------------------


def test_week_stays_a_timeline_and_month_becomes_shares(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    service.ingest(_state("cleaning", _ts(2026, 10, 20, 9)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    week = query.query_series(tmp, index, ENTITY, "week", TZ, now)
    assert week["chart_type"] == "timeline"

    month = query.query_series(tmp, index, ENTITY, "month", TZ, now)
    assert month["chart_type"] == "shares"
    assert month["bucket"] == "tag"
    assert [p["ts"] for p in month["points"]] == [_ts(2026, 10, 20)]
    assert _shares(month["points"][0]) == {0: (HOUR, 1), 1: (3 * HOUR, 1)}
    assert month["states"][-1]["idx"] == STATE_UNAVAILABLE_INDEX


def test_month_view_combines_rollups_and_the_running_month(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("cleaning", _ts(2026, 9, 30, 22)))
    service.ingest(_state("charging", _ts(2026, 10, 1, 1)))
    now = datetime(2026, 10, 1, 2, tzinfo=TZ)

    year = query.query_series(tmp, index, ENTITY, "year", TZ, now)
    by_month = {p["ts"]: _shares(p) for p in year["points"]}
    # "cleaning" kam zuerst und hat deshalb Index 0.
    assert by_month[_ts(2026, 9, 1)] == {0: (2 * HOUR, 1)}
    # Der laufende Monat beginnt mit dem übernommenen Saugen.
    assert by_month[_ts(2026, 10, 1)] == {0: (HOUR, 0), 1: (HOUR, 1)}


def test_a_month_without_changes_carries_the_previous_state(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 7, 31, 12)))
    # August und September ohne einen einzigen Wechsel.
    service.ingest(_state("cleaning", _ts(2026, 10, 1, 6)))
    now = datetime(2026, 10, 1, 7, tzinfo=TZ)

    year = query.query_series(tmp, index, ENTITY, "year", TZ, now)
    by_month = {p["ts"]: _shares(p) for p in year["points"]}
    assert by_month[_ts(2026, 8, 1)] == {0: (pytest.approx(31 * 24 * HOUR), 0)}
    assert by_month[_ts(2026, 9, 1)] == {0: (pytest.approx(30 * 24 * HOUR), 0)}
    # Vor dem ersten Wert gibt es nichts aufzufüllen.
    assert _ts(2026, 6, 1) not in by_month


def test_decade_sums_months_into_years(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 9, 30, 23)))
    service.ingest(_state("cleaning", _ts(2026, 10, 1, 1)))
    now = datetime(2026, 10, 1, 3, tzinfo=TZ)

    decade = query.query_series(tmp, index, ENTITY, "decade", TZ, now)
    assert [p["ts"] for p in decade["points"]] == [_ts(2026, 1, 1)]
    assert _shares(decade["points"][0]) == {0: (2 * HOUR, 1), 1: (2 * HOUR, 1)}


# --- Kachel, Diagrammtyp, Repair-Hinweise ------------------------------------


def test_state_durations_and_since(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 6)))
    service.ingest(_state("cleaning", _ts(2026, 10, 20, 9)))
    service.ingest(_state("cleaning", _ts(2026, 10, 20, 10)))  # doppelt gemeldet
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    day = query.query_series(tmp, index, ENTITY, "day", TZ, now)
    assert query.state_durations(day) == {0: 3 * HOUR, 1: 3 * HOUR}
    month = query.query_series(tmp, index, ENTITY, "month", TZ, now)
    assert query.state_durations(month) == {0: 3 * HOUR, 1: 3 * HOUR}
    # Seit dem Wechsel um 9 Uhr, nicht seit der doppelten Meldung.
    assert query.state_since(tmp, index, ENTITY, TZ, now) == _ts(2026, 10, 20, 9)


def test_charts_of_states_default_to_the_timeline() -> None:
    from app.chart_types import effective_chart_type

    types = {"sensor.a": "state", "switch.b": "switch", "sensor.c": "standard"}
    assert effective_chart_type({"chart_type": "auto", "entity_ids": ["sensor.a", "switch.b"]}, types) == "timeline"
    assert effective_chart_type({"chart_type": "bar", "entity_ids": ["sensor.a"]}, types) == "timeline"
    assert effective_chart_type({"chart_type": "donut", "entity_ids": ["sensor.a"]}, types) == "donut"
    assert effective_chart_type({"chart_type": "auto", "entity_ids": ["sensor.a", "sensor.c"]}, types) == "auto"


def test_rejections_become_notices_and_are_throttled(setup) -> None:
    from app import notices

    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 6)))
    number = IngestEvent(
        event_id=uuid.uuid4().hex, entity_id=ENTITY, domain="sensor", ts=_ts(2026, 10, 20, 7),
        value=21.5, state_class="measurement",
    )
    assert service.ingest(number) == "skipped"
    first = index.state_rejections(0)
    service.ingest(replace_event_id(number))
    # Gedrosselt: der zweite Fehlschlag schreibt nicht erneut.
    assert index.state_rejections(0) == first

    found = notices.state_rejection_notices(index)
    assert [n["id"] for n in found] == ["entities.state_type_mismatch"]
    assert ENTITY in found[0]["detail"]
    # Eine Woche nach dem letzten Vorkommen ist der Hinweis weg.
    later = first[0]["last"] + notices.STATE_REJECTION_NOTICE_SECONDS + 1
    assert notices.state_rejection_notices(index, now=later) == []


def replace_event_id(event: IngestEvent) -> IngestEvent:
    from dataclasses import replace

    return replace(event, event_id=uuid.uuid4().hex)


def test_entity_stats_carries_the_tile_state(setup) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import api_routes
    from app.storage.coordinator import StorageCoordinator

    tmp, index, service = setup
    now_ts = datetime.now(TZ).timestamp()
    service.ingest(_state("charging", now_ts - 5 * HOUR))
    service.ingest(_state("cleaning", now_ts - HOUR))
    coordinator = StorageCoordinator()
    deps = api_routes.ApiDependencies(
        data_dir=tmp, index=index, tz=TZ, coordinator=coordinator,
        ingestion=IngestionService(tmp, index, TZ, coordinator),
        api_token=lambda: "t", app_version="0.0.0-test", collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    with TestClient(app) as client:
        body = client.get("/api/entity-stats", params={"entity_ids": ENTITY, "range": "day"}).json()
    state = body["series"][0]["state"]
    assert state["idx"] == 1
    assert state["since"] == pytest.approx(now_ts - HOUR)
    assert [p["value"] for p in state["strip"]][-2:] == [0.0, 1.0]
    assert sum(seconds for _idx, seconds in state["durations"]) > 0
    assert state["days"]
