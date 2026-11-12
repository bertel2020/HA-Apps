"""Tabellen-Editor: Zeilen-Buchstaben bleiben alphabetisch (A, B, C … von oben nach unten), die Formeln ziehen mit.

Jede Änderung, die Buchstaben verschiebt, muss die Formeln mitnehmen — nicht nur das Verschieben (Forum 2026-11-12):
Beim Löschen, Duplizieren und beim Wechsel von/zu „Trennlinie“ blieb die Formel auf dem alten Buchstaben und zeigte
still auf eine andere Zeile."""

import re

from _paths import page_text


def _body(name: str) -> str:
    editor = page_text("table_editor.html")
    treffer = re.search(r"\n        " + re.escape(name) + r"\([^)]*\) \{\n(.*?)\n        \},", editor, re.S)
    assert treffer, name
    return treffer.group(1)


def test_every_letter_shifting_action_takes_the_formulas_along() -> None:
    for name in ("moveRow", "removeRow", "duplicateRow", "setRowType"):
        body = _body(name)
        assert "snapshotRowLetters()" in body, name
        assert body.index("snapshotRowLetters()") < body.index("correctFormulaLetters("), name


def test_a_formula_pointing_at_a_vanished_row_is_marked_instead_of_repointed() -> None:
    body = _body("correctFormulaLetters")
    assert "|| '?'" in body and "|| ch" not in body


def test_the_letters_stay_positional() -> None:
    """Keine festen, gespeicherten Buchstaben: sie ergeben sich aus der Reihenfolge (alphabetisch)."""
    editor = page_text("table_editor.html")
    assert "String.fromCharCode(65 + ((dataIndex++) % 26))" in editor
