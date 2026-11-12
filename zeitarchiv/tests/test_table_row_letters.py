"""Feste Zeilen-Buchstaben in Tabellen (Forum 2026-11-12: Buchstaben bleiben beim Verschieben).

Formeln verweisen auf Zeilen über deren Buchstaben. Früher folgte der Buchstabe der Position, jedes
Umsortieren vertauschte ihn; jetzt gehört er zur Zeile (table_rows.letter)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from _paths import APP, page_text

from app.storage.index import Index
from app.table_letters import assign_row_letters

COLUMNS = [{"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False}]


def _row(label: str, row_type: str = "entity", **extra) -> dict:
    return {"label": label, "row_type": row_type, "entity_ids": [], "formula": "", "bold": False, **extra}


def test_without_stored_letters_the_old_positional_assignment_results() -> None:
    rows = [_row("a"), _row("b"), _row("t", "separator"), _row("c")]
    assert assign_row_letters(rows) == ["A", "B", "", "C"]


def test_stored_letters_are_kept_and_new_rows_get_the_smallest_free_one() -> None:
    rows = [_row("x", letter="C"), _row("neu"), _row("y", letter="A"), _row("neu2")]
    assert assign_row_letters(rows) == ["C", "B", "A", "D"]


def test_duplicates_separators_and_invalid_letters_are_repaired() -> None:
    rows = [_row("a", letter="A"), _row("b", letter="A"), _row("t", "separator", letter="B"), _row("c", letter="7")]
    assert assign_row_letters(rows) == ["A", "B", "", "C"]


def test_more_than_26_rows_leave_the_rest_without_a_letter() -> None:
    letters = assign_row_letters([_row(str(i)) for i in range(28)])
    assert letters[:26] == list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") and letters[26:] == ["", ""]


def test_letters_survive_saving_in_a_different_order() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-row-letters-"))
    try:
        index = Index(tmp / "index.sqlite")
        original = [_row("PV"), _row("Netz"), _row("Anteil", "formula", formula="A / B * 100", letter="")]
        table_id = index.create_saved_table("Buchstaben", COLUMNS, original)
        saved = index.get_saved_table(table_id)["rows"]
        assert [r["letter"] for r in saved] == ["A", "B", "C"]

        # Der Editor schickt die Zeilen umsortiert zurück, mit ihren Buchstaben: sie bleiben.
        umsortiert = [saved[1], saved[0], saved[2]]
        index.update_saved_table(table_id, "Buchstaben", COLUMNS, umsortiert)
        neu = index.get_saved_table(table_id)["rows"]
        assert [(r["label"], r["letter"]) for r in neu] == [("Netz", "B"), ("PV", "A"), ("Anteil", "C")]
        assert neu[2]["formula"] == "A / B * 100"  # die Formel wurde nicht angefasst
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_existing_tables_keep_their_positional_letters_on_migration() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-row-letters-migration-"))
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
        for table_id, position, row_type in [(1, 0, "entity"), (1, 1, "separator"), (1, 2, "entity"), (2, 0, "entity")]:
            connection.execute(
                "INSERT INTO table_rows (table_id, position, label, row_type) VALUES (?, ?, 'x', ?)",
                (table_id, position, row_type),
            )
        connection.commit()
        connection.close()

        index = Index(db_path)
        rows = index._conn.execute("SELECT table_id, position, letter FROM table_rows ORDER BY table_id, position").fetchall()
        assert [(r["table_id"], r["position"], r["letter"]) for r in rows] == [(1, 0, "A"), (1, 1, ""), (1, 2, "B"), (2, 0, "A")]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_api_rejects_an_invalid_letter(client) -> None:
    body = {"name": "Ungültiger Buchstabe", "columns": COLUMNS, "rows": [{**_row("x", entity_ids=["sensor.x"]), "letter": "ab"}]}
    assert client.post("/tables", json=body).status_code == 422


def test_the_editor_sends_and_keeps_letters_and_no_longer_rewrites_formulas() -> None:
    editor = page_text("table_editor.html")
    assert "nextFreeLetter()" in editor and "letter: r.letter || ''" in editor
    for entfallen in ("correctFormulaLetters", "snapshotRowLetters"):
        assert entfallen not in editor, entfallen


def test_the_formula_scope_follows_the_stored_letters() -> None:
    """In node: nach dem Umsortieren (A unter B) bleibt „A / B“ dieselbe Rechnung."""
    script = (
        "const T=require(process.argv[1]);"
        "const rows=[{row_type:'entity',letter:'B'},{row_type:'entity',letter:'A'},{row_type:'formula',letter:'C',formula:'A / B'}];"
        "console.log(JSON.stringify(T.rowLetters(rows)));"
        "console.log(JSON.stringify(T.rowLetters([{row_type:'entity'},{row_type:'separator'},{row_type:'entity'}])));"
    )
    source = (APP / "static/js/table-compute.js").read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-letters-node-"))
    try:
        # table-compute.js ist ein Browser-Skript: t() und window bereitstellen, Export über TableCompute.
        wrapper = tmp / "compute.cjs"
        wrapper.write_text(
            "global.t=(s)=>s;global.window=global;global.document={documentElement:{lang:'de'}};\n" + source + "\nmodule.exports=global.TableCompute||window.TableCompute;\n",
            encoding="utf-8",
        )
        run = subprocess.run(["node", "-e", script, str(wrapper)], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        gespeichert, ohne = (json.loads(line) for line in run.stdout.strip().splitlines())
        assert gespeichert == ["B", "A", "C"]
        assert ohne == ["A", None, "B"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
