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
        _row("sensor.wohnzimmer_temp", "Wohnzimmer", device_class="temperature", area="Wohnzimmer"),
        _row("sensor.bad_feuchte", "Bad", device_class="humidity", area="Bad"),
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
        _row("sensor.diag", "Wohnzimmer", device_class="temperature", entity_category="diagnostic", area="Wohnzimmer"),
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
        _row("sensor.a", device_class="temperature", device="Thermo", area="Bad"),
        _row("sensor.b", device_class="humidity", device="Thermo", area="Bad"),
        _row("sensor.c", device_class="temperature", device="Andere", area="Flur"),
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


def test_battery_low_flag_is_only_more_when_the_device_has_a_percentage() -> None:
    rec, more, _ = _rows("batterien", [
        _row("sensor.tuer_batterie", "Tür", device_class="battery", unit="%", device="Türkontakt"),
        _row("binary_sensor.tuer_batterie_schwach", "Tür schwach", device_class="battery", device="Türkontakt"),
        _row("binary_sensor.melder_batterie_schwach", "Melder schwach", device_class="battery", device="Melder"),
    ])
    assert set(rec) == {"sensor.tuer_batterie", "binary_sensor.melder_batterie_schwach"}
    assert set(more) == {"binary_sensor.tuer_batterie_schwach"}


def test_room_climate_needs_an_area_and_skips_setpoints() -> None:
    rec, more, _ = _rows("klima", [
        _row("sensor.bad_temp", "Bad", device_class="temperature", area="Bad"),
        _row("sensor.ohne_bereich", "Irgendwo", device_class="temperature"),
        _row("sensor.feuchte_ohne_bereich", "Irgendwo", device_class="humidity"),
        _row("sensor.bad_soll", "Bad Zielwert", device_class="temperature", area="Bad"),
        _row("sensor.bad_offset", "Bad Temperatur Offset", device_class="temperature", area="Bad"),
        _row("climate.heizung", "Heizung", text=True),
    ])
    assert set(rec) == {"sensor.bad_temp", "climate.heizung"}
    assert set(more) == {"sensor.ohne_bereich", "sensor.feuchte_ohne_bereich", "sensor.bad_soll", "sensor.bad_offset"}


def test_system_recommends_only_host_main_values() -> None:
    rec, more, _ = _rows("system", [
        _row("sensor.node_pve_cpu_auslastung", "CPU Auslastung", platform="proxmoxve", unit="%"),
        _row("sensor.node_pve_prozentsatz_genutzter_arbeitsspeicher", "Arbeitsspeicher belegt", platform="proxmoxve", unit="%"),
        _row("sensor.node_pve_prozentsatz_freier_arbeitsspeicher", "Arbeitsspeicher frei", platform="proxmoxve", unit="%"),
        _row("sensor.node_pve_gesamtarbeitsspeicher", "Arbeitsspeicher gesamt", platform="proxmoxve", unit="GiB"),
        _row("sensor.lxc_traccar_105_cpu_auslastung", "CPU Auslastung", platform="proxmoxve", unit="%"),
        _row("sensor.qemu_win_vm_104_cpu_auslastung", "CPU Auslastung", platform="proxmoxve", unit="%"),
        _row("sensor.disk_pve_ssd_temperatur", "Temperatur", platform="proxmoxve", device_class="temperature"),
        _row("sensor.qnap01_temperature_drive_0_1", "Temperature Drive", platform="qnap", device_class="temperature"),
        _row("sensor.qnap01_cpu_temperature", "CPU Temperature", platform="qnap", device_class="temperature"),
        _row("sensor.home_assistant_core_cpu_prozent", "Core CPU", platform="hassio", unit="%"),
        _row("sensor.grafana_cpu_percent", "Grafana CPU", platform="hassio", unit="%"),
    ])
    assert set(rec) == {
        "sensor.node_pve_cpu_auslastung", "sensor.node_pve_prozentsatz_genutzter_arbeitsspeicher",
        "sensor.qnap01_cpu_temperature", "sensor.home_assistant_core_cpu_prozent",
    }
    assert {
        "sensor.node_pve_prozentsatz_freier_arbeitsspeicher", "sensor.node_pve_gesamtarbeitsspeicher",
        "sensor.lxc_traccar_105_cpu_auslastung", "sensor.qemu_win_vm_104_cpu_auslastung",
        "sensor.disk_pve_ssd_temperatur", "sensor.qnap01_temperature_drive_0_1", "sensor.grafana_cpu_percent",
    } <= set(more)


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
        _row("binary_sensor.klappe", "Klappe", device_class="opening"),
        _row("binary_sensor.bewegung", "Flur", device_class="motion"),
    ])
    assert rec["binary_sensor.haustuer"]["reason"] == "Türkontakt"
    assert set(rec) == {"binary_sensor.haustuer", "binary_sensor.bad_fenster", "binary_sensor.garage"}
    assert set(more) == {"lock.haustuer", "binary_sensor.klappe"}


def test_wetter_recommends_a_rain_switch_but_not_a_leak_sensor() -> None:
    catalog = [
        _row("binary_sensor.regensensor", "Regensensor Garten", device_class="moisture"),
        _row("binary_sensor.rain", "Rain", device_class=None),
        _row("binary_sensor.waschkeller_wasser", "Wasserleck Keller", device_class="moisture"),
    ]
    rec, more, _result = _rows("wetter", catalog)
    assert {"binary_sensor.regensensor", "binary_sensor.rain"} <= set(rec)
    assert "binary_sensor.waschkeller_wasser" not in rec and "binary_sensor.waschkeller_wasser" not in more


