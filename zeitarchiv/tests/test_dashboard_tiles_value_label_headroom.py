"""Kachel-Balken mit "Werte anzeigen" lassen Platz für das Label des höchsten Balkens.

Die Y-Achse endet exakt beim Maximalwert; mit nur 10 px Kachelrand wurde die Zahl
über dem höchsten Balken abgeschnitten (nur in der Übersicht, Chart-Editor und
Detailseite haben mehr Rand).
"""

from __future__ import annotations

import re

from _paths import tile_js


SOURCE = tile_js()


def _main_grid() -> str:
    match = re.search(r"grid: \{\s*left: 6,.*?containLabel: true,\s*\}", SOURCE, re.S)
    assert match
    return match.group(0)


def test_grid_reserves_room_above_bars_only_when_values_are_shown() -> None:
    grid = _main_grid()
    assert "showValues && !horizontalActive && !stackedActive ? 10 + scaledFont(10) + 6 : 10" in grid


def test_grid_reserves_room_right_of_horizontal_bars_when_values_are_shown() -> None:
    assert "right: showValues && horizontalActive ? 36 : 6" in _main_grid()
