"""Abruf des aktuellen RAM-Verbrauchs dieses Addon-Containers über die
Home-Assistant-Supervisor-API (Konzept "Über Zeitarchiv": RAM-Anzeige).
Dasselbe Zugriffsmuster wie log_source.fetch_supervisor_log_text (Token aus
SUPERVISOR_TOKEN, http://supervisor/..., 5s Timeout)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from .formatting import format_size

if TYPE_CHECKING:
    from .storage.index import Index

logger = logging.getLogger(__name__)

SUPERVISOR_STATS_URL = "http://supervisor/addons/self/stats"


def fetch_memory_usage_bytes() -> int:
    """Liest memory_usage (Bytes) aus der Supervisor-Stats-API für dieses
    Addon. Wirft RuntimeError, wenn kein Supervisor verfügbar ist (z. B.
    lokale Entwicklung ohne HA-Supervisor), die Anfrage fehlschlägt oder die
    Antwort nicht das erwartete Format hat."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("Supervisor ist in dieser Umgebung nicht verfügbar")
    request = urllib.request.Request(
        SUPERVISOR_STATS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Zeitarchiv/Stats",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("Supervisor-Statistik konnte nicht geladen werden") from exc
    try:
        return int(payload["data"]["memory_usage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Supervisor-Statistik hat unerwartetes Format") from exc


def describe_memory_usage() -> str:
    """Formatierter RAM-Verbrauch für "Über Zeitarchiv", oder ein Hinweistext
    außerhalb des Supervisor-Umfelds (z. B. lokale Entwicklung)."""
    try:
        return format_size(fetch_memory_usage_bytes())
    except RuntimeError:
        return "nicht verfügbar (kein Supervisor)"


def maybe_record_memory_snapshot(index: "Index") -> None:
    """Schreibt einen stündlichen RAM-Datenpunkt, falls fällig (siehe
    Index.is_memory_snapshot_due) — ruft den Supervisor nur bei tatsächlicher
    Fälligkeit auf, nicht bei jedem Wartungsplaner-Tick."""
    if not index.is_memory_snapshot_due():
        return
    try:
        memory_usage_bytes = fetch_memory_usage_bytes()
    except RuntimeError:
        return
    index.record_memory_snapshot(memory_usage_bytes)
    logger.debug("Stündlicher RAM-Schnappschuss gespeichert")
