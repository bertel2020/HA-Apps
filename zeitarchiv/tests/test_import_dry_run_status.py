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


def test_failed_check_does_not_claim_there_is_no_data(client, entities, monkeypatch) -> None:
    """Ein fehlgeschlagener Abruf liefert leere Ergebnisse; „Keine Daten“ wäre dann falsch."""
    from app import import_routes
    from app.storage import ha_import

    def failing(self, entity_ids, start, end, history_source="raw", period="hour"):
        return {e: ha_import.EntityAvailability(e) for e in entity_ids}, "Supervisor nicht erreichbar"

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_availability", failing)
    html = client.post("/import/ha/availability", data={
        "check_ids": IDS[:1], "history_source": "raw", "period": "hour", "range_preset": "max",
    }).text
    row = html[html.index('data-entity-id="sensor.dry_new"'):]
    assert 'data-status=""' in row[: row.index("</tr>")]


def _vm_influx_post(client, monkeypatch, entities, target: str):
    """Prüft ohne Markierung bei VictoriaMetrics bzw. InfluxDB: die angezeigten Entitäten gehen als check_ids."""
    from app import import_routes, influxdb_import_routes
    from app.storage import ha_import

    seen: dict[str, list[str]] = {}

    def fake(self, entity_ids, *args, **kwargs):
        seen["ids"] = list(entity_ids)
        return {e: ha_import.EntityAvailability(e, first_ts=1.0, last_ts=2.0, count=3) for e in entity_ids}, None

    if target == "vm":
        monkeypatch.setattr(import_routes.ImportService, "_fetch_vm_availability", fake)
        path = "/import/vm/availability"
    else:
        monkeypatch.setattr(influxdb_import_routes.InfluxImportService, "_fetch_availability", fake)
        path = "/import/influxdb/availability"
    html = client.post(path, data={"check_ids": entities[:2], "base_url": "http://127.0.0.1:1", "database": "d"}).text
    return seen, html


@pytest.mark.parametrize("target", ["vm", "influx"])
def test_vm_and_influx_check_without_marking_use_listed_entities(client, entities, monkeypatch, target) -> None:
    seen, html = _vm_influx_post(client, monkeypatch, entities, target)
    assert seen["ids"] == IDS[:2]
    assert "Keine Entität zum Prüfen" not in html
    assert "checked" not in html.split('name="entity_ids"')[1].split(">")[0]


def test_import_tables_have_sortable_headers() -> None:
    """Symcon, VictoriaMetrics und InfluxDB sortieren wie die Home-Assistant-Tabelle."""
    from _paths import TEMPLATES

    for name, keys in (
        ("import.html", ("id", "period", "rows", "unit", "entity")),
        ("_vm_import_section.html", ("entity", "type", "unit", "available")),
        ("_influxdb_import_section.html", ("entity", "type", "unit", "available")),
    ):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for key in keys:
            assert f'data-sort-key="{key}"' in text, (name, key)


def test_connection_checks_can_be_cancelled() -> None:
    """VictoriaMetrics und InfluxDB: Wer sich bei der Adresse vertippt hat, wartet nicht auf das Zeitlimit."""
    from _paths import PAGE_JS, TEMPLATES

    for name, prefix in (("_vm_import_section.html", "vm"), ("_influxdb_import_section.html", "influx")):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert f'data-request-button="{prefix}-connection-btn"' in text
        assert f'id="{prefix}-connection-btn"' in text and f'id="{prefix}-connection-result"' in text
        assert 'onclick="cancelImportRequest(this)"' in text
    assert "htmx.trigger(trigger, 'htmx:abort')" in (PAGE_JS / "import.js").read_text(encoding="utf-8")


def test_availability_checks_can_be_cancelled() -> None:
    from _paths import TEMPLATES

    for name, prefix in (
        ("_ha_import_section.html", "ha"), ("_vm_import_section.html", "vm"), ("_influxdb_import_section.html", "influx"),
    ):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert f'data-request-button="{prefix}-availability-btn"' in text
        # Direkt hinter dem Status-Chip, sonst greift die Regel „.htmx-request + .ha-cancel-check“ nicht.
        chip = text.index(f'id="{prefix}-availability-status"')
        assert text.index("ha-cancel-check", chip) < text.index(f'id="{prefix}-availability-hint"', chip)


def test_vm_and_influx_tables_show_last_import(client, entities) -> None:
    """Letzter Import und der Filter „Nie importiert“ gibt es auch bei VictoriaMetrics und InfluxDB."""
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from app.storage import import_last, import_reports

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    report = import_reports.create(
        data_dir, source_type="csv", started_at=datetime.now(timezone.utc),
        source={"filename": "x.csv", "size_bytes": 0}, configuration={},
        results=[{"entity_id": IDS[0], "rows_imported": 4}], errors=[],
    )
    try:
        html = client.get("/import").text
        for prefix in ("vm", "influx"):
            table = html[html.index(f'id="{prefix}-import-table"'):]
            table = table[: table.index("</table>")]
            row = table[table.index(f'data-entity-id="{IDS[0]}"'):]
            row = row[: row.index("</tr>")]
            assert 'data-sort-last=""' not in row and "CSV" in row
            other = table[table.index(f'data-entity-id="{IDS[1]}"'):]
            assert 'data-sort-last=""' in other[: other.index("</tr>")]
            assert f'id="{prefix}-filter-never"' in html and f'id="{prefix}-mark-filtered"' in html
    finally:
        import_reports.delete(data_dir, report["id"])
        (data_dir / import_last.FILENAME).unlink(missing_ok=True)
