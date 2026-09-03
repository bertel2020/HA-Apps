"""Tests für den zeitlichen Zoom-Anker der Entitätschart-Navigation."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "static" / "js" / "period-navigation.js"
TZ = ZoneInfo("Europe/Berlin")


def _ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp() * 1000)


def _run_js(expression: str):
    code = f"const p=require({json.dumps(str(SCRIPT))}); console.log(JSON.stringify({expression}));"
    env = {**os.environ, "TZ": "Europe/Berlin"}
    result = subprocess.run(
        ["node", "-e", code], check=True, capture_output=True, text=True, env=env
    )
    return json.loads(result.stdout)


def test_current_window_uses_now_as_anchor() -> None:
    now = _ms(2026, 8, 24, 14, 30)
    anchor = _run_js(f"p.anchorForWindow({_ms(2026, 8, 24) / 1000}, {now / 1000}, true, {now})")
    assert anchor == now


def test_past_window_uses_its_midpoint_as_anchor() -> None:
    start = _ms(2026, 8, 20)
    end = _ms(2026, 8, 21)
    anchor = _run_js(f"p.anchorForWindow({start / 1000}, {end / 1000}, false, {_ms(2026, 8, 24, 14, 30)})")
    assert anchor == _ms(2026, 8, 20, 12)


def test_anchor_maps_to_every_target_resolution_without_losing_context() -> None:
    now = _ms(2026, 8, 24, 14, 30)
    cases = {
        "hour": (_ms(2026, 8, 20, 12), -98),
        "day": (_ms(2026, 8, 20, 12), -4),
        "week": (_ms(2026, 8, 20, 12), -1),
        "month": (_ms(2026, 7, 16, 12), -1),
        "year": (_ms(2025, 7, 2, 12), -1),
        "decade": (_ms(2015, 1, 1, 12), -1),
    }
    for range_key, (anchor, expected) in cases.items():
        actual = _run_js(f"p.offsetForRange('{range_key}', {anchor}, {now})")
        assert actual == expected, range_key


def test_future_anchor_is_clamped_to_current_period() -> None:
    actual = _run_js(
        f"p.offsetForRange('day', {_ms(2026, 8, 26, 12)}, {_ms(2026, 8, 24, 14, 30)})"
    )
    assert actual == 0


def test_same_anchor_survives_a_coarse_current_period_and_maps_back_to_its_day() -> None:
    """22. August → Monat (offset 0) → Tag darf nicht zu 24. August werden."""
    now = _ms(2026, 8, 24, 14, 30)
    anchor = _ms(2026, 8, 22, 12)
    offsets = _run_js(
        f"['day', 'month', 'day'].map(r => p.offsetForRange(r, {anchor}, {now}))"
    )
    assert offsets == [-2, 0, -2]


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
