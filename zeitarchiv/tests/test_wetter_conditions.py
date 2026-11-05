"""Wetterdashboard: Wetterlage als Satz aus Himmel, Wind, Regen und Temperatur (ROADMAP 1.43)."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import wetter_conditions as cond
from app import wetter_routes
from app.storage import hotbuffer
from app.storage.index import Index
from app.wetter_routes import WetterDependencies, WetterService

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=TZ)


def _ts(hour: int, minute: int = 0, day: int = 20) -> float:
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ).timestamp()


@pytest.fixture()
def service():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-lage-test-"))
    index = Index(tmp / "index.sqlite")
    yield WetterService(WetterDependencies(
        data_dir=tmp, index=index, tz=TZ, templates=None, app_root_context=lambda request: {},
    ))
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _add(service, entity_id, domain, state_class, unit, points):
    deps = service.deps
    deps.index.get_or_create_entity(entity_id, domain, state_class, unit)
    for ts, value in points:
        hotbuffer.append(deps.data_dir, entity_id, ts, value, TZ)
        deps.index.record_write(entity_id, ts, value)


def _configure(service, **roles):
    wetter_routes._save_config(service.deps.index, {**wetter_routes._empty_config(), **roles})


def _minutes(value: float, start: int = 52, end: int = 59) -> list[tuple[float, float]]:
    return [(_ts(11, minute), value) for minute in range(start, end + 1)]


def test_sky_class_follows_the_cuts_and_scales_with_the_sun_threshold() -> None:
    cuts = cond.lx_cuts(25000.0)
    assert cuts == pytest.approx((15000.0, 25000.0, 70000.0))
    assert [cond.sky_class(v, cuts) for v in (0, 14999, 15000, 24999, 25000, 69999, 70000, 99000)] == [
        cond.OVERCAST, cond.OVERCAST, cond.CLOUDY, cond.CLOUDY, cond.FAIR, cond.FAIR, cond.SUNNY, cond.SUNNY,
    ]
    assert cond.lx_cuts(40000.0)[1] == 40000.0  # „heiter“ beginnt dort, wo Sonnenschein zählt


def test_beaufort_table_and_units() -> None:
    assert [cond.beaufort(v) for v in (0, 0.9, 1, 3, 7, 11, 21, 36, 38, 39, 100, 130)] == [0, 0, 1, 1, 2, 2, 4, 5, 5, 6, 10, 12]
    assert cond.to_kmh(10.0, "m/s") == pytest.approx(36.0)
    assert cond.to_kmh(20.0, "kn") == pytest.approx(37.04)
    assert cond.to_kmh(5.0, "") == 5.0
    assert cond.to_kmh(5.0, "furlong") is None  # unbekannte Einheit: keine geratene Stärke


def test_rain_state_from_the_switch_history() -> None:
    now = 10_000.0
    raining = cond.rain_state([(0.0, False), (7000.0, True), (8000.0, True)], now)
    assert raining["raining"] and raining["since"] == 7000.0 and raining["minutes"] == pytest.approx(50.0)
    dry = cond.rain_state([(0.0, False), (3000.0, True), (4000.0, False)], now)
    assert dry == {"raining": False, "since": None, "last": 4000.0, "minutes": None}
    assert cond.rain_state([(0.0, False)], now)["last"] is None
    assert cond.rain_state([(0.0, True)], now)["since"] is None  # lief schon am Anfang des Fensters
    assert cond.rain_state([], now) is None


def _station(service, *, lux=80000.0, dawn=1000.0, wind=21.0):
    _add(service, "sensor.lux", "sensor", "measurement", "lx", _minutes(lux))
    _add(service, "sensor.dawn", "sensor", "measurement", "lx", _minutes(dawn))
    _add(service, "sensor.wind", "sensor", "measurement", "km/h", _minutes(wind))
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(11, 58), 14.2)])
    _add(service, "binary_sensor.regen", "binary_sensor", None, "", [(_ts(11, 30), 1.0)])
    _configure(
        service, temperature="sensor.t", illuminance="sensor.lux", illuminance_dawn="sensor.dawn", wind="sensor.wind",
        precip_dur="binary_sensor.regen",
    )


def test_conditions_sentence_parts_by_day(service) -> None:
    _station(service)
    data = service.compute_conditions(NOW)
    assert data["sky"]["class"] == cond.SUNNY and data["sky"]["value"] == pytest.approx(80000.0)
    assert data["sky"]["dawn"] == pytest.approx(1000.0) and data["sky"]["unit"] == "lx"
    assert data["wind"] == {"kmh": 21.0, "bft": 4}
    assert data["rain"]["raining"] is True and data["rain"]["minutes"] == pytest.approx(30.0)
    assert data["temp"]["value"] == 14.2 and data["temp"]["unit"] == "°C"


def test_conditions_carry_the_extras_for_the_hero_and_tiles(service) -> None:
    _station(service)
    _add(service, "sensor.gust", "sensor", "measurement", "km/h", _minutes(35.0))
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(5, 0), 3.1), (_ts(9, 0), 9.4), (_ts(11, 58), 8.3)])
    _configure(
        service, temperature="sensor.t", illuminance="sensor.lux", illuminance_dawn="sensor.dawn", wind="sensor.wind",
        gust="sensor.gust", precip_dur="binary_sensor.regen",
    )
    data = service.compute_conditions(NOW)
    assert data["wind"]["gust_kmh"] == pytest.approx(35.0)
    assert data["temp"]["min"] <= 8.3 <= data["temp"]["max"]
    assert "light" in data


@pytest.mark.parametrize(("dawn", "expected"), [(0.0, cond.NIGHT), (100.0, cond.DUSK), (400.0, cond.SUNNY)])
def test_conditions_sky_uses_the_dawn_sensor_for_night_and_twilight(service, dawn, expected) -> None:
    _station(service, dawn=dawn)
    assert service.compute_conditions(NOW)["sky"]["class"] == expected


def test_conditions_without_dawn_sensor_use_the_main_sensor_for_the_dark(service) -> None:
    _add(service, "sensor.lux", "sensor", "measurement", "lx", _minutes(0.0))
    _configure(service, temperature="sensor.t", illuminance="sensor.lux")
    assert service.compute_conditions(NOW)["sky"]["class"] == cond.NIGHT
    _add(service, "sensor.lux2", "sensor", "measurement", "lx", _minutes(12000.0))
    _configure(service, temperature="sensor.t", illuminance="sensor.lux2")
    assert service.compute_conditions(NOW)["sky"]["class"] == cond.OVERCAST


def test_conditions_radiation_is_the_source_without_illuminance(service) -> None:
    _add(service, "sensor.rad", "sensor", "measurement", "W/m²", _minutes(300.0))
    _configure(service, temperature="sensor.t", radiation="sensor.rad")
    sky = service.compute_conditions(NOW)["sky"]
    assert sky["class"] == cond.FAIR and sky["source"] == "radiation" and sky["unit"] == "W/m²"


def test_conditions_hold_a_value_when_no_row_is_new(service) -> None:
    _add(service, "sensor.lux", "sensor", "measurement", "lx", [(_ts(9, 0), 90000.0)])  # seit Stunden unverändert
    _configure(service, temperature="sensor.t", illuminance="sensor.lux")
    assert service.compute_conditions(NOW)["sky"]["class"] == cond.SUNNY


def test_conditions_rain_dry_with_last_rain_and_amount(service) -> None:
    _add(service, "binary_sensor.regen", "binary_sensor", None, "", [(_ts(9, 0), 1.0), (_ts(9, 30), 0.0)])
    _add(service, "sensor.menge", "sensor", "total_increasing", "mm", [(_ts(0, 1), 10.0), (_ts(9, 30), 11.5)])
    _configure(service, temperature="sensor.t", precip_dur="binary_sensor.regen", precip_mm="sensor.menge")
    rain = service.compute_conditions(NOW)["rain"]
    assert rain["raining"] is False and rain["last"] == _ts(9, 30)
    assert rain["mm_today"] == pytest.approx(1.5) and rain["mm_unit"] == "mm"


def test_conditions_rain_amount_alone_has_no_state(service) -> None:
    _add(service, "sensor.menge", "sensor", "total_increasing", "mm", [(_ts(0, 1), 10.0), (_ts(9, 30), 11.5)])
    _configure(service, temperature="sensor.t", precip_mm="sensor.menge")
    rain = service.compute_conditions(NOW)["rain"]
    assert rain["raining"] is None and rain["mm_today"] == pytest.approx(1.5)


def test_conditions_are_none_without_any_usable_sensor(service) -> None:
    _configure(service, temperature="sensor.t")
    assert service.compute_conditions(NOW) is None
    _configure(service, temperature="sensor.t", wind="sensor.fehlt")
    assert service.compute_conditions(NOW) is None


def test_conditions_card_and_route_through_the_app(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.lage_test_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.lage_test_wind", "sensor", "measurement", "km/h")
    base = {"temperature": "sensor.lage_test_temp"}
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert "data-wt-conditions" not in client.get("/wetterdashboard").text  # nur Temperatur: keine Wetterlage
    assert client.post("/wetterdashboard/setup", data={**base, "wind": "sensor.lage_test_wind"}).status_code == 200
    assert "data-wt-conditions" in client.get("/wetterdashboard").text
    body = client.get("/wetterdashboard/wetterlage")
    assert body.status_code == 200 and body.json()["sky"] is None
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    assert client.get("/wetterdashboard/wetterlage").status_code == 409


def test_conditions_script_builds_the_sentence_and_follows_the_date() -> None:
    from _paths import APP

    script = (APP / "static/js/wetter.js").read_text(encoding="utf-8")
    assert "wetterdashboard/wetterlage" in script and "conditionsLine" in script and "beaufortName" in script
    assert "regnerisch" in script and "windig" in script  # eine allgemeine Wetterlage statt einer Aufzählung
    assert "conditionsDayParts" in script and "wetterdashboard/wetterlage${stichtag" in script  # Rückblick: Tageszusammenfassung


def _day_station(service) -> None:
    hours = range(5, 20)
    lux = {5: 0.0, 6: 300.0, 7: 20000.0, 8: 80000.0, 9: 90000.0, 10: 85000.0, 11: 30000.0, 12: 20000.0, 13: 90000.0,
           14: 92000.0, 15: 88000.0, 16: 9000.0, 17: 300.0, 18: 0.0, 19: 0.0}
    dawn = {hour: (0.0 if value < 5 else 100.0 if value < 5000 else 1000.0) for hour, value in lux.items()}
    _add(service, "sensor.lux", "sensor", "measurement", "lx", [(_ts(h, 30, day=19), lux[h]) for h in hours])
    _add(service, "sensor.dawn", "sensor", "measurement", "lx", [(_ts(h, 30, day=19), dawn[h]) for h in hours])
    _add(service, "sensor.wind", "sensor", "measurement", "km/h", [(_ts(h, 30, day=19), 8.0 + h) for h in hours])
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(h, 30, day=19), 4.0 + h / 2) for h in hours])
    _configure(
        service, temperature="sensor.t", illuminance="sensor.lux", illuminance_dawn="sensor.dawn", wind="sensor.wind",
        sun_threshold_lx=25000.0,
    )


def test_conditions_day_summarises_a_past_day(service) -> None:
    _day_station(service)
    data = service.compute_conditions_day("2026-08-19", NOW)
    assert data["mode"] == "day" and data["date"] == "2026-08-19"
    # Helle Stunden = Dämmerungssensor ab 400 lx: 7 bis 16 Uhr; sonnig sind 8, 9, 10, 13, 14, 15
    assert data["sky"]["class"] == cond.SUNNY and data["sky"]["day_hours"] >= 7
    assert data["sky"]["sun_hours"] >= 5
    assert data["wind"]["kmh"] == pytest.approx(27.0) and data["wind"]["bft"] == 4
    assert data["temp"]["min"] == pytest.approx(6.5) and data["temp"]["max"] == pytest.approx(13.5)
    assert data["rain"] is None


def test_conditions_day_is_none_for_today_and_without_data(service) -> None:
    _day_station(service)
    assert service.compute_conditions_day("2026-08-20", NOW) is None  # heute gilt als „jetzt“
    assert service.compute_conditions_day("2026-08-10", NOW) is None  # kein Wert an diesem Tag


def test_conditions_route_takes_a_date(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.lagetag_wind", "sensor", "measurement", "km/h")
    index.get_or_create_entity("sensor.lagetag_temp", "sensor", "measurement", "°C")
    data = {"temperature": "sensor.lagetag_temp", "wind": "sensor.lagetag_wind"}
    assert client.post("/wetterdashboard/setup", data=data).status_code == 200
    body = client.get("/wetterdashboard/wetterlage?date=2020-01-01")
    assert body.status_code == 200 and body.json()["wind"] is None


def test_conditions_name_the_wind_direction_only_with_wind(service) -> None:
    _station(service)
    _add(service, "sensor.richtung", "sensor", "measurement", "°", _minutes(225.0))
    _configure(
        service, temperature="sensor.t", illuminance="sensor.lux", illuminance_dawn="sensor.dawn", wind="sensor.wind",
        wind_direction="sensor.richtung",
    )
    assert service.compute_conditions(NOW)["wind"]["dir_deg"] == pytest.approx(225.0)
    _add(service, "sensor.flaute", "sensor", "measurement", "km/h", _minutes(0.4))
    _configure(service, temperature="sensor.t", wind="sensor.flaute", wind_direction="sensor.richtung")
    assert "dir_deg" not in service.compute_conditions(NOW)["wind"]  # bei Flaute sagt die Richtung nichts


def test_conditions_remember_yesterdays_end_of_daylight(service) -> None:
    _station(service)
    ramp = [(6, 30, 0.0), (7, 30, 500.0), (8, 0, 1000.0), (16, 0, 1000.0), (16, 40, 300.0), (17, 30, 0.0)]
    _add(service, "sensor.dawn", "sensor", "measurement", "lx", [(_ts(h, m, day=19), v) for h, m, v in ramp] + [(_ts(6, 30), 0.0), (_ts(7, 30), 1000.0)] + _minutes(1000.0))
    light = service.compute_conditions(NOW)["light"]
    assert light is not None and _ts(16, 0, day=19) <= light["yesterday_to"] <= _ts(17, 0, day=19)


def test_own_sky_limits_replace_the_automatic_ones(service) -> None:
    _station(service, lux=80000.0)
    assert service.compute_conditions(NOW)["sky"]["class"] == cond.SUNNY
    config = wetter_routes._load_config(service.deps.index)
    wetter_routes._save_config(service.deps.index, {**config, "sky_cuts_lx": [20000.0, 40000.0, 90000.0]})
    assert service.compute_conditions(NOW)["sky"]["class"] == cond.FAIR
    # ungültig gespeicherte Grenzen (nicht aufsteigend) fallen auf automatisch zurück
    wetter_routes._save_config(service.deps.index, {**config, "sky_cuts_lx": [90000.0, 40000.0, 20000.0]})
    assert wetter_routes._load_config(service.deps.index)["sky_cuts_lx"] == []
    assert wetter_routes.sky_cuts({**config, "sky_cuts_lx": []}) == pytest.approx(cond.lx_cuts(config["sun_threshold_lx"]))
