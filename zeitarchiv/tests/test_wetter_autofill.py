"""Rollenvorschläge für die Einrichtung des Wetterdashboards."""

from __future__ import annotations

from app import wetter_autofill


def _entity(entity_id, unit=None, kind="standard", state_class=None):
    return {"entity_id": entity_id, "aggregation_type": kind, "unit": unit, "state_class": state_class}


def _catalog(entity_id, **fields):
    return {"entity_id": entity_id, **fields}


STATION = "Ecowitt GW2000"


def _station():
    archived = [
        _entity("sensor.ecowitt_temp_aussen", "°C"),
        _entity("sensor.ecowitt_feuchte_aussen", "%"),
        _entity("sensor.ecowitt_druck_relativ", "hPa"),
        _entity("sensor.ecowitt_druck_absolut", "hPa"),
        _entity("sensor.ecowitt_wind", "km/h"),
        _entity("sensor.ecowitt_boe", "km/h"),
        _entity("sensor.ecowitt_regen_gesamt", "mm", "counter", "total_increasing"),
        _entity("sensor.ecowitt_regenrate", "mm/h"),
        _entity("sensor.keller_temperatur", "°C"),
        _entity("sensor.keller_feuchte", "%"),
        _entity("sensor.cpu_temperatur", "°C"),
        _entity("sensor.waermepumpe_energie", "kWh", "counter", "total_increasing"),
    ]
    catalog = [
        _catalog("sensor.ecowitt_temp_aussen", device_class="temperature", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_feuchte_aussen", device_class="humidity", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_druck_relativ", device_class="atmospheric_pressure", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_druck_absolut", device_class="atmospheric_pressure", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_wind", device_class="wind_speed", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_boe", device_class="wind_speed", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_regen_gesamt", device_class="precipitation", platform="ecowitt", device=STATION),
        _catalog("sensor.keller_temperatur", device_class="temperature", platform="zha", device="Keller"),
        _catalog("sensor.keller_feuchte", device_class="humidity", platform="zha", device="Keller"),
        _catalog("sensor.cpu_temperatur", device_class="temperature", platform="systemmonitor", device="Server"),
    ]
    labels = {row["entity_id"]: row["entity_id"].split(".", 1)[1].replace("_", " ") for row in archived}
    return archived, labels, catalog


def test_station_roles_are_filled_from_one_device() -> None:
    archived, labels, catalog = _station()
    result = wetter_autofill.suggest(archived, labels, catalog)
    roles = result["roles"]
    assert roles["temperature"]["entity_id"] == "sensor.ecowitt_temp_aussen"
    assert roles["humidity"]["entity_id"] == "sensor.ecowitt_feuchte_aussen"
    assert roles["wind"]["entity_id"] == "sensor.ecowitt_wind"
    assert roles["gust"]["entity_id"] == "sensor.ecowitt_boe"
    assert roles["precip_mm"]["entity_id"] == "sensor.ecowitt_regen_gesamt"
    assert "sensor.keller_temperatur" not in {pick["entity_id"] for pick in roles.values()}
    assert "gleiches Gerät" in roles["humidity"]["reason"]
    assert "precip_dur" not in roles and "radiation" not in roles


def test_two_pressure_sensors_prefer_the_relative_one_but_ask_for_a_check() -> None:
    archived, labels, catalog = _station()
    pressure = wetter_autofill.suggest(archived, labels, catalog)["roles"]["pressure"]
    assert pressure["entity_id"] == "sensor.ecowitt_druck_relativ"
    assert [c["entity_id"] for c in pressure["candidates"]] == ["sensor.ecowitt_druck_absolut"]


def test_device_temperatures_and_indoor_sensors_are_not_suggested_as_outdoor_temperature() -> None:
    archived = [_entity("sensor.cpu_temperatur", "°C"), _entity("sensor.keller_temperatur", "°C")]
    catalog = [
        _catalog("sensor.cpu_temperatur", device_class="temperature", platform="systemmonitor", device="Server"),
        _catalog("sensor.keller_temperatur", device_class="temperature", platform="zha", device="Keller"),
    ]
    assert wetter_autofill.suggest(archived, {}, catalog)["roles"] == {}


