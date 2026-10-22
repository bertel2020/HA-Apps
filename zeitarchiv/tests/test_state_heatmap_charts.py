"""Zustands-Heatmap in Chart-Editor und Kachel (ROADMAP 1.29, Phase 3):
der Diagrammtyp "heatmap", der gespeicherte Zustand je Entität
(saved_charts.heatmap_states) und der Parameter heatmap an den Abfragen.

Die Entitätsseite ist in test_state_heatmap.py abgedeckt (Abfrage selbst).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import _paths  # noqa: F401
from app import chart_types
from app.storage.ingestion import IngestEvent, IngestionService

TZ = ZoneInfo("Europe/Berlin")
STATE = "sensor.heatmap_robot"
NUMBER = "sensor.heatmap_number"


def _chart(chart_type: str, entity_ids: list[str]) -> dict:
    return {"chart_type": chart_type, "entity_ids": entity_ids}


# --- Typregeln ---------------------------------------------------------------


def test_heatmap_is_a_valid_editor_type_for_states_only() -> None:
    assert "heatmap" in chart_types.CHART_EDITOR_CHART_TYPES
    types = {"sensor.a": "state", "sensor.b": "state", "sensor.c": "standard"}
    assert chart_types.effective_chart_type(_chart("heatmap", ["sensor.a", "sensor.b"]), types) == "heatmap"
    # Kam später eine Zahl hinzu, wird es wie "auto" behandelt, statt Zahlen als Zustände zu zeichnen.
    assert chart_types.effective_chart_type(_chart("heatmap", ["sensor.a", "sensor.c"]), types) == "auto"


def test_heatmap_charts_get_their_own_overview_type() -> None:
    types = {"sensor.a": "state"}
    assert chart_types.chart_type_key(_chart("heatmap", ["sensor.a"]), types) == "heatmap"
    assert chart_types.CHART_TYPE_LABELS["heatmap"] == "Heatmap"


# --- Gespeicherter Zustand -----------------------------------------------------


def test_only_known_entities_with_text_keep_their_state() -> None:
    cleaned = chart_types.heatmap_states_clean(
        {"sensor.a": "cleaning", "sensor.weg": "docked", "sensor.b": "", "sensor.c": 5, "sensor.d": "x" * 300},
        ["sensor.a", "sensor.b", "sensor.c", "sensor.d"],
    )
    assert cleaned == {"sensor.a": "cleaning"}
    assert chart_types.heatmap_states_clean(None, ["sensor.a"]) == {}


def test_the_state_of_the_entity_page_travels_into_the_editor() -> None:
    params = {"entity_id": STATE, "chart_type": "heatmap", "heatmap_state": "cleaning"}
    prefill = chart_types.editor_display_prefill(params, set(), set(), set())
    assert prefill["chart_type"] == "heatmap"
    assert prefill["heatmap_states"] == {STATE: "cleaning"}
    assert chart_types.editor_display_prefill({"entity_id": STATE}, set(), set(), set())["heatmap_states"] == {}


def test_the_state_is_saved_with_the_chart_and_survives_duplicate(client) -> None:
    from app import main

    body = {
        "name": f"Heatmap {uuid.uuid4().hex[:6]}", "entity_ids": [STATE], "range_key": "month",
        "chart_type": "heatmap", "heatmap_states": {STATE: "cleaning", "sensor.nicht_im_chart": "x"},
    }
    response = client.post("/charts", json=body)
    assert response.status_code == 200, response.text
    chart_id = response.json()["id"]
    chart = main.index.get_saved_chart(chart_id)
    assert chart["chart_type"] == "heatmap"
    assert chart["heatmap_states"] == {STATE: "cleaning"}

    changed = client.post(f"/charts/{chart_id}", json={**body, "heatmap_states": {STATE: "docked"}})
    assert changed.status_code == 200
    assert main.index.get_saved_chart(chart_id)["heatmap_states"] == {STATE: "docked"}

    copy_id = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
    assert main.index.get_saved_chart(copy_id)["heatmap_states"] == {STATE: "docked"}


def test_a_chart_without_a_choice_has_an_empty_state_map(client) -> None:
    from app import main

    chart_id = main.index.create_saved_chart(f"Ohne {uuid.uuid4().hex[:6]}", [NUMBER], "day", continuous=False)
    assert main.index.get_saved_chart(chart_id)["heatmap_states"] == {}


# --- Abfragen ------------------------------------------------------------------


def _ingest_states(client) -> None:
    from app import main

    service = IngestionService(main.DATA_DIR, main.index, TZ)
    now = datetime.now(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for hour, state in ((0, "docked"), (1, "cleaning"), (2, "docked")):
        service.ingest(IngestEvent(
            event_id=uuid.uuid4().hex, entity_id=STATE, domain="sensor",
            ts=day.replace(hour=hour).timestamp(), value=None, state=state,
        ))
    service.ingest(IngestEvent(
        event_id=uuid.uuid4().hex, entity_id=NUMBER, domain="sensor", ts=day.timestamp(), value=1.0, state=None,
    ))


@pytest.fixture
def ingested(client):
    _ingest_states(client)
    return client


def test_query_returns_the_cells_for_a_state_entity(ingested) -> None:
    response = ingested.get(f"/api/query?entity_id={STATE}&range=month&heatmap=true")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["chart_type"] == "heatmap"
    assert data["cells"]


def test_query_multi_returns_cells_per_state_entity_and_leaves_numbers_alone(ingested) -> None:
    response = ingested.get(f"/api/query-multi?entity_ids={STATE},{NUMBER}&range=month&heatmap=true")
    assert response.status_code == 200, response.text
    by_id = {s["entity_id"]: s for s in response.json()["series"]}
    assert by_id[STATE]["cells"]
    assert "cells" not in by_id[NUMBER]


@pytest.mark.parametrize("url", [
    f"/api/query?entity_id={STATE}&range=week&heatmap=true",
    f"/api/query-multi?entity_ids={STATE}&range=day&heatmap=true",
    f"/api/query?entity_id={NUMBER}&range=month&heatmap=true",
])
def test_heatmap_is_refused_where_it_makes_no_sense(ingested, url) -> None:
    assert ingested.get(url).status_code == 400
