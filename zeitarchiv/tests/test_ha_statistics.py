"""Tests für app/storage/ha_statistics.py — WebSocket-Protokoll
(_ws_call: Auth-Handshake, id-Zuordnung von Antworten) und den Abruf/die
Auswertung von recorder/statistics_during_period-Ergebnissen."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import ha_import, ha_statistics


class _FakeWs:
    """Ersetzt websockets.sync.client.connect() — liefert eine vorgegebene
    Nachrichtenfolge über recv() und zeichnet gesendete Nachrichten auf, ohne
    einen echten Socket zu öffnen."""

    def __init__(self, incoming: list[dict]) -> None:
        self._incoming = list(incoming)
        self.sent: list[dict] = []

    def __enter__(self) -> "_FakeWs":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def recv(self, timeout: float | None = None) -> str:
        if not self._incoming:
            raise TimeoutError("keine weiteren Testnachrichten")
        return json.dumps(self._incoming.pop(0))

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


def _auth_ok_then(*results: dict):
    """Baut die Standard-Handshake-Nachrichten (auth_required, auth_ok) plus
    die übergebenen result-Nachrichten in dieser Reihenfolge."""
    return [{"type": "auth_required"}, {"type": "auth_ok"}, *results]


def test_ws_call_completes_auth_handshake_and_sends_ids(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    fake = _FakeWs(_auth_ok_then(
        {"id": 1, "type": "result", "success": True, "result": {}},
        {"id": 2, "type": "result", "success": True, "result": {}},
    ))
    monkeypatch.setattr(ha_statistics, "connect", lambda *a, **kw: fake)

    results = ha_statistics._ws_call([{"type": "a"}, {"type": "b"}])

    assert [r["id"] for r in results] == [1, 2]
    assert fake.sent[0] == {"type": "auth", "access_token": "test-token"}
    assert fake.sent[1]["id"] == 1 and fake.sent[1]["type"] == "a"
    assert fake.sent[2]["id"] == 2 and fake.sent[2]["type"] == "b"


def test_ws_call_matches_responses_arriving_out_of_order(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    fake = _FakeWs(_auth_ok_then(
        {"id": 2, "type": "result", "success": True, "result": {}},
        {"id": 1, "type": "result", "success": True, "result": {}},
    ))
    monkeypatch.setattr(ha_statistics, "connect", lambda *a, **kw: fake)

    results = ha_statistics._ws_call([{"type": "a"}, {"type": "b"}])

    # Trotz vertauschter Ankunftsreihenfolge in derselben Reihenfolge wie
    # die commands zurückgegeben, nicht in Ankunftsreihenfolge.
    assert results[0]["id"] == 1
    assert results[1]["id"] == 2


def test_ws_call_ignores_unsolicited_messages_without_matching_id(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    fake = _FakeWs(_auth_ok_then(
        {"id": 99, "type": "event", "event": {}},  # keine passende id, muss verworfen werden
        {"id": 1, "type": "result", "success": True, "result": {}},
    ))
    monkeypatch.setattr(ha_statistics, "connect", lambda *a, **kw: fake)

    results = ha_statistics._ws_call([{"type": "a"}])
    assert results[0]["id"] == 1


def test_ws_call_debug_logging_handles_list_shaped_result(monkeypatch) -> None:
    """recorder/list_statistic_ids liefert "result" als Liste, nicht als
    dict wie recorder/statistics_during_period — die reine Debug-Zählung in
    _ws_call() muss beide Formen vertragen (Regressionstest für einen
    AttributeError: 'list' object has no attribute 'values', der in
    Produktion jede Statistik-Verfügbarkeitsprüfung mit HTTP 500 abbrechen
    ließ)."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    fake = _FakeWs(_auth_ok_then(
        {"id": 1, "type": "result", "success": True, "result": [{"statistic_id": "sensor.a"}]},
    ))
    monkeypatch.setattr(ha_statistics, "connect", lambda *a, **kw: fake)

    results = ha_statistics._ws_call([{"type": "recorder/list_statistic_ids"}])
    assert results[0]["result"] == [{"statistic_id": "sensor.a"}]


