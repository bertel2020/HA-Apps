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


# ---- Energiedashboard (Schritt 4) -------------------------------------------------------------

def _energy_row(entity_id, role, device=None, name=None):
    return {"entity_id": entity_id, "domain": "sensor", "name": name or entity_id, "energy_role": role,
            "device": device, "device_class": "energy", "state_class": "total_increasing", "unit": "kWh"}


ENERGY = [
    _energy_row("sensor.netz_bezug", "grid_import", name="Netzbezug"),
    _energy_row("sensor.netz_einsp", "grid_export", name="Netz Einspeisung"),
    _energy_row("sensor.pv_dach", "solar", name="PV Dach"),
    _energy_row("sensor.wp", "consumer", name="Wärmepumpe"),
    _energy_row("sensor.akku_laden", "battery_in", device="Akku", name="Akku Laden"),
    _energy_row("sensor.akku_entladen", "battery_out", device="Akku", name="Akku Entladen"),
]
ENERGY_IDS = [row["entity_id"] for row in ENERGY]


def _config(**fields):
    from app import energiedashboard_routes as edr

    return {**edr._empty_config(), **fields}  # noqa: SLF001


def test_energy_proposals_follow_the_home_assistant_energy_roles() -> None:
    proposals = selection_roles.energie_proposals(_config(), ENERGY, ENERGY_IDS)
    assert [(p["target"], p["assign"], p["confidence"]) for p in proposals] == [
        ("netzbezug", "netzbezug:sensor.netz_bezug", "ok"),
        ("einspeisung", "einspeisung:sensor.netz_einsp", "ok"),
        ("erzeuger", "erzeuger:sensor.pv_dach", "ok"),
        ("speicher", "speicher:sensor.akku_entladen,sensor.akku_laden", "ok"),
        ("verbraucher", "verbraucher:sensor.wp", "ok"),
    ]
    assert proposals[3]["name"] == "Akku Entladen + Akku Laden"


def test_energy_proposals_skip_filled_fields_and_entities_that_already_have_a_role() -> None:
    config = _config(netzbezug="sensor.anderer_zaehler", erzeuger=[{"entity_id": "sensor.pv_dach", "name": "PV"}])
    targets = [p["target"] for p in selection_roles.energie_proposals(config, ENERGY, ENERGY_IDS)]
    assert "netzbezug" not in targets and "erzeuger" not in targets and "einspeisung" in targets


def test_energy_proposals_only_use_the_given_entities() -> None:
    assert [p["target"] for p in selection_roles.energie_proposals(_config(), ENERGY, ["sensor.wp"])] == ["verbraucher"]


def test_energy_proposals_mark_ambiguous_cases_as_check() -> None:
    rows = [
        _energy_row("sensor.bezug_a", "grid_import"), _energy_row("sensor.bezug_b", "grid_import"),
        _energy_row("sensor.laden_nur", "battery_in", device="Solo"),
        _energy_row("sensor.akku1_aus", "battery_out", device="Zwei"), _energy_row("sensor.akku1_ein", "battery_in", device="Zwei"),
        _energy_row("sensor.akku2_aus", "battery_out", device="Zwei"),
        _energy_row("sensor.ohne_geraet_aus", "battery_out"),
    ]
    got = {p["assign"]: p["confidence"] for p in selection_roles.energie_proposals(_config(), rows, [r["entity_id"] for r in rows])}
    assert got["netzbezug:sensor.bezug_a"] == got["netzbezug:sensor.bezug_b"] == "check"
    assert got["speicher:,sensor.laden_nur"] == "check"
    assert got["speicher:sensor.ohne_geraet_aus,"] == "check"
    assert got["speicher:sensor.akku1_aus,"] == "check" and got["speicher:sensor.akku2_aus,"] == "check"


def test_energy_offer_needs_the_dashboard_switched_on(index) -> None:
    timeline = [_entry(["sensor.wp"])]
    assert selection_roles.energie_offer(index, ENERGY, timeline, {"sensor.wp"}, now=1_000_100.0) == []
    index.set_setting("energiedashboard_enabled", "1")
    offer = selection_roles.energie_offer(index, ENERGY, timeline, {"sensor.wp"}, now=1_000_100.0)
    assert [row["assign"] for row in offer] == ["verbraucher:sensor.wp"]
    assert selection_roles.energie_offer(index, ENERGY, timeline, set(), now=1_000_100.0) == []  # noch nicht archiviert


