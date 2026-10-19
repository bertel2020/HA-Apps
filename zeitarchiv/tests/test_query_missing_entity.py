"""Gespeicherte Diagramme nennen Entitäten, die es nicht mehr gibt (gelöscht): die Abfragen
liefern dann eine leere Serie mit vollständigem Zeitfenster statt HTTP 500."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=TZ)
WINDOW_KEYS = {"window_start", "window_end", "period_end", "is_current"}


@pytest.mark.parametrize("offset,yoy", [(0, False), (-1, False), (0, True)])
def test_query_series_for_unknown_entity_has_full_shape(client, offset, yoy) -> None:
    from app import main
    from app.storage import query

    result = query.query_series(
        main.DATA_DIR, main.index, "sensor.gibt_es_nicht", "day", TZ, NOW, offset=offset, year_over_year=yoy
    )

    assert result["points"] == []
    assert WINDOW_KEYS <= set(result)
    assert result["window_start"] < result["window_end"]


def test_query_raw_series_for_unknown_entity_has_full_shape(client) -> None:
    from app import main
    from app.storage import query

    result = query.query_raw_series(main.DATA_DIR, main.index, "sensor.gibt_es_nicht", "day", TZ, NOW)

    assert result["points"] == []
    assert WINDOW_KEYS <= set(result)


def test_api_endpoints_survive_unknown_entity(client) -> None:
    ghost = "sensor.gibt_es_nicht"

    for url in (
        f"/api/query-multi?entity_ids={ghost}&range=day&offset=0",
        f"/api/query-multi?entity_ids={ghost}&range=day&offset=0&raw=true",
        f"/api/query?entity_id={ghost}&range=day&offset=0&compare=true",
        f"/api/entity-stats?entity_ids={ghost}&range=day&offset=0",
    ):
        response = client.get(url)
        assert response.status_code == 200, (url, response.text)
