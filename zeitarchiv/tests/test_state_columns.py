"""Zustands-Zeitstrahl in Spalten vom Server (ROADMAP 1.29, Pixelverdichtung
Schritt 2): ein Jahr oder eine Dekade in ``px`` gleich breite Spalten aus den
Stunden-Rollups, für den Fall, dass die Rohwechsel über der Grenze liegen.

Geprüft wird, was die Spalten richtig macht:

- Eine Spalte enthält genau ihre Breite an Zeit (die Summe stimmt mit der
  erfassten Zeit überein), eine Stunde über einer Spaltengrenze wird nach
  Zeitanteilen verteilt.
- Ein Beginn zählt einmal, in der Spalte, in der der Zustand anfängt.
- Es gibt nur Zeiträume Jahr und Dekade; die Breite wird begrenzt.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import _paths  # noqa: F401
from app.storage import query
from app.storage.index import Index
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


def _seconds(result: dict) -> dict[int, float]:
    totals: dict[int, float] = {}
    for _start, parts in result["columns"]:
        for idx, seconds, _begins in parts:
            totals[idx] = totals.get(idx, 0.0) + seconds
    return totals


def test_columns_hold_the_recorded_time_and_split_states(setup) -> None:
    tmp, index, service = setup
    # Zwei Zustände im laufenden Monat: 8 bis 9 Uhr Laden, danach Saugen bis 10 Uhr, danach Laden.
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    service.ingest(_state("cleaning", _ts(2026, 10, 20, 9)))
    service.ingest(_state("charging", _ts(2026, 10, 20, 10)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    result = query.query_state_columns(tmp, index, ENTITY, "year", TZ, now, px=1000)

    assert result["chart_type"] == "columns"
    assert result["dt"] == pytest.approx((result["period_end"] - result["window_start"]) / 1000)
    totals = _seconds(result)
    # "cleaning" (Index 1) lief genau eine Stunde, "charging" (Index 0) steht für den Rest.
    assert totals[1] == pytest.approx(HOUR, abs=1.0)
    assert totals[0] > 2 * HOUR


def test_an_hour_over_a_column_edge_is_split_by_time(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    service.ingest(_state("cleaning", _ts(2026, 10, 20, 9)))
    service.ingest(_state("charging", _ts(2026, 10, 20, 10)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)
    # Dekade bei der kleinsten Breite: eine Spalte ist Tage breit, die Stunde Saugen liegt
    # in einer, höchstens zwei Spalten, und nichts geht verloren.
    result = query.query_state_columns(tmp, index, ENTITY, "decade", TZ, now, px=query.STATE_COLUMNS_MIN)

    assert _seconds(result)[1] == pytest.approx(HOUR, abs=1.0)
    begins = sum(parts_begin for _start, parts in result["columns"] for idx, _s, parts_begin in parts if idx == 1)
    assert begins == 1


def test_only_year_and_decade_exist_and_the_width_is_clamped(setup) -> None:
    tmp, index, service = setup
    service.ingest(_state("charging", _ts(2026, 10, 20, 8)))
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)

    with pytest.raises(ValueError):
        query.query_state_columns(tmp, index, ENTITY, "month", TZ, now, px=500)

    narrow = query.query_state_columns(tmp, index, ENTITY, "year", TZ, now, px=1)
    wide = query.query_state_columns(tmp, index, ENTITY, "year", TZ, now, px=10**6)
    span = narrow["period_end"] - narrow["window_start"]
    assert narrow["dt"] == pytest.approx(span / query.STATE_COLUMNS_MIN)
    assert wide["dt"] == pytest.approx(span / query.STATE_COLUMNS_MAX)


def test_a_missing_entity_is_an_empty_result(setup) -> None:
    tmp, index, _service = setup
    now = datetime(2026, 10, 20, 12, tzinfo=TZ)
    result = query.query_state_columns(tmp, index, "sensor.gibt_es_nicht", "year", TZ, now, px=500)
    assert result.get("columns", []) == []
