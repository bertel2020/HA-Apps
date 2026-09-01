"""Periodische, rein informative Prüfung auf eine neuere Zeitarchiv-Version
im offiziellen Repository — läuft im Wartungsplaner (main.py), nie
synchron im Anfrage-Pfad: ein einzelner HTTP-GET auf eine winzige
config.yaml, kein GitHub-API-Aufruf, kein Token, kein Rate-Limit-Risiko.
Jeder Netzwerkfehler (kein Internet, GitHub nicht erreichbar, Timeout) wird
still geschluckt — die Prüfung selbst ist optional, ihr Ausfall darf nie den
Wartungsplaner oder eine Seite stören."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

VERSION_CHECK_URL = "https://raw.githubusercontent.com/bertel2020/HA-Apps/main/zeitarchiv/config.yaml"
VERSION_CHECK_SETTING = "version_check_cache"
# Einmal täglich reicht für eine Anwendung, die üblicherweise in Wochen- bis
# Monatsabständen neue Versionen bekommt — kein Grund, GitHub öfter zu fragen.
VERSION_CHECK_MIN_INTERVAL_SECONDS = 24 * 60 * 60
VERSION_CHECK_TIMEOUT_SECONDS = 5

_VERSION_LINE_RE = re.compile(r'^version:\s*"?([0-9][0-9A-Za-z.\-]*)"?\s*$', re.MULTILINE)


def _parse_version(text: str) -> tuple[int, ...]:
    """Rein numerischer Vergleich (1.2.3 -> (1,2,3)) — reicht für die
    einfache x.y.z-Zählung dieser App, ohne eine Versions-Vergleichsbibliothek
    für Suffixe wie -beta einzuführen, die hier nie vorkommen."""
    return tuple(int(part) for part in re.findall(r"\d+", text))


def fetch_latest_version() -> str | None:
    try:
        request = urllib.request.Request(
            VERSION_CHECK_URL, headers={"User-Agent": "zeitarchiv-version-check"}
        )
        with urllib.request.urlopen(request, timeout=VERSION_CHECK_TIMEOUT_SECONDS) as response:
            text = response.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    match = _VERSION_LINE_RE.search(text)
    return match.group(1) if match else None


def is_stale(index, min_interval_seconds: float = VERSION_CHECK_MIN_INTERVAL_SECONDS) -> bool:
    raw = index.get_setting(VERSION_CHECK_SETTING)
    if raw is None:
        return True
    try:
        checked_at = json.loads(raw).get("checked_at")
    except (json.JSONDecodeError, AttributeError):
        return True
    return checked_at is None or time.time() - checked_at >= min_interval_seconds


def refresh_if_stale(index) -> None:
    if not is_stale(index):
        return
    latest_version = fetch_latest_version()
    payload = {"checked_at": time.time(), "latest_version": latest_version}
    index.set_setting(VERSION_CHECK_SETTING, json.dumps(payload, ensure_ascii=False))


def get_cached_state(index) -> dict | None:
    raw = index.get_setting(VERSION_CHECK_SETTING)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def latest_known_version(index) -> str | None:
    state = get_cached_state(index)
    return state.get("latest_version") if state else None


def update_available(index, current_version: str) -> bool:
    latest = latest_known_version(index)
    if not latest:
        return False
    try:
        return _parse_version(latest) > _parse_version(current_version)
    except ValueError:
        return False
