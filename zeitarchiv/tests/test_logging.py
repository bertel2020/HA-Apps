"""Regressionstests für maskierte, begrenzte Zeitarchiv-Protokolle."""

from __future__ import annotations

import logging
import io
import re


from app.log_source import filter_external_log_text
from app import log_source
from app.logging_setup import (
    DEFAULT_ACCESS_LOG_MODE,
    DEFAULT_LOG_LEVEL,
    LOG_AREA_LABELS,
    configure_logging,
    local_log_lines,
    log_area,
    log_http_request,
    log_rate_limited,
    redact_log_text,
)


def test_quiet_but_actionable_logging_defaults() -> None:
    assert DEFAULT_LOG_LEVEL == "warning"
    assert DEFAULT_ACCESS_LOG_MODE == "errors"


def test_redaction_removes_bearer_and_named_secrets() -> None:
    text = redact_log_text(
        "Authorization: Bearer super-secret api_token=abc123 password='hidden' ?token=query-secret"
    )
    assert "super-secret" not in text
    assert "abc123" not in text
    assert "hidden" not in text
    assert "query-secret" not in text
    assert text.count("[REDACTED]") == 4


def test_redaction_removes_json_secrets_before_supervisor_output() -> None:
    text = redact_log_text(
        '{"token":"abc123","password": "hidden", "api_token": "third", "safe":"visible"}'
    )
    assert "abc123" not in text
    assert "hidden" not in text
    assert "third" not in text
    assert '"safe":"visible"' in text
    assert text.count("[REDACTED]") == 3


def test_foreign_logger_handler_is_redacted_before_output() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    foreign = logging.getLogger("uvicorn.error")
    foreign.addHandler(handler)
    try:
        configure_logging("debug", "off")
        foreign.warning('foreign-json {"token":"must-not-leak"}')
        output = stream.getvalue()
        assert "must-not-leak" not in output
        assert "[REDACTED]" in output
    finally:
        foreign.removeHandler(handler)


def test_ring_buffer_respects_runtime_application_level() -> None:
    configure_logging("warning", "off")
    log = logging.getLogger("app.logging_test")
    log.info("unique-info-must-not-appear")
    log.warning("unique-warning-must-appear")
    lines = local_log_lines(search="unique-", limit=50)
    assert not any("unique-info-must-not-appear" in line for line in lines)
    assert any("unique-warning-must-appear" in line for line in lines)


def test_access_mode_errors_omits_success_and_keeps_failure() -> None:
    configure_logging("debug", "errors")
    log_http_request("GET", "/logging-success", 200, 1.0)
    log_http_request("GET", "/logging-failure", 404, 2.0)
    lines = local_log_lines(search="/logging-", limit=50)
    assert not any("/logging-success" in line for line in lines)
    assert any("/logging-failure" in line and "404" in line for line in lines)


def test_internal_log_poll_does_not_log_itself_in_all_mode() -> None:
    configure_logging("debug", "all")
    log_http_request("GET", "/api/logs", 200, 4.0, request_id="poll-test")
    assert not any("poll-test" in line for line in local_log_lines(search="poll-test", limit=50))


def test_slow_success_is_warning_even_when_access_mode_only_keeps_errors() -> None:
    configure_logging("debug", "errors")
    log_http_request("GET", "/slow-test", 200, 2_500.0, request_id="slow-test")
    lines = local_log_lines(search="slow-test", limit=50)
    assert any("WARNING" in line and "event=http_slow" in line for line in lines)


def test_health_probe_401_is_labelled_not_warned() -> None:
    # Der Docker-Healthcheck ruft /api/health absichtlich ohne Token auf und
    # wertet die daraus resultierende 401 als Beweis, dass der Prozess lebt
    # (siehe app/healthcheck.py) — im Protokoll soll das deshalb nicht wie
    # ein echter Unauthorized-Zugriff aussehen, auch nicht im Modus "errors".
    configure_logging("debug", "errors")
    log_http_request("GET", "/api/health", 401, 2.5, request_id="health-401-test")
    lines = local_log_lines(search="health-401-test", limit=50)
    assert any(
        "INFO" in line and "event=healthcheck_probe" in line and "Healthcheck" in line
        for line in lines
    )
    assert not any("WARNING" in line for line in lines)


