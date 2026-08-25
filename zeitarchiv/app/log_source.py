"""Abruf und sichere Aufbereitung der sichtbaren Zeitarchiv-Protokolle."""

from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.request

from .logging_setup import local_log_lines, redact_log_text


SUPERVISOR_LOG_URL = "http://supervisor/addons/self/logs/latest"
_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b", re.IGNORECASE)
_LEVEL_NUMBERS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def fetch_supervisor_log_text(lines: int = 2_000) -> str:
    """Liest den aktuellen Containerstart aus dem Supervisor-Journal."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("Supervisor ist in dieser Umgebung nicht verfügbar")
    request = urllib.request.Request(
        f"{SUPERVISOR_LOG_URL}?lines={lines}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "text/plain",
            "User-Agent": "Zeitarchiv/Logs",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Supervisor-Protokoll konnte nicht geladen werden") from exc
    if len(payload) > 8 * 1024 * 1024:
        payload = payload[-8 * 1024 * 1024 :]
    return payload.decode("utf-8", errors="replace")


def filter_external_log_text(
    text: str, *, level: str = "all", search: str = "", limit: int = 500
) -> list[str]:
    """Filtert Journalzeilen; Fortsetzungszeilen erben das vorherige Level."""
    threshold = _LEVEL_NUMBERS.get(level, 0)
    needle = search.casefold().strip()
    current_level = logging.INFO
    result: list[str] = []
    for raw_line in text.splitlines():
        line = redact_log_text(raw_line)
        match = _LEVEL_RE.search(line)
        if match:
            current_level = _LEVEL_NUMBERS[match.group(1).lower()]
        if threshold and current_level < threshold:
            continue
        if needle and needle not in line.casefold():
            continue
        result.append(line)
    return result[-limit:]


def load_log_lines(*, level: str = "all", search: str = "", limit: int = 500) -> dict:
    """Supervisor ist primär; lokaler Ringpuffer ist der robuste Fallback."""
    try:
        text = fetch_supervisor_log_text(max(limit * 4, 2_000))
        return {
            "source": "Home Assistant Supervisor",
            "lines": filter_external_log_text(text, level=level, search=search, limit=limit),
            "fallback": False,
        }
    except RuntimeError as exc:
        return {
            "source": "Lokaler Prozesspuffer",
            "lines": local_log_lines(level=level, search=search, limit=limit),
            "fallback": True,
            "notice": str(exc),
        }

