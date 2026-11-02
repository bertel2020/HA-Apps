"""Demo-Daten für das Wetterdashboard (ROADMAP 1.43): Luftdruck, Böe, Strahlung, Niederschlag,
zweites Thermometer und Heizenergie in drei Zählern."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.demo_generation import (
    DEMO_ENTITIES,
    WETTER_ENTITY_IDS,
    WeatherContext,
    build_appliance_schedules,
    build_rain_schedule,
    demo_category_index,
    localized_id,
    simulate_household,
)

TZ = ZoneInfo("Europe/Berlin")
WETTER_IDS = {
    "sensor.demo_luftdruck": ("measurement", "hPa"),
    "sensor.demo_boe": ("measurement", "km/h"),
    "sensor.demo_strahlung": ("measurement", "W/m²"),
    "sensor.demo_niederschlag": ("total_increasing", "mm"),
    "sensor.demo_aussentemperatur_nord": ("measurement", "°C"),
    "sensor.demo_waermepumpe_energie": ("total_increasing", "kWh"),
    "sensor.demo_heizstab_energie": ("total_increasing", "kWh"),
    "sensor.demo_konvektoren_energie": ("total_increasing", "kWh"),
}


def _simulate(seed: int, start: datetime, days: int) -> dict:
    end = start + timedelta(days=days)
    rng = random.Random(seed)
    weather = WeatherContext(start, end, rng)
    schedules = build_appliance_schedules(start, end, rng)
    rain = build_rain_schedule(start, end, weather, rng)
    return simulate_household(start, end, TZ, rng, weather, schedules, rain)


@pytest.fixture(scope="module")
def winter() -> dict:
    return _simulate(5, datetime(2024, 1, 1, tzinfo=TZ), 30)


@pytest.fixture(scope="module")
def summer() -> dict:
    return _simulate(5, datetime(2024, 7, 1, tzinfo=TZ), 30)


def test_weather_entities_are_registered_with_the_right_type_and_unit() -> None:
    by_id = {entity.entity_id: entity for entity in DEMO_ENTITIES}
    for entity_id, (state_class, unit) in WETTER_IDS.items():
        assert (by_id[entity_id].state_class, by_id[entity_id].unit) == (state_class, unit), entity_id
        assert demo_category_index(entity_id) in (4, 5)
    assert demo_category_index("sensor.demo_niederschlag") == 5
    assert demo_category_index("sensor.demo_waermepumpe_energie") == 4
    # Neue Entitäten stehen am Ende, damit die Zufallsfolge der früheren unverändert bleibt.
    assert [e.entity_id for e in DEMO_ENTITIES[-len(WETTER_IDS):]] == list(WETTER_IDS) == list(WETTER_ENTITY_IDS)


def test_english_ids_are_unique_and_translated() -> None:
    ids = [localized_id(entity.entity_id, "en") for entity in DEMO_ENTITIES]
    assert len(ids) == len(set(ids))
    assert localized_id("sensor.demo_boe", "en") == "sensor.demo_gust"
    assert localized_id("sensor.demo_waermepumpe_energie", "en") == "sensor.demo_heat_pump_energy"


def test_same_seed_gives_the_same_weather(winter) -> None:
    again = _simulate(5, datetime(2024, 1, 1, tzinfo=TZ), 30)
    for key in ("luftdruck", "boe", "strahlung", "niederschlag", "outdoor_temp_nord", "waermepumpe_energie"):
        assert again[key] == winter[key], key


def test_pressure_is_plausible_and_steady(winter) -> None:
    values = [value for _ts, value in winter["luftdruck"]]
    assert 985.0 <= min(values) and max(values) <= 1040.0
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(steps) < 2.0  # Fünf-Minuten-Schritt: kein Tagessprung um Mitternacht


def test_gust_is_never_below_the_mean_wind(winter) -> None:
    for (_t1, wind), (_t2, gust) in zip(winter["wind"], winter["boe"]):
        assert gust > wind


def test_radiation_follows_the_pv_curve(summer) -> None:
    for (_t1, pv), (_t2, radiation) in zip(summer["pv_power"], summer["strahlung"]):
        if pv <= 0:
            assert radiation == 0.0
    assert 500 < max(value for _ts, value in summer["strahlung"]) <= 1100


def test_rain_amount_only_grows_while_the_rain_sensor_is_on(summer) -> None:
    total = summer["niederschlag"]
    assert all(b[1] >= a[1] - 1e-9 for a, b in zip(total, total[1:]))
    assert total[-1][1] > total[0][1]  # in 30 Tagen regnet es
    # Der Regensensor meldet Wechsel; zwischen zwei Zählerständen ohne Regenfenster steigt nichts.
    on_intervals = []
    state_on_since = None
    for ts, state in summer["regensensor"]:
        if state == 1.0:
            state_on_since = ts
        elif state_on_since is not None:
            on_intervals.append((state_on_since, ts))
            state_on_since = None
    assert on_intervals
    grew = [(a[0], b[0]) for a, b in zip(total, total[1:]) if b[1] > a[1] + 1e-9]
    for begin, finish in grew:
        assert any(start <= finish and begin <= stop + 1800 for start, stop in on_intervals), (begin, finish)


def test_heating_counters_split_the_heating_energy(winter) -> None:
    wp, stab, konv = (winter[key] for key in ("waermepumpe_energie", "heizstab_energie", "konvektoren_energie"))
    for series in (wp, stab, konv):
        assert all(b[1] >= a[1] - 1e-9 for a, b in zip(series, series[1:]))
    added = [series[-1][1] - series[0][1] for series in (wp, stab, konv)]
    heating_kwh = sum(value for _ts, value in winter["heizung"]) / 1000 * (5 / 60)
    assert sum(added) == pytest.approx(heating_kwh, rel=0.03)
    assert added[0] > added[2] > added[1] > 0  # Wärmepumpe vor Konvektoren vor Heizstab


def test_summer_heating_is_a_fraction_of_winter_heating(winter, summer) -> None:
    def added(household: dict) -> float:
        series = household["waermepumpe_energie"]
        return series[-1][1] - series[0][1]

    assert added(summer) < 0.1 * added(winter)


def test_north_thermometer_is_cooler_than_outdoor_on_average(summer) -> None:
    outdoor = [value for _ts, value in summer["outdoor_temp"]]
    north = [value for _ts, value in summer["outdoor_temp_nord"]]
    assert sum(north) / len(north) < sum(outdoor) / len(outdoor) - 0.5
