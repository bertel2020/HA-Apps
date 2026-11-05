"""Demo-Daten für das Wetterdashboard (ROADMAP 1.43): Luftdruck, Böe, Strahlung, Niederschlag,
zweites Thermometer, Heizenergie in drei Zählern sowie Helligkeit Süd und Dämmerung."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import wetter_daylight as dl
from app.demo_generation import (
    DEMO_ENTITIES,
    SUED_LUX_MAX,
    SUED_LUX_STEP,
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
    "sensor.demo_helligkeit_sued": ("measurement", "lx"),
    "sensor.demo_helligkeit_daemmerung": ("measurement", "lx"),
    "sensor.demo_windrichtung": ("measurement", "°"),
    "sensor.demo_sonnenhoehe": ("measurement", "°"),
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
    assert localized_id("sensor.demo_helligkeit_sued", "en") == "sensor.demo_illuminance_south"
    assert localized_id("sensor.demo_helligkeit_daemmerung", "en") == "sensor.demo_illuminance_dusk_dawn"


def test_same_seed_gives_the_same_weather(winter) -> None:
    again = _simulate(5, datetime(2024, 1, 1, tzinfo=TZ), 30)
    for key in (
        "luftdruck", "boe", "strahlung", "niederschlag", "outdoor_temp_nord", "waermepumpe_energie",
        "helligkeit_sued", "helligkeit_daemmerung", "windrichtung", "sonnenhoehe",
    ):
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


def test_south_illuminance_follows_the_radiation_and_saturates(summer, winter) -> None:
    for household in (summer, winter):
        for (_t1, radiation), (_t2, lux) in zip(household["strahlung"], household["helligkeit_sued"]):
            assert radiation > 0 or lux == 0.0
            assert radiation < 20 or lux > 0.0  # unter etwa 300 lx meldet die gestufte Anzeige 0
            assert 0.0 <= lux <= SUED_LUX_MAX
            assert lux in (SUED_LUX_MAX, round(lux / SUED_LUX_STEP) * SUED_LUX_STEP)  # gestuft wie der echte Sensor
    assert max(value for _ts, value in summer["helligkeit_sued"]) > 0.9 * SUED_LUX_MAX  # klare Sommertage: fast Sättigung
    assert max(value for _ts, value in winter["helligkeit_sued"]) < max(value for _ts, value in summer["helligkeit_sued"])


def test_dawn_illuminance_is_a_stepped_ramp_between_night_and_day(summer) -> None:
    values = [value for _ts, value in summer["helligkeit_daemmerung"]]
    assert min(values) == 0.0 and max(values) > 800.0
    assert all(0.0 <= value <= 1000.0 and value % 4 == 0 for value in values)
    # Eine Rampe, kein Sprung: zwischen zwei Fünf-Minuten-Werten ändert sich der Wert nie um mehr als die halbe Skala.
    assert max(abs(b - a) for a, b in zip(values, values[1:])) < 500.0


def _day_length_hours(household: dict, day: datetime) -> float:
    start, end = day.timestamp(), (day + timedelta(days=1)).timestamp()
    points = [(ts, value) for ts, value in household["helligkeit_daemmerung"] if start <= ts < end]
    result = dl.summarize_fine(dl.hold_points(points, start, end, 5.0), 5.0, 400.0, end, live=False)
    return result["length"] / 3600.0


def test_dawn_curve_gives_a_longer_day_in_summer_than_in_winter(summer, winter) -> None:
    long_day = _day_length_hours(summer, datetime(2024, 7, 10, tzinfo=TZ))
    short_day = _day_length_hours(winter, datetime(2024, 1, 10, tzinfo=TZ))
    assert 14.0 < long_day < 17.0 and 7.0 < short_day < 10.5


def test_wind_direction_stays_within_the_compass_and_sun_elevation_follows_the_season(summer, winter) -> None:
    for household in (summer, winter):
        assert all(0.0 <= value < 360.0 and value == int(value) for _ts, value in household["windrichtung"])
    noon = lambda household: max(value for _ts, value in household["sonnenhoehe"])  # noqa: E731
    assert noon(summer) > 55.0 > 25.0 > noon(winter) > 10.0  # Mitteleuropa: etwa 62° im Sommer, 19° im Winter
    assert min(value for _ts, value in summer["sonnenhoehe"]) < -10.0  # nachts unter dem Horizont


def test_south_illuminance_has_all_sky_classes_over_a_summer_month(summer) -> None:
    peaks = {}
    for ts, lux in summer["helligkeit_sued"]:
        day = datetime.fromtimestamp(ts, TZ).date()
        peaks[day] = max(peaks.get(day, 0.0), lux)
    classes = {"trueb" if p < 15000 else "bewoelkt" if p < 25000 else "heiter" if p < 70000 else "sonnig" for p in peaks.values()}
    assert len(classes) >= 3, classes  # nicht fast nur sonnige Tage, damit Tagesbild und Heizen+Sonne etwas zeigen
