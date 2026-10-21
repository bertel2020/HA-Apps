"""Abruf und sichere Aufbereitung der sichtbaren Zeitarchiv-Protokolle."""

from __future__ import annotations

from .i18n import tr

import logging
import os
import re
import urllib.error
import urllib.request

from fastapi import HTTPException

from .logging_setup import LOG_AREA_LABELS, LOG_LEVEL_LABELS, local_log_lines, log_area, redact_log_text


SUPERVISOR_LOG_URL = "http://supervisor/addons/self/logs/latest"
_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b", re.IGNORECASE)
# Logger-Name steht direkt hinter der Stufe: "<Zeit>  INFO  app.ai_routes  <Text>".
_LOGGER_RE = re.compile(
    r"\b(?:DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+([A-Za-z_][\w.]*)\s", re.IGNORECASE
)
_LEVEL_NUMBERS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def validate_log_request(
    level: str, search: str, limit: int, source: str = "local", area: str = "all"
) -> tuple[str, str, int, str, str]:
    if level != "all" and level not in LOG_LEVEL_LABELS:
        raise HTTPException(status_code=400, detail=tr("Ungültiger Logfilter"))
    if area != "all" and area not in LOG_AREA_LABELS:
        raise HTTPException(status_code=400, detail=tr("Ungültiger Logfilter"))
    if source not in {"local", "supervisor"}:
        raise HTTPException(status_code=400, detail=tr("Ungültige Logquelle"))
    return level, search[:200], max(50, min(limit, 5_000)), source, area


def fetch_supervisor_log_text(lines: int = 2_000) -> str:
    """Liest den aktuellen Containerstart aus dem Supervisor-Journal."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError(tr("Supervisor ist in dieser Umgebung nicht verfügbar"))
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
        raise RuntimeError(tr("Supervisor-Protokoll konnte nicht geladen werden")) from exc
    if len(payload) > 8 * 1024 * 1024:
        payload = payload[-8 * 1024 * 1024 :]
    return payload.decode("utf-8", errors="replace")


def filter_external_log_text(
    text: str, *, level: str = "all", search: str = "", limit: int = 500, area: str = "all"
) -> list[str]:
    """Filtert Journalzeilen; Fortsetzungszeilen erben Level und Bereich der vorherigen."""
    threshold = _LEVEL_NUMBERS.get(level, 0)
    needle = search.casefold().strip()
    current_level = logging.INFO
    current_area = "other"
    result: list[str] = []
    for raw_line in text.splitlines():
        line = redact_log_text(raw_line)
        match = _LEVEL_RE.search(line)
        if match:
            current_level = _LEVEL_NUMBERS[match.group(1).lower()]
            logger_match = _LOGGER_RE.search(line)
            current_area = log_area(logger_match.group(1)) if logger_match else "other"
        if threshold and current_level < threshold:
            continue
        if area != "all" and current_area != area:
            continue
        if needle and needle not in line.casefold():
            continue
        result.append(line)
    return result[-limit:]


def load_log_lines(
    *, level: str = "all", search: str = "", limit: int = 500,
    source: str = "supervisor", area: str = "all",
) -> dict:
    """Lädt lokale Live-Meldungen oder die umfassendere Supervisor-Historie."""
    if source == "local":
        return {
            "source": "Lokaler Prozesspuffer",
            "lines": local_log_lines(level=level, search=search, limit=limit, area=area),
            "fallback": False,
        }
    try:
        text = fetch_supervisor_log_text(max(limit * 4, 2_000))
        return {
            "source": "Home Assistant Supervisor",
            "lines": filter_external_log_text(text, level=level, search=search, limit=limit, area=area),
            "fallback": False,
        }
    except RuntimeError as exc:
        return {
            "source": "Lokaler Prozesspuffer",
            "lines": local_log_lines(level=level, search=search, limit=limit, area=area),
            "fallback": True,
            "notice": str(exc),
        }
