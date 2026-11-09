"""Raum-Sensoren der Demo für „Temperatur & Klima“ (ROADMAP 1.51): Werte, Entitäten und Historie bei ``--append``."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.demo_generation import (
    DEMO_ENTITIES,
    ROOM_ENTITY_IDS,
    ROOM_HISTORY_MONTHS,
    ROOM_NAMES,
    ROOM_SERIES_KEYS,
    WeatherContext,
    localized_id,
    missing_room_ids,
    run_generation,
    simulate_rooms,
)
from app.i18n import translate
from app.storage.entity_removal import delete_entity
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
DAY = 86400


def _rooms(start: datetime, days: int) -> dict:
    end = start + timedelta(days=days)
    return simulate_rooms(start, end, WeatherContext(start, end, random.Random(3)), random.Random(4))


def _at(rows: list, day: datetime, hour: float) -> float:
    target = (day + timedelta(hours=hour)).timestamp()
    return min(rows, key=lambda row: abs(row[0] - target))[1]


def test_four_rooms_with_temperature_and_humidity_come_last() -> None:
    assert list(ROOM_NAMES) == ["schlafzimmer", "kueche", "bad", "keller"]
    assert len(ROOM_ENTITY_IDS) == 8 == len(ROOM_SERIES_KEYS)
    assert [e.entity_id for e in DEMO_ENTITIES[-8:]] == list(ROOM_ENTITY_IDS)
    for entity in DEMO_ENTITIES[-8:]:
        assert entity.domain == "sensor" and entity.state_class == "measurement"
        assert entity.unit == ("°C" if entity.entity_id.endswith("_temperatur") else "%")
        # Übersetzt, sonst entstehen englische Entitäten ohne Namen
        assert translate(entity.friendly_name, "en") != entity.friendly_name
    assert localized_id("sensor.demo_bad_temperatur", "en") == "sensor.demo_bathroom_temperature"


def test_values_look_like_the_rooms_they_name() -> None:
    winter = _rooms(datetime(2025, 1, 14, tzinfo=TZ), 3)
    day = datetime(2025, 1, 15, tzinfo=TZ)
    keller = [v for _t, v in winter["keller_temp"]]
    assert max(keller) - min(keller) < 1.5 and 10 < min(keller) and max(keller) < 16
    bad = winter["bad_feuchte"]
    assert _at(bad, day, 7.2) > _at(bad, day, 14) + 15  # Dusche am Morgen (Mittwoch)
    assert max(v for _t, v in bad) < 96
    kueche = winter["kueche_feuchte"]
    assert _at(kueche, day, 18.6) > _at(kueche, day, 15) + 6  # Abendessen
    schlaf = [v for _t, v in winter["schlafzimmer_temp"]]
    assert 16 < min(schlaf) and max(schlaf) < 21
    for key, rows in winter.items():
        assert len(rows) == 3 * 288 + 1, key
        stamps = [ts for ts, _v in rows]
        assert stamps == sorted(stamps)
        if key.endswith("_feuchte"):
            assert all(25 <= v <= 95 for _t, v in rows), key


def test_the_cellar_is_damper_and_warmer_in_summer() -> None:
    winter = _rooms(datetime(2025, 1, 14, tzinfo=TZ), 2)
    summer = _rooms(datetime(2025, 7, 22, tzinfo=TZ), 2)
    mean = lambda rows: sum(v for _t, v in rows) / len(rows)  # noqa: E731
    assert mean(summer["keller_temp"]) > mean(winter["keller_temp"]) + 3
    assert mean(summer["keller_feuchte"]) > mean(winter["keller_feuchte"]) + 8


def _first_and_last(index: Index, entity_id: str) -> tuple[float, float]:
    entity = index.get_entity(entity_id)
    return entity["first_ts"], entity["last_ts"]


def test_append_adds_three_years_of_history_when_the_rooms_are_missing(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(9), months=1, weather_months=1)
        for entity_id in ROOM_ENTITY_IDS:  # eine Demo von vor dieser Änderung kennt die Räume nicht
            delete_entity(tmp_path, index, entity_id)
        assert missing_room_ids(index, "de") == list(ROOM_ENTITY_IDS)
        other = _first_and_last(index, "sensor.demo_wohnzimmer_temperatur")
        status: list[str] = []
        result = run_generation(tmp_path, index, TZ, random.Random(10), append=True, months=1, on_status=status.append)
        now = datetime.now(TZ).timestamp()
        assert missing_room_ids(index, "de") == []
        for entity_id in ROOM_ENTITY_IDS:
            first, last = _first_and_last(index, entity_id)
            assert now - first >= (ROOM_HISTORY_MONTHS * 30 - 1) * DAY, entity_id
            assert now - last < 900, entity_id
        assert any("Raum-Sensoren" in line for line in status)
        assert result.rows_written > 8 * 100_000
        # Was schon da war, behält seinen Anfang.
        assert _first_and_last(index, "sensor.demo_wohnzimmer_temperatur")[0] == other[0]
    finally:
        index.close()


def test_append_continues_existing_rooms_without_a_second_history(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(11), months=1, weather_months=1)
        before = _first_and_last(index, "sensor.demo_bad_luftfeuchte")
        assert missing_room_ids(index, "de") == []
        status: list[str] = []
        run_generation(tmp_path, index, TZ, random.Random(12), append=True, months=1, on_status=status.append)
        after = _first_and_last(index, "sensor.demo_bad_luftfeuchte")
        assert after[0] == before[0] and after[1] >= before[1]
        assert not any("Raum-Sensoren" in line for line in status)
    finally:
        index.close()


def test_full_generation_gives_the_rooms_the_weather_history(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(13), months=1, weather_months=3)
        first, last = _first_and_last(index, "sensor.demo_keller_temperatur")
        assert 85 * DAY <= last - first <= 95 * DAY
        household_first = _first_and_last(index, "sensor.demo_gesamtwirkleistung")[0]
        assert first < household_first - 50 * DAY
    finally:
        index.close()
