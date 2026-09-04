"""Regressionstests für optionale Darstellungsmerkmale von Vergleichstabellen."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "app/templates/table_editor.html").read_text(encoding="utf-8")
COMPUTE = (ROOT / "app/static/js/table-compute.js").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app/static/js/dashboard-tiles.js").read_text(encoding="utf-8")
MAIN = (ROOT / "app/main.py").read_text(encoding="utf-8")


def test_every_new_display_feature_is_an_independent_editor_option() -> None:
    # separator_labels und formula_row_accent waren globale style.*-Schalter,
    # leben seither je pro Zeile als row.show_label bzw. row.accent (siehe
    # _TableRowBody in main.py) — keine unabhängigen Editor-Optionen im
    # style.*-Sinn mehr, daher hier entfernt.
    options = {
        "sticky_first_col": "Erste Spalte fixieren",
        "comparison_columns": "Vergleichsspalten absetzen",
        "show_deviation": "Abweichung zum Vergleich",
        "explicit_missing": "Fehlende Werte ausschreiben",
        "show_units": "Einheiten anzeigen",
        "align_units": "Einheiten ausrichten",
        "small_units": "Einheiten kleiner",
        "align_numbers": "Dezimalstellen ausrichten",
    }
    for key, label in options.items():
        assert f"style.{key}" in EDITOR
        assert label in EDITOR
        assert f"{key}: bool" in MAIN


def test_units_can_be_hidden_without_changing_computed_values() -> None:
    assert "style.show_units && cellUnit(row, col)" in EDITOR
    assert "style.show_units === false ? '' : TableCompute.cellUnit(cell)" in DASHBOARD
    assert "function cellUnit(cell)" in COMPUTE


def test_comparison_columns_and_deviation_share_pairing_logic() -> None:
    assert "function isComparisonColumn(col)" in COMPUTE
    assert "function comparisonIndexForBase(columns, baseIndex)" in COMPUTE
    assert "function deviationText(baseCell, comparisonCell)" in COMPUTE
    assert "TableCompute.comparisonIndexForBase(visibleCols, ci)" in DASHBOARD
    assert "TableCompute.deviationText(baseCell, comparisonCell)" in EDITOR
    assert "const comparisonLabel = this.renderedColumnLabel(comparisonCol);" in EDITOR
    assert "`Gegenüber ${comparisonLabel}" in EDITOR


def test_separator_label_is_editable_saved_and_rendered() -> None:
    assert "Abschnittsname (optional)" in EDITOR
    assert "return cap(row.label.trim())" in EDITOR
    assert "tbl-separator-label" in EDITOR
    assert "row.show_label && row.label" in DASHBOARD


def test_formula_unit_field_matches_aggregation_picker_width() -> None:
    assert ".tbl-row-top .tbl-agg-picker{flex:none;width:76px;}" in EDITOR
    assert "input.tbl-formula-unit-inline{flex:none;width:76px;" in EDITOR
