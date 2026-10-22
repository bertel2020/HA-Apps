"""Zustands-Entitäten, Heatmap (ROADMAP 1.29 Phase 3): Zeit je Zustand nach
Wochentag und Stunde, abgeleitet aus stunde.parquet.

Geprüft wird, was die Zellen richtig macht:

- Wochentag und Stunde zählen in Ortszeit, nicht in UTC.
- Eine Zelle sammelt gleiche Wochentage und Stunden über den ganzen Zeitraum.
- "nicht verfügbar" steht in der Liste, zählt aber nicht in den verfügbaren
  Nenner und nicht als Beobachtung.
- Ein Monat ganz ohne Wechsel bekommt den vorher geltenden Zustand.
- Der laufende Monat kommt live aus dem Hot Buffer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import _paths  # noqa: F401
from app.storage import query
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


def _cells(result: dict) -> dict[tuple[int, int], dict]:
    return {
        (weekday, hour): {"n": n, "available": available, "seconds": {idx: s for idx, s in states}}
        for weekday, hour, n, available, states in result["cells"]
    }


def test_the_running_month_is_read_live_in_local_time(setup) -> None:
    tmp, index, service = setup
    # Dienstag, 20.10.2026: 9 bis 10 Uhr Saugen, danach Laden.
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    service.ingest(_state("cleaning", _ts(2026, 10, 20, 9)))
    service.ingest(_state("charging", _ts(2026, 10, 20, 10)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    result = query.query_state_heatmap(tmp, index, ENTITY, "month", TZ, now)

    assert result["chart_type"] == "heatmap"
    cells = _cells(result)
    # "charging" kam zuerst und hat Index 0, "cleaning" Index 1.
    assert cells[(1, 9)] == {"n": 1, "available": HOUR, "seconds": {1: HOUR}}
    assert cells[(1, 8)]["seconds"] == {0: HOUR}
    assert cells[(1, 11)]["seconds"] == {0: HOUR}
    assert (1, 12) not in cells
    assert result["states"][-1]["idx"] == STATE_UNAVAILABLE_INDEX


def test_a_cell_collects_the_same_weekday_and_hour_over_the_period(setup) -> None:
    tmp, index, service = setup
    # Jeden Dienstag im Oktober von 9 bis 10 Uhr Saugen (6., 13., 20.).
    for day in (6, 13, 20):
        service.ingest(_state("charging", _ts(2026, 10, day, 8)))
        service.ingest(_state("cleaning", _ts(2026, 10, day, 9)))
        service.ingest(_state("charging", _ts(2026, 10, day, 10)))
    now = datetime(2026, 10, 20, 23, tzinfo=TZ)

    cells = _cells(query.query_state_heatmap(tmp, index, ENTITY, "month", TZ, now))

    assert cells[(1, 9)]["n"] == 3
    assert cells[(1, 9)]["seconds"][1] == pytest.approx(3 * HOUR)
    assert cells[(1, 9)]["available"] == pytest.approx(3 * HOUR)


def test_unavailable_stays_out_of_the_denominator_and_the_observations(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    service.ingest(_state("unavailable", _ts(2026, 10, 20, 9)))
    service.ingest(_state("charging", _ts(2026, 10, 20, 10)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    cells = _cells(query.query_state_heatmap(tmp, index, ENTITY, "month", TZ, now))

    nine = cells[(1, 9)]
    assert nine["seconds"] == {STATE_UNAVAILABLE_INDEX: HOUR}
    assert nine["available"] == 0.0
    assert nine["n"] == 0


def test_a_part_hour_below_the_coverage_does_not_count_as_an_observation(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    now = datetime(2026, 10, 20, 10, 10, tzinfo=TZ)

    cells = _cells(query.query_state_heatmap(tmp, index, ENTITY, "month", TZ, now))

    assert cells[(1, 8)]["n"] == 1
    assert cells[(1, 10)]["n"] == 0
    assert cells[(1, 10)]["available"] == pytest.approx(600.0)


def test_completed_months_come_from_the_hour_rollups(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 8, 31, 23)))
    service.ingest(_state("cleaning", _ts(2026, 9, 1, 2)))  # Dienstag
    service.ingest(_state("charging", _ts(2026, 9, 1, 3)))
    service.ingest(_state("charging", _ts(2026, 10, 2)))
    service.ingest(_state("charging", _ts(2026, 11, 2)))  # rotiert August bis Oktober
    now = datetime(2026, 11, 2, 12, tzinfo=TZ)

    cells = _cells(query.query_state_heatmap(tmp, index, ENTITY, "year", TZ, now))

    # 1.9. war ein Dienstag, zwischen 2 und 3 Uhr lief "cleaning" (Index 1).
    assert cells[(1, 2)]["seconds"][1] == pytest.approx(HOUR)
    # Ein Jahr deckt jede Zelle, in der der Zustand gilt, mehrfach ab.
    assert cells[(1, 2)]["n"] >= 4


def test_a_month_without_changes_carries_the_previous_state(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 7, 31, 12)))
    # August und September ohne einen einzigen Wechsel.
    service.ingest(_state("cleaning", _ts(2026, 10, 1, 6)))
    now = datetime(2026, 10, 1, 7, tzinfo=TZ)

    cells = _cells(query.query_state_heatmap(tmp, index, ENTITY, "year", TZ, now))

    # 61 Tage, jeder Wochentag etwa achtmal; Dienstag um 3 Uhr also mindestens acht Beobachtungen.
    assert cells[(1, 3)]["n"] >= 8
    assert set(cells[(1, 3)]["seconds"]) == {0}
    # Vor dem ersten Wert gibt es nichts aufzufüllen: erst ab 31.7. 12 Uhr.
    assert cells[(4, 13)]["seconds"][0] > 0


def test_the_hours_of_a_daylight_saving_change_follow_the_clock(setup) -> None:
    tmp, index, service = setup
    # Umstellung am Sonntag, 25.10.2026: 2 Uhr kommt zweimal vor.
    service.ingest(_state("charging", _ts(2026, 10, 24, 20)))
    service.ingest(_state("charging", _ts(2026, 10, 26, 6)))
    now = datetime(2026, 10, 26, 7, tzinfo=TZ)

    cells = _cells(query.query_state_heatmap(tmp, index, ENTITY, "month", TZ, now))

    assert cells[(6, 2)]["n"] == 2
    assert cells[(6, 2)]["available"] == pytest.approx(2 * HOUR)


def test_a_week_has_no_heatmap(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    with pytest.raises(ValueError):
        query.query_state_heatmap(tmp, index, ENTITY, "week", TZ, now)


def test_an_unknown_entity_gets_the_empty_result(setup) -> None:
    tmp, index, _service = setup
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    result = query.query_state_heatmap(tmp, index, "sensor.gibt_es_nicht", "month", TZ, now)

    assert result["points"] == []
    assert "cells" not in result or result["cells"] == []
