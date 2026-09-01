"""Zentrale Sammlung aktiver System-Hinweise für das Meldungs-Center in der
Topnav (Glocken-Icon, siehe _topnav.html) — läuft bei JEDEM Seitenaufruf mit
(context_processors in main.py), deshalb bewusst nur günstige Abfragen
(PRAGMA-Werte, LIMIT-1-Queries), keine vollständigen Tabellen-Scans. Kein
eigener Persistenz-/Dismiss-Mechanismus für die Meldungen SELBST: jede wird
direkt aus dem aktuellen Zustand abgeleitet und verschwindet automatisch,
sobald die zugrunde liegende Ursache behoben ist (Index optimiert, nächstes
Backup erfolgreich, …).

IDs folgen dem Schema "namespace.kind" (z. B. "system.index_optimization"),
optional "#instance" für Meldungsarten, die mehrfach gleichzeitig auftreten
können (z. B. künftig "entity.duplicate_values#sensor.wohnzimmer_temperatur")
— siehe Konzept-Diskussion zur Skalierbarkeit. "#" bewusst statt "." als
Trenner vor der Instanz, weil Entity-IDs selbst schon Punkte enthalten und
das Parsen sonst zweideutig würde.

Stummschaltung (siehe mute_notice()) ist nur für "info"/"warn" erlaubt, nie
für "error" — ein echter Fehler soll sich nie dauerhaft verstecken lassen."""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import version_check
from .formatting import format_size
from .index_optimization import get_index_optimization_state
from .version import APP_VERSION

MUTABLE_SEVERITIES = {"info", "warn"}
NOTICE_MUTES_SETTING = "notice_mutes"

# Feste Presets statt eines Datums-Pickers im ohnehin schon engen
# Meldungs-Dropdown. "forever" (None) bleibt trotzdem sicher: der Fingerprint
# in _is_muted() lässt die Meldung wieder auftauchen, sobald sich ihr Inhalt
# merklich ändert — eine dauerhaft stummgeschaltete Empfehlung kann sich so
# nicht hinter einem längst überholten Zustand verstecken.
SNOOZE_PRESETS = {
    "1h": 60 * 60,
    "1d": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
    "forever": None,
}
SNOOZE_LABELS = {
    "1h": "1 Stunde", "1d": "1 Tag", "7d": "7 Tage", "30d": "30 Tage", "forever": "Dauerhaft",
}


def build_notices(index, index_path: Path) -> list[dict]:
    """Ungefilterte, aktuell aktive Meldungen — auch stummgeschaltete sind
    hier noch enthalten (main.py braucht das z. B. beim Stummschalten selbst,
    um Titel/Text/Severity serverseitig nachzuschlagen statt dem Client zu
    vertrauen). Für die Anzeige in der Topnav siehe collect_notices()."""
    notices: list[dict] = []

    # TODO DEMO — wieder entfernen. Nur zum Anschauen der "info"-Severity im
    # Meldungs-Center eingefügt, es gibt dafür noch keine echte Quelle.
    notices.append({
        "id": "demo.info_example",
        "severity": "info",
        "title": "Beispiel: Info-Meldung",
        "detail": "So sieht eine rein informative Meldung aus (severity=info) — nur zur Ansicht, keine echte Quelle.",
        "meta": "Demo",
        "link": "/settings",
    })

    latest_version = version_check.latest_known_version(index)
    if latest_version and version_check.update_available(index, APP_VERSION):
        notices.append({
            "id": "system.update_available",
            "severity": "info",
            "title": "Update verfügbar",
            "detail": f"Version {latest_version} ist verfügbar (aktuell installiert: {APP_VERSION}).",
            "meta": "Über Zeitarchiv",
            "link": "/settings#ueber",
        })

    optimization = get_index_optimization_state(index, index_path)
    if optimization["recommended"]:
        ratio_percent = round(optimization["reclaimable_ratio"] * 100)
        notices.append({
            "id": "system.index_optimization",
            "severity": "warn",
            "title": "Index-Optimierung empfohlen",
            "detail": (
                f"Indexdatei ist {format_size(optimization['file_bytes'])}, davon "
                f"{format_size(optimization['reclaimable_bytes'])} wiederherstellbar "
                f"({ratio_percent} %)."
            ),
            "meta": "Statistik · System",
            "link": "/statistik/index",
        })

    last_backup = index.list_backup_jobs(1)
    if last_backup and last_backup[0]["status"] == "failed":
        notices.append({
            "id": "backup.job_failed",
            "severity": "error",
            "title": "Backup fehlgeschlagen",
            "detail": last_backup[0]["error"] or "Die letzte Sicherung konnte nicht abgeschlossen werden.",
            "meta": "Sicherung",
            "link": "/backup",
        })

    last_retention = index.list_retention_jobs(1)
    if last_retention and last_retention[0]["status"] == "failed":
        notices.append({
            "id": "retention.job_failed",
            "severity": "error",
            "title": "Automatische Aufbewahrung fehlgeschlagen",
            "detail": (
                last_retention[0]["error"]
                or "Der letzte geplante Bereinigungslauf ist fehlgeschlagen."
            ),
            "meta": "Aufbewahrung",
            "link": "/settings",
        })

    # Einmal zentral statt in jedem Eintrag oben von Hand gesetzt — kann bei
    # neuen Meldungsarten nicht vergessen werden. Für main.py (Server-seitige
    # Prüfung vor dem Stummschalten) und die Anzeige des 🔕-Icons im Template.
    for notice in notices:
        notice["mutable"] = notice["severity"] in MUTABLE_SEVERITIES
    return notices


