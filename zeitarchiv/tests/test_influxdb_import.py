"""Tests für app/storage/influxdb_import.py — Abruf (Serien- und
Measurement-Zusammenführung, Fenster, Dedup, Zustandsfeld), gebündelte
Verfügbarkeit und Fehlerabbildung. Das HTTP ist über ``influxdb_import._open_lines``
ersetzt: kein Netz, keine echte InfluxDB."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.storage import influxdb_import

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
BASE = int(START.timestamp() * 1000)


def _reply(*series: dict) -> bytes:
    return json.dumps({"results": [{"statement_id": 0, "series": list(series)}]}).encode()


def _values(name: str, rows: list[list], columns: tuple = ("time", "value"), **tags) -> dict:
    series = {"name": name, "columns": list(columns), "values": rows}
    if tags:
        series["tags"] = tags
    return series


def _fake(monkeypatch, handler) -> list:
    """handler(params) -> bytes (eine oder mehrere Antwortzeilen); liefert die aufgezeichneten Anfragen."""
    seen: list = []

    def fake(request, verify_tls=True):
        seen.append(request)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        return iter(handler(params).splitlines())

    monkeypatch.setattr(influxdb_import, "_open_lines", fake)
    return seen


def params_of(request) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)


def test_normalize_base_url() -> None:
    assert influxdb_import.normalize_base_url(None) == influxdb_import.DEFAULT_BASE_URL
    assert influxdb_import.normalize_base_url("  ") == influxdb_import.DEFAULT_BASE_URL
    assert influxdb_import.normalize_base_url("192.168.1.2:8086/") == "http://192.168.1.2:8086"
    assert influxdb_import.normalize_base_url("https://influx.local") == "https://influx.local"
    with pytest.raises(influxdb_import.InfluxApiError):
        influxdb_import.normalize_base_url("ftp://influx.local")


def test_split_entity_id_separates_domain_and_object_id() -> None:
    assert influxdb_import.split_entity_id("sensor.wohnzimmer_temperatur") == ("sensor", "wohnzimmer_temperatur")
    for invalid in ("kein_punkt", ".x", "sensor."):
        with pytest.raises(ValueError):
            influxdb_import.split_entity_id(invalid)


def test_query_filters_on_domain_and_object_tags_not_the_full_entity_id(monkeypatch) -> None:
    """Der Tag entity_id enthält nur den Objektteil, die Domain steht im Tag domain."""
    seen = _fake(monkeypatch, lambda params: b"")
    influxdb_import.fetch_history_rows("sensor.pv_leistung", START, START + timedelta(days=1), "http://i:8086", "ha")
    query = params_of(seen[0])["q"][0]
    assert """"domain" = 'sensor' AND "entity_id" = 'pv_leistung'""" in query
    assert 'FROM /.*/' in query and 'SELECT "value"' in query
    assert params_of(seen[0])["db"] == ["ha"]
    assert params_of(seen[0])["chunked"] == ["true"]
    assert params_of(seen[0])["epoch"] == ["ms"]


def test_a_quote_in_an_entity_id_cannot_break_out_of_the_query(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: b"")
    influxdb_import.fetch_history_rows("sensor.a'b", START, START + timedelta(days=1))
    assert "'a\\'b'" in params_of(seen[0])["q"][0]


def test_fetch_history_rows_merges_series_and_measurements_by_timestamp(monkeypatch) -> None:
    """Umbenennung (neue Serie) und Einheitenwechsel (anderes Measurement) ergeben
    mehrere Antwortserien — sie gehören zu EINER Zeitreihe, sortiert."""
    _fake(monkeypatch, lambda params: _reply(
        _values("kW", [[BASE + 2000, 2.0], [BASE + 3000, 3.0]]),
        _values("W", [[BASE, 0.0], [BASE + 1000, 1.0]]),
    ))
    result = influxdb_import.fetch_history_rows("sensor.pv", START, START + timedelta(days=1))
    assert result.rows == [(BASE / 1000 + i, float(i)) for i in range(4)]
    assert result.skipped == 0


def test_fetch_history_rows_reports_the_measurement_names(monkeypatch) -> None:
    """Der Measurement-Name ist in der Voreinstellung der HA-Integration die Einheit
    (ROADMAP 1.3) und geht an die Import-Vorschau."""
    _fake(monkeypatch, lambda params: _reply(
        _values("Wh", [[BASE, 1000.0]]),
        _values("kWh", [[BASE + 1000, 2.0]]),
        _values("Wh", [[BASE + 2000, 3000.0]]),
    ))
    result = influxdb_import.fetch_history_rows("sensor.pv", START, START + timedelta(days=1))
    assert result.source_units == ["Wh", "kWh"]


def test_fetch_history_rows_reads_every_chunk_line(monkeypatch) -> None:
    """chunked=true liefert mehrere JSON-Zeilen — alle müssen gelesen werden."""
    _fake(monkeypatch, lambda params: b"\n".join([
        _reply(_values("W", [[BASE, 1.0], [BASE + 1000, 2.0]])),
        _reply(_values("W", [[BASE + 2000, 3.0]])),
    ]))
    result = influxdb_import.fetch_history_rows("sensor.pv", START, START + timedelta(days=1))
    assert [v for _, v in result.rows] == [1.0, 2.0, 3.0]


def test_fetch_history_rows_collapses_duplicate_timestamps_and_skips_bad_values(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: _reply(_values("W", [
        [BASE, 5.0], [BASE, 6.0], [BASE + 1000, "x"], [BASE + 2000, None], [BASE + 3000, True],
    ])))
    result = influxdb_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))
    assert result.rows == [(BASE / 1000, 5.0)]  # erster Punkt gewinnt
    assert result.skipped == 3  # "x", None und bool
    assert len(result.discarded) == 4  # drei verworfene Werte + ein Duplikat


def test_fetch_history_rows_rounds_values_like_the_other_imports(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: _reply(_values("W", [[BASE, 21.123456]])))
    result = influxdb_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))
    assert result.rows[0][1] == 21.123


def test_fetch_history_rows_uses_the_state_field_for_state_entities(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: _reply(_values("state", [[BASE, "sunny"], [BASE + 1000, "rainy"]], columns=("time", "state"))))
    mapping = {"sunny": 0.0, "rainy": 1.0}
    result = influxdb_import.fetch_history_rows(
        "weather.home", START, START + timedelta(days=1), state_mapper=lambda text, ts: mapping.get(text),
    )
    assert 'SELECT "state"' in params_of(seen[0])["q"][0]
    assert result.rows == [(BASE / 1000, 0.0), (BASE / 1000 + 1, 1.0)]


def test_fetch_history_rows_skips_state_texts_the_mapper_refuses(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: _reply(_values("state", [[BASE, "unbekannt"]], columns=("time", "state"))))
    result = influxdb_import.fetch_history_rows(
        "weather.home", START, START + timedelta(days=1), state_mapper=lambda text, ts: None,
    )
    assert result.rows == [] and result.skipped == 1


def test_fetch_history_rows_queries_in_windows_and_covers_the_whole_range(monkeypatch) -> None:
    end = START + influxdb_import.HISTORY_CHUNK * 2 + timedelta(days=5)
    seen = _fake(monkeypatch, lambda params: b"")
    result = influxdb_import.fetch_history_rows("sensor.t", START, end)
    assert result.rows == []
    assert len(seen) == 3
    import re

    windows = [
        tuple(int(x) for x in re.search(r"time >= (\d+)ms AND time < (\d+)ms", params_of(r)["q"][0]).groups())
        for r in seen
    ]
    assert windows[0][0] == BASE and windows[-1][1] == int(end.timestamp() * 1000)
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))  # lückenlos


def test_fetch_history_rows_enforces_the_row_limit(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: _reply(_values("W", [[BASE + i, 1.0] for i in range(5)])))
    with pytest.raises(ValueError):
        influxdb_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1), max_rows=4)


def test_an_invalid_response_is_reported(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: b"das ist kein json")
    with pytest.raises(influxdb_import.InfluxApiError):
        influxdb_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))


def test_influxdb_errors_delivered_with_status_200_are_raised(monkeypatch) -> None:
    """InfluxDB meldet "database not found" mit HTTP 200 im JSON-Körper."""
    _fake(monkeypatch, lambda params: json.dumps({"results": [{"statement_id": 0, "error": "database not found: x"}]}).encode())
    with pytest.raises(influxdb_import.InfluxApiError) as exc:
        influxdb_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1), database="x")
    assert "database not found: x" in str(exc.value)

    _fake(monkeypatch, lambda params: json.dumps({"error": "unable to parse"}).encode())
    with pytest.raises(influxdb_import.InfluxApiError) as exc:
        influxdb_import.fetch_history_rows("sensor.t", START, START + timedelta(days=1))
    assert "unable to parse" in str(exc.value)


def _counts(rows: list[tuple[str, str, str, float]], column: str = "count") -> bytes:
    """rows: (measurement, domain, entity_id, wert)"""
    return _reply(*[
        _values(m, [[0, v]], columns=("time", column), domain=d, entity_id=e) for m, d, e, v in rows
    ])


def test_fetch_availability_sums_counts_and_takes_extreme_times_over_measurements(monkeypatch) -> None:
    def handler(params):
        query = params["q"][0]
        if "count(" in query:
            return _counts([("W", "sensor", "pv", 800), ("kW", "sensor", "pv", 700), ("%", "sensor", "bat", 5)])
        if "first(" in query:
            return _reply(
                _values("W", [[2000, 1.0]], domain="sensor", entity_id="pv"),
                _values("kW", [[1000, 1.0]], domain="sensor", entity_id="pv"),
                _values("%", [[3000, 1.0]], domain="sensor", entity_id="bat"),
            )
        return _reply(
            _values("W", [[7000, 1.0]], domain="sensor", entity_id="pv"),
            _values("kW", [[9000, 1.0]], domain="sensor", entity_id="pv"),
            _values("%", [[8000, 1.0]], domain="sensor", entity_id="bat"),
        )

    _fake(monkeypatch, handler)
    result = influxdb_import.fetch_availability(
        ["sensor.pv", "sensor.bat", "sensor.missing"], START, START + timedelta(days=10)
    )
    pv = result["sensor.pv"]
    assert (pv.count, pv.first_ts, pv.last_ts) == (1500, 1.0, 9.0)
    assert result["sensor.bat"].count == 5
    assert result["sensor.missing"].has_data is False and result["sensor.missing"].first_ts is None


def test_fetch_availability_ignores_series_that_match_only_domain_or_object_alone(monkeypatch) -> None:
    """Die Abfrage filtert Domain und Objektteil je als Regex: eine Serie
    ``light.pv`` darf nicht als ``sensor.pv`` gezählt werden."""
    _fake(monkeypatch, lambda params: _counts([("W", "light", "pv", 99)]))
    result = influxdb_import.fetch_availability(["sensor.pv"], START, START + timedelta(days=1))
    assert result["sensor.pv"].has_data is False


def test_fetch_availability_uses_the_state_field_for_state_entities(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: b"")
    influxdb_import.fetch_availability(
        ["sensor.temp", "weather.home"], START, START + timedelta(days=1), state_entity_ids={"weather.home"}
    )
    queries = [params_of(r)["q"][0] for r in seen]
    assert any('count("value")' in q and "temp" in q for q in queries)
    assert any('count("state")' in q and "home" in q for q in queries)


def test_fetch_availability_escapes_regex_characters(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: b"")
    influxdb_import.fetch_availability(["sensor.a+b"], START, START + timedelta(days=1))
    assert r"a\+b" in params_of(seen[0])["q"][0]


def test_fetch_availability_batches_entities(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: b"")
    ids = [f"sensor.e{i}" for i in range(influxdb_import.ENTITY_BATCH_SIZE + 1)]
    influxdb_import.fetch_availability(ids, START, START + timedelta(days=1))
    assert len(seen) == 2 * 3  # zwei Batches, je drei Abfragen (count/first/last)


def test_open_maps_http_errors_with_status_and_detail(monkeypatch) -> None:
    class Opener:
        def open(self, request, timeout):  # noqa: A003
            raise urllib.error.HTTPError(request.full_url, 500, "x", {}, io.BytesIO(b"boom"))

    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: Opener())
    request = influxdb_import._request("http://i:8086", "ha", "SHOW SERIES", None)
    with pytest.raises(influxdb_import.InfluxApiError) as exc:
        list(influxdb_import._open_lines(request))
    assert "500" in str(exc.value) and "boom" in str(exc.value)


def test_a_401_is_explained_as_wrong_credentials(monkeypatch) -> None:
    class Opener:
        def open(self, request, timeout):  # noqa: A003
            raise urllib.error.HTTPError(request.full_url, 401, "x", {}, io.BytesIO(b'{"error":"authorization failed"}'))

    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: Opener())
    request = influxdb_import._request("http://i:8086", "ha", "SHOW SERIES", ("u", "p"))
    with pytest.raises(influxdb_import.InfluxApiError) as exc:
        list(influxdb_import._open_lines(request))
    assert "Zugangsdaten" in str(exc.value)


def test_open_maps_network_errors(monkeypatch) -> None:
    class Opener:
        def open(self, request, timeout):  # noqa: A003
            raise urllib.error.URLError("No route to host")

    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: Opener())
    with pytest.raises(influxdb_import.InfluxApiError) as exc:
        list(influxdb_import._open_lines(influxdb_import._request("http://i:8086", "ha", "x", None)))
    assert "No route to host" in str(exc.value)


def test_request_adds_basic_auth_only_when_given_and_never_in_the_url() -> None:
    plain = influxdb_import._request("http://i:8086", "ha", "SHOW SERIES", None)
    assert plain.get_header("Authorization") is None
    authed = influxdb_import._request("http://i:8086", "ha", "SHOW SERIES", ("user", "pw"))
    assert authed.get_header("Authorization") == "Basic dXNlcjpwdw=="
    assert "pw" not in authed.full_url and "user" not in authed.full_url


def test_certificate_check_can_be_switched_off_only_on_request() -> None:
    import ssl

    def contexts(opener):
        return [h._context for h in opener.handlers if getattr(h, "_context", None) is not None]

    assert all(c.verify_mode != ssl.CERT_NONE for c in contexts(influxdb_import._opener(True)))
    unchecked = contexts(influxdb_import._opener(False))
    assert unchecked and all(c.verify_mode == ssl.CERT_NONE and c.check_hostname is False for c in unchecked)


def test_a_redirect_is_not_followed_and_never_carries_the_credentials_along() -> None:
    """urllib schickt den Authorization-Header bei einer Weiterleitung auch an den neuen Host mit; die Zugangsdaten
    gehören nur zur eingegebenen Adresse."""
    import http.server
    import threading

    received: list[dict] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            received.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Redirector(Target):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
            self.end_headers()

    redirector = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (target, redirector)]
    for thread in threads:
        thread.start()
    try:
        request = influxdb_import._request(
            f"http://127.0.0.1:{redirector.server_port}", "ha", "SHOW SERIES", ("user", "pw")
        )
        with pytest.raises(influxdb_import.InfluxApiError) as exc:
            list(influxdb_import._open_lines(request))
        assert "302" in str(exc.value)
        assert received == []
    finally:
        for server in (target, redirector):
            server.shutdown()
            server.server_close()


# ------------------------------------------------------------ Verbindungstest


class _PingOpener:
    """Ersetzt ``influxdb_import._opener``: ``/ping`` antwortet mit der Version
    (oder einem Fehler); die eigentliche Abfrage läuft über ``_open_lines``."""

    def __init__(self, version: str = "1.8.10", error: Exception | None = None) -> None:
        self.version, self.error, self.requests = version, error, []

    def open(self, request, timeout=None):  # noqa: A003
        self.requests.append(request)
        if self.error:
            raise self.error
        outer = self

        class _Response:
            headers = {"X-Influxdb-Version": outer.version} if outer.version else {}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()


def _domains(*names: str) -> bytes:
    return _reply(_values("W", [["domain", n] for n in names], columns=("key", "value")))


def test_check_connection_reports_version_and_domains(monkeypatch) -> None:
    ping = _PingOpener("1.8.10")
    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: ping)
    seen = _fake(monkeypatch, lambda params: _domains("switch", "sensor"))
    version, domains = influxdb_import.check_connection("http://influx:8086", "ha", ("u", "pw"))
    assert (version, domains) == ("1.8.10", ["sensor", "switch"])
    assert ping.requests[0].full_url == "http://influx:8086/ping"
    assert params_of(seen[0])["db"] == ["ha"] and "domain" in params_of(seen[0])["q"][0]
    # Zugangsdaten nur im Header der Abfrage, nie in einer URL
    assert "pw" not in seen[0].full_url and seen[0].get_header("Authorization")


def test_check_connection_tolerates_a_missing_version(monkeypatch) -> None:
    error = urllib.error.HTTPError("http://x/ping", 404, "nf", {}, io.BytesIO(b""))
    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: _PingOpener(error=error))
    _fake(monkeypatch, lambda params: _domains("sensor"))
    assert influxdb_import.check_connection("http://influx:8086", "ha", None) == ("", ["sensor"])


def test_check_connection_rejects_influxdb_2(monkeypatch) -> None:
    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: _PingOpener("v2.7.1"))
    seen = _fake(monkeypatch, lambda params: _domains("sensor"))
    with pytest.raises(influxdb_import.InfluxApiError, match="1.x"):
        influxdb_import.check_connection("http://influx:8086", "ha", None)
    assert not seen  # keine Abfrage mehr nach dem erkannten 2.x


def test_check_connection_unreachable_and_empty_database(monkeypatch) -> None:
    monkeypatch.setattr(
        influxdb_import, "_opener", lambda verify_tls: _PingOpener(error=urllib.error.URLError("refused"))
    )
    with pytest.raises(influxdb_import.InfluxApiError, match="nicht erreichbar"):
        influxdb_import.check_connection("http://influx:8086", "ha", None)

    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: _PingOpener())
    _fake(monkeypatch, lambda params: json.dumps({"results": [{"statement_id": 0}]}).encode())
    with pytest.raises(influxdb_import.InfluxApiError, match="domain"):
        influxdb_import.check_connection("http://influx:8086", "ha", None)


def test_check_connection_passes_wrong_database_and_credentials_on(monkeypatch) -> None:
    monkeypatch.setattr(influxdb_import, "_opener", lambda verify_tls: _PingOpener())
    _fake(monkeypatch, lambda params: json.dumps({"error": "database not found: \"nope\""}).encode())
    with pytest.raises(influxdb_import.InfluxApiError, match="Datenbank"):
        influxdb_import.check_connection("http://influx:8086", "nope", None)


# ------------------------------------------------------------ Abschnitte großer Entitäten

BERLIN = ZoneInfo("Europe/Berlin")


def _day(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def test_monthly_counts_sum_measurements_and_group_by_local_month(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: _reply(
        _values("W", [[_day(2026, 1, 31), 5], [_day(2026, 2, 1), 7]], columns=("time", "count")),
        # Einheitenwechsel: dieselbe Entität in einem zweiten Measurement
        _values("kW", [[_day(2026, 2, 2), 3]], columns=("time", "count")),
    ))
    counts = influxdb_import.fetch_monthly_counts(
        "sensor.pv", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc), BERLIN,
    )
    # 1.2. 00:00 UTC ist in Berlin schon 01:00 am 1.2.; 31.1. 00:00 UTC bleibt im Januar.
    assert counts == {(2026, 1): 5, (2026, 2): 10}
    query = params_of(seen[0])["q"][0]
    assert 'count("value")' in query and "GROUP BY time(1d) fill(none)" in query
    assert '"domain" = \'sensor\' AND "entity_id" = \'pv\'' in query


def test_monthly_counts_ignore_rows_outside_the_window_and_broken_rows(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: _reply(_values("W", [
        [0, 99], [_day(2026, 1, 10), 4], [_day(2026, 1, 11), 0], ["x", 1], [_day(2026, 1, 12)],
    ], columns=("time", "count"))))
    counts = influxdb_import.fetch_monthly_counts(
        "sensor.pv", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc), BERLIN,
    )
    assert counts == {(2026, 1): 4}


def test_state_entities_are_counted_on_the_text_field(monkeypatch) -> None:
    seen = _fake(monkeypatch, lambda params: _reply())
    influxdb_import.fetch_monthly_counts(
        "sensor.modus", START, START + timedelta(days=1), BERLIN, state=True,
    )
    assert 'count("state")' in params_of(seen[0])["q"][0]


def _span(months: dict, target: int, start=None, end=None):
    start = start or datetime(2026, 1, 1, tzinfo=BERLIN)
    end = end or datetime(2026, 12, 1, tzinfo=BERLIN)
    return influxdb_import.plan_sections(months, start, end, BERLIN, target)


def test_a_small_history_is_one_section() -> None:
    assert _span({(2026, 1): 10, (2026, 2): 10}, target=100) == [
        (datetime(2026, 1, 1, tzinfo=BERLIN), datetime(2026, 12, 1, tzinfo=BERLIN))
    ]
    assert len(_span({}, target=100)) == 1  # ohne Zählung ebenso


def test_sections_pack_whole_months_up_to_the_target() -> None:
    sections = _span({(2026, 1): 40, (2026, 2): 40, (2026, 3): 40, (2026, 4): 40, (2026, 5): 40}, target=100)
    assert [(s.month, e.month) for s, e in sections] == [(1, 3), (3, 5), (5, 12)]
    # Genau am Zielwert bleibt es ein Abschnitt, einer darüber teilt.
    assert len(_span({(2026, 1): 50, (2026, 2): 50}, target=100)) == 1
    assert len(_span({(2026, 1): 50, (2026, 2): 51}, target=100)) == 2


def test_sections_are_contiguous_and_cover_the_whole_range() -> None:
    start, end = datetime(2025, 11, 15, 8, tzinfo=BERLIN), datetime(2026, 9, 10, tzinfo=BERLIN)
    months = {(2025, 11): 60, (2025, 12): 60, (2026, 1): 60, (2026, 2): 60, (2026, 3): 60, (2026, 8): 60}
    sections = influxdb_import.plan_sections(months, start, end, BERLIN, 100)
    assert sections[0][0] == start and sections[-1][1] == end
    for (_, previous_end), (next_start, _) in zip(sections, sections[1:], strict=False):
        assert previous_end == next_start  # keine Lücke, keine Überschneidung
    assert all(s < e for s, e in sections)


def test_section_boundaries_are_local_month_starts_even_across_daylight_saving() -> None:
    sections = _span({(2026, 3): 80, (2026, 4): 80, (2026, 5): 80}, target=100)
    for _, boundary in sections[:-1]:
        local = boundary.astimezone(BERLIN)
        assert (local.day, local.hour, local.minute) == (1, 0, 0)
    # April beginnt in Berlin in der Sommerzeit: 31.3. 22:00 UTC.
    assert sections[1][0].astimezone(timezone.utc) == datetime(2026, 3, 31, 22, tzinfo=timezone.utc)


def test_one_month_over_the_target_stays_a_section_of_its_own() -> None:
    sections = _span({(2026, 1): 10, (2026, 2): 500, (2026, 3): 10}, target=100)
    assert [(s.month, e.month) for s, e in sections] == [(1, 2), (2, 3), (3, 12)]


def test_a_cancelled_fetch_stops_with_import_cancelled(monkeypatch) -> None:
    _fake(monkeypatch, lambda params: _reply(_values("W", [[BASE, 1.0]])))
    with pytest.raises(influxdb_import.ImportCancelled):
        influxdb_import.fetch_history_rows("sensor.pv", START, START + timedelta(days=1), should_cancel=lambda: True)

