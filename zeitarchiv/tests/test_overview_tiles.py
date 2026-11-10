"""Auswahl-Kacheln der Übersicht und Statistik-Schalter (overview_tiles.py)."""

from __future__ import annotations

import time
from zoneinfo import ZoneInfo

import pytest

from app import overview_tiles
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "index.sqlite")
    yield idx
    idx.close()


def _keys(index: Index) -> list[str]:
    return overview_tiles.selected_keys(index)


def test_default_selection_has_the_four_fixed_pages_and_skips_disabled_dashboards(index) -> None:
    assert _keys(index) == ["charts", "tables", "entities", "statistik"]
    index.set_setting("energiedashboard_enabled", "1")
    index.set_setting("wetterdashboard_enabled", "1")
    keys = _keys(index)
    assert keys[:2] == ["energiedashboard", "wetterdashboard"] and keys[-4:] == ["charts", "tables", "entities", "statistik"]


def test_default_selection_fills_up_with_own_dashboards_but_never_exceeds_the_limit(index) -> None:
    index.set_setting("energiedashboard_enabled", "1")
    index.set_setting("wetterdashboard_enabled", "1")
    for number in range(10):
        index.create_dashboard(f"Dash {number}")
    keys = _keys(index)
    assert len(keys) == overview_tiles.MAX_TILES
    # Energie- und Wetterdashboard, die vier festen Seiten, der Rest sind eigene Dashboards.
    assert sum(1 for key in keys if key.startswith("dashboard:")) == overview_tiles.MAX_TILES - 6


def test_the_standard_dashboard_is_not_a_tile_target(index) -> None:
    default_id = index.get_default_dashboard_id()
    assert f"dashboard:{default_id}" not in {row["key"] for row in overview_tiles.settings_rows(index)}


def test_toggle_adds_at_the_end_removes_and_respects_the_limit(index) -> None:
    overview_tiles.apply_form(index, {"overview_tile_toggle": "export"})
    assert _keys(index)[-1] == "export"
    overview_tiles.apply_form(index, {"overview_tile_toggle": "charts"})
    assert "charts" not in _keys(index)

    # Neue eigene Dashboards erscheinen nach der ersten Änderung ausgeschaltet und füllen die Auswahl bis zum Limit auf.
    own = [f"dashboard:{index.create_dashboard(f'Dash {number}')}" for number in range(overview_tiles.MAX_TILES)]
    for key in ("housekeeping", "import", "charts", "wetterdashboard", *own):
        if len(_keys(index)) < overview_tiles.MAX_TILES:
            overview_tiles.apply_form(index, {"overview_tile_toggle": key})
    assert len(_keys(index)) == overview_tiles.MAX_TILES
    index.set_setting("energiedashboard_enabled", "1")
    with pytest.raises(ValueError):
        overview_tiles.apply_form(index, {"overview_tile_toggle": "energiedashboard"})
    assert len(_keys(index)) == overview_tiles.MAX_TILES


def test_move_swaps_neighbours_and_ignores_the_ends(index) -> None:
    overview_tiles.apply_form(index, {"overview_tile_move": "tables:up"})
    assert _keys(index)[:2] == ["tables", "charts"]
    overview_tiles.apply_form(index, {"overview_tile_move": "tables:up"})
    assert _keys(index)[0] == "tables"
    overview_tiles.apply_form(index, {"overview_tile_move": "statistik:down"})
    assert _keys(index)[-1] == "statistik"
    with pytest.raises(ValueError):
        overview_tiles.apply_form(index, {"overview_tile_move": "export:up"})  # nicht aktiv
    with pytest.raises(ValueError):
        overview_tiles.apply_form(index, {"overview_tile_move": "charts:sideways"})


def test_unknown_keys_are_rejected_and_stale_stored_keys_fall_away(index) -> None:
    with pytest.raises(ValueError):
        overview_tiles.apply_form(index, {"overview_tile_toggle": "dashboard:9999"})
    dash_id = index.create_dashboard("Wohnzimmer")
    assert f"dashboard:{dash_id}" in _keys(index)  # ohne gespeicherte Auswahl folgt die Standardliste
    overview_tiles.apply_form(index, {"overview_tile_toggle": "export"})  # ab jetzt ist die Auswahl festgeschrieben
    assert f"dashboard:{dash_id}" in _keys(index)
    later = index.create_dashboard("Später")
    assert f"dashboard:{later}" not in _keys(index)  # neue Dashboards erscheinen danach ausgeschaltet
    index.delete_dashboard(dash_id)
    assert f"dashboard:{dash_id}" not in _keys(index)
    index.set_setting(overview_tiles.SETTING_TILES, "kaputt")
    assert _keys(index) == [f"dashboard:{later}", "charts", "tables", "entities", "statistik"]


def test_page_tiles_skip_a_disabled_dashboard_but_keep_it_selected(index) -> None:
    overview_tiles.apply_form(index, {"overview_tile_toggle": "energiedashboard"})
    labels = [tile["key"] for tile in overview_tiles.page_tiles(index, "0", "0 B")]
    assert "energiedashboard" not in labels
    index.set_setting("energiedashboard_enabled", "1")
    tiles = overview_tiles.page_tiles(index, "0", "0 B")
    energy = next(tile for tile in tiles if tile["key"] == "energiedashboard")
    assert energy["href"] == "energiedashboard" and energy["meta"] == "Noch nicht eingerichtet"


