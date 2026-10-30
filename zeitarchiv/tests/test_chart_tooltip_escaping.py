"""Tooltips der Diagramme: Namen und Einheiten aus der App werden maskiert.

ECharts setzt den Text eines Tooltips als HTML ein, und zwar im body der Seite, also auch in Home Assistant
(Lovelace-Karte), nicht nur in der Oberfläche der App. Ein Anzeigename oder eine Einheit wie
`<img src=x onerror=…>` darf dort nie als Markup ankommen. Der Test führt die echten Formatter von
chart-tile-render.js mit solchen Werten aus (node) und sucht im Ergebnis nach rohen Tags.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from _paths import APP

SCRIPT = r"""
const fs = require('fs');
const D = process.argv[1];
const src = n => fs.readFileSync(D + '/' + n, 'utf8');
const win = {ZA_FORMAT: 'de'};
const doc = {documentElement: {dataset: {tz: 'Europe/Berlin', format: 'de'}, lang: 'de'}, body: {}, createElement: () => ({getContext: () => new Proxy({}, {get: (o, k) => (k in o ? o[k] : () => ({addColorStop() {}})), set: (o, k, v) => { o[k] = v; return true; }})})};
const gcs = () => ({getPropertyValue: n => (/^--chart-(\d)$/.test(n) ? '#009AC7' : '')});
const t = (v, vals) => (vals ? v.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? vals[k] : m)) : v);
const ev = (s, names, vals) => new Function(...names, s)(...vals);
ev(src('number-format.js'), ['window', 'document'], [win, doc]);
ev(src('tooltip-hold.js'), ['window', 'document'], [win, doc]);
ev(src('server-time.js'), ['window', 'document'], [win, doc]);
ev(src('html-escape.js'), ['window'], [win]);
ev(src('state-entities.js'), ['window', 'document', 'getComputedStyle', 't', 'NumberFormat', 'escapeHtml'], [win, doc, gcs, t, win.NumberFormat, win.escapeHtml]);
const R = ev(src('chart-tile-render.js') + '\nreturn window.ChartTileRender;',
  ['window', 'document', 'getComputedStyle', 't', 'NumberFormat', 'TooltipHold', 'StateEntities', 'PeriodNavigation', 'echarts', 'escapeHtml'],
  [win, doc, gcs, t, win.NumberFormat, win.TooltipHold, win.StateEntities, {}, {graphic: {}}, win.escapeHtml]);
const EVIL = '<img src=x onerror=alert(1)>';
const UNIT = '<b onmouseover=alert(2)>W';
const style = {getPropertyValue: () => ''};
const spec = (chartType, series, over = {}) => R.readSpec({entityIds: JSON.stringify(series.map(s => s.entity_id)), entityNames: '{}', hiddenEntityIds: '[]',
  seriesColors: '{}', heatmapStates: '{}', compareSettings: '', periodAnchor: '', range: 'day', continuous: 'false', resolutionPreset: 'auto',
  dynamicYAxis: 'true', chartType, donutAggregation: 'sum', showValues: 'false', decimals: 'auto', averageLine: 'false', averageStyle: 'flat',
  areaFill: 'true', showPoints: 'false', stacked: 'false', normalize: 'false', horizontal: 'false', animation: 'false', ...over});
const draw = (sp, series, extra = {}) => {
  const data = {series, window_start: 1767222000, window_end: 1767308400, period_end: 1767308400, ...extra};
  let opt;
  const chart = {setOption: o => { opt = o; }, getOption: () => ({legend: [{selected: {}}]}), getWidth: () => 600, getHeight: () => 260, on() {}, off() {}, clear() {}};
  const prepared = R.prepareSeries(data);
  R.draw(chart, {spec: sp, data, series: prepared, viewType: R.viewTypeFor(sp, prepared), multiYear: null, hiddenSet: undefined, style});
  return opt;
};
const out = {};
const numeric = (extra) => ({entity_id: 'sensor.a', friendly_name: EVIL, unit: UNIT, decimals: 1, aggregation_type: 'standard', chart_type: 'line', points: [{ts: 1767225600, value: 5}, {ts: 1767229200, value: 6}], ...extra});
out.line = draw(spec('auto', [numeric()]), [numeric()]).tooltip.formatter([
  {seriesIndex: 0, seriesName: EVIL, marker: '', axisValue: 1767225600000, data: [1767225600000, 5, UNIT, 1, false], value: [1767225600000, 5]}]);
out.normalized = draw(spec('auto', [numeric(), numeric({entity_id: 'sensor.b'})], {stacked: 'true', normalize: 'true'}), [numeric({chart_type: 'bar'}), numeric({entity_id: 'sensor.b', chart_type: 'bar'})]).tooltip.formatter([
  {seriesIndex: 0, seriesName: EVIL, marker: '', axisValue: 1767225600000, data: [1767225600000, 50, '%', 0, false, 5, UNIT, 1], value: [1767225600000, 50]}]);
const counters = [numeric({aggregation_type: 'counter', chart_type: 'bar', points: [{ts: 1767225600, value: 5}]}), numeric({entity_id: 'sensor.b', friendly_name: 'ok', aggregation_type: 'counter', chart_type: 'bar', points: [{ts: 1767225600, value: 7}]})];
out.donut = draw(spec('donut', counters), counters).tooltip.formatter({dataIndex: 0, marker: '', name: EVIL, value: 5, percent: 41});
const sw = [{entity_id: 'binary_sensor.x', friendly_name: EVIL, unit: '', decimals: 0, aggregation_type: 'switch', chart_type: 'line', points: [{ts: 1767225600, value: 1}, {ts: 1767229200, value: 0}]}];
out.timeline = draw(spec('timeline', sw), sw).tooltip.formatter({value: [0, 1767225600000, 1767229200000, 0, 1, -1]});
// Vorjahres-Balkenpaare (Monatsansicht): Seriennamen enthalten den Anzeigenamen.
const year = [numeric({aggregation_type: 'counter', chart_type: 'bar', points: [{ts: 1767225600, value: 5}], compare_points: [{ts: 1735689600, value: 4}], compare_window_start: 1735689600})];
const yo = draw(spec('auto', year, {range: 'year', compareSettings: JSON.stringify({compare: true, mode: 'previous', cumsum: true})}), year, {is_current: true});
out.yearsCompare = yo.tooltip.formatter([{seriesIndex: 0, seriesName: EVIL + ' (2026)', marker: '', axisValue: 'Jan', value: 5, data: 5}]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def tooltips() -> dict[str, str]:
    run = subprocess.run(["node", "-e", SCRIPT, str(APP / "static" / "js")], check=True, capture_output=True, text=True)
    return json.loads(run.stdout)


@pytest.mark.parametrize("view", ["line", "normalized", "donut", "timeline", "yearsCompare"])
def test_a_tooltip_never_contains_markup_from_a_name_or_unit(tooltips: dict[str, str], view: str) -> None:
    html = tooltips[view]
    assert html, f"{view}: kein Tooltip"
    # Maskiert (&lt;img onerror=…&gt;) ist Text und in Ordnung; entscheidend ist, dass kein Tag mehr entsteht.
    for raw in ("<img", "<b onmouseover", "<svg"):
        assert raw not in html, f"{view}: {raw} steht roh im Tooltip: {html}"
    assert "&lt;" in html, f"{view}: der Name sollte als Text erscheinen: {html}"
