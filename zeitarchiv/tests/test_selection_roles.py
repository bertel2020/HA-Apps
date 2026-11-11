"""Hinweis „Rolle im Wetterdashboard“ im Auswahl-Assistenten (ROADMAP 1.54, Schritt 2)."""

from __future__ import annotations

import json

import pytest

from app import selection_roles, selection_search, wetter_routes
from app.storage.index import Index

_RAW = [
    {"entity_id": "sensor.sun_solar_elevation", "name": "Sun Solare Elevation", "unit": "°", "platform": "sun"},
    {"entity_id": "sensor.druck", "name": "Druck relativ", "device_class": "atmospheric_pressure", "unit": "hPa",
     "platform": "ecowitt", "device": "Station"},
    {"entity_id": "binary_sensor.sun_solar_rising", "name": "Sun Solare Zunahme", "platform": "sun"},
]
CATALOG = [{"domain": row["entity_id"].split(".", 1)[0], **row} for row in _RAW]
IDS = [row["entity_id"] for row in CATALOG]


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "index.sqlite")
    yield idx
    idx.close()


def _enable(index, **roles) -> None:
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    index.set_setting(wetter_routes.SETTING_CONFIG, json.dumps(roles))


def test_no_hints_while_the_weather_dashboard_is_off(index) -> None:
    index.set_setting(wetter_routes.SETTING_CONFIG, json.dumps({}))
    assert selection_roles.wetter_hints(index, CATALOG, IDS) == {}


def test_hints_name_the_empty_role_a_listed_entity_fits(index) -> None:
    _enable(index)
    hints = selection_roles.wetter_hints(index, CATALOG, IDS)
    assert hints["sensor.sun_solar_elevation"]["role"] == "sun_elevation"
    assert hints["sensor.sun_solar_elevation"]["label"] == "Sonnenhöhe"
    assert hints["sensor.druck"]["role"] == "pressure"
    assert "binary_sensor.sun_solar_rising" not in hints


def test_filled_roles_get_no_hint(index) -> None:
    _enable(index, pressure="sensor.irgendein_druck")
    hints = selection_roles.wetter_hints(index, CATALOG, IDS)
    assert "sensor.druck" not in hints and "sensor.sun_solar_elevation" in hints


def test_hint_is_attached_only_to_the_matching_rows(index) -> None:
    _enable(index)
    result = selection_search.search("sun", CATALOG)
    selection_roles.attach(result, selection_roles.wetter_hints(index, CATALOG, IDS))
    rows = {row["entity_id"]: row for group in result["more"] for row in group["rows"]}
    assert rows["sensor.sun_solar_elevation"]["role_hint"]["label"] == "Sonnenhöhe"
    assert "role_hint" not in rows["binary_sensor.sun_solar_rising"]


def test_attach_without_hints_changes_nothing(index) -> None:
    result = selection_search.search("sun", CATALOG)
    selection_roles.attach(result, {})
    assert all("role_hint" not in row for group in result["more"] for row in group["rows"])


def test_selection_page_shows_the_role_hint_for_a_search_hit(client) -> None:
    import os
    from pathlib import Path

    from app import selection_catalog

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    target = data_dir / selection_catalog.FILENAME
    selection_catalog.save(data_dir, [
        {"entity_id": "sensor.sun_solar_elevation", "name": "Sun Solare Elevation", "unit": "°", "platform": "sun"},
        {"entity_id": "binary_sensor.sun_solar_rising", "name": "Sun Solare Zunahme", "platform": "sun"},
    ])
    try:
        assert "Rolle im Wetterdashboard" not in client.get("/selection?q=sun").text  # Dashboard aus
        client.post("/wetterdashboard/enable", follow_redirects=False)
        page = client.get("/selection?q=sun").text
        assert page.count("Rolle im Wetterdashboard: Sonnenhöhe") == 1
    finally:
        client.post("/wetterdashboard/disable", follow_redirects=False)
        target.unlink(missing_ok=True)


