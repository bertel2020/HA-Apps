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
        # 1. Oktober: nur 5 Stunden Werte; mit der Fortschreibung (12 h) bleiben 17 Stunden, das ist zu dünn.
        # Alle anderen Tage voll. Wert = Tagesnummer + Stunde/100.
        hours = 5 if day == 0 else 24
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
    # Sechs Stunden mit Wert plus zwölf fortgeschriebene ergeben genau die Mindestzahl.
    real = query.DAILY_MIN_HOURS - query.DAILY_CARRY_HOURS
    rows = [
        rollup.FineRow(bucket_start=(start + timedelta(hours=hour)).timestamp(), value=5.0, min_value=5.0, max_value=5.0)
        for hour in range(real)
    ]
    rollup._append_table(rollup.rollup_path(tmp, "sensor.t", "stunde"), rollup._fine_rows_to_table(rows, "standard"), "2026-10")
    (point,) = query.query_daily_series(tmp, index, "sensor.t", start, datetime(2026, 10, 2, tzinfo=TZ), TZ, NOW)
    assert (point.hours, point.covered) == (query.DAILY_MIN_HOURS, True)


def _write_hours(tmp: Path, hours: list[tuple[datetime, float]]) -> None:
    rows = [rollup.FineRow(bucket_start=ts.timestamp(), value=v, min_value=v, max_value=v) for ts, v in hours]
    rollup._append_table(rollup.rollup_path(tmp, "sensor.t", "stunde"), rollup._fine_rows_to_table(rows, "standard"), "2026-10")


