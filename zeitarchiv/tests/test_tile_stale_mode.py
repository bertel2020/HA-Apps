"""Veraltet-Schwelle je Werte-Kachel (Stufen, siehe app/staleness.py)."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.staleness import STALE_MODES, stale_thresholds, staleness
from app.storage.index import Index

from _paths import APP, template_text

MINUTE, HOUR, DAY = 60, 3600, 86400


def test_standard_keeps_the_previous_fifteen_minutes_and_one_hour() -> None:
    assert staleness(14 * MINUTE, "standard") == "fresh"
    assert staleness(16 * MINUTE, "standard") == "warn"
    assert staleness(61 * MINUTE, "standard") == "stale"


def test_daily_tolerates_a_day_old_value() -> None:
    assert staleness(23 * HOUR, "daily") == "fresh"
    assert staleness(27 * HOUR, "daily") == "warn"
    assert staleness(49 * HOUR, "daily") == "stale"


def test_rare_and_off() -> None:
    assert staleness(2 * DAY, "rare") == "fresh"
    assert staleness(4 * DAY, "rare") == "warn"
    assert staleness(8 * DAY, "rare") == "stale"
    assert staleness(365 * DAY, "off") == "fresh"
    assert stale_thresholds("off") is None


def test_no_last_value_is_never_marked_and_unknown_modes_use_the_standard() -> None:
    assert staleness(None, "standard") == "fresh"
    assert staleness(2 * HOUR, "gibt-es-nicht") == "stale"


def test_new_tile_defaults_to_standard_and_the_setter_validates(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        pin_id = index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        assert index.list_dashboard_pins(dashboard_id)[0]["stale_mode"] == "standard"

        assert index.set_dashboard_entity_pin_stale_mode(dashboard_id, pin_id, "daily")
        assert index.list_dashboard_pins(dashboard_id)[0]["stale_mode"] == "daily"

        with pytest.raises(ValueError):
            index.set_dashboard_entity_pin_stale_mode(dashboard_id, pin_id, "weekly")
        assert index.list_dashboard_pins(dashboard_id)[0]["stale_mode"] == "daily"
        assert not index.set_dashboard_entity_pin_stale_mode(dashboard_id, pin_id + 99, "off")
    finally:
        index.close()


def test_the_setting_belongs_to_one_tile_only(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        pin_a = index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        pin_b = index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        index.set_dashboard_entity_pin_stale_mode(dashboard_id, pin_a, "rare")
        modes = {p["id"]: p["stale_mode"] for p in index.list_dashboard_pins(dashboard_id)}
        assert modes == {pin_a: "rare", pin_b: "standard"}
    finally:
        index.close()


def test_duplicating_a_dashboard_keeps_the_stale_mode(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        pin_id = index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        index.set_dashboard_entity_pin_stale_mode(dashboard_id, pin_id, "off")
        copy_id = index.duplicate_dashboard(dashboard_id)
        assert index.list_dashboard_pins(copy_id)[0]["stale_mode"] == "off"
    finally:
        index.close()


def test_old_database_without_the_column_keeps_the_previous_behaviour(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE dashboards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE dashboard_pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dashboard_id INTEGER NOT NULL DEFAULT 1,
            item_type TEXT NOT NULL, item_id INTEGER NOT NULL, item_entity_id TEXT,
            position INTEGER NOT NULL,
            grid_cols INTEGER NOT NULL DEFAULT 1, grid_rows INTEGER NOT NULL DEFAULT 1,
            show_legend INTEGER NOT NULL DEFAULT 0, show_sparkline INTEGER NOT NULL DEFAULT 1,
            UNIQUE(dashboard_id, item_type, item_id, item_entity_id));
        INSERT INTO dashboards (id, name, position) VALUES (1, 'Alt', 0);
        INSERT INTO dashboard_pins (dashboard_id, item_type, item_id, item_entity_id, position)
            VALUES (1, 'entity', 0, 'sensor.alt', 0);
        """
    )
    connection.commit()
    connection.close()

    index = Index(db_path)
    try:
        assert index.list_dashboard_pins(1)[0]["stale_mode"] == "standard"
    finally:
        index.close()


