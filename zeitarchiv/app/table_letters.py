"""Feste Zeilen-Buchstaben (A–Z) der Tabellen.

Formeln verweisen auf Zeilen über deren Buchstaben („A / B * 100“). Bis 1.40 ergaben sich die
Buchstaben aus der Position; jedes Verschieben oder Löschen vertauschte sie, und die Formeln mussten
mitgezogen werden. Jetzt gehört der Buchstabe zur Zeile, wird mitgespeichert und bleibt beim
Verschieben. Trennlinien haben keinen. Bei tables/table-compute.js (rowLetters) und im Editor
(table_editor.js, nextFreeLetter) steht dieselbe Regel."""

from collections.abc import Sequence

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def assign_row_letters(rows: Sequence[dict]) -> list[str]:
    """Ein Buchstabe je Zeile ("" bei Trennlinien und wenn alle 26 vergeben sind).

    Gespeicherte, gültige und noch freie Buchstaben bleiben; alle anderen Datenzeilen bekommen der Reihe
    nach den kleinsten freien. Ohne gespeicherte Buchstaben (Tabellen aus 1.40, ältere Aufrufer) ergibt
    das A, B, C … in Zeilenreihenfolge, also die bisherige Zuordnung."""
    used: set[str] = set()
    result: list[str] = []
    for row in rows:
        letter = str(row.get("letter") or "")
        if row.get("row_type") != "separator" and letter in LETTERS and len(letter) == 1 and letter not in used:
            used.add(letter)
            result.append(letter)
        else:
            result.append("")
    free = (letter for letter in LETTERS if letter not in used)
    for index, row in enumerate(rows):
        if row.get("row_type") != "separator" and not result[index]:
            result[index] = next(free, "")
    return result
