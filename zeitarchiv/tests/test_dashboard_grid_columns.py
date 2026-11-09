"""Kacheln pro Zeile (Roadmap 1.40, Phase 6): Spalte `grid_columns`, Grenzen, Modi, Anzeige-Kappung."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from app import dashboard_grid
from app.storage.index import Index

from _paths import APP, PAGE_CSS, TEMPLATES, template_text


@pytest.fixture
def index(tmp_path: Path):
    idx = Index(tmp_path / "index.sqlite")
    try:
        yield idx
    finally:
        idx.close()


def _pinned_chart(index: Index, dashboard_id: int) -> int:
    chart_id = index.create_saved_chart("Test", ["sensor.a"], "day", continuous=False)
    assert index.pin_item_to_dashboard(dashboard_id, "chart", chart_id)
    return chart_id


def test_migration_adds_the_column_with_default_three(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    first = Index(path)
    first.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE dashboards DROP COLUMN grid_columns")
    conn.commit()
    conn.close()
    reopened = Index(path)
    try:
        assert reopened.get_dashboard(1)["grid_columns"] == 3
    finally:
        reopened.close()


def test_set_grid_columns_accepts_only_three_or_four(index: Index) -> None:
    assert index.set_dashboard_grid_columns(1, 4) is True
    assert index.get_dashboard(1)["grid_columns"] == 4
    for bad in (2, 5, 0, 6):
        with pytest.raises(ValueError):
            index.set_dashboard_grid_columns(1, bad)
    assert index.set_dashboard_grid_columns(999, 4) is False


def test_size_limits_follow_columns_and_precise_mode(index: Index) -> None:
    assert dashboard_grid.size_limits(index.get_dashboard(1)) == (3, 3)
    index.set_dashboard_grid_columns(1, 4)
    assert dashboard_grid.size_limits(index.get_dashboard(1)) == (4, 3)
    index.set_dashboard_precise_mode(1, True)
    assert dashboard_grid.size_limits(index.get_dashboard(1)) == (8, 6)
    assert dashboard_grid.size_limits(None) == (3, 3)


def test_pin_size_is_limited_by_columns_and_rows_separately(index: Index) -> None:
    chart_id = _pinned_chart(index, 1)
    with pytest.raises(ValueError):
        index.set_dashboard_pin_size(1, "chart", chart_id, 4, 1, max_cols=3, max_rows=3)
    assert index.set_dashboard_pin_size(1, "chart", chart_id, 4, 3, max_cols=4, max_rows=3)
    with pytest.raises(ValueError):
        index.set_dashboard_pin_size(1, "chart", chart_id, 4, 4, max_cols=4, max_rows=3)
    # Präzise, vier Spalten: 8×6 geht, 8×7 und 9×1 nicht.
    assert index.set_dashboard_pin_size(1, "chart", chart_id, 8, 6, max_cols=8, max_rows=6)
    with pytest.raises(ValueError):
        index.set_dashboard_pin_size(1, "chart", chart_id, 9, 1, max_cols=8, max_rows=6)


def test_switching_back_to_three_columns_keeps_the_stored_sizes(index: Index) -> None:
    chart_id = _pinned_chart(index, 1)
    index.set_dashboard_grid_columns(1, 4)
    index.set_dashboard_pin_size(1, "chart", chart_id, 4, 2, max_cols=4, max_rows=3)
    index.set_dashboard_grid_columns(1, 3)
    pin = index.list_dashboard_pins(1)[0]
    assert (pin["grid_cols"], pin["grid_rows"]) == (4, 2)  # die Anzeige kappt, die Datenbank nicht


def test_precise_mode_round_trip_with_four_columns(index: Index) -> None:
    chart_id = _pinned_chart(index, 1)
    index.set_dashboard_grid_columns(1, 4)
    index.set_dashboard_pin_size(1, "chart", chart_id, 4, 3, max_cols=4, max_rows=3)
    index.set_dashboard_precise_mode(1, True)
    pin = index.list_dashboard_pins(1)[0]
    assert (pin["grid_cols"], pin["grid_rows"]) == (8, 6)
    index.set_dashboard_precise_mode(1, False)
    pin = index.list_dashboard_pins(1)[0]
    assert (pin["grid_cols"], pin["grid_rows"]) == (4, 3)  # nicht auf 3 gekappt


def test_duplicate_copies_the_modes_and_columns(index: Index) -> None:
    index.set_dashboard_grid_columns(1, 4)
    index.set_dashboard_precise_mode(1, True)
    index.set_dashboard_fill_gaps(1, True)
    copy_id = index.duplicate_dashboard(1)
    copy = index.get_dashboard(copy_id)
    assert (copy["grid_columns"], copy["precise_mode"], copy["fill_gaps"], copy["period_nav"]) == (4, 1, 1, 0)


def test_factory_reset_sets_three_columns(index: Index) -> None:
    index.set_dashboard_grid_columns(1, 4)
    index.reset_everything()
    assert index.get_dashboard(1)["grid_columns"] == 3


def test_routes_validate_and_apply_the_limits(client) -> None:
    from app.main import index as app_index

    created = client.post("/dashboards", json={"name": "Spalten-Test"}).json()["id"]
    try:
        chart_id = app_index.create_saved_chart("Spalten", ["sensor.a"], "day", continuous=False)
        assert app_index.pin_item_to_dashboard(created, "chart", chart_id)
        body = {"dashboard_id": created, "item_type": "chart", "item_id": chart_id, "grid_cols": 4, "grid_rows": 1}
        assert client.post("/dashboard/size", json=body).status_code == 422
        assert client.post(f"/dashboards/{created}/grid-columns", json={"grid_columns": 5}).status_code == 400
        assert client.post("/dashboards/99999/grid-columns", json={"grid_columns": 4}).status_code == 404
        assert client.post(f"/dashboards/{created}/grid-columns", json={"grid_columns": 4}).status_code == 200
        assert client.post("/dashboard/size", json=body).status_code == 200
        page = client.get(f"/dashboards/{created}").text
        assert 'data-columns="4"' in page and "cols-4" in page
        client.post(f"/dashboards/{created}/grid-columns", json={"grid_columns": 3})
        assert 'data-columns' not in client.get(f"/dashboards/{created}").text
    finally:
        client.post(f"/dashboards/{created}/delete")


def test_menu_and_page_use_separate_column_and_row_limits() -> None:
    menu = template_text("_dashboard_tile_menu.html")
    assert "size_max_cols" in menu and "size_max_rows" in menu
    assert "size_max " not in menu.replace("size_max=", "")  # kein gemeinsames size_max mehr
    editor = template_text("dashboard_editor.html")
    assert "page_layout == 'wide'" in editor and "dash-four-columns" in editor


def test_every_span_above_the_stored_columns_has_a_cap_rule_below_1500px() -> None:
    """Zu breite Kacheln dürfen in einem Raster mit weniger Spalten keine impliziten Spalten anlegen."""
    for name in ("dashboard_detail.css", "entities.css"):
        css = (PAGE_CSS / name).read_text(encoding="utf-8")
        assert '.dashboard-grid:not(.is-precise)[data-columns="4"] .dtile[data-grid-cols="4"]{grid-column:span 3;}' in css
        assert 'data-grid-cols="7"]' in css and 'data-grid-cols="8"]' in css
        assert css.count('.dtile[data-grid-cols="3"],.dtile[data-grid-cols="4"]{grid-column:span 2;}') == 1
        assert '.dtile[data-grid-cols="4"]{grid-column:span 1;}' in css  # Telefon: eine Spalte
    detail = (PAGE_CSS / "dashboard_detail.css").read_text(encoding="utf-8")
    # Vier Spalten nur im Schalter-Block ab 1500px.
    block = detail[detail.index("@media (min-width:1500px){"):]
    assert 'repeat(4,minmax(0,1fr))' in block and 'repeat(8,minmax(0,1fr))' in block
    assert detail.count("--dash-max:1920px") == 1


def test_restore_carries_the_column_count() -> None:
    source = (APP / "storage" / "restore_selective.py").read_text(encoding="utf-8")
    assert source.count("grid_columns") >= 3  # Vergleich, Überschreiben mit Rückfall auf 3
    assert 'row.get("grid_columns", 3)' in source


def test_templates_mention_columns_only_through_the_context() -> None:
    assert (TEMPLATES / "_dashboard_tiles.html").read_text(encoding="utf-8").count("dashboard_grid_columns == 4") == 1
    assert re.search(r"dashboard_grid_columns", (APP / "dashboard_tiles.py").read_text(encoding="utf-8"))


def test_precise_tablet_block_keeps_a_four_wide_tile_full_width() -> None:
    """Regression: Die Regel für zwei Spalten (normal) machte aus einer 4er-Kachel im Präzisen Modus
    auf dem Tablet eine halbe; dort hat das Raster vier Spalten und die Kachel soll die Zeile füllen."""
    for name in ("dashboard_detail.css", "entities.css"):
        css = (PAGE_CSS / name).read_text(encoding="utf-8")
        start = css.index("@media (min-width:521px) and (max-width:820px){")
        assert '.dashboard-grid.is-precise .dtile[data-grid-cols="4"]{grid-column:span 4;}' in css[start: css.index("\n  }", start)]


def test_four_columns_hint_logic(index: Index) -> None:
    assert dashboard_grid.four_columns_hint_open(index, "wide", 3)
    assert not dashboard_grid.four_columns_hint_open(index, "standard", 3)
    assert not dashboard_grid.four_columns_hint_open(index, "wide", 4)
    index.set_setting(dashboard_grid.HINT_SETTING, "1")
    assert not dashboard_grid.four_columns_hint_open(index, "wide", 3)


def test_four_columns_hint_page_and_route(client) -> None:
    from app.main import index as app_index

    created = client.post("/dashboards", json={"name": "Hinweis-Test"}).json()["id"]
    old_layout = app_index.get_setting("page_layout", "standard")
    old_hint = app_index.get_setting(dashboard_grid.HINT_SETTING, "")
    try:
        app_index.set_setting(dashboard_grid.HINT_SETTING, "")
        app_index.set_setting("page_layout", "standard")
        assert "dash-hint-four" not in client.get(f"/dashboards/{created}").text.replace(".dash-hint-four", "")
        app_index.set_setting("page_layout", "wide")
        page = client.get(f"/dashboards/{created}").text
        assert 'class="dash-hint-four"' in page and "4 Kacheln nutzen" in page
        assert 'class="dash-hint-four"' in client.get("/uebersicht").text
        client.post(f"/dashboards/{created}/grid-columns", json={"grid_columns": 4})
        assert 'class="dash-hint-four"' not in client.get(f"/dashboards/{created}").text
        client.post(f"/dashboards/{created}/grid-columns", json={"grid_columns": 3})
        assert client.post("/dashboard-hints/four-columns").status_code == 200
        assert 'class="dash-hint-four"' not in client.get(f"/dashboards/{created}").text
    finally:
        app_index.set_setting("page_layout", old_layout or "standard")
        app_index.set_setting(dashboard_grid.HINT_SETTING, old_hint or "")
        client.post(f"/dashboards/{created}/delete")


def test_four_columns_hint_is_hidden_below_1500px_by_css() -> None:
    css = (APP / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".dash-hint-four{display:none;" in css
    assert "@media (min-width:1500px){.dash-hint-four{display:flex;}}" in css
