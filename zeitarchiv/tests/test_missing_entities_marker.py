"""Gespeicherte Diagramme/Tabellen, die eine gelöschte Entität nennen, werden markiert."""

from __future__ import annotations

import pytest

from app.entity_usage import missing_entity_ids


def test_missing_entity_ids_keeps_order_and_drops_duplicates() -> None:
    assert missing_entity_ids(["sensor.b", "sensor.a", "sensor.b", "sensor.c"], {"sensor.a"}) == ["sensor.b", "sensor.c"]
    assert missing_entity_ids([], {"sensor.a"}) == []


@pytest.fixture()
def stored(client):
    from app import main

    main.index.get_or_create_entity("sensor.marker_da", "sensor", "measurement", "kWh")
    chart_id = main.index.create_saved_chart(
        "Marker-Test-Diagramm", ["sensor.marker_da", "sensor.marker_weg"], "day", False
    )
    yield chart_id
    main.index.delete_saved_chart(chart_id)
    main.index.delete_entity("sensor.marker_da")


def test_chart_list_and_view_flag_only_charts_with_missing_entities(client, stored) -> None:
    liste = client.get("/charts").text
    assert "Entität fehlt" in liste and "has-missing" in liste
    assert "sensor.marker_weg" in liste  # Tooltip nennt die fehlende ID
    assert "sensor.marker_da" not in liste.split("Marker-Test-Diagramm")[1].split("</div>")[0]

    ansicht = client.get(f"/charts/{stored}").text
    assert "missing-note" in ansicht and "sensor.marker_weg" in ansicht


def test_chart_without_missing_entities_is_not_flagged(client) -> None:
    from app import main

    main.index.get_or_create_entity("sensor.marker_ok", "sensor", "measurement", "kWh")
    chart_id = main.index.create_saved_chart("Marker-Ok-Diagramm", ["sensor.marker_ok"], "day", False)
    try:
        assert "has-missing" not in client.get("/charts").text.split("Marker-Ok-Diagramm")[0].split("chart-grid")[-1]
        assert "missing-note" not in client.get(f"/charts/{chart_id}").text
    finally:
        main.index.delete_saved_chart(chart_id)
        main.index.delete_entity("sensor.marker_ok")


def test_dashboard_tile_and_table_list_flag_missing_entities(client) -> None:
    from app import main

    main.index.get_or_create_entity("sensor.marker_t", "sensor", "measurement", "kWh")
    chart_id = main.index.create_saved_chart("Marker-Kachel-Diagramm", ["sensor.marker_t", "sensor.marker_t_weg"], "day", False)
    table_id = main.index.create_saved_table(
        "Marker-Tabelle", [{"label": "Tag", "range_key": "day", "offset": 0, "year_over_year": False}],
        [{"label": "Weg", "row_type": "entity", "entity_ids": ["sensor.marker_t_weg"], "formula": "", "bold": False}],
    )
    dashboard_id = main.index.list_dashboards()[0]["id"]
    main.index.pin_item_to_dashboard(dashboard_id, "chart", chart_id)
    main.index.pin_item_to_dashboard(dashboard_id, "table", table_id)
    try:
        kacheln = client.get(f"/dashboards/{dashboard_id}").text
        assert kacheln.count("missing-badge is-tile") == 2 and "sensor.marker_t_weg" in kacheln
        assert "has-missing" in client.get("/tables").text
    finally:
        main.index.unpin_item_from_dashboard(dashboard_id, "chart", chart_id)
        main.index.unpin_item_from_dashboard(dashboard_id, "table", table_id)
        main.index.delete_saved_chart(chart_id)
        main.index.delete_saved_table(table_id)
        main.index.delete_entity("sensor.marker_t")
