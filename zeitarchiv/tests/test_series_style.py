"""Darstellung je Serie (saved_charts.series_style): Linie oder Balken, dazu eine eigene Achse für Linien.

Ohne Eintrag gilt wie bisher die Entitätsart (Zähler Balken, Messwert Linie); Schalter und Zustände
bleiben, wie sie sind.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from _paths import APP, template_text
from app.chart_types import series_style_clean
from app.storage.ingestion import IngestEvent, IngestionService

TZ = ZoneInfo("Europe/Berlin")
COUNTER = "sensor.style_zaehler"
VALUE = "sensor.style_wert"
SWITCH = "sensor.style_schalter"
IDS = [COUNTER, VALUE, SWITCH]
TYPES = {COUNTER: "counter", VALUE: "standard", SWITCH: "switch"}
EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
TILE_JS = (APP / "static/js/chart-tile-render.js").read_text(encoding="utf-8")


def test_clean_keeps_valid_entries_and_drops_the_rest() -> None:
    raw = {
        COUNTER: {"kind": "line", "axis": "own"},
        VALUE: {"kind": "bar", "axis": "shared", "extra": 1},
        SWITCH: {"kind": "line"},
        "sensor.fremd": {"kind": "line"},
    }
    assert series_style_clean(raw, IDS, TYPES) == {
        COUNTER: {"kind": "line", "axis": "own"},
        VALUE: {"kind": "bar"},
    }


def test_clean_drops_empty_and_broken_input() -> None:
    assert series_style_clean(None, IDS) == {}
    assert series_style_clean("kaputt", IDS) == {}
    assert series_style_clean({COUNTER: {"kind": "punkte"}, VALUE: "line", SWITCH: {}}, IDS) == {}
    # Die eigene Achse allein bleibt: sie gilt, sobald die Serie eine Linie ist.
    assert series_style_clean({VALUE: {"axis": "own"}}, IDS) == {VALUE: {"axis": "own"}}


@pytest.fixture
def ingested(client):
    from app import main

    service = IngestionService(main.DATA_DIR, main.index, TZ)
    now = datetime.now(TZ).replace(hour=1, minute=0, second=0, microsecond=0)
    for hour, counter, value in ((0, 1.0, 20.0), (1, 2.5, 21.0)):
        for entity_id, number, state_class, unit in (
            (COUNTER, counter, "total_increasing", "kWh"), (VALUE, value, "measurement", "°C"),
        ):
            service.ingest(IngestEvent(
                event_id=uuid.uuid4().hex, entity_id=entity_id, domain="sensor",
                ts=now.replace(hour=hour).timestamp(), value=number, state_class=state_class, unit=unit,
            ))
    return client


def test_style_survives_save_open_and_duplicate(client) -> None:
    from app.main import index

    index.get_or_create_entity(COUNTER, "sensor", "total_increasing", "kWh")
    index.get_or_create_entity(VALUE, "sensor", "measurement", "°C")
    style = {COUNTER: {"kind": "line", "axis": "own"}, VALUE: {"kind": "bar"}}
    body = {"name": "Darstellung-Probe", "entity_ids": [COUNTER, VALUE], "range_key": "day", "series_style": style}
    chart_id = client.post("/charts", json=body).json()["id"]
    assert index.get_saved_chart(chart_id)["series_style"] == style
    assert '"own"' in client.get(f"/charts/{chart_id}").text

    kopie = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
    assert index.get_saved_chart(kopie)["series_style"] == style

    client.post(f"/charts/{chart_id}", json={**body, "series_style": {}})
    assert index.get_saved_chart(chart_id)["series_style"] == {}


def test_multi_query_without_kinds_keeps_the_entity_type(ingested) -> None:
    series = ingested.get(f"/api/query-multi?entity_ids={COUNTER},{VALUE}&range=day").json()["series"]
    assert [s["chart_type"] for s in series] == ["bar", "line"]


def test_multi_query_draws_a_counter_as_line_and_a_value_as_bar(ingested) -> None:
    response = ingested.get(f"/api/query-multi?entity_ids={COUNTER},{VALUE}&range=day&kinds={COUNTER}:line,{VALUE}:bar")
    assert response.status_code == 200, response.text
    series = response.json()["series"]
    assert [s["chart_type"] for s in series] == ["line", "bar"]
    assert all(s["points"] for s in series)


def test_multi_query_applies_the_kind_to_the_comparison_too(ingested) -> None:
    response = ingested.get(
        f"/api/query-multi?entity_ids={COUNTER}&range=day&compare=true&kinds={COUNTER}:line"
    )
    assert response.status_code == 200, response.text
    assert response.json()["series"][0]["chart_type"] == "line"


def test_multi_query_ignores_a_kind_for_switches(ingested) -> None:
    from app import main

    main.index.get_or_create_entity(SWITCH, "switch", "switch", "")
    response = ingested.get(f"/api/query-multi?entity_ids={SWITCH}&range=day&kinds={SWITCH}:line")
    assert response.status_code == 200, response.text
    assert response.json()["series"][0]["chart_type"] == "bar"


@pytest.mark.parametrize("kinds", ["sensor.x", "sensor.x:punkte", ":line", "sensor.x:line:bar"])
def test_multi_query_refuses_a_broken_kinds_parameter(ingested, kinds) -> None:
    assert ingested.get(f"/api/query-multi?entity_ids={COUNTER}&range=day&kinds={kinds}").status_code == 400


def test_renderers_read_the_style_and_key_the_axis_alike() -> None:
    # Editor und Kachel müssen dieselbe Regel haben (Parität, siehe chart-tile-render.js).
    for source in (EDITOR_JS, TILE_JS):
        assert "ownAxis" in source
        assert "kinds" in source


def test_the_editor_row_has_the_kind_field_and_the_own_axis_switch() -> None:
    template = template_text("chart_editor.html")
    assert "setSeriesKind(" in template
    assert "setSeriesAxis(" in template
