"""Zustands-Zeitstrahl in Spalten, Abfrage-Schnittstelle (ROADMAP 1.29,
Pixelverdichtung Schritt 2): ``/api/query?columns=true&px=…`` liefert für
Zustands-Entitäten in Jahr und Dekade Spalten statt Rohwechseln.

Die Rechnung selbst steht in test_state_columns.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import _paths  # noqa: F401
from app.storage.ingestion import IngestEvent, IngestionService

TZ = ZoneInfo("Europe/Berlin")
STATE = "sensor.columns_robot"
NUMBER = "sensor.columns_number"


@pytest.fixture
def ingested(client):
    from app import main

    service = IngestionService(main.DATA_DIR, main.index, TZ)
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for hour, state in ((0, "docked"), (1, "cleaning"), (2, "docked")):
        service.ingest(IngestEvent(
            event_id=uuid.uuid4().hex, entity_id=STATE, domain="sensor",
            ts=day.replace(hour=hour).timestamp(), value=None, state=state,
        ))
    service.ingest(IngestEvent(
        event_id=uuid.uuid4().hex, entity_id=NUMBER, domain="sensor", ts=day.timestamp(), value=1.0, state=None,
    ))
    return client


def test_query_returns_columns_for_a_state_entity(ingested) -> None:
    response = ingested.get(f"/api/query?entity_id={STATE}&range=year&columns=true&px=600")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["chart_type"] == "columns"
    assert data["columns"]
    assert data["dt"] > 0
    assert data["points"] == []
    assert data["states"]


@pytest.mark.parametrize("url", [
    f"/api/query?entity_id={STATE}&range=month&columns=true&px=600",
    f"/api/query?entity_id={STATE}&range=year&columns=true",
    f"/api/query?entity_id={NUMBER}&range=year&columns=true&px=600",
])
def test_query_refuses_what_has_no_columns(ingested, url) -> None:
    assert ingested.get(url).status_code == 400
