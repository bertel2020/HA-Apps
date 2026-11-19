"""Anteile (%) auf einer Achse mit Balken ohne Stapel: Höhe nach der Summe.

Liegen neben dem Stapel Balken ohne Stapel oder Linien auf derselben Achse, lässt sich der Stapel nicht auf
0–100 % normieren. Dann bleibt die Höhe absolut, die Segmente tragen ihren Anteil als Beschriftung und über
dem Balken steht die Summe (ein unsichtbares, leeres Segment im selben Stapel). Chart-Seite und Kachel
zeichnen getrennt, beide müssen es können.
"""

from _paths import APP

FILES = ("static/js/chart-tile-render.js", "static/js/pages/chart_editor.js")


def _source(path: str) -> str:
    return (APP / path).read_text(encoding="utf-8")


def test_both_renderers_keep_the_axis_absolute_and_label_shares() -> None:
    for path in FILES:
        source = _source(path)
        assert "const shareAxis = new Set(" in source, path
        assert "shareTotals" in source, path
        assert "isTotalHelper: true" in source, path


def test_the_total_sits_in_the_same_stack_as_a_transparent_empty_segment() -> None:
    for path in FILES:
        source = _source(path)
        helper = source[source.index("isTotalHelper: true"):]
        helper = helper[:helper.index("label: {")]
        assert "stack: key" in helper, path
        assert "itemStyle: {color: 'transparent'}" in source, path


def test_the_tooltip_does_not_list_the_total_helper() -> None:
    for path in FILES:
        assert "!(echartsSeries[p.seriesIndex] || {}).isTotalHelper" in _source(path), path


def test_the_hint_describes_the_absolute_height() -> None:
    template = (APP / "templates/chart_editor.html").read_text(encoding="utf-8")
    assert "bleibt die Höhe absolut" in template
    assert "wirken erst" not in template
