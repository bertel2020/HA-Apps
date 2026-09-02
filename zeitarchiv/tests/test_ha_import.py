"""Tests für app/storage/ha_import.py — Wertnormalisierung (dieselben
Regeln wie der Live-Pfad, custom_components/zeitarchiv/events.py) und den
zeitfensterweisen Historienabruf inkl. Fenstergrenzen-Dedup."""

from __future__ import annotations

import io
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import ha_import


def test_parse_state_normalizes_switch_domains_to_one_zero() -> None:
    assert ha_import._parse_state("on", "binary_sensor") == 1.0
    assert ha_import._parse_state("off", "switch") == 0.0
    assert ha_import._parse_state("ON", "input_boolean") == 1.0
    assert ha_import._parse_state("open", "binary_sensor") is None


def test_parse_state_requires_numeric_for_other_domains() -> None:
    assert ha_import._parse_state("21.5", "sensor") == 21.5
    assert ha_import._parse_state("not-a-number", "sensor") is None
    for state in ("nan", "inf", "-inf"):
        assert ha_import._parse_state(state, "sensor") is None


def test_parse_state_ignores_unavailable_and_unknown() -> None:
    for state in ("unavailable", "unknown", "none", "", "  "):
        assert ha_import._parse_state(state, "sensor") is None


