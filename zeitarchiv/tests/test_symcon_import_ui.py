"""Regressionstests für die kompakte und bedienbare Symcon-Importtabelle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORT = (ROOT / "app/templates/import.html").read_text(encoding="utf-8")
CSV_SECTION = (ROOT / "app/templates/_csv_import_section.html").read_text(encoding="utf-8")
REPORTS_PANEL = (ROOT / "app/templates/_reports_panel.html").read_text(encoding="utf-8")
NAV = (ROOT / "app/templates/_settings_nav.html").read_text(encoding="utf-8")
MAIN = (ROOT / "app/import_routes.py").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def test_delete_action_precedes_filters_and_table() -> None:
    delete_pos = IMPORT.index('class="import-delete-row"')
    toolbar_pos = IMPORT.index('class="toolbar import-filter-toolbar"')
    table_pos = IMPORT.index('id="import-table"')
    assert delete_pos < toolbar_pos < table_pos


def test_both_delete_actions_match_and_require_confirmation() -> None:
    assert 'static/js/confirm-dialog.js?v={{ js_v }}' in IMPORT
    assert "onsubmit=\"confirmSymconDelete(event, this)\"" in IMPORT
    assert "async function confirmSymconDelete(event, form)" in IMPORT
    assert "optionalen settings.json wirklich löschen" in IMPORT
    assert 'class="btn btn-danger import-delete-button">Quelldaten löschen</button>' in IMPORT
    assert 'class="btn btn-danger import-delete-button"' in CSV_SECTION
    assert ">Quelldaten löschen</button>" in CSV_SECTION
    assert "hx-confirm=" in CSV_SECTION
    assert "aktuelle Importkonfiguration wirklich löschen" in CSV_SECTION
    assert 'data-confirm-label="Quelldaten löschen"' in CSV_SECTION
    assert ".import-delete-button{min-width:154px;}" in IMPORT


def test_symcon_delete_redirects_to_styled_import_page() -> None:
    route_start = MAIN.index('@router.post("/import/delete"')
    route_end = MAIN.index('@router.post("/import/dry-run"', route_start)
    route = MAIN[route_start:route_end]
    assert "RedirectResponse" in route
    assert 'url=f"{app_root}/import"' in route
    assert "status_code=303" in route
    assert 'TemplateResponse(request, "import.html"' not in route


def test_compact_column_layout_prioritizes_entity_mapping() -> None:
    assert 'class="dt symcon-import-table"' in IMPORT
    assert 'class="symcon-col-check"' in IMPORT
    assert 'class="symcon-col-id"' in IMPORT
    assert 'class="symcon-col-name"' in IMPORT
    assert ".symcon-col-check{width:40px;}" in IMPORT
    assert ".symcon-col-id{width:92px;}" in IMPORT
    assert ".symcon-col-name{width:235px;}" in IMPORT


def test_name_tooltip_contains_name_and_parent_as_two_lines() -> None:
    assert 'class="entity-tooltip symcon-name-tooltip"' in IMPORT
    assert "<strong>{{ row.symcon_name }}</strong>" in IMPORT
    assert 'class="symcon-tooltip-parent"' in IMPORT
    assert "data-tooltip=\"{{ row.symcon_name }}\"" not in IMPORT


def test_mapping_can_be_cleared_and_reselected_in_both_importers() -> None:
    assert "function clearMapping(button)" in IMPORT
    assert "input.showPicker()" in IMPORT
    assert IMPORT.count('class="map-clear"') == 1
    assert CSV_SECTION.count('class="map-clear"') == 1
    assert "onfocus=\"selectMappingValue(this)\"" in IMPORT
    assert "onfocus=\"selectMappingValue(this)\"" in CSV_SECTION


def test_period_uses_german_date_and_regular_table_font() -> None:
    assert 'strftime("%d.%m.%Y")' in MAIN
    assert 'class="period-cell"' in IMPORT
    assert 'class="mono nowrap"' not in IMPORT
    assert "table.dt{width:100%;border-collapse:collapse;font-family:var(--font-display)" in CSS


def test_row_count_is_formatted_only_in_template() -> None:
    assert '"row_count": v.row_count' in MAIN
    assert '"row_count": format_int(v.row_count)' not in MAIN
    assert "{{ row.row_count | format_int }}" in IMPORT


def test_unit_has_its_own_compact_column() -> None:
    assert '<col class="symcon-col-period"><col class="symcon-col-rows"><col class="symcon-col-unit">' in IMPORT
    assert ".symcon-col-unit{width:70px;}" in IMPORT
    assert "{{ row.row_count | format_int }}" in IMPORT
    assert '{{ row.symcon_unit or "—" }}' in IMPORT


def test_unit_mismatch_shows_factor_control_and_conversion_formula() -> None:
    assert 'data-symcon-unit="{{ row.symcon_unit or \'\' }}"' in IMPORT
    assert 'data-unit="{{ unit }}"' in IMPORT
    assert 'class="unit-conversion" hidden' in IMPORT
    assert 'name="factor_{{ row.variable_id }}"' in IMPORT
    assert "Einheiten stimmen nicht überein" in IMPORT
    assert "Importwert = Symcon-Wert × Faktor" in IMPORT
    assert "'klx': ['illuminance', 1000]" in IMPORT
    assert "factor=factor" in MAIN


def test_csv_hint_matches_current_workflow() -> None:
    assert "So funktioniert der Import einer CSV-Datei" in IMPORT
    assert "Zeitstempel- und Wertspalte auswählen" in IMPORT
    assert "Bestehende Monatsarchive bleiben unverändert" in IMPORT
    assert "bis „Quelldaten löschen“ gespeichert" in IMPORT


def test_reports_are_the_third_import_tab_instead_of_sidebar_navigation() -> None:
    symcon = IMPORT.index('id="tab-btn-symcon"')
    csv = IMPORT.index('id="tab-btn-csv"')
    reports = IMPORT.index('id="tab-btn-reports"')
    assert symcon < csv < reports
    assert 'id="tab-reports"' in IMPORT
    assert '{% include "_reports_panel.html" %}' in IMPORT
    assert '>Reports <span class="tag">' not in NAV


def test_report_filters_keep_the_reports_tab_active() -> None:
    assert '<input type="hidden" name="tab" value="reports">' in REPORTS_PANEL
    assert 'action="import"' in REPORTS_PANEL
    assert "window.location.href = url" in IMPORT
