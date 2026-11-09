"""Weitere Sensoren des Wetterdashboards nach Raum (Roadmap 1.51): Zuordnung, Speicherung und Tabelle."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from _paths import ADDON
from app import wetter_rooms
from app.storage.index import Index
from app.wetter_routes import WetterDependencies, WetterService

JS = ADDON / "app" / "static" / "js"
TZ = ZoneInfo("Europe/Berlin")


def test_room_by_hand_beats_the_area_and_both_may_be_missing() -> None:
    known = {"sensor.a": {"area": "Bad", "floor": "Obergeschoss", "device_class": "temperature"}}
    assert wetter_rooms.room_of({"entity_id": "sensor.a", "room": " Dusche "}, known) == "Dusche"
    assert wetter_rooms.room_of({"entity_id": "sensor.a"}, known) == "Bad"
    assert wetter_rooms.room_of({"entity_id": "sensor.b"}, known) == ""


def test_floor_belongs_to_the_area_not_to_a_room_typed_by_hand() -> None:
    known = {"sensor.a": {"area": "Bad", "floor": "Obergeschoss", "device_class": None}}
    assert wetter_rooms.floor_of({"entity_id": "sensor.a"}, known) == "Obergeschoss"
    assert wetter_rooms.floor_of({"entity_id": "sensor.a", "room": "Bad"}, known) == "Obergeschoss"
    assert wetter_rooms.floor_of({"entity_id": "sensor.a", "room": "Dusche"}, known) == ""
    assert wetter_rooms.floor_of({"entity_id": "sensor.zz"}, known) == ""


def test_entry_keeps_old_settings_unchanged_without_a_room() -> None:
    assert wetter_rooms.entry("sensor.a", "Nord") == {"entity_id": "sensor.a", "name": "Nord"}
    assert wetter_rooms.entry("sensor.a", "", "  Bad ") == {"entity_id": "sensor.a", "name": "", "room": "Bad"}
    assert len(wetter_rooms.entry("sensor.a", "", "x" * 200)["room"]) == wetter_rooms.MAX_ROOM_LENGTH


@pytest.mark.parametrize(("unit", "device_class", "expected"), [
    ("°C", None, True), ("°F", "temperature", True), ("%", "humidity", True), ("%", None, True),
    ("%", "battery", False), ("W", None, False), ("lx", "illuminance", False), (None, None, False),
])
def test_only_temperature_and_humidity_qualify(unit, device_class, expected) -> None:
    assert wetter_rooms.is_climate_sensor(unit, device_class) is expected


@pytest.fixture()
def service():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rooms-test-"))
    index = Index(tmp / "index.sqlite")
    yield WetterService(WetterDependencies(
        data_dir=tmp, index=index, tz=TZ, templates=None, app_root_context=lambda request: {},
    ))
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_row_specs_carry_the_room_but_keep_keys_and_order(service) -> None:
    from test_wetter import _add, _configure  # gleiche Hilfen wie die Wetter-Tests

    now = datetime(2026, 8, 20, 12, 0, tzinfo=TZ).timestamp()
    for entity_id in ("sensor.t", "sensor.bad_t", "sensor.bad_h", "sensor.kueche_t"):
        _add(service, entity_id, "sensor", "measurement", "%" if entity_id.endswith("_h") else "°C", [(now, 20.0)])
    (service.deps.data_dir / "selection_catalog.json").write_text(json.dumps({"entities": [
        {"entity_id": "sensor.kueche_t", "area": "Küche", "floor": "Erdgeschoss", "device_class": "temperature"},
    ]}), encoding="utf-8")
    _configure(service, temperature="sensor.t", extra_temperatures=[
        {"entity_id": "sensor.bad_t", "name": "Bad Temperatur", "room": "Bad"},
        {"entity_id": "sensor.kueche_t", "name": ""},
        {"entity_id": "sensor.bad_h", "name": "Bad Feuchte", "room": "Bad"},
    ])
    from app import wetter_routes

    specs = service._row_specs(wetter_routes._load_config(service.deps.index))
    extras = [(s["key"], s["room"], s["floor"]) for s in specs if s["key"].startswith("temperature:")]
    assert extras == [("temperature:0", "Bad", ""), ("temperature:1", "Küche", "Erdgeschoss"), ("temperature:2", "Bad", "")]
    assert "room" not in next(s for s in specs if s["key"] == "temperature")


def test_comparison_series_carry_room_and_floor_for_the_chart_selection(service) -> None:
    from test_wetter import NOW, _add, _configure

    for entity_id, unit in (("sensor.t", "°C"), ("sensor.bad_t", "°C"), ("sensor.bad_h", "%")):
        _add(service, entity_id, "sensor", "measurement", unit, [(NOW.timestamp() - 3600, 20.0)])
    (service.deps.data_dir / "selection_catalog.json").write_text(json.dumps({"entities": [
        {"entity_id": "sensor.bad_t", "area": "Bad", "floor": "Obergeschoss"},
        {"entity_id": "sensor.bad_h", "area": "Bad", "floor": "Obergeschoss"},
    ]}), encoding="utf-8")
    _configure(service, temperature="sensor.t", extra_temperatures=[
        {"entity_id": "sensor.bad_t", "name": "Bad T"}, {"entity_id": "sensor.bad_h", "name": "Bad H"},
    ])
    data = service.compute_temperatures(3, NOW)
    assert [(row["key"], row["room"], row["floor"]) for row in data["series"]] == [
        ("temperature", "", ""), ("temperature:0", "Bad", "Obergeschoss")]
    assert [(row["key"], row["room"]) for row in data["humidity"]] == [("temperature:1", "Bad")]


def test_the_page_loads_the_room_helpers_before_the_dashboard_script() -> None:
    html = (ADDON / "app" / "templates" / "wetterdashboard.html").read_text(encoding="utf-8")
    assert html.index("wetter-rooms.js") < html.index("js/wetter.js")


def _node(expression: str):
    if shutil.which("node") is None:
        pytest.skip("node fehlt")
    script = f"const r = require({json.dumps(str(JS / 'wetter-rooms.js'))}); console.log(JSON.stringify({expression}));"
    return json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)


CMP = (
    '{series: [{key: "temperature", room: "", floor: ""}, {key: "temperature:0", room: "Bad", floor: "OG"},'
    ' {key: "temperature:1", room: "", floor: ""}, {key: "temperature:2", room: "Keller", floor: "UG"}],'
    ' humidity: [{key: "humidity"}, {key: "temperature:3", room: "Bad", floor: "OG"}, {key: "temperature:4", room: "Küche", floor: "EG"}]}'
)


def test_rooms_come_in_order_of_entries_with_unassigned_last() -> None:
    assert _node(f"r.roomsOf({CMP})") == ["Bad", "Keller", "Küche", ""]
    assert _node("r.roomsOf({series: [{key: 'temperature'}], humidity: []})") == []


def test_floors_group_their_rooms_and_unassigned_come_last() -> None:
    assert _node(f"r.floorsOf({CMP})") == [
        {"floor": "OG", "rooms": ["Bad"]}, {"floor": "UG", "rooms": ["Keller"]},
        {"floor": "EG", "rooms": ["Küche"]}, {"floor": "", "rooms": [""]},
    ]


def test_hidden_rooms_drop_their_lines_but_never_the_outdoor_line_and_colors_stay() -> None:
    kept = _node(f'r.visible({CMP}.series, ["Bad", ""])')
    assert [(row["key"], row["_i"]) for row in kept] == [("temperature", 0), ("temperature:2", 3)]
    assert [row["key"] for row in _node(f"r.visible({CMP}.humidity, [\"Bad\"])")] == ["humidity", "temperature:4"]
    assert len(_node(f"r.visible({CMP}.series, [])")) == 4


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
const cell = (v) => ({cur: {min: v - 1, max: v + 1, avg: v}, vj: {min: v - 2, max: v + 2, avg: v - 0.5}});
const row = (key, label, unit, room, v) => ({key, label, kind: 'standard', unit, decimals: 1, room, cells: {heute: cell(v)}});
const base = {
  vorjahr: true, now: 1764547200, at: null,
  columns: [{key: 'heute', range: 'day', cur: span(1764547200, 1764633600), vj: span(1733011200, 1733097600)}],
};
const rooms = {...base, rows: [
  row('temperature:0', 'Bad Temperatur', '°C', 'Bad', 23),
  row('temperature:1', 'Dachboden', '°C', '', 9),
  row('temperature:2', 'Bad Feuchte', '%', 'Bad', 66),
  row('temperature:3', 'Keller Sensor', '°C', 'Keller', 14),
  row('temperature:4', 'Keller Zweitsensor', '°C', 'Keller', 15),
]};
const flat = {...base, rows: rooms.rows.map((r) => ({...r, room: ''}))};
const count = (html, needle) => html.split(needle).length - 1;
const heads = (html) => [...html.matchAll(/<tr class="wt-grp"><th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);
const subs = (html) => [...html.matchAll(/<tr class="wt-sub"><th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);
const labels = (html) => [...html.matchAll(/<th scope="row">([^<]*)/g)].map((m) => m[1]);
const grouped = R.weatherComparison(rooms, 'klima', true);
const averages = R.weatherComparison(rooms, 'klima', true, {avgOnly: true});
const unchanged = R.weatherComparison(flat, 'klima', true);
const flatAvg = R.weatherComparison(flat, 'klima', true, {avgOnly: true});
console.log(JSON.stringify({
  groupedHeads: heads(grouped), groupedSubs: subs(grouped), groupedRows: count(grouped, '<th scope="row">'),
  avgHeads: heads(averages), avgLabels: labels(averages), avgSubs: subs(averages),
  flatHeads: heads(unchanged), flatSubs: subs(unchanged), flatRows: count(unchanged, '<th scope="row">'),
  flatAvgLabels: labels(flatAvg),
  basisUntouched: R.weatherComparison({...rooms, rows: rooms.rows.map((r) => ({...r, key: 'temperature'}))}, 'basis', true, {avgOnly: true}).includes('Max'),
}));
"""