def test_the_tile_menu_offers_every_mode_the_setter_accepts() -> None:
    menu = template_text("_dashboard_tile_menu.html")
    for mode in STALE_MODES:
        assert f"('{mode}', " in menu, mode


def test_the_client_reads_the_thresholds_the_server_renders() -> None:
    """Die Schwellen stehen nur in staleness.py: der Server rendert sie als
    data-Attribute, der Client liest sie — keine zweite Kopie der Zahlen."""
    tiles = template_text("_dashboard_tiles.html")
    script = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
    assert "data-stale-warn-after" in tiles and "data-stale-stale-after" in tiles
    assert "dataset.staleWarnAfter" in script and "dataset.staleStaleAfter" in script
    assert "secondsAgo > 900" not in script
    assert "secondsAgo > 3600" not in script


def test_the_client_class_toggle_follows_the_rendered_thresholds() -> None:
    """Das Umschalten von is-warn/is-stale wird aus dem echten Client-Ausschnitt
    per node ausgeführt: gelb, rot, frisch und 'nie markieren' (leere Werte)."""
    script = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
    start = script.index("if (tileEl) {\n      const warnAfter")
    end = script.index("el.querySelectorAll('.dtile-entity-stat')", start)
    block = script[start:end]
    harness = f"""
    function run(warn, stale, secondsAgo) {{
      const classes = new Set();
      const tileEl = {{classList: {{toggle: (c, on) => on ? classes.add(c) : classes.delete(c)}}}};
      const el = {{closest: () => tileEl, dataset: {{staleWarnAfter: warn, staleStaleAfter: stale}}}};
      {block}
      return [...classes].sort().join(',');
    }}
    console.log(JSON.stringify([
      run('900', '3600', 100), run('900', '3600', 1000), run('900', '3600', 4000),
      run('93600', '172800', 4000), run('', '', 10 ** 9), run('900', '3600', null),
    ]));
    """
    result = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == '["","is-warn","is-stale","","",""]'


def test_endpoint_stores_the_mode_and_the_page_renders_the_frame(client) -> None:
    """End-to-end durch Route, Context-Builder und Template: ein Wert von vor
    vier Tagen ist bei 'standard' rot, bei 'rare' gelb, bei 'off' ohne Rahmen."""
    import re
    import time

    from app.main import index

    entity_id = "sensor.stale_mode_e2e"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    with index._lock, index._conn:  # noqa: SLF001 — Testaufbau: letzter Wert vor 4 Tagen
        index._conn.execute(
            "UPDATE entities SET last_value = 21.0, last_ts = ? WHERE entity_id = ?",
            (time.time() - 4 * DAY, entity_id),
        )
    dashboard_id = index.get_default_dashboard_id()
    pin_id = index.pin_entity_to_dashboard(dashboard_id, entity_id)

    def frame() -> str:
        html = client.get(f"/?dashboard_id={dashboard_id}").text
        match = re.search(
            r'<div class="(dtile dtile-entity[^"]*)"[^>]*data-item-id="%d"' % pin_id, html
        )
        assert match, "Kachel nicht gerendert"
        return match.group(1)

    def set_mode(mode: str):
        return client.post(
            "/dashboard/entity-stale-mode",
            json={"dashboard_id": dashboard_id, "pin_id": pin_id, "stale_mode": mode},
        )

    assert "is-stale" in frame()

    response = set_mode("rare")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "stale_mode": "rare", "stale_warn_after": 3 * DAY, "stale_stale_after": 7 * DAY,
    }
    assert "is-warn" in frame() and "is-stale" not in frame()

    assert set_mode("off").json()["stale_warn_after"] == ""
    assert "is-warn" not in frame() and "is-stale" not in frame()

    assert set_mode("bogus").status_code == 422
    assert client.post(
        "/dashboard/entity-stale-mode",
        json={"dashboard_id": dashboard_id, "pin_id": pin_id + 999, "stale_mode": "off"},
    ).status_code == 404
