"""Dynamischer Strompreis in den Demo-Daten (sensor.demo_strompreis_dynamisch).

Die Reihe hängt nur an Zeitstempel und Kalendertag, nicht an der Simulation. Eine Demo, die vor
dieser Entität angelegt wurde, bekommt den Verlauf beim Ergänzen rückwirkend.
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.demo_generation import (
    DYNAMIC_PRICE_ENTITY_ID,
    dynamic_price_at,
    gen_dynamic_price,
    localized_id,
    run_generation,
)
from app.storage.entity_removal import delete_all_values
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def test_price_is_reproducible_and_in_a_plausible_range() -> None:
    start = datetime(2026, 3, 1, tzinfo=TZ)
    end = datetime(2026, 3, 8, tzinfo=TZ)
    rows = gen_dynamic_price(start, end, TZ)
    assert rows == gen_dynamic_price(start, end, TZ)
    assert len(rows) == 7 * 96
    values = [v for _ts, v in rows]
    assert 0.12 <= min(values) and max(values) <= 0.80
    # ein Tarif mit Tagesgang: nicht jeder Wert gleich, Abend teurer als Mittag
    abend = [dynamic_price_at(datetime(2026, 3, d, 19, 0, tzinfo=TZ).timestamp(), TZ) for d in range(1, 8)]
    mittag = [dynamic_price_at(datetime(2026, 3, d, 13, 0, tzinfo=TZ).timestamp(), TZ) for d in range(1, 8)]
    assert sum(abend) > sum(mittag)


def test_append_backfills_the_price_for_a_demo_created_without_it(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(3), months=2)
        price_id = localized_id(DYNAMIC_PRICE_ENTITY_ID)
        delete_all_values(tmp_path, index, price_id)  # so sah eine ältere Demo aus
        assert index.get_entity(price_id)["last_ts"] is None

        result = run_generation(tmp_path, index, TZ, random.Random(4), months=2, append=True)

        entity = index.get_entity(price_id)
        anchor = index.get_entity(localized_id("sensor.demo_gesamtwirkleistung"))
        assert not result.skipped
        assert entity["last_ts"] is not None
        # reicht bis zum Beginn der übrigen Historie zurück
        assert entity["first_ts"] <= anchor["first_ts"] + 900
    finally:
        index.close()
