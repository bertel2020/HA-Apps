"""Löschen großer Auswahlen im Reiter „Bereinigen“."""
from __future__ import annotations


def test_the_delete_button_sends_the_chosen_page_size(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_bulk_delete_button"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    html = client.get(f"/entities/{entity_id}/rows?range=all&page_size=500").text

    assert 'hx-include="[name=ts]:checked, #controls, [name=page_size]"' in html
