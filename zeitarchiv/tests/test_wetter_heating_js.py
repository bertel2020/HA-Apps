"""Rechenkern des Reiters Heizen (static/js/wetter-heating.js), in node ausgeführt."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _paths import APP

MODULE = APP / "static/js/wetter-heating.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


def _run(expression: str):
    script = f"const h = require({json.dumps(str(MODULE))}); console.log(JSON.stringify({expression}));"
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(done.stdout)


def test_periods_run_from_october_to_april() -> None:
    assert _run('[h.periodOf("2025-10-01"), h.periodOf("2026-04-30"), h.periodOf("2026-05-01"), h.periodOf("2026-01-15")]') == [2025, 2025, None, 2025]
    assert _run("h.label(2025)") == "2025/26" and _run("h.label(2099)") == "2099/00"
    assert _run('h.currentPeriod("2026-11-02")') == 2026 and _run('h.currentPeriod("2026-03-02")') == 2025


def test_gradtage_count_only_days_below_the_limit() -> None:
    assert _run("[h.gradtage(5, 20, 15), h.gradtage(15, 20, 15), h.gradtage(18, 20, 15), h.gradtage(-3, 20, 15)]") == [15, 0, 0, 23]


def test_stat_sums_selected_counters_and_divides_by_degree_days() -> None:
    days = '[["2025-10-01", 10, [4, 2]], ["2025-10-02", 5, [8, 4]], ["2025-10-03", 16, [1, 1]]]'
    both = _run(f'h.stat({days}, {{room: 20, limit: 15, on: [true, true]}}, "2025-10-01", "2025-10-31")')
    assert both == {"gt": 25, "kwh": 20, "n": 3, "q": 0.8}
    only_first = _run(f'h.stat({days}, {{room: 20, limit: 15, on: [true, false]}}, "2025-10-01", "2025-10-02")')
    assert only_first["kwh"] == 12 and only_first["n"] == 2
    assert _run('h.stat([["2025-10-03", 16, [1]]], {room: 20, limit: 15}, "2025-10-01", "2025-10-31")')["q"] is None


def test_ranges_and_the_cut_to_yesterday() -> None:
    assert _run("h.periodRange(2025)") == ["2025-10-01", "2026-04-30"]
    assert _run("h.monthRange(2025, 12)") == ["2025-12-01", "2025-12-31"] and _run("h.monthRange(2025, 1)") == ["2026-01-01", "2026-01-31"]
    cut = _run('h.cutRange(2025, "2026-11-02")')
    assert cut["to"] == "2025-11-01" and cut["label"] == {"day": 1, "month": 11}
    assert _run('h.cutRange(2024, "2026-11-02")')["to"] == "2024-11-01"
    assert _run('h.cutRange(2025, "2026-06-10")') == {"from": "2025-10-01", "to": "2026-04-30", "label": None}  # Sommer: ganze Periode
    assert _run('h.cutRange(2025, "2026-10-01")')["label"] is None  # gestern war im September


def test_fit_is_a_line_through_days_below_the_limit_only() -> None:
    rows = [[f"2025-12-{d:02d}", float(t), [20 - 1.5 * t]] for d, t in zip(range(1, 9), (-3, -1, 0, 2, 4, 6, 8, 10), strict=True)]
    rows.append(["2025-12-20", 18.0, [99]])  # über der Heizgrenze: gehört nicht zur Geraden
    result = _run(f'h.fit({json.dumps(rows)}, {{limit: 15}}, 2025)')
    assert result["slope"] == pytest.approx(-1.5) and result["intercept"] == pytest.approx(20) and result["n"] == 8
    assert result["r2"] == pytest.approx(1.0)
    assert _run(f'h.fit({json.dumps(rows[:3])}, {{limit: 15}}, 2025)') is None  # zu wenige Tage
    assert _run(f'h.fit({json.dumps(rows)}, {{limit: 15}}, 2024)') is None


def test_shares_group_by_month_and_counter() -> None:
    days = '[["2025-10-02", 5, [1, 2]], ["2025-10-03", 5, [3, 4]], ["2026-01-05", 0, [10, 0]], ["2026-07-01", 20, [9, 9]]]'
    totals = _run(f"h.shares({days}, [0, 1], 2025)")
    assert totals[0] == [4, 6] and totals[3] == [10, 0] and totals[1] == [0, 0]  # Oktober, Januar; Sommer zählt nicht


def test_periods_are_listed_newest_first() -> None:
    assert _run('h.periods([["2023-11-01", 1, [1]], ["2025-02-01", 1, [1]], ["2024-12-01", 1, [1]], ["2025-07-01", 1, [1]]])') == [2024, 2023]
