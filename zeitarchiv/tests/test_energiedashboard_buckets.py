"""Bucket-Achse für die Kachel-Popups des Energiedashboards (energiedashboard_buckets.py).

Die Fälle, an denen ein Index als Achse scheitert: der 25-Stunden-Tag am Ende der Sommerzeit, der
23-Stunden-Tag an ihrem Beginn, Monate unterschiedlicher Länge, und die Serie des laufenden Zeitraums,
die grobe und feine Buckets mischt."""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from _paths import APP  # noqa: F401  (legt den Importpfad)

from app import energiedashboard_buckets as eb

TZ = ZoneInfo("Europe/Berlin")


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return _dt.datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp()


def _hourly(year: int, month: int, day: int, count: int, value: float = 1.0) -> dict[float, float]:
    """``count`` Stunden-Buckets ab lokaler Mitternacht, in absoluten Stunden (wie die Abfrage)."""
    start = _ts(year, month, day)
    return {start + 3600 * i: value for i in range(count)}


def test_keys_follow_the_calendar_not_the_position() -> None:
    assert eb.bucket_key(_ts(2026, 11, 1, 13, 5), "hour", TZ) == 5
    assert eb.bucket_key(_ts(2026, 11, 1, 13), "day", TZ) == 13
    assert eb.bucket_key(_ts(2026, 11, 1), "week", TZ) == 6  # Sonntag
    assert eb.bucket_key(_ts(2026, 11, 17), "month", TZ) == 17
    assert eb.bucket_key(_ts(2026, 11, 17), "year", TZ) == 11
    assert eb.bucket_key(_ts(2026, 11, 17), "decade", TZ) == 6
    with pytest.raises(ValueError):
        eb.bucket_key(0.0, "century", TZ)


@pytest.mark.parametrize("year, month, days", [(2026, 2, 28), (2028, 2, 29), (2026, 4, 30), (2026, 10, 31)])
def test_month_axis_has_the_real_length(year: int, month: int, days: int) -> None:
    keys = eb.axis_keys("month", _dt.datetime(year, month, 1, tzinfo=TZ))
    assert keys == list(range(1, days + 1))


def test_fixed_axes() -> None:
    start = _dt.datetime(2026, 11, 1, tzinfo=TZ)
    assert eb.axis_keys("hour", start) == list(range(0, 60, 5))
    assert eb.axis_keys("day", start) == list(range(24))
    assert eb.axis_keys("week", start) == list(range(7))
    assert eb.axis_keys("year", start) == list(range(1, 13))
    assert eb.axis_keys("decade", start) == list(range(10))


def test_the_25_hour_day_folds_the_doubled_hour_and_keeps_the_energy() -> None:
    """Am 25.10.2026 (Ende der Sommerzeit) gibt es zwei Buckets mit lokaler Stunde 2. Beide
    gehören auf eine Achsenposition; die Tagessumme bleibt erhalten."""
    series = _hourly(2026, 10, 25, 25)
    assert len(series) == 25
    folded = eb.fold_series(series, "day", TZ)
    assert sorted(folded) == list(range(24))
    assert folded[2] == 2.0
    assert folded[3] == 1.0
    assert sum(folded.values()) == sum(series.values()) == 25.0


def test_the_23_hour_day_leaves_the_missing_hour_empty() -> None:
    """Am 29.03.2026 (Beginn der Sommerzeit) fehlt die lokale Stunde 2: ohne Wert, nicht 0."""
    series = _hourly(2026, 3, 29, 23)
    result = eb.align(series, {}, "day", _dt.datetime(2026, 3, 29, tzinfo=TZ), TZ)
    assert result["keys"] == list(range(24))
    assert result["current"][2] is None
    assert result["current"][1] == 1.0 and result["current"][3] == 1.0
    assert sum(v for v in result["current"] if v is not None) == 23.0


def test_the_running_month_folds_the_fine_tail_into_its_day() -> None:
    """Der laufende Monat liefert Tages-Buckets bis gestern und Stunden-Buckets für heute (siehe
    _FINER_RANGE). Die Stunden von heute werden zu EINEM Tageswert addiert."""
    series: dict[float, float] = {_ts(2026, 11, 1): 2.0, _ts(2026, 11, 2): 3.0}
    today = {_ts(2026, 11, 3, h): 0.5 for h in range(6)}
    original = dict(series) | today
    result = eb.align(original, {}, "month", _dt.datetime(2026, 11, 1, tzinfo=TZ), TZ)
    assert result["current"][:4] == [2.0, 3.0, 3.0, None]
    assert len(result["keys"]) == 30
    assert sum(v for v in result["current"] if v is not None) == pytest.approx(8.0)
    assert original == dict(series) | today  # Eingabe unverändert


def test_the_running_year_folds_days_into_the_current_month() -> None:
    series: dict[float, float] = {_ts(2026, 9, 1): 100.0, _ts(2026, 10, 1): 120.0}
    series.update({_ts(2026, 11, d): 1.0 for d in range(1, 4)})
    result = eb.align(series, {}, "year", _dt.datetime(2026, 1, 1, tzinfo=TZ), TZ)
    assert result["current"][8:11] == [100.0, 120.0, 3.0]
    assert result["current"][0] is None and result["current"][11] is None


def test_previous_period_is_aligned_by_calendar_key() -> None:
    """Oktober (31 Tage) gegen November (30): der 31. der Vorperiode erscheint nicht in der
    Achse des laufenden Novembers; der 1. liegt an derselben Position wie der 1."""
    current = {_ts(2026, 11, 1): 4.0, _ts(2026, 11, 2): 5.0}
    previous = {_ts(2026, 10, d): float(d) for d in range(1, 32)}
    result = eb.align(current, previous, "month", _dt.datetime(2026, 11, 1, tzinfo=TZ), TZ)
    assert len(result["keys"]) == 30
    assert result["previous"][0] == 1.0 and result["previous"][29] == 30.0
    assert 31.0 not in result["previous"]
    assert result["current"][:3] == [4.0, 5.0, None]


def test_previous_day_is_aligned_by_hour_across_a_dst_change() -> None:
    """Gestern war der 25-Stunden-Tag, heute ein normaler: Stunde 3 steht bei beiden an Position 3."""
    previous = _hourly(2026, 10, 25, 25)
    current = {_ts(2026, 10, 26, h): 2.0 for h in range(5)}
    result = eb.align(current, previous, "day", _dt.datetime(2026, 10, 26, tzinfo=TZ), TZ)
    assert result["previous"][2] == 2.0 and result["previous"][3] == 1.0
    assert result["current"][3] == 2.0 and result["current"][5] is None


def test_a_measured_zero_is_not_an_empty_bucket() -> None:
    result = eb.align({_ts(2026, 11, 1, 0): 0.0}, {}, "day", _dt.datetime(2026, 11, 1, tzinfo=TZ), TZ)
    assert result["current"][0] == 0.0
    assert result["current"][1] is None


def test_none_values_count_as_zero_when_folding() -> None:
    assert eb.fold_series({_ts(2026, 11, 1): None}, "month", TZ) == {1: 0.0}  # type: ignore[dict-item]
