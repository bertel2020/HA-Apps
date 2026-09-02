"""Tests für app/tips.py — Rotationslogik der Tipps im Meldungs-Center."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tips import TIPS, rotation_order


def test_rotation_order_cycles_through_all_tips_over_30_days() -> None:
    seen = {rotation_order(day, 1)[0]["slug"] for day in range(30)}
    assert seen == {t["slug"] for t in TIPS}


def test_rotation_order_is_deterministic() -> None:
    assert rotation_order(5, 1) == rotation_order(5, 1)
