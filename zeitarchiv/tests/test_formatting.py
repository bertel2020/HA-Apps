"""Tests für app/formatting.py — insbesondere format_value (Rohwert-Anzeige)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.formatting import decimals_to_int, format_value


def test_whole_number_drops_trailing_decimal_zeros() -> None:
    assert format_value(4.0) == "4"
    assert format_value(-5.0) == "-5"
    assert format_value(0.0) == "0"


def test_fractional_value_keeps_meaningful_decimals() -> None:
    # Oberflächenformat ist deutsch (NUMBER_LOCALE="de-DE" in formatting.py):
    # Komma als Dezimal-, Punkt als Tausendertrennzeichen.
    assert format_value(21.437) == "21,437"
    assert format_value(0.1) == "0,1"


def test_rounds_to_three_decimals_but_still_strips_trailing_zeros() -> None:
    assert format_value(6403.06) == "6.403,06"
    assert format_value(6403.0601) == "6.403,06"


def test_explicit_decimals_always_uses_that_many_places() -> None:
    assert format_value(4.0, decimals=2) == "4,00"
    assert format_value(21.437, decimals=1) == "21,4"
    assert format_value(21.437, decimals=0) == "21"


def test_decimals_to_int_maps_auto_to_none() -> None:
    assert decimals_to_int("auto") is None
    assert decimals_to_int(None) is None
    assert decimals_to_int("2") == 2
    assert decimals_to_int("0") == 0


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
