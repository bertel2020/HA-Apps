"""Löschen großer Auswahlen im Reiter „Bereinigen“."""
from __future__ import annotations


def test_deleting_a_full_page_of_1000_rows_marks_them_all(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_bulk_delete"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    timestamps = [str(1_700_000_000.0 + i) for i in range(1000)]

    response = client.post(
        f"/entities/{entity_id}/rows/delete",
        data={"ts": timestamps, "range": "all", "filter": "all", "page": "1", "page_size": "1000"},
    )

    assert response.status_code == 200
    assert sum(index.get_deleted_counts_for_entity(entity_id).values()) == 1000


def test_the_delete_button_sends_the_chosen_page_size(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_bulk_delete_button"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    html = client.get(f"/entities/{entity_id}/rows?range=all&page_size=500").text

    assert 'hx-include="[name=ts]:checked, #controls, [name=page_size]"' in html
