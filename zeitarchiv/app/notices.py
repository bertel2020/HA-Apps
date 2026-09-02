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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import tips as tips_mod
from . import version_check
from .energiedashboard_routes import SETTING_HOURLY_BACKFILL_PENDING
from .formatting import format_int, format_size
from .index_optimization import get_index_optimization_state
from .report_routes import SOURCE_LABELS
from .storage import import_reports
from .storage.index import VALUE_FILTER_HEARTBEAT_SECONDS
from .version import APP_VERSION

MUTABLE_SEVERITIES = {"info", "warn"}
NOTICE_MUTES_SETTING = "notice_mutes"
TIPS_ENABLED_SETTING = "tips_enabled"
# Eskalierende Schwellwerte statt eines einzelnen — je länger eine Entität
# schon schweigt, desto ernster die Severity. Bänder sind exklusiv (siehe
# _bucket_inactive_entities): dieselbe Entität zählt nie in mehr als einer
# gleichzeitig, sonst würde eine 10 Tage inaktive Entität info UND warn UND
# error gleichzeitig auslösen — redundant statt aussagekräftig.
INACTIVE_ENTITY_INFO_DAYS = 1
INACTIVE_ENTITY_WARN_DAYS = 3
INACTIVE_ENTITY_ERROR_DAYS = 7
# Dieselbe Ableitung wie main.py's _VALUE_FILTER_GAP_FLOOR_MINUTES — der
# Wertänderungsfilter garantiert spätestens alle VALUE_FILTER_HEARTBEAT_SECONDS
# ein Lebenszeichen, eine engere Lücken-Schwelle löst dadurch regelmäßig
# Fehlalarme aus. main.py verhindert das für NEUE Aktivierungen automatisch
# (siehe _should_raise_gap_threshold_for_value_filter); diese Meldung deckt
# bereits bestehende Entitäten mit der riskanten Kombination ab, die die
# Automatik nicht rückwirkend anfasst.
VALUE_FILTER_GAP_FLOOR_MINUTES = VALUE_FILTER_HEARTBEAT_SECONDS // 60
# Täglich statt mehrtägig (Konzept-Entscheidung): nur so garantiert "morgen"
# auch wirklich einen ANDEREN Tipp, wenn der heutige ausgeblendet wurde —
# siehe hide_tip_today(). Bei einem mehrtägigen Fenster wäre der übernächste
# Tag noch derselbe (ausgeblendete) Tipp gewesen.
TIP_ROTATION_DAYS = 1

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


