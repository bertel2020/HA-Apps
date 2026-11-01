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


def test_every_action_button_keeps_the_chosen_page_size(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_page_size_buttons"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    index.mark_deleted(entity_id, [1_700_000_000.0], deleted_at=1_700_000_100.0)

    for filter_ in ("all", "duplicates", "repetitions", "counter_decreases"):
        html = client.get(f"/entities/{entity_id}/rows?range=all&filter={filter_}&page_size=500").text
        # Einzige erlaubte Ausnahme: der Seitengrößen-Auswahl selbst, der seinen Wert als Auslöser mitschickt.
        assert html.count('hx-include="#controls"') == 1, filter_

    for route in ("undo-preview", "duplicates-preview", "repetitions-preview"):
        html = client.post(
            f"/entities/{entity_id}/rows/{route}", data={"range": "all", "page_size": "500"}
        ).text
        assert '<input type="hidden" name="page_size" value="500">' in html, route
        assert 'hx-include="#controls"' not in html, route


def test_bulk_entity_actions_accept_more_than_1000_selected_entities(client) -> None:
    ids = [f"sensor.pytest_bulk_select_{i}" for i in range(1001)]

    for path in (
        "/entities/usage-check",
        "/housekeeping/datenqualitaet/duplicates/preview",
        "/housekeeping/datenqualitaet/duplicates/delete",
        "/housekeeping/datenqualitaet/duplicates/undo",
    ):
        response = client.post(path, data={"entity_id": ids})
        assert response.status_code != 400 or "Too many fields" not in response.text, path

    response = client.post("/entities/delete-selected", data={"entity_id": ids})
    assert "Too many fields" not in response.text
