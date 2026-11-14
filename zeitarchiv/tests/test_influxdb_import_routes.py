"""Tests für app/influxdb_import_routes.py — Formularauswertung, Kontext,
Verfügbarkeit, Vorschau und Import über die echten Routen mit echtem Index und
Archiv im Temp-Ordner. Nur das HTTP zu InfluxDB ist ersetzt
(``influxdb_import._open_lines``)."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request

from app import import_routes
from app.storage import influxdb_import
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
BASE_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


class FakeTemplates:
    def TemplateResponse(self, request, template_name, context):  # noqa: N802
        return template_name, context


@pytest.fixture()
def service(tmp_path) -> import_routes.ImportService:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(
        "sensor.demo", domain="sensor", state_class="measurement", unit="W", friendly_name="Demo"
    )
    index.get_or_create_entity(
        "sensor.other", domain="sensor", state_class="measurement", unit="W", friendly_name="Other"
    )
    deps = import_routes.ImportDependencies(
        data_dir=tmp_path, tz=TZ, index=index, coordinator=StorageCoordinator(),
        templates=FakeTemplates(), app_root_context=None, reports_context=None,
        run_storage_reconciliation=lambda **kwargs: {"entity_ids": kwargs["entity_ids"]},
        symcon_import_dir=tmp_path, csv_import_dir=tmp_path,
        symcon_names_path=tmp_path / "names.json",
        symcon_source_meta_path=tmp_path / "meta.json",
        symcon_scan_cache_path=tmp_path / "scan.json",
    )
    return import_routes.ImportService(replace(deps))


def _endpoint(service, path: str, method: str):
    from fastapi import APIRouter

    router = service.router() if hasattr(service, "router") else APIRouter()
    return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)


def _request(path: str, fields: list[tuple[str, str]], method: str = "POST") -> Request:
    body = urllib.parse.urlencode(fields).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http", "method": method, "path": path,
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
        ],
    }, receive)


def _fake_influx(monkeypatch, handler) -> list[urllib.request.Request]:
    seen: list = []

    def fake(request, verify_tls=True):
        seen.append((request, verify_tls))
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        return iter(handler(params, request).splitlines())

    monkeypatch.setattr(influxdb_import, "_open_lines", fake)
    return seen


def _history(params, request) -> bytes:
    query = params["q"][0]
    if "count(" in query:
        return json.dumps({"results": [{"series": [
            {"name": "W", "tags": {"domain": "sensor", "entity_id": "demo"}, "columns": ["time", "count"], "values": [[0, 3]]}
        ]}]}).encode()
    if "first(" in query or "last(" in query:
        stamp = BASE_MS if "first(" in query else BASE_MS + 120_000
        return json.dumps({"results": [{"series": [
            {"name": "W", "tags": {"domain": "sensor", "entity_id": "demo"}, "columns": ["time", "first"], "values": [[stamp, 1.0]]}
        ]}]}).encode()
    if "'demo'" in query:
        return json.dumps({"results": [{"series": [{"name": "W", "columns": ["time", "value"], "values": [
            [BASE_MS + i * 60_000, 10.0 + i] for i in range(3)
        ]}]}]}).encode()
    return b""


FORM = [
    ("entity_ids", "sensor.demo"), ("range_preset", "max"), ("base_url", "http://influx:8086"),
    ("database", "ha"), ("username", "roberto"), ("password", "geheim"), ("verify_tls", "on"),
]


def test_context_defaults_and_certificate_check_is_on(service) -> None:
    context = service._influx.context()
    assert context["influx_base_url"] == influxdb_import.DEFAULT_BASE_URL
    assert context["influx_database"] == influxdb_import.DEFAULT_DATABASE
    assert context["influx_verify_tls"] is True
    assert context["influx_availability_checked"] is False
    assert {e["entity_id"] for e in context["influx_entities"]} == {"sensor.demo", "sensor.other"}


def test_availability_passes_the_form_through_and_the_cache_never_holds_the_password(service, monkeypatch) -> None:
    seen = _fake_influx(monkeypatch, _history)
    endpoint = _endpoint(service, "/import/influxdb/availability", "POST")
    template, context = asyncio.run(endpoint(_request("/import/influxdb/availability", FORM)))

    assert template == "_influxdb_import_section.html"
    row = next(e for e in context["influx_entities"] if e["entity_id"] == "sensor.demo")
    assert row["has_data"] is True and row["available_count"].startswith("3")
    assert next(e for e in context["influx_entities"] if e["entity_id"] == "sensor.other")["has_data"] is None
    assert context["influx_username"] == "roberto"
    assert "geheim" not in json.dumps(context, default=str)
    assert "geheim" not in repr(service._influx._availability)
    # Zugangsdaten gehen nur im Header, Datenbank und Zertifikatsschalter kommen an.
    request, verify_tls = seen[0]
    assert request.get_header("Authorization") is not None and "geheim" not in request.full_url
    assert "db=ha" in request.full_url and verify_tls is True


def test_a_failed_availability_check_is_shown_and_kept(service, monkeypatch) -> None:
    def broken(params, request):
        raise influxdb_import.InfluxApiError("InfluxDB nicht erreichbar: weg")

    monkeypatch.setattr(influxdb_import, "_open_lines", lambda request, verify_tls=True: broken(None, request))
    endpoint = _endpoint(service, "/import/influxdb/availability", "POST")
    _, context = asyncio.run(endpoint(_request("/import/influxdb/availability", FORM)))
    assert "weg" in context["influx_availability_error"]
    assert service._influx.context(config=service._influx._config(_FormFrom(FORM)))["influx_availability_error"]


class _FormFrom:
    def __init__(self, fields):
        self._fields = fields

    def get(self, key, default=None):
        return next((v for k, v in reversed(self._fields) if k == key), default)

    def getlist(self, key):
        return [v for k, v in self._fields if k == key]


def test_availability_without_a_marked_entity_asks_for_one(service, monkeypatch) -> None:
    seen = _fake_influx(monkeypatch, _history)
    endpoint = _endpoint(service, "/import/influxdb/availability", "POST")
    _, context = asyncio.run(endpoint(_request("/import/influxdb/availability", [("range_preset", "max")])))
    assert context["influx_availability_error"] and seen == []


def test_dry_run_plans_without_writing(service, monkeypatch, tmp_path) -> None:
    _fake_influx(monkeypatch, _history)
    endpoint = _endpoint(service, "/import/influxdb/dry-run", "POST")
    template, context = asyncio.run(endpoint(_request("/import/influxdb/dry-run", FORM)))
    assert template == "_influxdb_import_dry_run.html" and not context["errors"]
    (item,) = context["items"]
    assert item["entity_id"] == "sensor.demo" and item["plan"].rows_to_import + item["plan"].rows_to_merge == 3
    # Eine Vorschau erzeugt keinen Importbericht und schreibt kein Archiv.
    assert not [p for p in tmp_path.rglob("*.json") if "influxdb" in p.read_text(encoding="utf-8")]
    assert service.deps.index.get_entity("sensor.demo") is not None


def test_dry_run_reports_unknown_entities_and_fetch_errors(service, monkeypatch) -> None:
    def broken(request, verify_tls=True):
        raise influxdb_import.InfluxApiError("database not found")

    monkeypatch.setattr(influxdb_import, "_open_lines", broken)
    endpoint = _endpoint(service, "/import/influxdb/dry-run", "POST")
    _, context = asyncio.run(endpoint(_request(
        "/import/influxdb/dry-run", [*FORM, ("entity_ids", "sensor.gibtsnicht")]
    )))
    assert context["items"] == []
    assert any("sensor.gibtsnicht" in e for e in context["errors"])
    assert any("sensor.demo" in e and "database not found" in e for e in context["errors"])


def test_start_imports_entity_by_entity_and_writes_a_report_without_the_password(service, monkeypatch, tmp_path) -> None:
    _fake_influx(monkeypatch, _history)
    endpoint = _endpoint(service, "/import/influxdb/start", "POST")
    template, _ = asyncio.run(endpoint(_request("/import/influxdb/start", FORM)))
    assert template == "_job_progress.html"

    progress = service._influx._progress
    for _ in range(100):
        stand = progress.snapshot()
        if not stand["running"]:
            break
        time.sleep(0.05)
    assert stand["result"]["errors"] == []
    (item,) = stand["result"]["items"]
    assert item["entity_id"] == "sensor.demo"
    assert item["result"].rows_imported + item["result"].rows_merged == 3

    reports = sorted(tmp_path.rglob("*.json"))
    texts = [p.read_text(encoding="utf-8") for p in reports if "influxdb" in p.read_text(encoding="utf-8")]
    assert texts, "kein Importbericht der Quelle influxdb gefunden"
    assert all("geheim" not in t for t in texts)
    assert any('"base_url": "http://influx:8086"' in t and '"database": "ha"' in t for t in texts)


def test_start_without_entities_returns_the_message_instead_of_a_job(service) -> None:
    endpoint = _endpoint(service, "/import/influxdb/start", "POST")
    template, context = asyncio.run(endpoint(_request("/import/influxdb/start", [("range_preset", "max")])))
    assert template == "_influxdb_import_result.html" and context["errors"]


def test_progress_endpoint_is_silent_before_any_run(service) -> None:
    endpoint = _endpoint(service, "/import/influxdb/progress", "GET")
    response = endpoint(_request("/import/influxdb/progress", [], method="GET"))
    assert response.body == b""


# ------------------------------------------------------------ Verbindungstest


def test_connection_check_shows_version_database_and_never_the_password(service, monkeypatch) -> None:
    calls = []

    def fake(base_url, database, auth, verify_tls=True):
        calls.append((base_url, database, auth, verify_tls))
        return "1.8.10", ["sensor", "switch"]

    monkeypatch.setattr(influxdb_import, "check_connection", fake)
    endpoint = _endpoint(service, "/import/influxdb/connection", "POST")
    template, context = asyncio.run(endpoint(_request("/import/influxdb/connection", FORM)))
    assert template == "_connection_check_result.html" and context["ok"] is True
    assert "1.8.10" in context["message"] and "ha" in context["message"] and "2" in context["message"]
    assert "geheim" not in json.dumps(context)
    assert calls == [("http://influx:8086", "ha", ("roberto", "geheim"), True)]


def test_connection_check_reports_the_error_text(service, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise influxdb_import.InfluxApiError("Die Datenbank wurde nicht gefunden")

    monkeypatch.setattr(influxdb_import, "check_connection", fail)
    endpoint = _endpoint(service, "/import/influxdb/connection", "POST")
    template, context = asyncio.run(endpoint(_request("/import/influxdb/connection", FORM)))
    assert context == {"ok": False, "message": "Die Datenbank wurde nicht gefunden"}


def test_vm_connection_check_uses_the_form_address(service, monkeypatch) -> None:
    from app.storage import vm_import

    seen = []
    monkeypatch.setattr(vm_import, "check_connection", lambda base_url: seen.append(base_url) or 7)
    endpoint = _endpoint(service, "/import/vm/connection", "POST")
    template, context = asyncio.run(endpoint(_request("/import/vm/connection", [("base_url", "http://vm:8428")])))
    assert seen == ["http://vm:8428"] and context["ok"] is True and "7" in context["message"]

    def fail(base_url):
        raise vm_import.VmApiError("VictoriaMetrics nicht erreichbar: refused")

    monkeypatch.setattr(vm_import, "check_connection", fail)
    template, context = asyncio.run(endpoint(_request("/import/vm/connection", [])))
    assert context["ok"] is False and "nicht erreichbar" in context["message"]
