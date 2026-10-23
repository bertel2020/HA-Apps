"""Vergleich in Dashboard-Kacheln: die Kachel zeigt den Vergleich, der mit
ihrem Chart gespeichert ist (saved_charts.compare_settings) — Vorjahr als
Nebenserie, Laufsumme/Soll als Monats-Balkenpaare, Langzeit als Linie je Jahr
mit Ø; eine 1×1-Kachel zeichnet nur das angezeigte Jahr und den Ø."""

from __future__ import annotations

from _paths import APP, TEMPLATES

TILES_JS = (APP / "static" / "js" / "dashboard-tiles.js").read_text(encoding="utf-8")


def test_tile_carries_the_saved_comparison() -> None:
    tiles_tpl = (TEMPLATES / "_dashboard_tiles.html").read_text(encoding="utf-8")
    assert "data-compare-settings='{{ tile.compare_settings | default({}) | tojson }}'" in tiles_tpl
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert '"compare_settings": c["compare_settings"],' in main


def test_tile_requests_the_comparison_like_the_editor() -> None:
    fn = TILES_JS.split("  function tileCompare(", 1)[1].split("\n  }\n", 1)[0]
    # Dieselben Sperren wie der Vergleichs-Knopf im Editor.
    assert "['timeline', 'donut', 'shares', 'changes', 'heatmap'].includes(chartType) || resolutionPreset === 'full'" in fn
    assert "mode === 'longterm' && range === 'year' && !continuous && entityIds.length === 1" in fn
    assert "yearsCompare: mode === 'previous' && range === 'year' && (cumsum || !!target)" in fn
    assert "params.set('compare_mode', compare.mode);" in TILES_JS
    assert "if (compare.longterm) params.set('previous_years', String(compare.years));" in TILES_JS


def test_longterm_tile_reduces_small_tiles_and_legend() -> None:
    assert "small: tileEl?.dataset.gridCols === '1' && tileEl?.dataset.gridRows === '1'" in TILES_JS
    assert "const shown = small ? years.slice(0, 1) : years;" in TILES_JS
    assert "legendItems.push({more: t('+{n} Jahre', {n: multiYear.hiddenYears})});" in TILES_JS
    # Größenwechsel über 1×1 hinweg zeichnet die Langzeit-Kachel neu.
    assert "if (dtileBody?.dataset.longtermActive === 'true') await renderTile(dtileBody);" in TILES_JS
