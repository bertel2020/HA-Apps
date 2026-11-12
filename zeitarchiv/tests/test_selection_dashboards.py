"""Übersicht „Passend für deine Dashboards“ im Auswahl-Assistenten und die Tabelle der Vorschläge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import energiedashboard_routes as edr
from app import selection_dashboards, wetter_routes
from app.storage.index import Index

_CATALOG = [
    {"entity_id": "sensor.druck", "domain": "sensor", "name": "Druck relativ", "device_class": "atmospheric_pressure",
     "unit": "hPa", "platform": "ecowitt", "device": "Station"},
    {"entity_id": "sensor.wp", "domain": "sensor", "name": "Wärmepumpe", "energy_role": "consumer",
     "device_class": "energy", "state_class": "total_increasing", "unit": "kWh"},
    {"entity_id": "sensor.netz_bezug", "domain": "sensor", "name": "Netzbezug", "energy_role": "grid_import",
     "device_class": "energy", "state_class": "total_increasing", "unit": "kWh"},
]


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "index.sqlite")
    yield idx
    idx.close()


def test_no_cards_while_both_dashboards_are_off(index) -> None:
    assert selection_dashboards.cards(index, _CATALOG, set()) == []


def test_weather_card_separates_filled_suggested_and_open_roles(index) -> None:
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    index.set_setting(wetter_routes.SETTING_CONFIG, json.dumps({"humidity": "sensor.feuchte"}))
    card = selection_dashboards.wetter(index, _CATALOG, {"sensor.feuchte"})
    states = {item["label"]: item["state"] for item in card["items"]}
    assert states["Luftfeuchte"] == "set"
    assert states["Luftdruck"] == "suggest"
    assert "open" in states.values()
    assert card["total"] == len(card["items"]) and card["set"] == 1


def test_energy_card_has_one_entry_per_suggestion_never_several_sources_in_one(index) -> None:
    index.set_setting(edr.SETTING_ENABLED, "1")
    index.set_setting(edr.SETTING_CONFIG, json.dumps({**edr._empty_config(), "einspeisung": "sensor.einsp"}))  # noqa: SLF001
    catalog = [*_CATALOG, {**_CATALOG[1], "entity_id": "sensor.pv1", "name": "PV 1", "energy_role": "solar"},
               {**_CATALOG[1], "entity_id": "sensor.pv2", "name": "PV 2", "energy_role": "solar"}]
    card = selection_dashboards.energie(index, catalog, set())
    producers = [item for item in card["items"] if item["label"] == "Erzeuger"]
    assert [item["ids"] for item in producers] == [["sensor.pv1"], ["sensor.pv2"]]
    by_label = {item["label"]: item for item in card["items"] if item["label"] != "Erzeuger"}
    assert by_label["Netzbezug"]["state"] == "suggest" and by_label["Einspeisung"]["state"] == "set"


def test_result_lists_the_suggestions_as_ordinary_rows_grouped_by_dashboard(index) -> None:
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    index.set_setting(wetter_routes.SETTING_CONFIG, json.dumps({}))
    index.set_setting(edr.SETTING_ENABLED, "1")
    got = selection_dashboards.result(index, _CATALOG, {"sensor.druck"})
    groups = {group["name"]: {row["entity_id"]: row for row in group["rows"]} for group in got["recommended"]}
    assert groups["Wetterdashboard"]["sensor.druck"]["status"] == "archived"
    assert groups["Wetterdashboard"]["sensor.druck"]["reason"] == "Rolle: Luftdruck"
    assert groups["Energiedashboard"]["sensor.wp"]["status"] == "new"
    assert got["counts"]["recommended_new"] >= 1 and got["topic"] == "dashboards"


def test_selection_page_has_a_dashboards_tile_and_view_only_when_a_dashboard_is_on(client) -> None:
    import os

    from app import selection_catalog

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    selection_catalog.save(data_dir, _CATALOG)
    try:
        assert "topic=dashboards" not in client.get("/selection?topic=wetter").text  # beide aus
        client.post("/wetterdashboard/enable", follow_redirects=False)
        topic_page = client.get("/selection?topic=wetter").text
        assert "topic=dashboards" in topic_page and "Rollen deiner Dashboards" not in topic_page
        page = client.get("/selection?topic=dashboards").text
        assert "Rollen deiner Dashboards" in page and "Wetterdashboard" in page
        assert 'value="sensor.druck"' in page  # normale Zeile mit Kästchen
        assert "sel-dash-card" not in client.get("/selection?q=druck").text
    finally:
        client.post("/wetterdashboard/disable", follow_redirects=False)
        (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)


def test_dashboard_suggestions_go_through_the_normal_apply_confirmation(client) -> None:
    import os

    from app import selection_catalog

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    selection_catalog.save(data_dir, _CATALOG, phase1=True)
    try:
        client.post("/wetterdashboard/enable", follow_redirects=False)
        reply = client.post("/selection/apply", data={
            "topic": "dashboards", "mode": "label", "entity_id": "sensor.druck",
        })
        assert reply.status_code == 200 and "Thema Dashboards" in reply.text
        assert "Gehört nicht zu diesem Thema" not in reply.text
    finally:
        client.post("/wetterdashboard/disable", follow_redirects=False)
        (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)


def test_selection_template_renders_the_table_with_group_boxes() -> None:
    root = Path(selection_dashboards.__file__).parent
    table = (root / "templates" / "_selection_table.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "pages" / "selection.js").read_text(encoding="utf-8")
    assert 'class="sel-group-all"' in table and "<thead>" in table
    assert "sel-group-all" in script


def test_topic_without_preselection_says_so_and_starts_with_zero_marked(client) -> None:
    import os

    from app import selection_catalog

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    door = {"entity_id": "binary_sensor.haustuer", "domain": "binary_sensor", "name": "Haustür", "device_class": "door"}
    selection_catalog.save(data_dir, [door], phase1=True)
    try:
        page = client.get("/selection?topic=tueren_fenster").text
        assert "Hier ist nichts vorausgewählt" in page
        assert '<b id="sel-checked">0</b>' in page and 'value="binary_sensor.haustuer"' in page
        assert " checked" not in page.split('value="binary_sensor.haustuer"')[1].split(">")[0]
    finally:
        (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)


def test_tile_shows_recommended_and_new_counts(client) -> None:
    import os

    from app import selection_catalog

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    door = {"entity_id": "binary_sensor.haustuer", "domain": "binary_sensor", "name": "Haustür", "device_class": "door"}
    selection_catalog.save(data_dir, [door], phase1=True)
    try:
        page = client.get("/selection").text
        assert "empfohlen</span>" not in page  # Zahl und Wort stehen in einer Zeile
        assert " neu</span>" in page
    finally:
        (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)
