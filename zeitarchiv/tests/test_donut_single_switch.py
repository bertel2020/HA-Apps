"""Donut mit einem einzelnen Schalter: „An“ und „Aus“ statt eines vollen Rings.

Mit einer einzigen Zahl wäre der Ring voll und ohne Aussage, bei einem Schalter gibt es aber zwei
Zustände. Chart-Editor und Kachel/Karte müssen dieselbe Aufteilung zeichnen."""

from __future__ import annotations

from _paths import APP, template_text

EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
TILE_JS = (APP / "static/js/chart-tile-render.js").read_text(encoding="utf-8")
STATE_JS = (APP / "static/js/state-entities.js").read_text(encoding="utf-8")


def test_the_donut_button_is_open_for_a_single_switch() -> None:
    assert "get singleSwitch()" in EDITOR_JS
    assert "this.series.length >= 2 || this.singleSwitch" in EDITOR_JS


def test_the_aggregation_row_is_hidden_for_the_on_off_donut() -> None:
    assert 'x-show="donut && !hasState && !singleSwitch"' in template_text("chart_editor.html")


def test_editor_and_tile_use_the_same_helper() -> None:
    assert "function onOffRows(" in STATE_JS
    assert "StateEntities.onOffRows(" in EDITOR_JS
    assert "StateEntities.onOffRows(" in TILE_JS
    # Die Kachel nimmt dieselbe Ausnahme wie der Editor: genau eine Schalter-Serie.
    assert "series.length === 1 && series[0].aggregation_type === 'switch'" in TILE_JS


def test_the_donut_centre_text_is_bound_for_both_donut_kinds() -> None:
    """Mitte des Rings: Summe bzw. Einschaltdauer in Ruhe, beim Überfahren der Wert der Scheibe."""
    assert EDITOR_JS.count("this.bindDonutCenter(") == 2
    assert "donutCenterGraphic(" in EDITOR_JS
