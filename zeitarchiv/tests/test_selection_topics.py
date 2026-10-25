"""Themenregeln des Auswahl-Assistenten außer Energie (ROADMAP 1.33, Phase 1)."""

from __future__ import annotations

import pytest

from app import selection_suggest, selection_topics


def _row(entity_id, name=None, **fields):
    base = {
        "entity_id": entity_id, "domain": entity_id.split(".", 1)[0], "name": name or entity_id,
        "device_class": None, "state_class": None, "unit": None, "platform": "demo", "device": None,
        "area": None, "entity_category": None, "energy_role": None, "hidden": False,
        "selected": False, "excluded": False, "text": False,
    }
    return {**base, **fields}


def _rows(topic, catalog, archived=()):
    result = selection_suggest.suggest(topic, catalog, archived)
    rec = {row["entity_id"]: row for group in result["recommended"] for row in group["rows"]}
    more = {row["entity_id"]: row for group in result["more"] for row in group["rows"]}
    return rec, more, result


def test_all_topics_are_known_and_have_a_label() -> None:
    assert selection_suggest.TOPICS == (
        "energie", "wetter", "klima", "luftqualitaet", "wasser_gas", "netzwerk", "batterien", "system", "tueren_fenster",
        "anwesenheit",
    )
    assert set(selection_topics.TOPIC_INFO) == set(selection_suggest.TOPICS)
    assert selection_topics.TOPIC_INFO["anwesenheit"]["sensitive"] is True
    assert selection_topics.TOPIC_INFO["tueren_fenster"]["sensitive"] is True
    assert selection_topics.TOPIC_INFO["tueren_fenster"]["note"]
    assert selection_topics.topic_label("klima") == "Temperatur & Klima"
    assert selection_topics.topic_label("unbekannt") == "unbekannt"


# ---- Wetter -------------------------------------------------------------------------------

def test_weather_entities_are_recommended_and_marked_as_text() -> None:
    rec, _, _ = _rows("wetter", [_row("weather.home", "Zuhause", text=True)])
    assert rec["weather.home"]["reason"] == "Wetterdienst: Zustand und Vorhersage"
    assert rec["weather.home"]["text"] is True and rec["weather.home"]["kind"] == "Text"


def test_weather_measurements_by_device_class() -> None:
    rec, more, _ = _rows("wetter", [
        _row("sensor.regen", device_class="precipitation", state_class="measurement"),
        _row("sensor.wind", device_class="wind_speed"),
        _row("sensor.druck_dienst", device_class="atmospheric_pressure", platform="met"),
        _row("sensor.druck_flur", device_class="atmospheric_pressure"),
        _row("sensor.strahlung", device_class="irradiance"),
        _row("sensor.uv", "UV", unit="UV index"),
    ])
    assert {"sensor.regen", "sensor.wind", "sensor.druck_dienst", "sensor.strahlung"} <= set(rec)
    assert {"sensor.druck_flur", "sensor.uv"} <= set(more)
    assert rec["sensor.wind"]["high_rate"] is True


def test_outdoor_temperature_by_name_area_or_weather_service() -> None:
    rec, _, _ = _rows("wetter", [
        _row("sensor.aussentemperatur", "Außentemperatur", device_class="temperature"),
        _row("sensor.t1", "Temperatur", device_class="temperature", area="Garten"),
        _row("sensor.owm_temp", "Temperatur", device_class="temperature", platform="openweathermap"),
        _row("sensor.wohnzimmer", "Wohnzimmer", device_class="temperature"),
    ])
    assert set(rec) == {"sensor.aussentemperatur", "sensor.t1", "sensor.owm_temp"}


def test_daily_rain_counters_are_derived() -> None:
    rec, more, result = _rows("wetter", [
        _row("sensor.regen_heute", "Regen heute", device_class="precipitation", state_class="total_increasing"),
    ])
    assert not rec and not more and result["counts"]["derived"] == 1


# ---- Temperatur & Klima -------------------------------------------------------------------