def _options(*ids, kind="counter"):
    return {eid: {"entity_id": eid, "label": f"Name {eid}", "aggregation_type": kind} for eid in ids}


def test_apply_assignments_fills_fields_and_appends_rows_without_touching_the_input() -> None:
    from app import energiedashboard_routes as edr

    config = _config(verbraucher=[{"entity_id": "sensor.alt", "name": "Alt", "gruppe": "", "parent": ""}])
    options = _options("sensor.b", "sensor.e", "sensor.p", "sensor.v", "sensor.out", "sensor.in")
    merged, applied = edr.apply_assignments(config, [
        "netzbezug:sensor.b", "einspeisung:sensor.e", "erzeuger:sensor.p", "verbraucher:sensor.v",
        "speicher:sensor.out,sensor.in",
    ], options)
    assert applied == 5
    assert merged["netzbezug"] == "sensor.b" and merged["einspeisung"] == "sensor.e"
    assert merged["erzeuger"][0]["entity_id"] == "sensor.p" and merged["erzeuger"][0]["name"] == "Name sensor.p"
    assert [r["entity_id"] for r in merged["verbraucher"]] == ["sensor.alt", "sensor.v"]
    assert merged["speicher"][0]["entladen_entity_id"] == "sensor.out" and merged["speicher"][0]["laden_entity_id"] == "sensor.in"
    assert config["netzbezug"] is None and len(config["verbraucher"]) == 1 and config["erzeuger"] == []


def test_apply_assignments_drops_everything_invalid() -> None:
    from app import energiedashboard_routes as edr

    config = _config(netzbezug="sensor.schon_da", erzeuger=[{"entity_id": "sensor.belegt", "name": "x"}])
    options = {**_options("sensor.neu", "sensor.belegt", "sensor.schon_da"), **_options("sensor.gauge", kind="standard"),
               "sensor.gauge": {"entity_id": "sensor.gauge", "label": "G", "aggregation_type": "standard"}}
    merged, applied = edr.apply_assignments(config, [
        "netzbezug:sensor.neu",            # Feld schon belegt
        "erzeuger:sensor.belegt",          # Entität hat schon eine Rolle
        "verbraucher:sensor.gauge",        # kein Zähler
        "verbraucher:sensor.nicht_da",     # nicht archiviert
        "unbekannt:sensor.neu", "kaputt",  # unbekanntes Ziel, kein Doppelpunkt
        "speicher:,", "speicher:sensor.gauge,sensor.gauge",
    ], options)
    assert applied == 0 and merged["netzbezug"] == "sensor.schon_da"
    assert len(merged["erzeuger"]) == 1 and merged["verbraucher"] == [] and merged["speicher"] == []


def test_apply_assignments_uses_an_entity_only_once() -> None:
    from app import energiedashboard_routes as edr

    merged, applied = edr.apply_assignments(
        _config(), ["verbraucher:sensor.v", "erzeuger:sensor.v"], _options("sensor.v"),
    )
    assert applied == 1 and len(merged["verbraucher"]) == 1 and merged["erzeuger"] == []


