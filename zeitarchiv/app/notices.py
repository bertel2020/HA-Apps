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

from . import ha_integration
from . import tips as tips_mod
from . import version_check
from .energiedashboard_routes import CONFIG_SCHEMA_VERSION, SETTING_CONFIG, SETTING_HOURLY_BACKFILL_PENDING
from .formatting import GAP_THRESHOLD_LABELS, format_int, format_resolution, format_size
from .index_optimization import get_index_optimization_state
from .report_routes import SOURCE_LABELS
from .storage import import_reports
from .storage.index import should_raise_gap_threshold
from .version import APP_VERSION

MUTABLE_SEVERITIES = {"info", "warn"}
NOTICE_MUTES_SETTING = "notice_mutes"
TIPS_ENABLED_SETTING = "tips_enabled"
# Takt ist 30s (_maintenance_scheduler_loop) — 5 Minuten sind ~10 verpasste
# Durchläufe, großzügig genug für einzelne Ausreißer, eng genug um einen
# wirklich hängengebliebenen Thread zeitnah zu melden.
SCHEDULER_STALLED_SECONDS = 5 * 60
# Anders als der Wartungsplaner hat der Speicherindex-Hintergrundabgleich
# keinen festen Takt (Laufzeit pro Entität hängt von deren Datenmenge ab) —
# ein einzelner audit_storage_metadata()-Aufruf braucht aber normalerweise
# Millisekunden bis niedrige Sekunden, nicht Minuten. Derselbe Schwellwert
# wie beim Scheduler ist trotzdem großzügig genug, um legitim langsame
# Entitäten nicht fälschlich zu melden, und eng genug, um denselben
# deadlock-artigen Zustand zeitnah zu erkennen, der 0.76.0 bereits den
# Wartungsplaner unsichtbar hängen ließ.
RECONCILE_STALLED_SECONDS = 5 * 60
# Anders als Wartungsplaner/Hintergrundabgleich läuft der Backup-Worker immer
# in einem eigenen, vom Wartungsplaner losgelösten Thread (siehe main.py,
# _run_backup_background()) — dessen eigener Heartbeat _last_scheduler_tick
# bliebe von einem Hang dort also unberührt und würde ihn nie melden. Gleicher
# Schwellwert wie oben, aus denselben Gründen.
BACKUP_WORKER_STALLED_SECONDS = 5 * 60
# Eskalierende Schwellwerte statt eines einzelnen — je länger eine Entität
# schon schweigt, desto ernster die Severity. Bänder sind exklusiv (siehe
# _bucket_inactive_entities): dieselbe Entität zählt nie in mehr als einer
# gleichzeitig, sonst würde eine 10 Tage inaktive Entität info UND warn UND
# error gleichzeitig auslösen — redundant statt aussagekräftig.
INACTIVE_ENTITY_INFO_DAYS = 1
INACTIVE_ENTITY_WARN_DAYS = 3
INACTIVE_ENTITY_ERROR_DAYS = 7
# Prozentbasiert statt fester Byte-Schwelle — bleibt so von der SD-Karte bis
# zur NVMe-SSD sinnvoll (ein fester GB-Wert wäre auf kleinen Installationen
# zu spät, auf großen zu früh dran).
HOST_DISK_WARN_RATIO = 0.10
HOST_DISK_ERROR_RATIO = 0.05
# Täglich statt mehrtägig (Konzept-Entscheidung): nur so garantiert "morgen"
# auch wirklich einen ANDEREN Tipp, wenn der heutige ausgeblendet wurde —
# siehe hide_tip_today(). Bei einem mehrtägigen Fenster wäre der übernächste
# Tag noch derselbe (ausgeblendete) Tipp gewesen.
TIP_ROTATION_DAYS = 1
# Gültige Tarife für should_raise_gap_threshold() (storage/index.py kennt
# bewusst keine Anzeige-Labels).
_GAP_THRESHOLD_MINUTE_TIERS = [int(k) for k in GAP_THRESHOLD_LABELS if k != "off"]

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


