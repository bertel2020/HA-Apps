"""Zeitstrahl ist der Standard für Charts, die nur aus Schaltern bestehen."""
from __future__ import annotations

from app.chart_types import effective_chart_type

TYPES = {"s1": "switch", "s2": "switch", "m1": "measurement"}


def _chart(chart_type: str, *entity_ids: str) -> dict:
    return {"chart_type": chart_type, "entity_ids": list(entity_ids)}


def test_auto_with_only_switches_becomes_timeline():
    assert effective_chart_type(_chart("auto", "s1", "s2"), TYPES) == "timeline"


def test_auto_with_mixed_or_empty_stays_auto():
    assert effective_chart_type(_chart("auto", "s1", "m1"), TYPES) == "auto"
    assert effective_chart_type(_chart("auto"), TYPES) == "auto"


def test_explicit_choices_are_kept():
    assert effective_chart_type(_chart("bar", "s1"), TYPES) == "bar"
    assert effective_chart_type(_chart("donut", "s1"), TYPES) == "donut"
    assert effective_chart_type(_chart("timeline", "m1"), TYPES) == "timeline"
