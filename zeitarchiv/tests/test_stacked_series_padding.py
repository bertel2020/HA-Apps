"""padStackedSeries(): gestapelte Balken-Serien mit unterschiedlichem Beginn.

ECharts stapelt auf einer Zeit-Achse nach Index in den Daten, nicht nach
Zeitstempel — beginnt eine Serie später als die andere, würde sie sonst auf
den falschen Bucket der ersten gestapelt. Die Funktion steht (gleichlautend)
in chart_editor.js und dashboard-tiles.js und wird hier ohne ECharts per
node ausgeführt.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from _paths import APP

FILES = ["static/js/pages/chart_editor.js", "static/js/dashboard-tiles.js"]


def _pad(path: str, series: list[dict]) -> list[dict]:
    source = (APP / path).read_text(encoding="utf-8")
    start = source.index("function padStackedSeries")
    end = source.index("function resamplePoints")
    code = (
        source[start:end]
        + f"; const s = {json.dumps(series)}; padStackedSeries(s); console.log(JSON.stringify(s));"
    )
    result = subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("path", FILES)
def test_later_starting_series_is_padded_with_null_at_the_start(path: str) -> None:
    a = {"stack": "bar-kWh", "data": [[1, 10, "a1"], [2, 20, "a2"], [3, 30, "a3"]]}
    b = {"stack": "bar-kWh", "data": [[2, 5, "b2"], [3, 6, "b3"]]}

    out_a, out_b = _pad(path, [a, b])

    assert [p[0] for p in out_a["data"]] == [1, 2, 3]
    assert out_b["data"] == [[1, None], [2, 5, "b2"], [3, 6, "b3"]]


@pytest.mark.parametrize("path", FILES)
def test_gaps_in_the_middle_are_padded_and_order_is_sorted(path: str) -> None:
    a = {"stack": "bar-kWh", "data": [[1, 1], [3, 3]]}
    b = {"stack": "bar-kWh", "data": [[2, 2], [4, 4]]}

    out_a, out_b = _pad(path, [a, b])

    assert out_a["data"] == [[1, 1], [2, None], [3, 3], [4, None]]
    assert out_b["data"] == [[1, None], [2, 2], [3, None], [4, 4]]


@pytest.mark.parametrize("path", FILES)
def test_other_stacks_and_unstacked_series_are_left_alone(path: str) -> None:
    a = {"stack": "bar-kWh", "data": [[1, 1], [2, 2]]}
    other_unit = {"stack": "bar-h", "data": [[2, 9]]}
    line = {"data": [[5, 7]]}

    out = _pad(path, [a, other_unit, line])

    assert out[0]["data"] == [[1, 1], [2, 2]]
    assert out[1]["data"] == [[2, 9]]
    assert out[2]["data"] == [[5, 7]]


def test_tooltips_skip_padded_points() -> None:
    for path in FILES:
        source = (APP / path).read_text(encoding="utf-8")
        assert "allParams.filter(p => p.data[1] != null)" in source, path
        assert "padStackedSeries(echartsSeries)" in source, path
