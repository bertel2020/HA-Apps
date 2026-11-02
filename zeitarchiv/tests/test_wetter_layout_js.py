"""Spaltenzahl der Kachelreihe (static/js/wetter-layout.js), in node ausgeführt."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _paths import APP

MODULE = APP / "static/js/wetter-layout.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


def _columns(count: int, width: int) -> int:
    script = f"const h = require({json.dumps(str(MODULE))}); console.log(h.tileColumns({count}, {width}));"
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