def test_energy_dashboard_opens_the_setup_with_the_assignments_prefilled(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.assign_netz_kwh", "sensor", "total_increasing", "kWh")
    index.set_setting("energiedashboard_enabled", "1")
    index.set_setting("energiedashboard_config", "")
    try:
        page = client.get("/energiedashboard?assign=netzbezug:sensor.assign_netz_kwh&assign=netzbezug:sensor.gibt_es_nicht").text
        assert 'hx-get="energiedashboard/setup?assign=netzbezug%3Asensor.assign_netz_kwh"' in page
        assert "gibt_es_nicht" not in page
        setup = client.get("/energiedashboard/setup?assign=netzbezug:sensor.assign_netz_kwh").text
        assert 'netzbezug: "sensor.assign_netz_kwh"' in setup
        assert "1 Zuordnungen aus dem Auswahl-Assistenten vorbelegt" in setup
        assert "Zuordnungen aus dem Auswahl-Assistenten vorbelegt" not in client.get("/energiedashboard/setup").text
        assert index.get_setting("energiedashboard_config", "") == ""  # nichts gespeichert
    finally:
        index.set_setting("energiedashboard_config", "")
        index.set_setting("energiedashboard_enabled", "0")


def test_overview_page_shows_both_offers_for_recently_added_entities(client) -> None:
    import os
    import time
    from pathlib import Path

    from app import selection_catalog
    from app.main import index

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    target = data_dir / selection_catalog.FILENAME
    index.get_or_create_entity("sensor.offer_sonne", "sensor", "measurement", "°")
    index.get_or_create_entity("sensor.offer_wp_kwh", "sensor", "total_increasing", "kWh")
    selection_catalog.save(data_dir, [
        {"entity_id": "sensor.offer_sonne", "name": "Sonnenstand", "unit": "°", "platform": "sun"},
        {"entity_id": "sensor.offer_wp_kwh", "name": "Wärmepumpe Offer", "energy_role": "consumer",
         "device_class": "energy", "state_class": "total_increasing", "unit": "kWh"},
    ], log=[{
        "id": "offer1", "ts": time.time(), "source": "assistant", "action": "add", "status": "applied",
        "entity_ids": ["sensor.offer_sonne", "sensor.offer_wp_kwh"], "count": 2,
    }], phase1=True)
    index.set_setting("wetterdashboard_enabled", "1")
    index.set_setting("energiedashboard_enabled", "1")
    try:
        page = client.get("/selection/overview").text
        assert "Passt in das Wetterdashboard" in page and 'value="sun_elevation:sensor.offer_sonne"' in page
        assert "Passt in das Energiedashboard" in page and 'value="verbraucher:sensor.offer_wp_kwh"' in page
        assert 'action="/energiedashboard"' in page or "/energiedashboard\"" in page
    finally:
        index.set_setting("wetterdashboard_enabled", "0")
        index.set_setting("energiedashboard_enabled", "0")
        target.unlink(missing_ok=True)


def test_energy_setup_suggestions_cover_all_archived_entities_with_a_home_assistant_role(client) -> None:
    import os
    from pathlib import Path

    from app import selection_catalog
    from app.main import index

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    target = data_dir / selection_catalog.FILENAME
    target.unlink(missing_ok=True)
    index.set_setting("energiedashboard_config", "")
    try:
        assert client.get("/energiedashboard/setup/vorschlaege").json() == {"has_catalog": False, "proposals": []}
        for entity_id in ("sensor.vs_bezug", "sensor.vs_pv", "sensor.vs_nicht_archiviert_aber_rolle"):
            if entity_id != "sensor.vs_nicht_archiviert_aber_rolle":
                index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
        selection_catalog.save(data_dir, [
            {"entity_id": "sensor.vs_bezug", "name": "Bezug", "energy_role": "grid_import", "unit": "kWh"},
            {"entity_id": "sensor.vs_pv", "name": "PV", "energy_role": "solar", "unit": "kWh"},
            {"entity_id": "sensor.vs_nicht_archiviert_aber_rolle", "name": "Fehlt", "energy_role": "consumer", "unit": "kWh"},
        ])
        data = client.get("/energiedashboard/setup/vorschlaege").json()
        assert data["has_catalog"] is True
        assert [(p["assign"], p["confidence"]) for p in data["proposals"]] == [
            ("netzbezug:sensor.vs_bezug", "ok"), ("erzeuger:sensor.vs_pv", "ok"),
        ]
        index.set_setting("energiedashboard_config", '{"netzbezug": "sensor.vs_bezug"}')
        again = client.get("/energiedashboard/setup/vorschlaege").json()
        assert [p["assign"] for p in again["proposals"]] == ["erzeuger:sensor.vs_pv"]
    finally:
        index.set_setting("energiedashboard_config", "")
        target.unlink(missing_ok=True)


def test_energy_setup_form_has_the_take_from_home_assistant_button(client) -> None:
    html = client.get("/energiedashboard/setup").text
    assert "takeFromHa()" in html and "Aus Home Assistant übernehmen" in html
