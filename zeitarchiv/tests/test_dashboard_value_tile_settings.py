"""Persistenztests für die erweiterten Werte-Kachel-Einstellungen."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.index import Index


def test_new_value_tile_enables_sparkline_and_uses_raw_resolution(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        assert index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["show_sparkline"] == 1
        assert pin["sparkline_resolution"] == "raw"
    finally:
        index.close()


def test_value_tile_resolution_and_entity_can_be_changed(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        index.get_or_create_entity("sensor.two", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")

        assert index.set_dashboard_entity_pin_sparkline_resolution(
            dashboard_id, "sensor.one", "5min"
        )
        assert index.set_dashboard_entity_pin_entity(
            dashboard_id, "sensor.one", "sensor.two"
        )
        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["item_entity_id"] == "sensor.two"
        assert pin["sparkline_resolution"] == "5min"
    finally:
        index.close()
