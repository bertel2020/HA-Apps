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


def test_direction_elevation_and_pv_power_are_suggested_by_name_and_unit() -> None:
    archived = [
        _entity("sensor.station_wind", "km/h"),
        _entity("sensor.station_windrichtung", "°"),
        _entity("sensor.sonne_hoehe", "°"),
        _entity("sensor.sun_solar_azimuth", "°"),
        _entity("sensor.sun_solar_elevation", "°"),
        _entity("sensor.dach_pv_leistung", "W"),
        _entity("sensor.balkonkraftwerk_pv_leistung", "W"),
        _entity("sensor.pv_prognose_leistung", "W"),
        _entity("sensor.pv_ertrag", "kWh", "counter", "total_increasing"),
        _entity("sensor.wohnzimmer_leistung", "W"),
    ]
    labels = {row["entity_id"]: row["entity_id"].split(".", 1)[1].replace("_", " ") for row in archived}
    roles = wetter_autofill.suggest(archived, labels)["roles"]
    assert roles["wind_direction"]["entity_id"] == "sensor.station_windrichtung"
    assert roles["wind"]["entity_id"] == "sensor.station_wind"  # die Richtung wird nicht als Windstärke vorgeschlagen
    assert roles["sun_elevation"]["entity_id"] == "sensor.sun_solar_elevation"  # Azimut ist keine Höhe
    assert roles["pv_power"]["entity_id"] == "sensor.dach_pv_leistung"  # Dachanlage vor Balkonkraftwerk, keine Prognose
    assert roles["pv_power"]["confidence"] == "ok"


# ---- suggest_for_entities: nur für eine Auswahl, ohne Archiv ---------------------------------

def _full_catalog():
    return [
        _catalog("sensor.sun_solar_elevation", name="Sun Solare Elevation", unit="°", platform="sun"),
        _catalog("sensor.ecowitt_druck_relativ", name="Druck relativ", device_class="atmospheric_pressure",
                 unit="hPa", platform="ecowitt", device=STATION),
        _catalog("sensor.ecowitt_temp_aussen", name="Außentemperatur", device_class="temperature",
                 unit="°C", platform="ecowitt", device=STATION),
        _catalog("sensor.helligkeit_sued", name="Helligkeit Süd", device_class="illuminance", unit="lx",
                 platform="ecowitt", device=STATION),
        _catalog("sensor.helligkeit_ost", name="Helligkeit Ost", device_class="illuminance", unit="lx",
                 platform="ecowitt", device=STATION),
        _catalog("sensor.keller_temperatur", name="Keller", device_class="temperature", unit="°C",
                 platform="zha", device="Keller"),
    ]


def test_suggest_for_entities_uses_only_the_given_entities_and_needs_no_archive() -> None:
    result = wetter_autofill.suggest_for_entities(
        ["sensor.sun_solar_elevation", "sensor.ecowitt_druck_relativ"], _full_catalog(),
    )
    assert set(result) == {"sun_elevation", "pressure"}
    assert result["sun_elevation"]["entity_id"] == "sensor.sun_solar_elevation"
    assert result["pressure"]["entity_id"] == "sensor.ecowitt_druck_relativ"


def test_suggest_for_entities_ignores_everything_outside_the_selection() -> None:
    result = wetter_autofill.suggest_for_entities(["sensor.ecowitt_druck_relativ"], _full_catalog())
    assert set(result) == {"pressure"}


def test_suggest_for_entities_never_proposes_roles_that_are_already_filled() -> None:
    result = wetter_autofill.suggest_for_entities(
        ["sensor.sun_solar_elevation", "sensor.ecowitt_druck_relativ"], _full_catalog(), filled={"pressure"},
    )
    assert set(result) == {"sun_elevation"}


def test_suggest_for_entities_marks_several_close_candidates_as_check() -> None:
    result = wetter_autofill.suggest_for_entities(
        ["sensor.helligkeit_sued", "sensor.helligkeit_ost"], _full_catalog(), anchor_id="sensor.ecowitt_temp_aussen",
    )
    pick = result["illuminance"]
    assert pick["entity_id"] == "sensor.helligkeit_sued"  # „Süd“ gilt für die Sonnenscheindauer
    assert {c["entity_id"] for c in pick["candidates"]} == {"sensor.helligkeit_ost"}