def test_without_a_catalog_unit_and_name_are_enough() -> None:
    archived = [_entity("sensor.garten_temperatur", "°C"), _entity("sensor.garten_feuchte", "%")]
    result = wetter_autofill.suggest(archived, {"sensor.garten_temperatur": "Garten Temperatur", "sensor.garten_feuchte": "Garten Feuchte"})
    assert result["has_catalog"] is False
    assert result["roles"]["temperature"]["entity_id"] == "sensor.garten_temperatur"
    assert result["roles"]["humidity"]["entity_id"] == "sensor.garten_feuchte"


def test_a_rain_switch_needs_rain_in_the_name() -> None:
    archived = [
        _entity("binary_sensor.regensensor_terrasse", None, "switch"),
        _entity("binary_sensor.wasserleck_keller", None, "switch"),
    ]
    catalog = [
        _catalog("binary_sensor.regensensor_terrasse", device_class="moisture", platform="demo", device="Terrasse"),
        _catalog("binary_sensor.wasserleck_keller", device_class="moisture", platform="demo", device="Keller"),
    ]
    roles = wetter_autofill.suggest(archived, {}, catalog)["roles"]
    assert roles["precip_dur"]["entity_id"] == "binary_sensor.regensensor_terrasse"


def test_heating_counters_are_found_by_name_and_unit() -> None:
    archived, labels, catalog = _station()
    heating = wetter_autofill.suggest(archived, labels, catalog)["heating"]
    assert [item["entity_id"] for item in heating] == ["sensor.waermepumpe_energie"]


def test_matching_entities_that_are_not_archived_are_reported_separately() -> None:
    archived, labels, catalog = _station()
    catalog = catalog + [_catalog("sensor.dwd_strahlung", device_class="irradiance", platform="dwd_weather", device="DWD", unit="W/m²", name="DWD Strahlung")]
    result = wetter_autofill.suggest(archived, labels, catalog)
    assert "radiation" not in result["roles"]
    assert [item["role"] for item in result["missing"]] == ["radiation"]


def test_extra_sensors_are_other_temperature_and_humidity_sensors_without_device_temperatures() -> None:
    archived, labels, catalog = _station()
    extras = wetter_autofill.suggest(archived, labels, catalog)["extras"]
    ids = [entry["entity_id"] for entry in extras]
    assert ids == ["sensor.keller_feuchte", "sensor.keller_temperatur"]
    assert "sensor.cpu_temperatur" not in ids


def test_illuminance_prefers_the_south_sensor_of_the_station_and_skips_the_moon() -> None:
    archived = [
        _entity("sensor.station_temperatur", "°C"),
        _entity("sensor.station_illuminance_dawn", "lx"),
        _entity("sensor.station_illuminance_east", "lx"),
        _entity("sensor.station_illuminance_central", "lx"),
        _entity("sensor.station_illuminance_west", "lx"),
        _entity("sensor.wolfegg_moon_illuminance", "lx"),
    ]
    catalog = [
        _catalog("sensor.station_temperatur", device_class="temperature", platform="eltako", device="Wetterstation"),
        *[
            _catalog(f"sensor.station_illuminance_{name}", device_class="illuminance", platform="eltako", device="Wetterstation")
            for name in ("dawn", "east", "central", "west")
        ],
        _catalog("sensor.wolfegg_moon_illuminance", device_class="illuminance", platform="moon", device="Mond"),
    ]
    labels = {row["entity_id"]: row["entity_id"] for row in archived}
    pick = wetter_autofill.suggest(archived, labels, catalog)["roles"]["illuminance"]
    assert pick["entity_id"] == "sensor.station_illuminance_central"
    assert pick["confidence"] == "ok"
    assert "sensor.wolfegg_moon_illuminance" not in {pick["entity_id"], *(c["entity_id"] for c in pick["candidates"])}