def test_the_statistics_tile_carries_the_numbers_of_the_removed_stat_tiles(index) -> None:
    tiles = {tile["key"]: tile for tile in overview_tiles.page_tiles(index, "111.598", "5,6 MB")}
    assert tiles["statistik"]["meta"] == "111.598 Datensätze · 5,6 MB"
    assert tiles["entities"]["meta"] == "0 Entitäten"
    index.get_or_create_entity("sensor.a", "sensor", "standard", "°C")
    one = {tile["key"]: tile for tile in overview_tiles.page_tiles(index, "0", "0 B")}
    assert one["entities"]["meta"] == "1 Entität"


def test_stats_mode_validation_and_default(index) -> None:
    assert overview_tiles.stats_mode(index) == "alert"
    overview_tiles.apply_form(index, {"overview_stats": "always"})
    assert overview_tiles.stats_mode(index) == "always"
    with pytest.raises(ValueError):
        overview_tiles.apply_form(index, {"overview_stats": "sometimes"})
    index.set_setting(overview_tiles.SETTING_STATS, "unsinn")
    assert overview_tiles.stats_mode(index) == "alert"


def test_existing_installations_keep_their_statistics_tiles_and_new_ones_do_not(index, tmp_path) -> None:
    overview_tiles.ensure_defaults(index)
    assert index.get_setting(overview_tiles.SETTING_STATS) == "alert"
    index.get_or_create_entity("sensor.a", "sensor", "standard", "°C")
    overview_tiles.ensure_defaults(index)
    assert index.get_setting(overview_tiles.SETTING_STATS) == "alert"  # festgeschrieben, kippt nicht mehr

    old = Index(tmp_path / "old.sqlite")
    old.get_or_create_entity("sensor.a", "sensor", "standard", "°C")
    overview_tiles.ensure_defaults(old)
    assert old.get_setting(overview_tiles.SETTING_STATS) == "always"
    old.set_setting(overview_tiles.SETTING_STATS, "off")
    overview_tiles.ensure_defaults(old)
    assert old.get_setting(overview_tiles.SETTING_STATS) == "off"
    old.close()


def test_the_alert_appears_only_for_open_points(index) -> None:
    assert overview_tiles.alert(index, TZ) is None
    index.get_or_create_entity("sensor.never", "sensor", "standard", "°C")  # nie empfangen = inaktiv
    found = overview_tiles.alert(index, TZ)
    assert found == {"parts": ["1 inaktive Entität"], "link": "housekeeping"}

    index.get_or_create_entity("sensor.live", "sensor", "standard", "°C")
    index._conn.execute("UPDATE entities SET last_ts = ?", (time.time(),))
    index._conn.commit()
    assert overview_tiles.alert(index, TZ) is None
    index.mark_deleted("sensor.live", [1.0])  # markierte Werte allein lösen nichts aus
    assert overview_tiles.alert(index, TZ) is None


def test_page_context_hides_the_alert_unless_the_mode_asks_for_it(index) -> None:
    index.get_or_create_entity("sensor.never", "sensor", "standard", "°C")
    assert overview_tiles.page_context(index, TZ, "0", "0 B")["overview_alert"]
    overview_tiles.apply_form(index, {"overview_stats": "always"})
    assert overview_tiles.page_context(index, TZ, "0", "0 B")["overview_alert"] is None


def test_overview_page_renders_the_tiles_and_the_switch(client) -> None:
    from app.main import index as app_index

    saved = {key: app_index.get_setting(key) for key in (overview_tiles.SETTING_STATS, overview_tiles.SETTING_TILES)}
    try:
        assert client.post("/dashboards/overview-settings", data={"overview_stats": "off"}).status_code == 200
        page = client.get("/uebersicht").text
        assert 'class="ov-tiles"' in page and 'class="stat-row"' not in page and "ov-alert" not in page
        assert 'href="charts"' in page and 'href="statistik"' in page

        assert client.post("/dashboards/overview-settings", data={"overview_stats": "always"}).status_code == 200
        assert 'class="stat-row"' in client.get("/uebersicht").text

        form = client.post("/dashboards/overview-settings", data={"overview_tile_toggle": "export"})
        assert form.status_code == 200 and 'id="overview-tiles-list"' in form.text
        assert 'id="overview-options"' in form.text
        assert 'href="export"' in client.get("/uebersicht").text
        assert client.post("/dashboards/overview-settings", data={"overview_tile_toggle": "nope"}).status_code == 400
        assert client.post("/dashboards/overview-settings", data={"overview_stats": "nope"}).status_code == 400
    finally:
        for key, value in saved.items():
            if value is not None:
                app_index.set_setting(key, value)
            else:
                app_index._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                app_index._conn.commit()


def test_overview_options_live_in_the_default_dashboard_editor_only(client) -> None:
    from app.main import index as app_index

    dashboards = app_index.list_dashboards()
    default = next(d for d in dashboards if d["is_default"])
    editor = client.get(f"/dashboards/{default['id']}/edit").text
    assert 'id="overview-options"' in editor and 'id="overview-tiles-list"' in editor
    for other in (d for d in dashboards if not d["is_default"]):
        assert 'id="overview-options"' not in client.get(f"/dashboards/{other['id']}/edit").text
    settings = client.get("/settings/darstellung").text if False else client.get("/settings").text
    assert 'id="overview-tiles-list"' not in settings
