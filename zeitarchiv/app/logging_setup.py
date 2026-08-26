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


def redact_log_text(value: object) -> str:
    """Entfernt ANSI-Steuerzeichen und typische Geheimnisse aus Logtext."""
    text = _ANSI_RE.sub("", str(value))
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_RE.sub(r"\1[REDACTED]", text)
    return _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


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
            timestamp = datetime.fromtimestamp(entry["ts"]).astimezone().strftime("%d.%m.%Y %H:%M:%S")
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
    _RedactingFormatter("%(asctime)s  %(levelname)-8s %(name)s  %(message)s", "%Y-%m-%d %H:%M:%S")
)
_ACCESS_MODE = DEFAULT_ACCESS_LOG_MODE


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
    _ACCESS_MODE = access_mode


def current_access_mode() -> str:
    return _ACCESS_MODE


def log_http_request(method: str, path: str, status: int, duration_ms: float) -> None:
    mode = _ACCESS_MODE
    if mode == "off" or (mode == "errors" and status < 400):
        return
    log = logging.getLogger("zeitarchiv.access")
    message = "%s %s -> %d · %.1f ms"
    if status >= 500:
        log.error(message, method, path, status, duration_ms)
    elif status >= 400:
        log.warning(message, method, path, status, duration_ms)
    else:
        log.info(message, method, path, status, duration_ms)


def local_log_lines(*, level: str = "all", search: str = "", limit: int = 500) -> list[str]:
    return _RING_HANDLER.lines(level=level, search=search, limit=limit)
