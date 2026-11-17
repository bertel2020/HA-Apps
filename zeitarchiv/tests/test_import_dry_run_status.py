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
    # Der Dienst ist prozessweit: Vorschau- und Prüfstände dürfen nicht von Test zu Test wandern.
    main._import_service._ha_dry_run_status.clear()
    main._import_service._ha_availability_cache.entries.clear()
    yield IDS
    main._import_service._ha_dry_run_status.clear()
    main._import_service._ha_availability_cache.entries.clear()
    for entity_id in IDS:
        if main.index.get_entity(entity_id) is not None:
            main.index.delete_entity(entity_id)


def _run_dry_run(client, form):
    """Startet die Vorschau (Hintergrundauftrag) und wartet auf die fertige Sektion."""
    started = client.post("/import/ha/dry-run", data=form)
    assert "job-progress" in started.text  # sofort die Fortschrittsanzeige, keine Tabelle
    for _ in range(100):
        polled = client.get("/import/ha/progress?watch=dry-run")
        if 'id="ha-section"' in polled.text:
            return polled
        time.sleep(0.05)
    raise AssertionError("Vorschau wurde nicht fertig")


def _fake_fetch(self, entity_ids, start, end, history_source, period, on_entity=None):
    from app.storage import ha_import

    now = time.time()
    if on_entity:
        for nummer, entity_id in enumerate(entity_ids):
            on_entity(nummer, entity_id)
    fetched = {
        "sensor.dry_new": ha_import.HistoryFetchResult(rows=[(now - 3600, 1.0), (now - 1800, 2.0)]),
        "sensor.dry_empty": ha_import.HistoryFetchResult(rows=[]),
    }
    return fetched, ["sensor.dry_fail: Timeout"]


def test_dry_run_fills_status_column_and_filters(client, entities, monkeypatch) -> None:
    from app import import_routes

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", _fake_fetch)
    form = {"entity_ids": IDS, "history_source": "raw", "period": "hour", "range_preset": "max"}
    response = _run_dry_run(client, form)
    html = response.text
    # Die fertige Sektion ersetzt die ganze alte, nicht nur die Fortschrittsanzeige.
    assert response.headers["HX-Retarget"] == "#ha-section" and response.headers["HX-Reswap"] == "outerHTML"

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


def test_finished_preview_is_not_swapped_in_again_on_plain_page_load(client, entities, monkeypatch) -> None:
    """Das Neuladen der Seite fragt das Poll-Ziel ohne ?watch: nach einer Vorschau kommt nichts zurück."""
    from app import import_routes

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", _fake_fetch)
    _run_dry_run(client, {"entity_ids": IDS, "history_source": "raw", "period": "hour", "range_preset": "max"})
    assert client.get("/import/ha/progress").text == ""


def test_dry_run_without_selection_reports_error_in_table_page(client, entities) -> None:
    html = client.post("/import/ha/dry-run", data={"history_source": "raw", "period": "hour"}).text
    assert 'id="ha-section"' in html
    assert "ha-dry-details" in html and "mindestens eine Entität" in html


def test_check_without_marking_covers_listed_entities_and_marks_nothing(client, entities, monkeypatch) -> None:
    """Prüfen ohne Markierung: Die Seite schickt die angezeigten Entitäten als check_ids.
    Das Ergebnis füllt die Status-Spalte („Daten vorhanden“ / „Keine Daten“), die Auswahl bleibt leer."""
    from app import import_routes
    from app.storage import ha_import

    seen: dict[str, list[str]] = {}

    def fake_availability(self, entity_ids, start, end, history_source="raw", period="hour"):
        seen["ids"] = list(entity_ids)
        return {
            "sensor.dry_new": ha_import.EntityAvailability("sensor.dry_new", first_ts=1.0, last_ts=2.0, count=5),
            "sensor.dry_empty": ha_import.EntityAvailability("sensor.dry_empty"),
        }, None

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_availability", fake_availability)
    form = {"check_ids": IDS[:2], "history_source": "raw", "period": "hour", "range_preset": "max"}
    html = client.post("/import/ha/availability", data=form).text

    def row(entity_id: str) -> str:
        part = html[html.index(f'data-entity-id="{entity_id}"'):]
        return part[: part.index("</tr>")]

    assert seen["ids"] == IDS[:2]
    assert 'data-status="found"' in row("sensor.dry_new")
    assert 'data-status="none"' in row("sensor.dry_empty")
    assert 'data-status=""' in row("sensor.dry_fail")  # nicht geprüft
    # Nichts ist markiert, obwohl geprüft wurde.
    assert "checked" not in row("sensor.dry_new").split('class="ha-mark-checkbox"')[1].split(">")[0]
    assert 'data-ha-filter="found"' in html


def test_check_with_nothing_to_check_reports_it(client, entities) -> None:
    html = client.post("/import/ha/availability", data={"history_source": "raw", "period": "hour"}).text
    assert "Keine Entität zum Prüfen" in html
