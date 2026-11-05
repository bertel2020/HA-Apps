"""Längere Historie der Wetter-Reihen in den Demo-Daten (``weather_months``).

Nur die Reihen, die das Wetterdashboard liest, reichen weiter zurück; der Rest der Demo behält seinen Zeitraum.
Das Wetter und die Zähler müssen an der Nahtstelle nahtlos weiterlaufen.
"""

from __future__ import annotations

import random
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import demo_generation
from app.demo_generation import WEATHER_HISTORY_KEYS, WEATHER_HISTORY_MONTHS, run_generation
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
DAY = 86400


@pytest.fixture
def captured(monkeypatch):
    """Geschriebene Zeilen je Entität, ohne den eigentlichen Schreibweg zu ändern."""
    rows: dict[str, list] = {}
    original = demo_generation.write_entity

    def spy(data_dir, index, tz, entity, entity_rows, language):
        rows[entity.entity_id] = list(entity_rows)
        return original(data_dir, index, tz, entity, entity_rows, language)

    monkeypatch.setattr(demo_generation, "write_entity", spy)
    return rows


def test_only_the_weather_series_reach_further_back(tmp_path: Path, captured) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(5), months=1, weather_months=3)
    finally:
        index.close()
    household_first = captured["sensor.demo_gesamtwirkleistung"][0][0]
    for entity_id in WEATHER_HISTORY_KEYS:
        first = captured[entity_id][0][0]
        # Der Regensensor schreibt nur bei Wechseln, sein letzter Wert liegt nicht am Ende des Zeitraums.
        if entity_id != "binary_sensor.demo_regensensor":
            assert 85 * DAY <= captured[entity_id][-1][0] - first <= 95 * DAY, entity_id
        assert first < household_first - 50 * DAY, entity_id
    assert 28 * DAY <= captured["sensor.demo_gesamtwirkleistung"][-1][0] - household_first <= 32 * DAY


def test_counters_and_timestamps_continue_across_the_seam(tmp_path: Path, captured) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(6), months=1, weather_months=3)
    finally:
        index.close()
    for entity_id in ("sensor.demo_niederschlag", "sensor.demo_waermepumpe_energie", "sensor.demo_heizstab_energie"):
        values = [value for _ts, value in captured[entity_id]]
        assert values == sorted(values), f"{entity_id}: Zähler fällt an der Nahtstelle"
    for entity_id in WEATHER_HISTORY_KEYS:
        stamps = [ts for ts, _value in captured[entity_id]]
        assert stamps == sorted(stamps), f"{entity_id}: Zeitstempel unsortiert"
        # Doppelte gibt es nur bei der Zeitumstellung (Herbst, lokale Zeit läuft eine Stunde zurück), nicht an der Nahtstelle.
        assert len(stamps) - len(set(stamps)) <= 12, entity_id


def test_no_extension_when_weather_months_is_not_larger(tmp_path: Path, captured) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(7), months=1, weather_months=1)
    finally:
        index.close()
    first = captured["sensor.demo_niederschlag"][0][0]
    household_first = captured["sensor.demo_gesamtwirkleistung"][0][0]
    assert abs(first - household_first) < 2 * DAY


def test_weather_history_is_five_years() -> None:
    assert WEATHER_HISTORY_MONTHS == 60
