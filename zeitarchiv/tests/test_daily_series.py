"""query_daily_series(): Tageswerte für das Wetterdashboard (ROADMAP 1.43, Phase 1.5)."""

from __future__ import annotations

import calendar
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.storage import hotbuffer, query, rollup
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 2, 12, tzinfo=TZ)


@pytest.fixture()
def env():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-daily-test-"))
    index = Index(tmp / "index.sqlite")
    query._DAILY_YEAR_CACHE.clear()
    yield tmp, index
    index.close()
    query._DAILY_YEAR_CACHE.clear()
    shutil.rmtree(tmp, ignore_errors=True)


def _write_year(tmp: Path, entity_id: str, year: int, hour_value, *, months=range(1, 13), level="stunde") -> None:
    """Schreibt Monatssegmente wie die Rotation: stündlich (Messwert) bzw. täglich (Zähler)."""
    for month in months:
        start = datetime(year, month, 1, tzinfo=TZ)
        count = calendar.monthrange(year, month)[1]
        if level == "stunde":
            rows = [
                rollup.FineRow(
                    bucket_start=(start + timedelta(hours=hour)).timestamp(),
                    value=hour_value(hour), min_value=hour_value(hour) - 1, max_value=hour_value(hour) + 1,
                )
                for hour in range(count * 24)
            ]
            table = rollup._fine_rows_to_table(rows, "standard")
        else:
            rows = [
                rollup.FineRow(bucket_start=(start + timedelta(days=day)).timestamp(), value=hour_value(day))
                for day in range(count)
            ]
            table = rollup._fine_rows_to_table(rows, "counter")
        rollup._append_table(rollup.rollup_path(tmp, entity_id, level), table, f"{year:04d}-{month:02d}")