def gap_threshold_conflicts(index) -> list[dict]:
    """Entitäten, deren Lücken-Erkennung strukturell nie zutreffen kann —
    ihre Auflösung oder der aktive Wertänderungsfilter erzwingt selbst
    schon einen größeren Mindestabstand zwischen Werten (siehe
    effective_gap_floor_minutes() in storage/index.py). Einzige Quelle für
    die entities.gap_threshold_conflict-Meldung unten (Zähler) UND
    Housekeeping → Konfiguration (main.py, volle Liste) — beide dürfen nie
    unterschiedliche Kriterien verwenden."""
    rows = []
    for e in index.list_entities():
        should_flag, suggested_gap = should_raise_gap_threshold(
            e["gap_threshold"], e["resolution"], e["value_filter"], _GAP_THRESHOLD_MINUTE_TIERS
        )
        if not should_flag:
            continue
        rows.append({
            "entity_id": e["entity_id"],
            "friendly_name": e["custom_name"] or e["friendly_name"] or e["entity_id"],
            "resolution_label": format_resolution(e["resolution"]),
            "gap_threshold_label": GAP_THRESHOLD_LABELS.get(e["gap_threshold"], e["gap_threshold"]),
            "value_filter_active": e["value_filter"] == "decimals",
            "suggested_gap_label": GAP_THRESHOLD_LABELS.get(suggested_gap, suggested_gap),
        })
    return rows


