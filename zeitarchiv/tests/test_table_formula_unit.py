"""Regressionstests für Einheiten berechneter Tabellenzeilen."""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT ))

from app.storage.index import Index


def test_existing_table_rows_are_migrated_with_automatic_unit_default() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-formula-unit-migration-test-"))
    try:
        db_path = tmp / "index.sqlite"
        connection = sqlite3.connect(db_path)
        connection.execute(
            """CREATE TABLE table_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT, table_id INTEGER NOT NULL,
                position INTEGER NOT NULL, label TEXT NOT NULL,
                row_type TEXT NOT NULL DEFAULT 'entity',
                entity_ids TEXT NOT NULL DEFAULT '[]',
                formula TEXT NOT NULL DEFAULT '', bold INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.execute(
            "INSERT INTO table_rows (table_id, position, label, row_type, formula) VALUES (1, 0, 'Alt', 'formula', 'A * 2')"
        )
        connection.commit()
        connection.close()

        index = Index(db_path)
        row = index._conn.execute("SELECT formula_unit FROM table_rows").fetchone()
        assert row["formula_unit"] == ""
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_formula_unit_is_saved_and_loaded() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-formula-unit-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        table_id = index.create_saved_table(
            "Formeln",
            [{"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False}],
            [
                {
                    "label": "Anteil", "row_type": "formula", "entity_ids": [],
                    "formula": "A / B * 100", "formula_unit": "%", "bold": False,
                }
            ],
        )

        assert index.get_saved_table(table_id)["rows"][0]["formula_unit"] == "%"
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_formula_unit_feature_is_wired_through_editor_and_compute_core() -> None:
    editor = (ROOT / "app/templates/table_editor.html").read_text(encoding="utf-8")
    compute = (ROOT / "app/static/js/table-compute.js").read_text(encoding="utf-8")

    # Natives title auf das App-eigene data-tooltip-System umgestellt.
    assert 'data-tooltip="Einheit"' in editor
    assert "formula_unit: r.row_type === 'formula'" in editor
    assert "inheritedFormulaUnit(row.formula, unitScope)" in compute
    assert "memberUnits.length === 1 ? memberUnits[0] : ''" in compute