def test_measurement_days_aggregate_hours_and_flag_thin_days(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    start = datetime(2026, 10, 1, tzinfo=TZ)
    rows = []
    for day in range(31):
        # 1. Oktober: nur 17 Stunden Werte (zu dünn); alle anderen Tage voll. Wert = Tagesnummer + Stunde/100.
        hours = 17 if day == 0 else 24
        for hour in range(hours):
            ts = (start + timedelta(days=day, hours=hour)).timestamp()
            rows.append(rollup.FineRow(bucket_start=ts, value=day + hour / 100, min_value=day - 1.0, max_value=day + 1.0))
    rollup._append_table(rollup.rollup_path(tmp, "sensor.t", "stunde"), rollup._fine_rows_to_table(rows, "standard"), "2026-10")

    points = query.query_daily_series(tmp, index, "sensor.t", start, datetime(2026, 11, 1, tzinfo=TZ), TZ, NOW)
    assert len(points) == 31
    first, third = points[0], points[2]
    assert (first.hours, first.covered) == (17, False)
    assert third.day_start == datetime(2026, 10, 3, tzinfo=TZ).timestamp()
    assert (third.hours, third.covered) == (24, True)
    assert third.value == pytest.approx(2 + sum(range(24)) / 24 / 100)
    assert (third.min_value, third.max_value) == (1.0, 3.0)


def test_exactly_the_minimum_number_of_hours_counts_as_covered(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    start = datetime(2026, 10, 1, tzinfo=TZ)
    rows = [
        rollup.FineRow(bucket_start=(start + timedelta(hours=hour)).timestamp(), value=5.0, min_value=5.0, max_value=5.0)
        for hour in range(query.DAILY_MIN_HOURS)
    ]
    rollup._append_table(rollup.rollup_path(tmp, "sensor.t", "stunde"), rollup._fine_rows_to_table(rows, "standard"), "2026-10")
    (point,) = query.query_daily_series(tmp, index, "sensor.t", start, datetime(2026, 10, 2, tzinfo=TZ), TZ, NOW)
    assert (point.hours, point.covered) == (query.DAILY_MIN_HOURS, True)


def test_days_without_rows_are_missing_not_zero(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    ts = datetime(2026, 10, 5, 10, tzinfo=TZ).timestamp()
    rollup._append_table(
        rollup.rollup_path(tmp, "sensor.t", "stunde"),
        rollup._fine_rows_to_table([rollup.FineRow(bucket_start=ts, value=3.0, min_value=3.0, max_value=3.0)], "standard"),
        "2026-10",
    )
    points = query.query_daily_series(tmp, index, "sensor.t", datetime(2026, 10, 1, tzinfo=TZ), datetime(2026, 11, 1, tzinfo=TZ), TZ, NOW)
    assert [datetime.fromtimestamp(p.day_start, TZ).day for p in points] == [5]


def test_counter_returns_daily_sums_and_the_running_month_from_the_hot_buffer(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.r", "sensor", "total_increasing", "mm")
    _write_year(tmp, "sensor.r", 2025, lambda day: float(day % 3), level="tag")
    for ts, value in ((datetime(2026, 11, 1, 6, tzinfo=TZ), 10.0), (datetime(2026, 11, 2, 6, tzinfo=TZ), 12.5)):
        hotbuffer.append(tmp, "sensor.r", ts.timestamp(), value, TZ)
        index.record_write("sensor.r", ts.timestamp(), value)
    points = query.query_daily_series(tmp, index, "sensor.r", datetime(2025, 12, 30, tzinfo=TZ), NOW, TZ, NOW)
    by_day = {datetime.fromtimestamp(p.day_start, TZ).date().isoformat(): p.value for p in points}
    assert by_day["2025-12-30"] == 29 % 3 and by_day["2025-12-31"] == 30 % 3
    assert by_day["2026-11-02"] == pytest.approx(2.5)
    assert all(p.covered for p in points)


def test_switch_returns_seconds_on_per_day(env) -> None:
    tmp, index = env
    index.get_or_create_entity("binary_sensor.regen", "binary_sensor", None, "")
    start = datetime(2026, 10, 10, tzinfo=TZ)
    rows = [rollup.FineRow(bucket_start=(start + timedelta(hours=hour)).timestamp(), on_seconds=60.0 if hour == 7 else 0.0) for hour in range(24)]
    rollup._append_table(rollup.rollup_path(tmp, "binary_sensor.regen", "stunde"), rollup._fine_rows_to_table(rows, "switch"), "2026-10")
    (point,) = query.query_daily_series(tmp, index, "binary_sensor.regen", start, datetime(2026, 10, 11, tzinfo=TZ), TZ, NOW)
    assert point.value == 60.0 and point.covered and point.hours == 24


def test_unknown_and_state_entities_and_empty_windows_give_nothing(env) -> None:
    tmp, index = env
    window = (datetime(2026, 10, 1, tzinfo=TZ), datetime(2026, 11, 1, tzinfo=TZ))
    assert query.query_daily_series(tmp, index, "sensor.gibt_es_nicht", *window, TZ, NOW) == []
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    assert query.query_daily_series(tmp, index, "sensor.t", window[1], window[0], TZ, NOW) == []


def _calls(monkeypatch) -> list[tuple[datetime, datetime]]:
    calls: list[tuple[datetime, datetime]] = []
    original = query._daily_points

    def counting(data_dir, index, entity_id, aggregation_type, start, end, *rest):
        calls.append((start, end))
        return original(data_dir, index, entity_id, aggregation_type, start, end, *rest)

    monkeypatch.setattr(query, "_daily_points", counting)
    return calls


def test_completed_years_are_cached_and_the_current_year_is_not(env, monkeypatch) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    _write_year(tmp, "sensor.t", 2024, lambda hour: 10.0)
    _write_year(tmp, "sensor.t", 2025, lambda hour: 12.0)
    calls = _calls(monkeypatch)
    window = (datetime(2024, 1, 1, tzinfo=TZ), NOW)
    first = query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert len(calls) == 3  # 2024, 2025, laufendes Jahr
    calls.clear()
    second = query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert second == first and [start.year for start, _end in calls] == [2026]
    assert len(first) == 366 + 365  # das laufende Jahr hat keine Daten


def test_partial_years_and_years_without_december_are_not_cached(env, monkeypatch) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    _write_year(tmp, "sensor.t", 2024, lambda hour: 10.0, months=range(1, 12))  # Dezember fehlt (noch im Hot Buffer)
    _write_year(tmp, "sensor.t", 2025, lambda hour: 12.0)
    calls = _calls(monkeypatch)
    query.query_daily_series(tmp, index, "sensor.t", datetime(2024, 1, 1, tzinfo=TZ), datetime(2026, 1, 1, tzinfo=TZ), TZ, NOW)
    calls.clear()
    query.query_daily_series(tmp, index, "sensor.t", datetime(2024, 1, 1, tzinfo=TZ), datetime(2026, 1, 1, tzinfo=TZ), TZ, NOW)
    assert [start.year for start, _end in calls] == [2024]  # 2025 aus dem Cache, 2024 jedes Mal frisch
    calls.clear()
    query.query_daily_series(tmp, index, "sensor.t", datetime(2025, 3, 1, tzinfo=TZ), datetime(2025, 4, 1, tzinfo=TZ), TZ, NOW)
    query.query_daily_series(tmp, index, "sensor.t", datetime(2025, 3, 1, tzinfo=TZ), datetime(2025, 4, 1, tzinfo=TZ), TZ, NOW)
    assert len(calls) == 2  # ein angeschnittenes Jahr wird nie zwischengespeichert


def test_rewriting_a_segment_invalidates_the_cached_year(env, monkeypatch) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    _write_year(tmp, "sensor.t", 2025, lambda hour: 12.0)
    window = (datetime(2025, 1, 1, tzinfo=TZ), datetime(2026, 1, 1, tzinfo=TZ))
    before = query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert before[10].value == 12.0
    time.sleep(0.02)
    # Wie ein Purge oder eine Neuberechnung (replace_month): derselbe Schlüssel wird ersetzt.
    _write_year(tmp, "sensor.t", 2025, lambda hour: 20.0, months=[1])
    after = query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert after[10].value == 20.0 and after[40].value == 12.0


def test_cache_is_bounded(env, monkeypatch) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    _write_year(tmp, "sensor.t", 2025, lambda hour: 12.0)
    monkeypatch.setattr(query, "_DAILY_YEAR_CACHE_MAX", 2)
    for name in ("a", "b", "c"):
        query._DAILY_YEAR_CACHE[("x", name, 1, "tz")] = ((), [])
    query.query_daily_series(tmp, index, "sensor.t", datetime(2025, 1, 1, tzinfo=TZ), datetime(2026, 1, 1, tzinfo=TZ), TZ, NOW)
    assert len(query._DAILY_YEAR_CACHE) == 2


def test_ten_years_of_hourly_data_are_one_cheap_call_once_cached(env, monkeypatch) -> None:
    """Zehn Jahre sind etwa 87.600 Stundenzeilen. Gemessen auf dem Entwicklungsrechner: kalt rund
    0,4 s, aus dem Cache rund 0,06 s. Die Schranke ist bewusst großzügig (langsame Hardware, CI)."""
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    for year in range(2016, 2026):
        _write_year(tmp, "sensor.t", year, lambda hour: 10.0 + (hour % 24) / 24)
    calls = _calls(monkeypatch)
    window = (datetime(2016, 1, 1, tzinfo=TZ), NOW)
    began = time.perf_counter()
    cold = query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert time.perf_counter() - began < 10.0
    assert len(cold) == 3653 and all(point.covered for point in cold)
    calls.clear()
    assert query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW) == cold
    assert [start.year for start, _end in calls] == [2026]