def build_notices(
    index,
    index_path: Path,
    tz: ZoneInfo,
    purge_totals: dict,
    storage_reconcile: dict | None,
    stale_entity_count: int,
    scheduler_last_tick: float,
    reconcile_last_tick: float,
    reconcile_in_progress: bool,
    host_disk_usage: dict | None = None,
    backup_worker_last_tick: float | None = None,
    backup_worker_in_progress: bool = False,
) -> list[dict]:
    """Ungefilterte, aktuell aktive Meldungen — auch stummgeschaltete sind
    hier noch enthalten (main.py braucht das z. B. beim Stummschalten selbst,
    um Titel/Text/Severity serverseitig nachzuschlagen statt dem Client zu
    vertrauen). Für die Anzeige in der Topnav siehe collect_notices().

    purge_totals/storage_reconcile/stale_entity_count/scheduler_last_tick/
    reconcile_last_tick/reconcile_in_progress/host_disk_usage/
    backup_worker_last_tick/backup_worker_in_progress kommen von main.py
    (jeweils aus bereits vorhandenen, günstigen Quellen: _load_purge_preview(),
    dem In-Prozess-Zustand _storage_reconcile_last, _count_stale_entities(),
    _last_scheduler_tick, _last_reconcile_tick, _reconcile_in_progress(),
    _host_disk_usage_cached, _last_backup_worker_tick, _backup_progress.running)
    statt hier selbst gelesen zu werden — vermeidet zweite, abweichende
    Kopien dieser main.py-spezifischen Zugriffe in diesem Modul.
    host_disk_usage ist {"free": int, "total": int} in Bytes oder None (z. B.
    wenn der Scheduler noch nicht gelaufen ist oder shutil.disk_usage
    fehlschlug). backup_worker_last_tick ist None, solange noch nie ein
    Backup gestartet wurde (dann kann auch backup_worker_in_progress nicht
    True sein)."""
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

    integration_info = ha_integration.get_info(index)
    if integration_info and ha_integration.is_outdated(integration_info["version"]):
        notices.append({
            "id": "integration.outdated",
            "severity": "warn",
            "title": "Home-Assistant-Integration veraltet",
            "detail": (
                f"Die Zeitarchiv-Integration meldet sich mit Version "
                f"{integration_info['version']}, unterstützt wird ab "
                f"{ha_integration.MIN_SUPPORTED_INTEGRATION_VERSION}. Bitte über "
                "HACS oder manuell aktualisieren — sonst funktionieren neuere "
                "Funktionen (z. B. Rückmeldungen an Home Assistant) nicht "
                "zuverlässig."
            ),
            "meta": "Verbindung",
            "link": "/settings#verbindung",
        })

    latest_integration_version = ha_integration.latest_known_integration_version(index)
    integration_update_kind = (
        ha_integration.integration_update_kind(integration_info["version"], latest_integration_version)
        if integration_info and latest_integration_version
        else None
    )
    if integration_update_kind:
        notices.append({
            "id": "integration.update_available",
            "severity": "info",
            "title": (
                "Integrations-Bugfix verfügbar"
                if integration_update_kind == "bugfix"
                else "Neue Integrations-Version verfügbar"
            ),
            "detail": (
                f"Version {latest_integration_version} der Home-Assistant-Integration ist verfügbar "
                f"(aktuell verbunden: {integration_info['version']})."
            ),
            "meta": "Verbindung",
            "link": "/settings#verbindung",
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

    # Kein Tick seit über 5 Minuten (Takt ist 30s, das sind ~10 verpasste
    # Durchläufe) — deckt einen Wartungsplaner-Thread ab, der ganz aufgehört
    # hat zu laufen (Endlosschleife, blockierender Aufruf ohne eigenes
    # Timeout), nicht nur einen einzelnen fehlgeschlagenen Durchlauf (der
    # wird schon geloggt und beim nächsten Tick automatisch erneut versucht).
    # Nur erreichbar, solange die App selbst noch antwortet — ein Deadlock im
    # Index-Lock (siehe system.index_lock_contention unten) friert auch diese
    # Abfrage ein; dafür gibt's stattdessen den Docker-Healthcheck.
    scheduler_idle_seconds = time.time() - scheduler_last_tick
    if scheduler_idle_seconds > SCHEDULER_STALLED_SECONDS:
        notices.append({
            "id": "system.scheduler_stalled",
            "severity": "warn",
            "title": "Wartungsplaner reagiert nicht",
            "detail": (
                f"Seit {round(scheduler_idle_seconds / 60)} Minuten kein "
                "abgeschlossener Durchlauf des Wartungsplaners — Sicherungs-/"
                "Aufbewahrungspläne, Statistik-Schnappschüsse und Vorschauen "
                "könnten veraltet sein."
            ),
            "meta": "Diagnose",
            "link": "/settings#diagnose",
        })

    # Analog zu system.scheduler_stalled oben, aber nur relevant, während der
    # Hintergrundabgleich tatsächlich noch laufen sollte (reconcile_in_
    # progress) — im synchronen Modus (Restore/Crash) oder nach normalem
    # Abschluss bleibt der letzte Tick sonst stehen und würde sonst dauerhaft
    # fälschlich als "veraltet" gelten.
    reconcile_idle_seconds = time.time() - reconcile_last_tick
    if reconcile_in_progress and reconcile_idle_seconds > RECONCILE_STALLED_SECONDS:
        notices.append({
            "id": "system.storage_reconcile_stalled",
            "severity": "warn",
            "title": "Speicherindex-Abgleich reagiert nicht",
            "detail": (
                f"Seit {round(reconcile_idle_seconds / 60)} Minuten kein "
                "Fortschritt beim Hintergrundabgleich des Speicherindex — "
                "möglicherweise an einer Entitäts-Sperre hängengeblieben."
            ),
            "meta": "Diagnose",
            "link": "/settings#diagnose",
        })

    # Analog zu system.storage_reconcile_stalled oben, aber für den
    # Backup-Hintergrund-Thread (siehe _last_backup_worker_tick in main.py) —
    # dessen Hang bliebe anders als beim Wartungsplaner-Loop selbst vom
    # Scheduler-Heartbeat unbemerkt, weil der Backup-Worker immer in einem
    # eigenen, losgelösten Thread läuft. Nur relevant, während ein Backup
    # laut _backup_progress.running tatsächlich noch laufen sollte.
    if backup_worker_in_progress and backup_worker_last_tick is not None:
        backup_worker_idle_seconds = time.time() - backup_worker_last_tick
        if backup_worker_idle_seconds > BACKUP_WORKER_STALLED_SECONDS:
            notices.append({
                "id": "system.backup_worker_stalled",
                "severity": "warn",
                "title": "Backup reagiert nicht",
                "detail": (
                    f"Seit {round(backup_worker_idle_seconds / 60)} Minuten kein "
                    "Fortschritt beim laufenden Backup — möglicherweise an einer "
                    "Entitäts-Sperre hängengeblieben."
                ),
                "meta": "Diagnose",
                "link": "/settings#diagnose",
            })

    # IndexBusy-Vorkommen (siehe _TimeoutLock in index.py) — heilt sich
    # selbst, bliebe sonst aber unbemerkt. Bewusst nur info: ein einzelnes
    # Vorkommen kann ein legitimes, länger laufendes VACUUM sein; erst
    # gehäuftes Auftreten wäre ein Hinweis auf ein echtes Problem.
    busy_events = index.recent_lock_busy_events()
    if busy_events:
        notices.append({
            "id": "system.index_lock_contention",
            "severity": "info",
            "title": "Kurzzeitige Datenbank-Überlastung erkannt",
            "detail": (
                f"{busy_events}× in den letzten 24h musste eine Datenbank-"
                "Operation abgebrochen werden, weil sie nicht rechtzeitig an "
                "die Reihe kam — trat z. B. bei einem laufenden VACUUM "
                "auf und hat sich von selbst gelöst."
            ),
            "meta": "Diagnose",
            "link": "/settings#diagnose",
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

    # main.py verhindert das für NEUE Änderungen an resolution/value_filter
    # automatisch (storage.index.should_raise_gap_threshold); diese Meldung
    # deckt bereits bestehende Entitäten mit der riskanten Kombination ab,
    # die die Automatik nicht rückwirkend anfasst — Details siehe
    # gap_threshold_conflicts() oben (auch Grundlage für Housekeeping →
    # Konfiguration).
    conflict_count = len(gap_threshold_conflicts(index))
    if conflict_count:
        notices.append({
            "id": "entities.gap_threshold_conflict",
            "severity": "warn",
            "title": "Lücken-Erkennung kann strukturell nicht zutreffen",
            "detail": (
                f"{conflict_count} Entität{'en haben' if conflict_count != 1 else ' hat'} eine "
                "Lücken-Erkennung, die enger eingestellt ist als der Mindestabstand, den die "
                "gewählte Auflösung oder der aktive Wertänderungsfilter selbst zwischen "
                "gespeicherten Werten erzwingt — das führt zu falschen Lücken-Meldungen."
            ),
            "meta": "Housekeeping",
            "link": "/housekeeping#konfiguration",
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

    # Downgrade-Schutz (siehe ROADMAP.md "Neu seit 0.76.1", Punkt 3 /
    # CONFIG_SCHEMA_VERSION in energiedashboard_routes.py): _load_config()
    # behandelt eine Config mit unbekannt hoher schema_version bereits
    # sicher als leer statt zu raten — diese Meldung erklärt nur, WARUM die
    # Energiedashboard-Einstellungen gerade leer wirken, statt das wie
    # stillen Datenverlust aussehen zu lassen.
    try:
        stored_energiedashboard_config = json.loads(index.get_setting(SETTING_CONFIG, "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        stored_energiedashboard_config = {}
    stored_config_schema_version = (
        stored_energiedashboard_config.get("schema_version", 0)
        if isinstance(stored_energiedashboard_config, dict) else 0
    )
    if isinstance(stored_config_schema_version, int) and stored_config_schema_version > CONFIG_SCHEMA_VERSION:
        notices.append({
            "id": "energiedashboard.config_from_newer_version",
            "severity": "warn",
            "title": "Energiedashboard-Konfiguration von neuerer Version",
            "detail": (
                "Die gespeicherte Energiedashboard-Konfiguration wurde von einer neueren "
                "Zeitarchiv-Version geschrieben und wird von dieser Version sicherheitshalber "
                "als leer behandelt, statt sie falsch zu interpretieren. Ein Update auf die "
                "neuere Version stellt die eigentlichen Einstellungen wieder her."
            ),
            "meta": "Energiedashboard",
            "link": "/energiedashboard",
        })

    # Andere Frage als "wie viel Platz braucht Zeitarchiv intern" (siehe die
    # übrigen housekeeping.*-Meldungen) — prüft stattdessen, ob die
    # zugrunde liegende Host-Partition selbst knapp wird. shutil.disk_usage()
    # auf DATA_DIR statt der Supervisor-API (GET /host/info): Letztere würde
    # eine hassio_role über dem aktuellen Minimalstand erfordern (siehe
    # ROADMAP.md 2.2), für eine reine Speicherplatz-Anzeige nicht
    # gerechtfertigt. DATA_DIR ist in der Praxis (HAOS/Supervised, eine
    # Platte) dieselbe Partition wie der Rest des Hosts.
    if host_disk_usage and host_disk_usage.get("total"):
        free_bytes = host_disk_usage["free"]
        total_bytes = host_disk_usage["total"]
        free_ratio = free_bytes / total_bytes
        if free_ratio < HOST_DISK_WARN_RATIO:
            notices.append({
                "id": "housekeeping.host_disk_space_low",
                "severity": "error" if free_ratio < HOST_DISK_ERROR_RATIO else "warn",
                "title": "Host-Speicherplatz wird knapp",
                "detail": (
                    f"Noch {format_size(free_bytes)} frei ({round(free_ratio * 100)} % der Partition) "
                    "auf dem Dateisystem, auf dem Zeitarchiv seine Daten ablegt."
                ),
                "meta": "Speicherplatz",
                "link": "/housekeeping#speicherplatz",
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
            "meta": "Housekeeping",
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
    scheduler_last_tick: float,
    reconcile_last_tick: float,
    reconcile_in_progress: bool,
    host_disk_usage: dict | None = None,
    backup_worker_last_tick: float | None = None,
    backup_worker_in_progress: bool = False,
) -> list[dict]:
    """Für die Anzeige in der Topnav — build_notices() abzüglich aktuell
    gültiger Stummschaltungen (beim Tipp bereits durch _current_tip_notice
    vorgefiltert, dieser Schritt betrifft ihn also nie doppelt)."""
    mutes = _load_mutes(index)
    now = time.time()
    return [
        notice for notice in build_notices(
            index, index_path, tz, purge_totals, storage_reconcile, stale_entity_count,
            scheduler_last_tick, reconcile_last_tick, reconcile_in_progress, host_disk_usage,
            backup_worker_last_tick, backup_worker_in_progress,
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
