"""PV-Ertrag gegen Helligkeit: die reinen Funktionen und der Dienst."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import wetter_pv as pv

DAY0 = date(2026, 6, 1)


def _day(day: date, factor: float = 1.0, lux_scale: float = 1.0) -> list[tuple[date, int, float, float]]:
    """Ein Tag von 8 bis 15 Uhr: Leistung 0,05 kW je 1000 lx (zwei Stufen), mit ``factor`` verfälschbar."""
    rows = []
    for hour in range(8, 16):
        lux = (30000.0 if hour in (8, 9, 14, 15) else 60000.0) * lux_scale
        rows.append((day, hour, lux, lux / 1000 * 0.05 * factor))
    return rows


def _pairs(days: int, tweak: dict[date, float] | None = None) -> list:
    out = []
    for offset in range(days):
        day = DAY0 + timedelta(days=offset)
        out += _day(day, (tweak or {}).get(day, 1.0))
    return out


def test_bins_follow_the_edges() -> None:
    assert [pv.bin_of(lux) for lux in (0, 2999, 3000, 9999, 10000, 98800)] == [0, 0, 1, 1, 2, 10]


def test_curve_uses_the_median_and_drops_thin_bins() -> None:
    samples = [(35000.0, value) for value in (1.0, 2.0, 100.0, 1.5, 1.6)] + [(65000.0, 3.0)] * 4
    assert pv.learn_curve(samples) == {4: 1.6}  # Median ohne Ausreißer; die Stufe mit vier Stunden fehlt


def test_a_day_below_the_learned_curve_is_flagged() -> None:
    bad = DAY0 + timedelta(days=30)
    pairs = _pairs(31, {bad: 0.6})
    result = pv.evaluate(pairs, [bad - timedelta(days=1), bad])
    by_day = {row["date"]: row for row in result["days"]}
    assert by_day[(bad - timedelta(days=1)).isoformat()]["flagged"] is False
    row = by_day[bad.isoformat()]
    assert row["deviation"] == pytest.approx(-40.0, abs=1.0) and row["flagged"] is True
    assert row["actual"] < row["expected"]


def test_small_expectations_are_never_flagged() -> None:
    """Ein dunkler Tag mit kleiner Erwartung schwankt relativ stark; das ist keine Meldung wert."""
    pairs = _pairs(30)  # Spitze 3 kW, also gilt eine Erwartung ab 4,5 kWh
    for offset in range(30):  # trübe Stunden in der Vorgeschichte: 5.000 lx, 0,1 kW
        pairs += [(DAY0 + timedelta(days=offset), hour, 5000.0, 0.1) for hour in range(10, 16)]
    dark = DAY0 + timedelta(days=30)
    pairs += [(dark, hour, 5000.0, 0.0) for hour in range(10, 16)]  # Erwartung 0,6 kWh, Ertrag 0
    row = pv.evaluate(pairs, [dark])["days"][0]
    assert row["deviation"] == pytest.approx(-100.0) and row["flagged"] is False


def test_days_without_a_learning_window_are_left_out() -> None:
    assert pv.evaluate(_pairs(3), [DAY0 + timedelta(days=2)]) is None
    assert pv.evaluate([], [DAY0]) is None


def test_ratio_by_hour_exposes_a_systematic_morning_shortfall() -> None:
    pairs = []
    for offset in range(40):
        day = DAY0 + timedelta(days=offset)
        rows = _day(day)
        if offset >= 30:
            rows = [(d, h, lux, power * (0.5 if h < 10 else 1.0)) for d, h, lux, power in rows]
        pairs += rows
    shown = [DAY0 + timedelta(days=offset) for offset in range(30, 40)]
    result = pv.evaluate(pairs, shown)
    ratio = {row["hour"]: row["ratio"] for row in result["hours"]}
    assert ratio[8] < 0.6 and ratio[12] == pytest.approx(1.0, abs=0.02)