@pytest.fixture(scope="module")
def output() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node fehlt")
    done = subprocess.run(["node", "-e", SCRIPT, str(JS)], check=True, capture_output=True, text=True)
    return json.loads(done.stdout)


def test_table_groups_sensors_by_room_in_order_of_entries_with_unassigned_last(output) -> None:
    assert output["groupedHeads"] == ["Bad", "Keller", "Ohne Raum"]
    # Bad: Temperatur und Feuchte; Keller: zwei Sensoren gleicher Art tragen ihren Namen; Ohne Raum: Name des Sensors
    assert output["groupedSubs"] == ["Temperatur", "Feuchte", "Keller Sensor", "Keller Zweitsensor", "Dachboden"]
    assert output["groupedRows"] == 5 * 3


def test_average_only_gives_one_row_per_sensor(output) -> None:
    assert output["avgHeads"] == ["Bad", "Keller", "Ohne Raum"]
    assert output["avgSubs"] == []
    assert output["avgLabels"] == ["Temperatur Ø", "Feuchte Ø", "Keller Sensor Ø", "Keller Zweitsensor Ø", "Dachboden Ø"]


def test_without_rooms_the_table_stays_one_block_per_sensor(output) -> None:
    assert output["flatHeads"] == ["Bad Temperatur", "Dachboden", "Bad Feuchte", "Keller Sensor", "Keller Zweitsensor"]
    assert output["flatSubs"] == [] and output["flatRows"] == 5 * 3
    assert output["flatAvgLabels"] == ["Ø"] * 5
    assert output["basisUntouched"] is True
