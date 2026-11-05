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


LONGTERM_SCRIPT = SCRIPT.split("const R = win.ElementTableRender;")[0] + r"""
run('element-kpi-render.js', ['window', 't', 'escapeHtml', 'NumberFormat'], [win, t, win.escapeHtml, win.NumberFormat]);
const R = win.ElementTableRender;
const field = (days, hit) => ({days, hit, days_ytd: days, hit_ytd: hit});
const kt = {
  available: ['fast_autark', 'sonnig'],
  groups: {fast_autark: 'eigenversorgung', sonnig: 'erzeugung'},
  thresholds: {autark: 90, sonnig: 4},
  years: [
    {year: 2026, fields: {fast_autark: field(300, 30), sonnig: field(300, 0)}},
    {year: 2025, fields: {fast_autark: field(365, 73), sonnig: field(0, 0)}},
  ],
};
const changes = {
  cur: {start: 1767225600, end: 1772668800}, vj: {start: 1735689600, end: 1741132800},
  items: [
    {name: 'Wärmepumpe <b>', entity_id: 'sensor.wp', diff: 120, percent: 12.4},
    {name: 'Herd', entity_id: null, diff: -30, percent: null},
  ],
};
console.log(JSON.stringify({
  plain: R.energyKenntage(kt, {ytd: false, years: 0, current: 2026, interactive: false}),
  button: R.energyKenntage(kt, {ytd: false, years: 1, current: 2026, interactive: true}),
  linked: R.energyChanges(changes, {links: true}),
  unlinked: R.energyChanges(changes, {links: false}),
  range: R.changesRange(changes),
  records: win.ElementKpiRender.energyRecords({enough: true, unit: 'kWh', records: [{key: 'erzeugung', label: 'Stärkster Erzeugungstag', value: 61.234, date: '2026-06-14'}]}),
  notYet: win.ElementKpiRender.energyRecords({enough: false, records: []}),
}));
"""


@pytest.fixture(scope="module")
def longterm() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node fehlt")
    run = subprocess.run(["node", "-e", LONGTERM_SCRIPT, str(JS)], check=True, capture_output=True, text=True)
    return json.loads(run.stdout)


def test_the_key_days_card_draws_plain_rows_and_the_dashboard_draws_buttons(longterm: dict) -> None:
    plain, button = longterm["plain"], longterm["button"]
    assert "<button" not in plain and 'class="wt-kt-row"' in plain and "data-field" not in plain
    assert "Eigenversorgung" in plain and "Fast autark" in plain and "Autarkie ab 90 %" in plain
    assert "2026*" in plain and "<b>30</b> Tage · 10 %" in plain
    # ein Jahr ohne ausgewertete Tage hat keine Zeile; 0 Jahre heißt alle
    assert plain.count("wt-kt-row") == 3 and "<b>0</b>" in plain
    assert '<button type="button" class="wt-kt-row" data-field="fast_autark" data-year="2026"' in button
    assert "2025" not in button  # years=1 zeigt nur das neueste Jahr


def test_the_changes_link_the_consumers_only_in_the_app_and_escape_names(longterm: dict) -> None:
    assert '<a href="entities/sensor.wp">Wärmepumpe &lt;b&gt;</a>' in longterm["linked"]
    assert "<a " not in longterm["unlinked"] and "Wärmepumpe &lt;b&gt;" in longterm["unlinked"]
    assert "var(--danger)" in longterm["linked"] and "var(--positive)" in longterm["linked"]
    assert "▲ +120\u00a0kWh (+12 %)" in longterm["linked"] and "▼ −30" in longterm["linked"]
    assert " gegen " in longterm["range"]


def test_the_records_come_as_tiles_and_wait_for_a_year_of_data(longterm: dict) -> None:
    assert "Stärkster Erzeugungstag" in longterm["records"] and "61,2" in longterm["records"] and "14.06.2026" in longterm["records"]
    assert "kpi-empty" in longterm["notYet"] and "kpi-tile" not in longterm["notYet"]
