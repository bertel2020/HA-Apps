"""Spaltenzahl der Kachelreihe (static/js/wetter-layout.js), in node ausgeführt."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _paths import APP

MODULE = APP / "static/js/wetter-layout.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


def _columns(count: int, width: int, maximum: int | None = None) -> int:
    extra = "" if maximum is None else f", {maximum}"
    script = f"const h = require({json.dumps(str(MODULE))}); console.log(h.tileColumns({count}, {width}{extra}));"
    return int(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)


@pytest.mark.parametrize(
    ("count", "width", "expected"),
    [
        (8, 940, 4),  # nicht 6+2
        (8, 340, 2),  # Telefon: vier Reihen zu zwei
        (6, 940, 3),  # fünf Spalten ergäben 5+1: lieber 3+3
        (6, 1200, 6),  # eine Reihe
        (6, 560, 3),  # 3+3
        (5, 940, 5),
        (7, 940, 4),  # 4+3 statt 6+1
        (4, 940, 4),
        (3, 940, 3),
        (2, 940, 2),
        (1, 940, 1),
        (3, 340, 2),  # schmal: 2+1; drei Spalten würden unter 150 px schrumpfen
    ],
)
def test_no_tile_is_left_alone_in_the_last_row(count: int, width: int, expected: int) -> None:
    assert _columns(count, width) == expected


@pytest.mark.parametrize(
    ("count", "width", "expected"),
    [
        (8, 1400, 8),  # ohne Obergrenze eine Reihe aus acht (heute)
        (6, 1400, 6),
        (12, 1400, 6),  # 6+6
        (5, 1400, 5),
        (7, 1400, 7),
    ],
)
def test_without_a_maximum_the_row_may_use_the_whole_width(count: int, width: int, expected: int) -> None:
    assert _columns(count, width) == expected


@pytest.mark.parametrize(
    ("count", "width", "expected"),
    [
        (8, 1400, 4),  # nicht 8 in einer Reihe, nicht 6+2
        (6, 1400, 6),
        (12, 1400, 6),
        (9, 1400, 5),
        (4, 1400, 4),
        (7, 1400, 4),  # 4+3
    ],
)
def test_the_wide_layout_caps_the_row_at_six_tiles(count: int, width: int, expected: int) -> None:
    assert _columns(count, width, 6) == expected


def test_only_the_wide_layout_passes_the_cap() -> None:
    script = (APP / "static/js/wetter.js").read_text(encoding="utf-8")
    assert "document.documentElement.dataset.pageLayout === 'wide' ? 6 : 0" in script
    assert "WetterLayout.tileColumns(count, element.clientWidth, WIDE_TILE_MAX_COLUMNS)" in script
