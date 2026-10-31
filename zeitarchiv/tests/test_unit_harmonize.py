"""Wh- und kWh-Serien im selben Chart teilen sich eine Achse.

Das Chart zeichnet eine Y-Achse je Einheit. Ertrag in Wh (OpenDTU) und
Prognose in kWh landeten deshalb auf zwei unabhängig skalierten Achsen und
waren optisch nicht vergleichbar. harmonizeUnits() rechnet gemischte
Einheiten einer Familie auf die größte vorkommende um.
"""

from __future__ import annotations

import json
import subprocess

from _paths import APP_JS


SCRIPT = APP_JS / "number-format.js"

_RUNNER = r"""
const [script, series] = process.argv.slice(1);
const vm = require('vm');
const ctx = {window: {ZA_FORMAT: 'en-GB'}};
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(script, 'utf8'), ctx);
const input = JSON.parse(series);
const out = ctx.window.NumberFormat.harmonizeUnits(input);
console.log(JSON.stringify({same: out === input, out}));
"""


def _run(series: list[dict]) -> dict:
    result = subprocess.run(
        ["node", "-e", _RUNNER, str(SCRIPT), json.dumps(series)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _s(unit: str, value: float, decimals: int | None = None, **extra) -> dict:
    return {
        "unit": unit, "decimals": decimals,
        "points": [{"ts": 1, "value": value, "min": value, "max": value}], **extra,
    }


def test_wh_and_kwh_become_kwh() -> None:
    result = _run([_s("kWh", 1.628, 3), _s("Wh", 780, 0)])["out"]
    assert [s["unit"] for s in result] == ["kWh", "kWh"]
    assert result[0]["points"][0]["value"] == 1.628
    wh = result[1]
    assert wh["points"][0]["value"] == 0.78
    assert (wh["points"][0]["min"], wh["points"][0]["max"]) == (0.78, 0.78)
    assert wh["decimals"] == 3


def test_largest_unit_wins() -> None:
    result = _run([_s("Wh", 2_000_000), _s("kWh", 5), _s("MWh", 1)])["out"]
    assert [s["unit"] for s in result] == ["MWh", "MWh", "MWh"]
    assert result[0]["points"][0]["value"] == 2.0
    assert result[1]["points"][0]["value"] == 0.005


def test_power_family_is_separate_from_energy() -> None:
    result = _run([_s("W", 500), _s("kW", 2), _s("kWh", 3)])["out"]
    assert [s["unit"] for s in result] == ["kW", "kW", "kWh"]
    assert result[0]["points"][0]["value"] == 0.5


def test_single_unit_per_family_is_untouched() -> None:
    result = _run([_s("Wh", 780), _s("Wh", 100), _s("°C", 20)])
    assert result["same"] is True


def test_compare_points_and_gaps_are_converted() -> None:
    wh = _s("Wh", 1000, None, compare_points=[{"ts": 1, "value": 500, "min": None, "max": None}])
    wh["points"].append({"ts": 2, "value": None, "min": None, "max": None})
    result = _run([_s("kWh", 1), wh])["out"][1]
    assert result["compare_points"][0]["value"] == 0.5
    assert result["compare_points"][0]["min"] is None
    assert result["points"][1]["value"] is None
    assert result["decimals"] is None