def _load_mutes(index) -> dict:
    raw = index.get_setting(NOTICE_MUTES_SETTING, "{}")
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_mutes(index, mutes: dict) -> None:
    index.set_setting(NOTICE_MUTES_SETTING, json.dumps(mutes, ensure_ascii=False))


def _is_muted(notice: dict, mute_entry: dict | None, now: float) -> bool:
    if not mute_entry:
        return False
    until = mute_entry.get("until")
    if until is not None and until < now:
        return False
    # Fingerprint: nur stumm, solange sich der Inhalt seit dem Stummschalten
    # nicht geändert hat — sonst könnte eine später deutlich verschärfte
    # Bedingung hinter einer alten Stummschaltung verschwinden.
    snapshot = mute_entry.get("detail_snapshot")
    return snapshot is None or snapshot == notice["detail"]


def collect_notices(index, index_path: Path) -> list[dict]:
    """Für die Anzeige in der Topnav — build_notices() abzüglich aktuell
    gültiger Stummschaltungen."""
    mutes = _load_mutes(index)
    now = time.time()
    return [
        notice for notice in build_notices(index, index_path)
        if not _is_muted(notice, mutes.get(notice["id"]), now)
    ]


def mute_notice(index, notice_id: str, title: str, detail: str, meta: str, until: float | None = None) -> None:
    mutes = _load_mutes(index)
    mutes[notice_id] = {
        "title": title,
        "detail_snapshot": detail,
        "meta": meta,
        "muted_at": time.time(),
        "until": until,
    }
    _save_mutes(index, mutes)


def unmute_notice(index, notice_id: str) -> None:
    mutes = _load_mutes(index)
    if mutes.pop(notice_id, None) is not None:
        _save_mutes(index, mutes)


def list_muted_notices(index) -> list[dict]:
    """Für Einstellungen → Meldungen — zeigt gespeicherte Stummschaltungen
    unabhängig davon, ob die zugehörige Meldung gerade noch aktiv wäre
    (deshalb eine eigene title/detail/meta-Kopie beim Stummschalten, statt
    auf die aktuell aktive Liste angewiesen zu sein)."""
    mutes = _load_mutes(index)
    entries = [
        {
            "id": notice_id,
            "title": entry.get("title", notice_id),
            "detail": entry.get("detail_snapshot", ""),
            "meta": entry.get("meta", ""),
            "muted_at": entry.get("muted_at"),
            "until": entry.get("until"),
        }
        for notice_id, entry in mutes.items()
    ]
    entries.sort(key=lambda e: e["muted_at"] or 0, reverse=True)
    return entries
