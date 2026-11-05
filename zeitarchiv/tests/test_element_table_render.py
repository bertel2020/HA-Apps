"""element-table-render.js (Roadmap 1.48, P0): die Tabelle ohne Seite und ohne Alpine.

Das Modul läuft hier in einer nachgebauten Umgebung, wie sie die Lovelace-Karte baut: nur die Globals, die
table-tile-render.js auch braucht (t, escapeHtml, NumberFormat, ServerTime) und window.ZA_FORMAT. Läuft die
Tabelle so, hängt sie an keinem Seitenzustand und die Karte kann sie ohne Umbau einsetzen.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _paths import ADDON

JS = ADDON / "app" / "static" / "js"

SCRIPT = r"""
const fs = require('fs');
const D = process.argv[1];
const read = (n) => fs.readFileSync(D + '/' + n, 'utf8');
const win = {ZA_FORMAT: 'de'};
const doc = {documentElement: {dataset: {tz: 'Europe/Berlin', format: 'de'}, lang: 'de'}, body: {}};
const t = (value, values) => (values ? value.replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m)) : value);
const run = (file, names, values) => new Function(...names, read(file))(...values);
run('number-format.js', ['window', 'document'], [win, doc]);
run('server-time.js', ['window', 'document'], [win, doc]);
run('html-escape.js', ['window'], [win]);
run('element-table-render.js', ['window', 't', 'escapeHtml', 'NumberFormat', 'ServerTime'],
  [win, t, win.escapeHtml, win.NumberFormat, win.ServerTime]);
const R = win.ElementTableRender;
const span = (start, end) => ({start, end});
const data = {
  vorjahr: true,
  columns: [{key: 'monat', cur: span(1764547200, 1767225600), vj: span(1733011200, 1735689600)}],
  rows: [
    {group: 'Erzeugung', label: 'PV', kind: 'energy', better: 'up', cells: {monat: {cur: 1200, vj: 1000}}},
    {group: 'Erzeugung', label: 'Autarkie', kind: 'pct', better: 'up', cells: {monat: {cur: 60.5, vj: 70}}},
    {group: 'Verbrauch', label: 'Netzbezug', kind: 'energy', better: 'down', cells: {monat: {cur: null, vj: 5, cur_from: 1764547200}}},
  ],
};
console.log(JSON.stringify({
  withDiff: R.energyComparison(data, true),
  noDiff: R.energyComparison(data, false),
  empty: R.energyComparison({columns: [], rows: [], vorjahr: false}, true),
  energy: [R.energy(5), R.energy(1234.5), R.energy(12500)],
}));
"""


@pytest.fixture(scope="module")
def output() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node fehlt")
    run = subprocess.run(["node", "-e", SCRIPT, str(JS)], check=True, capture_output=True, text=True)
    return json.loads(run.stdout)


def test_the_comparison_table_is_built_without_the_page(output: dict) -> None:
    html = output["withDiff"]
    assert html.startswith("<thead>") and html.endswith("</tbody>")
    assert "Vorjahr" in html and "Erzeugung" in html and "Verbrauch" in html
    # mehr Erzeugung ist günstig, weniger Autarkie ungünstig; der fehlende Wert bekommt eine leere Zelle
    assert "wt-dl-good" in html and "wt-dl-bad" in html
    assert "wt-na" in html


def test_the_difference_row_can_be_switched_off(output: dict) -> None:
    assert "wt-dl" not in output["noDiff"]


def test_an_empty_selection_gives_a_hint_not_a_broken_table(output: dict) -> None:
    assert "Keine Spalte ausgewählt." in output["empty"]


def test_energy_switches_to_mwh_from_ten_thousand_kwh(output: dict) -> None:
    small, mid, big = output["energy"]
    assert small.endswith("kWh") and mid.endswith("kWh") and big.endswith("MWh")
