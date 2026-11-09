"""Tabellen-Aggregation "Aktueller Wert" und Nachkommastellen der Entität bei Spalte "Auto"."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.api_routes import _table_aggregates
from app.main import _TABLE_ROW_AGGREGATIONS

from _paths import APP

COMPUTE = APP / "static/js/table-compute.js"

SCRIPT = r"""
const fs = require('fs');
global.window = {NumberFormat: {fmt: (v, d) => d == null ? String(v) : v.toFixed(d), DECIMAL_SEP: '.'}};
global.t = s => s;
global.document = {documentElement: {lang: 'de'}};
const vm = require('vm');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const TableCompute = window.TableCompute;
const series = (decimals, aggregates) => ({entity_id: 'sensor.a', unit: '%', decimals, aggregation_type: 'standard', aggregates});
const run = async (aggregation, decimals, columnDecimals) => {
  const rows = [{row_type: 'entity', entity_ids: ['sensor.a'], aggregation}];
  const query = async () => ({columns: [{series: [series(decimals, {last: 87, avg: 89.04, auto: 89.04})]}]});
  const res = await TableCompute.computeValues('', [{range_key: 'day', offset: 0}], rows, {queryTable: query});
  const cell = res.values[0][0];
  return TableCompute.cellText(cell, columnDecimals);
};
(async () => {
  console.log(JSON.stringify([
    await run('last', null, 'auto'),
    await run('avg', 0, 'auto'),
    await run('avg', 0, '1'),
    await run('avg', null, 'auto'),
  ]));
})();
"""


def test_last_is_the_last_point_with_a_value() -> None:
    points = [
        {"ts": 1.0, "value": 90.0, "min": None, "max": None},
        {"ts": 2.0, "value": 87.0, "min": None, "max": None},
        {"ts": 3.0, "value": None, "min": None, "max": None},
    ]
    assert _table_aggregates({"aggregation_type": "standard", "points": points})["last"] == 87.0
    assert _table_aggregates({"aggregation_type": "standard", "points": []})["last"] is None


def test_last_is_a_valid_row_aggregation() -> None:
    assert "last" in _TABLE_ROW_AGGREGATIONS


def test_editor_offers_last() -> None:
    editor_js = (APP / "static/js/pages/table_editor.js").read_text(encoding="utf-8")
    assert "['last', t('Aktueller Wert')]" in editor_js


def test_last_value_and_entity_decimals_in_the_browser() -> None:
    if shutil.which("node") is None:
        pytest.skip("node fehlt")
    out = subprocess.run(["node", "-e", SCRIPT, str(COMPUTE)], check=True, capture_output=True, text=True)
    last, entity_decimals, column_wins, standard = __import__("json").loads(out.stdout)
    assert last == "87 %"
    assert entity_decimals == "89 %"  # Spalte "Auto": Entität sagt 0 Stellen
    assert column_wins == "89.0 %"  # eigene Spalten-Einstellung hat Vorrang
    assert standard == "89.04 %"  # beides "Auto": bisherige Standardformatierung
