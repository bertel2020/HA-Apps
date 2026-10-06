"""Zugriffe auf die Home-Assistant-Supervisor-API für dieses Addon: RAM-
Verbrauch (Konzept "Über Zeitarchiv") und Selbst-Neustart nach einem Restore
(Konzept "Restore-Rollbacks", 22.09.2026). Dasselbe Zugriffsmuster wie
log_source.fetch_supervisor_log_text (Token aus SUPERVISOR_TOKEN,
http://supervisor/..., kurzer Timeout)."""

from __future__ import annotations

from .i18n import tr

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
SUPERVISOR_RESTART_URL = "http://supervisor/addons/self/restart"


def fetch_memory_usage_bytes() -> int:
    """Liest memory_usage (Bytes) aus der Supervisor-Stats-API für dieses
    Addon. Wirft RuntimeError, wenn kein Supervisor verfügbar ist (z. B.
    lokale Entwicklung ohne HA-Supervisor), die Anfrage fehlschlägt oder die
    Antwort nicht das erwartete Format hat."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError(tr("Supervisor ist in dieser Umgebung nicht verfügbar"))
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
        raise RuntimeError(tr("Supervisor-Statistik konnte nicht geladen werden")) from exc
    try:
        return int(payload["data"]["memory_usage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(tr("Supervisor-Statistik hat unerwartetes Format")) from exc


def restart_addon() -> None:
    """Startet dieses Addon über die Supervisor-API neu — für den
    "Jetzt neu starten"-Dialog nach einem vorgemerkten Restore (Konzept
    "Restore-Rollbacks"): ein Restore wird erst beim nächsten Start
    eingespielt, vorher bleibt die alte Datenbank offen. Wirft RuntimeError,
    wenn kein Supervisor verfügbar ist (z. B. lokale Entwicklung) oder der
    Aufruf fehlschlägt — der Aufrufer zeigt dann den Hinweis, manuell neu zu
    starten, statt eines stillen Fehlers.

    Kehrt normal zurück, sobald Supervisor den Neustart ENTGEGENGENOMMEN
    hat — der Container selbst lebt zu dem Zeitpunkt meist noch kurz weiter,
    genug, um diese Antwort noch auszuliefern, bevor er tatsächlich fällt."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError(tr("Supervisor ist in dieser Umgebung nicht verfügbar"))
    request = urllib.request.Request(
        SUPERVISOR_RESTART_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Zeitarchiv/Restart",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(tr("Neustart über Supervisor fehlgeschlagen")) from exc
    if payload.get("result") != "ok":
        raise RuntimeError(
            tr("Supervisor meldete: {v}", v=payload.get('message') or 'unbekannter Fehler')
        )


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
