"""Import → Home Assistant: Der Dry Run trägt sein Ergebnis als Status in die Tabelle ein
(statt nur eine Liste darunter) und merkt es sich je Einstellungen."""

from __future__ import annotations

import time

import pytest

from _paths import APP  # noqa: F401  (legt den App-Pfad auf sys.path)

IDS = ["sensor.dry_new", "sensor.dry_empty", "sensor.dry_fail"]


@pytest.fixture()
def entities(client):
    from app import main

    for entity_id in IDS:
        main.index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    yield IDS
    for entity_id in IDS:
        if main.index.get_entity(entity_id) is not None:
            main.index.delete_entity(entity_id)


def _fake_fetch(self, entity_ids, start, end, history_source, period, on_entity=None):
    from app.storage import ha_import

    now = time.time()
    fetched = {
        "sensor.dry_new": ha_import.HistoryFetchResult(rows=[(now - 3600, 1.0), (now - 1800, 2.0)]),
        "sensor.dry_empty": ha_import.HistoryFetchResult(rows=[]),
    }
    return fetched, ["sensor.dry_fail: Timeout"]


def test_dry_run_fills_status_column_and_filters(client, entities, monkeypatch) -> None:
    from app import import_routes

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", _fake_fetch)
    form = {"entity_ids": IDS, "history_source": "raw", "period": "hour", "range_preset": "max"}
    html = client.post("/import/ha/dry-run", data=form).text

    def status_of(entity_id: str) -> str:
        row = html[html.index(f'data-entity-id="{entity_id}"'):]
        return row[row.index('data-status="') + len('data-status="'):].split('"', 1)[0]

    assert status_of("sensor.dry_new") == "new"
    assert status_of("sensor.dry_empty") == "none"
    assert status_of("sensor.dry_fail") == "err"
    # Filter-Chips und eingeklappte Details gibt es erst nach einer Vorschau.
    assert 'data-ha-filter="new"' in html and "ha-dry-details" in html

    # Die Seite merkt sich die Vorschau für dieselben Einstellungen (Seitenwechsel).
    again = client.post("/import/ha/source", data={**form}).text
    assert 'data-ha-filter="new"' in again


def test_dry_run_without_selection_reports_error_in_table_page(client, entities) -> None:
    html = client.post("/import/ha/dry-run", data={"history_source": "raw", "period": "hour"}).text
    assert 'id="ha-section"' in html
    assert "ha-dry-details" in html and "mindestens eine Entität" in html
