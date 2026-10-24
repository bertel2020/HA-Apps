"""CSV-Import mehrerer Spalten derselben Datei in verschiedene Entitäten
(Vorschau, Dry Run, Import) — durch die echten Routen, mit hochgeladener Datei."""

from __future__ import annotations

import time

import pytest

from app.i18n import N_
from app.main import _import_service, index
from app.progress import JobProgress


@pytest.fixture(autouse=True, scope="module")
def _leave_no_trace(client):
    """Die Tests hier lassen einen Auftrag und eine hochgeladene Datei zurück; andere
    Tests (test_long_running_feedback) setzen voraus, dass der Fortschritt still ist."""
    yield
    client.post("/import/csv/delete")
    _import_service._csv_progress = JobProgress("csv-import", unit=N_("Zeilen"), label=N_("CSV-Import"))


def _upload(client, text: str) -> None:
    client.post("/import/csv/delete")
    response = client.post("/import/csv/upload", files={"file": ("werte.csv", text.encode("utf-8"), "text/csv")})
    assert response.status_code == 200


def _wait_for_result(client, kind_marker: str) -> str:
    for _ in range(60):
        html = client.get("/import/csv/progress").text
        if kind_marker in html:
            return html
        time.sleep(0.25)
    raise AssertionError(html)


FORM = {"delimiter": ";", "has_header": "on", "ts_col": "0", "ts_format": "de_date", "custom_pattern": ""}
CSV = "Zeitraum;PV;Netz\n15.01.2026;84,1;837,7\n15.02.2026;156,5;668,2\n15.03.2026;891,2;64,5\n"


def test_preview_adds_and_removes_extra_rows(client) -> None:
    _upload(client, CSV)
    added = client.post("/import/csv/preview", data={**FORM, "value_col": "1", "add_extra": "1"}).text
    assert 'name="extra_col"' in added and 'name="extra_entity"' in added
    removed = client.post(
        "/import/csv/preview",
        data={**FORM, "value_col": "1", "extra_col": "2", "extra_entity": "sensor.mc_b", "remove_extra": "0"},
    ).text
    assert 'name="extra_entity"' not in removed


def test_dry_run_and_import_write_each_column_to_its_entity(client) -> None:
    index.get_or_create_entity("sensor.mc_pv", "sensor", "total_increasing", "kWh")
    index.get_or_create_entity("sensor.mc_netz", "sensor", "total_increasing", "kWh")
    _upload(client, CSV)
    data = {**FORM, "value_col": "1", "entity_id": "sensor.mc_pv", "extra_col": "2", "extra_entity": "sensor.mc_netz"}

    client.post("/import/csv/dry-run", data=data)
    html = _wait_for_result(client, "Vorschau (nichts wurde geschrieben)")
    assert "werte.csv · PV → sensor.mc_pv" in html
    assert "werte.csv · Netz → sensor.mc_netz" in html

    client.post("/import/csv/start", data=data)
    _wait_for_result(client, "sensor.mc_netz")
    for entity_id in ("sensor.mc_pv", "sensor.mc_netz"):
        row = index.get_entity(entity_id)
        assert row["row_count"] == 3, entity_id
        assert row["first_ts"] is not None, entity_id


def test_import_refuses_a_row_without_entity_and_repeated_entities(client) -> None:
    index.get_or_create_entity("sensor.mc_pv", "sensor", "total_increasing", "kWh")
    _upload(client, CSV)
    base = {**FORM, "value_col": "1", "entity_id": "sensor.mc_pv"}
    leer = client.post("/import/csv/start", data={**base, "extra_col": "2", "extra_entity": ""}).text
    assert "Bitte für jede Spalte eine Ziel-Entität auswählen." in leer
    doppelt = client.post("/import/csv/start", data={**base, "extra_col": "2", "extra_entity": "sensor.mc_pv"}).text
    assert "Jede Ziel-Entität darf nur einmal vorkommen." in doppelt
