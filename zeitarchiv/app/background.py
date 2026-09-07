"""Hintergrundarbeit: Wartungsplaner, Backup, Aufbewahrung, Speicherabgleich.

Alles, was ohne Request läuft — und deshalb bis 0.85.0 nur deshalb in main.py
stand, weil es dort beim Start eingehängt wurde.

Der Anlass für den Umzug steht in test_route_modules.py: main.py hat ein
Zeilenbudget, und dessen Kommentar benennt diese Gruppe seit dem 7. September
2026 ausdrücklich als den nächsten Schnitt — sie ist klar abgegrenzt, hängt an
keinem Request und ist von den Routen nur über eine Handvoll Aufrufe erreichbar.
Sie umfasste zuletzt 570 Zeilen, also 10 % der Datei.

Der zweite, konkrete Anlass: Die Kopfleiste zeigt seit 0.85.0 laufende
Vorgänge an (siehe progress.py und _activity_block.html). Fünf Vorgänge fehlen
dort noch — Speicherabgleich, Stunden-Rollup-Backfill, Rotation,
Rollup-Neuaufbau bei Typwechsel — und sie sitzen alle in genau diesem Modul.
Jede Anmeldung hätte main.py weiter wachsen lassen.

Aufbau nach dem Muster der übrigen ausgelagerten Bereiche (api_routes.py,
import_routes.py, housekeeping_routes.py): eine eingefrorene
Abhängigkeiten-Datenklasse und ein Dienst, der sie bekommt. Der Unterschied zu
jenen: Dieser Dienst hält Zustand — Threads, Lebenszeichen, Zwischenspeicher.
Genau der lag vorher als Modul-Globale in main.py und musste dort mit `global`
neu gebunden werden; als Instanzattribut sehen alle Leser über die Instanz
automatisch den aktuellen Wert, ohne dass es dafür Getter braucht.

Der Umzug selbst war rein mechanisch: jeder Modul-Globale X wurde zu self.X,
danach ein zweiter Durchlauf für sprechendere Attributnamen. Beide Schritte
wurden rückwärts gegen das Original geprüft, damit kein Kommentar und keine
Zeile Logik unbemerkt verloren geht.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ha_integration
from . import notices as notices_mod
from . import supervisor_stats
from . import version_check
from .backup_scheduler import next_scheduled_run
from .energiedashboard_routes import (
    process_pending_hourly_backfill,
    refresh_heatmap_weekday_cache_if_stale,
    sync_hourly_rollup_flags_for_current_config,
)
from .limits import MAX_UI_ANALYSIS_ROWS
from .storage import backup, cleanup, reconcile
from .storage import retention as retention_mod
from .storage.coordinator import StorageCoordinator
from .storage.index import Index

logger = logging.getLogger(__name__)

#: Zwischenspeicher-Schlüssel und Höchstalter der beiden teuren Vorschauen.
#: Modulweit statt am Dienst: Es sind Konstanten, keine Zustände.
PURGE_PREVIEW_SETTING = "purge_preview_snapshot"
PURGE_PREVIEW_MAX_AGE_SECONDS = 3600
RETENTION_OVERVIEW_SETTING = "retention_overview_snapshot"
RETENTION_OVERVIEW_MAX_AGE_SECONDS = 3600


class _RetentionProgress:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.job_id: int | None = None


class _BackupProgress:
    """Geteilter Fortschritts-Status für das Erstellen eines Backup-ZIPs im
    Hintergrund-Thread (eigene Seite "Backup") — dasselbe Muster wie beim
    Symcon-Import (_UploadProgress/_ImportProgress, Konzept Abschnitt 04):
    /backup/progress wird per htmx-Self-Polling (hx-trigger="every 500ms")
    abgefragt, damit ein großes Archiv den Server nicht für die volle Dauer
    eines einzelnen Requests blockiert."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.done = 0
        self.total = 0
        self.job_id: int | None = None
        self.error: str | None = None

@dataclass(frozen=True)
class BackgroundDependencies:
    """Was die Hintergrundarbeit von außen braucht — Daten oben, Funktionen
    darunter, wie in den übrigen Bereichsmodulen."""

    data_dir: Path
    tz: ZoneInfo
    index: Index
    coordinator: StorageCoordinator
    backups_dir: Path
    symcon_import_dir: Path
    csv_import_dir: Path
    backup_default_time: str
    backup_default_weekday: int
    retention_default_time: str
    retention_default_weekday: int
    #: Zählt Entitäten mit einem abgeschlossenen Monat im Hot Buffer. Bleibt in
    #: main.py, weil es auch der Request-Pfad live aufruft (Housekeeping-Seite).
    count_stale_entities: Callable[[], int]


