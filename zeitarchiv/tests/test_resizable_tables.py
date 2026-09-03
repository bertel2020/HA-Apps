"""Regressionstests für anpassbare technische Datentabellen."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
SCRIPT = ROOT / "app" / "static" / "js" / "resizable-tables.js"
STYLES = ROOT / "app" / "static" / "css" / "app.css"


def test_all_technical_table_pages_load_resize_module() -> None:
    expected = {
        "backup.html",
        "cleanup.html",
        "entities_list.html",
        "entity_config.html",
        "export.html",
        "import.html",
        "settings.html",
        "statistik.html",
    }
    for name in expected:
        assert "resizable-tables.js" in (TEMPLATES / name).read_text(encoding="utf-8"), name


def test_saved_table_function_and_dashboard_are_excluded() -> None:
    for name in ("tables.html", "table_editor.html", "entities.html"):
        assert "resizable-tables.js" not in (TEMPLATES / name).read_text(encoding="utf-8"), name


def test_resize_module_persists_widths_and_offers_reset() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "localStorage.setItem" in source
    assert "localStorage.removeItem" in source
    assert "Spaltenbreiten zurücksetzen" in source
    assert "table-column-resize-handle" in source
    assert "MutationObserver" in source
    assert "htmx:afterSwap" in source
    assert "ArrowLeft" in source and "ArrowRight" in source
    assert "event.stopImmediatePropagation();" in source
    assert "handle.setAttribute('hx-disable', '')" in source
    assert "header.getBoundingClientRect().right - 10" in source


def test_wide_resized_tables_scroll_without_expanding_settings_panel() -> None:
    styles = STYLES.read_text(encoding="utf-8").replace("\n", "")
    assert ".settings-layout{display:grid;grid-template-columns:190px minmax(0,1fr)" in styles
    assert ".settings-panel{min-width:0;max-width:100%" in styles
    assert ".tbl-wrap{" in styles
    assert "max-width:100%;min-width:0;overflow-x:auto" in styles


def test_entity_config_preview_uses_shared_scroll_wrapper() -> None:
    source = (TEMPLATES / "_entity_config_form.html").read_text(encoding="utf-8")
    assert '<div class="tbl-wrap">' in source
    assert '<table class="dt compact">' in source


def test_csv_export_unit_and_rows_are_left_aligned() -> None:
    source = (TEMPLATES / "_export_table.html").read_text(encoding="utf-8")
    page = (TEMPLATES / "export.html").read_text(encoding="utf-8")
    assert "'centered' if col.key == 'type'" in source
    assert '<td>{{ row.unit or "—" }}</td>' in source
    assert "<td>{{ row.row_count | format_int }}</td>" in source
    assert "width:100%;padding:8px 0;text-align:left" in page
