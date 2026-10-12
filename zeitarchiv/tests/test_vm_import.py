"""Tests für app/storage/vm_import.py — Export-Abruf (Serien-Zusammenführung,
Fenster, Dedup), gebündelte Verfügbarkeit und Fehlerabbildung. Das HTTP ist
über ``vm_import._open`` ersetzt: kein Netz, keine echte VictoriaMetrics."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from app.storage import vm_import

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _export_line(timestamps_ms: list[int], values: list, **tags) -> bytes:
    return json.dumps({"metric": {"__name__": "x", **tags}, "values": values, "timestamps": timestamps_ms}).encode()


def _fake_open(monkeypatch, handler) -> list[urllib.request.Request]:
    """handler(path, params) -> bytes; liefert die aufgezeichneten Requests."""
    seen: list = []

    def fake(request):
        seen.append(request)
        parsed = urllib.parse.urlsplit(request.full_url)
        return handler(parsed.path, urllib.parse.parse_qs(parsed.query))

    monkeypatch.setattr(vm_import, "_open", fake)
    return seen


def test_normalize_base_url() -> None:
    assert vm_import.normalize_base_url(None) == vm_import.DEFAULT_BASE_URL
    assert vm_import.normalize_base_url("  ") == vm_import.DEFAULT_BASE_URL
    assert vm_import.normalize_base_url("192.168.1.2:8428/") == "http://192.168.1.2:8428"
    assert vm_import.normalize_base_url("https://vm.local") == "https://vm.local"
    with pytest.raises(vm_import.VmApiError):
        vm_import.normalize_base_url("ftp://vm.local")


def test_fetch_history_rows_merges_series_and_selects_value_series(monkeypatch) -> None:
    """Ein Tag-Wechsel (z. B. umbenannter friendly_name) erzeugt eine zweite
    Serie desselben Namens — beide gehören zu einer Zeitreihe, sortiert."""
    base = int(START.timestamp() * 1000)

    def handler(path, params):
        assert path == "/api/v1/export"
        return b"\n".join([
            _export_line([base + 2000, base + 3000], [2.0, 3.0], friendly_name="neu"),
            _export_line([base, base + 1000], [0.0, 1.0], friendly_name="alt"),
            b"",
        ])

    seen = _fake_open(monkeypatch, handler)
    result = vm_import.fetch_history_rows("sensor.temp", START, START + timedelta(days=1), "http://vm:8428")
    assert result.rows == [(base / 1000 + i, float(i)) for i in range(4)]
    assert result.skipped == 0
    # Gematcht wird über den Metriknamen mit voller Entity-ID, nicht über den Tag.
    assert params_of(seen[0])["match[]"] == ['{__name__="sensor.temp_value"}']


def params_of(request) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)


def test_fetch_history_rows_collapses_duplicate_timestamps_and_skips_bad_values(monkeypatch) -> None:
    base = int(START.timestamp() * 1000)
    _fake_open(monkeypatch, lambda path, params: _export_line(
        [base, base, base + 1000, base + 2000], [5.0, 6.0, "x", float("nan")]
    ).replace(b"NaN", b'"nan"'))
    result = vm_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))
    assert result.rows == [(base / 1000, 5.0)]  # erster Punkt gewinnt
    assert result.skipped == 2  # "x" und nan
    assert len(result.discarded) == 3  # zwei verworfene Werte + ein Duplikat


def test_fetch_history_rows_rounds_values_like_the_ha_import(monkeypatch) -> None:
    base = int(START.timestamp() * 1000)
    _fake_open(monkeypatch, lambda path, params: _export_line([base], [21.123456]))
    result = vm_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))
    assert result.rows[0][1] == 21.123


def test_fetch_history_rows_queries_in_chunks_and_covers_the_whole_range(monkeypatch) -> None:
    end = START + vm_import.HISTORY_CHUNK * 2 + timedelta(days=5)
    seen = _fake_open(monkeypatch, lambda path, params: b"")
    result = vm_import.fetch_history_rows("sensor.t", START, end)
    assert result.rows == []
    windows = [(float(params_of(r)["start"][0]), float(params_of(r)["end"][0])) for r in seen]
    assert len(windows) == 3
    assert windows[0][0] == START.timestamp() and windows[-1][1] == end.timestamp()
    # Fenster schließen lückenlos aneinander an.
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))


def test_fetch_history_rows_enforces_the_row_limit(monkeypatch) -> None:
    base = int(START.timestamp() * 1000)
    _fake_open(monkeypatch, lambda path, params: _export_line([base + i for i in range(5)], [1.0] * 5))
    with pytest.raises(ValueError):
        vm_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1), max_rows=4)


def test_fetch_history_rows_rejects_an_invalid_export(monkeypatch) -> None:
    _fake_open(monkeypatch, lambda path, params: b"das ist kein json")
    with pytest.raises(vm_import.VmApiError):
        vm_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))


def _instant(rows: list[tuple[str, str, float]]) -> bytes:
    return json.dumps({"status": "success", "data": {"result": [
        {"metric": {"domain": d, "entity_id": e}, "value": [0, str(v)]} for d, e, v in rows
    ]}}).encode()


def test_fetch_availability_groups_by_domain_and_entity_id_tags(monkeypatch) -> None:
    """Rollup-Funktionen verwerfen den Metriknamen — die volle Entity-ID wird
    aus den Tags domain/entity_id zusammengesetzt."""
    def handler(path, params):
        assert path == "/api/v1/query"
        query = params["query"][0]
        assert "by (domain, entity_id)" in query
        if query.startswith("sum"):
            return _instant([("sensor", "temp", 12), ("switch", "pump", 3)])
        if query.startswith("min"):
            return _instant([("sensor", "temp", 1000), ("switch", "pump", 2000)])
        return _instant([("sensor", "temp", 5000), ("switch", "pump", 6000)])

    _fake_open(monkeypatch, handler)
    end = START + timedelta(days=10)
    result = vm_import.fetch_availability(["sensor.temp", "switch.pump", "sensor.missing"], START, end)
    assert (result["sensor.temp"].count, result["sensor.temp"].first_ts, result["sensor.temp"].last_ts) == (12, 1000, 5000)
    assert result["switch.pump"].count == 3
    assert result["sensor.missing"].has_data is False
    assert result["sensor.missing"].first_ts is None


def test_fetch_availability_escapes_dots_without_a_backslash(monkeypatch) -> None:
    """MetricsQL lehnt ein einfaches \\. im Query-Literal als Syntaxfehler ab."""
    seen = _fake_open(monkeypatch, lambda path, params: _instant([]))
    vm_import.fetch_availability(["sensor.a_b", "light.c"], START, START + timedelta(days=1))
    query = params_of(seen[0])["query"][0]
    assert "sensor[.]a_b_value|light[.]c_value" in query
    assert "\\" not in query


def test_fetch_availability_batches_entities(monkeypatch) -> None:
    seen = _fake_open(monkeypatch, lambda path, params: _instant([]))
    ids = [f"sensor.e{i}" for i in range(vm_import.ENTITY_BATCH_SIZE + 1)]
    vm_import.fetch_availability(ids, START, START + timedelta(days=1))
    assert len(seen) == 2 * 3  # zwei Batches, je drei Queries (count/first/last)


def test_fetch_availability_rejects_an_invalid_response(monkeypatch) -> None:
    _fake_open(monkeypatch, lambda path, params: b'{"status": "error"}')
    with pytest.raises(vm_import.VmApiError):
        vm_import.fetch_availability(["sensor.a"], START, START + timedelta(days=1))


def test_open_maps_http_errors_with_status_and_detail(monkeypatch) -> None:
    def raise_http(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 422, "x", {}, io.BytesIO(b"too many points"))

    monkeypatch.setattr(vm_import.urllib.request, "urlopen", raise_http)
    request = vm_import._request("http://vm:8428", "/api/v1/query", None, None)
    with pytest.raises(vm_import.VmApiError) as exc:
        vm_import._open(request)
    assert "422" in str(exc.value) and "too many points" in str(exc.value)


def test_open_maps_network_errors(monkeypatch) -> None:
    def raise_url(request, timeout):
        raise urllib.error.URLError("No route to host")

    monkeypatch.setattr(vm_import.urllib.request, "urlopen", raise_url)
    with pytest.raises(vm_import.VmApiError) as exc:
        vm_import._open(vm_import._request("http://vm:8428", "/health", None, None))
    assert "No route to host" in str(exc.value)


def test_request_adds_basic_auth_only_when_given() -> None:
    plain = vm_import._request("http://vm:8428", "/health", None, None)
    assert plain.get_header("Authorization") is None
    authed = vm_import._request("http://vm:8428", "/health", None, ("user", "pw"))
    assert authed.get_header("Authorization") == "Basic dXNlcjpwdw=="
