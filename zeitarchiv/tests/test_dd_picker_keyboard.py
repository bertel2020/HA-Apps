"""Tastaturbedienung der gemeinsamen Auswahllisten (dd-picker.js).

Die Zeilen sind <div>s mit onclick ohne eigenen Fokus. dd-picker.js macht sie
beim Öffnen per Tastatur erreichbar. Das Verhalten selbst ist im Browser
geprüft (Housekeeping → Aktivität); dieser Test hält die tragenden Teile fest,
damit sie bei einem Umbau nicht still verschwinden.
"""

from __future__ import annotations

from _paths import APP_CSS, APP_JS


SCRIPT = (APP_JS / "dd-picker.js").read_text(encoding="utf-8")


def test_all_keys_of_the_listbox_pattern_are_handled() -> None:
    assert "document.addEventListener('keydown'" in SCRIPT
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Enter", " ", "Escape", "Tab"):
        assert f"e.key === '{key}'" in SCRIPT, key


def test_rows_become_focusable_only_by_arrow_keys() -> None:
    # tabindex=-1: per Pfeiltaste fokussierbar, ohne die Tab-Reihenfolge der
    # Seite um jede Zeile jeder Auswahlliste zu verlängern.
    assert "setAttribute('tabindex', '-1')" in SCRIPT


def test_keyboard_selection_returns_focus_to_the_trigger() -> None:
    select = SCRIPT[SCRIPT.index("function selectDDOption"):]
    select = select[: select.index("\n}\n")]
    assert "popover.contains(document.activeElement)" in select
    assert "btn.focus()" in select


def test_alpine_popovers_count_as_open_when_visible() -> None:
    # position:fixed (Tabellen-Editor) — offsetParent wäre dort immer null.
    assert "getClientRects().length > 0" in SCRIPT


def test_focused_row_shows_the_focus_ring() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert ".dd-picker-row:focus-visible{outline:2px solid var(--accent-line)" in css
