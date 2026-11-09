"""Der Resize-Listener der Dashboard-Kacheln wird einmal registriert.

setup() läuft nach jedem htmx-Austausch des Dashboard-Gitters erneut. Stand die
Registrierung darin, kam bei jedem Austausch ein weiterer Listener hinzu.
"""

from __future__ import annotations

import re

from _paths import APP_JS


SOURCE = (APP_JS / "dashboard-tiles.js").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(rf"^( *)function {name}\(\) \{{.*?^\1\}}", SOURCE, re.M | re.S)
    assert match, name
    return match.group(0)


def test_resize_listener_is_registered_exactly_once() -> None:
    assert SOURCE.count("window.addEventListener('resize'") == 1


def test_setup_does_not_register_window_listeners() -> None:
    assert "window.addEventListener" not in _function_body("setup")


def test_charts_follow_their_container_through_one_resize_observer() -> None:
    """Phase 3 der breiten Darstellung: ein ResizeObserver je Seite, nicht je setup()-Lauf."""
    assert SOURCE.count("new ResizeObserver") == 1
    assert "ResizeObserver" not in _function_body("setup")
    # Jede ECharts-Instanz, auch der Zustands-Donut, wird beobachtet.
    assert SOURCE.count("= echarts.init(chartEl)") == SOURCE.count("observeChart(chart, chartEl)") == 2


def test_disposed_charts_are_unobserved() -> None:
    assert SOURCE.count("chart.dispose()") == 1  # nur in disposeChart()
    assert "instances.forEach(disposeChart)" in SOURCE
    assert "resizeObserver.unobserve(el)" in SOURCE


def test_window_resize_is_only_the_fallback_without_resize_observer() -> None:
    assert "if (!resizeObserver) window.addEventListener('resize'" in SOURCE
