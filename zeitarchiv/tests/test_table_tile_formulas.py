"""Regression tests for formula rows in the dashboard table tile."""


from _paths import APP


DASHBOARD = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")


def test_tile_computes_formulas_over_hidden_rows_too() -> None:
    # Hidden rows are not rendered but keep their formula letter, like in
    # table_editor.html. Filtering them out before computeValues() shifted the
    # letters and turned formulas that reference a hidden row into "Fehler".
    assert "TableCompute.computeValues(base, visibleCols, computeRows)" in DASHBOARD
    assert "TableCompute.computeValues(base, visibleCols, visibleRows)" not in DASHBOARD
    assert "values = values.map(colValues => visibleRowIndexes.map(i => colValues[i]))" in DASHBOARD


def test_tile_still_skips_queries_for_unreferenced_hidden_rows() -> None:
    # Keeps the intent of test_dashboard_only_computes_visible_table_slice: a
    # hidden entity row that no formula needs must not trigger a query.
    assert "!referencedLetters.has(rowLetters[i])" in DASHBOARD
    assert "? {...r, entity_ids: []}" in DASHBOARD

