"""Einschaltvorgänge in der API (ROADMAP 1.74, Schritt 2): Serienpunkte, Tagesreihen, Tabellen-Kennzahlen,
Anzeigemodus."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import api_routes, formatting
from app.storage import hotbuffer, query
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def _switch(tmp: Path, entity_id: str, events: list[tuple[float, float]]) -> Index:
    index = Index(tmp / "index.sqlite")
    index.get_or_create_entity(entity_id, "binary_sensor", None, None)
    for ts, value in events:
        hotbuffer.append(tmp, entity_id, ts, value, TZ)
        index.record_write(entity_id, ts)
    return index


def test_series_points_of_a_switch_carry_the_number_of_starts() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-count-api-"))
    try:
        entity_id = "binary_sensor.defrost"
        index = _switch(tmp, entity_id, [
            (_ts(2024, 1, 15, 1, 0), 1.0), (_ts(2024, 1, 15, 1, 5), 0.0),
            (_ts(2024, 1, 15, 1, 30), 1.0), (_ts(2024, 1, 15, 1, 40), 0.0),
            (_ts(2024, 1, 15, 4, 0), 1.0), (_ts(2024, 1, 15, 4, 4), 0.0),
        ])
        assert index.get_entity(entity_id)["aggregation_type"] == "switch"
        now = datetime(2024, 1, 15, 12, 0, tzinfo=TZ)
        result = query.query_series(tmp, index, entity_id, "day", TZ, now, chart_type="bar")
        assert [(p["value"], p["count"]) for p in result["points"]] == [(900.0, 2), (240.0, 1)]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_an_on_state_from_the_day_before_is_not_counted_as_a_start() -> None:
    """Der Randwert für Schalter: lief er schon, ist die erste gemeldete 1 am neuen Tag keiner."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-count-api-"))
    try:
        entity_id = "binary_sensor.pump"
        index = _switch(tmp, entity_id, [
            (_ts(2024, 1, 14, 23, 0), 1.0),
            (_ts(2024, 1, 15, 0, 10), 1.0),  # doppelt gemeldet
            (_ts(2024, 1, 15, 0, 20), 0.0),
        ])
        now = datetime(2024, 1, 15, 12, 0, tzinfo=TZ)
        result = query.query_series(tmp, index, entity_id, "day", TZ, now, chart_type="bar")
        assert [p["count"] for p in result["points"]] == [0]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_switch_points_have_no_count_key() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-count-api-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")
        hotbuffer.append(tmp, "sensor.temp", _ts(2024, 1, 15, 1), 5.0, TZ)
        index.record_write("sensor.temp", _ts(2024, 1, 15, 1))
        result = query.query_series(tmp, index, "sensor.temp", "day", TZ, datetime(2024, 1, 15, 12, tzinfo=TZ))
        assert all("count" not in point for point in result["points"])
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_table_count_aggregates() -> None:
    points = [{"count": 2}, {"count": 5}, {"count": 0}]
    assert api_routes._count_aggregates(points) == {
        "count_last": 0, "count_avg": 7 / 3, "count_min": 0, "count_max": 5, "count_sum": 7,
    }


def test_one_unknown_count_makes_sum_and_average_unknown() -> None:
    aggregates = api_routes._count_aggregates([{"count": 2}, {"count": None}, {"count": 5}])
    assert aggregates["count_sum"] is None and aggregates["count_avg"] is None
    assert (aggregates["count_min"], aggregates["count_max"], aggregates["count_last"]) == (2, 5, 5)


def test_no_points_gives_no_counts() -> None:
    assert set(api_routes._count_aggregates([]).values()) == {None}


def test_table_aggregates_add_counts_only_for_switches() -> None:
    switch = {"aggregation_type": "switch", "points": [{"value": 60.0, "min": None, "max": None, "count": 3}]}
    counter = {"aggregation_type": "counter", "points": [{"value": 6.0, "min": None, "max": None}]}
    assert api_routes._table_aggregates(switch)["count_sum"] == 3
    assert not any(key.startswith("count_") for key in api_routes._table_aggregates(counter))


def test_count_display_modes_are_valid_and_offered_in_the_form() -> None:
    assert list(formatting.DISPLAY_MODE_LABELS) == ["onoff", "time", "count", "both"]
