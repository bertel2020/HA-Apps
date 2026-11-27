"""Tabellenzeile „Σ Einschaltungen“ (count_sum) für Schalter (ROADMAP 1.74)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.main import _TABLE_ROW_AGGREGATIONS

from _paths import APP

COMPUTE = APP / "static/js/table-compute.js"

SCRIPT = r"""
const fs = require('fs');
global.window = {NumberFormat: {fmt: (v, d) => d == null ? String(v) : v.toFixed(d), DECIMAL_SEP: '.'}};
global.t = s => s;
global.document = {documentElement: {lang: 'de'}};
require('vm').runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const TC = window.TableCompute;
const sw = (id, aggregates) => ({entity_id: id, unit: '', decimals: null, aggregation_type: 'switch', aggregates});
const sensor = {entity_id: 'sensor.t', unit: '°C', decimals: 1, aggregation_type: 'standard', aggregates: {avg: 20, auto: 20}};
const run = async (rows, series) => {
  const query = async () => ({columns: [{series}]});
  return TC.computeValues('', [{range_key: 'day', offset: 0}], rows, {queryTable: query});
};
(async () => {
  const one = await run([{row_type: 'entity', entity_ids: ['switch.a'], aggregation: 'count_sum'}],
    [sw('switch.a', {auto: 5400, sum: 5400, count_sum: 7})]);
  const cell = one.values[0][0];
  const group = await run([{row_type: 'group', entity_ids: ['switch.a', 'switch.b'], aggregation: 'count_sum'}],
    [sw('switch.a', {count_sum: 7}), sw('switch.b', {count_sum: 3})]);
  const unknown = await run([{row_type: 'entity', entity_ids: ['switch.a'], aggregation: 'count_sum'}],
    [sw('switch.a', {auto: 5400, count_sum: null})]);
  const other = await run([{row_type: 'entity', entity_ids: ['sensor.t'], aggregation: 'count_sum'}], [sensor]);
  console.log(JSON.stringify({
    text: [TC.cellText(cell, 'auto'), cell.value, cell.unit],
    group: group.values[0][0].value,
    unknown: unknown.values[0][0],
    other: other.values[0][0],
  }));
})();
"""


def test_count_sum_is_a_valid_row_aggregation() -> None:
    assert "count_sum" in _TABLE_ROW_AGGREGATIONS


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_the_cell_shows_the_number_of_switch_ons_with_its_own_unit() -> None:
    out = subprocess.run(["node", "-e", SCRIPT, str(COMPUTE)], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    assert result["text"] == ["7 Einschaltungen", 7, "Einschaltungen"]  # ganze Zahl mit Einheit, nicht die 5400 Sekunden
    assert result["group"] == 10  # eine Gruppe addiert ihre Mitglieder
    assert result["unknown"] is None  # nicht erfasst: leere Zelle, nicht 0
    assert result["other"] is None  # kein Schalter: leere Zelle, nicht der Durchschnitt der Punkte
