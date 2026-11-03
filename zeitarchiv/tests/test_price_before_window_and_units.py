"""Code-Review Phase 5: Preis am Fensteranfang, Vorperiode ohne Qualitätsprüfung, Einheiten der Kenntage.

Alle drei Fälle rechneten mit plausiblen, aber falschen Zahlen und ohne Fehlermeldung.
"""

from __future__ import annotations

import datetime as _dt
import tempfile
from pathlib import Path

import pytest

try:
    from test_energiedashboard_flow import TZ, _beispielanlage, _Anlage, _FesteUhr, _ts, ed, hotbuffer
    from app import wetter_analysis as analysis
    from app.storage import rotate
    from app.storage.query import DailyPoint, QueryReadCache

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="pyarrow fehlt")


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ---------------------------------------------------------------- A7: Preis vor dem ersten Wert im Fenster


def _tarif_vor_dem_fenster(tmp: Path, mit_wechsel: bool = True):
    """Netzbezug 3 kWh um 5 Uhr und 3 kWh um 12 Uhr am 15.03. Der Preis (ein Tarif-Helfer) steht seit dem
    10.02. auf 0,10 €. Mit ``mit_wechsel`` wechselt er am 15.03. um 12 Uhr auf 0,50 €: im Monat März gibt es dann
    nur diesen einen Wert."""
    a = _Anlage(tmp)
    a.zaehler("sensor.netz", (0, 100.0), (5, 103.0), (12, 106.0), (13, 106.0))
    a.index.set_entity_hourly_rollup("sensor.netz", True)
    a.index.get_or_create_entity("sensor.preis", "sensor", "measurement", "EUR/kWh")
    februar = _dt.datetime(2024, 2, 10, 8, tzinfo=TZ).timestamp()
    hotbuffer.append(tmp, "sensor.preis", februar, 0.10, TZ)
    a.index.record_write("sensor.preis", februar)
    if mit_wechsel:
        hotbuffer.append(tmp, "sensor.preis", _ts(12), 0.50, TZ)
        a.index.record_write("sensor.preis", _ts(12))
    rotate.rotate_if_needed(tmp, "sensor.preis", _ts(12), a.index, TZ)
    config = ed._empty_config()
    config["netzbezug"] = "sensor.netz"
    config["kosten"] = {"preis_netzbezug": "sensor.preis"}
    return a, config


def test_month_costs_use_the_price_that_was_valid_when_the_window_began(monkeypatch, tmp: Path) -> None:
    """Früher ersetzte die Stundenreihe der Preise (nur Stunden mit Rohwert) die Bucket-Reihe, die den Preis vom
    Fensteranfang kennt: alles vor der ersten Änderung galt als "ohne Preis", die Kosten fielen zu niedrig aus."""
    a, config = _tarif_vor_dem_fenster(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "month", 0)["kpi"]
        assert kpi["kosten_aufloesung"] == "stunde"
        assert kpi["netzbezug_cost"] == pytest.approx(3 * 0.10 + 3 * 0.50, abs=0.01)
        assert kpi["netzbezug_avg_price"] == pytest.approx(0.30, abs=0.001)
        assert kpi["kosten_luecke"] == []
    finally:
        a.close()


def test_a_price_without_any_change_in_the_window_still_prices_everything(monkeypatch, tmp: Path) -> None:
    """Gegenprobe: Im ganzen März steht nur der Wert vom Februar. Die Stundenreihe ist leer, die Bucket-Reihe
    trägt ihn über den Randpunkt (war schon vorher richtig und bleibt es)."""
    a, config = _tarif_vor_dem_fenster(tmp, mit_wechsel=False)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "month", 0)["kpi"]
        assert kpi["netzbezug_cost"] == pytest.approx(0.60, abs=0.01)
        assert kpi["kosten_luecke"] == []
    finally:
        a.close()


# ---------------------------------------------------------------- B18: Vorperiode ohne Qualitätsprüfung


