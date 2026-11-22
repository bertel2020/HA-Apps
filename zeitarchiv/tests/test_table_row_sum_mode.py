"""Zeilenschalter „In Σ-Spalte“ (sum_mode): Automatisch / Ja / Nein."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.main import _TABLE_ROW_SUM_MODES

from _paths import APP

COMPUTE = APP / "static/js/table-compute.js"

SCRIPT = r"""
const fs = require('fs');
global.window = {NumberFormat: {fmt: (v, d) => String(v), DECIMAL_SEP: '.'}};
global.t = s => s;
global.document = {documentElement: {lang: 'de'}};
require('vm').runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const TC = window.TableCompute;
const cell = (value, unit = '') => ({value, unit});
const cols = [{}, {}, {}];
// A: Messwert (SoC, Letzter Wert), B: Kosten, C: Formel auf A, D: Zähler
const base = [
  {row_type: 'entity', aggregation: 'last'},
  {row_type: 'entity', aggregation: 'last'},
  {row_type: 'formula', formula: '((90.0 - A) / 100.0) * 10'},
  {row_type: 'entity', aggregation: 'auto'},
];
const values = [
  [cell(40, '%'), cell(2.5, 'EUR'), cell(5, 'kWh'), cell(1, 'kWh')],
  [cell(50, '%'), cell(3.5, 'EUR'), cell(4, 'kWh'), cell(2, 'kWh')],
  [cell(60, '%'), cell(null, 'EUR'), cell(3, 'kWh'), cell(3, 'kWh')],
];
const summable = [false, false, false, true];
const run = modes => {
  const rows = base.map((r, i) => ({...r, sum_mode: modes[i]}));
  return TC.computeRowSums(cols, rows, values, summable).map(c => c && c.value);
};
console.log(JSON.stringify({
  auto: run(['auto', 'auto', 'auto', 'auto']),
  yes: run(['no', 'yes', 'yes', 'auto']),
  none: run(['auto', 'auto', 'auto', 'no']),
  legacy: TC.computeRowSums(cols, base, values, summable).map(c => c && c.value),
}));
"""


def test_sum_modes_are_validated() -> None:
    assert _TABLE_ROW_SUM_MODES == ("auto", "yes", "no")


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_sum_mode_controls_the_sum_column() -> None:
    out = subprocess.run(["node", "-e", SCRIPT, str(COMPUTE)], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    # Automatisch: wie bisher — Messwerte und Formel „–“, nur der Zähler summiert.
    assert result["auto"] == [None, None, None, 6]
    assert result["legacy"] == result["auto"]
    # Ja: Zellwerte addieren (fehlende Zelle zählt nicht); bei der Formel die Ergebnisse je Spalte, nicht die Formel auf Σ.
    assert result["yes"] == [None, 6, 12, 6]
    # Nein: nur diese Zeile bleibt leer.
    assert result["none"] == [None, None, None, None]
