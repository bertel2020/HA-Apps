"""Import abbrechen: Home-Assistant- und VictoriaMetrics-Import.

Abgebrochen wird zwischen zwei Entitäten, nie mitten in einem Schreibvorgang. Vor dem Schreiben
bleibt alles unberührt; danach bleiben die schon geschriebenen Entitäten erhalten."""

from __future__ import annotations

import threading
import time

import pytest

from _paths import APP  # noqa: F401  (legt den App-Pfad auf sys.path)

A, B = "sensor.cancel_a", "sensor.cancel_b"


@pytest.fixture()
def service(client):
    from app import main

    for entity_id in (A, B):
        main.index.get_or_create_entity(entity_id, "sensor", "measurement", "kW")
    svc = main._import_service
    svc._ha_cancel.clear()
    svc._vm_cancel.clear()
    svc._ha_dry_run_status.clear()
    yield svc
    for progress in (svc._ha_progress, svc._vm_progress):
        progress.started = False
        progress.kind = ""
        progress.result = None
    svc._ha_import_request = None
    svc._ha_cancel.clear()
    svc._vm_cancel.clear()
    for entity_id in (A, B):
        if main.index.get_entity(entity_id) is not None:
            main.index.delete_entity(entity_id)


def _rows() -> list[tuple[float, float]]:
    now = time.time()
    return [(now - 7200, 1.0), (now - 3600, 2.0)]


def _row_count(entity_id: str) -> int:
    from app import main

    return main.index.get_entity(entity_id)["row_count"]


def _ha_result(client) -> str:
    """Wartet auf das Ende des Imports und liefert den Ergebnisblock (er kommt nach dem Neuzeichnen der Sektion)."""
    _wait(client, "/import/ha/progress?watch=import", "ha-section")
    return client.get("/import/ha/progress").text


def _wait(client, url: str, marker: str) -> str:
    for _ in range(200):
        text = client.get(url).text
        if marker in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"{marker} nicht gefunden")


HA_FORM = {"entity_ids": [A, B], "history_source": "raw", "period": "hour", "range_preset": "max"}


def _ha_fetch(cancel_during_fetch: bool):
    def fetch(self, entity_ids, start, end, history_source, period, on_entity=None):
        from app.storage import ha_import

        if cancel_during_fetch:
            self._ha_cancel.set()
        return {i: ha_import.HistoryFetchResult(rows=_rows()) for i in entity_ids}, []

    return fetch


def test_ha_cancel_before_writing_writes_nothing(client, service, monkeypatch) -> None:
    from app import import_routes

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", _ha_fetch(cancel_during_fetch=True))
    client.post("/import/ha/start", data=HA_FORM)
    text = _ha_result(client)
    assert "Es wurde nichts geschrieben" in text
    assert _row_count(A) == 0 and _row_count(B) == 0


def test_ha_cancel_while_writing_keeps_what_is_written(client, service, monkeypatch) -> None:
    from app import import_routes
    from app.storage import symcon_import

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", _ha_fetch(cancel_during_fetch=False))
    real = symcon_import.import_rows

    def import_then_cancel(*args, **kwargs):
        result = real(*args, **kwargs)
        service._ha_cancel.set()
        return result

    monkeypatch.setattr(symcon_import, "import_rows", import_then_cancel)
    client.post("/import/ha/start", data=HA_FORM)
    text = _ha_result(client)
    assert "Bereits geschriebene Entitäten bleiben erhalten" in text
    assert _row_count(A) > 0 and _row_count(B) == 0


def test_ha_import_without_cancel_writes_everything(client, service, monkeypatch) -> None:
    from app import import_routes

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", _ha_fetch(cancel_during_fetch=False))
    client.post("/import/ha/start", data=HA_FORM)
    text = _ha_result(client)
    assert "abgebrochen" not in text.lower()
    assert _row_count(A) > 0 and _row_count(B) > 0


def test_ha_cancel_route_sets_the_flag_only_while_an_import_runs(client, service, monkeypatch) -> None:
    from app import import_routes

    idle = client.post("/import/ha/cancel")
    assert idle.status_code == 200 and not service._ha_cancel.is_set()

    release = threading.Event()

    def slow_fetch(self, entity_ids, start, end, history_source, period, on_entity=None):
        release.wait(5)
        return {}, []

    monkeypatch.setattr(import_routes.ImportService, "_fetch_ha_history", slow_fetch)
    client.post("/import/ha/start", data=HA_FORM)
    for _ in range(100):
        if service._ha_progress.snapshot()["running"]:
            break
        time.sleep(0.02)
    html = client.post("/import/ha/cancel").text
    assert "import/ha/cancel" in html  # der Knopf bleibt sichtbar, bis der Auftrag endet
    assert service._ha_cancel.is_set()
    release.set()
    _wait(client, "/import/ha/progress?watch=import", "ha-section")


def test_ha_cancel_button_is_not_offered_for_the_preview(client, service) -> None:
    service._ha_progress.kind = "dry-run"
    assert service._ha_progress_context()["cancel_url"] is None
    service._ha_progress.kind = ""
    assert service._ha_progress_context()["cancel_url"] == "import/ha/cancel"


VM_FORM = {"entity_ids": [A, B], "range_preset": "max"}


def _vm_fetch(cancel_during_fetch: bool):
    def fetch(self, entity_ids, start, end, base_url, on_entity=None):
        from app.storage import ha_import

        if cancel_during_fetch:
            self._vm_cancel.set()
        return {i: ha_import.HistoryFetchResult(rows=_rows()) for i in entity_ids}, []

    return fetch


def test_vm_cancel_before_writing_writes_nothing(client, service, monkeypatch) -> None:
    from app import import_routes

    monkeypatch.setattr(import_routes.ImportService, "_fetch_vm_history", _vm_fetch(cancel_during_fetch=True))
    client.post("/import/vm/start", data=VM_FORM)
    text = _wait(client, "/import/vm/progress", "Es wurde nichts geschrieben")
    assert "Es wurde nichts geschrieben" in text
    assert _row_count(A) == 0 and _row_count(B) == 0


def test_vm_cancel_while_writing_keeps_what_is_written(client, service, monkeypatch) -> None:
    from app import import_routes
    from app.storage import symcon_import

    monkeypatch.setattr(import_routes.ImportService, "_fetch_vm_history", _vm_fetch(cancel_during_fetch=False))
    real = symcon_import.import_rows

    def import_then_cancel(*args, **kwargs):
        result = real(*args, **kwargs)
        service._vm_cancel.set()
        return result

    monkeypatch.setattr(symcon_import, "import_rows", import_then_cancel)
    client.post("/import/vm/start", data=VM_FORM)
    text = _wait(client, "/import/vm/progress", "Bereits geschriebene Entitäten bleiben erhalten")
    assert "Bereits geschriebene Entitäten bleiben erhalten" in text
    assert _row_count(A) > 0 and _row_count(B) == 0
