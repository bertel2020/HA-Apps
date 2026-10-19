"""Zahlen in Tabellenformeln: ein ungültiges Zahlentoken ist ein Formelfehler.

parseFloat() nahm nur den gültigen Anfang — "1.2.3" ergab 1,2 und "2..5"
ergab 2, also plausibel wirkende, falsche Werte in Vergleichstabellen.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from _paths import APP_JS


SCRIPT = APP_JS / "table-compute.js"


_RUNNER = r"""
const [script, formula, sep] = process.argv.slice(1);
const vm = require('vm');
const ctx = {
  window: {NumberFormat: {DECIMAL_SEP: sep}},
  document: {documentElement: {lang: 'de', dataset: {}}},
  t: (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => v[k]),
};
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(script, 'utf8'), ctx);
let out;
try { out = {value: ctx.window.TableCompute.evalFormula(formula, {})}; }
catch (e) { out = {error: e.message}; }
console.log(JSON.stringify(out));
"""


def _eval(formula: str, decimal_sep: str) -> dict:
    result = subprocess.run(
        ["node", "-e", _RUNNER, str(SCRIPT), formula, decimal_sep],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("formula", "sep"),
    [("1.2.3", ","), ("2..5", ","), ("1,2,3", ","), ("1,2.3", ","), ("1.2.3", ".")],
)
def test_malformed_numbers_are_formula_errors(formula, sep) -> None:
    result = _eval(formula, sep)
    assert "error" in result, result
    assert "Ungültige Zahl" in result["error"]


@pytest.mark.parametrize(
    ("formula", "sep", "expected"),
    [
        ("1,5", ",", 1.5),
        ("1.5", ",", 1.5),
        ("1.5", ".", 1.5),
        ("42", ",", 42),
        (",5 * 2", ",", 1.0),
        ("5, + 1", ",", 6),
    ],
)
def test_valid_numbers_keep_working(formula, sep, expected) -> None:
    assert _eval(formula, sep) == {"value": expected}
