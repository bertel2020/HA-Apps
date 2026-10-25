"""Ranking des Auswahl-Assistenten, Thema „Energie“ (ROADMAP 1.33)."""

from __future__ import annotations

from app import selection_catalog, selection_suggest


def _row(entity_id, name=None, **fields):
    base = {
        "entity_id": entity_id, "domain": entity_id.split(".", 1)[0], "name": name or entity_id,
        "device_class": None, "state_class": None, "unit": None, "platform": "shelly", "device": None,
        "area": None, "entity_category": None, "energy_role": None, "hidden": False,
        "selected": False, "excluded": False,
    }
    return {**base, **fields}


def _energy(entity_id, name=None, **fields):
    return _row(entity_id, name, device_class="energy", state_class="total_increasing", unit="kWh", **fields)


CATALOG = [
    _energy("sensor.netz_bezug", "Netzbezug", device="Shelly 3EM", energy_role="grid_import"),
    _energy("sensor.waermepumpe_energie", "Wärmepumpe Energie", device="Wärmepumpe", energy_role="consumer"),
    _row("sensor.netz_leistung", "Netzleistung", device="Shelly 3EM", device_class="power", unit="W", state_class="measurement"),
    _row("sensor.garage_leistung", "Garage Leistung", device="Garage", device_class="power", unit="W", state_class="measurement"),
    _energy("sensor.garage_energie", "Garage Energie", device="Garage"),
    _energy("sensor.netz_bezug_tag", "Netzbezug Tag", device="Shelly 3EM"),
    _energy("sensor.verbrauch_monatlich", "Verbrauch", platform="utility_meter"),
    _energy("sensor.versteckt", "Versteckt", hidden=True),
    _row("sensor.wohnzimmer_temperatur", "Wohnzimmer", device_class="temperature", unit="°C"),
    _row("light.decke", "Decke"),
]


def _by_id(groups):
    return {row["entity_id"]: row for group in groups for row in group["rows"]}


def test_dashboard_sources_are_recommended_with_their_reason() -> None:
    result = selection_suggest.suggest_energy(CATALOG)
    recommended = _by_id(result["recommended"])
    assert recommended["sensor.netz_bezug"]["reason"] == "Im Energie-Dashboard als Netzbezug"
    assert recommended["sensor.waermepumpe_energie"]["reason"] == "Im Energie-Dashboard als Verbraucher"


def test_power_on_a_dashboard_device_is_recommended_other_power_is_not() -> None:
    result = selection_suggest.suggest_energy(CATALOG)
    assert "sensor.netz_leistung" in _by_id(result["recommended"])
    assert _by_id(result["recommended"])["sensor.netz_leistung"]["high_rate"] is True
    more = _by_id(result["more"])
    assert "sensor.garage_leistung" in more and "sensor.garage_energie" in more


def test_daily_monthly_and_utility_meter_counters_are_not_suggested_but_counted() -> None:
    result = selection_suggest.suggest_energy(CATALOG)
    shown = set(_by_id(result["recommended"])) | set(_by_id(result["more"]))
    assert "sensor.netz_bezug_tag" not in shown
    assert "sensor.verbrauch_monatlich" not in shown
    assert result["counts"]["derived"] == 2


def test_hidden_other_domains_and_other_device_classes_are_ignored() -> None:
    shown = _by_id(selection_suggest.suggest_energy(CATALOG)["recommended"]) | _by_id(selection_suggest.suggest_energy(CATALOG)["more"])
    assert "sensor.versteckt" not in shown
    assert "sensor.wohnzimmer_temperatur" not in shown
    assert "light.decke" not in shown


def test_status_reflects_archive_selection_and_exclusion() -> None:
    catalog = [
        _energy("sensor.a", "A", energy_role="consumer", selected=True),
        _energy("sensor.b", "B", energy_role="consumer"),
        _energy("sensor.c", "C", energy_role="consumer", excluded=True),
        _energy("sensor.d", "D", energy_role="consumer", selected=True),
    ]
    rows = _by_id(selection_suggest.suggest_energy(catalog, archived={"sensor.d"})["recommended"])
    assert rows["sensor.a"]["status"] == "selected"
    assert rows["sensor.b"]["status"] == "new"
    assert rows["sensor.c"]["status"] == "excluded"
    assert rows["sensor.d"]["status"] == "archived"


def test_groups_put_the_group_with_most_new_entities_first() -> None:
    catalog = [
        _energy("sensor.a", "A", device="Zwei", energy_role="consumer"),
        _energy("sensor.b", "B", device="Eins", energy_role="consumer", selected=True),
        _energy("sensor.c", "C", device="Zwei", energy_role="consumer"),
    ]
    groups = selection_suggest.suggest_energy(catalog)["recommended"]
    assert [g["name"] for g in groups] == ["Zwei", "Eins"]


def test_unknown_topic_raises() -> None:
    try:
        selection_suggest.suggest("quatsch", [])
    except ValueError:
        return
    raise AssertionError("ValueError erwartet")


def test_catalog_cleaning_drops_invalid_rows_and_unknown_fields(tmp_path) -> None:
    rows = selection_catalog.clean_entities([
        {"entity_id": "sensor.gut", "name": " Gut ", "wert": 12, "selected": 1},
        {"entity_id": "Sensor.Böse"}, {"entity_id": "sensor.gut"}, "kaputt", {"name": "ohne id"},
    ])
    assert rows == [{
        "entity_id": "sensor.gut", "domain": "sensor", "name": "Gut", "device_class": None, "state_class": None,
        "unit": None, "platform": None, "device": None, "area": None, "entity_category": None,
        "energy_role": None, "hidden": False, "selected": True, "excluded": False, "text": False,
        "drifted": False, "via": [], "excluded_by": [], "origin": None, "topic": None, "added_ts": None,
    }]


def test_catalog_is_replaced_atomically_and_unreadable_files_give_none(tmp_path) -> None:
    assert selection_catalog.load(tmp_path) is None
    assert selection_catalog.save(tmp_path, [{"entity_id": "sensor.a"}]) == 1
    assert selection_catalog.save(tmp_path, [{"entity_id": "sensor.b"}, {"entity_id": "sensor.c"}]) == 2
    assert [row["entity_id"] for row in selection_catalog.load(tmp_path)["entities"]] == ["sensor.b", "sensor.c"]
    (tmp_path / selection_catalog.FILENAME).write_text("{kaputt", encoding="utf-8")
    assert selection_catalog.load(tmp_path) is None
