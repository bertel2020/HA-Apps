"""Dashboard-Kachel „Bereinigung“ (item_type='cleanup', cleanup_tile.py)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from app import cleanup_tile
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _decrease_row(entity_id: str, count: int) -> dict:
    return {
        "entity_id": entity_id, "friendly_name": entity_id, "unit": "kWh", "decimals": "auto",
        "count": count, "returning": 0, "returning_days": [],
        "last": {"ts": 1_000_000.0, "previous": 5.0, "value": 0.0, "returns": False, "recovered_value": None, "recovered_after": None},
    }


def test_the_tile_counts_the_open_points_from_the_snapshots(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.set_counter_decrease_snapshot([_decrease_row("sensor.a", 2), _decrease_row("sensor.b", 1)])
    index.set_duplicate_snapshot([{"entity_id": "sensor.a", "friendly_name": "A", "count": 14}])

    rows = {row["label"]: row for row in cleanup_tile.items(index, TZ)}

    assert rows["Zählerrückgänge"]["count"] == 3 and rows["Zählerrückgänge"]["sub"] == "2 Zähler"
    assert rows["Doppelte Zeitstempel"]["count"] == 14
    assert rows["Markierte Werte"]["count"] == 0
    tile = cleanup_tile.build(index, TZ, {"item_id": 7, "grid_cols": 2, "grid_rows": 1})
    assert tile["open_total"] == 17 and not tile["all_clear"]
    index.close()


def test_the_tile_is_calm_when_nothing_is_open_and_marked_values_do_not_count_as_open(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.a", "sensor", "total_increasing", "kWh")
    index.mark_deleted("sensor.a", [1.0, 2.0])
    tile = cleanup_tile.build(index, TZ, {"item_id": 7, "grid_cols": 2, "grid_rows": 1})
    assert tile["all_clear"] and tile["open_total"] == 0
    assert [row["count"] for row in tile["lines"]][-1] == 2
    index.close()


def test_the_tile_can_be_pinned_once_per_dashboard_and_removed(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    first = index.pin_cleanup_to_dashboard(1)
    assert first is not None
    assert index.pin_cleanup_to_dashboard(1) == first  # nicht doppelt
    assert [p["item_type"] for p in index.list_dashboard_pins(1)] == ["cleanup"]
    index.unpin_item_from_dashboard(1, "cleanup", first)
    assert index.list_dashboard_pins(1) == []
    index.close()


def test_the_routes_pin_render_and_unpin_the_tile(client) -> None:
    from app.main import index

    try:
        added = client.post("/dashboard/pin-cleanup?dashboard_id=1")
        assert added.status_code == 200
        assert "dtile-cleanup" in added.text and 'data-item-type="cleanup"' in added.text
        pin_id = next(p["item_id"] for p in index.list_dashboard_pins(1) if p["item_type"] == "cleanup")
        removed = client.post(f"/dashboard/unpin-cleanup/{pin_id}?dashboard_id=1")
        assert removed.status_code == 200 and "dtile-cleanup" not in removed.text
    finally:
        for pin in index.list_dashboard_pins(1):
            if pin["item_type"] == "cleanup":
                index.unpin_item_from_dashboard(1, "cleanup", pin["item_id"])


def test_the_picker_offers_the_tile(client) -> None:
    html = client.get("/dashboards/1").text
    assert "dashboard/pin-cleanup" in html


def test_the_size_can_be_switched_between_large_and_small(client) -> None:
    from app.main import index

    try:
        client.post("/dashboard/pin-cleanup?dashboard_id=1")
        pin = next(p for p in index.list_dashboard_pins(1) if p["item_type"] == "cleanup")
        assert (pin["grid_cols"], pin["grid_rows"]) == (2, 2)

        small = client.post(f"/dashboard/cleanup-size/{pin['item_id']}?dashboard_id=1&size=small")
        assert small.status_code == 200 and small.headers["HX-Refresh"] == "true"
        pin = next(p for p in index.list_dashboard_pins(1) if p["item_type"] == "cleanup")
        assert (pin["grid_cols"], pin["grid_rows"]) == (1, 1)
        assert "dtile-cleanup-compact" in client.post("/dashboard/pin-cleanup?dashboard_id=1").text

        assert client.post(f"/dashboard/cleanup-size/{pin['item_id']}?dashboard_id=1&size=huge").status_code == 400
        assert client.post("/dashboard/cleanup-size/999999?dashboard_id=1&size=large").status_code == 404
    finally:
        for pin in index.list_dashboard_pins(1):
            if pin["item_type"] == "cleanup":
                index.unpin_item_from_dashboard(1, "cleanup", pin["item_id"])


def test_a_locked_dashboard_refuses_the_size_change(client) -> None:
    from app.main import index

    index.pin_cleanup_to_dashboard(1)
    pin = next(p for p in index.list_dashboard_pins(1) if p["item_type"] == "cleanup")
    try:
        index.set_dashboard_locked(1, True)
        assert client.post(f"/dashboard/cleanup-size/{pin['item_id']}?dashboard_id=1&size=small").status_code == 423
    finally:
        index.set_dashboard_locked(1, False)
        index.unpin_item_from_dashboard(1, "cleanup", pin["item_id"])