class BackgroundService:
    """Wartungsplaner, Backup-, Retention- und Abgleich-Läufe samt ihrem Zustand."""

    def __init__(self, deps: BackgroundDependencies) -> None:
        self.deps = deps
        # Abhängigkeiten flach am Dienst, nicht über self.deps.x: Der Code
        # darunter stammt unverändert aus main.py, wo dieselben Namen
        # Modul-Globale waren. Eine Ebene weniger hält den Umzug lesbar und
        # war beim Prüfen die Voraussetzung dafür, ihn umkehrbar zu machen.
        self.data_dir = deps.data_dir
        self.tz = deps.tz
        self.index = deps.index
        self.coordinator = deps.coordinator
        self.backups_dir = deps.backups_dir
        self.symcon_import_dir = deps.symcon_import_dir
        self.csv_import_dir = deps.csv_import_dir
        self.backup_default_time = deps.backup_default_time
        self.backup_default_weekday = deps.backup_default_weekday
        self.retention_default_time = deps.retention_default_time
        self.retention_default_weekday = deps.retention_default_weekday
        self.count_stale_entities = deps.count_stale_entities

        # Wird von main.py nachgereicht: Der Dienst muss vor dem
        # Energiedashboard existieren, weil der Speicherabgleich beim Start
        # früher läuft als dessen Aufbau. Der Wartungsplaner startet erst
        # danach und findet ihn dann vor.
        self.energiedashboard_service: object | None = None
        #: Setzt main.py beim Start: True nach einer Wiederherstellung oder
        #: einem unsauberen Herunterfahren — dann läuft der Abgleich synchron
        #: vor dem ersten Request statt im Hintergrund.
        self.requires_synchronous_reconciliation = False

        # --- Zustand, vorher Modul-Globale in main.py ---------------------
        self.retention_progress = _RetentionProgress()
        self.backup_progress = _BackupProgress()
        self.storage_reconcile_last: dict | None = None
        self._storage_reconcile_thread: threading.Thread | None = None
        self._storage_reconcile_stop = threading.Event()
        self._storage_reconcile_completed = False
        self._maintenance_scheduler_stop = threading.Event()
        self._maintenance_scheduler_thread: threading.Thread | None = None

        # Vom Wartungsplaner (alle 30s) gepflegter Zwischenstand für die Meldungen
        # (housekeeping.rotation_pending). count_stale_entities() selbst nimmt über
        # coordinator.entities() die Sperren ALLER Entitäten — ohne Timeout,
        # wartend auf laufende Exklusiv-Wartung (Backup/VACUUM) und diese ihrerseits
        # blockierend. Als Teil von _notices_context() lief das bisher bei JEDER
        # Template-Antwort, auch bei jedem htmx-Such-Fragment pro Tastendruck — in
        # Produktion mit laufender Ingestion (die dieselben Sperren je Entität hält)
        # der Grund für sekundenlange Hänger beim Filtern der Entitätenliste. Nur
        # _settings_rotation_context() (Housekeeping → Rotation, bewusster
        # Seitenaufruf) zählt weiterhin live.
        self.stale_entity_count_cached = 0

        # Für housekeeping.host_disk_space_low (notices.py) — Host-Speicherplatz statt
        # Zeitarchivs eigener interner Aufschlüsselung. Wie stale_entity_count_cached
        # im Wartungsplaner statt pro Request aktualisiert; shutil.disk_usage() selbst
        # ist zwar günstig genug für den Request-Pfad (siehe index_optimization.py),
        # aber _notices_context() läuft bei JEDER Template-Antwort (siehe deren
        # Docstring) — ein Syscall weniger pro Seitenaufruf ist der einfachere Weg,
        # konsistent mit dem Rest dieser Cache-Gruppe zu bleiben.
        self.host_disk_usage_cached: dict | None = None

        # Zeitpunkt des letzten (versuchten) Wartungsplaner-Durchlaufs — unabhängig
        # davon, ob er erfolgreich war (siehe try/except in
        # _maintenance_scheduler_loop()). Erkennt einen Thread, der ganz aufgehört
        # hat zu ticken (z. B. eine Endlosschleife oder ein blockierender Aufruf ohne
        # eigenes Timeout), nicht nur einzelne fehlgeschlagene Durchläufe — die
        # werden schon geloggt. Initial auf den Startzeitpunkt gesetzt, damit vor dem
        # ersten Tick keine falsche "seit 1970 kein Tick"-Meldung entsteht.
        self.last_scheduler_tick = time.time()

        # Analog zu last_scheduler_tick, aber pro geprüfter Entität statt pro
        # Schleifendurchlauf aktualisiert — der Hintergrundabgleich hat keinen festen
        # Takt wie der Wartungsplaner (Laufzeit hängt von der Datenmenge je Entität
        # ab), ein Deadlock am selben Index-Lock (siehe 0.76.0-Fund in
        # Index.get_or_create_entity()) würde ihn aber genauso unsichtbar hängen
        # lassen. Initial auf den Startzeitpunkt gesetzt, siehe oben.
        self.last_reconcile_tick = time.time()

        # Lebenszeichen des Backup-Hintergrund-Threads — analog zu
        # last_reconcile_tick, aber für create_source_snapshot()/create_backup():
        # dieser Thread läuft IMMER losgelöst vom Wartungsplaner (auch bei geplanten
        # Backups), ein Hang an einem Entitäts-Lock bliebe also vom
        # Scheduler-Heartbeat unentdeckt. Nur relevant, während
        # backup_progress.running True ist (siehe notices.py).
        self.last_backup_worker_tick = time.time()

    def run_storage_reconciliation(self,
        *, entity_ids: list[str] | None = None, repair: bool
    ) -> dict:
        """Gemeinsamer, bereits durch den Aufrufer koordinierter Indexabgleich."""
        report = reconcile.audit_storage_metadata(
            self.data_dir, self.index, self.tz, entity_ids=entity_ids, repair=repair
        )
        self.storage_reconcile_last = report
        if report["mismatches"]:
            logger.warning(
                "Speicherindex %s · event=storage_reconcile_completed mismatches=%d errors=%d",
                "repariert" if report["repaired"] else "geprüft",
                len(report["mismatches"]),
                len(report["errors"]),
            )
        elif report["errors"]:
            logger.error(
                "Speicherindex-Prüfung beendet · event=storage_reconcile_failed errors=%d",
                len(report["errors"]),
            )
        else:
            logger.info(
                "Speicherindex konsistent · event=storage_reconcile_completed entities=%d",
                report["entities_checked"],
            )
        return report

    def reconcile_in_progress(self) -> bool:
        """True nur, während der Hintergrund-Thread tatsächlich noch laufen
        sollte. Im synchronen Modus (self.requires_synchronous_reconciliation) wird
        er nie gestartet — dann bliebe self._storage_reconcile_completed sonst
        dauerhaft False und eine Stall-Meldung würde fälschlich für immer aktiv
        bleiben, obwohl der Abgleich längst (synchron, vor dem ersten Request)
        passiert ist."""
        return self._storage_reconcile_thread is not None and not self._storage_reconcile_completed

    def begin_retention_job(self, trigger: str, scheduled_for: float | None = None) -> int | None:
        with self.retention_progress.lock:
            if self.retention_progress.running:
                if trigger == "scheduled":
                    skipped_id = self.index.create_retention_job(trigger, scheduled_for)
                    self.index.update_retention_job(
                        skipped_id,
                        status="skipped",
                        finished_at=time.time(),
                        error="Übersprungen, weil bereits ein Retention-Lauf aktiv ist",
                    )
                return None
            job_id = self.index.create_retention_job(trigger, scheduled_for)
            self.retention_progress.running = True
            self.retention_progress.job_id = job_id
            return job_id

    def finish_retention_job(self, job_id: int, *, now: datetime | None = None) -> dict:
        started_at = time.time()
        self.index.update_retention_job(job_id, status="running", started_at=started_at)
        logger.info("Retention gestartet · event=retention_started job_id=%d", job_id)
        try:
            with self.coordinator.exclusive():
                totals = retention_mod.enforce_retention_all(self.data_dir, self.index, self.tz, now=now)
            finished_at = time.time()
            self.index.update_retention_job(
                job_id,
                status="success",
                finished_at=finished_at,
                rows_deleted=totals["rows_deleted"],
                bytes_freed=totals["bytes_freed"],
                months_deleted=totals["months_deleted"],
                entities_affected=totals["entities_affected"],
            )
            self.index.set_setting("retention_enforcement_last_run", str(finished_at))
            self.index.set_setting("retention_last_success", str(finished_at))
            try:
                self.refresh_retention_overview_if_stale(force=True)
            except Exception:
                # Die Löschung war erfolgreich; ein Fehler der rein informativen
                # Folgevorschau darf den Job nicht nachträglich als Fehler markieren.
                logger.exception(
                    "Retention-Übersicht konnte nicht aktualisiert werden · "
                    "event=retention_followup_failed job_id=%d",
                    job_id,
                )
            logger.info(
                "Retention erfolgreich · event=retention_completed job_id=%d rows_deleted=%d "
                "months_deleted=%d bytes_freed=%d duration_s=%.1f",
                job_id,
                totals["rows_deleted"],
                totals["months_deleted"],
                totals["bytes_freed"],
                max(0.0, finished_at - started_at),
            )
            return {"status": "success", "totals": totals}
        except Exception as exc:
            logger.exception(
                "Retention fehlgeschlagen · event=retention_failed job_id=%d phase=enforce",
                job_id,
            )
            finished_at = time.time()
            error = str(exc)[:2000] or exc.__class__.__name__
            self.index.update_retention_job(
                job_id,
                status="failed",
                finished_at=finished_at,
                error=error,
            )
            self.index.set_setting("retention_last_failure", str(finished_at))
            return {"status": "failed", "error": error}
        finally:
            with self.retention_progress.lock:
                self.retention_progress.running = False
                self.retention_progress.job_id = None

    def _run_retention_background(self, *, scheduled_for: float | None = None) -> bool:
        job_id = self.begin_retention_job("scheduled", scheduled_for)
        if job_id is None:
            return False
        threading.Thread(
            target=self.finish_retention_job,
            args=(job_id,),
            name="zeitarchiv-retention",
            daemon=True,
        ).start()
        return True

    def set_next_retention_run(self, now: datetime) -> float | None:
        schedule = self.index.get_setting("retention_enforcement", "off")
        if schedule not in ("daily", "weekly"):
            schedule = "off"
        weekday = int(self.index.get_setting("retention_enforcement_weekday", str(self.retention_default_weekday)))
        next_run = next_scheduled_run(
            now,
            schedule,
            self.index.get_setting("retention_enforcement_time", self.retention_default_time),
            weekday,
        )
        self.index.set_setting("retention_enforcement_next_run", "" if next_run is None else str(next_run.timestamp()))
        return None if next_run is None else next_run.timestamp()

    def _run_retention_enforcement_if_due(self, now: datetime) -> None:
        """Führt genau einen fälligen Lauf (täglich/wöchentlich) aus, unabhängig von Requests."""
        if self.index.get_setting("retention_enforcement", "off") not in ("daily", "weekly"):
            return
        raw_next = self.index.get_setting("retention_enforcement_next_run", "")
        try:
            next_ts = float(raw_next) if raw_next else self.set_next_retention_run(now)
        except (TypeError, ValueError):
            next_ts = self.set_next_retention_run(now)
        if next_ts is None or now.timestamp() < next_ts:
            return
        self._run_retention_background(scheduled_for=next_ts)
        self.set_next_retention_run(now)

    def _empty_retention_overview(self) -> dict:
        return {
            "generated_at": None,
            "totals": {
                "rows_deleted": 0,
                "bytes_freed": 0,
                "months_deleted": 0,
                "entities_affected": 0,
            },
            "groups": [],
        }

    def load_retention_overview(self) -> dict:
        raw = self.index.get_setting(RETENTION_OVERVIEW_SETTING, "")
        if not raw:
            return self._empty_retention_overview()
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._empty_retention_overview()
        if not isinstance(value, dict) or not isinstance(value.get("groups"), list):
            return self._empty_retention_overview()
        return value

    def refresh_retention_overview_if_stale(self, *, force: bool = False) -> dict:
        """Aktualisiert die teure Dateivorschau höchstens einmal pro Stunde."""
        current = self.load_retention_overview()
        generated_at = current.get("generated_at")
        now_ts = time.time()
        if (
            not force
            and isinstance(generated_at, (int, float))
            and now_ts - generated_at < RETENTION_OVERVIEW_MAX_AGE_SECONDS
        ):
            return current
        limited_ids = [
            entity["entity_id"]
            for entity in self.index.list_entities()
            if entity["retention"] != "unlimited"
        ]
        with self.coordinator.entities(limited_ids):
            overview = retention_mod.preview_retention_overview(self.data_dir, self.index, self.tz)
        self.index.set_setting(
            RETENTION_OVERVIEW_SETTING,
            json.dumps(overview, ensure_ascii=False, separators=(",", ":")),
        )
        logger.debug(
            "Retention-Übersicht aktualisiert · fällige Zeilen=%d · Entitäten=%d",
            overview["totals"]["rows_deleted"],
            overview["totals"]["entities_affected"],
        )
        return overview

    def invalidate_retention_overview(self) -> None:
        self.index.set_setting(RETENTION_OVERVIEW_SETTING, "")

    def _refresh_stale_entity_count(self) -> None:
        self.stale_entity_count_cached = self.count_stale_entities()

    def _refresh_host_disk_usage(self) -> None:
        usage = shutil.disk_usage(self.data_dir)
        self.host_disk_usage_cached = {"free": usage.free, "total": usage.total}

    def _empty_purge_preview(self) -> dict:
        return {
            "generated_at": None,
            "totals": {
                "marked_rows": 0,
                "removable_rows": 0,
                "hot_rows": 0,
                "archive_rows": 0,
                "archive_months": 0,
                "entities_affected": 0,
                "not_removable_rows": 0,
            },
            "rows": [],
        }

    def invalidate_purge_preview(self) -> None:
        """Erzwingt eine Aktualisierung der Bereinigungsvorschau beim nächsten
        Wartungsplaner-Durchlauf (binnen ~30s), NICHT synchron im aufrufenden
        Request — der Vollscan ist teuer (siehe self.refresh_purge_preview_if_stale)
        und würde sonst jeden einzelnen Markier-Klick auf der Bereinigungsseite
        spürbar verlangsamen. Nach dem Markieren neuer Datensätze zur Löschung
        aufgerufen (delete_rows, Duplikate-/Wiederholungen-Löschung), damit
        Housekeeping → Speicherplatz nicht bis zu einer Stunde lang veraltete
        Zahlen zeigt, nur weil noch niemand "Jetzt bereinigen" geklickt hat."""
        current = self.load_purge_preview()
        current["generated_at"] = 0
        self.index.set_setting(PURGE_PREVIEW_SETTING, json.dumps(current, ensure_ascii=False, separators=(",", ":")))

    def load_purge_preview(self) -> dict:
        raw = self.index.get_setting(PURGE_PREVIEW_SETTING, "")
        if not raw:
            return self._empty_purge_preview()
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._empty_purge_preview()
        if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
            return self._empty_purge_preview()
        return value

    def refresh_purge_preview_if_stale(self, *, force: bool = False) -> dict:
        """Aktualisiert die teure Bereinigungsvorschau (liest für jede Entität mit
        markierten Löschungen die betroffenen Archiv-Parquet-Dateien) höchstens
        einmal pro Stunde — dieselbe Zwischenspeicher-Konvention wie
        self.refresh_retention_overview_if_stale(). Ohne diesen Cache lief
        preview_purge() bei JEDEM Aufruf von /settings neu; bei einer Entität mit
        sehr vielen markierten Zeilen und vielen Archiv-Monaten machte allein das
        die Einstellungen-Seite spürbar langsam (mehrere Sekunden)."""
        current = self.load_purge_preview()
        generated_at = current.get("generated_at")
        now_ts = time.time()
        if (
            not force
            and isinstance(generated_at, (int, float))
            and now_ts - generated_at < PURGE_PREVIEW_MAX_AGE_SECONDS
        ):
            return current
        entity_ids = [row["entity_id"] for row in self.index.get_deleted_points_by_entity()]
        with self.coordinator.entities(entity_ids):
            preview = cleanup.preview_purge(self.data_dir, self.index, self.tz)
        preview["generated_at"] = now_ts
        self.index.set_setting(
            PURGE_PREVIEW_SETTING,
            json.dumps(preview, ensure_ascii=False, separators=(",", ":")),
        )
        logger.debug(
            "Bereinigungsvorschau aktualisiert · entfernbare Zeilen=%d · Entitäten=%d",
            preview["totals"]["removable_rows"],
            preview["totals"]["entities_affected"],
        )
        return preview

    def run_backup(self, *, trigger: str = "manual", scheduled_for: float | None = None) -> bool:
        with self.backup_progress.lock:
            if self.backup_progress.running:
                if trigger == "scheduled":
                    skipped_id = self.index.create_backup_job(trigger, scheduled_for)
                    self.index.update_backup_job(
                        skipped_id,
                        status="skipped",
                        finished_at=time.time(),
                        error="Übersprungen, weil bereits ein Backup läuft",
                    )
                return False
            job_id = self.index.create_backup_job(trigger, scheduled_for)
            self.backup_progress.running = True
            self.backup_progress.done = 0
            self.backup_progress.total = backup.estimate_file_count(self.data_dir)
            self.backup_progress.job_id = job_id
            self.backup_progress.error = None
        logger.info(
            "Backup gestartet · event=backup_started job_id=%d trigger=%s",
            job_id,
            trigger,
        )

        def on_progress(done: int, total: int) -> None:
            with self.backup_progress.lock:
                self.backup_progress.done = done
                self.backup_progress.total = total
            self.last_backup_worker_tick = time.time()

        def on_entity_snapshot_done(done: int, total: int) -> None:
            self.last_backup_worker_tick = time.time()

        def worker() -> None:
            started_at = time.time()
            self.last_backup_worker_tick = started_at
            self.index.update_backup_job(job_id, status="running", started_at=started_at)
            snapshot_dir = self.backups_dir / f".backup-source-{job_id}-{secrets.token_hex(6)}"
            try:
                self.backups_dir.mkdir(parents=True, exist_ok=True)
                backup.cleanup_stale_source_snapshots(self.backups_dir)
                entity_ids = [row["entity_id"] for row in self.index.list_entities()]
                backup.create_source_snapshot(
                    self.data_dir,
                    snapshot_dir,
                    entity_ids,
                    self.coordinator,
                    on_entity_done=on_entity_snapshot_done,
                )
                with self.backup_progress.lock:
                    self.backup_progress.total = backup.estimate_file_count(snapshot_dir)

                dest_path = self.backups_dir / f"zeitarchiv-backup-{datetime.now(self.tz).strftime('%Y-%m-%d-%H%M%S')}.zip"
                backup.create_backup(
                    snapshot_dir,
                    dest_path,
                    on_progress=on_progress,
                    consistent_sqlite=True,
                    metadata={
                        "timezone": str(self.tz),
                        "trigger": trigger,
                        "snapshot_mode": "entity-consistent",
                    },
                )
                keep_count_raw = self.index.get_setting("backup_keep_count", "unlimited")
                keep_days_raw = self.index.get_setting("backup_keep_days", "unlimited")
                keep_count = int(keep_count_raw) if keep_count_raw != "unlimited" else None
                keep_days = retention_mod.RETENTION_DAYS.get(keep_days_raw)
                cleanup_error = None
                try:
                    backup.prune_backups(self.backups_dir, keep_count, keep_days, time.time())
                except OSError as exc:
                    cleanup_error = f"Backup gültig; alte Sicherungen konnten nicht bereinigt werden: {exc}"[:2000]
                    logger.exception(
                        "Alte Backups konnten nicht bereinigt werden · "
                        "event=backup_prune_failed job_id=%d",
                        job_id,
                    )
                finished_at = time.time()
                self.index.update_backup_job(
                    job_id,
                    status="success",
                    finished_at=finished_at,
                    filename=dest_path.name,
                    size_bytes=dest_path.stat().st_size,
                    error=cleanup_error,
                )
                self.index.set_setting("backup_last_success", str(finished_at))
                logger.info(
                    "Backup erfolgreich · event=backup_completed job_id=%d file=%s "
                    "size_bytes=%d duration_s=%.1f",
                    job_id,
                    dest_path.name,
                    dest_path.stat().st_size,
                    max(0.0, finished_at - started_at),
                )
            except Exception as exc:
                logger.exception(
                    "Backup fehlgeschlagen · event=backup_failed job_id=%d",
                    job_id,
                )
                finished_at = time.time()
                error = str(exc)[:2000] or exc.__class__.__name__
                self.index.update_backup_job(
                    job_id,
                    status="failed",
                    finished_at=finished_at,
                    error=error,
                )
                self.index.set_setting("backup_last_failure", str(finished_at))
                with self.backup_progress.lock:
                    self.backup_progress.error = error
            finally:
                shutil.rmtree(snapshot_dir, ignore_errors=True)
                with self.backup_progress.lock:
                    self.backup_progress.running = False
                    self.backup_progress.job_id = None

        threading.Thread(target=worker, daemon=True).start()
        return True

    def set_next_backup_run(self, now: datetime) -> float | None:
        schedule = self.index.get_setting("backup_schedule", "off")
        time_value = self.index.get_setting("backup_schedule_time", self.backup_default_time)
        weekday = int(self.index.get_setting("backup_schedule_weekday", str(self.backup_default_weekday)))
        next_run = next_scheduled_run(now, schedule, time_value, weekday)
        self.index.set_setting("backup_schedule_next_run", "" if next_run is None else str(next_run.timestamp()))
        return None if next_run is None else next_run.timestamp()

    def _run_backup_schedule_if_due(self, now: datetime) -> None:
        """Startet höchstens einen verpassten Termin und plant sofort den nächsten."""
        schedule = self.index.get_setting("backup_schedule", "off")
        if schedule not in {"daily", "weekly"}:
            return
        raw_next = self.index.get_setting("backup_schedule_next_run", "")
        try:
            next_ts = float(raw_next) if raw_next else self.set_next_backup_run(now)
        except (TypeError, ValueError):
            next_ts = self.set_next_backup_run(now)
        if next_ts is None or now.timestamp() < next_ts:
            return
        self.run_backup(trigger="scheduled", scheduled_for=next_ts)
        self.set_next_backup_run(now)

    def _background_storage_reconciliation(self) -> None:
        """Prüft einen normalen, sauber beendeten Bestand entitätsweise.

        Dadurch ist der HTTP-Listener sofort verfügbar und ein großer Bestand hält
        nie sämtliche Entitäten gleichzeitig an. Nach Restore/Crash wird dieser
        Pfad bewusst nicht verwendet; dort lief der Abgleich bereits synchron.
        """
        started_at = time.time()
        reports: list[dict] = []
        entities = [entity["entity_id"] for entity in self.index.list_entities()]
        for entity_id in entities:
            if self._storage_reconcile_stop.is_set():
                return
            with self.coordinator.entity(entity_id):
                reports.append(
                    reconcile.audit_storage_metadata(
                        self.data_dir, self.index, self.tz, entity_ids=[entity_id], repair=True
                    )
                )
            # Nach jeder Entität statt nur einmal am Ende — sonst würde ein Hänger
            # an der Entitäts-Sperre (with self.coordinator.entity(...)) oder
            # im audit_storage_metadata()-Aufruf selbst nie sichtbar, weil der
            # Tick sowieso erst nach vollständigem Durchlauf käme.
            self.last_reconcile_tick = time.time()
        self.storage_reconcile_last = {
            "checked_at": time.time(),
            "started_at": started_at,
            "entities_checked": sum(report["entities_checked"] for report in reports),
            "mismatches": [item for report in reports for item in report["mismatches"]],
            "errors": [item for report in reports for item in report["errors"]],
            "repaired": any(report["repaired"] for report in reports),
            "background": True,
        }
        self._storage_reconcile_completed = True
        logger.info(
            "Speicherindex-Hintergrundabgleich beendet · event=storage_reconcile_completed "
            "entities=%d mismatches=%d errors=%d duration_s=%.1f",
            self.storage_reconcile_last["entities_checked"],
            len(self.storage_reconcile_last["mismatches"]),
            len(self.storage_reconcile_last["errors"]),
            max(0.0, time.time() - started_at),
        )

    def _refresh_duplicate_snapshot_if_stale(self) -> None:
        """Berechnet die Duplikat-Zählung für /housekeeping höchstens einmal pro
        Stunde im Hintergrund (ZP-002 in PERFORMANCE.md) — dieselbe teure
        Rohdaten-Prüfung wie zuvor, aber nicht mehr bei jedem Seitenaufruf."""
        if not self.index.is_duplicate_snapshot_stale():
            return
        rows = cleanup.count_duplicate_rows_by_entity(
            self.data_dir, self.index, self.tz, max_rows_per_entity=MAX_UI_ANALYSIS_ROWS
        )
        self.index.set_duplicate_snapshot(
            [{"entity_id": r["entity_id"], "friendly_name": r["friendly_name"], "count": r["count"]} for r in rows]
        )

    def _maintenance_scheduler_loop(self) -> None:
        """Prüft interne Zeitpläne und schreibt Statistikpunkte ohne UI-Aufruf."""
        while not self._maintenance_scheduler_stop.is_set():
            try:
                if self.index.record_stats_snapshot_if_stale():
                    logger.debug(
                        "Stündlicher Statistik-Schnappschuss gespeichert · "
                        "event=hourly_stats_snapshot_completed"
                    )
                supervisor_stats.maybe_record_memory_snapshot(self.index)
                self.refresh_retention_overview_if_stale()
                self.refresh_purge_preview_if_stale()
                self._refresh_duplicate_snapshot_if_stale()
                self._refresh_stale_entity_count()
                self._refresh_host_disk_usage()
                notices_mod.refresh_import_leftovers_if_stale(self.symcon_import_dir, self.csv_import_dir)
                version_check.refresh_if_stale(self.index)
                ha_integration.refresh_integration_version_check_if_stale(self.index)
                process_pending_hourly_backfill(self.data_dir, self.index, self.tz, self.coordinator)
                refresh_heatmap_weekday_cache_if_stale(self.energiedashboard_service)
                self._run_backup_schedule_if_due(datetime.now(self.tz))
                self._run_retention_enforcement_if_due(datetime.now(self.tz))
            except Exception:
                logger.exception(
                    "Wartungsplaner konnte den nächsten Lauf nicht prüfen · "
                    "event=maintenance_scheduler_failed"
                )
            self.last_scheduler_tick = time.time()
            self._maintenance_scheduler_stop.wait(30)

    def start(self) -> None:
        # Einmalig beim Start: entities.hourly_rollup für eine bereits VOR diesem
        # Feature gespeicherte Energiedashboard-Konfiguration nachziehen, sonst
        # bräuchte jede bestehende Installation ein manuelles erneutes Speichern
        # des Setup-Formulars, damit der rückwirkende Backfill überhaupt anläuft.
        sync_hourly_rollup_flags_for_current_config(self.energiedashboard_service)
        # Einmal vorab, damit die erste Seite nach dem Start nicht 30s lang
        # fälschlich "0 ausstehende Rotationen" meldet — zu diesem Zeitpunkt hält
        # noch niemand Entitäts-Sperren, der Aufruf ist hier ungefährlich.
        try:
            self._refresh_stale_entity_count()
        except Exception:
            logger.exception("Rotation-Zähler beim Start nicht ermittelbar · event=stale_entity_count_failed")
        try:
            self._refresh_host_disk_usage()
        except Exception:
            logger.exception("Host-Speicherplatz beim Start nicht ermittelbar · event=host_disk_usage_failed")
        if not self.requires_synchronous_reconciliation and (
            self._storage_reconcile_thread is None or not self._storage_reconcile_thread.is_alive()
        ):
            self._storage_reconcile_stop.clear()
            self._storage_reconcile_thread = threading.Thread(
                target=self._background_storage_reconciliation,
                name="zeitarchiv-storage-reconcile",
                daemon=True,
            )
            self._storage_reconcile_thread.start()
        if self._maintenance_scheduler_thread is not None and self._maintenance_scheduler_thread.is_alive():
            return
        self._maintenance_scheduler_stop.clear()
        self._maintenance_scheduler_thread = threading.Thread(
            target=self._maintenance_scheduler_loop,
            name="zeitarchiv-maintenance-scheduler",
            daemon=True,
        )
        self._maintenance_scheduler_thread.start()

    def stop(self) -> None:
        self._maintenance_scheduler_stop.set()
        if self._maintenance_scheduler_thread is not None:
            self._maintenance_scheduler_thread.join(timeout=5)
        self._storage_reconcile_stop.set()
        if self._storage_reconcile_thread is not None:
            self._storage_reconcile_thread.join(timeout=5)
        # Ein abgebrochener Hintergrundabgleich gilt vorsichtshalber nicht als
        # sauberer Shutdown; dann wird beim nächsten Start synchron geprüft.
        if self.requires_synchronous_reconciliation or self._storage_reconcile_completed:
            self.index.set_setting("storage_clean_shutdown", "1")