# ---- Weniger Rauschen (Maßnahme 1) ------------------------------------------------------------

def test_indoor_outdoor_meter_is_not_an_outdoor_sensor() -> None:
    meter = _row("sensor.bad_indoor_outdoor_meter_temperatur", "Bad Indoor/Outdoor Meter Temperatur",
                 device_class="temperature", area="Bad", platform="switchbot")
    garden = _row("sensor.garten_temperatur", "Garten Temperatur", device_class="temperature", area="Garten")
    wetter_rec, _more, _ = _rows("wetter", [meter, garden])
    assert "sensor.garten_temperatur" in wetter_rec and "sensor.bad_indoor_outdoor_meter_temperatur" not in wetter_rec
    klima_rec, _more, _ = _rows("klima", [meter])
    assert "sensor.bad_indoor_outdoor_meter_temperatur" in klima_rec  # gehört jetzt zum Raumklima


def test_derived_weather_values_are_only_more_hits() -> None:
    rows = [
        _row("sensor.station_temperatur", "Station Außentemperatur", device_class="temperature", platform="ecowitt"),
        _row("sensor.station_gefuehlt", "Station gefühlte Temperatur", device_class="temperature", platform="ecowitt"),
        _row("sensor.station_taupunkt", "Station Taupunkt", device_class="temperature", platform="ecowitt"),
    ]
    rec, more, _ = _rows("wetter", rows)
    assert set(rec) == {"sensor.station_temperatur"} and set(more) == {"sensor.station_gefuehlt", "sensor.station_taupunkt"}


def test_vehicle_and_appliance_doors_are_only_more_hits() -> None:
    rows = [
        _row("binary_sensor.haustuer", "Haustür", device_class="door"),
        _row("binary_sensor.auto_tuer", "Auto Tür", device_class="door", platform="audiconnect"),
        _row("binary_sensor.waschmaschine_tuer", "Waschmaschine Tür", device_class="door", platform="home_connect"),
    ]
    rec, more, _ = _rows("tueren_fenster", rows)
    assert set(rec) == {"binary_sensor.haustuer"}
    assert set(more) == {"binary_sensor.auto_tuer", "binary_sensor.waschmaschine_tuer"}


def test_camera_detections_and_printers_are_not_presence() -> None:
    rows = [
        _row("binary_sensor.wohnzimmer_praesenz", "Wohnzimmer Präsenz", device_class="occupancy"),
        _row("binary_sensor.hof_katze", "Hof Katze", device_class="occupancy", platform="frigate"),
        _row("binary_sensor.drucker_filament", "Drucker Filament", device_class="occupancy", platform="moonraker"),
    ]
    rec, more, _ = _rows("anwesenheit", rows)
    assert set(rec) == {"binary_sensor.wohnzimmer_praesenz"} and len(more) == 2


def test_smoothed_thermostat_values_and_battery_voltage_are_only_more_hits() -> None:
    ema = _row("sensor.thermo_temperatur_ema", "Thermostat Temperatur EMA", device_class="temperature", area="Bad")
    room = _row("sensor.bad_temperatur", "Bad Temperatur", device_class="temperature", area="Bad")
    rec, more, _ = _rows("klima", [ema, room])
    assert set(rec) == {"sensor.bad_temperatur"} and "sensor.thermo_temperatur_ema" in more
    volt = _row("sensor.batt_v", "Sensor Batterie", device_class="battery", unit="V")
    percent = _row("sensor.batt_p", "Sensor Batterie Stand", device_class="battery", unit="%")
    rec, more, _ = _rows("batterien", [volt, percent])
    assert set(rec) == {"sensor.batt_p"} and set(more) == {"sensor.batt_v"}


# ---- Doppelte (Maßnahme 2) -------------------------------------------------------------------

def _door(entity_id, platform, device="Kontakt Haustür", **fields):
    return _row(entity_id, device, device_class="door", platform=platform, device=device, **fields)


def test_same_device_from_two_integrations_keeps_one_recommended_and_marks_the_other() -> None:
    rows = [_door("binary_sensor.a_haustuer", "eltako"), _door("binary_sensor.b_haustuer", "enocean_eltako")]
    rec, more, _ = _rows("tueren_fenster", rows)
    assert set(rec) == {"binary_sensor.a_haustuer"}  # alphabetisch erste Integration
    assert more["binary_sensor.b_haustuer"]["reason"] == "Doppelt: dieselbe Entität kommt auch über eltako"


def test_the_already_archived_duplicate_is_the_one_that_stays() -> None:
    rows = [_door("binary_sensor.a_haustuer", "eltako"), _door("binary_sensor.b_haustuer", "enocean_eltako")]
    rec, more, _ = _rows("tueren_fenster", rows, archived={"binary_sensor.b_haustuer"})
    assert set(rec) == {"binary_sensor.b_haustuer"} and set(more) == {"binary_sensor.a_haustuer"}


def test_several_rows_per_integration_are_not_a_duplicate_pair() -> None:
    rows = [
        _door("binary_sensor.a1", "eltako"), _door("binary_sensor.a2", "eltako"),
        _door("binary_sensor.b1", "enocean_eltako"),
    ]
    rec, _more, _ = _rows("tueren_fenster", rows)
    assert set(rec) == {"binary_sensor.a1", "binary_sensor.a2", "binary_sensor.b1"}


def test_rows_without_a_device_are_never_collapsed() -> None:
    rows = [_row("binary_sensor.x", "X", device_class="door", platform="a"), _row("binary_sensor.y", "Y", device_class="door", platform="b")]
    rec, _more, _ = _rows("tueren_fenster", rows)
    assert len(rec) == 2
