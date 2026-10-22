"""Pixelverdichtung des Zustands-Zeitstrahls (ROADMAP 1.29).

Über lange Zeiträume zeichnet der Zeitstrahl Spalten mit den Anteilen der
Zustände statt einzelner Rechtecke. Geprüft wird die Rechnung in
state-entities.js (condenseSegments, shouldCondense, stackParts), nicht das
Zeichnen: jede Spalte muss genau ihre Breite an Zeit enthalten, ein Abschnitt
über Spaltengrenzen wird dort geteilt, ein Beginn zählt nur einmal.
"""

from __future__ import annotations

import json
import subprocess

from _paths import APP_JS

SCRIPT = APP_JS / "state-entities.js"

_RUNNER = r"""
const [script, code] = process.argv.slice(1);
const vm = require('vm');
const ctx = {window: {}, t: s => s};
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(script, 'utf8'), ctx);
const SE = ctx.window.StateEntities;
console.log(JSON.stringify(new Function('SE', code)(SE)));
"""


def _run(code: str):
    result = subprocess.run(
        ["node", "-e", _RUNNER, str(SCRIPT), code], check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _seg(s: float, e: float, idx: int) -> str:
    return f"{{s:{s},e:{e},idx:{idx},key:{s}}}"


def test_every_column_holds_exactly_its_width() -> None:
    # Zehn Spalten à 10 s über [0, 100): ein Abschnitt über Spaltengrenzen wird geteilt.
    out = _run(f"""
      const segs = [{_seg(0, 25, 0)}, {_seg(25, 100, 1)}];
      const {{items, dt}} = SE.condenseSegments(segs, 0, 100, 10);
      return {{dt, items}};
    """)
    assert out["dt"] == 10
    covered = sum(sum(p[1] for p in it["parts"]) for it in out["items"])
    assert covered == 100
    # Spalte 2 (20 bis 30 s) ist gemischt: 5 s Zustand 0, 5 s Zustand 1.
    mixed = [it for it in out["items"] if len(it["parts"]) == 2]
    assert len(mixed) == 1
    assert mixed[0]["s"] == 20 and mixed[0]["e"] == 30
    assert mixed[0]["parts"] == [[0, 5, 0], [1, 5, 1]]


def test_begins_count_once_where_a_segment_starts() -> None:
    out = _run(f"""
      const segs = [{_seg(0, 100, 0)}];
      return SE.condenseSegments(segs, 0, 100, 10).items;
    """)
    # Ein einziger Zustand über alle Spalten: zu einem Streifen zusammengefasst, ein Beginn.
    assert len(out) == 1
    assert out[0]["parts"] == [[0, 100, 1]]
    assert out[0]["s"] == 0 and out[0]["e"] == 100


def test_segment_started_before_the_window_is_not_a_begin() -> None:
    out = _run(f"""
      const segs = [{_seg(0, 50, 0)}, {_seg(50, 100, 1)}];
      return SE.condenseSegments(segs, 30, 100, 7).items;
    """)
    first = out[0]["parts"][0]
    assert first[0] == 0
    assert first[2] == 0  # beginnt vor dem Ausschnitt


def test_hidden_states_are_left_out_and_shares_redistribute() -> None:
    out = _run(f"""
      const segs = [{_seg(0, 5, 0)}, {_seg(5, 7, 1)}, {_seg(7, 10, 2)}];
      return SE.condenseSegments(segs, 0, 10, 1, [1]).items;
    """)
    assert out[0]["parts"] == [[0, 5, 1], [2, 3, 1]]


def test_unavailable_is_sorted_last() -> None:
    out = _run(f"""
      const segs = [{_seg(0, 4, -1)}, {_seg(4, 10, 3)}];
      return SE.condenseSegments(segs, 0, 10, 1).items;
    """)
    assert [p[0] for p in out[0]["parts"]] == [3, -1]


def test_condense_only_when_more_segments_than_pixels() -> None:
    out = _run("""
      const segs = [];
      for (let i = 0; i < 100; i++) segs.push({s: i, e: i + 1, idx: i % 2, key: i});
      return [
        SE.shouldCondense(segs, 0, 100, 100),
        SE.shouldCondense(segs, 0, 100, 99),
        SE.shouldCondense(segs, 0, 50, 60),
        SE.shouldCondense(segs, 0, 100, 99, [1]),
      ];
    """)
    # 100 Abschnitte auf 100 Bildpunkte: roh. Auf 99: verdichtet. Halbes Fenster: roh.
    # Mit einem ausgeblendeten Zustand zählen nur die 50 sichtbaren.
    assert out == [False, True, False, False]


def test_condense_also_above_the_absolute_limit() -> None:
    out = _run("""
      const n = SE.CONDENSE_MAX_RECTS + 1;
      const segs = [];
      for (let i = 0; i < n; i++) segs.push({s: i, e: i + 1, idx: i % 2, key: i});
      return [SE.shouldCondense(segs, 0, n, n * 2), SE.shouldCondense(segs.slice(0, 10), 0, 10, 20000)];
    """)
    assert out == [True, False]


def test_stack_fills_the_band_and_keeps_one_percent_visible() -> None:
    out = _run("""
      // 80/20 verteilt sich auf ganze Bildpunkte; 3 % von 36 Bildpunkten sind 1,08.
      const plain = SE.stackParts([[0, 80, 1], [1, 20, 1]], 36);
      const thin = SE.stackParts([[0, 97, 1], [2, 3, 1]], 36);
      return {plain, thin};
    """)
    assert out["plain"] == [{"idx": 0, "y": 0, "h": 29}, {"idx": 1, "y": 29, "h": 7}]
    assert sum(layer["h"] for layer in out["thin"]) == 36
    # 3 % von 36 Bildpunkten sind 1,08: bleibt mindestens ein Bildpunkt hoch.
    assert out["thin"][1]["idx"] == 2 and out["thin"][1]["h"] >= 1


def test_server_columns_merge_into_strips_and_respect_hidden_states() -> None:
    out = _run("""
      // Vier Spalten à 100 s: drei mit demselben einzigen Zustand, eine gemischt.
      const columns = [
        [0, [[0, 100, 1]]],
        [100, [[0, 100, 0]]],
        [200, [[0, 60, 0], [1, 40, 1]]],
        [300, [[1, 100, 0]]],
      ];
      return {
        all: SE.mergeColumns(columns, 100),
        noOne: SE.mergeColumns(columns, 100, [1]),
      };
    """)
    # Die ersten beiden Spalten werden ein Streifen, Beginne und Sekunden summiert.
    assert out["all"][0] == {"s": 0, "e": 200, "parts": [[0, 200, 1]]}
    assert out["all"][1]["parts"] == [[0, 60, 0], [1, 40, 1]]
    assert out["all"][2]["parts"] == [[1, 100, 0]]
    # Ohne Zustand 1: die gemischte Spalte hat nur noch Zustand 0, und alle drei verschmelzen.
    assert out["noOne"] == [{"s": 0, "e": 300, "parts": [[0, 260, 1]]}]
