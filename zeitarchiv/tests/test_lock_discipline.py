"""Sperrdisziplin der Routen (Code-Review Phase 2).

Ein Lesezugriff oder ein Export darf weder die Aufnahme aus HA anhalten noch eine Wartung auf Dauer
blockieren. Der Coordinator bevorzugt wartende exclusive()-Aufrufe: ein einziger solcher Aufruf hinter einem
langen Import hält jede neue Entitätsoperation an.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from starlette.testclient import TestClient

    from app import api_routes, export_data, report_routes
    from app.storage import cleanup, hotbuffer, import_reports
    from app.storage.coordinator import CoordinatorBusy, StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/pyarrow fehlt")

TZ = ZoneInfo("Europe/Berlin")


def _hold_entity(coordinator: StorageCoordinator) -> tuple[threading.Event, threading.Thread]:
    """Hält `entity()` in einem eigenen Thread — wie ein langer Import."""
    started, release = threading.Event(), threading.Event()

    def run() -> None:
        with coordinator.entity("sensor.import"):
            started.set()
            release.wait(timeout=30)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    return release, thread


def _within(seconds: float, func):
    """Führt `func` in einem Thread aus und liefert (Ergebnis, fertig?) — hängt `func`, bleibt der Test nicht stehen."""
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    return outcome, not thread.is_alive()


# ---------------------------------------------------------------- Reports


@pytest.fixture
def reports(tmp_path: Path):
    coordinator = StorageCoordinator()
    deps = report_routes.ReportDependencies(
        data_dir=tmp_path, tz=TZ, coordinator=coordinator,
        templates=Jinja2Templates(directory=str(tmp_path)),
        app_root_context=lambda _request: {"app_root": ""},
    )
    service = report_routes.ReportService(deps)
    app = FastAPI()
    app.include_router(service.router())
    report = import_reports.create(
        tmp_path, source_type="csv", started_at=datetime.now(TZ), source={}, configuration={}, results=[], errors=[],
    )
    with TestClient(app) as client:
        yield client, service, coordinator, report["id"]


def test_report_list_and_download_do_not_wait_for_a_running_import(reports) -> None:
    client, service, coordinator, report_id = reports
    release, holder = _hold_entity(coordinator)
    try:
        listed, finished = _within(3, service.context)
        assert finished, "Die Importliste hängt an der laufenden Entitätsoperation"
        assert [row["id"] for row in listed["value"]["reports"]] == [report_id]

        downloaded, finished = _within(3, lambda: client.get(f"/reports/{report_id}/download"))
        assert finished, "Der Report-Download hängt an der laufenden Entitätsoperation"
        assert downloaded["value"].status_code == 200
    finally:
        release.set()
        holder.join(timeout=5)


def test_report_delete_answers_busy_instead_of_queueing_behind_an_import(reports, monkeypatch) -> None:
    client, _service, coordinator, report_id = reports
    monkeypatch.setattr(report_routes, "UI_EXCLUSIVE_TIMEOUT_SECONDS", 0.2)
    release, holder = _hold_entity(coordinator)
    try:
        with pytest.raises(CoordinatorBusy):
            client.post(f"/reports/{report_id}/delete", follow_redirects=False)
        with pytest.raises(CoordinatorBusy):
            client.post("/reports/delete-all", follow_redirects=False)
    finally:
        release.set()
        holder.join(timeout=5)
    # Nach dem Import geht es wieder.
    response = client.post(f"/reports/{report_id}/delete", follow_redirects=False)
    assert response.status_code == 303


def test_deleting_an_already_deleted_report_is_not_an_error(tmp_path: Path) -> None:
    report = import_reports.create(
        tmp_path, source_type="csv", started_at=datetime.now(TZ), source={}, configuration={}, results=[], errors=[],
    )
    path = import_reports.download_path(tmp_path, report["id"])
    assert import_reports.delete(tmp_path, report["id"]) is True
    assert path is not None and not path.exists()
    assert import_reports.delete(tmp_path, report["id"]) is False


# ---------------------------------------------------------------- Export


@pytest.fixture
def export_setup(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    entity_id = "sensor.export"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    # Im laufenden Monat, denn nur dort liest der Export die Hot-Datei; 12 Blöcke à 5000 Zeilen.
    month_start = datetime.now(TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = [(month_start + 60 + i, float(i)) for i in range(60_000)]
    hotbuffer.append_many(tmp_path, entity_id, rows, TZ)
    index.record_write(entity_id, rows[0][0])
    index.record_write(entity_id, rows[-1][0])
    coordinator = StorageCoordinator()
    yield tmp_path, index, index.get_entity(entity_id), coordinator
    index.close()


def _chunks(setup, max_rows: int = 5_000_000):
    data_dir, index, entity, coordinator = setup
    return export_data.csv_chunks(
        data_dir, index, entity, None, None, "raw", TZ, coordinator, max_rows, export_data.CsvFormat(),
    )


def _exclusive_is_available(coordinator: StorageCoordinator) -> bool:
    try:
        with coordinator.exclusive(timeout=2):
            return True
    except CoordinatorBusy:
        return False


def test_export_stream_advanced_from_different_threads_releases_the_lock(export_setup) -> None:
    """Die Entitätssperre gehört dem Thread, der sie nimmt. Starlette schaltet den Generator in wechselnden
    Worker-Threads weiter; früher scheiterte die Freigabe dort ("cannot release un-acquired lock"), und
    exclusive() wartete danach für immer."""
    _data_dir, _index, _entity, coordinator = export_setup
    stream = _chunks(export_setup)
    text, calls = [], 0
    # Jeder Schritt in einem eigenen Thread, der bis zum Ende am Leben bleibt: CPython vergibt die Kennung eines
    # beendeten Threads neu, ein RLock hielte den nächsten Thread dann fälschlich für seinen Besitzer.
    parked = threading.Event()
    try:
        while True:
            outcome, done = {}, threading.Event()

            def step(outcome=outcome, done=done) -> None:
                try:
                    outcome["value"] = next(stream)
                except BaseException as exc:  # noqa: BLE001
                    outcome["error"] = exc
                done.set()
                parked.wait(timeout=30)

            threading.Thread(target=step, daemon=True).start()
            assert done.wait(timeout=10)
            if isinstance(outcome.get("error"), StopIteration):
                break
            assert "error" not in outcome, outcome["error"]
            text.append(outcome["value"])
            calls += 1
    finally:
        parked.set()
    assert calls >= 12
    assert len("".join(text).splitlines()) == 60_001  # Kopfzeile + Werte
    assert _exclusive_is_available(coordinator)


def test_abandoned_export_releases_the_lock(export_setup) -> None:
    """Bricht der Client den Download ab, darf die Sperre nicht an einem toten Generator hängen."""
    _data_dir, _index, _entity, coordinator = export_setup
    stream = _chunks(export_setup)
    next(stream)
    stream.close()
    assert _exclusive_is_available(coordinator)


def test_export_holds_the_entity_lock_while_reading(export_setup) -> None:
    _data_dir, _index, _entity, coordinator = export_setup
    stream = _chunks(export_setup)
    next(stream)
    try:
        with pytest.raises(CoordinatorBusy):
            with coordinator.exclusive(timeout=0.3):
                pass
    finally:
        stream.close()


def test_export_limit_error_reaches_the_caller_and_frees_the_lock(export_setup) -> None:
    _data_dir, _index, _entity, coordinator = export_setup
    with pytest.raises(cleanup.ResultLimitExceeded):
        list(_chunks(export_setup, max_rows=100))
    assert _exclusive_is_available(coordinator)


# ---------------------------------------------------------------- KI


@pytest.fixture
def ai_client(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.temperatur", "sensor", "measurement", "°C")
    coordinator = StorageCoordinator()
    calls: list[list[str]] = []
    original = coordinator.entities

    def spy(entity_ids, *args, **kwargs):
        calls.append(list(entity_ids))
        return original(entity_ids, *args, **kwargs)

    coordinator.entities = spy  # type: ignore[method-assign]
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path, index=index, tz=TZ, coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator),
        api_token=lambda: "test-token", app_version="0.0.0-test",
        collect_notices=lambda: [], latest_backup=lambda: None,
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    with TestClient(app) as client:
        yield client, index, calls
    index.close()


_SUMMARY = {"entity_ids": ["sensor.temperatur"], "period": "day"}


def test_unauthenticated_ai_call_takes_no_storage_lock(ai_client) -> None:
    client, _index, calls = ai_client
    assert client.post("/api/ai/get_summary", json=_SUMMARY).status_code == 401
    assert client.post(
        "/api/ai/get_summary", json=_SUMMARY, headers={"Authorization": "Bearer falsch"}
    ).status_code == 401
    assert calls == []


def test_switched_off_ai_call_takes_no_storage_lock(ai_client) -> None:
    client, _index, calls = ai_client
    response = client.post(
        "/api/ai/get_summary", json=_SUMMARY, headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 403
    assert calls == []


def test_authorized_ai_call_locks_exactly_the_requested_entities(ai_client) -> None:
    client, index, calls = ai_client
    index.set_setting("ai_enabled", "1")
    response = client.post(
        "/api/ai/get_summary", json=_SUMMARY, headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200
    assert calls == [["sensor.temperatur"]]


# ---------------------------------------------------------------- Quelltext-Vertrag


def _exclusive_calls(path: Path, function_names: set[str] | None = None) -> list[tuple[str, bool]]:
    """(Funktion, hat timeout=) je `.exclusive(...)`-Aufruf, optional nur in den genannten Funktionen."""
    import ast

    found: list[tuple[str, bool]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function_names is not None and function.name not in function_names:
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "exclusive"
            ):
                found.append((function.name, any(keyword.arg == "timeout" for keyword in node.keywords)))
    return found


def test_ui_routes_never_wait_unbounded_for_exclusive_access() -> None:
    """Ein wartender exclusive()-Aufruf hält jede neue Entitätsoperation an. In UI-Routen darum nur mit
    Zeitbudget (503 statt Stau); ohne Timeout bleiben allein Hintergrund-Jobs und Wartung."""
    import _paths

    reports = _exclusive_calls(_paths.APP / "report_routes.py")
    assert reports and all(has_timeout for _name, has_timeout in reports), reports
    main_routes = _exclusive_calls(
        _paths.APP / "main.py", {"backup_delete", "backup_delete_all", "backup_rollback_delete"}
    )
    assert len(main_routes) == 3 and all(has_timeout for _name, has_timeout in main_routes), main_routes


def test_read_only_routes_take_no_exclusive_access() -> None:
    import _paths

    assert _exclusive_calls(_paths.APP / "main.py", {"settings_diagnostics_download", "statistik_view"}) == []
    assert _exclusive_calls(_paths.APP / "report_routes.py", {"context", "report_detail", "report_download"}) == []
