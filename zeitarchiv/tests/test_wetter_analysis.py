"""Auswertungen des Wetterdashboards über Tageswerte (ROADMAP 1.43, Phase 2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import wetter_analysis as wa
from app.storage.query import DailyPoint

TZ = ZoneInfo("Europe/Berlin")


def _point(day: date, value=10.0, low=5.0, high=15.0, covered=True) -> DailyPoint:
    return DailyPoint(
        day_start=datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp(),
        value=value, min_value=low, max_value=high, hours=24 if covered else 10, covered=covered,
    )


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def test_leap_day_counts_to_the_28th() -> None:
    assert wa.slot_of(date(2024, 2, 29)) == wa.slot_of(date(2024, 2, 28)) == wa.slot_of(date(2025, 2, 28))
    assert wa.slot_of(date(2024, 3, 1)) == wa.slot_of(date(2025, 3, 1))
    assert wa.slot_of(date(2024, 12, 31)) == 364


def test_cumulative_holds_over_gaps_and_stops_at_today() -> None:
    daily = {date(2026, 1, 1): 2.0, date(2026, 1, 4): 3.0, date(2026, 1, 10): 1.0}
    result = wa.precipitation(daily, date(2026, 1, 6), wet_threshold=1.0)
    cumulative = result["cumulative"]
    assert cumulative[0] == 2.0 and cumulative[2] == 2.0 and cumulative[3] == 5.0  # Lücke hält den Stand
    assert cumulative[5] == 5.0 and cumulative[6] is None  # nie in die Zukunft, 10.1. liegt hinter heute
    assert result["total"] == 5.0


def test_leap_day_rain_is_added_to_the_28th() -> None:
    daily = {date(2024, 2, 28): 1.0, date(2024, 2, 29): 2.0}
    result = wa.precipitation(daily, date(2025, 6, 1), 1.0)
    assert result["previous"][wa.slot_of(date(2024, 2, 28))] == 3.0


def _years(rain_by_year: dict[int, float]) -> dict[date, float]:
    daily: dict[date, float] = {}
    for year, per_day in rain_by_year.items():
        for day in _days(date(year, 1, 1), date(year, 12, 31)):
            daily[day] = per_day
    return daily


def test_average_and_band_use_only_complete_previous_years() -> None:
    daily = _years({2023: 1.0, 2024: 3.0, 2025: 2.0})
    daily.update({day: 9.0 for day in _days(date(2022, 12, 1), date(2022, 12, 31))})  # angeschnittenes Jahr
    today = date(2026, 3, 1)
    daily.update({day: 0.5 for day in _days(date(2026, 1, 1), date(2026, 3, 1))})
    result = wa.precipitation(daily, today, 1.0)
    assert result["avg_years"] == [2023, 2024, 2025]
    slot = wa.slot_of(date(2026, 3, 1))
    # 2024 ist ein Schaltjahr: sein 29.02. (3 mm) zählt zum 28.02. und steckt damit schon im 1. März.
    assert result["avg"][slot] == pytest.approx(((1 + 3 + 2) * (slot + 1) + 3) / 3)
    assert (result["low"][slot], result["high"][slot]) == (pytest.approx(slot + 1), pytest.approx(3 * (slot + 1) + 3))
    assert result["previous_year"] == 2025 and result["previous_to_date"] == pytest.approx(2 * (slot + 1))
    assert result["avg_to_date"] == pytest.approx(((1 + 3 + 2) * (slot + 1) + 3) / 3)
    assert result["total"] == pytest.approx(0.5 * (slot + 1))


def test_older_years_are_capped_but_average_and_band_use_all_complete_years() -> None:
    daily = _years({year: float(year - 2018) for year in range(2019, 2026)})  # 2019 bis 2025, sieben Jahre
    daily[date(2026, 1, 1)] = 1.0
    result = wa.precipitation(daily, date(2026, 1, 2), 1.0)
    assert result["avg_years"] == list(range(2019, 2026))  # alle sieben im Mittel
    # Gezeigt: das Vorjahr (eigene Linie) plus vier ältere = fünf frühere Jahre.
    assert result["previous_year"] == 2025
    assert sorted(result["older"]) == ["2021", "2022", "2023", "2024"]
    assert result["older"]["2024"][0] == pytest.approx(6.0)


def test_average_needs_two_years_for_the_comparison() -> None:
    result = wa.precipitation(_years({2025: 2.0}) | {date(2026, 1, 1): 1.0}, date(2026, 1, 2), 1.0)
    assert result["avg_to_date"] is None and result["avg_years"] == [2025]


def test_wet_days_wettest_day_and_dry_spells() -> None:
    values = [0.0, 0.2, 5.0, 0.0, 0.0, 0.0, 2.0, 0.0]
    daily = {date(2026, 4, 1 + i): v for i, v in enumerate(values)}
    result = wa.precipitation(daily, date(2026, 4, 9), 1.0)
    assert result["wet_days"] == 2
    assert result["wettest"] == {"value": 5.0, "date": "2026-04-03"}
    assert result["longest_dry_days"] == 3 and result["longest_dry_end"] == "2026-04-06"
    assert result["current_dry_days"] == 1  # der 8.4. war trocken, heute (9.4.) fehlt noch


def test_a_missing_day_breaks_a_dry_spell() -> None:
    daily = {date(2026, 4, 1): 0.0, date(2026, 4, 2): 0.0, date(2026, 4, 4): 0.0}  # der 3.4. fehlt
    assert wa.precipitation(daily, date(2026, 4, 5), 1.0)["longest_dry_days"] == 2


def test_no_rain_data_gives_an_empty_series() -> None:
    result = wa.precipitation({}, date(2026, 4, 5), 1.0)
    assert result["total"] is None and result["wettest"] is None and result["previous"] is None


def test_monthly_mean_uses_covered_days_and_needs_coverage() -> None:
    points = [_point(day, value=10.0) for day in _days(date(2025, 1, 1), date(2025, 1, 31))]
    points += [_point(day, value=1.0) for day in _days(date(2025, 2, 1), date(2025, 2, 5))]  # zu wenige Tage
    points += [_point(date(2025, 3, d), value=0.0, covered=False) for d in range(1, 29)]  # nicht abgedeckt
    result = wa.monthly(points, TZ, date(2026, 5, 1), "mean")
    assert result["years"]["2025"][0] == 10.0
    assert result["years"]["2025"][1] is None and result["years"]["2025"][2] is None


def test_monthly_sum_and_band_over_past_years_exclude_the_running_one() -> None:
    points = []
    for year, per_day in ((2024, 1.0), (2025, 3.0)):
        points += [_point(day, value=per_day) for day in _days(date(year, 1, 1), date(year, 1, 31))]
    points += [_point(day, value=2.0) for day in _days(date(2026, 1, 1), date(2026, 1, 10))]
    result = wa.monthly(points, TZ, date(2026, 1, 10), "sum")
    assert result["years"]["2026"][0] == 20.0  # laufender Monat zeigt den Teilwert
    assert result["avg"][0] == 62.0 and (result["low"][0], result["high"][0]) == (31.0, 93.0)
    assert result["counts"][0] == 2


def test_headline_compares_the_same_stretch_with_earlier_years() -> None:
    points = []
    for year, value in ((2023, 6.0), (2024, 8.0), (2025, 7.0), (2026, 4.0)):
        points += [_point(day, value=value) for day in _days(date(year, 11, 1), date(year, 11, 30))]
    result = wa.headline(points, TZ, date(2026, 11, 3))
    assert (result["month"], result["last_day"], result["n_years"]) == (11, 2, 3)
    assert result["current"] == 4.0 and result["reference"] == 7.0
    assert (result["first_year"], result["last_year"]) == (2023, 2025)


def test_headline_needs_two_earlier_years_and_handles_the_first_of_the_month() -> None:
    one_year = [_point(day, value=5.0) for year in (2025, 2026) for day in _days(date(year, 11, 1), date(year, 11, 30))]
    assert wa.headline(one_year, TZ, date(2026, 11, 3)) is None
    points = [_point(day) for year in (2024, 2025, 2026) for day in _days(date(year, 10, 1), date(year, 10, 31))]
    result = wa.headline(points, TZ, date(2026, 11, 1))
    assert (result["month"], result["last_day"]) == (10, 31)  # am Monatsersten zählt der ganze Vormonat


def test_kenntage_use_dwd_thresholds_and_skip_today_and_thin_days() -> None:
    today = date(2026, 7, 10)
    points = [
        _point(date(2026, 1, 1), low=-0.1, high=2.0),  # Frosttag
        _point(date(2026, 1, 2), low=-5.0, high=-0.1),  # Eistag, zugleich Frosttag
        _point(date(2026, 1, 3), low=0.0, high=3.0),  # 0,0 ist kein Frost
        _point(date(2026, 7, 1), low=15.0, high=25.0),  # Sommertag (ab 25)
        _point(date(2026, 7, 2), low=18.0, high=30.0),  # heißer Tag, zugleich Sommertag
        _point(date(2026, 7, 3), low=18.0, high=24.9),
        _point(date(2026, 7, 4), low=-9.0, high=40.0, covered=False),  # zu dünn: zählt nicht
        _point(today, low=-9.0, high=40.0),  # laufender Tag: zählt nie
    ]
    year = wa.kenntage(points, TZ, today)["2026"]
    assert year["frost"][0] == 2 and year["eis"][0] == 1
    assert year["sommer"][6] == 2 and year["heiss"][6] == 1
    assert year["days"][0] == 3 and year["days"][6] == 3


def test_count_kenntage_over_a_window() -> None:
    today = date(2026, 3, 1)
    points = [_point(date(2026, 2, d), low=-1.0, high=4.0) for d in range(1, 11)]
    start = datetime(2026, 2, 1, tzinfo=TZ).timestamp()
    end = datetime(2026, 3, 1, tzinfo=TZ).timestamp()
    assert wa.count_kenntage(points, TZ, start, end, today) == {"frost": 10, "eis": 0, "sommer": 0, "heiss": 0, "days": 10}
    assert wa.count_kenntage([], TZ, start, end, today) is None


def test_records_start_with_the_first_day_and_name_the_days() -> None:
    today = date(2026, 6, 1)
    days = list(_days(date(2025, 5, 1), date(2026, 5, 31)))
    points = [_point(day, value=10.0, low=2.0, high=18.0) for day in days]
    short = wa.records(points[:100], TZ, today)  # schon ein junges Archiv hat Rekorde
    assert short["days"] == 100 and short["since"] == "2025-05-01"
    assert wa.records([], TZ, today) is None
    hottest = _point(date(2025, 8, 3), value=27.5, low=19.0, high=38.2)
    coldest = _point(date(2026, 1, 20), value=-8.0, low=-14.0, high=-2.0)
    points[points.index(next(p for p in points if p.day_start == hottest.day_start))] = hottest
    points[points.index(next(p for p in points if p.day_start == coldest.day_start))] = coldest
    found = wa.records(points, TZ, today)
    assert found["warmest_day"] == {"value": 27.5, "date": "2025-08-03"}
    assert found["coldest_day"] == {"value": -8.0, "date": "2026-01-20"}
    assert found["highest"] == {"value": 38.2, "date": "2025-08-03"}
    assert found["lowest"] == {"value": -14.0, "date": "2026-01-20"}
    assert found["since"] == "2025-05-01" and found["days"] == len(days)


def test_a_rain_sensor_holds_the_total_over_dry_days() -> None:
    daily = {date(2026, 1, 1): 30.0, date(2026, 1, 3): 10.0}  # danach keine Wechsel mehr: trocken, nicht unbekannt
    held = wa.precipitation(daily, date(2026, 1, 8), 10.0, hold_to_end=True)
    assert held["total"] == 40.0 and held["cumulative"][7] == 40.0 and held["cumulative"][8] is None
    unknown = wa.precipitation(daily, date(2026, 1, 8), 10.0)
    assert unknown["total"] == 40.0 and unknown["cumulative"][7] is None  # Zähler: Linie endet am letzten Wert


def test_rain_sensor_years_are_complete_when_the_archive_started_early_and_dry_days_count() -> None:
    daily = {date(2025, 3, 3): 40.0, date(2025, 9, 9): 30.0, date(2026, 1, 5): 12.0, date(2026, 1, 20): 12.0}
    result = wa.precipitation(daily, date(2026, 2, 1), 10.0, hold_to_end=True, archive_start=date(2025, 1, 2))
    assert result["avg_years"] == [2025]  # Archiv begann am 2.1.2025: das Jahr gilt als vollständig
    assert result["longest_dry_days"] == 14 and result["longest_dry_end"] == "2026-01-19"  # 6. bis 19.1., ohne Zeilen
    late = wa.precipitation(daily, date(2026, 2, 1), 10.0, hold_to_end=True, archive_start=date(2025, 6, 1))
    assert late["avg_years"] == []  # Archiv begann mitten im Jahr


def test_monthly_sums_for_a_rain_sensor_count_quiet_months_as_dry() -> None:
    points = [_point(date(2025, 3, 3), value=40.0)]
    result = wa.monthly(points, TZ, date(2025, 6, 10), "sum", dry_start=date(2025, 1, 2))
    assert result["years"]["2025"][:6] == [0.0, 0.0, 40.0, 0.0, 0.0, 0.0]
    assert wa.monthly(points, TZ, date(2025, 6, 10), "sum")["years"] == {}  # ohne Abdeckung unbekannt
    late = wa.monthly(points, TZ, date(2025, 6, 10), "sum", dry_start=date(2025, 4, 20))
    assert late["years"]["2025"][2] == 40.0 and late["years"]["2025"][0] is None  # vor Archivbeginn unbekannt


# -- Jahr×Tag-Matrix ---------------------------------------------------------------------------


def test_matrix_slots_give_the_leap_day_its_own_column() -> None:
    assert wa.matrix_slot(date(2024, 2, 29)) == 59
    assert wa.matrix_slot(date(2025, 3, 1)) == 60 and wa.matrix_slot(date(2024, 3, 1)) == 60
    assert wa.matrix_slot(date(2025, 12, 31)) == 365


def test_matrix_rows_are_years_newest_on_top_and_gaps_are_missing_not_zero() -> None:
    today = date(2026, 3, 10)
    points = [_point(day, value=5.0) for day in _days(date(2025, 1, 1), date(2025, 12, 31)) if day != date(2025, 5, 5)]
    points += [_point(day, value=2.0) for day in _days(date(2026, 1, 1), date(2026, 3, 9))]
    result = wa.matrix(points, TZ, today, "value", archive_start=date(2025, 1, 1))
    assert result["years"] == [2026, 2025]
    by_cell = {(slot, row): value for slot, row, value in result["cells"]}
    assert by_cell[(0, 0)] == 2.0 and by_cell[(0, 1)] == 5.0
    assert [wa.matrix_slot(date(2025, 5, 5)), 1] in result["missing"] and (wa.matrix_slot(date(2025, 5, 5)), 1) not in by_cell
    assert (wa.matrix_slot(date(2026, 3, 10)), 0) not in by_cell  # heute fehlt immer
    assert [wa.matrix_slot(date(2026, 3, 10)), 0] not in result["missing"]  # Zukunft und heute sind leer, nicht grau
    assert (result["min"], result["max"]) == (2.0, 5.0)


def test_matrix_skips_days_before_the_archive_and_thin_days() -> None:
    today = date(2026, 1, 20)
    points = [_point(date(2026, 1, 12), value=1.0), _point(date(2026, 1, 13), value=1.0, covered=False)]
    result = wa.matrix(points, TZ, today, "value", archive_start=date(2026, 1, 10))
    slots = {slot for slot, _row, _value in result["cells"]} | {slot for slot, _row in result["missing"]}
    assert min(slots) == 9 and len(result["cells"]) == 1  # ab dem 10.1.; der dünne 13.1. ist grau
    assert [12, 0] in result["missing"]


def test_matrix_dry_fill_counts_quiet_days_as_zero_for_a_rain_sensor() -> None:
    points = [_point(date(2026, 1, 3), value=20.0)]
    result = wa.matrix(points, TZ, date(2026, 1, 6), "value", scale=0.5, archive_start=date(2026, 1, 1), dry_fill=True, require_coverage=False)
    assert sorted(result["cells"]) == [[0, 0, 0.0], [1, 0, 0.0], [2, 0, 10.0], [3, 0, 0.0], [4, 0, 0.0]]
    assert result["missing"] == []


def test_matrix_without_data_is_empty() -> None:
    assert wa.matrix([], TZ, date(2026, 1, 6), "value")["years"] == []


# -- Heizen ------------------------------------------------------------------------------------


def test_heating_days_cover_only_closed_heating_season_days_with_data() -> None:
    today = date(2026, 11, 3)
    temperature = [
        _point(date(2026, 10, 1), value=9.0),
        _point(date(2026, 7, 1), value=22.0),  # Sommer: keine Heizperiode
        _point(date(2026, 10, 2), value=8.0, covered=False),  # Temperatur zu dünn
        _point(date(2026, 10, 3), value=7.0),  # kein Zähler hat Werte
        _point(date(2026, 11, 3), value=4.0),  # heute
        _point(date(2026, 11, 2), value=5.0),
    ]
    heat_pump = [_point(date(2026, 10, 1), value=6000.0), _point(date(2026, 7, 1), value=1000.0),
                 _point(date(2026, 10, 2), value=7000.0), _point(date(2026, 11, 2), value=9000.0)]
    rod = [_point(date(2026, 11, 2), value=1.5)]
    days = wa.heating_days(temperature, [heat_pump, rod], [0.001, 1.0], TZ, today)  # Wärmepumpe zählt in Wh
    assert days == [["2026-10-01", 9.0, [6.0, 0.0]], ["2026-11-02", 5.0, [9.0, 1.5]]]


def test_heating_days_without_counters_are_empty() -> None:
    assert wa.heating_days([_point(date(2026, 1, 5))], [[]], [1.0], TZ, date(2026, 2, 1)) == []
