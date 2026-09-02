"""Tests für app/storage/index.py::derive_type (Konzept Abschnitt 03)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.index import derive_type  # noqa: E402


def test_measurement_is_standard() -> None:
    assert derive_type("sensor", "measurement") == "standard"


def test_total_increasing_is_counter() -> None:
    assert derive_type("sensor", "total_increasing") == "counter"


def test_total_is_counter() -> None:
    assert derive_type("sensor", "total") == "counter"


def test_binary_sensor_is_switch_even_with_no_state_class() -> None:
    assert derive_type("binary_sensor", None) == "switch"


def test_switch_domain_is_switch() -> None:
    assert derive_type("switch", None) == "switch"


def test_switch_domain_wins_over_stray_state_class() -> None:
    # Praktisch kommt das nicht vor, aber die Domain soll trotzdem Vorrang haben.
    assert derive_type("binary_sensor", "total_increasing") == "switch"


def test_no_state_class_and_unknown_domain_falls_back_to_standard() -> None:
    assert derive_type("sensor", None) == "standard"


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