def build_notices(
    index,
    index_path: Path,
    tz: ZoneInfo,
    purge_totals: dict,
    storage_reconcile: dict | None,
    stale_entity_count: int,
) -> list[dict]:
    """Ungefilterte, aktuell aktive Meldungen — auch stummgeschaltete sind
    hier noch enthalten (main.py braucht das z. B. beim Stummschalten selbst,
    um Titel/Text/Severity serverseitig nachzuschlagen statt dem Client zu
    vertrauen). Für die Anzeige in der Topnav siehe collect_notices().

    purge_totals/storage_reconcile/stale_entity_count kommen von main.py
    (jeweils aus bereits vorhandenen, günstigen Quellen: _load_purge_preview(),
    dem In-Prozess-Zustand _storage_reconcile_last, _count_stale_entities())
    statt hier selbst gelesen zu werden — vermeidet zweite, abweichende
    Kopien dieser main.py-spezifischen Zugriffe in diesem Modul."""
    notices: list[dict] = []

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

    # errors sind Entitäten, die gar nicht erst geprüft werden konnten — ein
    # echtes ungelöstes Problem, unabhängig vom Reparatur-Status.
    if storage_reconcile and storage_reconcile.get("errors"):
        error_count = len(storage_reconcile["errors"])
        notices.append({
            "id": "system.storage_reconcile_errors",
            "severity": "warn",
            "title": "Speicherindex-Prüfung unvollständig",
            "detail": (
                f"{error_count} Entität{'en' if error_count != 1 else ''} "
                f"{'konnten' if error_count != 1 else 'konnte'} beim letzten Abgleich "
                "nicht vollständig geprüft werden."
            ),
            "meta": "Speicherplatz",
            "link": "/housekeeping#speicherplatz",
        })

    # mismatches: der automatische Hintergrundabgleich repariert sie immer
    # sofort (main.py _run_storage_reconcile, repair=True) — dann nur info,
    # rein zur Kenntnis. Ein MANUELLER Klick auf "Index prüfen" ist dagegen
    # zunächst nur lesend (siehe _settings_storage_index_form.html); bleiben
    # dabei gefundene Abweichungen unrepariert stehen, ist das noch zu tun —
    # dann warn, weil eine echte Handlung (Button "Index reparieren") fehlt.
    if storage_reconcile and storage_reconcile.get("mismatches"):
        mismatch_count = len(storage_reconcile["mismatches"])
        repaired = bool(storage_reconcile.get("repaired"))
        notices.append({
            "id": "system.storage_reconcile_mismatches",
            "severity": "info" if repaired else "warn",
            "title": "Index-Abweichungen automatisch behoben" if repaired else "Index-Abweichungen gefunden",
            "detail": (
                f"{mismatch_count} Abweichung{'en' if mismatch_count != 1 else ''} zwischen Index und "
                "gespeicherten Rohdaten beim letzten Abgleich gefunden"
                + (
                    " und automatisch korrigiert — betrifft nur die Index-Metadaten, nicht die Messwerte."
                    if repaired
                    else ", aber noch nicht behoben."
                )
            ),
            "meta": "Speicherplatz",
            "link": "/housekeeping#speicherplatz",
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
    elif index.get_setting("backup_schedule", "off") == "off":
        notices.append({
            "id": "backup.no_schedule",
            "severity": "info",
            "title": "Kein automatisches Backup eingerichtet",
            "detail": "Es ist aktuell kein Zeitplan für regelmäßige Sicherungen aktiv.",
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
            "link": "/housekeeping#aufbewahrung",
        })
    elif index.get_setting("retention_enforcement_schedule", "off") == "off":
        limited_count = sum(1 for e in index.list_entities() if e["retention"] != "unlimited")
        if limited_count:
            notices.append({
                "id": "retention.enforcement_disabled",
                "severity": "warn",
                "title": "Aufbewahrung konfiguriert, aber nicht aktiv",
                "detail": (
                    f"{limited_count} Entität{'en haben' if limited_count != 1 else ' hat'} eine "
                    "begrenzte Aufbewahrungsfrist, die automatische Durchsetzung ist aber ausgeschaltet "
                    "— abgelaufene Werte werden dadurch nie automatisch entfernt."
                ),
                "meta": "Aufbewahrung",
                "link": "/housekeeping#aufbewahrung",
            })

    import_reports_list = import_reports.list_all(index_path.parent)
    if import_reports_list and import_reports_list[0]["status"] in ("failed", "partial"):
        last_report = import_reports_list[0]
        source_label = SOURCE_LABELS.get(last_report.get("source_type"), "Unbekannt")
        if last_report["status"] == "failed":
            notices.append({
                "id": "import.job_failed",
                "severity": "error",
                "title": "Import fehlgeschlagen",
                "detail": f"Der letzte {source_label}-Import konnte nicht abgeschlossen werden.",
                "meta": "Import",
                "link": "/import?tab=reports",
            })
        else:
            notices.append({
                "id": "import.job_failed",
                "severity": "warn",
                "title": "Import unvollständig",
                "detail": f"Der letzte {source_label}-Import wurde nur teilweise abgeschlossen.",
                "meta": "Import",
                "link": "/import?tab=reports",
            })

    conflict_count = sum(
        1 for e in index.list_entities()
        if e["value_filter"] == "decimals"
        and e["gap_threshold"] != "off"
        and e["gap_threshold"].isdigit()
        and int(e["gap_threshold"]) < VALUE_FILTER_GAP_FLOOR_MINUTES
    )
    if conflict_count:
        notices.append({
            "id": "entities.value_filter_gap_conflict",
            "severity": "warn",
            "title": "Wertänderungsfilter und Lücken-Erkennung im Konflikt",
            "detail": (
                f"{conflict_count} Entität{'en haben' if conflict_count != 1 else ' hat'} den "
                "Wertänderungsfilter aktiv, aber eine Lücken-Erkennung unter 6 Stunden — das führt "
                "zu falschen Lücken-Meldungen, weil der Filter selbst bis zu 6 Stunden lang keine "
                "neuen Werte schreibt."
            ),
            "meta": "Entitäten",
            "link": "/entities",
        })

    # entities.hourly_rollup wird bereits automatisch synchron gehalten (beim
    # Speichern der Energiedashboard-Konfiguration UND einmalig beim Start,
    # siehe sync_hourly_rollup_flags/_for_current_config) — eine Zähler-Rolle
    # OHNE das Flag sollte im laufenden Betrieb praktisch nie vorkommen. Was
    # tatsächlich vorkommt: das Flag ist gesetzt, aber der rückwirkende
    # Stunden-Rollup für bereits archivierte Monate läuft noch (der
    # Wartungsplaner arbeitet die Warteschlange mit einer Entität pro 30s-Tick
    # ab) — bis dahin kann das Tageslastprofil nach Wochentag für ältere
    # Monate unvollständig aussehen. Rein informativ, löst sich von selbst.
    try:
        hourly_backfill_pending = json.loads(index.get_setting(SETTING_HOURLY_BACKFILL_PENDING, "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        hourly_backfill_pending = []
    if isinstance(hourly_backfill_pending, list) and hourly_backfill_pending:
        pending_count = len(hourly_backfill_pending)
        notices.append({
            "id": "energiedashboard.hourly_backfill_pending",
            "severity": "info",
            "title": "Tageslastprofil wird noch vervollständigt",
            "detail": (
                f"{pending_count} Zähler-Entität{'en holen' if pending_count != 1 else ' holt'} im "
                "Energiedashboard gerade rückwirkend ihr Stunden-Rollup für bereits archivierte Monate "
                "nach — das Tageslastprofil nach Wochentag kann bis dahin für ältere Monate unvollständig sein."
            ),
            "meta": "Energiedashboard",
            "link": "/energiedashboard",
        })

    removable_rows = purge_totals.get("removable_rows", 0)
    if removable_rows:
        entities_affected = purge_totals.get("entities_affected", 0)
        notices.append({
            "id": "housekeeping.purge_available",
            "severity": "warn",
            "title": "Endgültige Bereinigung möglich",
            "detail": (
                f"{format_int(removable_rows)} markierte Datensätze über "
                f"{entities_affected} Entität{'en' if entities_affected != 1 else ''} "
                "können endgültig entfernt werden."
            ),
            "meta": "Speicherplatz",
            "link": "/housekeeping#speicherplatz",
        })

    # Derselbe stündliche globale Duplikat-Schnappschuss, der bereits die
    # Housekeeping-Tabelle treibt (siehe main.py _refresh_duplicate_snapshot_if_stale) —
    # nur ein zusätzlicher, günstiger Blick auf denselben Cache.
    duplicate_rows = (index.get_duplicate_snapshot() or {}).get("rows", [])
    if duplicate_rows:
        duplicate_total = sum(row["count"] for row in duplicate_rows)
        notices.append({
            "id": "housekeeping.duplicates_found",
            "severity": "warn",
            "title": "Duplikate gefunden",
            "detail": (
                f"{format_int(duplicate_total)} doppelte Zeitstempel über "
                f"{len(duplicate_rows)} Entität{'en' if len(duplicate_rows) != 1 else ''} "
                "in den letzten 30 Tagen."
            ),
            "meta": "Duplikate",
            "link": "/housekeeping#duplikate",
        })

    if stale_entity_count:
        notices.append({
            "id": "housekeeping.rotation_pending",
            "severity": "info",
            "title": "Rotation ausstehend",
            "detail": (
                f"{stale_entity_count} Entität{'en haben' if stale_entity_count != 1 else ' hat'} "
                "eine noch nicht rotierte Hot-Datei aus einem vergangenen Monat."
            ),
            "meta": "Rotation",
            "link": "/housekeeping#rotation",
        })

    inactive_counts = _bucket_inactive_entities(index, tz)
    # Schwerste Stufe zuerst, damit sie bei gleichzeitig mehreren nicht-leeren
    # Bändern (unwahrscheinlich, aber möglich) oben in der Liste steht.
    for tier, threshold_days, severity in (
        ("error", INACTIVE_ENTITY_ERROR_DAYS, "error"),
        ("warn", INACTIVE_ENTITY_WARN_DAYS, "warn"),
        ("info", INACTIVE_ENTITY_INFO_DAYS, "info"),
    ):
        count = inactive_counts[tier]
        if not count:
            continue
        notices.append({
            "id": f"housekeeping.inactive_entities_{tier}",
            "severity": severity,
            "title": "Inaktive Entitäten gefunden",
            "detail": (
                f"{count} Entität{'en' if count != 1 else ''} "
                f"{'haben' if count != 1 else 'hat'} seit mindestens "
                f"{threshold_days} Tag{'en' if threshold_days != 1 else ''} keinen neuen Wert geliefert."
            ),
            "meta": "Housekeeping",
            "link": "/housekeeping#entitaeten",
        })

    # Ganz am Ende (niedrigste Priorität) — ein Tipp soll nie vor einer
    # echten Warnung/einem Fehler stehen. tips_enabled() global abschaltbar,
    # siehe Einstellungen → Meldungen.
    tip_notice = _current_tip_notice(index, tz)
    if tip_notice is not None:
        notices.append(tip_notice)

    # Einmal zentral statt in jedem Eintrag oben von Hand gesetzt — kann bei
    # neuen Meldungsarten nicht vergessen werden. Für main.py (Server-seitige
    # Prüfung vor dem Stummschalten) und die Anzeige des 🔕-Icons im Template.
    # setdefault statt Überschreiben: der Tipp trägt sein "mutable" bereits
    # explizit (immer False, siehe _current_tip_notice) — er nutzt bewusst
    # NICHT das allgemeine Stummschalt-System (siehe hide_tip_today()).
    for notice in notices:
        notice.setdefault("mutable", notice["severity"] in MUTABLE_SEVERITIES)
    return notices


def _bucket_inactive_entities(index, tz: ZoneInfo) -> dict[str, int]:
    """Zählt Entitäten ohne neuen Wert (entities.last_ts, ohnehin vorhanden)
    in drei EXKLUSIVE Bänder nach Tagen seit dem letzten Wert — dieselbe
    günstige Grundlage wie das "Inaktive Entitäten"-Dropdown auf der
    Housekeeping-Seite (main.py, _stale_entities_context), hier aber bewusst
    nur Zählungen statt der vollen, formatierten Zeilenliste. Nie empfangene
    Entitäten (last_ts NULL) zählen ins schwerste Band (error) — für sie
    gibt es kein "seit wann", nur "noch nie"."""
    now_ts = datetime.now(tz).timestamp()
    counts = {"info": 0, "warn": 0, "error": 0}
    for entity in index.list_entities():
        last_ts = entity["last_ts"]
        if last_ts is None:
            counts["error"] += 1
            continue
        days = (now_ts - last_ts) / 86400
        if days >= INACTIVE_ENTITY_ERROR_DAYS:
            counts["error"] += 1
        elif days >= INACTIVE_ENTITY_WARN_DAYS:
            counts["warn"] += 1
        elif days >= INACTIVE_ENTITY_INFO_DAYS:
            counts["info"] += 1
    return counts


def tips_enabled(index) -> bool:
    return index.get_setting(TIPS_ENABLED_SETTING, "1") != "0"


def set_tips_enabled(index, enabled: bool) -> None:
    index.set_setting(TIPS_ENABLED_SETTING, "1" if enabled else "0")


TIP_HIDDEN_SETTING = "tip_hidden_today"


def _load_hidden_tip(index) -> dict | None:
    """{"slug": ..., "ordinal": ...} des zuletzt für einen bestimmten Tag
    ausgeblendeten Tipps, oder None. Nur EIN Eintrag nötig (nicht pro Tipp):
    an einem gegebenen Tag ist immer nur der jeweils fällige Tipp überhaupt
    sichtbar (TIP_ROTATION_DAYS=1), also auch nur er ausblendbar."""
    raw = index.get_setting(TIP_HIDDEN_SETTING, "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and "slug" in data and "ordinal" in data else None


def is_tip_hidden_today(index, slug: str, ordinal: int) -> bool:
    hidden = _load_hidden_tip(index)
    return hidden is not None and hidden["slug"] == slug and hidden["ordinal"] == ordinal


def hide_tip_today(index, slug: str, ordinal: int) -> None:
    index.set_setting(TIP_HIDDEN_SETTING, json.dumps({"slug": slug, "ordinal": ordinal}))


def unhide_tip_today(index) -> None:
    index.set_setting(TIP_HIDDEN_SETTING, "")


def resolve_today_tip(tz: ZoneInfo) -> tuple[dict, int]:
    """(Tipp, Kalendertag-Ordnungszahl) des heute fälligen Tipps — einzige
    Stelle, die tips.rotation_order() mit TIP_ROTATION_DAYS aufruft, damit
    "heute" überall in diesem Modul konsistent berechnet wird."""
    ordinal = datetime.now(tz).toordinal()
    return tips_mod.rotation_order(ordinal, TIP_ROTATION_DAYS)[0], ordinal


def _current_tip_notice(index, tz: ZoneInfo) -> dict | None:
    """Der heute fällige Tipp — None, wenn Tipps global deaktiviert sind oder
    genau dieser Tipp für heute ausgeblendet wurde (siehe hide_tip_today()).
    Bewusst KEIN automatischer Ersatz aus der Rotation mehr, wenn ausgeblendet
    — das war das vorherige Verhalten (Stummschalt-System, "erster nicht
    stummgeschaltete Tipp"), fühlte sich aber wie ein Sprung in der Rotation
    an statt wie ein einfaches "heute übersprungen". Mit täglicher Rotation
    kommt ohnehin morgen ein anderer Tipp fällig."""
    if not tips_enabled(index):
        return None
    tip, ordinal = resolve_today_tip(tz)
    if is_tip_hidden_today(index, tip["slug"], ordinal):
        return None
    return {
        "id": f"tips.{tip['slug']}",
        "severity": "info",
        "title": tip["title"],
        "detail": tip["detail"],
        "meta": tip["meta"],
        "link": tip.get("link"),
        # Eigenes Ausblenden statt des allgemeinen Stummschalt-Systems (siehe
        # hide_tip_today/_settings_tips_form.html) — taucht deshalb auch nicht
        # in Einstellungen → Meldungen → Stummgeschaltet auf, das ist nur für
        # das allgemeine System mit seinen Dauer-Presets gedacht.
        "mutable": False,
    }


def list_tips_with_status(index, tz: ZoneInfo) -> list[dict]:
    """Für den "Alle Tipps"-Dialog (Einstellungen → Meldungen) — jeder Tipp
    mit Kennzeichnung, ob er heute fällig ist und ob er (falls ja) gerade
    ausgeblendet ist. Nur der heute fällige Tipp lässt sich aus-/einblenden,
    siehe _current_tip_notice()."""
    today_tip, ordinal = resolve_today_tip(tz)
    hidden_today = is_tip_hidden_today(index, today_tip["slug"], ordinal)
    return [
        {
            **tip,
            "is_today": tip["slug"] == today_tip["slug"],
            "hidden_today": tip["slug"] == today_tip["slug"] and hidden_today,
        }
        for tip in tips_mod.TIPS
    ]


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


def collect_notices(
    index,
    index_path: Path,
    tz: ZoneInfo,
    purge_totals: dict,
    storage_reconcile: dict | None,
    stale_entity_count: int,
) -> list[dict]:
    """Für die Anzeige in der Topnav — build_notices() abzüglich aktuell
    gültiger Stummschaltungen (beim Tipp bereits durch _current_tip_notice
    vorgefiltert, dieser Schritt betrifft ihn also nie doppelt)."""
    mutes = _load_mutes(index)
    now = time.time()
    return [
        notice for notice in build_notices(
            index, index_path, tz, purge_totals, storage_reconcile, stale_entity_count
        )
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