def test_ring_buffer_uses_iso_timestamp_with_timezone_offset() -> None:
    configure_logging("debug", "off")
    logging.getLogger("app.logging_test").warning("iso-timestamp-test")
    line = local_log_lines(search="iso-timestamp-test", limit=1)[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}", line)


def test_rate_limiter_reports_suppressed_repetitions() -> None:
    configure_logging("debug", "off")
    log = logging.getLogger("app.logging_test")
    key = "rate-limit-test-unique"
    assert log_rate_limited(log, logging.WARNING, key, "rate-first", interval_seconds=60)
    assert not log_rate_limited(log, logging.WARNING, key, "rate-hidden", interval_seconds=60)
    assert log_rate_limited(log, logging.WARNING, key, "rate-next", interval_seconds=0)
    lines = local_log_lines(search="rate-", limit=50)
    assert any("rate-first" in line for line in lines)
    assert not any("rate-hidden" in line for line in lines)
    assert any("rate-next" in line and "unterdrueckte_wiederholungen=1" in line for line in lines)


def test_external_filter_inherits_level_for_traceback_continuations() -> None:
    lines = filter_external_log_text(
        "INFO normal\nERROR failed\n  traceback detail\nDEBUG noisy",
        level="error",
        limit=50,
    )
    assert lines == ["ERROR failed", "  traceback detail"]


def test_local_log_source_never_calls_supervisor(monkeypatch) -> None:
    def fail_if_called(_lines: int = 2_000) -> str:
        raise AssertionError("Supervisor must not be queried for local live logs")

    monkeypatch.setattr(log_source, "fetch_supervisor_log_text", fail_if_called)
    configure_logging("debug", "off")
    logging.getLogger("app.logging_test").warning("local-source-test")
    result = log_source.load_log_lines(source="local", search="local-source-test")
    assert result["fallback"] is False
    assert any("local-source-test" in line for line in result["lines"])


def test_log_areas_map_by_logger_name_and_unknown_ones_fall_into_other() -> None:
    assert log_area("app.ai_routes") == log_area("ai_routes") == "ai"
    assert log_area("app.storage.ingestion") == "ingest"
    assert log_area("zeitarchiv.access") == "http"
    assert log_area("uvicorn.error") == "other"
    assert log_area("app.ein_neues_modul") == "other"
    assert set(LOG_AREA_LABELS) >= {log_area(name) for name in ("app.background", "app.import_routes", "app.main")}


def test_local_log_area_filter_keeps_only_that_area() -> None:
    configure_logging("debug", "off")
    logging.getLogger("app.ai_routes").warning("area-test-ai")
    logging.getLogger("app.background").warning("area-test-wartung")
    ai = local_log_lines(search="area-test-", area="ai", limit=50)
    assert any("area-test-ai" in line for line in ai)
    assert not any("area-test-wartung" in line for line in ai)
    assert any("area-test-wartung" in line for line in local_log_lines(search="area-test-", area="maintenance", limit=50))
    assert len(local_log_lines(search="area-test-", area="all", limit=50)) >= 2


def test_external_filter_inherits_area_for_continuations_and_combines_with_level() -> None:
    text = (
        "2026-10-21T22:00:00  INFO     app.ai_routes  KI-Aufruf\n"
        "2026-10-21T22:00:01  ERROR    app.ai_routes  kaputt\n"
        "  traceback detail\n"
        "2026-10-21T22:00:02  ERROR    app.background  Wartung kaputt\n"
    )
    assert filter_external_log_text(text, area="ai", limit=50) == text.splitlines()[:3]
    assert filter_external_log_text(text, area="ai", level="error", limit=50) == text.splitlines()[1:3]
    assert filter_external_log_text(text, area="other", limit=50) == []


def test_log_request_rejects_unknown_area() -> None:
    from fastapi import HTTPException

    assert log_source.validate_log_request("all", "x", 500, "local", "ai")[4] == "ai"
    try:
        log_source.validate_log_request("all", "x", 500, "local", "gibtsnicht")
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("unbekannter Bereich muss abgelehnt werden")


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
