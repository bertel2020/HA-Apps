"""Tests für die optionalen range/offset-Query-Parameter auf /entities/{id}
(Nutzerwunsch: Energiedashboard-KPI-Kacheln verlinken auf den Entitäts-Chart
mit dem dort gerade gewählten Zeitraum). Ungültige/fehlende Werte müssen auf
das bisherige Alpine-Standardverhalten zurückfallen ('day'/0), nicht auf
einen Fehler oder ungültigen Zeitraum."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MAIN_SOURCE = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8")
ENTITY_DETAIL_SOURCE = (
    Path(__file__).resolve().parents[1] / "app/templates/entity_detail.html"
).read_text(encoding="utf-8")


def test_entity_detail_route_accepts_range_and_offset_query_params() -> None:
    assert 'range_key: str | None = Query(None, alias="range")' in MAIN_SOURCE
    assert "initial_range = range_key if range_key in query_mod.RANGE_KEYS else None" in MAIN_SOURCE


def test_entity_detail_route_ignores_offset_without_a_valid_range() -> None:
    """Ein offset ohne gültigen range wäre bedeutungslos (kein Zeitraum, auf
    den er sich bezöge) — main.py muss ihn dann verwerfen, nicht ungeprüft
    an den Client durchreichen."""
    assert '"initial_offset": offset if initial_range else 0' in MAIN_SOURCE


def test_entity_chart_seeds_range_and_offset_from_the_query_params() -> None:
    assert "const INITIAL_RANGE = {{ initial_range | tojson }};" in ENTITY_DETAIL_SOURCE
    assert "const INITIAL_OFFSET = {{ initial_offset | tojson }};" in ENTITY_DETAIL_SOURCE
    assert "range: INITIAL_RANGE || 'day'," in ENTITY_DETAIL_SOURCE
    assert "offset: INITIAL_OFFSET," in ENTITY_DETAIL_SOURCE
