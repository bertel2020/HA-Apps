"""Tests für den HA-Verfügbarkeits-Cache (_HaAvailabilityCache) in
import_routes.py — Ergebnisse eines "Verfügbarkeit prüfen"-Klicks müssen
einen Seitenwechsel überleben (GET /import, Quellen-/Perioden-Wechsel über
/import/ha/source), inkl. Zeitstempel-Anzeige und Veraltet-Warnung nach
HA_AVAILABILITY_STALE_SECONDS."""

from __future__ import annotations

import asyncio
import sys
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import import_routes
from app.storage import ha_import
from app.storage.index import Index


@pytest.fixture()
def service(tmp_path) -> import_routes.ImportService:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(
        "sensor.demo", domain="sensor", state_class="measurement", unit="°C", friendly_name="Demo"
    )
    deps = import_routes.ImportDependencies(
        data_dir=tmp_path, tz=ZoneInfo("Europe/Berlin"), index=index,
        coordinator=None, templates=None, app_root_context=None,
        reports_context=None, run_storage_reconciliation=None,
        symcon_import_dir=tmp_path, csv_import_dir=tmp_path,
        symcon_names_path=tmp_path / "names.json",
        symcon_source_meta_path=tmp_path / "meta.json",
        symcon_scan_cache_path=tmp_path / "scan.json",
    )
    return import_routes.ImportService(deps)


def test_context_shows_no_checked_at_before_any_check(service) -> None:
    ctx = service._ha_import_context(history_source="raw", period="hour")
    assert ctx["ha_availability_checked"] is False
    assert ctx["ha_availability_checked_at_label"] is None
    assert ctx["ha_availability_stale"] is False


def test_cache_store_is_visible_on_a_later_context_call(service) -> None:
    availability = {"sensor.demo": ha_import.EntityAvailability("sensor.demo", first_ts=1.0, last_ts=2.0, count=3)}
    service._ha_cache_store("raw", "hour", availability, None)

    ctx = service._ha_import_context(history_source="raw", period="hour")
    assert ctx["ha_availability_checked"] is True
    assert ctx["ha_availability_checked_at_label"] is not None
    assert ctx["ha_availability_stale"] is False
    assert ctx["ha_entities"][0]["has_data"] is True


def test_cache_is_kept_separate_per_source_and_period(service) -> None:
    service._ha_cache_store("raw", "hour", {}, None)

    assert service._ha_import_context(history_source="stats", period="hour")["ha_availability_checked"] is False
    assert service._ha_import_context(history_source="stats", period="day")["ha_availability_checked"] is False
    assert service._ha_import_context(history_source="raw", period="hour")["ha_availability_checked"] is True


def test_failed_check_is_cached_and_shown_on_reload(service) -> None:
    service._ha_cache_store("raw", "hour", {}, "Supervisor ist in dieser Umgebung nicht verfügbar")

    ctx = service._ha_import_context(history_source="raw", period="hour")
    assert ctx["ha_availability_checked"] is True
    assert ctx["ha_availability_error"] == "Supervisor ist in dieser Umgebung nicht verfügbar"


def test_stale_flag_set_after_threshold(service, monkeypatch) -> None:
    availability = {"sensor.demo": ha_import.EntityAvailability("sensor.demo")}
    service._ha_cache_store("raw", "hour", availability, None)

    entry = service._ha_cache_lookup("raw", "hour")
    entry["checked_at"] -= import_routes.HA_AVAILABILITY_STALE_SECONDS + 1

    ctx = service._ha_import_context(history_source="raw", period="hour")
    assert ctx["ha_availability_stale"] is True


def test_stale_flag_not_set_just_under_threshold(service) -> None:
    availability = {"sensor.demo": ha_import.EntityAvailability("sensor.demo")}
    service._ha_cache_store("raw", "hour", availability, None)

    entry = service._ha_cache_lookup("raw", "hour")
    entry["checked_at"] -= import_routes.HA_AVAILABILITY_STALE_SECONDS - 5

    ctx = service._ha_import_context(history_source="raw", period="hour")
    assert ctx["ha_availability_stale"] is False


def test_availability_endpoint_checks_only_marked_entities(service, monkeypatch) -> None:
    service.deps.index.get_or_create_entity(
        "sensor.other", domain="sensor", state_class="measurement", unit="W", friendly_name="Other"
    )
    checked: list[str] = []

    def fake_fetch(entity_ids, start, end, history_source, period):
        checked.extend(entity_ids)
        return {
            entity_id: ha_import.EntityAvailability(entity_id, first_ts=1, last_ts=2, count=3)
            for entity_id in entity_ids
        }, None

    class FakeTemplates:
        def TemplateResponse(self, request, template_name, context):
            return context

    routed_service = import_routes.ImportService(
        replace(service.deps, templates=FakeTemplates())
    )
    monkeypatch.setattr(routed_service, "_fetch_ha_availability", fake_fetch)
    endpoint = next(
        route.endpoint
        for route in routed_service.router().routes
        if route.path == "/import/ha/availability"
    )
    body = urllib.parse.urlencode({
        "entity_ids": "sensor.demo",
        "range_preset": "10d",
        "history_source": "raw",
        "period": "hour",
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
        "path": "/import/ha/availability",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
        ],
    }, receive)

    context = asyncio.run(endpoint(request))

    assert checked == ["sensor.demo"]
    assert context["ha_selected_ids"] == {"sensor.demo"}
    entities = {row["entity_id"]: row for row in context["ha_entities"]}
    assert entities["sensor.demo"]["has_data"] is True
    assert entities["sensor.other"]["has_data"] is None
