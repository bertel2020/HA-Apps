"""Dashboard-Einstellung „Legenden angleichen“: Spalte `legend_align`, Standardwerte, Kopie, Route und Verdrahtung."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.storage.index import Index

from _paths import APP, PAGE_CSS, TEMPLATES, template_text


@pytest.fixture
def index(tmp_path: Path):
    idx = Index(tmp_path / "index.sqlite")
    try:
        yield idx
    finally:
        idx.close()


def test_new_dashboards_start_aligned_but_the_default_dashboard_does_not(index: Index) -> None:
    assert index.get_dashboard(1)["legend_align"] == 0
    new_id = index.create_dashboard("Neu")
    assert index.get_dashboard(new_id)["legend_align"] == 1


def test_migration_leaves_existing_dashboards_unaligned(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    first = Index(path)
    created = first.create_dashboard("Alt")
    first.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE dashboards DROP COLUMN legend_align")
    conn.commit()
    conn.close()
    reopened = Index(path)
    try:
        assert reopened.get_dashboard(created)["legend_align"] == 0
        assert reopened.get_dashboard(1)["legend_align"] == 0
    finally:
        reopened.close()


def test_setter_and_copy_keep_the_value(index: Index) -> None:
    assert index.set_dashboard_legend_align(1, True) is True
    assert index.get_dashboard(1)["legend_align"] == 1
    copy_id = index.duplicate_dashboard(1)
    assert index.get_dashboard(copy_id)["legend_align"] == 1
    assert index.set_dashboard_legend_align(1, False) is True
    assert index.get_dashboard(1)["legend_align"] == 0
    assert index.set_dashboard_legend_align(999, True) is False


def test_route_saves_the_setting_and_reports_a_missing_dashboard(client) -> None:
    from app.main import index

    new_id = index.create_dashboard("Route")
    try:
        response = client.post(f"/dashboards/{new_id}/legend-align", json={"legend_align": False})
        assert response.status_code == 200 and response.json() == {"ok": True, "legend_align": False}
        assert index.get_dashboard(new_id)["legend_align"] == 0
        assert client.post("/dashboards/99999/legend-align", json={"legend_align": True}).status_code == 404
    finally:
        # Die App-Instanz der Tests ist geteilt: ein liegengebliebenes Dashboard stünde in den Übersichts-Kacheln.
        index.delete_dashboard(new_id)


def test_grid_class_script_and_editor_are_wired_together() -> None:
    tiles = template_text("_dashboard_tiles.html")
    assert "' is-aligned' if dashboard_legend_align" in tiles
    editor = template_text("dashboard_editor.html")
    assert 'x-model="legendAlign"' in editor and "legendAlign: {{" in editor
    editor_js = (APP / "static/js/pages/dashboard_editor.js").read_text(encoding="utf-8")
    assert "/legend-align" in editor_js and "legend_align: this.legendAlign" in editor_js
    script = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
    assert "function alignLegends()" in script and "classList.contains('is-aligned')" in script
    assert "scheduleAlignLegends();" in script.split("function renderLegend(")[1].split("function ")[0]
    # Kein eigener Fenster-Listener (test_dashboard_tiles_resize_listener): Der ResizeObserver der Charts stößt es an.
    assert "scheduleAlignLegends();\n      if (resizeQueued) return;" in script
    css = (PAGE_CSS / "dashboard_detail.css").read_text(encoding="utf-8")
    assert ".dashboard-grid.is-aligned .dtile-legend{align-content:flex-start;}" in css
    assert ".dashboard-grid.is-aligned .dtile-legend:has(> .tbl-wrap){align-content:stretch;}" in css


def test_energy_and_weather_dashboards_do_not_use_the_tile_grid() -> None:
    for name in ("energiedashboard.html", "_energiedashboard_view.html", "wetterdashboard.html", "_wetter_view.html"):
        assert "is-aligned" not in (TEMPLATES / name).read_text(encoding="utf-8")
