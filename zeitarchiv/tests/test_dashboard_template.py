"""Regressionstests für das Dashboard der leeren Zeitarchiv-Startseite."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


class _FakeURL:
    path = "/"


class _FakeRequest:
    """Minimaler Ersatz für Starlettes Request — _topnav.html liest nur
    request.url.path (aktuelle Seite hervorheben), sonst nichts."""

    url = _FakeURL()


def _render_empty_dashboard() -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    return environment.get_template("entities.html").render(
        request=_FakeRequest(),
        css_v=1,
        js_v=1,
        font_scale_value="1",
        dashboard_name="Dashboard",
        dashboard_row_height=210,
        entity_count=0,
        type_breakdown="",
        total_rows="0",
        total_size="0 B",
        rows_sparkline=None,
        size_sparkline=None,
        tiles=[],
        can_add_tile=True,
        unpinned_charts=[],
        unpinned_tables=[],
    )


def test_empty_home_page_still_renders_dashboard_and_add_tile() -> None:
    html = _render_empty_dashboard()

    assert "<h2>Dashboard</h2>" in html
    assert 'id="dashboard-grid"' in html
    assert 'class="dtile dtile-add"' in html


def test_empty_dashboard_links_to_first_chart_and_table_editors() -> None:
    html = _render_empty_dashboard()

    assert 'href="charts/new">+ Neuer Chart</a>' in html
    assert 'href="tables/new">+ Neue Tabelle</a>' in html


def test_dashboard_tile_has_three_by_three_size_picker_and_grid_spans() -> None:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    html = environment.get_template("_dashboard_tiles.html").render(
        tiles=[{
            "kind": "chart",
            "id": 7,
            "name": "Großer Chart",
            "entity_ids": ["sensor.a"],
            "entity_names": {},
            "range_key": "day",
            "continuous": False,
            "resolution_preset": "auto",
            "dynamic_y_axis": False,
            "show_legend": False,
            "chart_stats": False,
            "legend_metrics": [],
            "legend_style": "chips",
            "chart_type": "auto",
            "show_values": False,
            "decimals": "auto",
            "grid_cols": 2,
            "grid_rows": 3,
        }],
        can_add_tile=False,
        unpinned_charts=[],
        unpinned_tables=[],
    )
    assert 'data-grid-cols="2" data-grid-rows="3"' in html
    assert 'style="--tile-cols:2;--tile-rows:3"' in html
    assert html.count('class="dtile-size-cell') == 9
    assert "Kachelmenü öffnen" in html
    assert "Vom Dashboard entfernen" in html
    assert 'class="dtile-remove"' not in html


def test_dashboard_css_and_script_support_variable_tile_sizes() -> None:
    template = (TEMPLATES_DIR / "entities.html").read_text(encoding="utf-8")
    script = (TEMPLATES_DIR.parent / "static" / "js" / "dashboard-tiles.js").read_text(encoding="utf-8")
    assert "grid-auto-rows:var(--dashboard-row-height)" in template
    assert "--dashboard-row-height:{{ dashboard_row_height | default(218) }}px" in template
    assert 'data-grid-cols="3"' in template
    assert "'entity-size' : 'size'" in script
    # War früher ein JS-seitiges Zeilen-Slice-Limit (TABLE_TILE_MAX_ROWS_PER_
    # GRID_ROW) — ersetzt durch CSS-Scrolling der Tabellen-Kachel selbst.
    assert ".dtile-table-preview{" in template and "overflow:auto;" in template


def test_dashboard_tile_title_only_reserves_space_for_one_menu_button() -> None:
    template = (TEMPLATES_DIR / "entities.html").read_text(encoding="utf-8")
    assert "padding-right:30px" in template
    assert ".dtile-menu-btn{" in template
    assert ".dtile-size-btn{" not in template


def test_value_tile_editor_and_sparkline_defaults_are_exposed() -> None:
    menu = (TEMPLATES_DIR / "_dashboard_tile_menu.html").read_text(encoding="utf-8")
    tiles = (TEMPLATES_DIR / "_dashboard_tiles.html").read_text(encoding="utf-8")
    script = (TEMPLATES_DIR.parent / "static" / "js" / "dashboard-tiles.js").read_text(encoding="utf-8")
    assert "auto_open_entity_id == tile.entity_id" in menu
    assert 'name="new_entity_id"' in menu
    assert "entityPicker(" in menu
    assert 'placeholder="Entität suchen …"' in menu
    assert "Letzte Aktualisierung" in menu
    assert "Sparkline-Auflösung" in menu
    assert "('5min', '5 Min')" in menu
    assert "Sparkline-Auflösung <strong>" not in menu
    assert "Nachkommastellen <strong>" not in menu
    assert 'data-sparkline-resolution="{{ tile.sparkline_resolution }}"' in tiles
    assert "dashboard/sparkline-resolution" in script
    assert "resampleSparklinePoints" in script
    assert "range=day&raw=true" in script


def test_value_tile_layout_bottom_aligns_age_and_moves_title_only_when_roomy() -> None:
    for template_name in ("entities.html", "dashboard_detail.html"):
        source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
        assert "display:flex;align-content:center;align-items:baseline;justify-content:space-between" in source
        assert ".dtile-entity[data-grid-rows=\"1\"] .dtile-title{padding-top:0;}" in source


def test_table_tile_sticky_corner_stays_above_header_and_first_column() -> None:
    selector = ".tbl-style-sticky-header.tbl-style-sticky-first-col tr.tbl-header-row th:first-child{z-index:4;}"
    for template_name in ("entities.html", "dashboard_detail.html"):
        source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
        assert selector in source


def test_table_tile_keeps_saved_widths_scrollable_and_sticky_borders_attached() -> None:
    script = (TEMPLATES_DIR.parent / "static" / "js" / "dashboard-tiles.js").read_text(encoding="utf-8")
    assert "width:${w}px;min-width:${w}px;max-width:${w}px" in script
    # Eine gespeicherte Label-Breite allein erzwingt seither kein Spalten-
    # Layout mehr — nur noch gespeicherte Wert-Breiten (savedValueWidths).
    assert "const hasSavedValueWidths = savedValueWidths.some(w => w != null);" in script
    assert "const needsColumnLayout = hasSavedValueWidths || !!style.equal_value_cols;" in script
    assert "width:max(100%,${savedTableWidth}px);min-width:${savedTableWidth}px;table-layout:fixed;" in script
    assert '<colgroup>${layoutWidths.map(width => `<col style="width:${width}px">`)' in script
    for template_name in ("entities.html", "dashboard_detail.html"):
        source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
        assert "table.dt.dtile-mini-table{width:100%;table-layout:auto;}" in source
        assert "table.dt.dtile-mini-table.tbl-style-sticky-header tr.tbl-header-row th{border-bottom:0;}" in source
        assert "table.dt.dtile-mini-table.tbl-style-sticky-header{border-collapse:separate" not in source
        assert "table.dt.dtile-mini-table{width:100%;table-layout:auto;font-size:" not in source


def test_stacked_chart_decimal_options_are_centered() -> None:
    css = (TEMPLATES_DIR.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".menu-row-stack>.seg{align-self:center;max-width:100%;}" in css


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