def test_room_climate_excludes_outdoor_and_device_temperatures() -> None:
    rec, more, _ = _rows("klima", [
        _row("sensor.wohnzimmer_temp", "Wohnzimmer", device_class="temperature"),
        _row("sensor.bad_feuchte", "Bad", device_class="humidity"),
        _row("sensor.co2", "Büro CO2", device_class="carbon_dioxide"),
        _row("climate.heizung", "Heizung", text=True),
        _row("sensor.cpu_temp", "CPU Temperatur", device_class="temperature"),
        _row("sensor.aussen", "Außen", device_class="temperature"),
        _row("sensor.pm25", device_class="pm25"),
    ])
    assert set(rec) == {"sensor.wohnzimmer_temp", "sensor.bad_feuchte", "climate.heizung"}
    assert "sensor.cpu_temp" in more
    assert "sensor.co2" not in rec and "sensor.co2" not in more and "sensor.pm25" not in more  # Thema „Luftqualität“
    assert "sensor.aussen" not in rec and "sensor.aussen" not in more


def test_diagnostic_entities_are_never_recommended_and_config_is_skipped() -> None:
    rec, more, _ = _rows("klima", [
        _row("sensor.diag", "Wohnzimmer", device_class="temperature", entity_category="diagnostic"),
        _row("sensor.conf", "Wohnzimmer", device_class="temperature", entity_category="config"),
        _row("sensor.hidden", "Wohnzimmer", device_class="temperature", hidden=True),
    ])
    assert not rec and set(more) == {"sensor.diag"}


# ---- Wasser & Gas -------------------------------------------------------------------------

def test_water_and_gas_from_the_energy_dashboard_and_meters() -> None:
    rec, more, result = _rows("wasser_gas", [
        _row("sensor.gas", device_class="gas", state_class="total_increasing", energy_role="gas"),
        _row("sensor.wasser", device_class="water", state_class="total_increasing", energy_role="water"),
        _row("sensor.garten_wasser", device_class="water", state_class="total_increasing", energy_role="water_consumer"),
        _row("sensor.gas_zweit", device_class="gas", state_class="total"),
        _row("sensor.wasser_tag", "Wasser Tag", device_class="water", state_class="total_increasing"),
        _row("sensor.fluss", device_class="volume_flow_rate"),
        _row("binary_sensor.leck", device_class="moisture"),
    ])
    assert set(rec) == {"sensor.gas", "sensor.wasser", "sensor.garten_wasser"}
    assert rec["sensor.gas"]["reason"] == "Im Energie-Dashboard als Gas"
    assert set(more) == {"sensor.gas_zweit", "sensor.fluss", "binary_sensor.leck"}
    assert result["counts"]["derived"] == 1


def test_energy_topic_does_not_pick_up_gas_or_water() -> None:
    rec, more, _ = _rows("energie", [
        _row("sensor.gas", device_class="gas", state_class="total_increasing", unit="m³", energy_role="gas"),
    ])
    assert not rec and not more


# ---- Netzwerk -----------------------------------------------------------------------------

def test_network_rules() -> None:
    rec, more, _ = _rows("netzwerk", [
        _row("sensor.speedtest_download", device_class="data_rate", platform="speedtestdotnet"),
        _row("sensor.fritz_down", device_class="data_rate", platform="fritz"),
        _row("binary_sensor.fritz_connected", device_class="connectivity", platform="fritz"),
        _row("binary_sensor.drucker_online", "Drucker", device_class="connectivity"),
        _row("sensor.nas_rate", device_class="data_rate"),
        _row("sensor.fritz_uptime", device_class="timestamp", platform="fritz"),
    ])
    assert set(rec) == {"sensor.speedtest_download", "sensor.fritz_down", "binary_sensor.fritz_connected"}
    assert set(more) == {"binary_sensor.drucker_online", "sensor.nas_rate"}


# ---- Anwesenheit --------------------------------------------------------------------------

def test_presence_rules() -> None:
    rec, more, _ = _rows("anwesenheit", [
        _row("person.anna", "Anna", text=True),
        _row("binary_sensor.flur_praesenz", device_class="occupancy"),
        _row("device_tracker.handy", text=True),
        _row("binary_sensor.flur_bewegung", device_class="motion"),
        _row("sensor.irgendwas"),
    ])
    assert set(rec) == {"person.anna", "binary_sensor.flur_praesenz"}
    assert set(more) == {"device_tracker.handy", "binary_sensor.flur_bewegung"}
    assert more["binary_sensor.flur_bewegung"]["high_rate"] is True


# ---- Gemeinsam ----------------------------------------------------------------------------

