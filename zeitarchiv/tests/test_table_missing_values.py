"""Fehlende Werte in Tabellen: kein Zeitraum ohne Daten wird als 0 weitergerechnet."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.api_routes import _table_aggregates

from _paths import APP

COMPUTE = APP / "static/js/table-compute.js"

SCRIPT = r"""
const fs = require('fs');
global.window = {NumberFormat: {fmt: (v, d) => String(v), DECIMAL_SEP: '.'}};
global.t = s => s;
global.document = {documentElement: {lang: 'de'}};
require('vm').runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const TC = window.TableCompute;
const rows = [
  {row_type: 'entity', entity_ids: ['sensor.soc'], aggregation: 'auto'},
  {row_type: 'formula', formula: '((90.0 - A) / 100.0) * 10.26'},
];
const series = aggregates => ({entity_id: 'sensor.soc', unit: '%', aggregation_type: 'standard', aggregates});
const query = async () => ({columns: [
  {series: [series({auto: 40, avg: 40})]},
  {series: [series({auto: null, avg: null})]},
]});
(async () => {
  const res = await TC.computeValues('', [{range_key: 'day', offset: 0}, {range_key: 'day', offset: -1}], rows, {queryTable: query});
  console.log(JSON.stringify([res.values[0][1].value, res.values[1][0], res.values[1][1].value, !!res.values[1][1].error]));
})();
"""


def test_empty_period_is_none_for_tables() -> None:
    assert _table_aggregates({"aggregation_type": "standard", "points": []}, empty_is_none=True)["auto"] is None
    only_gaps = [{"ts": 1.0, "value": None, "min": None, "max": None}]
    result = _table_aggregates({"aggregation_type": "counter", "points": only_gaps}, empty_is_none=True)
    assert result["sum"] is None and result["avg"] is None
    zero = [{"ts": 1.0, "value": 0.0, "min": None, "max": None}]
    assert _table_aggregates({"aggregation_type": "counter", "points": zero}, empty_is_none=True)["sum"] == 0.0
    # Standardverhalten (Kacheln, Energiedashboard) bleibt unverändert.
    assert _table_aggregates({"aggregation_type": "standard", "points": []})["auto"] == 0.0


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_missing_value_is_not_calculated_as_zero() -> None:
    out = subprocess.run(["node", "-e", SCRIPT, str(COMPUTE)], capture_output=True, text=True, check=True)
    formula_ok, missing_cell, formula_missing, formula_error = json.loads(out.stdout)
    assert formula_ok == pytest.approx(5.13)
    assert missing_cell is None
    assert formula_missing is None
    assert formula_error is False
