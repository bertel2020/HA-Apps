"""Zentrale, zur Laufzeit umschaltbare Protokollierung für Zeitarchiv.

Die eigentliche dauerhafte Loghaltung übernimmt unter Home Assistant der
Supervisor über stdout/stderr. Ein begrenzter Ringpuffer hält dieselben
Zeitarchiv-Meldungen zusätzlich im Prozess, damit die Logseite auch beim
lokalen Venv-Betrieb ohne Supervisor funktioniert.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime


LOG_LEVEL_LABELS = {
    "error": "Fehler",
    "warning": "Warnungen",
    "info": "Informationen",
    "debug": "Debug",
}
ACCESS_LOG_LABELS = {
    "off": "Aus",
    "errors": "Nur fehlgeschlagene Anfragen",
    "all": "Alle Anfragen",
}

DEFAULT_LOG_LEVEL = "warning"
DEFAULT_ACCESS_LOG_MODE = "errors"

_LEVEL_NUMBERS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")
_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?token|token|password|secret)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?token|token|password|secret)=)[^&#\s]+"
)
_JSON_SECRET_RE = re.compile(
    r'''(?ix)
    (["'](?:api[_-]?token|token|password|secret)["']\s*:\s*)
    (?:"[^"]*"|'[^']*'|[^,}\]\s]+)
    '''
)

# Erfolgreiche technische Polls sollen auch im Modus "Alle Anfragen" nicht
# das Protokoll mit seinen eigenen Aktualisierungen füllen. Fehler bleiben
# weiterhin sichtbar.
_QUIET_SUCCESS_PATHS = {
    "/api/health",
    "/api/logs",
    "/settings/logging/debug",
}
SLOW_REQUEST_MS = 2_000.0


def redact_log_text(value: object) -> str:
    """Entfernt ANSI-Steuerzeichen und typische Geheimnisse aus Logtext."""
    text = _ANSI_RE.sub("", str(value))
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _JSON_SECRET_RE.sub(r'\1"[REDACTED]"', text)
    text = _SECRET_RE.sub(r"\1[REDACTED]", text)
    return _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="milliseconds")


class _RedactingFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return _iso_timestamp(record.created)

    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


class _RedactingProxyFormatter(logging.Formatter):
    """Maskiert auch Ausgaben bereits vorhandener Fremdlogger-Formatter."""

    def __init__(self, wrapped: logging.Formatter | None) -> None:
        super().__init__()
        self._wrapped = wrapped or logging.Formatter("%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(self._wrapped.format(record))


def _ensure_redacting_formatter(handler: logging.Handler) -> None:
    if getattr(handler, "_zeitarchiv_redacting_formatter", False):
        return
    if not isinstance(handler.formatter, _RedactingFormatter):
        handler.setFormatter(_RedactingProxyFormatter(handler.formatter))
    handler._zeitarchiv_redacting_formatter = True  # type: ignore[attr-defined]


class RingLogHandler(logging.Handler):
    """Thread-sicherer, strikt begrenzter Speicherpuffer für die Logseite."""

    def __init__(self, max_entries: int = 2_000) -> None:
        super().__init__(logging.DEBUG)
        self._entries: deque[dict] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self.setFormatter(_RedactingFormatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname.lower(),
                "levelno": record.levelno,
                "logger": record.name,
                "message": self.format(record),
            }
            with self._lock:
                self._entries.append(entry)
        except Exception:
            self.handleError(record)

    def lines(self, *, level: str = "all", search: str = "", limit: int = 500) -> list[str]:
        threshold = _LEVEL_NUMBERS.get(level, 0)
        needle = search.casefold().strip()
        with self._lock:
            entries = list(self._entries)
        result = []
        for entry in entries:
            if threshold and entry["levelno"] < threshold:
                continue
            timestamp = _iso_timestamp(entry["ts"])
            logger_name = entry["logger"].removeprefix("app.").removeprefix("zeitarchiv.")
            line = f"{timestamp}  {entry['level'].upper():<8} {logger_name:<18} {entry['message']}"
            if needle and needle not in line.casefold():
                continue
            result.append(line)
        return result[-limit:]


_RING_HANDLER = RingLogHandler()
_CONSOLE_HANDLER = logging.StreamHandler(sys.stdout)
_CONSOLE_HANDLER.setLevel(logging.DEBUG)
_CONSOLE_HANDLER.setFormatter(
    _RedactingFormatter("%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
)
_ACCESS_MODE = DEFAULT_ACCESS_LOG_MODE
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_STATE: dict[str, dict[str, float | int]] = {}


def configure_logging(level: str, access_mode: str) -> None:
    """Konfiguriert App- und HTTP-Logger idempotent und sofort wirksam."""
    global _ACCESS_MODE
    if level not in LOG_LEVEL_LABELS:
        level = DEFAULT_LOG_LEVEL
    if access_mode not in ACCESS_LOG_LABELS:
        access_mode = DEFAULT_ACCESS_LOG_MODE

    app_logger = logging.getLogger("app")
    app_logger.handlers = [
        handler for handler in app_logger.handlers
        if getattr(handler, "_zeitarchiv_handler", False)
    ]
    for handler in (_CONSOLE_HANDLER, _RING_HANDLER):
        handler._zeitarchiv_handler = True  # type: ignore[attr-defined]
        if handler not in app_logger.handlers:
            app_logger.addHandler(handler)
    app_logger.setLevel(_LEVEL_NUMBERS[level])
    app_logger.propagate = False

    access_logger = logging.getLogger("zeitarchiv.access")
    access_logger.handlers = [_CONSOLE_HANDLER, _RING_HANDLER]
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False
    access_logger.disabled = access_mode == "off"

    # Gezieltes Entity-Tracing (Konzept "Debugging"): bleibt IMMER auf DEBUG,
    # unabhängig vom konfigurierten Loglevel oben — eine bewusst gestartete,
    # zeitlich begrenzte Aufzeichnung soll sichtbar sein, ohne dass man dafür
    # zusätzlich den globalen Loglevel umstellen (und wieder zurückstellen)
    # muss.
    trace_logger = logging.getLogger("zeitarchiv.trace")
    trace_logger.handlers = [_CONSOLE_HANDLER, _RING_HANDLER]
    trace_logger.setLevel(logging.DEBUG)
    trace_logger.propagate = False

    # Uvicorns Standard-Accesslog würde jeden Messwert-POST zusätzlich loggen.
    # Zeitarchiv übernimmt das differenziert im eigenen HTTP-Middleware.
    logging.getLogger("uvicorn.access").disabled = True
    uvicorn_error = logging.getLogger("uvicorn.error")
    if _RING_HANDLER not in uvicorn_error.handlers:
        uvicorn_error.addHandler(_RING_HANDLER)
    # Fremdlogger besitzen teils eigene Console-Handler. Deren Originalformat
    # bleibt erhalten, wird aber vor stdout/stderr ebenfalls redigiert; eine
    # reine Nachbearbeitung in der Logseite wäre zu spät, weil der Supervisor
    # die ursprüngliche Ausgabe bereits dauerhaft aufgenommen hätte.
    for foreign_logger in (
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        uvicorn_error,
        logging.getLogger("fastapi"),
    ):
        for handler in foreign_logger.handlers:
            _ensure_redacting_formatter(handler)
    _ACCESS_MODE = access_mode


def current_access_mode() -> str:
    return _ACCESS_MODE


def log_rate_limited(
    log: logging.Logger,
    level: int,
    key: str,
    message: str,
    *args: object,
    interval_seconds: float = 300.0,
) -> bool:
    """Loggt eine repetitive Meldung höchstens einmal je Intervall.

    Beim nächsten sichtbaren Eintrag wird die Zahl der zwischenzeitlich
    unterdrückten Wiederholungen angehängt. Der Rückgabewert sagt, ob die
    Meldung tatsächlich ausgegeben wurde.
    """
    now = time.monotonic()
    suppressed = 0
    with _RATE_LIMIT_LOCK:
        state = _RATE_LIMIT_STATE.get(key)
        if state is not None and now - float(state["last_logged"]) < interval_seconds:
            state["suppressed"] = int(state["suppressed"]) + 1
            return False
        if state is not None:
            suppressed = int(state["suppressed"])
        _RATE_LIMIT_STATE[key] = {"last_logged": now, "suppressed": 0}
    if suppressed:
        message += " · unterdrueckte_wiederholungen=%d"
        args = (*args, suppressed)
    log.log(level, message, *args)
    return True


def log_http_request(
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    *,
    request_id: str | None = None,
) -> None:
    mode = _ACCESS_MODE
    request_field = request_id or "-"
    if status < 400 and path in _QUIET_SUCCESS_PATHS:
        return
    if status < 400 and duration_ms >= SLOW_REQUEST_MS:
        logging.getLogger("app.http").warning(
            "Langsame HTTP-Anfrage · event=http_slow request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
            request_field, method, path, status, duration_ms,
        )
        return
    if mode == "off" or (mode == "errors" and status < 400):
        return
    log = logging.getLogger("zeitarchiv.access")
    message = "%s %s -> %d · %.1f ms · event=http_request request_id=%s"
    if status >= 500:
        log.error(message, method, path, status, duration_ms, request_field)
    elif status >= 400:
        log.warning(message, method, path, status, duration_ms, request_field)
    else:
        log.info(message, method, path, status, duration_ms, request_field)


def local_log_lines(*, level: str = "all", search: str = "", limit: int = 500) -> list[str]:
    return _RING_HANDLER.lines(level=level, search=search, limit=limit)