@pytest.mark.parametrize("topic", selection_suggest.TOPICS)
def test_recommended_new_rows_are_prechecked_locked_ones_and_more_are_not(topic) -> None:
    catalog = [
        _row("weather.a", text=True), _row("climate.a"), _row("person.a"),
        _row("sensor.gas", device_class="gas", state_class="total", energy_role="gas"),
        _row("sensor.speed", device_class="data_rate", platform="speedtestdotnet"),
        _row("sensor.netz", device_class="energy", state_class="total_increasing", energy_role="grid_import"),
        _row("person.b", selected=True), _row("climate.b", excluded=True),
    ]
    result = selection_suggest.suggest(topic, catalog, archived={"weather.b"})
    for group in result["recommended"]:
        for row in group["rows"]:
            assert row["checked"] is (row["status"] == "new")
    for group in result["more"]:
        assert all(row["checked"] is False for row in group["rows"])


def test_rows_are_grouped_by_device() -> None:
    _, _, result = _rows("klima", [
        _row("sensor.a", device_class="temperature", device="Thermo"),
        _row("sensor.b", device_class="humidity", device="Thermo"),
        _row("sensor.c", device_class="temperature", device="Andere"),
    ])
    groups = {group["name"]: [row["entity_id"] for row in group["rows"]] for group in result["recommended"]}
    assert groups == {"Thermo": ["sensor.a", "sensor.b"], "Andere": ["sensor.c"]}


# ---- Luftqualität, Batterien, System & Server, Türen & Fenster ---------------------------

def test_air_quality_recommends_co2_particles_and_voc_and_lists_pollutants_as_more() -> None:
    rec, more, _ = _rows("luftqualitaet", [
        _row("sensor.co2", "Büro CO2", device_class="carbon_dioxide"),
        _row("sensor.pm", "Feinstaub", device_class="pm25"),
        _row("sensor.voc", "VOC", device_class="volatile_organic_compounds"),
        _row("sensor.ozon", "Ozon", device_class="ozone"),
        _row("sensor.temp", "Raum", device_class="temperature"),
    ])
    assert set(rec) == {"sensor.co2", "sensor.pm", "sensor.voc"}
    assert set(more) == {"sensor.ozon"}


def test_batteries_stay_recommended_although_they_are_diagnostic_entities() -> None:
    rec, more, _ = _rows("batterien", [
        _row("sensor.fenster_batterie", "Fenster", device_class="battery", unit="%", entity_category="diagnostic"),
        _row("binary_sensor.rauchmelder_batterie", "Rauchmelder", device_class="battery"),
        _row("sensor.ladung", "Akku Staubsauger", unit="%"),
        _row("sensor.temp", "Raum", device_class="temperature"),
    ])
    assert set(rec) == {"sensor.fenster_batterie", "binary_sensor.rauchmelder_batterie"}
    assert set(more) == {"sensor.ladung"}


def test_system_topic_uses_the_monitoring_platforms_and_keeps_device_temperatures_out_of_the_room_climate() -> None:
    rows = [
        _row("sensor.processor_use", "Prozessorauslastung", platform="systemmonitor", unit="%"),
        _row("sensor.processor_temperature", "Prozessortemperatur", platform="systemmonitor", device_class="temperature"),
        _row("sensor.last_boot", "Letzter Start", platform="systemmonitor", device_class="timestamp"),
        _row("sensor.irgendwas", "Etwas", platform="systemmonitor", unit="kg"),
        _row("sensor.raum", "Raum", device_class="temperature"),
    ]
    rec, more, _ = _rows("system", rows)
    assert set(rec) == {"sensor.processor_use", "sensor.processor_temperature"}
    assert set(more) == {"sensor.last_boot", "sensor.irgendwas"}
    klima_rec, klima_more, _ = _rows("klima", rows)
    assert "sensor.processor_temperature" not in klima_rec and "sensor.processor_temperature" not in klima_more


def test_doors_and_windows_are_recommended_and_locks_are_more() -> None:
    rec, more, _ = _rows("tueren_fenster", [
        _row("binary_sensor.haustuer", "Haustür", device_class="door"),
        _row("binary_sensor.bad_fenster", "Bad", device_class="window"),
        _row("binary_sensor.garage", "Garage", device_class="garage_door"),
        _row("lock.haustuer", "Haustür Schloss", text=True),
        _row("binary_sensor.bewegung", "Flur", device_class="motion"),
    ])
    assert rec["binary_sensor.haustuer"]["reason"] == "Türkontakt"
    assert set(rec) == {"binary_sensor.haustuer", "binary_sensor.bad_fenster", "binary_sensor.garage"}
    assert set(more) == {"lock.haustuer"}
