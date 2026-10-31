"""Linker Rand der Charts aus der gemessenen Breite der Y-Achsen-Beschriftung (axis-fit.js)."""

from __future__ import annotations

import pytest

from _paths import APP, TEMPLATES, page_text

JS = APP / "static" / "js" / "axis-fit.js"


def test_both_chart_pages_load_and_attach_axis_fit() -> None:
    for template, script in (("entity_detail.html", "pages/entity_detail.js"), ("chart_editor.html", "pages/chart_editor.js")):
        assert "js/axis-fit.js" in page_text(TEMPLATES / template), template
        assert "AxisFit.attach(chartInstance)" in (APP / "static" / "js" / script).read_text(encoding="utf-8"), script


def test_axis_fit_is_loaded_before_the_page_scripts() -> None:
    for template, page_script in (("entity_detail.html", "js/pages/entity_detail.js"), ("chart_editor.html", "js/pages/chart_editor.js")):
        html = page_text(TEMPLATES / template)
        assert html.index("js/axis-fit.js") < html.index(page_script), template


def test_axis_fit_measures_instead_of_guessing() -> None:
    text = JS.read_text(encoding="utf-8")
    assert "measureText" in text and "getViewLabels" in text
    assert "document.fonts.ready" in text


@pytest.mark.parametrize("fragment", ["leftAxes === 1", "grid.length !== 1"])
def test_axis_fit_leaves_multi_axis_and_multi_grid_charts_alone(fragment: str) -> None:
    assert fragment in JS.read_text(encoding="utf-8")
