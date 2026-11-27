"""Art des Niederschlags in der Wetterlage (static/js/wetter-rain.js), in node ausgeführt."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _paths import APP

MODULE = APP / "static/js/wetter-rain.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


def _run(expression: str):
    script = f"const r = require({json.dumps(str(MODULE))}); console.log(JSON.stringify({expression}));"
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(done.stdout)


def test_cold_means_at_most_one_degree_celsius() -> None:
    assert _run('[r.isCold({unit: "°C", value: 1}), r.isCold({unit: "°C", value: 1.1}), r.isCold({unit: "°C", value: -1.3}), r.isCold({unit: "°C", value: 0})]') == [True, False, True, True]


def test_cold_needs_a_celsius_temperature() -> None:
    assert _run('[r.isCold(null), r.isCold({unit: "°F", value: 20}), r.isCold({unit: "°C", value: null}), r.isCold({unit: "K", value: 270})]') == [False] * 4


@pytest.mark.parametrize("kind", ["rain", "snow", "sleet", "hail"])
def test_the_kind_from_the_weather_state_wins_even_when_cold(kind: str) -> None:
    assert _run(f'r.kindOf({{raining: true, kind: "{kind}"}}, true)') == kind
    assert _run(f'r.kindOf({{raining: false, last_kind: "{kind}"}}, false)') == kind


def test_without_a_kind_it_is_neutral_in_the_cold_and_rain_otherwise() -> None:
    assert _run('[r.kindOf({raining: true}, true), r.kindOf({raining: true}, false)]') == ["precip", "rain"]
    assert _run('[r.kindOf({raining: false, last: 5}, true), r.kindOf({raining: false, last: 5}, false)]') == ["precip", "rain"]


def test_a_running_kind_is_not_used_for_the_dry_state_and_the_other_way_round() -> None:
    assert _run('r.kindOf({raining: false, kind: "snow"}, false)') == "rain"
    assert _run('r.kindOf({raining: true, last_kind: "snow"}, false)') == "rain"
    assert _run("r.kindOf(null, false)") is None


def test_since_parts_follow_the_old_thresholds() -> None:
    assert _run("[r.sinceParts(null), r.sinceParts(35), r.sinceParts(119.6), r.sinceParts(120), r.sinceParts(185)]") == [
        {"over": 48}, {"n": 35}, {"h": 2, "m": "00"}, {"h": 2, "m": "00"}, {"h": 3, "m": "05"},
    ]