def test_previous_period_comparison_skips_the_quality_work_but_keeps_the_numbers(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        service = a.service
        read_cache = QueryReadCache()
        current = service.compute_flow(config, "day", -1, read_cache=read_cache)
        calls: list[dict] = []
        original = service.compute_flow

        def spy(*args, **kwargs):
            calls.append({"offset": args[2], **kwargs})
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "compute_flow", spy)
        compare, _cur, previous_kpi = service.compute_period_comparison(config, "day", -1, current, read_cache)

        assert [call["offset"] for call in calls] == [-2]
        assert calls[0].get("skip_quality") is True
        # Dieselben Zahlen wie mit dem vollen Durchlauf: nur Kosten, Auffälligkeiten und Qualität entfallen.
        full = original(config, "day", -2, read_cache=QueryReadCache())["kpi"]
        for key in ("erzeugung", "verbrauch", "netzbezug", "einspeisung", "autarkie", "eigenverbrauch"):
            assert previous_kpi.get(key) == full.get(key), key
        assert set(compare) >= {"erzeugung", "verbrauch", "netzbezug", "einspeisung"}
    finally:
        a.close()


# ---------------------------------------------------------------- B19: Einheiten der Wetter-Kenntage


def test_celsius_limits_are_converted_to_the_unit_of_the_entity() -> None:
    assert analysis.celsius_to_unit(25.0, "°C") == 25.0
    assert analysis.celsius_to_unit(25.0, None) == 25.0
    assert analysis.celsius_to_unit(25.0, "°F") == pytest.approx(77.0)
    assert analysis.celsius_to_unit(0.0, "°F") == pytest.approx(32.0)
    assert analysis.celsius_to_unit(0.0, "K") == pytest.approx(273.15)
    assert analysis.mm_to_unit(2.54, "in") == pytest.approx(0.1)
    assert analysis.mm_to_unit(1.0, "mm") == 1.0


def _day(day: _dt.date, low: float, high: float) -> DailyPoint:
    start = _dt.datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp()
    return DailyPoint(day_start=start, value=(low + high) / 2, min_value=low, max_value=high)


TODAY = _dt.date(2025, 7, 1)


@pytest.mark.parametrize(
    ("unit", "frost_day", "summer_day", "mild_day"),
    [
        ("°C", (-2.0, 3.0), (15.0, 26.0), (8.0, 15.0)),
        ("°F", (28.0, 37.0), (59.0, 79.0), (46.0, 59.0)),
        ("K", (271.15, 276.15), (288.15, 299.15), (281.15, 288.15)),
    ],
)
def test_kenntage_count_the_same_days_in_every_temperature_unit(unit, frost_day, summer_day, mild_day) -> None:
    points = [
        _day(_dt.date(2025, 1, 10), *frost_day),
        _day(_dt.date(2025, 6, 10), *summer_day),
        _day(_dt.date(2025, 6, 11), *mild_day),
    ]
    start = _dt.datetime(2025, 1, 1, tzinfo=TZ).timestamp()
    end = _dt.datetime(2025, 7, 1, tzinfo=TZ).timestamp()

    counted = analysis.count_kenntage(points, TZ, start, end, TODAY, unit=unit)
    assert counted == {"frost": 1, "eis": 0, "sommer": 1, "heiss": 0, "days": 3}
    yearly = analysis.kenntage(points, TZ, TODAY, unit)["2025"]
    assert yearly["frost"][0] == 1 and yearly["sommer"][5] == 1 and sum(yearly["eis"]) == 0


def test_a_fahrenheit_sensor_no_longer_makes_almost_every_day_a_summer_day() -> None:
    mild = _day(_dt.date(2025, 6, 10), 50.0, 68.0)  # 10 bis 20 °C
    start = _dt.datetime(2025, 6, 1, tzinfo=TZ).timestamp()
    end = _dt.datetime(2025, 7, 1, tzinfo=TZ).timestamp()
    assert analysis.count_kenntage([mild], TZ, start, end, TODAY, unit="°F")["sommer"] == 0
    # Ohne Einheitenangabe bleibt es beim bisherigen Verhalten (68 ≥ 25).
    assert analysis.count_kenntage([mild], TZ, start, end, TODAY)["sommer"] == 1