def test_token_available_reflects_env_var(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert ha_import.token_available() is False
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    assert ha_import.token_available() is True


def test_fetch_history_rows_drops_synthetic_window_boundary_duplicate(monkeypatch) -> None:
    """HA kann am Fensteranfang einen künstlichen "Zustand zu diesem
    Zeitpunkt" liefern, der mit dem letzten Punkt des Vorfensters
    übereinstimmt — dieser darf beim Zusammenfügen mehrerer Fenster nicht
    zu einem zusätzlichen Messpunkt werden."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=10)
    boundary = start + ha_import.HISTORY_CHUNK

    calls = []

    def fake_get(path: str, params: dict | None = None):
        calls.append(params["end_time"])
        if len(calls) == 1:
            return [[
                {"state": "20.0", "last_changed": start.isoformat()},
                {"state": "21.0", "last_changed": boundary.isoformat()},
            ]]
        return [[
            # Künstlicher Fenster-Startzustand, identisch zum letzten Punkt oben.
            {"state": "21.0", "last_changed": boundary.isoformat()},
            {"state": "22.0", "last_changed": (boundary + timedelta(hours=1)).isoformat()},
        ]]

    monkeypatch.setattr(ha_import, "_get", fake_get)
    result = ha_import.fetch_history_rows("sensor.temp", "sensor", start, end)

    values = [value for _, value in result.rows]
    assert values == [20.0, 21.0, 22.0]
    assert len(calls) >= 2
    assert result.discarded == [{
        "reason": "Zeitstempel ist doppelt oder nicht aufsteigend",
        "state": "21.0",
        "last_changed": boundary.isoformat(),
    }]


def test_fetch_history_rows_skips_non_numeric_and_counts_them(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)

    monkeypatch.setattr(
        ha_import,
        "_get",
        lambda path, params=None: [[
            {"state": "20.0", "last_changed": start.isoformat()},
            {"state": "unavailable", "last_changed": (start + timedelta(minutes=1)).isoformat()},
        ]],
    )
    result = ha_import.fetch_history_rows("sensor.temp", "sensor", start, end)
    assert [v for _, v in result.rows] == [20.0]
    assert result.skipped == 1
    assert result.discarded[0]["reason"] == "Zustand ist nicht importierbar"
    assert result.discarded[0]["state"] == "unavailable"


def test_fetch_history_rows_enforces_max_rows(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)

    monkeypatch.setattr(
        ha_import,
        "_get",
        lambda path, params=None: [[
            {"state": str(i), "last_changed": (start + timedelta(seconds=i)).isoformat()}
            for i in range(5)
        ]],
    )
    with pytest.raises(ValueError):
        ha_import.fetch_history_rows("sensor.temp", "sensor", start, end, max_rows=2)


def test_token_missing_raises_ha_api_error(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(ha_import.HaApiError):
        ha_import.fetch_history_rows(
            "sensor.temp", "sensor", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)
        )


def test_fetch_availability_identifies_entities_by_first_entry_not_position(monkeypatch) -> None:
    """minimal_response liefert entity_id nur im ersten Eintrag je Array —
    fetch_availability() muss sich daran orientieren, nicht an der
    Listenposition (die bei mehreren angefragten Entitäten nicht garantiert
    in Anfragereihenfolge zurückkommt)."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)

    def fake_get(path, params=None):
        assert params["filter_entity_id"] == "sensor.a,binary_sensor.b"
        # Absichtlich in umgekehrter Reihenfolge zur Anfrage zurückgegeben.
        return [
            [
                {"entity_id": "binary_sensor.b", "state": "on", "last_changed": start.isoformat()},
                {"state": "off", "last_changed": (start + timedelta(minutes=30)).isoformat()},
            ],
            [
                {"entity_id": "sensor.a", "state": "20.0", "last_changed": start.isoformat()},
                {"state": "21.0", "last_changed": (start + timedelta(minutes=10)).isoformat()},
            ],
        ]

    monkeypatch.setattr(ha_import, "_get", fake_get)
    result = ha_import.fetch_availability(
        {"sensor.a": "sensor", "binary_sensor.b": "binary_sensor"}, start, end
    )
    assert result["sensor.a"].count == 2
    assert result["sensor.a"].first_ts == start.timestamp()
    assert result["binary_sensor.b"].count == 2
    assert result["binary_sensor.b"].has_data is True


def test_fetch_availability_reports_no_data_for_entity_without_history(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    monkeypatch.setattr(ha_import, "_get", lambda path, params=None: [[]])
    result = ha_import.fetch_availability({"sensor.a": "sensor"}, start, end)
    assert result["sensor.a"].has_data is False
    assert result["sensor.a"].count == 0
    assert result["sensor.a"].first_ts is None


def test_get_surfaces_http_error_status_and_message(monkeypatch) -> None:
    """HTTPError ist eine Unterklasse von URLError — muss VOR dem generischen
    URLError-Zweig abgefangen werden, sonst verschwindet der tatsächliche
    Statuscode/die Fehlermeldung (z. B. 401 bei fehlender homeassistant_api-
    Berechtigung) hinter der generischen "nicht erreichbar"-Meldung."""
    body = io.BytesIO(b'{"message": "401: Unauthorized"}')

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(ha_import.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ha_import.HaApiError) as excinfo:
        ha_import._get("/states")
    assert "401" in str(excinfo.value)
    assert "Unauthorized" in str(excinfo.value)


def test_get_handles_http_error_with_non_json_body(monkeypatch) -> None:
    body = io.BytesIO(b"<html>Bad Gateway</html>")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, body)

    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    monkeypatch.setattr(ha_import.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ha_import.HaApiError) as excinfo:
        ha_import._get("/states")
    assert "502" in str(excinfo.value)


def test_get_distinguishes_connection_error_from_json_error(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")

    def raise_connection_error(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(ha_import.urllib.request, "urlopen", raise_connection_error)
    with pytest.raises(ha_import.HaApiError, match="nicht erreichbar"):
        ha_import._get("/states")


def test_fetch_availability_batches_entities_by_batch_size(monkeypatch) -> None:
    calls = []

    def fake_get(path, params=None):
        calls.append(params["filter_entity_id"].split(","))
        return [[]]

    monkeypatch.setattr(ha_import, "_get", fake_get)
    monkeypatch.setattr(ha_import, "ENTITY_BATCH_SIZE", 2)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    entity_domains = {f"sensor.e{i}": "sensor" for i in range(5)}
    ha_import.fetch_availability(entity_domains, start, end)
    assert [len(batch) for batch in calls] == [2, 2, 1]
