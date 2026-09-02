from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.responses import FileResponse
from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import import_routes
from app.storage import ha_import
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index


TZ = ZoneInfo("Europe/Berlin")


def _service(tmp_path: Path) -> import_routes.ImportService:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(
        "sensor.demo", "sensor", "measurement", "°C", friendly_name="Demo"
    )
    return import_routes.ImportService(import_routes.ImportDependencies(
        data_dir=tmp_path,
        tz=TZ,
        index=index,
        coordinator=StorageCoordinator(),
        templates=None,
        app_root_context=None,
        reports_context=None,
        run_storage_reconciliation=None,
        symcon_import_dir=tmp_path,
        csv_import_dir=tmp_path,
        symcon_names_path=tmp_path / "names.json",
        symcon_source_meta_path=tmp_path / "meta.json",
        symcon_scan_cache_path=tmp_path / "scan.json",
    ))


def test_debug_payload_contains_full_rows_plan_and_discard_reasons(tmp_path: Path) -> None:
    service = _service(tmp_path)
    now = datetime.now(TZ).replace(day=2, hour=12, minute=0, second=0, microsecond=0)
    history = ha_import.HistoryFetchResult(
        rows=[(now.timestamp(), 21.5)],
        skipped=1,
        discarded=[{"reason": "Zustand ist nicht importierbar", "state": "unknown"}],
    )

    debug = service._ha_debug_entity("sensor.demo", history, False)

    assert debug["fetch"]["accepted_count"] == 1
    assert debug["fetch"]["discarded"][0]["state"] == "unknown"
    assert debug["plan"]["months_to_merge"] == [now.strftime("%Y-%m")]
    assert debug["months"][0]["source_rows"][0]["value"] == 21.5
    assert debug["months"][0]["planned_action"] == "hot_buffer_ergaenzen"


def test_debug_zip_contains_json_and_no_implicit_auth_data(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path, filename = service._ha_debug_zip({"token_free": True, "entities": []})
    try:
        assert filename.startswith("zeitarchiv-ha-import-debug-")
        with zipfile.ZipFile(path) as archive:
            payload = json.loads(archive.read("debug.json"))
        assert payload == {"token_free": True, "entities": []}
    finally:
        path.unlink(missing_ok=True)


def test_ha_import_ui_uses_standard_button_and_readable_status_chip() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "templates"
    section = (root / "_ha_import_section.html").read_text(encoding="utf-8")
    dry_run = (root / "_ha_import_dry_run.html").read_text(encoding="utf-8")

    assert 'class="btn" id="ha-availability-btn"' in section
    assert 'hx-post="import/ha/availability"' in section
    assert 'class="btn btn-sm" hx-post="import/ha/availability"' not in section
    assert "Verfügbarkeit erneut prüfen" not in section
    assert 'id="ha-availability-status"' in section
    assert "Prüfung läuft …" in section
    assert 'id="ha-entity-search"' in section
    assert 'name="check_entity_ids"' not in section
    assert "Prüft nur die markierten Entitäten" in section
    assert "availabilityButton.disabled = count === 0" in section
    assert 'class="ha-available-line"' in section
    assert 'formaction="import/ha/debug"' in dry_run


def test_debug_download_endpoint_returns_temporary_zip(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    now = datetime.now(TZ).replace(day=2, hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(
        service,
        "_fetch_ha_history",
        lambda entity_ids, start, end, history_source, period: (
            {"sensor.demo": ha_import.HistoryFetchResult(rows=[(now.timestamp(), 21.5)])},
            [],
        ),
    )
    endpoint = next(
        route.endpoint
        for route in service.router().routes
        if route.path == "/import/ha/debug"
    )
    body = urllib.parse.urlencode({
        "entity_ids": "sensor.demo",
        "range_preset": "10d",
        "history_source": "raw",
    }).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/import/ha/debug",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
        ],
    }, receive)

    response = asyncio.run(endpoint(request))

    assert isinstance(response, FileResponse)
    assert response.path.exists()
    with zipfile.ZipFile(response.path) as archive:
        payload = json.loads(archive.read("debug.json"))
    assert payload["entities"][0]["months"][0]["source_rows"][0]["value"] == 21.5
    asyncio.run(response.background())
    assert not response.path.exists()
