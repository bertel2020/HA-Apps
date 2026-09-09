"""Housekeeping-Bereich: Aufbewahrung, Rotation, Speicherplatz, Duplikate, Ausreißer.

Aus main.py ausgelagert (analog api_routes.py/report_routes.py/
import_routes.py). Der Anlass steht in test_route_modules.py: main.py hat ein
Zeilenbudget, und der Housekeeping-Bereich war mit rund 500 Zeilen der
größte zusammenhängende Brocken darin, der für sich steht.

Die Routen liegen teils unter /settings/… statt /housekeeping/… — die URLs
sind gewachsen, bevor die Housekeeping-Seite sie zusammenfasste, und ein
Umbenennen würde Lesezeichen und die Formular-Ziele in mehreren Templates
brechen. Maßgeblich ist, welche Seite sie bedienen, nicht ihr Pfad.

Was BEWUSST NICHT hier liegt: alles, was sich der Bereich mit dem
Wartungsplaner teilt — die beiden Vorschau-Zwischenspeicher
(load_purge_preview, refresh_*_if_stale), die Job-Klammer
(begin_/finish_retention_job), set_next_retention_run und
run_storage_reconciliation. Sie gehören seit 0.85.0 dem BackgroundService
(background.py) und werden von main.py als Callables in
HousekeepingDependencies hereingereicht — die Routen hier rufen sie auf, ohne
zu wissen, wer sie im Hintergrund pflegt.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import cleanup_stats
from . import demo_mode
from . import notices as notices_mod
from .backup_scheduler import parse_schedule_time
from .formatting import (
    BACKUP_SCHEDULE_LABELS,
    DECIMALS_LABELS,
    DEMO_APPEND_INTERVAL_LABELS,
    GAP_THRESHOLD_LABELS,
    OUTLIER_THRESHOLD_LABELS,
    RESOLUTION_LABELS,
    RETENTION_LABELS,
    VALUE_FILTER_LABELS,
    entity_display_name,
    format_int,
    format_retention,
    format_size,
    format_time,
    format_timestamp,
    format_uptime,
    format_value,
)
from .progress import JobBusy, JobProgress
from .storage import cleanup, rotate
from .storage.coordinator import StorageCoordinator
from .storage.index import (
    DEFAULT_GAP_THRESHOLD,
    DEFAULT_RESOLUTION,
    DEFAULT_VALUE_FILTER,
    Index,
    should_raise_gap_threshold,
)


logger = logging.getLogger(__name__)

#: Fortschritt der manuellen Bereinigung. Modulweit statt im Router-Bau, damit
#: der Zustand nicht an der Router-Instanz hängt: /settings/purge/progress muss
#: denselben Auftrag sehen wie /settings/purge, auch wenn die Anzeige nach einem
#: Seitenwechsel neu aufgebaut wird. Es gibt genau einen Bereinigungslauf
#: gleichzeitig — der Auftrag hält die globale Wartungssperre, ein zweiter
#: könnte ohnehin nur warten.
_purge_progress = JobProgress("purge", unit="Monate", label="Bereinigung")

#: Fortschritt der manuellen Rotation. Sie bleibt bewusst synchron — wer sie
#: auslöst, wartet auf die Antwort und braucht keine eigene Anzeige. In der
#: Kopfleiste steht sie trotzdem, weil sie unter der globalen Wartungssperre
#: läuft: Für jeden ANDEREN Tab sieht das sonst nach einem grundlos hängenden
#: Server aus. Siehe JobProgress.track().
_rotation_progress = JobProgress("rotation", unit="Entitäten", label="Rotation")


def _rotation_step(nummer: int, gesamt: int, entity_id: str) -> None:
    """Callback für rotate_all_stale(). Die Gesamtzahl kommt erst aus der
    Funktion selbst — sie zählt die Entitäten, nicht der Aufrufer."""
    _rotation_progress.set_total(gesamt)
    _rotation_progress.advance(done=nummer, detail=entity_id)


@dataclass(frozen=True)
class HousekeepingDependencies:
    """Laufzeitabhängigkeiten des Bereichs — Daten oben, geteilte Funktionen
    darunter. Die Callables sind kein Selbstzweck: jede davon wird auch
    außerhalb dieses Moduls gebraucht (Scheduler, Einstellungsseite), sonst
    wäre sie mit umgezogen."""

    data_dir: Path
    tz: ZoneInfo
    index: Index
    coordinator: StorageCoordinator
    # Demo-Modus (DEMO_MODUS_PLAN.md): base_dir ist IMMER die rohe
    # ZEITARCHIV_DATA_DIR, unabhängig vom aktuellen Modus — anders als
    # data_dir oben, das im Demo-Modus auf base_dir/demo zeigt. Housekeeping
    # braucht beide: data_dir für Zustand "aktiv" (läuft der Index gerade
    # gegen die Demo-Instanz), base_dir für Zustand "ungenutzt" (wo LÄGE
    # demo/, auch wenn gerade niemand hingegen läuft).
    base_dir: Path
    demo_mode_active: bool
    templates: Jinja2Templates
    retention_default_time: str
    retention_default_weekday: int
    chart_range_options: list
    gap_threshold_minute_tiers: list
    backup_weekday_options: list
    # Objekt, kein Wert: es wird in-place verändert, die Referenz bleibt.
    retention_progress: object
    storage_locked: Callable[..., Callable]
    settings_archivierung_context: Callable[..., dict]
    refresh_purge_preview_if_stale: Callable[..., object]
    refresh_retention_overview_if_stale: Callable[..., object]
    begin_retention_job: Callable[..., object]
    finish_retention_job: Callable[..., object]
    run_storage_reconciliation: Callable[..., dict]
    gap_threshold_auto_adjust_message: Callable[..., str]
    set_next_retention_run: Callable[..., float | None]
    chart_type_label: Callable[..., str]
    count_stale_entities: Callable[..., int]
    load_purge_preview: Callable[..., dict]
    load_retention_overview: Callable[..., dict]
    # Getter, KEINE Werte: beide werden in main.py per global neu gebunden
    # (Scheduler-Caches). Als Feldwert übergeben wäre hier für immer das
    # None vom Programmstart eingefroren.
    host_disk_usage_cached: Callable[[], dict | None]
    storage_reconcile_last: Callable[[], dict | None]


def create_housekeeping_router(deps: HousekeepingDependencies) -> APIRouter:
    router = APIRouter()

    def _duplicate_rows_for_display() -> tuple[list[dict], list[dict], str]:
        """Liest den gecachten globalen Duplikat-Schnappschuss (siehe
        _refresh_duplicate_snapshot_if_stale, stündlich, 30-Tage-Fenster über alle
        Entitäten) und bereitet ihn für die Anzeige im Housekeeping-Bereich auf —
        eigene Funktion statt Inline-Code in housekeeping_view(), damit die
        Aufbereitung unabhängig von der Route testbar/lesbar bleibt."""
        duplicate_rows = (deps.index.get_duplicate_snapshot() or {}).get("rows", [])
        duplicates_by_entity = [
            {
                "entity_id": row["entity_id"],
                "friendly_name": row["friendly_name"],
                "count": format_int(row['count']),
                "count_raw": row["count"],
            }
            for row in duplicate_rows
        ]
        duplicates_total = format_int(sum(row['count'] for row in duplicate_rows))
        return duplicate_rows, duplicates_by_entity, duplicates_total

    def _host_disk_usage_context() -> dict:
        """Für die immer sichtbare Host-Speicherplatz-Zeile in housekeeping.html —
        andere Frage als Zeitarchivs eigene interne Aufschlüsselung (Speicherindex,
        Bereinigung), siehe notices.py housekeeping.host_disk_space_low."""
        usage = deps.host_disk_usage_cached()
        if not usage or not usage.get("total"):
            return {"host_disk_usage": None}
        free_ratio = usage["free"] / usage["total"]
        # Dieselben Schwellwerte wie housekeeping.host_disk_space_low (notices.py)
        # — der Balken wechselt die Farbe genau dann, wenn auch die Notice
        # anspringen würde, statt eine unabhängige zweite Meinung zu sein.
        if free_ratio < notices_mod.HOST_DISK_ERROR_RATIO:
            severity = "danger"
        elif free_ratio < notices_mod.HOST_DISK_WARN_RATIO:
            severity = "warning"
        else:
            severity = "positive"
        return {
            "host_disk_usage": {
                "free_label": format_size(usage["free"]),
                "total_label": format_size(usage["total"]),
                "free_percent": round(free_ratio * 100),
                "used_percent": round((1 - free_ratio) * 100),
                "severity": severity,
            }
        }

    def _settings_rotation_context(result: str | None = None) -> dict:
        return {"stale_count": deps.count_stale_entities(), "result": result}

    def _settings_storage_index_context(report: dict | None = None) -> dict:
        report = report if report is not None else deps.storage_reconcile_last()
        if report is None:
            return {"storage_audit": None}
        rows = []
        for row in report["mismatches"]:
            rows.append({
                **row,
                "indexed_visible_rows_label": format_int(row['indexed_visible_rows']),
                "actual_visible_rows_label": format_int(row['actual_visible_rows']),
                "difference_label": format_int(row['actual_visible_rows'] - row['indexed_visible_rows'], signed=True),
                "indexed_size_label": format_size(row["indexed_size_bytes"]),
                "actual_size_label": format_size(row["actual_size_bytes"]),
            })
        checked_at = report.get("checked_at")
        return {
            "storage_audit": {
                **report,
                "rows": rows,
                "checked_at_label": (
                    f"{format_timestamp(checked_at, deps.tz)} {format_time(checked_at, deps.tz)}"
                    if checked_at else "—"
                ),
            }
        }

    def _settings_purge_context(result: str | None = None) -> dict:
        """Liefert die stets sichtbare, rein lesende Bereinigungsvorschau — aus
        dem Zwischenspeicher (siehe deps.refresh_purge_preview_if_stale()), NICHT bei
        jedem Aufruf neu berechnet. Die Aktualisierung übernimmt der
        Wartungsplaner (_maintenance_scheduler_loop()) im Hintergrund; nach einem
        tatsächlichen Purge-Klick erzwingt settings_purge() zusätzlich eine
        sofortige Aktualisierung, damit das Ergebnis nicht die alten Zahlen zeigt."""
        return {"result": result, "purge_preview": deps.load_purge_preview()}

    def _settings_retention_context(result: str | None = None) -> dict:
        limited_count = sum(1 for entity in deps.index.list_entities() if entity["retention"] != "unlimited")
        schedule = deps.index.get_setting("retention_enforcement", "off")
        if schedule not in BACKUP_SCHEDULE_LABELS:
            schedule = "off"
        enabled = schedule in ("daily", "weekly")
        next_raw = deps.index.get_setting("retention_enforcement_next_run", "")
        try:
            next_ts = float(next_raw) if next_raw else None
        except ValueError:
            next_ts = None
        if enabled and next_ts is None:
            next_ts = deps.set_next_retention_run(datetime.now(deps.tz))

        retention_overview = deps.load_retention_overview()
        retention_totals = retention_overview.get("totals", {})
        retention_history_30d = deps.index.get_retention_job_totals(time.time() - 30 * 86400)
        retention_history_all = deps.index.get_retention_job_totals(0.0)
        retention_groups = {
            row["retention"]: row for row in retention_overview.get("groups", [])
            if isinstance(row, dict) and row.get("retention")
        }
        by_retention = []
        for row in deps.index.get_stats_by_retention():
            due = retention_groups.get(row["retention"], {})
            rows_due = int(due.get("rows_due", 0) or 0)
            months_due = int(due.get("months_due", 0) or 0)
            next_expiration_ts = due.get("next_expiration_ts")
            if rows_due or months_due:
                next_expiration = "Jetzt fällig"
            elif isinstance(next_expiration_ts, (int, float)):
                next_expiration = (
                    f"{format_timestamp(next_expiration_ts, deps.tz)} "
                    f"{format_time(next_expiration_ts, deps.tz)}"
                )
            else:
                next_expiration = "—"
            by_retention.append({
                "label": format_retention(row["retention"]),
                "entity_count": format_int(row["entity_count"]),
                "total_rows": format_int(row['total_rows']),
                "total_size": format_size(row["total_size_bytes"]),
                "rows_due": format_int(rows_due),
                "months_due": months_due,
                "entities_due": int(due.get("entities_due", 0) or 0),
                "bytes_due": format_size(int(due.get("bytes_due", 0) or 0)),
                "next_expiration": next_expiration,
            })

        def display_ts(raw: str | None) -> str:
            try:
                ts = float(raw) if raw else None
            except ValueError:
                ts = None
            return f"{format_timestamp(ts, deps.tz)} {format_time(ts, deps.tz)}" if ts else "—"

        status_labels = {
            "queued": "Geplant", "running": "Läuft", "success": "Erfolgreich",
            "failed": "Fehlgeschlagen", "interrupted": "Abgebrochen", "skipped": "Übersprungen",
        }
        jobs = []
        for job in deps.index.list_retention_jobs(8):
            jobs.append({
                "created_at": f"{format_timestamp(job['created_at'], deps.tz)} {format_time(job['created_at'], deps.tz)}",
                "created_at_ts": job["created_at"],
                "trigger": "Zeitplan" if job["trigger"] == "scheduled" else "Manuell",
                "status": status_labels.get(job["status"], job["status"]),
                "status_key": job["status"],
                "rows_deleted": format_int(job["rows_deleted"]) if job["rows_deleted"] is not None else "—",
                "months_deleted": job["months_deleted"] if job["months_deleted"] is not None else "—",
                "entities_affected": format_int(job["entities_affected"]) if job["entities_affected"] is not None else "—",
                "bytes_freed": format_size(job["bytes_freed"] or 0) if job["bytes_freed"] else "—",
                "error": job["error"],
            })
        with deps.retention_progress.lock:
            running = deps.retention_progress.running
        last_success_raw = deps.index.get_setting("retention_last_success", "")
        return {
            "retention_enforcement_enabled": enabled,
            "retention_enforcement_schedule": schedule,
            "retention_enforcement_options": list(BACKUP_SCHEDULE_LABELS.items()),
            "retention_enforcement_time": deps.index.get_setting("retention_enforcement_time", deps.retention_default_time),
            "retention_enforcement_weekday": int(
                deps.index.get_setting("retention_enforcement_weekday", str(deps.retention_default_weekday))
            ),
            "retention_weekday_options": deps.backup_weekday_options,
            "retention_timezone": str(deps.tz),
            "retention_next_run": display_ts(str(next_ts) if next_ts is not None else None),
            "retention_last_success": display_ts(last_success_raw),
            "retention_last_failure": display_ts(deps.index.get_setting("retention_last_failure", "")),
            "last_run": display_ts(last_success_raw) if last_success_raw else None,
            "retention_jobs": jobs,
            "retention_running": running,
            "limited_retention_count": format_int(limited_count),
            "retention_due_rows": format_int(int(retention_totals.get('rows_deleted', 0) or 0)),
            "retention_due_entities": int(retention_totals.get("entities_affected", 0) or 0),
            "retention_due_months": int(retention_totals.get("months_deleted", 0) or 0),
            "retention_due_size": format_size(int(retention_totals.get("bytes_freed", 0) or 0)),
            "retention_history_30d_rows": format_int(retention_history_30d['rows_deleted']),
            "retention_history_30d_size": format_size(retention_history_30d["bytes_freed"]),
            "retention_history_all_rows": format_int(retention_history_all['rows_deleted']),
            "retention_history_all_size": format_size(retention_history_all["bytes_freed"]),
            "by_retention": by_retention,
            "retention_preview_generated_at": (
                f"{format_timestamp(retention_overview['generated_at'], deps.tz)} "
                f"{format_time(retention_overview['generated_at'], deps.tz)}"
                if isinstance(retention_overview.get("generated_at"), (int, float))
                else "Wird berechnet …"
            ),
            "result": result,
        }

    _STALE_ENTITIES_DAY_OPTIONS = [("1", "1 Tag"), ("3", "3 Tage"), ("7", "7 Tage"), ("14", "14 Tage"), ("30", "30 Tage")]
    _STALE_ENTITIES_DEFAULT_DAYS = "3"


    def _stale_entities_context(days: str = _STALE_ENTITIES_DEFAULT_DAYS) -> dict:
        """Entitäten, deren letzter Wert (entities.last_ts, ohnehin vorhanden —
        kein neuer Hintergrundjob nötig) länger als der gewählte Schwellwert
        zurückliegt. Meist harmlos (Gerät im Standby, seltener Sensor), aber ein
        früher Hinweis auf eine tote Integration oder eine umbenannte/entfernte
        HA-Entität. Nie empfangene Entitäten (last_ts NULL) erscheinen unabhängig
        vom gewählten Schwellwert immer — für sie gibt es kein sinnvolles "seit
        wann", das sich unter- oder überschreiten ließe."""
        if days not in dict(_STALE_ENTITIES_DAY_OPTIONS):
            days = _STALE_ENTITIES_DEFAULT_DAYS
        threshold_seconds = int(days) * 86400
        now_ts = time.time()
        rows = []
        for entity in deps.index.list_entities():
            last_ts = entity["last_ts"]
            if last_ts is None:
                days_ago = None
            else:
                age_seconds = now_ts - last_ts
                if age_seconds < threshold_seconds:
                    continue
                days_ago = age_seconds / 86400
            has_name = bool(entity["custom_name"] or entity["friendly_name"])
            rows.append({
                "entity_id": entity["entity_id"],
                "display_name": entity_display_name(entity["entity_id"], entity["friendly_name"], entity["custom_name"]),
                "has_name": has_name,
                "last_value_label": (
                    datetime.fromtimestamp(last_ts, deps.tz).strftime("%d.%m.%Y, %H:%M") if last_ts is not None else "Nie empfangen"
                ),
                # 10**6 Tage statt float('inf') — sortiert serverseitig genauso
                # zuverlässig an die Spitze, ist aber über data-sort auch für
                # sortable-table.js' parseFloat() im Client ein gültiger Wert
                # ("inf" wird dort zu NaN).
                "days_ago_raw": days_ago if days_ago is not None else 10**6,
                "days_ago_label": f"{format_value(days_ago, 1)} Tage" if days_ago is not None else "—",
                "row_count": format_int(entity["row_count"]),
                "row_count_raw": entity["row_count"],
            })
        rows.sort(key=lambda r: r["days_ago_raw"], reverse=True)
        return {
            "stale_entities": rows,
            "stale_entities_days": days,
            "stale_entities_day_options": _STALE_ENTITIES_DAY_OPTIONS,
        }


    def _demo_progress_context() -> dict:
        """Flach, NUR für _job_progress.html — wie _purge_progress_context().
        Bewusst getrennt von _demo_data_context() unten: würden done/total/
        error/result usw. direkt in dessen (mit den übrigen Housekeeping-
        Abschnitten geteilten) Kontext gemischt, kollidierten sie mit
        gleichnamigen Schlüsseln aus _settings_purge_context() &Co., sobald
        beide gleichzeitig in housekeeping_view() zusammengeführt werden."""
        return {
            **demo_mode.demo_progress.snapshot(),
            "progress_id": "demo-progress",
            "poll_url": "housekeeping/demo-data/progress",
        }

    def _demo_data_context() -> dict:
        """Housekeeping → Demo-Daten (DEMO_MODUS_PLAN.md) — drei mögliche
        Zustände, siehe demo_mode.current_demo_state(). "aktiv" und
        "ungenutzt" lesen ihre Zahlen aus komplett unterschiedlichen Quellen:
        der laufende Index (aktiv) bzw. ein reiner Dateisystem-Blick
        (ungenutzt) — dort wird NIE Index() gegen ein Verzeichnis geöffnet,
        das die laufende Instanz gerade nicht selbst verwendet (siehe
        demo_mode.py-Moduldoc). demo_progress bleibt absichtlich NAMESPACED
        (nicht wie _demo_progress_context() oben flach) — dieser Kontext hier
        landet gemeinsam mit allen anderen Housekeeping-Abschnitten in EINEM
        Dict (siehe housekeeping_view()), flache done/total/error/result-
        Schlüssel wären dort ein Kollisionsrisiko."""
        dir_info = None if deps.demo_mode_active else demo_mode.demo_dir_info(deps.base_dir)
        state = demo_mode.current_demo_state(deps.demo_mode_active, dir_info)
        if state is None:
            return {"demo_state": None}

        progress = demo_mode.demo_progress.snapshot()
        context: dict = {
            "demo_state": state,
            "demo_generating": progress["running"],
            "demo_progress": _demo_progress_context(),
        }
        now = time.time()
        if state == "active":
            overview = deps.index.get_overview()
            last_run_raw = deps.index.get_setting("demo_append_last_run", "")
            last_run = float(last_run_raw) if last_run_raw else None
            interval = deps.index.get_setting("demo_append_interval", "off")
            if interval not in DEMO_APPEND_INTERVAL_LABELS:
                interval = "off"
            interval_seconds = demo_mode.DEMO_APPEND_INTERVAL_SECONDS.get(interval)
            next_run_label = (
                f"in {format_uptime((last_run if last_run is not None else now) + interval_seconds - now)}"
                if interval_seconds is not None else "—"
            )
            context.update({
                "demo_entity_count_label": format_int(overview["entity_count"]),
                "demo_row_count_label": format_int(overview["total_rows"]),
                "demo_size_label": format_size(overview["total_size_bytes"]),
                "demo_last_run_label": f"vor {format_uptime(now - last_run)}" if last_run else "Noch nie",
                "demo_next_run_label": next_run_label,
                "demo_append_interval": interval,
                "demo_append_interval_options": list(DEMO_APPEND_INTERVAL_LABELS.items()),
            })
        else:  # "orphan"
            context.update({
                "demo_entity_count_label": format_int(dir_info["entity_count_approx"]),
                "demo_size_label": format_size(dir_info["size_bytes"]),
                "demo_newest_label": f"vor {format_uptime(now - dir_info['newest_mtime'])}",
            })
        return context


    @router.get("/housekeeping/stale-entities", response_class=HTMLResponse)
    def housekeeping_stale_entities(request: Request, days: str = _STALE_ENTITIES_DEFAULT_DAYS) -> HTMLResponse:
        """Von refreshStaleEntities() bzw. dem hx-trigger="change" auf
        #stale-entities-form (housekeeping.html) abgerufen, wenn der Schwellwert
        im Dropdown geändert wird — rendert nur die Tabelle neu, nicht die ganze
        Seite."""
        return deps.templates.TemplateResponse(request, "_stale_entities_body.html", _stale_entities_context(days))


    @router.get("/housekeeping", response_class=HTMLResponse)
    @deps.storage_locked(lambda _args: [row["entity_id"] for row in deps.index.list_entities()])
    def housekeeping_view(request: Request) -> HTMLResponse:
        """Sammelt Dinge, die niemandem auffallen, solange man nicht gezielt danach
        sucht: ungenutzte Charts/Tabellen (kein Dashboard-Pin), Entitäten mit
        erkannten Duplikaten (bestehender globaler Schnappschuss, siehe
        _duplicate_rows_for_display), Entitäten ohne neue Werte, Entitäten mit
        auffällig hoher Ausreißer-Quote und mit unwirksamer Lücken-Erkennung. Wiederholungen sind bewusst noch nicht
        enthalten (siehe Diskussion zu Schwellwert/Kalibrierung)."""
        aggregation_types = {
            row["entity_id"]: row["aggregation_type"] for row in deps.index.list_entities()
        }
        unused_charts = [
            {
                "id": c["id"],
                "name": c["name"],
                "entity_count": len(c["entity_ids"]),
                "range_label": dict(deps.chart_range_options).get(c["range_key"], c["range_key"]),
                "type_label": deps.chart_type_label(c, aggregation_types),
            }
            for c in deps.index.list_unused_saved_charts()
        ]
        unused_tables = [
            {"id": t["id"], "name": t["name"], "row_count": t["row_count"], "column_count": t["column_count"]}
            for t in deps.index.list_unused_saved_tables()
        ]
        _, duplicates_by_entity, duplicates_total = _duplicate_rows_for_display()
        return deps.templates.TemplateResponse(
            request,
            "housekeeping.html",
            {
                "unused_charts": unused_charts,
                "unused_tables": unused_tables,
                "chart_count": deps.index.count_saved_charts(),
                "table_count": deps.index.count_saved_tables(),
                "duplicates_by_entity": duplicates_by_entity,
                "duplicates_total": duplicates_total,
                "gap_threshold_conflicts": notices_mod.gap_threshold_conflicts(deps.index),
                "outlier_rates": cleanup_stats.outlier_rate_overview(deps.index),
                "outlier_notable_percent": format_value(
                    cleanup_stats.OUTLIER_RATE_NOTABLE_PERCENT, 0
                ),
                **_stale_entities_context(),
                **_host_disk_usage_context(),
                **_settings_storage_index_context(),
                **_settings_purge_context(),
                **_settings_retention_context(),
                **_settings_rotation_context(),
                **_demo_data_context(),
            },
        )

    @router.post("/settings/archivierung", response_class=HTMLResponse)
    async def settings_archivierung(request: Request) -> HTMLResponse:
        """Speichert die globalen Standardwerte für neu erkannte Entitäten
        (Einstellungen-Bereich, Konzept Abschnitt 03) — wirkt nur auf Entitäten,
        die AB JETZT zum ersten Mal einen Wert senden; bereits archivierte
        Entitäten behalten ihre individuelle Einstellung aus der jeweiligen
        Konfigurationsseite unverändert (Index.get_or_create_entity() greift nur
        beim Neuanlegen auf diese Standardwerte zu)."""
        form = await request.form()
        fields = {
            "default_resolution": (form.get("default_resolution"), RESOLUTION_LABELS, "Ungültige Auflösung"),
            "default_retention": (form.get("default_retention"), RETENTION_LABELS, "Ungültige Aufbewahrung"),
            "default_decimals": (form.get("default_decimals"), DECIMALS_LABELS, "Ungültige Nachkommastellen"),
            "default_value_filter": (form.get("default_value_filter"), VALUE_FILTER_LABELS, "Ungültiger Wertänderungsfilter"),
            "default_gap_threshold": (form.get("default_gap_threshold"), GAP_THRESHOLD_LABELS, "Ungültige Lücken-Erkennung"),
            "default_outlier_threshold": (form.get("default_outlier_threshold"), OUTLIER_THRESHOLD_LABELS, "Ungültige Ausreißer-Erkennung"),
        }
        for _key, (value, labels, error) in fields.items():
            if value is not None and value not in labels:
                raise HTTPException(status_code=400, detail=error)
        for key, (value, _labels, _error) in fields.items():
            if value is not None:
                deps.index.set_setting(key, str(value))
        # Wie update_entity_config unten — nur bei ÄNDERUNG von default_resolution/default_value_filter auslösen.
        gap_threshold_auto_adjusted = False
        gap_threshold_auto_adjusted_message = None
        default_resolution = fields["default_resolution"][0]
        default_value_filter = fields["default_value_filter"][0]
        if default_value_filter == "decimals" or default_resolution is not None:
            current_resolution = deps.index.get_setting("default_resolution", DEFAULT_RESOLUTION)
            current_value_filter = deps.index.get_setting("default_value_filter", DEFAULT_VALUE_FILTER)
            current_gap = deps.index.get_setting("default_gap_threshold", DEFAULT_GAP_THRESHOLD)
            should_raise, new_gap = should_raise_gap_threshold(
                current_gap, current_resolution, current_value_filter, deps.gap_threshold_minute_tiers
            )
            if should_raise:
                deps.index.set_setting("default_gap_threshold", new_gap)
                gap_threshold_auto_adjusted = True
                reason = "value_filter" if current_value_filter == "decimals" else "resolution"
                gap_threshold_auto_adjusted_message = deps.gap_threshold_auto_adjust_message(
                    reason, new_gap, current_resolution, label="Standard-Lücken-Erkennung")
        context = deps.settings_archivierung_context(saved=True)
        context["gap_threshold_auto_adjusted"] = gap_threshold_auto_adjusted
        context["gap_threshold_auto_adjusted_message"] = gap_threshold_auto_adjusted_message
        return deps.templates.TemplateResponse(request, "_settings_archivierung_form.html", context)


    @router.post("/settings/rotation", response_class=HTMLResponse)
    def settings_rotation(request: Request) -> HTMLResponse:
        """Manueller Rotations-Anstoß (Konzept "Offene Punkte": Rotation läuft sonst
        nur lazy beim nächsten Schreibvorgang einer Entität — eine Entität, die
        komplett aufhört zu senden, würde ihre letzte Hot-Datei sonst nie von
        selbst archivieren)."""
        with _rotation_progress.track():
            _rotation_progress.set_phase("Hot-Dateien werden archiviert")
            with deps.coordinator.exclusive():
                rotated = rotate.rotate_all_stale(
                    deps.data_dir, deps.index, deps.tz, on_entity=_rotation_step
                )
        if rotated == 0:
            result = "Nichts zu tun — alle Entitäten sind bereits aktuell rotiert."
        else:
            result = f"{rotated} Monatsdatei{'en' if rotated != 1 else ''} archiviert."
        logger.info(
            "Manuelle Rotation abgeschlossen · event=manual_rotation_completed files=%d",
            rotated,
        )
        return deps.templates.TemplateResponse(
            request, "_settings_rotation_form.html", _settings_rotation_context(result=result)
        )


    @router.post("/settings/storage-index/check", response_class=HTMLResponse)
    def settings_storage_index_check(request: Request) -> HTMLResponse:
        """Erstellt eine rein lesende Vorschau möglicher Indexabweichungen."""
        with deps.coordinator.exclusive():
            report = deps.run_storage_reconciliation(repair=False)
        return deps.templates.TemplateResponse(
            request, "_settings_storage_index_form.html", _settings_storage_index_context(report)
        )


    @router.post("/settings/storage-index/repair", response_class=HTMLResponse)
    def settings_storage_index_repair(request: Request) -> HTMLResponse:
        """Prüft erneut und ersetzt nur abgeleitete Metadaten atomar."""
        with deps.coordinator.exclusive():
            report = deps.run_storage_reconciliation(repair=True)
        return deps.templates.TemplateResponse(
            request, "_settings_storage_index_form.html", _settings_storage_index_context(report)
        )


    def _purge_progress_context() -> dict:
        return {
            **_purge_progress.snapshot(),
            "progress_id": "purge-progress",
            "poll_url": "settings/purge/progress",
        }

    def _purge_worker() -> str:
        """Der eigentliche Lauf, im Hintergrund-Thread. Rückgabewert ist der
        Ergebnistext, den die Vorlage nach dem letzten Polling anzeigt."""
        # Die Gesamtzahl kommt aus derselben Vorschau, die die Seite ohnehin
        # zeigt (deps.load_purge_preview()) — der Balken zählt also gegen
        # genau die Zahl, die der Nutzer vor dem Klick gelesen hat. Fehlt sie,
        # bleibt der Balken leer und die Zeile darüber trägt den Stand; das
        # ist ehrlicher als eine geschätzte Gesamtzahl.
        vorschau = deps.load_purge_preview() or {}
        _purge_progress.set_phase(
            "Bereinigung läuft…",
            int(vorschau.get("totals", {}).get("archive_months", 0) or 0),
        )
        with deps.coordinator.exclusive():
            hot_purged = cleanup.purge_hot_buffer(deps.data_dir, deps.index, deps.tz)
            archive_result = cleanup.purge_archived_months(
                deps.data_dir, deps.index, deps.tz,
                on_month=lambda anzahl, kennung: _purge_progress.advance(anzahl, kennung),
            )
        total_rows = hot_purged + archive_result["rows_purged"]
        months = archive_result["months_purged"]
        if total_rows == 0:
            result = "Nichts zu bereinigen — aktuell keine entfernbaren Datensätze gefunden."
        elif months == 0:
            result = f"{total_rows} Zeile{'n' if total_rows != 1 else ''} physisch entfernt."
        else:
            result = (
                f"{total_rows} Zeile{'n' if total_rows != 1 else ''} physisch entfernt, "
                f"davon {months} bereits archivierte{'r' if months == 1 else ''} Monat{'e' if months != 1 else ''} neu berechnet."
            )
        logger.info(
            "Manuelle Bereinigung abgeschlossen · event=manual_cleanup_completed "
            "rows=%d months=%d",
            total_rows,
            months,
        )
        deps.refresh_purge_preview_if_stale(force=True)
        return result

    @router.post("/settings/purge", response_class=HTMLResponse)
    def settings_purge(request: Request) -> HTMLResponse:
        """Manueller Anstoß, der zur Löschung markierte Datensätze überall
        physisch entfernt — sowohl im laufenden Monat (Hot Buffer, purge_hot_buffer())
        als auch in bereits archivierten Monaten (Parquet-Rewrite + Rollup-
        Neuberechnung, purge_archived_months()). Konzept "Offene Punkte".

        Läuft seit 0.85.0 im Hintergrund und antwortet sofort mit der
        Fortschrittsanzeige, statt die Antwort bis zum Ende offen zu halten.
        Gemessen an einem echten Bestand dauert der Lauf rund 20 Sekunden
        (163 Archivmonate neu berechnet) — und hält dabei die globale
        Wartungssperre, hält also auch die Aufnahme aus Home Assistant an.
        Genau deshalb muss er sichtbar sein statt stumm.

        Ein zweiter Klick startet keinen zweiten Lauf (JobProgress.claim), er
        bekommt die Anzeige des bereits laufenden zurück."""
        try:
            _purge_progress.start(_purge_worker, logger)
        except JobBusy:
            logger.info("Bereinigung bereits aktiv · event=manual_cleanup_already_running")
        return deps.templates.TemplateResponse(
            request, "_job_progress.html", _purge_progress_context()
        )

    @router.get("/settings/purge/progress", response_class=HTMLResponse)
    def settings_purge_progress(request: Request) -> HTMLResponse:
        """Poll-Ziel der Fortschrittsanzeige: liefert entweder wieder die
        Anzeige (und damit das nächste Polling) oder das fertige Formular
        ohne hx-trigger, was das Polling von selbst beendet."""
        stand = _purge_progress.snapshot()
        if not stand["started"]:
            return HTMLResponse("")
        if stand["running"]:
            return deps.templates.TemplateResponse(
                request, "_job_progress.html", _purge_progress_context()
            )
        if stand["error"]:
            ergebnis = f"Bereinigung fehlgeschlagen: {stand['error']}"
        else:
            ergebnis = stand["result"]
        return deps.templates.TemplateResponse(
            request, "_settings_purge_form.html", _settings_purge_context(result=ergebnis)
        )


    @router.get("/settings/purge/marked", response_class=HTMLResponse)
    def settings_purge_marked(
        request: Request,
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=10, le=200),
    ) -> HTMLResponse:
        """On-demand-Detailansicht der einzelnen Soft-Delete-Markierungen."""
        result = deps.index.list_deleted_points(search=search, page=page, page_size=page_size)
        rows = [
            {
                **row,
                "measured_at": datetime.fromtimestamp(row["ts"], deps.tz).strftime("%d.%m.%Y %H:%M:%S"),
                "marked_at": datetime.fromtimestamp(row["deleted_at"], deps.tz).strftime("%d.%m.%Y %H:%M:%S"),
            }
            for row in result["rows"]
        ]
        return deps.templates.TemplateResponse(
            request,
            "_settings_marked_points.html",
            {"rows": rows, "pagination": result["pagination"]},
        )


    @router.post("/settings/retention-enforcement", response_class=HTMLResponse)
    async def settings_retention_enforcement_toggle(request: Request) -> HTMLResponse:
        """Schaltet die automatische, tägliche Anwendung der Aufbewahrungsfrist
        an/aus (Konzept "Offene Punkte": Aufbewahrung wurde bisher nur
        gespeichert, nie angewendet) — bewusst standardmäßig aus, weil das anders
        als der Purge im Bereinigungs-Werkzeug ganze, nie zuvor markierte
        Zeiträume endgültig löscht."""
        form = await request.form()
        schedule = form.get("retention_enforcement")
        schedule_time = str(form.get("retention_enforcement_time", deps.retention_default_time))
        weekday_raw = str(form.get("retention_enforcement_weekday", deps.retention_default_weekday))
        if schedule not in BACKUP_SCHEDULE_LABELS:
            raise HTTPException(status_code=400, detail="Ungültiger Zeitplan")
        try:
            parse_schedule_time(schedule_time)
            weekday = int(weekday_raw)
            if weekday not in range(7):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Ungültige Uhrzeit") from exc
        deps.index.set_setting("retention_enforcement", schedule)
        deps.index.set_setting("retention_enforcement_time", schedule_time)
        deps.index.set_setting("retention_enforcement_weekday", str(weekday))
        deps.set_next_retention_run(datetime.now(deps.tz))
        return deps.templates.TemplateResponse(
            request, "_settings_retention_form.html", _settings_retention_context()
        )


    def _retention_result_text(totals: dict, *, preview: bool = False) -> str:
        if totals["rows_deleted"] == 0:
            return (
                "Vorschau: Aktuell würden keine Werte gelöscht."
                if preview else
                "Nichts zu tun — keine Werte jenseits der konfigurierten Aufbewahrungsfrist gefunden."
            )
        action = "würden endgültig gelöscht" if preview else "endgültig gelöscht"
        storage_action = "würden frei" if preview else "wurden frei"
        prefix = "Vorschau: " if preview else ""
        return (
            f"{prefix}{totals['rows_deleted']} Zeile{'n' if totals['rows_deleted'] != 1 else ''} in "
            f"{totals['months_deleted']} Monatsdatei{'en' if totals['months_deleted'] != 1 else ''} über "
            f"{totals['entities_affected']} Entität{'en' if totals['entities_affected'] != 1 else ''} {action}; "
            f"etwa {format_size(totals['bytes_freed'])} Archivspeicher {storage_action}."
        )


    @router.post("/settings/retention-enforcement/preview", response_class=HTMLResponse)
    def settings_retention_enforcement_preview(request: Request) -> HTMLResponse:
        overview = deps.refresh_retention_overview_if_stale(force=True)
        totals = overview["totals"]
        return deps.templates.TemplateResponse(
            request,
            "_settings_retention_form.html",
            _settings_retention_context(result=_retention_result_text(totals, preview=True)),
        )


    @router.post("/settings/retention-enforcement/run", response_class=HTMLResponse)
    def settings_retention_enforcement_run(request: Request) -> HTMLResponse:
        """Manueller Anstoß, unabhängig vom Automatik-Schalter — läuft sofort,
        unabhängig davon ob/wann der tägliche Automatik-Lauf zuletzt lief."""
        job_id = deps.begin_retention_job("manual")
        if job_id is None:
            result = "Retention läuft bereits — es wurde kein zweiter Lauf gestartet."
        else:
            outcome = deps.finish_retention_job(job_id)
            if outcome["status"] == "success":
                result = _retention_result_text(outcome["totals"])
            else:
                result = f"Retention fehlgeschlagen: {outcome['error']}"
        return deps.templates.TemplateResponse(
            request, "_settings_retention_form.html", _settings_retention_context(result=result)
        )

    @router.post("/housekeeping/demo-data/append-now", response_class=HTMLResponse)
    def housekeeping_demo_data_append_now(request: Request) -> HTMLResponse:
        """"Jetzt ergänzen" (DEMO_MODUS_PLAN.md Abschnitt 6) — nur im
        Demo-Modus erreichbar, der Button rendert sonst gar nicht erst
        (_housekeeping_demo_data_body.html). Läuft wie die Bereinigung im
        Hintergrund; ein zweiter Klick bekommt die Anzeige des schon
        laufenden Auftrags zurück (JobProgress.claim)."""
        if not deps.demo_mode_active:
            raise HTTPException(status_code=409, detail="Nur im Demo-Modus verfügbar")
        try:
            demo_mode.demo_progress.start(demo_mode.build_demo_worker(deps.data_dir, deps.index, deps.tz, deps.coordinator, "append"), logger)
        except JobBusy:
            logger.info("Demo-Daten-Generierung bereits aktiv · event=demo_generate_already_running")
        return deps.templates.TemplateResponse(request, "_job_progress.html", _demo_progress_context())

    @router.post("/housekeeping/demo-data/regenerate", response_class=HTMLResponse)
    def housekeeping_demo_data_regenerate(request: Request) -> HTMLResponse:
        """"Neu erzeugen" — leert vorhandene Demo-Werte und würfelt die
        komplette Historie neu (Rückfrage per hx-confirm im Template, wie
        beim manuellen Löschen im Bereinigen-Tab)."""
        if not deps.demo_mode_active:
            raise HTTPException(status_code=409, detail="Nur im Demo-Modus verfügbar")
        try:
            demo_mode.demo_progress.start(demo_mode.build_demo_worker(deps.data_dir, deps.index, deps.tz, deps.coordinator, "regenerate"), logger)
        except JobBusy:
            logger.info("Demo-Daten-Generierung bereits aktiv · event=demo_generate_already_running")
        return deps.templates.TemplateResponse(request, "_job_progress.html", _demo_progress_context())

    @router.get("/housekeeping/demo-data/progress", response_class=HTMLResponse)
    def housekeeping_demo_data_progress(request: Request) -> HTMLResponse:
        """Poll-Ziel, wie settings_purge_progress(): liefert entweder wieder
        die Anzeige (nächstes Polling) oder den fertigen Abschnitt zurück,
        was das Polling von selbst beendet."""
        stand = demo_mode.demo_progress.snapshot()
        if not stand["started"]:
            return HTMLResponse("")
        if stand["running"]:
            return deps.templates.TemplateResponse(request, "_job_progress.html", _demo_progress_context())
        return deps.templates.TemplateResponse(
            request, "_housekeeping_demo_data_body.html", _demo_data_context()
        )

    @router.post("/housekeeping/demo-data/interval", response_class=HTMLResponse)
    async def housekeeping_demo_data_interval(request: Request) -> HTMLResponse:
        """Speichert den Zeitplan für die automatische Ergänzung (Scheduler-
        Hook siehe background.py). Nur im Demo-Modus sinnvoll — der Dropdown
        rendert sonst nicht."""
        if not deps.demo_mode_active:
            raise HTTPException(status_code=409, detail="Nur im Demo-Modus verfügbar")
        form = await request.form()
        interval = form.get("demo_append_interval")
        if interval not in DEMO_APPEND_INTERVAL_LABELS:
            raise HTTPException(status_code=400, detail="Ungültiges Intervall")
        deps.index.set_setting("demo_append_interval", str(interval))
        return deps.templates.TemplateResponse(
            request, "_housekeeping_demo_data_body.html", _demo_data_context()
        )

    @router.post("/housekeeping/demo-data/remove", response_class=HTMLResponse)
    def housekeeping_demo_data_remove(request: Request) -> HTMLResponse:
        """Entfernt <BASE_DIR>/demo vollständig (DEMO_MODUS_PLAN.md
        Abschnitt 8) — nur außerhalb des Demo-Modus erreichbar: Index()
        dürfte sonst nie gegen ein Verzeichnis geöffnet werden, das die
        laufende Instanz selbst gerade verwendet (siehe demo_mode.py-
        Moduldoc). Der Button rendert im Demo-Modus gar nicht erst, diese
        Prüfung ist reine Verteidigung gegen einen veralteten Tab.

        hx-swap="delete" auf dem Button entfernt #demo-daten unabhängig vom
        Response-Body — die Antwort trägt trotzdem etwas: ein Out-of-Band-
        Swap für den Nav-Eintrag (liegt außerhalb von #demo-daten) und ein
        <script>, das refreshNoticePanel() (_topnav.html) aufruft, damit die
        Glocke sofort verschwindet statt erst beim nächsten Seitenaufruf."""
        if deps.demo_mode_active:
            raise HTTPException(status_code=409, detail="Nicht möglich, während die Instanz im Demo-Modus läuft")
        demo_mode.remove_demo_dir(deps.base_dir)
        return HTMLResponse(
            '<div id="demo-nav-entry" hx-swap-oob="delete"></div>'
            "<script>refreshNoticePanel();</script>"
        )

    return router
