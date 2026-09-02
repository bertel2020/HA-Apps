"""Regressionstest für längere Schwellwerte der Lücken-Erkennung."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT ))

from app.formatting import GAP_THRESHOLD_LABELS


def test_gap_threshold_offers_six_and_twelve_hours_in_order() -> None:
    options = list(GAP_THRESHOLD_LABELS.items())
    assert ("360", "6 Stunden") in options
    assert ("720", "12 Stunden") in options
    assert options.index(("60", "1 Stunde")) < options.index(("360", "6 Stunden"))
    assert options.index(("360", "6 Stunden")) < options.index(("720", "12 Stunden"))
    assert options.index(("720", "12 Stunden")) < options.index(("1440", "1 Tag"))
