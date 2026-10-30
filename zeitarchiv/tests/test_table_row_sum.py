"""Regressionstests für die Σ-Spalte (Summe je Zeile) in Vergleichstabellen."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.storage.index import Index

from _paths import APP, page_text

EDITOR = page_text("table_editor.html")
EDITOR_JS = (APP / "static/js/pages/table_editor.js").read_text(encoding="utf-8")
COMPUTE = (APP / "static/js/table-compute.js").read_text(encoding="utf-8")
DASHBOARD = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
MAIN = (APP / "main.py").read_text(encoding="utf-8")


def _column(label: str, **extra) -> dict:
    return {"label": label, "range_key": "day", "offset": 0, "year_over_year": False, **extra}


def test_in_sum_is_saved_loaded_and_defaults_to_true() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-row-sum-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        table_id = index.create_saved_table(
            "Strom",
            [_column("Heute"), _column("Gestern", in_sum=False)],
            [{"label": "WP", "row_type": "entity", "entity_ids": ["sensor.wp"], "formula": "", "bold": False}],
            {"show_row_sum": True, "row_sum_label": "Woche", "row_sum_position": "right"},
        )
        saved = index.get_saved_table(table_id)
        assert [c["in_sum"] for c in saved["columns"]] == [True, False]
        assert saved["style"]["row_sum_position"] == "right"
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_existing_columns_are_migrated_as_counting_towards_the_sum() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-row-sum-migration-test-"))
    try:
        db_path = tmp / "index.sqlite"
        connection = sqlite3.connect(db_path)
        connection.execute(
            """CREATE TABLE table_columns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, table_id INTEGER NOT NULL,
                position INTEGER NOT NULL, label TEXT NOT NULL,
                range_key TEXT NOT NULL DEFAULT 'month', offset INTEGER NOT NULL DEFAULT 0,
                year_over_year INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.execute("INSERT INTO table_columns (table_id, position, label) VALUES (1, 0, 'Alt')")
        connection.commit()
        connection.close()

        index = Index(db_path)
        row = index._conn.execute("SELECT in_sum FROM table_columns").fetchone()
        assert row["in_sum"] == 1
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sum_options_are_validated_on_the_server() -> None:
    assert "show_row_sum: bool = False" in MAIN
    assert 'row_sum_position: str = "left"' in MAIN
    assert "in_sum: bool = True" in MAIN
    assert "body.style.row_sum_position not in _TABLE_ROW_SUM_POSITIONS" in MAIN
    assert "MAX_TABLE_ROW_SUM_LABEL_LENGTH" in MAIN


def test_editor_and_tile_share_one_sum_calculation() -> None:
    assert "function computeRowSums(columns, rows, values, summable)" in COMPUTE
    assert "computeRowSums, sumOverlap" in COMPUTE
    assert "TableCompute.computeRowSums(cols, rows, this._computedValues, this._summable)" in EDITOR_JS
    assert "TableCompute.computeRowSums(visibleCols, allRows, allValues, summable)" in DASHBOARD


def test_only_counters_switches_and_explicit_sums_are_summable() -> None:
    assert "aggregation === 'sum'" in COMPUTE
    assert "s.aggregation_type === 'counter' || s.aggregation_type === 'switch'" in COMPUTE


def test_hidden_columns_never_count_towards_the_sum() -> None:
    assert "!columns[ci].hidden && columns[ci].in_sum !== false" in COMPUTE


def test_sum_column_is_rendered_left_or_right_in_editor_and_tile() -> None:
    assert "style.row_sum_position === 'left'" in EDITOR
    assert "style.row_sum_position === 'right'" in EDITOR
    assert "sumLeft ? sumTd(row, ri)" in DASHBOARD
    assert "sumRight ? sumTd(row, ri)" in DASHBOARD
    assert "visibleCols.length + 1 + (showRowSum ? 1 : 0)" in DASHBOARD


def test_csv_export_contains_the_sum_column() -> None:
    assert "csvEscape(this.sumLabel())" in EDITOR_JS
