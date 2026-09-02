"""Regressionstests für rein optische Trennzeilen in gespeicherten Tabellen."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "app/templates/table_editor.html").read_text(encoding="utf-8")
COMPUTE = (ROOT / "app/static/js/table-compute.js").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app/static/js/dashboard-tiles.js").read_text(encoding="utf-8")
MAIN = (ROOT / "app/main.py").read_text(encoding="utf-8")


def test_editor_offers_separator_and_full_width_preview() -> None:
    assert "+ Trennlinie" in EDITOR
    assert "row.row_type === 'separator'" in EDITOR
    assert 'class="tbl-separator-line"' in EDITOR
    # Berücksichtigt seither ausgeblendete Spalten (visibleColumnCount statt
    # der rohen Spaltenanzahl).
    assert ':colspan="visibleColumnCount + 1"' in EDITOR


def test_separator_can_be_positioned_in_existing_tables() -> None:
    assert "moveRow(row.uid, -1)" in EDITOR
    assert "moveRow(row.uid, 1)" in EDITOR


def test_separator_does_not_consume_formula_letter_or_trigger_queries() -> None:
    assert "if (r.row_type === 'separator') return null" in COMPUTE
    assert "r.row_type === 'entity' || r.row_type === 'group'" in COMPUTE
    assert "if (!letters[j]) continue" in COMPUTE


def test_separator_is_allowed_by_api_and_rendered_on_dashboard() -> None:
    # "summary" (Summenzeile) kam als eigener Zeilentyp dazu.
    assert '("entity", "group", "formula", "separator", "summary")' in MAIN
    assert 'row.row_type === \'separator\'' in DASHBOARD
    # Klasse wird inzwischen per Template-Literal zusammengesetzt
    # (sepClasses, samt optionalem tbl-bold), nicht mehr statisch geschrieben.
    assert "`tbl-separator-row${row.bold ? ' tbl-bold' : ''}`" in DASHBOARD


def test_separator_does_not_shift_zebra_striping() -> None:
    assert "let dataRowIndex = 0" in DASHBOARD
    assert "dataRowIndex % 2 === 1" in DASHBOARD
    assert "'tbl-zebra-alt': isAlternateDataRow(row.uid)" in EDITOR
    assert ".tbl-style-zebra tr.tbl-zebra-alt td" in EDITOR
    assert ".tbl-style-zebra tr:nth-child(even) td" not in EDITOR
