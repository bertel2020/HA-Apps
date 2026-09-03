"""Verbindungsstatus der Home-Assistant-Integration ("Zeitarchiv" unter
custom_components) — aus dem optionalen Header X-Zeitarchiv-Integration-
Version, den die Integration auf jeden authentifizierten Request mitschickt.
Rein informativ (Anzeige in den Settings, siehe main.py), ohne Einfluss auf
Auth oder Schreibpfad."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

INTEGRATION_INFO_SETTING = "ha_integration_info"
# Ein SQLite-Write bei JEDEM Request wäre unnötig teuer bei häufigen
# /api/write-Batches (bis zu alle 5s) — ein aktualisierter "zuletzt
# gesehen"-Zeitstempel alle 5 Minuten reicht für die Anzeige. Ein
# Versionswechsel (z. B. nach einem Integrations-Update) wird davon
# unabhängig immer sofort übernommen, statt bis zu 5 Minuten zu verzögern.
MIN_PERSIST_INTERVAL_SECONDS = 5 * 60


def record_seen(index, version: str) -> None:
    now = time.time()
    current = get_info(index)
    if (
        current is not None
        and current.get("version") == version
        and now - (current.get("last_seen") or 0) < MIN_PERSIST_INTERVAL_SECONDS
    ):
        return
    index.set_setting(
        INTEGRATION_INFO_SETTING,
        json.dumps({"version": version, "last_seen": now}, ensure_ascii=False),
    )


def get_info(index) -> dict | None:
    raw = index.get_setting(INTEGRATION_INFO_SETTING)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# Erste Version, die den X-Zeitarchiv-Integration-Version-Header überhaupt
# mitschickt (siehe api_routes.py) und /api/notices konsumieren kann (Repairs/
# binary_sensor). Muss zusammen mit dem Versionsbump in custom_components/
# zeitarchiv/manifest.json gepflegt werden, sobald diese Integrations-Version
# tatsächlich veröffentlicht ist — bis dahin bewusst ein Platzhalter.
MIN_SUPPORTED_INTEGRATION_VERSION = "0.15.0"


def _parse_version(version: str) -> tuple[int, ...]:
    """Rein numerischer Vergleich (0.14.0 -> (0,14,0)) — reicht für die
    einfache x.y.z-Zählung der Integration, siehe gleiches Vorgehen in
    version_check.py."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def is_outdated(version: str) -> bool:
    return _parse_version(version) < _parse_version(MIN_SUPPORTED_INTEGRATION_VERSION)


# Rein informative Prüfung auf eine neuere Integrations-Version im offiziellen
# Repository — unabhängig von MIN_SUPPORTED_INTEGRATION_VERSION (das ist ein
# Mindestanforderungs-Gate, kein "ist neuer verfügbar"-Hinweis). Gleiches
# Vorgehen wie version_check.py für die App selbst: täglicher GET auf eine
# winzige Datei, nie synchron im Anfrage-Pfad, jeder Netzwerkfehler wird
# still geschluckt.
INTEGRATION_VERSION_CHECK_URL = (
    "https://raw.githubusercontent.com/bertel2020/HA-Zeitarchiv/main/"
    "custom_components/zeitarchiv/manifest.json"
)
INTEGRATION_VERSION_CHECK_SETTING = "integration_version_check_cache"
INTEGRATION_VERSION_CHECK_MIN_INTERVAL_SECONDS = 24 * 60 * 60
INTEGRATION_VERSION_CHECK_TIMEOUT_SECONDS = 5


def fetch_latest_integration_version() -> str | None:
    try:
        request = urllib.request.Request(
            INTEGRATION_VERSION_CHECK_URL,
            headers={"User-Agent": "zeitarchiv-integration-version-check"},
        )
        with urllib.request.urlopen(
            request, timeout=INTEGRATION_VERSION_CHECK_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read(4096))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, str) else None


def integration_version_check_is_stale(
    index, min_interval_seconds: float = INTEGRATION_VERSION_CHECK_MIN_INTERVAL_SECONDS
) -> bool:
    raw = index.get_setting(INTEGRATION_VERSION_CHECK_SETTING)
    if raw is None:
        return True
    try:
        checked_at = json.loads(raw).get("checked_at")
    except (json.JSONDecodeError, AttributeError):
        return True
    return checked_at is None or time.time() - checked_at >= min_interval_seconds


def refresh_integration_version_check_if_stale(index) -> None:
    if not integration_version_check_is_stale(index):
        return
    latest_version = fetch_latest_integration_version()
    payload = {"checked_at": time.time(), "latest_version": latest_version}
    index.set_setting(INTEGRATION_VERSION_CHECK_SETTING, json.dumps(payload, ensure_ascii=False))


def latest_known_integration_version(index) -> str | None:
    raw = index.get_setting(INTEGRATION_VERSION_CHECK_SETTING)
    if raw is None:
        return None
    try:
        return json.loads(raw).get("latest_version")
    except json.JSONDecodeError:
        return None


def integration_update_kind(installed: str, latest: str) -> str | None:
    """None (aktuell) | "bugfix" (nur Patch neuer) | "feature" (Minor/Major
    neuer) — zweistufig und unabhängig von MIN_SUPPORTED_INTEGRATION_VERSION,
    das nur die Mindestanforderung für den Rückkanal selbst prüft."""
    installed_t = _parse_version(installed)
    latest_t = _parse_version(latest)
    if installed_t >= latest_t:
        return None
    return "bugfix" if installed_t[:2] == latest_t[:2] else "feature"