def test_quiet_hours_carry_the_last_value_forward(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    day = datetime(2026, 10, 4, tzinfo=TZ)
    # Wie ein Frosttag mit ganzen Graden: nur Änderungen sind gespeichert, dazwischen stundenlang Stille.
    _write_hours(tmp, [(day.replace(hour=h), v) for h, v in ((3, 0.0), (4, 1.0), (8, 2.0), (16, 1.0), (21, -1.0), (23, 0.0))])
    (point,) = query.query_daily_series(tmp, index, "sensor.t", day, day + timedelta(days=1), TZ, NOW)
    assert point.covered and point.hours == 21  # Stunden 3 bis 23
    # 3:0 | 4-7:1 | 8-15:2 | 16-20:1 | 21-22:-1 | 23:0, Stunden 0 bis 2 bleiben leer
    assert point.value == pytest.approx((0 + 4 * 1 + 8 * 2 + 5 * 1 + 2 * -1 + 0) / 21)
    assert (point.min_value, point.max_value) == (-1.0, 2.0)


def test_carry_forward_stops_after_twelve_hours_and_never_bridges_an_outage(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    start = datetime(2026, 10, 4, 6, tzinfo=TZ)
    _write_hours(tmp, [(start, 3.0), (start + timedelta(days=2), 4.0)])  # zwei Tage Ausfall dazwischen
    points = query.query_daily_series(tmp, index, "sensor.t", datetime(2026, 10, 4, tzinfo=TZ), datetime(2026, 10, 8, tzinfo=TZ), TZ, NOW)
    by_day = {datetime.fromtimestamp(p.day_start, TZ).day: p for p in points}
    assert sorted(by_day) == [4, 6]  # der 5. bleibt leer: 6 Uhr + 12 h endet am 4. um 18 Uhr
    assert by_day[4].hours == 13 and not by_day[4].covered  # 6 bis 18 Uhr
    assert by_day[6].hours == 13 and by_day[6].value == pytest.approx(3.0 * 0 + 4.0)


def test_carry_forward_does_not_run_past_the_current_hour(env) -> None:
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    now = datetime(2026, 10, 4, 9, 30, tzinfo=TZ)
    ts = datetime(2026, 10, 4, 8, 10, tzinfo=TZ)
    hotbuffer.append(tmp, "sensor.t", ts.timestamp(), 2.0, TZ)  # laufender Monat: kommt aus dem Hot Buffer
    index.record_write("sensor.t", ts.timestamp(), 2.0)
    (point,) = query.query_daily_series(tmp, index, "sensor.t", datetime(2026, 10, 4, tzinfo=TZ), datetime(2026, 10, 5, tzinfo=TZ), TZ, now)
    assert point.hours == 2  # 8 und 9 Uhr, nicht die Zukunft


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
    # Das laufende Jahr hat keine Daten; nur der 1. Januar 2026 erscheint, rein aus dem Wert vom Silvesterabend fortgeschrieben
    # (elf Stunden, nie abgedeckt) wie jeder andere Tag nach der letzten Zeile auch.
    assert len(first) == 366 + 365 + 1 and not first[-1].covered


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
    assert len(cold) == 3653 + 1 and sum(point.covered for point in cold) == 3653  # plus 1.1.2026, nur fortgeschrieben
    calls.clear()
    assert query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW) == cold
    assert [start.year for start, _end in calls] == [2026]


def _write_changes(tmp: Path, entity_id: str, changes: list[tuple[datetime, float]]) -> None:
    """Stundenzeilen nur zu den Änderungszeitpunkten (wie gespeichert), je Monatssegment."""
    by_month: dict[str, list[rollup.FineRow]] = {}
    for ts, value in changes:
        by_month.setdefault(f"{ts.year:04d}-{ts.month:02d}", []).append(
            rollup.FineRow(bucket_start=ts.timestamp(), value=value, min_value=value, max_value=value)
        )
    for label in sorted(by_month):
        table = rollup._fine_rows_to_table(by_month[label], "standard")
        rollup._append_table(rollup.rollup_path(tmp, entity_id, "stunde"), table, label)


def _by_day(points) -> dict:
    return {
        datetime.fromtimestamp(p.day_start, TZ).date(): (p.hours, p.covered, round(p.value, 6), p.min_value, p.max_value)
        for p in points
    }


@pytest.mark.parametrize(
    "changes",
    [
        # Mitten im Monat: seit 22:00 am Vortag 0 °C, erste Änderung um 09:00.
        [(datetime(2026, 10, 14, 22), 0.0), (datetime(2026, 10, 15, 9), 1.0), (datetime(2026, 10, 15, 20), 0.5)],
        # Monatswechsel (zwei Segmente): Ruhe von 20:00 am 30.9. bis 08:00 am 1.10.
        [(datetime(2026, 9, 30, 20), -1.0), (datetime(2026, 10, 1, 8), 2.0), (datetime(2026, 10, 1, 18), 1.0)],
        # Jahreswechsel: das abgeschlossene Jahr 2025 liegt im Zwischenspeicher, der 1.1. hängt vom Dezember ab.
        [(datetime(2025, 12, 31, 21), 0.0), (datetime(2026, 1, 1, 7), 1.0), (datetime(2026, 1, 1, 19), 0.0)],
    ],
    ids=["tagesgrenze", "monatsgrenze", "jahresgrenze"],
)
def test_a_day_has_the_same_values_whichever_day_the_window_starts_on(env, changes) -> None:
    """Ein Wert, der seit dem Vorabend gilt, zählt auch für die Stunden am Tagesanfang, wenn das Fenster genau an diesem
    Tag beginnt (bisher fehlten dann die Stunden vor der ersten Änderung, der Tag galt als nicht abgedeckt)."""
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    changes = [(ts.replace(tzinfo=TZ), value) for ts, value in changes]
    _write_changes(tmp, "sensor.t", changes)
    first_day = changes[0][0].replace(hour=0, minute=0)
    last_day = changes[-1][0].replace(hour=0, minute=0) + timedelta(days=1)
    whole = _by_day(query.query_daily_series(tmp, index, "sensor.t", first_day, last_day + timedelta(days=1), TZ, NOW))
    assert len(whole) >= 2
    for day, expected in whole.items():
        start = datetime(day.year, day.month, day.day, tzinfo=TZ)
        alone = _by_day(query.query_daily_series(tmp, index, "sensor.t", start, start + timedelta(days=1), TZ, NOW))
        assert alone == {day: expected}, f"{day}: {alone.get(day)} statt {expected}"
    # Das Fenster ab dem zweiten Tag sieht den Wert des Vorabends und füllt die Stunden am Tagesanfang.
    second = sorted(whole)[1]
    assert whole[second][0] > 12


def test_nothing_is_carried_in_from_before_the_first_value(env) -> None:
    """Vor dem ersten gespeicherten Wert gibt es nichts, was fortgeschrieben werden könnte."""
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    _write_changes(tmp, "sensor.t", [(datetime(2026, 10, 15, 9, tzinfo=TZ), 1.0)])
    day = datetime(2026, 10, 15, tzinfo=TZ)
    (point,) = query.query_daily_series(tmp, index, "sensor.t", day, day + timedelta(days=1), TZ, NOW)
    assert point.hours == 13 and point.min_value == 1.0  # 9 Uhr plus zwölf fortgeschriebene Stunden, nichts davor


def test_the_cached_year_follows_the_december_before_it(env, monkeypatch) -> None:
    """Der 1. Januar hängt vom Dezember des Vorjahres ab; ändert sich dessen Segment, gilt der Zwischenspeicher nicht mehr."""
    tmp, index = env
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    _write_year(tmp, "sensor.t", 2025, lambda hour: 10.0)
    _write_year(tmp, "sensor.t", 2024, lambda hour: 10.0)
    calls = _calls(monkeypatch)
    window = (datetime(2025, 1, 1, tzinfo=TZ), datetime(2026, 1, 1, tzinfo=TZ))
    query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    calls.clear()
    query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert calls == []  # unverändert: aus dem Zwischenspeicher
    segment = rollup.rollup_path(tmp, "sensor.t", "stunde") / rollup.segment_name(2024, 12)
    segment.touch()
    time.sleep(0.01)
    segment.write_bytes(segment.read_bytes())  # neue Änderungszeit: der Dezember des Vorjahres wurde neu geschrieben
    query.query_daily_series(tmp, index, "sensor.t", *window, TZ, NOW)
    assert [start.year for start, _end in calls] == [2025]
