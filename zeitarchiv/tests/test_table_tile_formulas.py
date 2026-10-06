"""Regression tests for hidden rows in the dashboard table tile."""


from _paths import APP


DASHBOARD = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")


def test_tile_computes_formulas_over_hidden_rows_too() -> None:
    # Hidden rows are not rendered but keep their formula letter, like in
    # table_editor.html. Filtering them out before computeValues() shifted the
    # letters and turned formulas that reference a hidden row into "Fehler".
    assert "TableCompute.computeValues(base, visibleCols, allRows)" in DASHBOARD
    assert "TableCompute.computeValues(base, visibleCols, visibleRows)" not in DASHBOARD
    assert "values = values.map(colValues => visibleRowIndexes.map(i => colValues[i]))" in DASHBOARD


def test_tile_sections_include_hidden_rows_like_the_editor() -> None:
    # Percent share and heatmap range run over every entity/group row of the
    # section, hidden ones included (sectionMemberCells/columnHeatmapRange in
    # table_editor.js iterate this.rows without a visibility filter).
    assert "allValues[ci] && allValues[ci][j]" in DASHBOARD
    assert "visibleRows[j].row_type" not in DASHBOARD