# ---- Block „Passt in das Wetterdashboard“ und Vorbelegung (Schritt 3) -------------------------

def _entry(ids, *, ts=1_000_000.0, action="add", source="assistant", status="applied", undone_by=None):
    return {"id": "e1", "ts": ts, "action": action, "source": source, "status": status, "entity_ids": ids,
            "results": {}, "undone_by": undone_by}


def test_offer_lists_recently_added_archived_entities_that_fit_an_empty_role(index) -> None:
    _enable(index)
    offer = selection_roles.wetter_offer(
        index, CATALOG, [_entry(["sensor.sun_solar_elevation", "sensor.druck"])],
        {"sensor.sun_solar_elevation", "sensor.druck"}, now=1_000_100.0,
    )
    assert [(row["entity_id"], row["role"]) for row in offer] == [
        ("sensor.druck", "pressure"), ("sensor.sun_solar_elevation", "sun_elevation"),
    ]
    assert offer[0]["name"] == "Druck relativ"


@pytest.mark.parametrize("entry", [
    _entry(["sensor.druck"], action="remove"),
    _entry(["sensor.druck"], source="integration"),
    _entry(["sensor.druck"], status="pending"),
    _entry(["sensor.druck"], undone_by="x"),
    _entry(["sensor.druck"], ts=1.0),  # zu alt
])
def test_offer_ignores_other_history_entries(index, entry) -> None:
    _enable(index)
    assert selection_roles.wetter_offer(index, CATALOG, [entry], {"sensor.druck"}, now=1_000_100.0) == []


def test_offer_waits_until_the_entity_is_archived(index) -> None:
    _enable(index)
    assert selection_roles.wetter_offer(index, CATALOG, [_entry(["sensor.druck"])], set(), now=1_000_100.0) == []


def test_offer_disappears_once_the_role_is_filled(index) -> None:
    _enable(index, pressure="sensor.druck")
    assert selection_roles.wetter_offer(index, CATALOG, [_entry(["sensor.druck"])], {"sensor.druck"}, now=1_000_100.0) == []


def test_weather_dashboard_opens_the_setup_with_valid_assignments_prefilled(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.assign_sonne", "sensor", "measurement", "°")
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    try:
        page = client.get("/wetterdashboard?assign=sun_elevation:sensor.assign_sonne&assign=pressure:sensor.nicht_archiviert").text
        assert 'hx-get="wetterdashboard/setup?assign=sun_elevation%3Asensor.assign_sonne"' in page
        assert "nicht_archiviert" not in page
        setup = client.get("/wetterdashboard/setup?assign=sun_elevation:sensor.assign_sonne&assign=pressure:sensor.nicht_archiviert").text
        assert '"sun_elevation": "sensor.assign_sonne"' in setup and '"pressure": ""' in setup
        assert "1 Rollen aus dem Auswahl-Assistenten vorbelegt" in setup
        assert "Rollen aus dem Auswahl-Assistenten vorbelegt" not in client.get("/wetterdashboard/setup").text
    finally:
        index.set_setting(wetter_routes.SETTING_CONFIG, "")
        index.set_setting(wetter_routes.SETTING_ENABLED, "0")


def test_prefill_never_overwrites_a_filled_role(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.assign_alt", "sensor", "measurement", "hPa")
    index.get_or_create_entity("sensor.assign_neu", "sensor", "measurement", "hPa")
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "pressure": "sensor.assign_alt"})  # noqa: SLF001
    try:
        setup = client.get("/wetterdashboard/setup?assign=pressure:sensor.assign_neu").text
        assert '"pressure": "sensor.assign_alt"' in setup and '"pressure": "sensor.assign_neu"' not in setup
        assert "Rollen aus dem Auswahl-Assistenten vorbelegt" not in setup
    finally:
        index.set_setting(wetter_routes.SETTING_CONFIG, "")
        index.set_setting(wetter_routes.SETTING_ENABLED, "0")