def test_ws_call_raises_when_auth_fails(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    fake = _FakeWs([{"type": "auth_required"}, {"type": "auth_invalid"}])
    monkeypatch.setattr(ha_statistics, "connect", lambda *a, **kw: fake)

    with pytest.raises(ha_import.HaApiError, match="Authentifizierung"):
        ha_statistics._ws_call([{"type": "a"}])


def test_ws_call_raises_without_supervisor_token(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    fake = _FakeWs([{"type": "auth_required"}])
    monkeypatch.setattr(ha_statistics, "connect", lambda *a, **kw: fake)

    with pytest.raises(ha_import.HaApiError, match="Supervisor"):
        ha_statistics._ws_call([{"type": "a"}])


def test_ws_call_returns_empty_list_for_no_commands() -> None:
    assert ha_statistics._ws_call([]) == []


def test_statistic_meta_supported_reflects_mean_or_sum() -> None:
    assert ha_statistics.StatisticMeta("sensor.x").supported is False
    assert ha_statistics.StatisticMeta("sensor.x", has_mean=True).supported is True
    assert ha_statistics.StatisticMeta("sensor.x", has_sum=True).supported is True


def test_bucket_ts_accepts_ms_epoch_and_iso_string() -> None:
    assert ha_statistics._bucket_ts({"start": 1700000000000}) == 1700000000.0
    assert ha_statistics._bucket_ts({"start": "2023-11-14T22:13:20+00:00"}) == pytest.approx(1700000000.0)
    assert ha_statistics._bucket_ts({"start": "not-a-date"}) is None
    assert ha_statistics._bucket_ts({}) is None
    assert ha_statistics._bucket_ts({"start": float("nan")}) is None


def test_statistic_value_prefers_state_over_mean_and_ignores_sum() -> None:
    # sum (Zählerreset-bereinigte fortlaufende Summe) wird bewusst nicht mehr
    # verwendet — state ist der tatsächliche Rohzählerstand, siehe Docstring
    # von _statistic_value(). Reihenfolge seither: state > mean > None.
    assert ha_statistics._statistic_value({"state": 108.0, "mean": 21.567}) == 108.0
    assert ha_statistics._statistic_value({"mean": 21.567, "sum": 5}) == 21.567
    assert ha_statistics._statistic_value({"sum": 42.1234}) is None
    assert ha_statistics._statistic_value({}) is None
    assert ha_statistics._statistic_value({"state": None, "mean": None, "sum": None}) is None
    assert ha_statistics._statistic_value({"mean": float("inf")}) is None
    assert ha_statistics._statistic_value({"state": float("inf"), "mean": 21.567}) == 21.567


def test_fetch_statistic_meta_filters_to_requested_ids(monkeypatch) -> None:
    def fake_ws_call(commands):
        assert commands == [{"type": "recorder/list_statistic_ids"}]
        return [{
            "id": 1, "type": "result", "success": True,
            "result": [
                {"statistic_id": "sensor.a", "has_mean": True, "has_sum": False, "statistics_unit_of_measurement": "°C"},
                {"statistic_id": "sensor.unwanted", "has_mean": True},
                {"statistic_id": "sensor.b", "has_sum": True},
            ],
        }]

    monkeypatch.setattr(ha_statistics, "_ws_call", fake_ws_call)
    meta = ha_statistics.fetch_statistic_meta(["sensor.a", "sensor.b", "sensor.c"])

    assert meta["sensor.a"].has_mean is True
    assert meta["sensor.a"].unit == "°C"
    assert meta["sensor.b"].has_sum is True
    assert meta["sensor.c"].supported is False  # nicht in der Antwort enthalten
    assert "sensor.unwanted" not in meta


def test_fetch_statistic_meta_empty_input_skips_ws_call(monkeypatch) -> None:
    def fail(*a, **kw):
        raise AssertionError("sollte bei leerer Liste nicht aufgerufen werden")

    monkeypatch.setattr(ha_statistics, "_ws_call", fail)
    assert ha_statistics.fetch_statistic_meta([]) == {}


def test_fetch_statistic_meta_raises_on_ws_failure(monkeypatch) -> None:
    monkeypatch.setattr(ha_statistics, "_ws_call", lambda commands: [
        {"id": 1, "type": "result", "success": False, "error": {"message": "invalid_format"}}
    ])
    with pytest.raises(ha_import.HaApiError, match="invalid_format"):
        ha_statistics.fetch_statistic_meta(["sensor.a"])


def test_fetch_statistics_rows_parses_sorts_and_counts_skipped(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)

    def fake_ws_call(commands):
        assert len(commands) == 1
        command = commands[0]
        assert command["type"] == "recorder/statistics_during_period"
        assert command["statistic_ids"] == ["sensor.temp"]
        return [{
            "id": 1, "type": "result", "success": True,
            "result": {
                "sensor.temp": [
                    {"start": int((start + timedelta(hours=2)).timestamp() * 1000), "mean": 22.0},
                    {"start": int(start.timestamp() * 1000), "mean": 20.0},
                    {"start": int((start + timedelta(hours=1)).timestamp() * 1000)},  # weder mean noch sum
                ]
            },
        }]

    monkeypatch.setattr(ha_statistics, "_ws_call", fake_ws_call)
    result = ha_statistics.fetch_statistics_rows("sensor.temp", start, end, "hour")

    assert [value for _, value in result.rows] == [20.0, 22.0]
    assert result.skipped == 1


def test_fetch_statistics_rows_chunks_long_ranges(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=100)  # > CHUNK_DAYS["hour"] (30) -> mehrere Fenster
    calls = []

    def fake_ws_call(commands):
        calls.append(commands[0])
        return [{"id": 1, "type": "result", "success": True, "result": {}}]

    monkeypatch.setattr(ha_statistics, "_ws_call", fake_ws_call)
    ha_statistics.fetch_statistics_rows("sensor.temp", start, end, "hour")

    assert len(calls) == 4  # 100 Tage / 30-Tage-Fenster aufgerundet


def test_fetch_statistics_rows_raises_when_exceeding_max_rows(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)

    def fake_ws_call(commands):
        return [{
            "id": 1, "type": "result", "success": True,
            "result": {"sensor.temp": [{"start": int(start.timestamp() * 1000), "mean": 1.0}]},
        }]

    monkeypatch.setattr(ha_statistics, "_ws_call", fake_ws_call)
    with pytest.raises(ValueError, match="Datenpunkte"):
        ha_statistics.fetch_statistics_rows("sensor.temp", start, end, "hour", max_rows=0)


def test_fetch_statistics_rows_raises_on_ws_failure(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    monkeypatch.setattr(ha_statistics, "_ws_call", lambda commands: [
        {"id": 1, "type": "result", "success": False, "error": {"message": "unauthorized"}}
    ])
    with pytest.raises(ha_import.HaApiError, match="unauthorized"):
        ha_statistics.fetch_statistics_rows("sensor.temp", start, end, "hour")


def test_fetch_statistics_rows_deduplicates_chunk_boundary_and_records_reason(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=31)
    ts = int(start.timestamp() * 1000)
    monkeypatch.setattr(ha_statistics, "_ws_call", lambda commands: [{
        "id": 1,
        "type": "result",
        "success": True,
        "result": {"sensor.temp": [{"start": ts, "mean": 1.0}]},
    }])

    result = ha_statistics.fetch_statistics_rows("sensor.temp", start, end, "hour")

    assert len(result.rows) == 1
    assert result.discarded[0]["reason"] == "Statistik-Zeitstempel ist doppelt"


def test_fetch_statistics_availability_skips_unsupported_entities(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    period_calls = []

    def fake_ws_call(commands):
        command = commands[0]
        if command["type"] == "recorder/list_statistic_ids":
            return [{
                "id": 1, "type": "result", "success": True,
                "result": [{"statistic_id": "sensor.temp", "has_mean": True}],
            }]
        period_calls.append(command)
        return [{
            "id": 1, "type": "result", "success": True,
            "result": {"sensor.temp": [{"start": int(start.timestamp() * 1000), "mean": 5.0}]},
        }]

    monkeypatch.setattr(ha_statistics, "_ws_call", fake_ws_call)
    result = ha_statistics.fetch_statistics_availability(
        ["sensor.temp", "binary_sensor.door"], start, end, "hour"
    )

    assert result["sensor.temp"].has_data is True
    assert result["sensor.temp"].count == 1
    assert result["binary_sensor.door"].supported is False
    assert result["binary_sensor.door"].has_data is False
    # binary_sensor.door ist nicht unterstützt -> darf nicht in den
    # statistics_during_period-Batch aufgenommen worden sein.
    assert all("binary_sensor.door" not in call["statistic_ids"] for call in period_calls)


def test_fetch_statistics_availability_tracks_first_last_and_count(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    ts1 = int(start.timestamp() * 1000)
    ts2 = int((start + timedelta(hours=2)).timestamp() * 1000)

    def fake_ws_call(commands):
        command = commands[0]
        if command["type"] == "recorder/list_statistic_ids":
            return [{
                "id": 1, "type": "result", "success": True,
                "result": [{"statistic_id": "sensor.temp", "has_mean": True}],
            }]
        return [{
            "id": 1, "type": "result", "success": True,
            "result": {"sensor.temp": [{"start": ts1, "mean": 1.0}, {"start": ts2, "mean": 2.0}]},
        }]

    monkeypatch.setattr(ha_statistics, "_ws_call", fake_ws_call)
    result = ha_statistics.fetch_statistics_availability(["sensor.temp"], start, end, "hour")

    avail = result["sensor.temp"]
    assert avail.count == 2
    assert avail.first_ts == pytest.approx(ts1 / 1000)
    assert avail.last_ts == pytest.approx(ts2 / 1000)
