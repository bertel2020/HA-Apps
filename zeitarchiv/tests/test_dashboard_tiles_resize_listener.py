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