def test_suggest_for_entities_with_unknown_entities_returns_nothing() -> None:
    assert wetter_autofill.suggest_for_entities(["sensor.gibt_es_nicht"], _full_catalog()) == {}
    assert wetter_autofill.suggest_for_entities([], _full_catalog()) == {}


def _state(entity_id, texts=("cloudy", "snowy")):
    return {**_entity(entity_id, kind="state"), "state_texts": list(texts)}


def _weather_pick(archived, catalog=None):
    labels = {row["entity_id"]: row["entity_id"].split(".", 1)[1].replace("_", " ") for row in archived}
    return wetter_autofill.suggest(archived, labels, catalog)["roles"].get("weather_state")


def test_weather_state_suggests_a_weather_entity_and_prefers_the_one_without_forecast() -> None:
    pick = _weather_pick([_state("weather.forecast_home"), _state("weather.home")])
    assert pick["entity_id"] == "weather.home" and pick["confidence"] == "ok"
    assert [item["entity_id"] for item in pick["candidates"]] == ["weather.forecast_home"]
    assert _weather_pick([_state("weather.forecast_home")])["entity_id"] == "weather.forecast_home"


def test_weather_state_suggests_a_sensor_with_the_standard_values_and_a_fitting_name() -> None:
    pick = _weather_pick([_state("sensor.wetterzustand", ("snowy", "rainy", "snowy-rainy"))])
    assert pick["entity_id"] == "sensor.wetterzustand" and pick["confidence"] == "ok"
    # Auch der Standardwert allein trägt einen Sensor, dessen Name nichts verrät
    assert _weather_pick([_state("sensor.station_3", ("cloudy", "sunny"))])["entity_id"] == "sensor.station_3"


def test_weather_state_rejects_translated_texts_and_numeric_codes() -> None:
    # „Bedeckt“ ist übersetzter Text, kein Schlüssel der Standardliste
    assert _weather_pick([_state("sensor.openweathermap_wetter", ("Bedeckt", "Leichter Regen"))]) is None
    # Auch mit passendem Namen: bekannte Texte ohne Standardwert lehnen den Sensor ab
    assert _weather_pick([_state("sensor.wetterzustand", ("Bedeckt",))]) is None
    # Zahlencodes und Messwerte sind keine Text-Zustände
    assert _weather_pick([_entity("sensor.openweathermap_wetter_code", kind="standard")]) is None
    assert _weather_pick([_entity("sensor.wetterzustand", kind="standard")]) is None
    assert _weather_pick([_state("sensor.wetter_code", ("cloudy",))]) is None
    assert _weather_pick([_state("sensor.wetter_symbol", ("cloudy",))]) is None


def test_weather_state_takes_the_condition_sensor_but_not_other_domains() -> None:
    pick = _weather_pick([_state("sensor.openweathermap_condition", ("cloudy",)), _state("sensor.openweathermap_wetter", ("Bedeckt",))])
    assert pick["entity_id"] == "sensor.openweathermap_condition"
    assert _weather_pick([_state("binary_sensor.wetterzustand", ("snowy",))]) is None


def test_weather_state_from_the_catalog_needs_no_archive_and_ignores_codes() -> None:
    catalog = [
        _catalog("weather.mycitylife", name="MyCityLife", platform="google_weather"),
        _catalog("sensor.wetterzustand", name="Wetterzustand"),
        _catalog("sensor.openweathermap_weather_code", name="OpenWeatherMap Wetter-Code"),
    ]
    picks = wetter_autofill.suggest_for_entities([row["entity_id"] for row in catalog], catalog)
    assert picks["weather_state"]["entity_id"] in {"weather.mycitylife", "sensor.wetterzustand"}
    assert "sensor.openweathermap_weather_code" not in {
        pick["entity_id"] for role, pick in picks.items() if role == "weather_state"
    } | {item["entity_id"] for item in picks["weather_state"]["candidates"]}
    assert wetter_autofill.suggest_for_entities(["sensor.openweathermap_weather_code"], catalog).get("weather_state") is None
    assert wetter_autofill.suggest_for_entities(["weather.mycitylife"], catalog, filled=["weather_state"]) .get("weather_state") is None


def test_weather_state_role_is_part_of_the_suggested_roles() -> None:
    assert "weather_state" in wetter_autofill.ROLE_KEYS
    assert wetter_autofill.ROLE_KEYS.index("weather_state") == wetter_autofill.ROLE_KEYS.index("precip_dur") + 1

