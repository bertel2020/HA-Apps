"""Zeitarchiv-App: FastAPI-Anwendung.

Phase 1: Schreibpfad (POST /api/write) + Gesundheitscheck (GET /api/health) +
Tabellenansicht in Ingress (GET /).

Phase 2: Rollup-Aggregation (storage/rollup.py, ausgelöst über rotate.py),
Chart-Daten (GET /api/query), Chart-Seite (GET /entities/{id}) und
Bereinigungs-Werkzeug (GET/POST /entities/{id}/rows, .../cleanup) — Details
und Entscheidungen siehe Konzept Abschnitt 04/05 und
~/.claude/plans/glittery-singing-wombat.md.
"""

from __future__ import annotations

import calendar
import dataclasses
import json
import logging
import math
import os
import platform
import secrets
import shutil
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .formatting import (
    BACKUP_KEEP_COUNT_LABELS,
    BACKUP_SCHEDULE_LABELS,
    DECIMALS_LABELS,
    DISPLAY_MODE_LABELS,
    FONT_SCALE_LABELS,
    GAP_THRESHOLD_LABELS,
    OUTLIER_THRESHOLD_LABELS,
    RESOLUTION_LABELS,
    RETENTION_LABELS,
    VALUE_FILTER_LABELS,
    decimals_to_int,
    format_int,
    format_resolution,
    format_retention,
    format_size,
    format_time,
    format_timestamp,
    format_type,
    format_value,
    parse_localized_number,
)
from .limits import (
    MAX_CSV_UPLOAD_BYTES,
    MAX_EXPORT_ROWS,
    MAX_IMPORT_ROWS_PER_ENTITY,
    MAX_SETTINGS_UPLOAD_BYTES,
    MAX_UI_ANALYSIS_ROWS,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
    MAX_ZIP_UPLOAD_BYTES,
)
from .log_source import load_log_lines
from . import supervisor_stats
from .logging_setup import (
    ACCESS_LOG_LABELS,
    DEFAULT_ACCESS_LOG_MODE,
    DEFAULT_LOG_LEVEL,
    LOG_LEVEL_LABELS,
    configure_logging,
    log_http_request,
)
from .security import ensure_api_token, generate_api_token
from .backup_scheduler import next_scheduled_run, parse_schedule_time
from .storage import (
    backup,
    csv_import,
    cleanup,
    entity_removal,
    hotbuffer,
    import_reports,
    retention as retention_mod,
    reconcile,
    rotate,
    symcon_import,
)
from .storage import query as query_mod
from .storage.index import DEFAULT_RESOLUTION, DEFAULT_RETENTION, Index
from .storage.ingestion import IngestionService
from .storage.coordinator import StorageCoordinator
from .storage.paths import ENTITY_ID_MAX_LENGTH, ENTITY_ID_PATTERN, validate_entity_id
from .timezone_config import load_timezone
from .version import APP_VERSION
from . import api_routes
from .report_routes import ReportDependencies, ReportService
from .route_support import storage_locked

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("zeitarchiv.trace")

EntityId = Annotated[
    str,
    Field(
        min_length=3,
        max_length=ENTITY_ID_MAX_LENGTH,
        pattern=ENTITY_ID_PATTERN,
    ),
]

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("ZEITARCHIV_DATA_DIR", "/data"))
# Entpackter Symcon-db-Ordner aus einem ZIP-Upload (Konzept Abschnitt 03) — kein
# Bind-Mount mehr nötig, bleibt bis zum expliziten /import/delete erhalten.
SYMCON_IMPORT_DIR = DATA_DIR / "symcon_import"
# Optionale ID→Name-Zuordnung aus einer separat hochgeladenen settings.json
# (Konzept "Offene Punkte") — bleibt gespeichert (kein erneuter Upload bei
# jedem Seitenaufruf/Neustart nötig), bewusst außerhalb von SYMCON_IMPORT_DIR,
# damit ein erneuter db-ZIP-Upload (ersetzt SYMCON_IMPORT_DIR komplett, siehe
# extract_zip) die einmal hochgeladenen Namen nicht mit wegwirft; Symcon-
# Variablen-IDs bleiben über Re-Exports desselben Systems ohnehin stabil. Der
# "Daten löschen"-Button (import_delete()) räumt beides zusammen weg — ein
# bewusster, kompletter Reset der Import-Sitzung, keine zwei getrennten.
SYMCON_NAMES_PATH = DATA_DIR / "symcon_names.json"
SYMCON_SOURCE_META_PATH = DATA_DIR / "symcon_source.json"
# Wie SYMCON_NAMES_PATH auf Platte statt nur im Prozessspeicher: das reine
# In-Memory-_ScanCache (siehe unten) verliert seinen Inhalt bei jedem
# Server-Neustart, wodurch /import trotz unveränderter, längst entpackter
# Daten jedes Mal von neuem den kompletten Symcon-Ordner scannen musste —
# bei einem echten Export mit hunderten Variablen spürbar langsam. Die Datei
# hält denselben Scan über einen Neustart hinweg fest; ein neuer ZIP-Upload
# überschreibt sie ohnehin komplett, "Daten löschen" räumt sie mit auf.
SYMCON_SCAN_CACHE_PATH = DATA_DIR / "symcon_scan_cache.json"
# Eigener CSV-Import (Konzept "Offene Punkte") — bewusst getrennt von
# SYMCON_IMPORT_DIR: eigenes, viel einfacheres Format (eine Datei, ein Ziel-
# Entität statt hunderter Symcon-Variablen), soll auf der Import-Seite klar
# getrennt vom Symcon-Assistenten bleiben statt sich mit dessen Zustand zu
# vermischen. Nur eine Datei gleichzeitig — ein neuer Upload ersetzt die vorige.
CSV_IMPORT_DIR = DATA_DIR / "csv_import"
# Backup (eigene Seite "Backup") — jeder Lauf schreibt eine neue,
# per Zeitstempel benannte Datei in ein eigenes Verzeichnis (statt eine feste
# Datei zu überschreiben), damit mehrere Stände nebeneinander bestehen bleiben
# und einzeln herunterladbar sind (Backup-Liste). Aufräumen übernimmt
# prune_backups() nach den Einstellungen "Anzahl behalten"/"Max. Alter".
BACKUPS_DIR = DATA_DIR / "backups"
_restore_startup_result = backup.apply_pending_restore(DATA_DIR, BACKUPS_DIR)
BACKUP_DEFAULT_TIME = "03:30"
BACKUP_DEFAULT_WEEKDAY = 6
RETENTION_DEFAULT_TIME = "04:30"
BACKUP_WEEKDAY_OPTIONS = [
    (0, "Montag"), (1, "Dienstag"), (2, "Mittwoch"), (3, "Donnerstag"),
    (4, "Freitag"), (5, "Samstag"), (6, "Sonntag"),
]


def _load_options() -> dict:
    options_path = DATA_DIR / "options.json"
    if options_path.exists():
        return json.loads(options_path.read_text(encoding="utf-8"))
    return {}


_OPTIONS = _load_options()
TZ = load_timezone(_OPTIONS, on_invalid=logger.error)

# Je eine Stufe unter/über "Normal" (Einstellungen, Bereich "Darstellung").
#
# Frühere Version davon nutzte CSS "zoom" auf <body> statt dieses reinen
# Multiplikators — zoom skaliert zwar mit echtem Reflow (anders als
# transform:scale), reißt aber die Mauskoordinaten-Berechnung von <canvas>-
# Inhalten unter sich mit: ECharts/zrender berechnen Klick-/Hover-Position aus
# dem MouseEvent relativ zur eigenen Canvas-Bounding-Box, und genau dieser
# Bezug geriet unter zoom messbar daneben (sichtbar als "Mauszeiger wirkt
# versetzt" auf jedem Chart inkl. des Donut-Charts, und als Legenden, die
# scheinbar nicht auf Klicks reagierten). Da JEDE Seite mit einem Chart
# betroffen war, kam ein Gegen-zoom auf einzelne Chart-Container als Workaround
# nicht in Frage — stattdessen jetzt ein reiner Zahlen-Multiplikator
# (--font-scale), den nur einzelne font-size-Deklarationen per
# calc(Npx * var(--font-scale, 1)) aufgreifen. Kein <canvas> und keine
# Maus-Koordinate wird dadurch je berührt, das Problem ist damit strukturell
# ausgeschlossen statt nur kaschiert. Skaliert bewusst NUR Schriftgrößen, nicht
# Abstände/Breiten (die blieben in px) — die praktisch übliche Bedeutung einer
# "Schriftgröße"-Einstellung, und ohne den Aufwand, jede Größenangabe im
# geteilten Stylesheet und in den Seiten-eigenen <style>-Blöcken anzufassen.
#
# Die drei vorhandenen Faktoren behalten ihre Schlüssel: bestehende Auswahl
# "Etwas kleiner" wird zu "Kleiner", "Normal" zu "Klein" und "Etwas größer"
# zu "Normal". So ist keine Migration gespeicherter Einstellungen nötig.
FONT_SCALE = {"0": "0.9", "1": "1", "2": "1.125", "3": "1.25", "4": "1.4"}
DASHBOARD_ROW_HEIGHT = {"0": 206, "1": 210, "2": 218, "3": 228, "4": 240}
COLOR_SCHEME_LABELS = {
    "zeitarchiv": "Zeitarchiv",
    "home_assistant": "Home Assistant",
}
COLOR_MODE_LABELS = {
    "auto": "Automatisch",
    "light": "Hell",
    "dark": "Dunkel",
}
# Vormals eine pro-Chart-Einstellung (saved_charts.dashboard_animation) — gilt
# jetzt global für alle Dashboard-Kacheln (Einstellungen → Darstellung), da
# eine Kachel-für-Kachel-Steuerung in der Praxis kaum genutzt wurde und die
# Chart-Bearbeitung dafür unnötig überladen hat.
DASHBOARD_ANIMATION_LABELS = {"1": "An", "0": "Aus"}


def _font_scale_context(request: Request) -> dict:
    """Starlette context_processor: läuft für JEDE TemplateResponse automatisch
    mit, ohne dass jede einzelne Route den Skalierungsfaktor selbst in ihren
    Kontext aufnehmen müsste."""
    font_scale = index.get_setting("font_scale", "1")
    if font_scale not in FONT_SCALE:
        font_scale = "2"
    color_scheme = index.get_setting("color_scheme", "zeitarchiv")
    color_mode = index.get_setting("color_mode", "auto")
    if color_scheme not in COLOR_SCHEME_LABELS:
        color_scheme = "zeitarchiv"
    if color_mode not in COLOR_MODE_LABELS:
        color_mode = "auto"
    dashboard_animation = index.get_setting("dashboard_animation", "1")
    if dashboard_animation not in DASHBOARD_ANIMATION_LABELS:
        dashboard_animation = "1"
    return {
        "font_scale_value": FONT_SCALE.get(font_scale, FONT_SCALE["1"]),
        "dashboard_row_height": DASHBOARD_ROW_HEIGHT[font_scale],
        "color_scheme": color_scheme,
        "color_mode": color_mode,
        "dashboard_animation_enabled": dashboard_animation == "1",
    }


def _app_root_context(request: Request) -> dict:
    """Ingress-Präfix für absolute Links; lokal bleibt die App bei ``/``."""
    ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
    if (
        ingress_path
        and ingress_path.startswith("/")
        and ".." not in ingress_path
        and all(char.isalnum() or char in "/_-" for char in ingress_path)
    ):
        app_root = ingress_path
    else:
        app_root = str(request.scope.get("root_path", "")).rstrip("/")
    return {"app_root": app_root}


app = FastAPI(title="Zeitarchiv")
templates = Jinja2Templates(
    directory=str(APP_DIR / "templates"),
    context_processors=[_font_scale_context, _app_root_context],
)


@app.middleware("http")
async def _request_logging(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_http_request(
            request.method,
            request.url.path,
            500,
            (time.perf_counter() - started) * 1000,
        )
        raise
    log_http_request(
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; "
        "img-src 'self' data:; connect-src 'self'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        # Alpine.js' Standard-Build kompiliert x-* Ausdrücke über
        # AsyncFunction und benötigt deshalb unsafe-eval. unsafe-inline wird
        # für die bestehenden seitenlokalen Skripte/Handler benötigt. Externe
        # Skriptquellen bleiben trotzdem vollständig auf 'self' begrenzt.
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
# Cache-Busting fürs geteilte Stylesheet (Design-System, siehe app/static/css/README.md):
# der Browser cacht static/-Antworten sonst über Deploys hinweg, weil StaticFiles
# keinen Cache-Control-Header setzt und nur Last-Modified/ETag liefert — je nach
# Heuristik reicht das nicht für ein zuverlässiges Update. Ein an die Datei-mtime
# gekoppelter Query-Parameter zwingt bei jeder Änderung eine frische Anfrage.
templates.env.globals["css_v"] = int((APP_DIR / "static" / "css" / "app.css").stat().st_mtime)
# Dieselbe Cache-Busting-Begründung wie oben, nur fürs JS (calendar-picker.js,
# confirm-dialog.js, …) — ohne das blieb z. B. ein Fix in calendar-picker.js im
# Browser-Cache unbemerkt hängen, obwohl der Server längst die neue Version
# ausliefert. Eine gemeinsame mtime über alle JS-Dateien statt einer pro Datei:
# einfacher als js_v-Kopien an jeder <script>-Stelle zu pflegen, und ändert sich
# ohnehin bei jedem Deploy dieses Verzeichnisses.
templates.env.globals["js_v"] = int(max(p.stat().st_mtime for p in (APP_DIR / "static" / "js").glob("*.js")))
# Upload-Grenzen zentral aus limits.py an die Oberfläche weiterreichen. So
# zeigen die Dropzones stets dieselben Werte an, die das Backend tatsächlich
# durchsetzt, statt leicht veraltende Zahlen in mehreren Templates zu pflegen.
templates.env.globals["import_limits"] = {
    "zip_upload_gib": MAX_ZIP_UPLOAD_BYTES // (1024 * 1024 * 1024),
    "csv_upload_mib": MAX_CSV_UPLOAD_BYTES // (1024 * 1024),
    "zip_uncompressed_gib": MAX_ZIP_UNCOMPRESSED_BYTES // (1024 * 1024 * 1024),
    "zip_members": format_int(MAX_ZIP_MEMBERS),
}

index = Index(DATA_DIR / "index.sqlite")
_previous_shutdown_clean = index.get_setting("storage_clean_shutdown", "0") == "1"
# Bereits beim Prozessaufbau als "läuft/unsauber" markieren. Nur der reguläre
# Shutdown setzt den Wert zurück; ein Crash erzwingt beim nächsten Start den
# synchronen Sicherheitsabgleich.
index.set_setting("storage_clean_shutdown", "0")
configure_logging(
    index.get_setting("log_level", DEFAULT_LOG_LEVEL) or DEFAULT_LOG_LEVEL,
    index.get_setting("access_log_mode", DEFAULT_ACCESS_LOG_MODE) or DEFAULT_ACCESS_LOG_MODE,
)
_interrupted_backup_jobs = index.recover_interrupted_backup_jobs()
if _interrupted_backup_jobs:
    logger.warning("%d unterbrochene Backup-Jobs erkannt", _interrupted_backup_jobs)
_interrupted_retention_jobs = index.recover_interrupted_retention_jobs()
if _interrupted_retention_jobs:
    logger.warning("%d unterbrochene Retention-Jobs erkannt", _interrupted_retention_jobs)
# Bestehende Installationen hatten nur diesen erfolgreichen Last-run-Wert.
# Einmalig in die neue, semantisch eindeutige Einstellung übernehmen.
if not index.get_setting("retention_last_success"):
    _legacy_retention_last_run = index.get_setting("retention_enforcement_last_run")
    if _legacy_retention_last_run:
        index.set_setting("retention_last_success", _legacy_retention_last_run)
storage_coordinator = StorageCoordinator()


def _run_storage_reconciliation(
    *, entity_ids: list[str] | None = None, repair: bool
) -> dict:
    """Gemeinsamer, bereits durch den Aufrufer koordinierter Indexabgleich."""
    global _storage_reconcile_last
    report = reconcile.audit_storage_metadata(
        DATA_DIR, index, TZ, entity_ids=entity_ids, repair=repair
    )
    _storage_reconcile_last = report
    if report["mismatches"]:
        logger.warning(
            "Speicherindex %s · Abweichungen=%d · Fehler=%d",
            "repariert" if report["repaired"] else "geprüft",
            len(report["mismatches"]),
            len(report["errors"]),
        )
    elif report["errors"]:
        logger.error("Speicherindex-Prüfung mit %d Fehlern beendet", len(report["errors"]))
    else:
        logger.info("Speicherindex konsistent · Entitäten=%d", report["entities_checked"])
    return report


_storage_reconcile_last: dict | None = None
_storage_reconcile_thread: threading.Thread | None = None
_storage_reconcile_stop = threading.Event()
_storage_reconcile_completed = False
# Beim ersten Start (und defensiv auch nach einem manuell geleerten DB-Wert)
# muss vor dem Öffnen eines HTTP-Listeners ein nicht-leerer Token existieren.
# ZEITARCHIV_API_TOKEN ist ausschließlich der explizite Override für den
# lokalen Compose-/Virtualenv-Test; im Supervisor wird immer kryptografisch
# sicher generiert und anschließend in SQLite persistiert.
ensure_api_token(index, development_token=os.environ.get("ZEITARCHIV_API_TOKEN"))
ingestion_service = IngestionService(DATA_DIR, index, TZ, storage_coordinator)
_recovered_ingest_events = ingestion_service.recover_pending()
if _recovered_ingest_events:
    logger.info(
        "%d unvollständig abgeschlossene Zeitarchiv-Events wiederhergestellt",
        _recovered_ingest_events,
    )
_requires_synchronous_reconciliation = bool(_restore_startup_result) or not _previous_shutdown_clean
if _requires_synchronous_reconciliation:
    with storage_coordinator.exclusive():
        _run_storage_reconciliation(repair=True)
else:
    logger.info("Speicherindex-Abgleich wird nach dem Start im Hintergrund ausgeführt")
logger.info(
    "Zeitarchiv gestartet · Datenverzeichnis=%s · Loglevel=%s · HTTP-Protokoll=%s",
    DATA_DIR,
    index.get_setting("log_level", DEFAULT_LOG_LEVEL),
    index.get_setting("access_log_mode", DEFAULT_ACCESS_LOG_MODE),
)


def _current_api_token() -> str:
    """Aktueller, immer nicht-leerer Token aus der settings-Tabelle."""
    return ensure_api_token(index)


_api_state = api_routes.ApiState()
_SERVER_STARTED_AT = _api_state.server_started_at
_CONNECTION_STATS = _api_state.connection_stats
_write_capture_lock = _api_state.write_capture_lock
_write_capture = _api_state.write_capture
_entity_trace_lock = _api_state.entity_trace_lock
_entity_trace = _api_state.entity_trace
_ENTITY_TRACE_DURATION_SECONDS = 15 * 60
app.include_router(
    api_routes.create_api_router(
        api_routes.ApiDependencies(
            data_dir=DATA_DIR,
            index=index,
            tz=TZ,
            coordinator=storage_coordinator,
            ingestion=ingestion_service,
            api_token=_current_api_token,
            app_version=APP_VERSION,
        ),
        _api_state,
    )
)

_report_service = ReportService(ReportDependencies(
    data_dir=DATA_DIR,
    tz=TZ,
    coordinator=storage_coordinator,
    templates=templates,
    app_root_context=_app_root_context,
))
_reports_context = _report_service.context
app.include_router(_report_service.router())


class UploadLimitExceeded(ValueError):
    """Ein Upload überschreitet sein festgelegtes Größenlimit."""


def _format_upload_limit(max_bytes: int) -> str:
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    if max_bytes % gib == 0:
        return f"{max_bytes // gib} GiB"
    return f"{max_bytes // mib} MiB"


def _copy_upload_limited(source, destination: Path, max_bytes: int) -> int:
    """Kopiert einen Upload atomar und bricht oberhalb von max_bytes ab."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_suffix(destination.suffix + ".part")
    written = 0
    try:
        with part_path.open("wb") as handle:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise UploadLimitExceeded(
                        f"Upload ist größer als {_format_upload_limit(max_bytes)}"
                    )
                handle.write(chunk)
        part_path.replace(destination)
        return written
    finally:
        part_path.unlink(missing_ok=True)


@app.exception_handler(cleanup.ResultLimitExceeded)
async def _result_limit_handler(
    _request: Request, exc: cleanup.ResultLimitExceeded
) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


def _storage_locked(entity_ids_getter):
    return storage_locked(storage_coordinator, entity_ids_getter)


def _sparkline_paths(values: list[float], width: float = 84, height: float = 28, pad: float = 2) -> dict[str, str] | None:
    """Baut die "d"-Pfaddaten für Linie + Füllfläche einer Sparkline (Konzept
    Abschnitt 03, "Verlaufs-Sparkline") — reine Pfad-Strings statt fertigem
    HTML, damit die Template-Auto-Escaping unverändert greift und das SVG-
    Markup selbst im Template bleibt statt in Python erzeugt zu werden. None
    bei weniger als zwei Punkten (keine Linie zeichenbar) — das Template
    blendet die Sparkline dann komplett aus."""
    if len(values) < 2:
        return None
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    step = (width - 2 * pad) / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = pad + (height - 2 * pad) * (1 - (v - lo) / span)
        points.append((x, y))
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
    # Füllfläche: dieselbe Linie, dann runter zur Grundlinie und zurück zum
    # Anfang — schließt die Fläche unterhalb der Kurve, ohne die Linie selbst
    # noch einmal berechnen zu müssen.
    area = f"{line} L{points[-1][0]:.1f},{height:.1f} L{points[0][0]:.1f},{height:.1f} Z"
    return {"line": line, "area": area}


class _RetentionProgress:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.job_id: int | None = None


_retention_progress = _RetentionProgress()


def _begin_retention_job(trigger: str, scheduled_for: float | None = None) -> int | None:
    with _retention_progress.lock:
        if _retention_progress.running:
            if trigger == "scheduled":
                skipped_id = index.create_retention_job(trigger, scheduled_for)
                index.update_retention_job(
                    skipped_id,
                    status="skipped",
                    finished_at=time.time(),
                    error="Übersprungen, weil bereits ein Retention-Lauf aktiv ist",
                )
            return None
        job_id = index.create_retention_job(trigger, scheduled_for)
        _retention_progress.running = True
        _retention_progress.job_id = job_id
        return job_id


def _finish_retention_job(job_id: int, *, now: datetime | None = None) -> dict:
    index.update_retention_job(job_id, status="running", started_at=time.time())
    try:
        with storage_coordinator.exclusive():
            totals = retention_mod.enforce_retention_all(DATA_DIR, index, TZ, now=now)
        finished_at = time.time()
        index.update_retention_job(
            job_id,
            status="success",
            finished_at=finished_at,
            rows_deleted=totals["rows_deleted"],
            bytes_freed=totals["bytes_freed"],
            months_deleted=totals["months_deleted"],
            entities_affected=totals["entities_affected"],
        )
        index.set_setting("retention_enforcement_last_run", str(finished_at))
        index.set_setting("retention_last_success", str(finished_at))
        try:
            _refresh_retention_overview_if_stale(force=True)
        except Exception:
            # Die Löschung war erfolgreich; ein Fehler der rein informativen
            # Folgevorschau darf den Job nicht nachträglich als Fehler markieren.
            logger.exception("Retention-Übersicht konnte nach dem Lauf nicht aktualisiert werden")
        logger.info(
            "Retention erfolgreich · Job=%d · Zeilen=%d · Monate=%d · Speicher=%s",
            job_id,
            totals["rows_deleted"],
            totals["months_deleted"],
            format_size(totals["bytes_freed"]),
        )
        return {"status": "success", "totals": totals}
    except Exception as exc:
        logger.exception("Retention konnte nicht ausgeführt werden")
        finished_at = time.time()
        error = str(exc)[:2000] or exc.__class__.__name__
        index.update_retention_job(
            job_id,
            status="failed",
            finished_at=finished_at,
            error=error,
        )
        index.set_setting("retention_last_failure", str(finished_at))
        return {"status": "failed", "error": error}
    finally:
        with _retention_progress.lock:
            _retention_progress.running = False
            _retention_progress.job_id = None


def _run_retention_background(*, scheduled_for: float | None = None) -> bool:
    job_id = _begin_retention_job("scheduled", scheduled_for)
    if job_id is None:
        return False
    threading.Thread(
        target=_finish_retention_job,
        args=(job_id,),
        name="zeitarchiv-retention",
        daemon=True,
    ).start()
    return True


def _set_next_retention_run(now: datetime) -> float | None:
    enabled = index.get_setting("retention_enforcement", "off") == "on"
    next_run = next_scheduled_run(
        now,
        "daily" if enabled else "off",
        index.get_setting("retention_enforcement_time", RETENTION_DEFAULT_TIME),
    )
    index.set_setting("retention_enforcement_next_run", "" if next_run is None else str(next_run.timestamp()))
    return None if next_run is None else next_run.timestamp()


def _run_retention_enforcement_if_due(now: datetime) -> None:
    """Führt genau einen fälligen täglichen Lauf aus, unabhängig von Requests."""
    if index.get_setting("retention_enforcement", "off") != "on":
        return
    raw_next = index.get_setting("retention_enforcement_next_run", "")
    try:
        next_ts = float(raw_next) if raw_next else _set_next_retention_run(now)
    except (TypeError, ValueError):
        next_ts = _set_next_retention_run(now)
    if next_ts is None or now.timestamp() < next_ts:
        return
    _run_retention_background(scheduled_for=next_ts)
    _set_next_retention_run(now)


_RETENTION_OVERVIEW_SETTING = "retention_overview_snapshot"
_RETENTION_OVERVIEW_MAX_AGE_SECONDS = 3600


def _empty_retention_overview() -> dict:
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


def _load_retention_overview() -> dict:
    raw = index.get_setting(_RETENTION_OVERVIEW_SETTING, "")
    if not raw:
        return _empty_retention_overview()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _empty_retention_overview()
    if not isinstance(value, dict) or not isinstance(value.get("groups"), list):
        return _empty_retention_overview()
    return value


def _refresh_retention_overview_if_stale(*, force: bool = False) -> dict:
    """Aktualisiert die teure Dateivorschau höchstens einmal pro Stunde."""
    current = _load_retention_overview()
    generated_at = current.get("generated_at")
    now_ts = time.time()
    if (
        not force
        and isinstance(generated_at, (int, float))
        and now_ts - generated_at < _RETENTION_OVERVIEW_MAX_AGE_SECONDS
    ):
        return current
    limited_ids = [
        entity["entity_id"]
        for entity in index.list_entities()
        if entity["retention"] != "unlimited"
    ]
    with storage_coordinator.entities(limited_ids):
        overview = retention_mod.preview_retention_overview(DATA_DIR, index, TZ)
    index.set_setting(
        _RETENTION_OVERVIEW_SETTING,
        json.dumps(overview, ensure_ascii=False, separators=(",", ":")),
    )
    logger.debug(
        "Retention-Übersicht aktualisiert · fällige Zeilen=%d · Entitäten=%d",
        overview["totals"]["rows_deleted"],
        overview["totals"]["entities_affected"],
    )
    return overview


def _invalidate_retention_overview() -> None:
    index.set_setting(_RETENTION_OVERVIEW_SETTING, "")


@app.get("/", response_class=HTMLResponse)
def entities_view(request: Request) -> HTMLResponse:
    overview = index.get_overview()
    snapshots = index.get_stats_snapshots(time.time() - 24 * 3600)
    type_counts = {row["aggregation_type"]: row["entity_count"] for row in index.get_stats_by_type()}
    type_breakdown = " · ".join(
        f"{type_counts[key]} {label}" for key, label in (("standard", "Standard"), ("counter", "Zähler"), ("switch", "Schalter")) if type_counts.get(key)
    )
    context = {
        "entity_count": overview["entity_count"],
        "type_breakdown": type_breakdown,
        "total_rows": format_int(overview['total_rows']),
        "total_size": format_size(overview["total_size_bytes"]),
        "rows_sparkline": _sparkline_paths([s["total_rows"] for s in snapshots]),
        "size_sparkline": _sparkline_paths([s["total_size_bytes"] for s in snapshots]),
        **_dashboard_tiles_context(),
    }
    return templates.TemplateResponse(request, "entities.html", context)


@app.get("/entities", response_class=HTMLResponse)
def entities_list_view(request: Request) -> HTMLResponse:
    """Eigene Seite für die Entitäten-Liste (vormals Teil der Übersichtsseite) —
    Suche/Filter/Tabelle unverändert über das bestehende /entities-table-
    Fragment, nur der umgebende Seitenrahmen ist jetzt die settings-layout-
    Familie (Sidebar wie Statistik/Import/Export/Backup)."""
    units = index.list_distinct_units()
    unit_options = [{"value": "__none__" if u is None else u, "label": "Ohne Einheit" if u is None else u} for u in units]
    return templates.TemplateResponse(
        request,
        "entities_list.html",
        {
            "unit_options": unit_options,
            "column_options": ENTITIES_OPTIONAL_COLUMNS,
            "visible_columns": _entities_visible_columns(),
        },
    )


def _settings_archivierung_context(saved: bool = False) -> dict:
    return {
        "default_resolution": index.get_setting("default_resolution", DEFAULT_RESOLUTION),
        "default_retention": index.get_setting("default_retention", DEFAULT_RETENTION),
        "resolution_options": list(RESOLUTION_LABELS.items()),
        "retention_options": list(RETENTION_LABELS.items()),
        "saved": saved,
    }


def _count_stale_entities() -> int:
    """Für die Rotation-Sektion: Anzahl Entitäten mit mindestens einer noch
    nicht rotierten Hot-Datei aus einem vergangenen Monat — reines Zählen,
    ohne tatsächlich zu rotieren (das macht erst der Button-Klick)."""
    now_ts = datetime.now(TZ).timestamp()
    entities = index.list_entities()
    with storage_coordinator.entities([entity["entity_id"] for entity in entities]):
        return sum(
            1
            for entity in entities
            if hotbuffer.find_stale_hot_files(DATA_DIR, entity["entity_id"], now_ts, TZ)
        )


def _settings_rotation_context(result: str | None = None) -> dict:
    return {"stale_count": _count_stale_entities(), "result": result}


def _settings_purge_context(result: str | None = None) -> dict:
    """Liefert die stets sichtbare, rein lesende Bereinigungsvorschau."""
    affected = index.get_deleted_points_by_entity()
    entity_ids = [row["entity_id"] for row in affected]
    with storage_coordinator.entities(entity_ids):
        preview = cleanup.preview_purge(DATA_DIR, index, TZ)
    return {"result": result, "purge_preview": preview}


def _settings_storage_index_context(report: dict | None = None) -> dict:
    report = report if report is not None else _storage_reconcile_last
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
                f"{format_timestamp(checked_at, TZ)} {format_time(checked_at, TZ)}"
                if checked_at else "—"
            ),
        }
    }


def _settings_retention_context(result: str | None = None) -> dict:
    limited_count = sum(1 for entity in index.list_entities() if entity["retention"] != "unlimited")
    enabled = index.get_setting("retention_enforcement", "off") == "on"
    next_raw = index.get_setting("retention_enforcement_next_run", "")
    try:
        next_ts = float(next_raw) if next_raw else None
    except ValueError:
        next_ts = None
    if enabled and next_ts is None:
        next_ts = _set_next_retention_run(datetime.now(TZ))

    retention_overview = _load_retention_overview()
    retention_groups = {
        row["retention"]: row for row in retention_overview.get("groups", [])
        if isinstance(row, dict) and row.get("retention")
    }
    by_retention = []
    for row in index.get_stats_by_retention():
        due = retention_groups.get(row["retention"], {})
        rows_due = int(due.get("rows_due", 0) or 0)
        months_due = int(due.get("months_due", 0) or 0)
        next_expiration_ts = due.get("next_expiration_ts")
        if rows_due or months_due:
            next_expiration = "Jetzt fällig"
        elif isinstance(next_expiration_ts, (int, float)):
            next_expiration = (
                f"{format_timestamp(next_expiration_ts, TZ)} "
                f"{format_time(next_expiration_ts, TZ)}"
            )
        else:
            next_expiration = "—"
        by_retention.append({
            "label": format_retention(row["retention"]),
            "entity_count": row["entity_count"],
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
        return f"{format_timestamp(ts, TZ)} {format_time(ts, TZ)}" if ts else "—"

    status_labels = {
        "queued": "Geplant", "running": "Läuft", "success": "Erfolgreich",
        "failed": "Fehlgeschlagen", "interrupted": "Abgebrochen", "skipped": "Übersprungen",
    }
    jobs = []
    for job in index.list_retention_jobs(8):
        jobs.append({
            "created_at": f"{format_timestamp(job['created_at'], TZ)} {format_time(job['created_at'], TZ)}",
            "trigger": "Zeitplan" if job["trigger"] == "scheduled" else "Manuell",
            "status": status_labels.get(job["status"], job["status"]),
            "status_key": job["status"],
            "rows_deleted": job["rows_deleted"] if job["rows_deleted"] is not None else "—",
            "months_deleted": job["months_deleted"] if job["months_deleted"] is not None else "—",
            "entities_affected": job["entities_affected"] if job["entities_affected"] is not None else "—",
            "bytes_freed": format_size(job["bytes_freed"] or 0) if job["bytes_freed"] else "—",
            "error": job["error"],
        })
    with _retention_progress.lock:
        running = _retention_progress.running
    last_success_raw = index.get_setting("retention_last_success", "")
    return {
        "retention_enforcement_enabled": enabled,
        "retention_enforcement_time": index.get_setting("retention_enforcement_time", RETENTION_DEFAULT_TIME),
        "retention_timezone": str(TZ),
        "retention_next_run": display_ts(str(next_ts) if next_ts is not None else None),
        "retention_last_success": display_ts(last_success_raw),
        "retention_last_failure": display_ts(index.get_setting("retention_last_failure", "")),
        "last_run": display_ts(last_success_raw) if last_success_raw else None,
        "retention_jobs": jobs,
        "retention_running": running,
        "limited_retention_count": limited_count,
        "by_retention": by_retention,
        "retention_preview_generated_at": (
            f"{format_timestamp(retention_overview['generated_at'], TZ)} "
            f"{format_time(retention_overview['generated_at'], TZ)}"
            if isinstance(retention_overview.get("generated_at"), (int, float))
            else "Wird berechnet …"
        ),
        "result": result,
    }


def _settings_darstellung_context(saved: bool = False) -> dict:
    color_scheme = index.get_setting("color_scheme", "zeitarchiv")
    if color_scheme not in COLOR_SCHEME_LABELS:
        color_scheme = "zeitarchiv"
    color_mode = index.get_setting("color_mode", "auto")
    if color_mode not in COLOR_MODE_LABELS:
        color_mode = "auto"
    dashboard_animation = index.get_setting("dashboard_animation", "1")
    if dashboard_animation not in DASHBOARD_ANIMATION_LABELS:
        dashboard_animation = "1"
    return {
        "font_scale": index.get_setting("font_scale", "1"),
        "font_scale_options": list(FONT_SCALE_LABELS.items()),
        "font_scale_values": FONT_SCALE,
        "color_scheme": color_scheme,
        "color_scheme_options": list(COLOR_SCHEME_LABELS.items()),
        "color_mode": color_mode,
        "color_mode_options": list(COLOR_MODE_LABELS.items()),
        "dashboard_animation": dashboard_animation,
        "dashboard_animation_options": list(DASHBOARD_ANIMATION_LABELS.items()),
        "saved": saved,
    }


def _settings_verbindung_context(saved: bool = False) -> dict:
    last_write_ts = index.get_last_write_ts()
    last_auth_failure_ts = _CONNECTION_STATS["last_auth_failure_ts"]
    return {
        "api_token": _current_api_token(),
        "token_saved": saved,
        "last_write_at": (
            f"{format_timestamp(last_write_ts, TZ)} {format_time(last_write_ts, TZ)}"
            if last_write_ts is not None
            else None
        ),
        "write_requests_ok": _CONNECTION_STATS["write_requests_ok"],
        "auth_failures": _CONNECTION_STATS["auth_failures"],
        "last_auth_failure_at": (
            f"{format_timestamp(last_auth_failure_ts, TZ)} {format_time(last_auth_failure_ts, TZ)}"
            if last_auth_failure_ts is not None
            else None
        ),
        "server_started_at": f"{format_timestamp(_SERVER_STARTED_AT, TZ)} {format_time(_SERVER_STARTED_AT, TZ)}",
    }


def _debug_tools_context() -> dict:
    """Zustand der beiden Debugging-Werkzeuge (Konzept "Debugging: nächsten
    Schreibvorgang aufzeichnen" / "Entity-Trace") — eigene Funktion statt Teil
    von _settings_logging_context(), damit das per htmx per Polling
    nachgeladene Fragment (settings/logging/debug) nur diesen kleinen
    Ausschnitt neu rendert, nicht die ganze Protokollierung-Sektion."""
    with _write_capture_lock:
        capture_armed = _write_capture["armed"]
        captured_at = _write_capture["captured_at"]
        payload = _write_capture["payload"]
    with _entity_trace_lock:
        trace_entity_id = _entity_trace["entity_id"]
        trace_expires_at = _entity_trace["expires_at"]

    now = time.time()
    trace_active = bool(trace_entity_id) and (trace_expires_at or 0) > now
    return {
        "capture_armed": capture_armed,
        "capture_captured_at": (
            f"{format_timestamp(captured_at, TZ)} {format_time(captured_at, TZ)}" if captured_at else None
        ),
        "capture_event_count": len(payload["events"]) if payload else None,
        "capture_payload_json": json.dumps(payload, indent=2, ensure_ascii=False) if payload else None,
        "trace_entity_id": trace_entity_id if trace_active else None,
        "trace_expires_in_minutes": (
            max(1, round((trace_expires_at - now) / 60)) if trace_active else None
        ),
    }


def _settings_logging_context(saved: bool = False) -> dict:
    return {
        "log_level": index.get_setting("log_level", DEFAULT_LOG_LEVEL),
        "log_level_options": list(LOG_LEVEL_LABELS.items()),
        "access_log_mode": index.get_setting("access_log_mode", DEFAULT_ACCESS_LOG_MODE),
        "access_log_options": list(ACCESS_LOG_LABELS.items()),
        "logging_saved": saved,
        **_debug_tools_context(),
    }


@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "app_version": APP_VERSION,
            "timezone": str(TZ),
            "data_dir": str(DATA_DIR),
            **_settings_verbindung_context(),
            **_settings_darstellung_context(),
            **_settings_archivierung_context(),
            **_settings_rotation_context(),
            **_settings_storage_index_context(),
            **_settings_purge_context(),
            **_settings_retention_context(),
            **_settings_logging_context(),
        },
    )


@app.get("/settings/ram", response_class=HTMLResponse)
def settings_ram(request: Request) -> HTMLResponse:
    # Per htmx nachgeladen statt Teil von settings_view() (Supervisor-Aufruf soll Seitenaufbau nicht blockieren).
    return templates.TemplateResponse(request, "_settings_ram.html", {"ram_text": supervisor_stats.describe_memory_usage()})


@app.get("/backup", response_class=HTMLResponse)
def backup_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "backup.html", _backup_context())


@app.get("/logs", response_class=HTMLResponse)
def logs_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "log_filter_options": [("all", "Alle"), *LOG_LEVEL_LABELS.items()],
        },
    )


def _validate_log_request(level: str, search: str, limit: int) -> tuple[str, str, int]:
    if level != "all" and level not in LOG_LEVEL_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Logfilter")
    return level, search[:200], max(50, min(limit, 5_000))


@app.get("/api/logs")
async def api_logs(
    level: str = "all",
    search: str = "",
    limit: int = Query(default=500, ge=50, le=2_000),
) -> dict:
    level, search, limit = _validate_log_request(level, search, limit)
    result = await run_in_threadpool(
        lambda: load_log_lines(level=level, search=search, limit=limit)
    )
    return {
        **result,
        "count": len(result["lines"]),
        "generated_at": time.time(),
    }


@app.get("/logs/download", response_class=PlainTextResponse)
async def logs_download(level: str = "all", search: str = "") -> PlainTextResponse:
    level, search, _ = _validate_log_request(level, search, 5_000)
    result = await run_in_threadpool(
        lambda: load_log_lines(level=level, search=search, limit=5_000)
    )
    content = "\n".join(result["lines"])
    if content:
        content += "\n"
    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": (
                "attachment; filename=\"zeitarchiv-protokoll-"
                f"{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.log\""
            )
        },
    )


@app.post("/settings/token/generate", response_class=HTMLResponse)
def settings_token_generate(request: Request) -> HTMLResponse:
    """Erzeugt einen neuen, zufälligen Token und ersetzt einen evtl. vorhandenen
    — GUI ist jetzt die alleinige Quelle der Wahrheit dafür (siehe
    _current_api_token()), die HA-Add-on-Konfiguration wird dadurch nicht
    mehr angefasst/gebraucht. token_urlsafe(32) statt z. B. uuid4: liefert
    ein für Bearer-Header unproblematisches, ausreichend langes Zufallstoken
    ohne Sonderzeichen."""
    index.set_setting("api_token", generate_api_token())
    return templates.TemplateResponse(
        request, "_settings_verbindung_form.html", _settings_verbindung_context(saved=True)
    )

@app.post("/settings/darstellung", response_class=HTMLResponse)
async def settings_darstellung(request: Request) -> HTMLResponse:
    """Globale Schriftgröße (Einstellungen, Bereich "Darstellung") — wirkt über
    den _font_scale_context-Kontextprozessor auf jede Seite, siehe FONT_SCALE oben."""
    form = await request.form()
    font_scale = form.get("font_scale")
    color_scheme = form.get("color_scheme")
    color_mode = form.get("color_mode")
    if font_scale is not None and font_scale not in FONT_SCALE_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Schriftgröße")
    if font_scale is not None:
        index.set_setting("font_scale", str(font_scale))
    if color_scheme is not None and color_scheme not in COLOR_SCHEME_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiges Farbschema")
    if color_scheme is not None:
        index.set_setting("color_scheme", str(color_scheme))
    if color_mode is not None and color_mode not in COLOR_MODE_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Darstellungsmodus")
    if color_mode is not None:
        index.set_setting("color_mode", str(color_mode))
    dashboard_animation = form.get("dashboard_animation")
    if dashboard_animation is not None and dashboard_animation not in DASHBOARD_ANIMATION_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Dashboard-Animation")
    if dashboard_animation is not None:
        index.set_setting("dashboard_animation", str(dashboard_animation))
    return templates.TemplateResponse(
        request, "_settings_darstellung_form.html", _settings_darstellung_context(saved=True)
    )


@app.post("/settings/logging", response_class=HTMLResponse)
async def settings_logging(request: Request) -> HTMLResponse:
    """Speichert und aktiviert Protokollstufen ohne App-Neustart."""
    form = await request.form()
    level = str(form.get("log_level", DEFAULT_LOG_LEVEL))
    access_mode = str(form.get("access_log_mode", DEFAULT_ACCESS_LOG_MODE))
    if level not in LOG_LEVEL_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiges Loglevel")
    if access_mode not in ACCESS_LOG_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiges HTTP-Protokoll")
    index.set_setting("log_level", level)
    index.set_setting("access_log_mode", access_mode)
    configure_logging(level, access_mode)
    logger.info("Protokollierung geändert · Loglevel=%s · HTTP-Protokoll=%s", level, access_mode)
    return templates.TemplateResponse(
        request, "_settings_logging_form.html", _settings_logging_context(saved=True)
    )


@app.get("/settings/logging/debug", response_class=HTMLResponse)
def settings_logging_debug(request: Request) -> HTMLResponse:
    """Nur der Debug-Werkzeuge-Ausschnitt — per htmx-Polling nachgeladen,
    solange eine Aufzeichnung scharf ist oder ein Trace läuft (siehe
    _debug_tools_context())."""
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/logging/capture-write/arm", response_class=HTMLResponse)
def settings_capture_write_arm(request: Request) -> HTMLResponse:
    """Zeichnet GENAU den nächsten eingehenden /api/write-Request auf (Rohdaten
    inkl. Werten/Entity-IDs, aber ohne Authorization-Header) — kein Dauer-
    Logging, siehe Kommentar bei _write_capture oben."""
    with _write_capture_lock:
        _write_capture["armed"] = True
        _write_capture["captured_at"] = None
        _write_capture["payload"] = None
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/logging/capture-write/clear", response_class=HTMLResponse)
def settings_capture_write_clear(request: Request) -> HTMLResponse:
    with _write_capture_lock:
        _write_capture["armed"] = False
        _write_capture["captured_at"] = None
        _write_capture["payload"] = None
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.get("/settings/logging/capture-write/download")
def settings_capture_write_download() -> Response:
    with _write_capture_lock:
        payload = _write_capture["payload"]
        captured_at = _write_capture["captured_at"]
    if payload is None:
        raise HTTPException(status_code=404, detail="Keine Aufzeichnung vorhanden")
    filename = f"zeitarchiv-write-capture-{datetime.fromtimestamp(captured_at, TZ).strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/settings/logging/trace/start", response_class=HTMLResponse)
async def settings_trace_start(request: Request) -> HTMLResponse:
    """Startet ein zeitlich begrenztes Trace einer einzelnen Entität (Konzept
    "Debugging: Entity-Trace") — protokolliert deren Rohwerte über
    zeitarchiv.trace, unabhängig vom allgemeinen Loglevel, für
    _ENTITY_TRACE_DURATION_SECONDS, dann automatisch wieder aus."""
    form = await request.form()
    entity_id = str(form.get("entity_id", "")).strip()
    if not entity_id:
        raise HTTPException(status_code=400, detail="Entity-ID fehlt")
    try:
        validate_entity_id(entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with _entity_trace_lock:
        _entity_trace["entity_id"] = entity_id
        _entity_trace["started_at"] = time.time()
        _entity_trace["expires_at"] = time.time() + _ENTITY_TRACE_DURATION_SECONDS
    trace_logger.debug("Trace gestartet für %s · %d Minuten", entity_id, _ENTITY_TRACE_DURATION_SECONDS // 60)
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/logging/trace/stop", response_class=HTMLResponse)
def settings_trace_stop(request: Request) -> HTMLResponse:
    with _entity_trace_lock:
        entity_id = _entity_trace["entity_id"]
        _entity_trace["entity_id"] = None
        _entity_trace["started_at"] = None
        _entity_trace["expires_at"] = None
    if entity_id:
        trace_logger.debug("Trace beendet für %s", entity_id)
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/archivierung", response_class=HTMLResponse)
async def settings_archivierung(request: Request) -> HTMLResponse:
    """Speichert die globalen Auflösungs-/Aufbewahrungs-Standardwerte für neu
    erkannte Entitäten (Einstellungen-Bereich, Konzept Abschnitt 03) — wirkt
    nur auf Entitäten, die AB JETZT zum ersten Mal einen Wert senden; bereits
    archivierte Entitäten behalten ihre individuelle Einstellung aus der
    jeweiligen Konfigurationsseite unverändert (Index.get_or_create_entity()
    greift nur beim Neuanlegen auf diese Standardwerte zu)."""
    form = await request.form()
    resolution = form.get("default_resolution")
    retention = form.get("default_retention")
    if resolution is not None and resolution not in RESOLUTION_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Auflösung")
    if retention is not None and retention not in RETENTION_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Aufbewahrung")
    if resolution is not None:
        index.set_setting("default_resolution", str(resolution))
    if retention is not None:
        index.set_setting("default_retention", str(retention))
    return templates.TemplateResponse(
        request, "_settings_archivierung_form.html", _settings_archivierung_context(saved=True)
    )


@app.post("/settings/rotation", response_class=HTMLResponse)
def settings_rotation(request: Request) -> HTMLResponse:
    """Manueller Rotations-Anstoß (Konzept "Offene Punkte": Rotation läuft sonst
    nur lazy beim nächsten Schreibvorgang einer Entität — eine Entität, die
    komplett aufhört zu senden, würde ihre letzte Hot-Datei sonst nie von
    selbst archivieren)."""
    with storage_coordinator.exclusive():
        rotated = rotate.rotate_all_stale(DATA_DIR, index, TZ)
    if rotated == 0:
        result = "Nichts zu tun — alle Entitäten sind bereits aktuell rotiert."
    else:
        result = f"{rotated} Monatsdatei{'en' if rotated != 1 else ''} archiviert."
    logger.info("Manuelle Rotation abgeschlossen · Monatsdateien=%d", rotated)
    return templates.TemplateResponse(
        request, "_settings_rotation_form.html", _settings_rotation_context(result=result)
    )


@app.post("/settings/storage-index/check", response_class=HTMLResponse)
def settings_storage_index_check(request: Request) -> HTMLResponse:
    """Erstellt eine rein lesende Vorschau möglicher Indexabweichungen."""
    with storage_coordinator.exclusive():
        report = _run_storage_reconciliation(repair=False)
    return templates.TemplateResponse(
        request, "_settings_storage_index_form.html", _settings_storage_index_context(report)
    )


@app.post("/settings/storage-index/repair", response_class=HTMLResponse)
def settings_storage_index_repair(request: Request) -> HTMLResponse:
    """Prüft erneut und ersetzt nur abgeleitete Metadaten atomar."""
    with storage_coordinator.exclusive():
        report = _run_storage_reconciliation(repair=True)
    return templates.TemplateResponse(
        request, "_settings_storage_index_form.html", _settings_storage_index_context(report)
    )


@app.post("/settings/purge", response_class=HTMLResponse)
def settings_purge(request: Request) -> HTMLResponse:
    """Manueller Anstoß, der zur Löschung markierte Datensätze überall
    physisch entfernt — sowohl im laufenden Monat (Hot Buffer, purge_hot_buffer())
    als auch in bereits archivierten Monaten (Parquet-Rewrite + Rollup-
    Neuberechnung, purge_archived_months()). Konzept "Offene Punkte"."""
    with storage_coordinator.exclusive():
        hot_purged = cleanup.purge_hot_buffer(DATA_DIR, index, TZ)
        archive_result = cleanup.purge_archived_months(DATA_DIR, index, TZ)
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
    logger.info("Manuelle Bereinigung abgeschlossen · Zeilen=%d · Monate=%d", total_rows, months)
    return templates.TemplateResponse(
        request, "_settings_purge_form.html", _settings_purge_context(result=result)
    )


@app.get("/settings/purge/marked", response_class=HTMLResponse)
def settings_purge_marked(
    request: Request,
    search: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    """On-demand-Detailansicht der einzelnen Soft-Delete-Markierungen."""
    result = index.list_deleted_points(search=search, page=page, page_size=50)
    rows = [
        {
            **row,
            "measured_at": datetime.fromtimestamp(row["ts"], TZ).strftime("%d.%m.%Y %H:%M:%S"),
            "marked_at": datetime.fromtimestamp(row["deleted_at"], TZ).strftime("%d.%m.%Y %H:%M:%S"),
        }
        for row in result["rows"]
    ]
    return templates.TemplateResponse(
        request,
        "_settings_marked_points.html",
        {"rows": rows, "pagination": result["pagination"]},
    )


@app.post("/settings/retention-enforcement", response_class=HTMLResponse)
async def settings_retention_enforcement_toggle(request: Request) -> HTMLResponse:
    """Schaltet die automatische, tägliche Anwendung der Aufbewahrungsfrist
    an/aus (Konzept "Offene Punkte": Aufbewahrung wurde bisher nur
    gespeichert, nie angewendet) — bewusst standardmäßig aus, weil das anders
    als der Purge im Bereinigungs-Werkzeug ganze, nie zuvor markierte
    Zeiträume endgültig löscht."""
    form = await request.form()
    enabled = form.get("retention_enforcement")
    schedule_time = str(form.get("retention_enforcement_time", RETENTION_DEFAULT_TIME))
    if enabled not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Ungültiger Wert")
    try:
        parse_schedule_time(schedule_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültige Uhrzeit") from exc
    index.set_setting("retention_enforcement", enabled)
    index.set_setting("retention_enforcement_time", schedule_time)
    _set_next_retention_run(datetime.now(TZ))
    return templates.TemplateResponse(
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


@app.post("/settings/retention-enforcement/preview", response_class=HTMLResponse)
def settings_retention_enforcement_preview(request: Request) -> HTMLResponse:
    overview = _refresh_retention_overview_if_stale(force=True)
    totals = overview["totals"]
    return templates.TemplateResponse(
        request,
        "_settings_retention_form.html",
        _settings_retention_context(result=_retention_result_text(totals, preview=True)),
    )


@app.post("/settings/retention-enforcement/run", response_class=HTMLResponse)
def settings_retention_enforcement_run(request: Request) -> HTMLResponse:
    """Manueller Anstoß, unabhängig vom Automatik-Schalter — läuft sofort,
    unabhängig davon ob/wann der tägliche Automatik-Lauf zuletzt lief."""
    job_id = _begin_retention_job("manual")
    if job_id is None:
        result = "Retention läuft bereits — es wurde kein zweiter Lauf gestartet."
    else:
        outcome = _finish_retention_job(job_id)
        if outcome["status"] == "success":
            result = _retention_result_text(outcome["totals"])
        else:
            result = f"Retention fehlgeschlagen: {outcome['error']}"
    return templates.TemplateResponse(
        request, "_settings_retention_form.html", _settings_retention_context(result=result)
    )


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


_backup_progress = _BackupProgress()


def _run_backup_background(*, trigger: str = "manual", scheduled_for: float | None = None) -> bool:
    with _backup_progress.lock:
        if _backup_progress.running:
            if trigger == "scheduled":
                skipped_id = index.create_backup_job(trigger, scheduled_for)
                index.update_backup_job(
                    skipped_id,
                    status="skipped",
                    finished_at=time.time(),
                    error="Übersprungen, weil bereits ein Backup läuft",
                )
            return False
        job_id = index.create_backup_job(trigger, scheduled_for)
        _backup_progress.running = True
        _backup_progress.done = 0
        _backup_progress.total = backup.estimate_file_count(DATA_DIR)
        _backup_progress.job_id = job_id
        _backup_progress.error = None
    logger.info("Backup gestartet · Job=%d · Auslöser=%s", job_id, trigger)

    def on_progress(done: int, total: int) -> None:
        with _backup_progress.lock:
            _backup_progress.done = done
            _backup_progress.total = total

    def worker() -> None:
        started_at = time.time()
        index.update_backup_job(job_id, status="running", started_at=started_at)
        snapshot_dir = BACKUPS_DIR / f".backup-source-{job_id}-{secrets.token_hex(6)}"
        try:
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            backup.cleanup_stale_source_snapshots(BACKUPS_DIR)
            entity_ids = [row["entity_id"] for row in index.list_entities()]
            backup.create_source_snapshot(
                DATA_DIR,
                snapshot_dir,
                entity_ids,
                storage_coordinator,
            )
            with _backup_progress.lock:
                _backup_progress.total = backup.estimate_file_count(snapshot_dir)

            dest_path = BACKUPS_DIR / f"zeitarchiv-backup-{datetime.now(TZ).strftime('%Y-%m-%d-%H%M%S')}.zip"
            backup.create_backup(
                snapshot_dir,
                dest_path,
                on_progress=on_progress,
                consistent_sqlite=True,
                metadata={
                    "timezone": str(TZ),
                    "trigger": trigger,
                    "snapshot_mode": "entity-consistent",
                },
            )
            keep_count_raw = index.get_setting("backup_keep_count", "unlimited")
            keep_days_raw = index.get_setting("backup_keep_days", "unlimited")
            keep_count = int(keep_count_raw) if keep_count_raw != "unlimited" else None
            keep_days = retention_mod.RETENTION_DAYS.get(keep_days_raw)
            cleanup_error = None
            try:
                backup.prune_backups(BACKUPS_DIR, keep_count, keep_days, time.time())
            except OSError as exc:
                cleanup_error = f"Backup gültig; alte Sicherungen konnten nicht bereinigt werden: {exc}"[:2000]
                logger.exception("Alte Backups konnten nicht bereinigt werden")
            finished_at = time.time()
            index.update_backup_job(
                job_id,
                status="success",
                finished_at=finished_at,
                filename=dest_path.name,
                size_bytes=dest_path.stat().st_size,
                error=cleanup_error,
            )
            index.set_setting("backup_last_success", str(finished_at))
            logger.info(
                "Backup erfolgreich · Job=%d · Datei=%s · Größe=%s · Dauer=%.1f s",
                job_id,
                dest_path.name,
                format_size(dest_path.stat().st_size),
                max(0.0, finished_at - started_at),
            )
        except Exception as exc:
            logger.exception("Backup konnte nicht erstellt werden")
            finished_at = time.time()
            error = str(exc)[:2000] or exc.__class__.__name__
            index.update_backup_job(
                job_id,
                status="failed",
                finished_at=finished_at,
                error=error,
            )
            index.set_setting("backup_last_failure", str(finished_at))
            with _backup_progress.lock:
                _backup_progress.error = error
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            with _backup_progress.lock:
                _backup_progress.running = False
                _backup_progress.job_id = None

    threading.Thread(target=worker, daemon=True).start()
    return True


def _set_next_backup_run(now: datetime) -> float | None:
    schedule = index.get_setting("backup_schedule", "off")
    time_value = index.get_setting("backup_schedule_time", BACKUP_DEFAULT_TIME)
    weekday = int(index.get_setting("backup_schedule_weekday", str(BACKUP_DEFAULT_WEEKDAY)))
    next_run = next_scheduled_run(now, schedule, time_value, weekday)
    index.set_setting("backup_schedule_next_run", "" if next_run is None else str(next_run.timestamp()))
    return None if next_run is None else next_run.timestamp()


def _run_backup_schedule_if_due(now: datetime) -> None:
    """Startet höchstens einen verpassten Termin und plant sofort den nächsten."""
    schedule = index.get_setting("backup_schedule", "off")
    if schedule not in {"daily", "weekly"}:
        return
    raw_next = index.get_setting("backup_schedule_next_run", "")
    try:
        next_ts = float(raw_next) if raw_next else _set_next_backup_run(now)
    except (TypeError, ValueError):
        next_ts = _set_next_backup_run(now)
    if next_ts is None or now.timestamp() < next_ts:
        return
    _run_backup_background(trigger="scheduled", scheduled_for=next_ts)
    _set_next_backup_run(now)


_maintenance_scheduler_stop = threading.Event()
_maintenance_scheduler_thread: threading.Thread | None = None


def _background_storage_reconciliation() -> None:
    """Prüft einen normalen, sauber beendeten Bestand entitätsweise.

    Dadurch ist der HTTP-Listener sofort verfügbar und ein großer Bestand hält
    nie sämtliche Entitäten gleichzeitig an. Nach Restore/Crash wird dieser
    Pfad bewusst nicht verwendet; dort lief der Abgleich bereits synchron.
    """
    global _storage_reconcile_last, _storage_reconcile_completed
    started_at = time.time()
    reports: list[dict] = []
    entities = [entity["entity_id"] for entity in index.list_entities()]
    for entity_id in entities:
        if _storage_reconcile_stop.is_set():
            return
        with storage_coordinator.entity(entity_id):
            reports.append(
                reconcile.audit_storage_metadata(
                    DATA_DIR, index, TZ, entity_ids=[entity_id], repair=True
                )
            )
    _storage_reconcile_last = {
        "checked_at": time.time(),
        "started_at": started_at,
        "entities_checked": sum(report["entities_checked"] for report in reports),
        "mismatches": [item for report in reports for item in report["mismatches"]],
        "errors": [item for report in reports for item in report["errors"]],
        "repaired": any(report["repaired"] for report in reports),
        "background": True,
    }
    _storage_reconcile_completed = True
    logger.info(
        "Speicherindex-Hintergrundabgleich beendet · Entitäten=%d · Abweichungen=%d · Fehler=%d",
        _storage_reconcile_last["entities_checked"],
        len(_storage_reconcile_last["mismatches"]),
        len(_storage_reconcile_last["errors"]),
    )


def _maintenance_scheduler_loop() -> None:
    """Prüft interne Zeitpläne und schreibt Statistikpunkte ohne UI-Aufruf."""
    while not _maintenance_scheduler_stop.is_set():
        try:
            if index.record_stats_snapshot_if_stale():
                logger.debug("Stündlicher Statistik-Schnappschuss gespeichert")
            supervisor_stats.maybe_record_memory_snapshot(index)
            _refresh_retention_overview_if_stale()
            _run_backup_schedule_if_due(datetime.now(TZ))
            _run_retention_enforcement_if_due(datetime.now(TZ))
        except Exception:
            logger.exception("Wartungsplaner konnte den nächsten Lauf nicht prüfen")
        _maintenance_scheduler_stop.wait(30)


@app.on_event("startup")
def _start_maintenance_scheduler() -> None:
    global _maintenance_scheduler_thread, _storage_reconcile_thread
    if not _requires_synchronous_reconciliation and (
        _storage_reconcile_thread is None or not _storage_reconcile_thread.is_alive()
    ):
        _storage_reconcile_stop.clear()
        _storage_reconcile_thread = threading.Thread(
            target=_background_storage_reconciliation,
            name="zeitarchiv-storage-reconcile",
            daemon=True,
        )
        _storage_reconcile_thread.start()
    if _maintenance_scheduler_thread is not None and _maintenance_scheduler_thread.is_alive():
        return
    _maintenance_scheduler_stop.clear()
    _maintenance_scheduler_thread = threading.Thread(
        target=_maintenance_scheduler_loop,
        name="zeitarchiv-maintenance-scheduler",
        daemon=True,
    )
    _maintenance_scheduler_thread.start()


@app.on_event("shutdown")
def _stop_maintenance_scheduler() -> None:
    _maintenance_scheduler_stop.set()
    if _maintenance_scheduler_thread is not None:
        _maintenance_scheduler_thread.join(timeout=5)
    _storage_reconcile_stop.set()
    if _storage_reconcile_thread is not None:
        _storage_reconcile_thread.join(timeout=5)
    # Ein abgebrochener Hintergrundabgleich gilt vorsichtshalber nicht als
    # sauberer Shutdown; dann wird beim nächsten Start synchron geprüft.
    if _requires_synchronous_reconciliation or _storage_reconcile_completed:
        index.set_setting("storage_clean_shutdown", "1")


_BACKUP_SORT_COLUMNS = [("created_at", "Erstellt"), ("size_bytes", "Größe")]


def _backup_list_context(sort: str = "created_at", direction: str = "desc", page: int = 1, page_size: int = 10) -> dict:
    """Sortierte/paginierte Backup-Liste (Konzept-Erweiterung: sortierbare
    Spalten + Paging, analog zur Entitäten-Übersicht/_entities_table_response)
    — eigene Funktion statt Teil von _backup_context(), damit das per htmx
    nachladbare Tabellen-Fragment (_backup_table.html, Route /backup/list)
    dieselbe Sortier-/Paging-Logik nutzt, ohne den kompletten Backup-Status-
    Kontext (Fortschritt, Jobs, Rollbacks, Warnungen) mit aufzubauen."""
    if sort not in dict(_BACKUP_SORT_COLUMNS):
        sort = "created_at"
    if direction not in ("asc", "desc"):
        direction = "desc"
    raw = backup.list_backups(BACKUPS_DIR)
    raw.sort(key=lambda b: b[sort], reverse=(direction == "desc"))
    page_raw, pagination = _paginate(raw, page, page_size)
    backups = [
        {
            "filename": b["filename"],
            "size": format_size(b["size_bytes"]),
            "created_at": f"{format_timestamp(b['created_at'], TZ)} {format_time(b['created_at'], TZ)}",
        }
        for b in page_raw
    ]

    def _next_dir(column: str) -> str:
        return "asc" if sort == column and direction == "desc" else "desc"

    columns = [
        {
            "key": key,
            "label": label,
            "next_dir": _next_dir(key),
            "active": sort == key,
            "arrow": ("↓" if direction == "desc" else "↑") if sort == key else "",
        }
        for key, label in _BACKUP_SORT_COLUMNS
    ]
    return {
        "backups": backups,
        "backup_columns": columns,
        "backup_sort": sort,
        "backup_dir": direction,
        "backup_pagination": pagination,
        "backup_total": pagination["total"],
    }


def _backup_context(
    *, message: str | None = None,
    sort: str = "created_at", direction: str = "desc", page: int = 1, page_size: int = 10,
) -> dict:
    with _backup_progress.lock:
        running = _backup_progress.running
        done = _backup_progress.done
        total = _backup_progress.total
        current_error = _backup_progress.error
    percent = int(done / total * 100) if total else 0
    jobs = []
    status_labels = {
        "queued": "Geplant", "running": "Läuft", "success": "Erfolgreich",
        "failed": "Fehlgeschlagen", "interrupted": "Abgebrochen", "skipped": "Übersprungen",
    }
    for job in index.list_backup_jobs(10):
        jobs.append({
            "trigger": "Zeitplan" if job["trigger"] == "scheduled" else "Manuell",
            "status": status_labels.get(job["status"], job["status"]),
            "status_key": job["status"],
            "created_at": f"{format_timestamp(job['created_at'], TZ)} {format_time(job['created_at'], TZ)}",
            "duration": (
                f"{max(0, round(job['finished_at'] - job['started_at']))} s"
                if job["started_at"] is not None and job["finished_at"] is not None else "—"
            ),
            "size": format_size(job["size_bytes"] or 0) if job["size_bytes"] else "—",
            "error": job["error"],
        })

    next_raw = index.get_setting("backup_schedule_next_run", "")
    try:
        next_ts = float(next_raw) if next_raw else None
    except ValueError:
        next_ts = None
    if next_ts is None and index.get_setting("backup_schedule", "off") != "off":
        next_ts = _set_next_backup_run(datetime.now(TZ))

    def display_ts(raw: str | None) -> str:
        try:
            ts = float(raw) if raw else None
        except ValueError:
            ts = None
        return f"{format_timestamp(ts, TZ)} {format_time(ts, TZ)}" if ts else "—"

    warnings = []
    source_size = backup.estimate_size_bytes(DATA_DIR)
    try:
        free_bytes = shutil.disk_usage(DATA_DIR).free
        if source_size and free_bytes < source_size * 2:
            warnings.append(
                f"Wenig freier Speicher: Für ein Backup werden ungefähr {format_size(source_size * 2)} frei empfohlen."
            )
    except OSError:
        pass
    schedule_value = index.get_setting("backup_schedule", "off")
    last_success_raw = index.get_setting("backup_last_success", "")
    try:
        last_success_ts = float(last_success_raw) if last_success_raw else None
    except ValueError:
        last_success_ts = None
    stale_after = {"daily": 2 * 86400, "weekly": 14 * 86400}.get(schedule_value)
    if stale_after and last_success_ts and time.time() - last_success_ts > stale_after:
        warnings.append("Das letzte erfolgreiche Backup ist älter als zwei Sicherungsintervalle.")

    if message is None and _restore_startup_result:
        if _restore_startup_result.get("success"):
            message = (
                f"Backup {_restore_startup_result['filename']} wurde wiederhergestellt. "
                f"Der vorherige Stand liegt in {_restore_startup_result['rollback']}."
            )
        else:
            message = f"Wiederherstellung fehlgeschlagen: {_restore_startup_result.get('error', 'Unbekannter Fehler')}"
    return {
        "running": running,
        "done": done,
        "total": total,
        "percent": percent,
        "backup_message": message,
        "backup_error": current_error,
        "backup_warnings": warnings,
        **_backup_list_context(sort, direction, page, page_size),
        "backup_jobs": jobs,
        "backup_rollbacks": backup.list_restore_rollbacks(DATA_DIR),
        "backup_schedule": schedule_value,
        "backup_schedule_options": list(BACKUP_SCHEDULE_LABELS.items()),
        "backup_schedule_time": index.get_setting("backup_schedule_time", BACKUP_DEFAULT_TIME),
        "backup_schedule_weekday": int(index.get_setting("backup_schedule_weekday", str(BACKUP_DEFAULT_WEEKDAY))),
        "backup_weekday_options": BACKUP_WEEKDAY_OPTIONS,
        "backup_timezone": str(TZ),
        "backup_next_run": display_ts(str(next_ts) if next_ts is not None else None),
        "backup_last_success": display_ts(last_success_raw),
        "backup_last_failure": display_ts(index.get_setting("backup_last_failure", "")),
        "backup_keep_count": index.get_setting("backup_keep_count", "unlimited"),
        "backup_keep_count_options": list(BACKUP_KEEP_COUNT_LABELS.items()),
        "backup_keep_days": index.get_setting("backup_keep_days", "unlimited"),
        "backup_keep_days_options": list(RETENTION_LABELS.items()),
    }


@app.post("/backup/start", response_class=HTMLResponse)
def backup_start(request: Request) -> HTMLResponse:
    """Startet das Erstellen eines Backup-ZIPs im Hintergrund (Konzept
    "Backups" — zusätzlich zu, nicht statt der automatischen Supervisor-
    Snapshots von /data). Ein neuer Lauf ersetzt ein vorheriges Backup erst,
    wenn er selbst fertig ist (create_backup schreibt atomar über eine
    .part-Datei) — ein fehlgeschlagener/abgebrochener Lauf lässt das alte
    Backup deshalb unangetastet nutzbar."""
    _run_backup_background()
    return _backup_status_response(request)


@app.get("/backup/progress", response_class=HTMLResponse)
def backup_progress(request: Request) -> HTMLResponse:
    return _backup_status_response(request)


def _backup_status_response(request: Request) -> HTMLResponse:
    """Nur der #backup-status-Ausschnitt (Fortschrittsbalken oder Button+
    Download-Link), nie das ganze _settings_backup_form.html — sonst würde
    der statische Hinweistext beim htmx-Swap (hx-target="#backup-status")
    verdoppelt, weil er außerhalb dieses Ausschnitts liegt und stehen bleibt."""
    ctx = _backup_context()
    template = "_settings_backup_progress.html" if ctx["running"] else "_settings_backup_ready.html"
    return templates.TemplateResponse(request, template, ctx)


@app.post("/backup/schedule", response_class=HTMLResponse)
async def backup_schedule_save(request: Request) -> HTMLResponse:
    """Speichert Kalenderzeitplan und berechnet dessen nächsten Termin neu."""
    form = await request.form()
    schedule = form.get("backup_schedule")
    keep_count = form.get("backup_keep_count")
    keep_days = form.get("backup_keep_days")
    schedule_time = str(form.get("backup_schedule_time", BACKUP_DEFAULT_TIME))
    weekday_raw = str(form.get("backup_schedule_weekday", BACKUP_DEFAULT_WEEKDAY))
    if schedule is not None and schedule not in BACKUP_SCHEDULE_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitplan")
    if keep_count is not None and keep_count not in BACKUP_KEEP_COUNT_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Anzahl")
    if keep_days is not None and keep_days not in RETENTION_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Aufbewahrung")
    try:
        parse_schedule_time(schedule_time)
        weekday = int(weekday_raw)
        if weekday not in range(7):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültiger Sicherungszeitpunkt") from exc
    if schedule is not None:
        index.set_setting("backup_schedule", str(schedule))
    if keep_count is not None:
        index.set_setting("backup_keep_count", str(keep_count))
    if keep_days is not None:
        index.set_setting("backup_keep_days", str(keep_days))
    index.set_setting("backup_schedule_time", schedule_time)
    index.set_setting("backup_schedule_weekday", str(weekday))
    _set_next_backup_run(datetime.now(TZ))
    return templates.TemplateResponse(
        request, "_settings_backup_schedule_form.html", _backup_context()
    )


@app.post("/backup/verify/{filename}", response_class=HTMLResponse)
def backup_verify(request: Request, filename: str) -> HTMLResponse:
    path = backup.resolve_backup_path(BACKUPS_DIR, filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")
    try:
        manifest = backup.validate_backup(path)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "_settings_backup_ready.html",
            _backup_context(message=f"Prüfung fehlgeschlagen: {exc}"),
        )
    version = manifest.get("format_version", 0)
    return templates.TemplateResponse(
        request,
        "_settings_backup_ready.html",
        _backup_context(message=f"Backup erfolgreich geprüft (Formatversion {version})."),
    )


@app.post("/backup/import", response_class=HTMLResponse)
async def backup_import(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
    """Importiert ein portables Backup erst nach vollständiger Validierung.

    Der Upload wird unter einem nicht sichtbaren temporären Namen geschrieben.
    Erst nach ZIP-, Prüfsummen- und SQLite-Prüfung erscheint er atomar in der
    Backup-Liste; ein ungültiger oder abgebrochener Upload hinterlässt nichts.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return templates.TemplateResponse(
            request,
            "_settings_backup_ready.html",
            _backup_context(message="Import fehlgeschlagen: Bitte eine ZIP-Datei auswählen."),
        )
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    staging = BACKUPS_DIR / f".backup-upload-{secrets.token_hex(12)}.zip"
    try:
        await run_in_threadpool(_copy_upload_limited, file.file, staging, MAX_ZIP_UPLOAD_BYTES)
        manifest = await run_in_threadpool(backup.validate_backup, staging)
        with storage_coordinator.exclusive():
            destination = backup.install_validated_backup(staging, BACKUPS_DIR, datetime.now(TZ))
    except UploadLimitExceeded as exc:
        logger.warning("Backup-Import abgelehnt · %s", exc)
        return templates.TemplateResponse(
            request,
            "_settings_backup_ready.html",
            _backup_context(message=f"Import fehlgeschlagen: {exc}"),
        )
    except (OSError, ValueError) as exc:
        logger.warning("Backup-Import fehlgeschlagen · %s", exc)
        return templates.TemplateResponse(
            request,
            "_settings_backup_ready.html",
            _backup_context(message=f"Import fehlgeschlagen: {exc}"),
        )
    finally:
        staging.unlink(missing_ok=True)
        await file.close()
    version = manifest.get("format_version", 0)
    logger.info(
        "Backup importiert · Datei=%s · Formatversion=%s · Größe=%s",
        destination.name,
        version,
        format_size(destination.stat().st_size),
    )
    return templates.TemplateResponse(
        request,
        "_settings_backup_ready.html",
        _backup_context(
            message=f"Backup importiert und erfolgreich geprüft (Formatversion {version}, {destination.name})."
        ),
    )


@app.post("/backup/restore/{filename}", response_class=HTMLResponse)
def backup_restore_prepare(request: Request, filename: str) -> HTMLResponse:
    """Validiert ein Backup und merkt den atomaren Restore für den Neustart vor."""
    try:
        backup.prepare_restore(DATA_DIR, BACKUPS_DIR, filename)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "_settings_backup_ready.html",
            _backup_context(message=f"Wiederherstellung nicht vorbereitet: {exc}"),
        )
    return templates.TemplateResponse(
        request,
        "_settings_backup_ready.html",
        _backup_context(
            message="Wiederherstellung vorbereitet. Bitte das Zeitarchiv-Add-on neu starten; "
                    "vor dem Öffnen der Datenbank wird das Backup eingespielt und der aktuelle Stand als Rollback behalten."
        ),
    )


@app.post("/backup/rollback/delete/{name}", response_class=HTMLResponse)
def backup_rollback_delete(request: Request, name: str) -> HTMLResponse:
    with storage_coordinator.exclusive():
        deleted = backup.delete_restore_rollback(DATA_DIR, name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rollback nicht gefunden")
    return templates.TemplateResponse(
        request,
        "_settings_backup_ready.html",
        _backup_context(message="Rollback-Daten wurden gelöscht."),
    )


@app.get("/backup/download/{filename}")
def backup_download(filename: str) -> StreamingResponse:
    path = backup.resolve_backup_path(BACKUPS_DIR, filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")

    def generate():
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        generate(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/backup/delete/{filename}", response_class=HTMLResponse)
def backup_delete(request: Request, filename: str) -> HTMLResponse:
    """Löscht ein einzelnes manuelles Backup nach UI-Bestätigung."""
    with storage_coordinator.exclusive():
        deleted = backup.delete_backup(BACKUPS_DIR, filename)
    if not deleted:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")
    return _backup_status_response(request)


@app.post("/backup/delete-all", response_class=HTMLResponse)
def backup_delete_all(request: Request) -> HTMLResponse:
    """Löscht alle vorhandenen Backup-ZIPs nach UI-Bestätigung (nicht den
    Ausführungsverlauf oder Restore-Rollbacks, die bleiben eigenständig über
    ihre jeweiligen Löschaktionen steuerbar)."""
    with storage_coordinator.exclusive():
        count = backup.delete_all_backups(BACKUPS_DIR)
    message = f"{count} Backup(s) gelöscht." if count else "Keine Backups zum Löschen vorhanden."
    return templates.TemplateResponse(request, "_settings_backup_ready.html", _backup_context(message=message))


@app.get("/backup/list", response_class=HTMLResponse)
def backup_list(
    request: Request,
    sort: str = "created_at",
    dir: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> HTMLResponse:
    """Nur das Backup-Tabellen-Fragment (Sortier-/Seiten-Wechsel) — dasselbe
    Prinzip wie /entities-table, aber auf #backup-table-wrap statt des
    gesamten #backup-status begrenzt, damit Import-Dropzone/Ausführungs-
    verlauf/Rollbacks beim reinen Umsortieren nicht mit neu gerendert werden."""
    return templates.TemplateResponse(
        request, "_backup_table.html", _backup_list_context(sort, dir, page, page_size)
    )


_GROWTH_RANGE_SINCE_SECONDS = {"day": 86400, "month": 30 * 86400, "year": 365 * 86400, "all": None}
_GROWTH_RANGE_OPTIONS = [("day", "Tag"), ("month", "Monat"), ("year", "Jahr"), ("all", "Gesamt")]


@app.get("/api/stats-snapshots")
def api_stats_snapshots(range: str = "month") -> dict:
    if range not in _GROWTH_RANGE_SINCE_SECONDS:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")
    seconds = _GROWTH_RANGE_SINCE_SECONDS[range]
    since_ts = 0.0 if seconds is None else time.time() - seconds
    snapshots = index.get_stats_snapshots(since_ts)
    return {
        "points": [
            {"ts": s["ts"], "total_rows": s["total_rows"], "total_size_bytes": s["total_size_bytes"]}
            for s in snapshots
        ]
    }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _storage_breakdown() -> list[dict]:
    """Speicherbedarf nach Kategorie, direkt aus dem Dateisystem gezählt (nicht
    aus dem Index) — als eigenständige, vom Index unabhängige Sicht auf den
    tatsächlichen Plattenverbrauch, auch für Verzeichnisse (Hot Buffer, Import,
    Backups), die der Index gar nicht mitführt."""
    index_path = DATA_DIR / "index.sqlite"
    return [
        {"key": "archive", "label": "Archiv", "bytes": _dir_size(DATA_DIR / "archive")},
        {"key": "rollup", "label": "Rollups", "bytes": _dir_size(DATA_DIR / "rollup")},
        {"key": "hot", "label": "Laufender Monat (Hot Buffer)", "bytes": _dir_size(DATA_DIR / "hot")},
        {"key": "index", "label": "Index", "bytes": index_path.stat().st_size if index_path.exists() else 0},
        {"key": "backups", "label": "Backups", "bytes": _dir_size(DATA_DIR / "backups")},
        {"key": "reports", "label": "Import-Reports", "bytes": _dir_size(DATA_DIR / "reports")},
        {
            "key": "import",
            "label": "Import-Zwischendateien",
            "bytes": _dir_size(DATA_DIR / "symcon_import") + _dir_size(DATA_DIR / "csv_import"),
        },
    ]


def _diagnostics_payload() -> dict:
    """Bereinigte App-Diagnose ohne Token, Messwerte oder Entitäts-IDs."""
    overview = index.get_overview()
    storage = _storage_breakdown()
    audit = _storage_reconcile_last or {}
    return {
        "format": "zeitarchiv-diagnostics",
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "application": {
            "version": APP_VERSION,
            "timezone": str(TZ),
            "data_dir": str(DATA_DIR),
            "uptime_seconds": round(max(0.0, time.time() - _SERVER_STARTED_AT), 1),
        },
        "runtime": {
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "architecture": platform.machine(),
        },
        "formats": {
            "portable_backup": backup.BACKUP_FORMAT_VERSION,
            "import_report": import_reports.FORMAT_VERSION,
        },
        "configuration": {
            "default_resolution": index.get_setting("default_resolution", DEFAULT_RESOLUTION),
            "default_retention": index.get_setting("default_retention", DEFAULT_RETENTION),
            "retention_enforcement": index.get_setting("retention_enforcement", "off"),
            "retention_enforcement_time": index.get_setting(
                "retention_enforcement_time", RETENTION_DEFAULT_TIME
            ),
            "backup_schedule": index.get_setting("backup_schedule", "off"),
            "log_level": index.get_setting("log_level", DEFAULT_LOG_LEVEL),
            "access_log_mode": index.get_setting("access_log_mode", DEFAULT_ACCESS_LOG_MODE),
            "color_scheme": index.get_setting("color_scheme", "zeitarchiv"),
            "color_mode": index.get_setting("color_mode", "auto"),
            "font_scale": index.get_setting("font_scale", "1"),
        },
        "storage": {
            "entity_count": int(overview["entity_count"]),
            "total_rows": int(overview["total_rows"]),
            "indexed_archive_bytes": int(overview["total_size_bytes"]),
            "filesystem_total_bytes": sum(int(row["bytes"]) for row in storage),
            "categories": {row["key"]: int(row["bytes"]) for row in storage},
            "import_report_count": len(import_reports.list_all(DATA_DIR)),
            "backup_count": len(list(BACKUPS_DIR.glob("zeitarchiv-backup-*.zip"))),
        },
        "storage_index_audit": {
            "checked_at": audit.get("checked_at"),
            "entities_checked": int(audit.get("entities_checked", 0) or 0),
            "mismatch_count": len(audit.get("mismatches", [])),
            "error_count": len(audit.get("errors", [])),
            "repaired": bool(audit.get("repaired", False)),
        },
    }


@app.get("/settings/diagnostics")
def settings_diagnostics_download() -> Response:
    with storage_coordinator.exclusive():
        payload = _diagnostics_payload()
    filename = f"zeitarchiv-diagnose-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ingestion_rate_per_second(snapshots: list[dict], window_seconds: float) -> float | None:
    """Ø Netto-Zeilenzuwachs pro Sekunde über die letzten window_seconds,
    aus den stündlichen stats_snapshots abgeleitet (siehe
    Index.get_stats_snapshots) — kein eigener Ereignis-Log nötig, da die
    Snapshots ohnehin unabhängig von Seitenaufrufen stündlich geschrieben
    werden. None, wenn vor dem Fenster noch kein Snapshot liegt (zu wenig
    Verlauf) oder gar keine zwei Snapshots vorhanden sind."""
    if len(snapshots) < 2:
        return None
    latest = snapshots[-1]
    cutoff = latest["ts"] - window_seconds
    baseline = next((s for s in reversed(snapshots[:-1]) if s["ts"] <= cutoff), None)
    if baseline is None:
        return None
    elapsed = latest["ts"] - baseline["ts"]
    if elapsed <= 0:
        return None
    # Retention-Läufe können total_rows zwischen zwei Snapshots senken (endgültig
    # gelöschte Zeilen) — als Ingest-Rate auf 0 statt negativ anzeigen, da eine
    # negative "Eventrate" hier verwirrender wäre als informativ.
    return max(0.0, (latest["total_rows"] - baseline["total_rows"]) / elapsed)


@app.get("/statistik", response_class=HTMLResponse)
@_storage_locked(lambda _args: [row["entity_id"] for row in index.list_entities()])
def statistik_view(request: Request) -> HTMLResponse:
    """Allgemeine Statistik (Konzept Abschnitt 03/10) — Aufschlüsselung
    nach Typ/Auflösung/Aufbewahrung plus Wachstumsverlauf aus denselben
    Schnappschüssen wie die Sparklines auf der Startseite."""
    now = datetime.now(TZ)
    overview = index.get_overview()
    by_type = [
        {
            "label": format_type(row["aggregation_type"]),
            "entity_count": row["entity_count"],
            "total_rows": format_int(row['total_rows']),
            "total_size": format_size(row["total_size_bytes"]),
        }
        for row in index.get_stats_by_type()
    ]
    by_resolution = [
        {
            "label": format_resolution(row["resolution"]),
            "entity_count": row["entity_count"],
            "total_rows": format_int(row['total_rows']),
            "total_size": format_size(row["total_size_bytes"]),
        }
        for row in index.get_stats_by_resolution()
    ]
    retention_overview = _load_retention_overview()
    snapshots = index.get_stats_snapshots(time.time() - 30 * 86400)
    growth_points = [
        {"ts": s["ts"], "total_rows": s["total_rows"], "total_size_bytes": s["total_size_bytes"]}
        for s in snapshots
    ]
    duplicate_rows = cleanup.count_duplicate_rows_by_entity(
        DATA_DIR, index, TZ, max_rows_per_entity=MAX_UI_ANALYSIS_ROWS
    )
    duplicates_by_entity = [
        {
            "entity_id": row["entity_id"],
            "friendly_name": row["friendly_name"],
            "count": format_int(row['count']),
        }
        for row in duplicate_rows
    ]
    storage_breakdown_raw = _storage_breakdown()
    storage_total_bytes = sum(row["bytes"] for row in storage_breakdown_raw)
    storage_breakdown = [
        {
            "key": row["key"],
            "label": row["label"],
            "bytes": row["bytes"],
            "size": format_size(row["bytes"]),
            "percent": round(row["bytes"] / storage_total_bytes * 100, 1) if storage_total_bytes else 0,
            "href": {"backups": "backup", "import": "import", "reports": "import?tab=reports"}.get(row["key"]),
        }
        for row in storage_breakdown_raw
    ]

    retention_totals = retention_overview.get("totals", {})
    retention_history_30d = index.get_retention_job_totals(now.timestamp() - 30 * 86400)
    retention_history_all = index.get_retention_job_totals(0.0)

    rate_per_hour = _ingestion_rate_per_second(growth_points, 24 * 3600)
    rate_per_day = _ingestion_rate_per_second(growth_points, 7 * 86400)
    dashboard_pin_count = index.count_dashboard_pins()

    return templates.TemplateResponse(
        request,
        "statistik.html",
        {
            "entity_count": overview["entity_count"],
            "total_rows": format_int(overview['total_rows']),
            "total_size": format_size(overview["total_size_bytes"]),
            "chart_count": index.count_saved_charts(),
            "table_count": index.count_saved_tables(),
            "dashboard_pin_count": dashboard_pin_count,
            "dashboard_pin_limit": index.DASHBOARD_TILE_LIMIT,
            "events_per_hour": format_int(round(rate_per_hour * 3600)) if rate_per_hour is not None else None,
            "events_per_day": format_int(round(rate_per_day * 86400)) if rate_per_day is not None else None,
            "by_type": by_type,
            "by_resolution": by_resolution,
            "retention_due_rows": format_int(int(retention_totals.get('rows_deleted', 0) or 0)),
            "retention_due_entities": int(retention_totals.get("entities_affected", 0) or 0),
            "retention_due_months": int(retention_totals.get("months_deleted", 0) or 0),
            "retention_due_size": format_size(int(retention_totals.get("bytes_freed", 0) or 0)),
            "retention_history_30d_rows": format_int(retention_history_30d['rows_deleted']),
            "retention_history_30d_size": format_size(retention_history_30d["bytes_freed"]),
            "retention_history_all_rows": format_int(retention_history_all['rows_deleted']),
            "retention_history_all_size": format_size(retention_history_all["bytes_freed"]),
            "growth_points": growth_points,
            "has_growth_history": len(growth_points) >= 2,
            "growth_range_options": _GROWTH_RANGE_OPTIONS,
            "duplicates_by_entity": duplicates_by_entity,
            "duplicates_total": format_int(sum(row['count'] for row in duplicate_rows)),
            "storage_breakdown": storage_breakdown,
            "storage_total_size": format_size(storage_total_bytes),
            "generated_at": f"{format_timestamp(now.timestamp(), TZ)} {format_time(now.timestamp(), TZ)}",
        },
    )


@app.get("/export", response_class=HTMLResponse)
def export_page(request: Request) -> HTMLResponse:
    """CSV-Export (Einstellungen-Bereich, analog zum Import): pro Entität die
    komplette Rohdaten-Historie (Hot Buffer + Archiv, ohne zur Löschung
    markierte Datensätze) als CSV herunterladbar — nutzt dieselbe list_raw_rows() wie die
    Bereinigungs-Seite, also garantiert dieselbe Sicht auf die Daten. Die eigentliche
    Liste lädt wie bei der Entitäten-Übersicht per htmx aus /export-table, damit
    Suche/Filter/Sortierung ohne Reload greifen."""
    units = index.list_distinct_units()
    unit_options = [{"value": "__none__" if u is None else u, "label": "Ohne Einheit" if u is None else u} for u in units]
    return templates.TemplateResponse(request, "export.html", {"unit_options": unit_options})


def _visible_row_count(entity) -> int:
    """Logische Rohwerte: physischer Indexzähler abzüglich Soft-Deletes."""
    deleted_count = int(entity["deleted_count"] or 0)
    return max(0, int(entity["row_count"] or 0) - deleted_count)


def _export_table_response(
    request: Request,
    search: str,
    type_filter: list[str],
    unit_filter: str,
    sort: str,
    direction: str,
    page: int = 1,
    page_size: int = 50,
) -> HTMLResponse:
    matched = index.list_entities(search=search or None, type_filter=type_filter, unit_filter=unit_filter, sort=sort, direction=direction)
    page_matched, pagination = _paginate(matched, page, page_size)
    rows = [
        {
            "entity_id": row["entity_id"],
            "friendly_name": row["friendly_name"],
            "aggregation_type": row["aggregation_type"],
            "type_label": format_type(row["aggregation_type"]),
            "unit": row["unit"],
            "row_count": _visible_row_count(row),
        }
        for row in page_matched
    ]

    def _next_dir(column: str) -> str:
        return "desc" if sort == column and direction == "asc" else "asc"

    columns = [
        ("entity_id", "Entität"),
        ("type", "Typ"),
        ("unit", "Einheit"),
        ("rows", "Datensätze"),
    ]
    header_links = [
        {
            "key": key,
            "label": label,
            "next_dir": _next_dir(key),
            "active": sort == key,
            "arrow": ("↓" if direction == "asc" else "↑") if sort == key else "",
        }
        for key, label in columns
    ]

    return templates.TemplateResponse(
        request,
        "_export_table.html",
        {
            "rows": rows,
            "search": search,
            "type": type_filter,
            "unit": unit_filter,
            "columns": header_links,
            "pagination": pagination,
        },
    )


@app.get("/export-table", response_class=HTMLResponse)
def export_table(
    request: Request,
    search: str = "",
    type: list[str] = Query(["all"]),
    unit: str = "all",
    sort: str = "entity_id",
    dir: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> HTMLResponse:
    return _export_table_response(request, search, type, unit, sort, dir, page, page_size)


@app.get("/export/download")
def export_download(entity_id: str) -> StreamingResponse:
    entity = _require_entity(entity_id)

    if _visible_row_count(entity) > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"CSV-Export ist auf {MAX_EXPORT_ROWS} Zeilen begrenzt",
        )

    first_ts = entity["first_ts"]
    last_ts = entity["last_ts"]

    def generate():
        with storage_coordinator.entity(entity_id):
            yield "timestamp,unix_ts,value\r\n"
            if first_ts is None or last_ts is None:
                return
            rows = cleanup.iter_raw_rows(
                DATA_DIR,
                index,
                entity_id,
                first_ts,
                last_ts + 1,
                TZ,
                max_rows=MAX_EXPORT_ROWS,
            )
            for ts, value in rows:
                timestamp = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")
                yield f"{timestamp},{ts:.3f},{value}\r\n"

    filename = f"{entity_id}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Spalten der Entitäten-Tabelle jenseits von Favorit-Stern und Entität
# (Name+ID) — beide sind Kernfunktion (Favorisieren, Navigation) und deshalb
# nicht abwählbar. Die Tabelle ist mit allen Spalten strukturell breiter als
# der verfügbare Platz im Settings-Panel (min-width:1100px vs. ~862px, siehe
# .col-*-Kommentar in entities_list.html) — statt das immer wegzuscrollen,
# lässt sich hier auswählen, welche Spalten überhaupt sichtbar sind. Auswahl
# wird wie font_scale über die settings-Tabelle persistiert (index.get_setting/
# set_setting), gilt also global fürs ganze Add-on, nicht pro Browser.
ENTITIES_OPTIONAL_COLUMNS = [
    ("type", "Typ"),
    ("first_ts", "Erster Wert"),
    ("last_ts", "Letzter Wert"),
    ("resolution", "Auflösung"),
    ("retention", "Aufbewahrung"),
    ("unit", "Einheit"),
    ("rows", "Datensätze"),
    ("size", "Größe"),
]
# Nur Auflösung/Aufbewahrung initial aus — diese beiden Konfigurationsdetails
# sind für den ersten Überblick am ehesten verzichtbar. Alle übrigen Spalten,
# einschließlich erstem und letztem Wert, sind standardmäßig sichtbar.
ENTITIES_DEFAULT_COLUMNS = "type,first_ts,last_ts,unit,rows,size"


def _entities_visible_columns() -> set[str]:
    valid = {key for key, _ in ENTITIES_OPTIONAL_COLUMNS}
    raw = index.get_setting("entities_columns", ENTITIES_DEFAULT_COLUMNS) or ""
    return {c for c in raw.split(",") if c} & valid


def _entities_table_response(
    request: Request,
    search: str,
    type_filter: list[str],
    unit_filter: str,
    sort: str,
    direction: str,
    page: int = 1,
    page_size: int = 50,
    favorites_only: bool = False,
    visible_columns: set[str] | None = None,
) -> HTMLResponse:
    if visible_columns is None:
        visible_columns = _entities_visible_columns()
    matched = index.list_entities(
        search=search or None, type_filter=type_filter, unit_filter=unit_filter, sort=sort, direction=direction,
        favorites_only=favorites_only,
    )
    page_matched, pagination = _paginate(matched, page, page_size)
    rows = [
        {
            "entity_id": row["entity_id"],
            "friendly_name": row["friendly_name"],
            "aggregation_type": row["aggregation_type"],
            "type_label": format_type(row["aggregation_type"]),
            "resolution_label": format_resolution(row["resolution"]),
            "retention_label": format_retention(row["retention"]),
            "unit": row["unit"],
            "row_count": _visible_row_count(row),
            "first_ts": format_timestamp(row["first_ts"], TZ),
            "first_ts_time": format_time(row["first_ts"], TZ),
            "last_ts": format_timestamp(row["last_ts"], TZ),
            "last_ts_time": format_time(row["last_ts"], TZ),
            "size": format_size(row["size_bytes"]),
            "is_favorite": bool(row["is_favorite"]),
        }
        for row in page_matched
    ]

    def _next_dir(column: str) -> str:
        return "desc" if sort == column and direction == "asc" else "asc"

    columns = [("entity_id", "Entität")] + [
        (key, label) for key, label in ENTITIES_OPTIONAL_COLUMNS if key in visible_columns
    ]
    # Eine CSS-Klasse pro Spalte, gemeinsam von <th> und <td> genutzt (siehe
    # _entities_table.html) — steuert dort sowohl feste Breite als auch
    # Ausrichtung (Entität links, Typ zentriert, alles andere rechts) in
    # genau einer Regel je Klasse statt Positions-Selektoren, die bei
    # ein-/ausgeblendeten optionalen Spalten sonst verrutschen würden.
    col_class = {
        "entity_id": "col-entity",
        "type": "col-type",
        "first_ts": "col-date",
        "last_ts": "col-date",
        "resolution": "col-resolution",
        "retention": "col-retention",
        "unit": "col-unit",
        "rows": "col-rows",
        "size": "col-size",
    }
    header_links = [
        {
            "key": key,
            "label": label,
            "next_dir": _next_dir(key),
            "active": sort == key,
            "arrow": ("↓" if direction == "asc" else "↑") if sort == key else "",
            "col_class": col_class[key],
        }
        for key, label in columns
    ]

    return templates.TemplateResponse(
        request,
        "_entities_table.html",
        {
            "rows": rows,
            "search": search,
            "type": type_filter,
            "unit": unit_filter,
            "sort": sort,
            "dir": direction,
            "favorites_only": favorites_only,
            "columns": header_links,
            "visible_columns": visible_columns,
            "pagination": pagination,
        },
    )


@app.get("/entities-table", response_class=HTMLResponse)
def entities_table(
    request: Request,
    search: str = "",
    type: list[str] = Query(["all"]),
    unit: str = "all",
    sort: str = "entity_id",
    dir: str = "asc",
    page: int = 1,
    page_size: int = 50,
    favorites: bool = False,
    columns: list[str] = Query([]),
    columns_submitted: bool = False,
) -> HTMLResponse:
    # columns_submitted unterscheidet "die Spalten-Auswahl aus #controls kam
    # tatsächlich mit" von "columns fehlt einfach im Request" (z. B. ein
    # externer/manueller Aufruf dieser Route ohne das Hidden-Field) — sonst
    # würde ein Aufruf ohne columns-Parameter die gespeicherte Auswahl
    # versehentlich auf "keine Spalten sichtbar" zurücksetzen (leere Liste).
    if columns_submitted:
        valid = {key for key, _ in ENTITIES_OPTIONAL_COLUMNS}
        index.set_setting("entities_columns", ",".join(c for c in columns if c in valid))
    return _entities_table_response(
        request, search, type, unit, sort, dir, page, page_size, favorites, _entities_visible_columns()
    )


def _entity_config_context(entity) -> dict:
    """Gemeinsamer Kontext für die volle Konfigurationsseite (GET) und das per
    htmx nachgeladene/gespeicherte Formular-Fragment (POST) — beide zeigen
    dieselben Details, denselben aktuellen Konfigurationsstand und dieselbe
    Werte-Vorschau (die sich bei einer Nachkommastellen-Änderung mit aktualisiert,
    weil sie im selben Fragment liegt)."""
    entity_id = entity["entity_id"]
    decimals = entity["decimals"]
    decimals_int = decimals_to_int(decimals)

    now = datetime.now(TZ)
    window_start = (now - timedelta(days=60)).timestamp()
    raw_rows = cleanup.list_raw_rows(
        DATA_DIR, index, entity_id, window_start, now.timestamp(), TZ,
        now=now, max_rows=MAX_UI_ANALYSIS_ROWS
    )
    preview_rows = [
        {
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M:%S"),
            "formatted_value": format_value(value, decimals_int),
        }
        for ts, value in reversed(raw_rows[-10:])
    ]

    return {
        "entity_id": entity_id,
        "friendly_name": entity["friendly_name"],
        "aggregation_type": entity["aggregation_type"],
        "type_label": format_type(entity["aggregation_type"]),
        "unit": entity["unit"] or "—",
        "row_count": format_int(_visible_row_count(entity)),
        "first_ts": format_timestamp(entity["first_ts"], TZ),
        "first_ts_time": format_time(entity["first_ts"], TZ),
        "last_ts": format_timestamp(entity["last_ts"], TZ),
        "last_ts_time": format_time(entity["last_ts"], TZ),
        "size": format_size(entity["size_bytes"]),
        "resolution": entity["resolution"],
        "retention": entity["retention"],
        "decimals": decimals,
        "value_filter": entity["value_filter"],
        "gap_threshold": entity["gap_threshold"],
        "outlier_threshold": entity["outlier_threshold"],
        "display_mode": entity["display_mode"],
        "resolution_options": list(RESOLUTION_LABELS.items()),
        "retention_options": list(RETENTION_LABELS.items()),
        "decimals_options": list(DECIMALS_LABELS.items()),
        "value_filter_options": list(VALUE_FILTER_LABELS.items()),
        "gap_threshold_options": list(GAP_THRESHOLD_LABELS.items()),
        "outlier_threshold_options": list(OUTLIER_THRESHOLD_LABELS.items()),
        "display_mode_options": list(DISPLAY_MODE_LABELS.items()),
        "preview_rows": preview_rows,
        "base": "../..",
    }


@app.get("/entities/{entity_id}/config", response_class=HTMLResponse)
@_storage_locked(lambda args: args["entity_id"])
def entity_config_page(request: Request, entity_id: str) -> HTMLResponse:
    entity = _require_entity(entity_id)
    return templates.TemplateResponse(request, "entity_config.html", _entity_config_context(entity))


@app.post("/entities/{entity_id}/config", response_class=HTMLResponse)
async def update_entity_config(request: Request, entity_id: str) -> HTMLResponse:
    """Auflösung/Aufbewahrung/Nachkommastellen einer Entität ändern (Konzept
    Abschnitt 03) — im eigenen Konfigurationsbereich der Entität, ähnlich dem
    Bereinigungs-Werkzeug ausgelagert statt inline in der Übersichtstabelle.
    Ändert nur den Index-Wert; wirkt sich für die Auflösung ab dem nächsten
    Schreibvorgang aus (Drosselung in /api/write), die Aufbewahrung ist aktuell
    rein informativ (kein Purge-Job, siehe Konzept Abschnitt 09)."""
    _require_entity(entity_id)
    form = await request.form()
    resolution = form.get("resolution")
    retention = form.get("retention")
    decimals = form.get("decimals")
    value_filter = form.get("value_filter")
    gap_threshold = form.get("gap_threshold")
    outlier_threshold = form.get("outlier_threshold")
    display_mode = form.get("display_mode")
    if resolution is not None and resolution not in RESOLUTION_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Auflösung")
    if retention is not None and retention not in RETENTION_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Aufbewahrung")
    if decimals is not None and decimals not in DECIMALS_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Nachkommastellen-Angabe")
    if value_filter is not None and value_filter not in VALUE_FILTER_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Wertänderungsfilter")
    if gap_threshold is not None and gap_threshold not in GAP_THRESHOLD_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Lücken-Schwellwert")
    if outlier_threshold is not None and outlier_threshold not in OUTLIER_THRESHOLD_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Ausreißer-Schwellwert")
    if display_mode is not None and display_mode not in DISPLAY_MODE_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Anzeigemodus")
    def update_locked() -> HTMLResponse:
        with storage_coordinator.entity(entity_id):
            index.set_config(
                entity_id,
                resolution=str(resolution) if resolution is not None else None,
                retention=str(retention) if retention is not None else None,
                decimals=str(decimals) if decimals is not None else None,
                value_filter=str(value_filter) if value_filter is not None else None,
                gap_threshold=str(gap_threshold) if gap_threshold is not None else None,
                outlier_threshold=str(outlier_threshold) if outlier_threshold is not None else None,
                display_mode=str(display_mode) if display_mode is not None else None,
            )
            if retention is not None:
                _invalidate_retention_overview()
            entity = _require_entity(entity_id)
            context = _entity_config_context(entity)
            context["saved"] = True
            return templates.TemplateResponse(request, "_entity_config_form.html", context)

    return await run_in_threadpool(update_locked)


def _require_entity(entity_id: str):
    _validate_entity_id_or_400(entity_id)
    entity = index.get_entity(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entität nicht gefunden")
    return entity


def _validate_entity_id_or_400(entity_id: str) -> str:
    try:
        return validate_entity_id(entity_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Ungültige Entitäts-ID") from err


@app.post("/entities/{entity_id}/favorite")
def entity_favorite_toggle(entity_id: str) -> dict:
    entity = _require_entity(entity_id)
    new_state = not entity["is_favorite"]
    index.set_entity_favorite(entity_id, new_state)
    return {"is_favorite": new_state}


@app.post("/entities/{entity_id}/values/delete-all")
@_storage_locked(lambda args: args["entity_id"])
def entity_delete_all_values(entity_id: str) -> dict:
    """Löscht alle Werte, behält aber Konfiguration und Entitätseintrag."""
    _require_entity(entity_id)
    entity_removal.delete_all_values(DATA_DIR, index, entity_id)
    _invalidate_retention_overview()
    return {"ok": True}


@app.post("/entities/{entity_id}/delete")
@_storage_locked(lambda args: args["entity_id"])
def entity_delete(entity_id: str) -> dict:
    """Entfernt eine Entität einschließlich aller Werte und Indexmetadaten."""
    _require_entity(entity_id)
    entity_removal.delete_entity(DATA_DIR, index, entity_id)
    _invalidate_retention_overview()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Charts-Bereich (Konzept "Offene Punkte": eigener Bereich zum Erstellen und
# "Ablegen" von Charts, inkl. Multi-Entitäts-Charts) — ein gespeichertes Chart
# ist eine gespeicherte Auswahl (Entitäten + Zeitraum), keine eingefrorenen
# Werte: beim Ansehen lädt es über /api/query-multi immer live neu, genau wie
# die Entität-eigene Chart-Seite oben. /charts/new und /charts/{id} teilen
# sich dieselbe Editor-Seite (chart_editor.html) — Anlegen und Bearbeiten
# unterscheiden sich nur darin, ob schon eine gespeicherte Konfiguration zum
# Vorbefüllen existiert.
# ---------------------------------------------------------------------------

_CHART_RANGE_OPTIONS = [
    ("hour", "Stunde"), ("day", "Tag"), ("week", "Woche"),
    ("month", "Monat"), ("year", "Jahr"), ("decade", "Dekade"),
]
_CHART_RESOLUTION_PRESETS = {"auto", "medium", "coarse"}


@app.get("/charts", response_class=HTMLResponse)
def charts_list(request: Request) -> HTMLResponse:
    charts = index.list_saved_charts()
    friendly_names = {
        row["entity_id"]: row["friendly_name"] or row["entity_id"]
        for row in index.list_entities()
    }
    rows = [
        {
            "id": c["id"],
            "name": c["name"],
            "entity_count": len(c["entity_ids"]),
            "entity_names": ", ".join(c["entity_ids"][:3]) + (" …" if len(c["entity_ids"]) > 3 else ""),
            "entity_tooltip_names": ", ".join(
                friendly_names.get(entity_id, entity_id) for entity_id in c["entity_ids"]
            ),
            "entity_tooltip_ids": ", ".join(c["entity_ids"]),
            "range_label": dict(_CHART_RANGE_OPTIONS).get(c["range_key"], c["range_key"]),
            "is_favorite": c["is_favorite"],
        }
        for c in charts
    ]
    return templates.TemplateResponse(request, "charts.html", {"rows": rows})


def _chart_editor_context(chart: dict | None, prefill: dict | None = None) -> dict:
    """prefill füllt ein NEUES (chart=None) Chart mit Startwerten vor, z. B. von
    "Als Chart speichern" auf der Entität-eigenen Chart-Seite (entity_detail.html)
    — übernimmt die dort gerade betrachtete Entität + Zeitraum-Einstellungen,
    damit nur noch ein Name vergeben und gespeichert werden muss. Ein
    bestehendes Chart (chart != None) ignoriert prefill immer, dessen eigene
    gespeicherte Werte haben Vorrang."""
    entity_options = [
        {
            "entity_id": row["entity_id"],
            "label": row["friendly_name"] or row["entity_id"],
        }
        for row in index.list_entities()
    ]
    prefill = prefill or {}
    return {
        "chart_id": chart["id"] if chart else None,
        "chart_name": chart["name"] if chart else prefill.get("name", ""),
        "selected_entity_ids": chart["entity_ids"] if chart else prefill.get("entity_ids", []),
        "range_key": chart["range_key"] if chart else prefill.get("range_key", "day"),
        "continuous": chart["continuous"] if chart else prefill.get("continuous", False),
        "resolution_preset": chart["resolution_preset"] if chart else prefill.get("resolution_preset", "auto"),
        "dynamic_y_axis": chart["dynamic_y_axis"] if chart else True,
        "chart_stats": chart["chart_stats"] if chart else True,
        "entity_names": chart["entity_names"] if chart else {},
        "entity_options": entity_options,
        "range_options": _CHART_RANGE_OPTIONS,
        # compare/compare_mode sind (wie im Chart-Editor selbst) reine
        # Laufzeit-Ansichtseinstellungen, kein gespeichertes Chart-Feld — nur
        # der Anfangszustand eines gerade erst über prefill eröffneten neuen
        # Charts kann sie daher überhaupt sinnvoll setzen.
        "compare": prefill.get("compare", False) if not chart else False,
        "compare_mode": prefill.get("compare_mode", "previous") if not chart else "previous",
        "base": "..",
    }


@app.get("/charts/new", response_class=HTMLResponse)
def charts_new(
    request: Request,
    entity_id: str | None = None,
    name: str | None = None,
    range: str = Query("day", alias="range"),
    continuous: bool = False,
    resolution_preset: str = "auto",
    compare: bool = False,
    compare_mode: str = "previous",
) -> HTMLResponse:
    prefill = None
    if entity_id:
        try:
            entity_id = validate_entity_id(entity_id)
        except ValueError:
            entity_id = None
    if entity_id:
        prefill = {
            "entity_ids": [entity_id],
            "name": (name or "").strip(),
            "range_key": range if range in dict(_CHART_RANGE_OPTIONS) else "day",
            "continuous": continuous,
            "resolution_preset": resolution_preset if resolution_preset in _CHART_RESOLUTION_PRESETS else "auto",
            "compare": compare,
            "compare_mode": compare_mode if compare_mode in ("previous", "year") else "previous",
        }
    return templates.TemplateResponse(request, "chart_editor.html", _chart_editor_context(None, prefill))


class _SaveChartBody(BaseModel):
    name: str
    entity_ids: list[EntityId]
    range_key: str
    continuous: bool = False
    entity_names: dict[str, str] = {}
    resolution_preset: str = "auto"
    dynamic_y_axis: bool = True
    chart_stats: bool = True


@app.post("/charts")
def charts_create(body: _SaveChartBody) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Bitte einen Namen für das Chart angeben")
    if not body.entity_ids:
        raise HTTPException(status_code=400, detail="Bitte mindestens eine Entität auswählen")
    if body.range_key not in dict(_CHART_RANGE_OPTIONS):
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")
    if body.resolution_preset not in _CHART_RESOLUTION_PRESETS:
        raise HTTPException(status_code=400, detail="Ungültige Chart-Auflösung")
    entity_names = {k: v.strip() for k, v in body.entity_names.items() if v.strip()}
    chart_id = index.create_saved_chart(
        body.name.strip(), body.entity_ids, body.range_key, body.continuous,
        entity_names, body.resolution_preset, body.dynamic_y_axis,
        chart_stats=body.chart_stats,
    )
    return {"id": chart_id}


@app.get("/charts/{chart_id}", response_class=HTMLResponse)
def charts_view(request: Request, chart_id: int) -> HTMLResponse:
    chart = index.get_saved_chart(chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    return templates.TemplateResponse(request, "chart_editor.html", _chart_editor_context(chart))


@app.post("/charts/{chart_id}")
def charts_update(chart_id: int, body: _SaveChartBody) -> dict:
    if index.get_saved_chart(chart_id) is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Bitte einen Namen für das Chart angeben")
    if not body.entity_ids:
        raise HTTPException(status_code=400, detail="Bitte mindestens eine Entität auswählen")
    if body.range_key not in dict(_CHART_RANGE_OPTIONS):
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")
    if body.resolution_preset not in _CHART_RESOLUTION_PRESETS:
        raise HTTPException(status_code=400, detail="Ungültige Chart-Auflösung")
    entity_names = {k: v.strip() for k, v in body.entity_names.items() if v.strip()}
    index.update_saved_chart(
        chart_id, body.name.strip(), body.entity_ids, body.range_key,
        body.continuous, entity_names, body.resolution_preset,
        body.dynamic_y_axis, chart_stats=body.chart_stats,
    )
    return {"id": chart_id}


@app.post("/charts/{chart_id}/delete")
def charts_delete(chart_id: int) -> dict:
    index.delete_saved_chart(chart_id)
    return {"ok": True}


@app.post("/charts/{chart_id}/favorite")
def charts_favorite_toggle(chart_id: int) -> dict:
    chart = index.get_saved_chart(chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    new_state = not chart["is_favorite"]
    index.set_chart_favorite(chart_id, new_state)
    return {"is_favorite": new_state}


def _dashboard_tiles_context() -> dict:
    """Für die Dashboard-Kacheln auf der Übersichtsseite (Konzept "Offene
    Punkte", erweitert um Vergleichstabellen) — sowohl vom initialen Laden
    von "/" als auch von pin/unpin/reorder genutzt (alle geben dasselbe
    Fragment zurück, damit eine Änderung nicht per Extra-Request neu geladen
    werden muss). dashboard_pins kennt zwei item_type-Werte ('chart'/
    'table'), hier gegen die jeweilige Tabelle aufgelöst — ein verwaister
    Pin (Chart/Tabelle zwischenzeitlich gelöscht, sollte durch die
    Bereinigung in delete_saved_chart()/delete_saved_table() praktisch nie
    vorkommen) wird dabei still übersprungen statt einen Fehler zu werfen."""
    pins = index.list_dashboard_pins()
    tiles = []
    for p in pins:
        if p["item_type"] == "chart":
            c = index.get_saved_chart(p["item_id"])
            if c is None:
                continue
            tiles.append({
                "kind": "chart", "id": c["id"], "name": c["name"],
                "entity_ids": c["entity_ids"], "range_key": c["range_key"],
                "continuous": c["continuous"], "entity_names": c["entity_names"],
                "resolution_preset": c["resolution_preset"],
                "dynamic_y_axis": c["dynamic_y_axis"],
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
            })
        elif p["item_type"] == "table":
            t = index.get_saved_table(p["item_id"])
            if t is None:
                continue
            tiles.append({
                "kind": "table", "id": t["id"], "name": t["name"],
                "columns": t["columns"], "rows": t["rows"], "style": t["style"],
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
            })
    pinned_chart_ids = {p["item_id"] for p in pins if p["item_type"] == "chart"}
    pinned_table_ids = {p["item_id"] for p in pins if p["item_type"] == "table"}
    return {
        "tiles": tiles,
        "can_add_tile": len(tiles) < index.DASHBOARD_TILE_LIMIT,
        "unpinned_charts": [c for c in index.list_saved_charts() if c["id"] not in pinned_chart_ids],
        "unpinned_tables": [t for t in index.list_saved_tables() if t["id"] not in pinned_table_ids],
    }


@app.post("/charts/{chart_id}/pin", response_class=HTMLResponse)
def charts_pin(request: Request, chart_id: int) -> HTMLResponse:
    if index.get_saved_chart(chart_id) is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    index.pin_item_to_dashboard("chart", chart_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context())


@app.post("/charts/{chart_id}/unpin", response_class=HTMLResponse)
def charts_unpin(request: Request, chart_id: int) -> HTMLResponse:
    index.unpin_item_from_dashboard("chart", chart_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context())


class _DashboardPinRef(BaseModel):
    item_type: str
    item_id: int


class _ReorderDashboardBody(BaseModel):
    pins: list[_DashboardPinRef]


class _ResizeDashboardTileBody(BaseModel):
    item_type: str
    item_id: int
    grid_cols: int = Field(ge=1, le=3)
    grid_rows: int = Field(ge=1, le=3)


@app.post("/dashboard/reorder")
def dashboard_reorder(body: _ReorderDashboardBody) -> dict:
    """Persistiert die per Drag&Drop auf der Übersichtsseite geänderte
    Kachel-Reihenfolge — das Frontend hat die Kacheln zu diesem Zeitpunkt
    schon live im DOM umsortiert (dashboard-tiles.js), dieser Aufruf schreibt
    das nur noch fest, ohne selbst ein neues Fragment zurückzugeben. Ein
    eigener Pfad statt "/charts/reorder"/"/tables/reorder", weil eine
    Kachel-Reihenfolge Charts UND Tabellen gemischt enthalten kann."""
    index.reorder_dashboard_pins([(p.item_type, p.item_id) for p in body.pins])
    return {"ok": True}


@app.post("/dashboard/size")
def dashboard_size(body: _ResizeDashboardTileBody) -> dict:
    if body.item_type not in {"chart", "table"}:
        raise HTTPException(status_code=422, detail="Ungültiger Dashboard-Kacheltyp")
    if not index.set_dashboard_pin_size(
        body.item_type, body.item_id, body.grid_cols, body.grid_rows
    ):
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "grid_cols": body.grid_cols, "grid_rows": body.grid_rows}


# ---------------------------------------------------------------------------
# Vergleichstabellen (Konzept "Offene Punkte", Abschnitt "Vergleichstabellen-
# Bereich — Überlegungen") — Vorbild Symcon-Archiv-Vergleichstabellen: Zeilen
# sind Größen (Entität/Gruppe/Formel), Spalten sind Zeiträume. Wie bei den
# Charts ist eine gespeicherte Tabelle nur die STRUKTUR (Spalten-/Zeilen-
# Definition) — die tatsächlichen Zellenwerte berechnet table_editor.html bei
# jedem Aufruf live über /api/query-multi, hier gibt es keinen eigenen
# Aggregations- oder Formel-Code: beides läuft client-seitig (siehe
# static/js/table-editor.js), damit query_series() die einzige Quelle für
# aggregierte Werte bleibt, statt einen zweiten, potenziell abweichenden
# Rechenweg in Python zu pflegen.
#
# "Entitäts-Gruppen" bewusst NICHT als eigenständiges, wiederverwendbares
# Konzept (das Konzept-Dokument nennt das als mögliche Erweiterung, auch für
# Charts) — hier lebt eine Gruppe nur als table_rows.entity_ids einer
# einzelnen "group"-Zeile. Ein eigenständiges entity_groups-Objekt wäre
# verfrühte Abstraktion, solange nur dieser eine Verwendungsfall existiert.
# ---------------------------------------------------------------------------


class _TableColumnBody(BaseModel):
    label: str
    range_key: str
    offset: int = 0
    year_over_year: bool = False


class _TableRowBody(BaseModel):
    label: str
    row_type: str
    entity_ids: list[EntityId] = []
    formula: str = ""
    formula_unit: str = ""
    bold: bool = False


class _TableStyleBody(BaseModel):
    """Rein optische Darstellung einer Vergleichstabelle (Konzept-Erweiterung
    "professionelles UI-Design") — bewusst getrennt von Spalten/Zeilen, siehe
    Kommentar bei saved_tables.style_json in index.py. Keines dieser Felder
    fließt in eine Berechnung ein, nur ins CSS von table_editor.html/den
    Dashboard-Kacheln."""
    zebra: bool = False
    borders: str = "horizontal"
    density: str = "comfortable"
    header_accent: bool = False
    first_col_accent: bool = False
    first_col_bold: bool = False


_TABLE_BORDER_OPTIONS = ("horizontal", "grid", "none")
_TABLE_DENSITY_OPTIONS = ("comfortable", "compact")


class _SaveTableBody(BaseModel):
    name: str
    columns: list[_TableColumnBody]
    rows: list[_TableRowBody]
    style: _TableStyleBody = _TableStyleBody()


@app.get("/tables", response_class=HTMLResponse)
def tables_list(request: Request) -> HTMLResponse:
    tables = index.list_saved_tables()
    return templates.TemplateResponse(request, "tables.html", {"rows": tables})


def _table_editor_context(table: dict | None) -> dict:
    entity_options = [
        {"entity_id": row["entity_id"], "label": row["friendly_name"] or row["entity_id"]}
        for row in index.list_entities()
    ]
    return {
        "table_id": table["id"] if table else None,
        "table_name": table["name"] if table else "",
        "columns": table["columns"] if table else [],
        "rows": table["rows"] if table else [],
        "style": table["style"] if table else {},
        "entity_options": entity_options,
        "range_options": _CHART_RANGE_OPTIONS,
        "base": "..",
    }


@app.get("/tables/new", response_class=HTMLResponse)
def tables_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "table_editor.html", _table_editor_context(None))


def _validate_table_body(body: _SaveTableBody) -> None:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Bitte einen Namen für die Tabelle angeben")
    if not body.columns:
        raise HTTPException(status_code=400, detail="Bitte mindestens eine Spalte anlegen")
    if not body.rows:
        raise HTTPException(status_code=400, detail="Bitte mindestens eine Zeile anlegen")
    for c in body.columns:
        if c.range_key not in dict(_CHART_RANGE_OPTIONS):
            raise HTTPException(status_code=400, detail="Ungültiger Zeitraum in einer Spalte")
    for r in body.rows:
        if r.row_type not in ("entity", "group", "formula", "separator"):
            raise HTTPException(status_code=400, detail="Ungültiger Zeilentyp")
        if r.row_type in ("entity", "group") and not r.entity_ids:
            raise HTTPException(status_code=400, detail=f'Zeile "{r.label}" braucht mindestens eine Entität')
        if r.row_type == "formula" and not r.formula.strip():
            raise HTTPException(status_code=400, detail=f'Zeile "{r.label}" braucht eine Formel')
    if body.style.borders not in _TABLE_BORDER_OPTIONS:
        raise HTTPException(status_code=400, detail="Ungültige Rahmen-Option")
    if body.style.density not in _TABLE_DENSITY_OPTIONS:
        raise HTTPException(status_code=400, detail="Ungültige Dichte-Option")


# Muss VOR "/tables/{table_id}" stehen — dieselbe Begründung wie bei
# "/charts/reorder" oben (sonst würde "new" als table_id-Pfadparameter
# fehlinterpretiert).
@app.post("/tables")
def tables_create(body: _SaveTableBody) -> dict:
    _validate_table_body(body)
    table_id = index.create_saved_table(
        body.name.strip(),
        [c.model_dump() for c in body.columns],
        [r.model_dump() for r in body.rows],
        body.style.model_dump(),
    )
    return {"id": table_id}


@app.get("/tables/{table_id}", response_class=HTMLResponse)
def tables_view(request: Request, table_id: int) -> HTMLResponse:
    table = index.get_saved_table(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    return templates.TemplateResponse(request, "table_editor.html", _table_editor_context(table))


@app.post("/tables/{table_id}")
def tables_update(table_id: int, body: _SaveTableBody) -> dict:
    if index.get_saved_table(table_id) is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    _validate_table_body(body)
    index.update_saved_table(
        table_id,
        body.name.strip(),
        [c.model_dump() for c in body.columns],
        [r.model_dump() for r in body.rows],
        body.style.model_dump(),
    )
    return {"id": table_id}


@app.post("/tables/{table_id}/delete")
def tables_delete(table_id: int) -> dict:
    index.delete_saved_table(table_id)
    return {"ok": True}


@app.post("/tables/{table_id}/favorite")
def tables_favorite_toggle(table_id: int) -> dict:
    table = index.get_saved_table(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    new_state = not table["is_favorite"]
    index.set_table_favorite(table_id, new_state)
    return {"is_favorite": new_state}


@app.post("/tables/{table_id}/pin", response_class=HTMLResponse)
def tables_pin(request: Request, table_id: int) -> HTMLResponse:
    if index.get_saved_table(table_id) is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    index.pin_item_to_dashboard("table", table_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context())


@app.post("/tables/{table_id}/unpin", response_class=HTMLResponse)
def tables_unpin(request: Request, table_id: int) -> HTMLResponse:
    index.unpin_item_from_dashboard("table", table_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context())


@app.get("/entities/{entity_id}", response_class=HTMLResponse)
@_storage_locked(lambda args: args["entity_id"])
def entity_detail(request: Request, entity_id: str) -> HTMLResponse:
    # "base" ist der relative Pfad zurück zur App-Wurzel — unter Ingress hat die Seite
    # einen dynamischen Pfad-Präfix, ein absoluter Pfad ("/api/query") würde daran
    # vorbeizeigen (Konzept Abschnitt 06). /entities/{id} liegt eine Ebene tief.
    entity = _require_entity(entity_id)
    # first_date/last_date grenzen den Kalender-Sprung (Periode-Label anklicken)
    # auf den Zeitraum ein, in dem die Entität überhaupt Daten hat — dieselbe
    # Konvention wie bei entity_cleanup() unten.
    first_date = datetime.fromtimestamp(entity["first_ts"], TZ).strftime("%Y-%m-%d") if entity["first_ts"] else None
    last_date = datetime.fromtimestamp(entity["last_ts"], TZ).strftime("%Y-%m-%d") if entity["last_ts"] else None
    return templates.TemplateResponse(
        request,
        "entity_detail.html",
        {
            "entity_id": entity_id,
            "friendly_name": entity["friendly_name"],
            "aggregation_type": entity["aggregation_type"],
            "type_label": format_type(entity["aggregation_type"]),
            "unit": entity["unit"],
            "decimals": decimals_to_int(entity["decimals"]),
            "display_mode": entity["display_mode"],
            "base": "..",
            "first_date": first_date,
            "last_date": last_date,
            "is_favorite": bool(entity["is_favorite"]),
        },
    )


@app.get("/entities/{entity_id}/cleanup", response_class=HTMLResponse)
@_storage_locked(lambda args: args["entity_id"])
def entity_cleanup(request: Request, entity_id: str) -> HTMLResponse:
    entity = _require_entity(entity_id)
    # first_date/last_date grenzen den Kalender-Sprung auf den Zeitraum ein, in
    # dem die Entität überhaupt Daten hat (Monatsnavigation im Widget) — welche
    # einzelnen Tage INNERHALB dieses Bereichs tatsächlich Daten haben, liefert
    # /entities/{id}/data-days on demand je sichtbarem Monat (Konzept "Offene
    # Punkte": ein natives <input type="date"> konnte das nicht).
    first_date = datetime.fromtimestamp(entity["first_ts"], TZ).strftime("%Y-%m-%d") if entity["first_ts"] else None
    last_date = datetime.fromtimestamp(entity["last_ts"], TZ).strftime("%Y-%m-%d") if entity["last_ts"] else None
    return templates.TemplateResponse(
        request,
        "cleanup.html",
        {
            "entity_id": entity_id,
            "friendly_name": entity["friendly_name"],
            "base": "../..",
            "first_date": first_date,
            "last_date": last_date,
            "gap_detection_enabled": entity["gap_threshold"] != "off",
            "outlier_detection_enabled": entity["outlier_threshold"] != "off",
            "counter_decrease_enabled": entity["state_class"] == "total_increasing",
            "is_favorite": bool(entity["is_favorite"]),
        },
    )


@app.get("/entities/{entity_id}/data-days")
@_storage_locked(lambda args: args["entity_id"])
def entity_data_days(entity_id: str, year: int, month: int) -> dict:
    """Für das Kalender-Widget der Bereinigungs-Seite: welche Tage des
    angefragten Monats haben mindestens einen Rohwert (zur Löschung markierte
    Datensätze ausgeschlossen) — damit das Widget Tage MIT Lücke innerhalb des
    Entität-Zeitraums ausgrauen kann, statt nur den Gesamtzeitraum
    einzugrenzen."""
    _require_entity(entity_id)
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat")
    days_in_month = calendar.monthrange(year, month)[1]
    month_start = datetime(year, month, 1, tzinfo=TZ)
    month_end = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=TZ)
    rows = cleanup.list_raw_rows(
        DATA_DIR, index, entity_id, month_start.timestamp(), month_end.timestamp() + 1, TZ,
        max_rows=MAX_UI_ANALYSIS_ROWS
    )
    days = sorted({datetime.fromtimestamp(ts, TZ).day for ts, _ in rows})
    return {"days": days}


def _paginate(items: list, page: int, page_size: int) -> tuple[list, dict]:
    """Teilt eine Liste in Seiten für Tabellen mit potenziell vielen Zeilen
    (Entitäten-Übersicht und Bereinigung). Die Obergrenze 1000 verhindert eine
    unbegrenzte Materialisierung durch alte ``page_size=0``-URLs oder manuell
    veränderte Requests. page wird auf den gültigen Bereich begrenzt, damit ein
    veralteter Seiten-Wert nach einem Filterwechsel nie eine leere Seite zeigt."""
    total = len(items)
    page_size = 1000 if page_size <= 0 else min(page_size, 1000)
    total_pages = max(1, -(-total // page_size))  # ceil ohne math.ceil-Import
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    return items[start:end], {
        "page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
        "start": start + 1 if total else 0, "end": end,
    }


# Zeiträume der Bereinigungsseite — dieselben Perioden wie im Chart
# (entity_detail.html/query._window(), Konsistenz zwischen beiden Werkzeugen),
# nur ohne "decade" (bei Rohwert-Zeilen wenig sinnvoll) und dafür mit "all" als
# Bereinigungs-spezifischer Ergänzung ohne Chart-Entsprechung.
CLEANUP_RANGE_KEYS = ("hour", "day", "week", "month", "year", "all")

# "Jahr" kann wie "Gesamt" MAX_UI_ANALYSIS_ROWS überschreiten (stiller 413, htmx swappt 4xx nicht ein) — beide laufen über den Streaming-Pfad.
_STREAMING_RANGE_KEYS = ("year", "all")

_MONTH_NAMES_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def _rows_window(range_key: str, offset: int, now: datetime, first_ts: float | None) -> tuple[datetime, datetime]:
    """[Anfang, Ende) für die Bereinigungsseite. Kalendarische Zeiträume kommen
    1:1 aus query._window() — dieselbe Perioden-Logik wie im Chart (offset 0 =
    aktuelle, kalendarisch verankerte Periode bis "jetzt", -1 = eine Periode
    zurück, …). "all" hat dort keine Entsprechung: deckt stattdessen den
    kompletten Datenbestand der Entität ab (seit dem ersten Rohwert) und kennt
    keine Navigation (offset wird vom Aufrufer immer auf 0 gehalten)."""
    if range_key not in CLEANUP_RANGE_KEYS:
        range_key = "day"
    if range_key == "all":
        start = datetime.fromtimestamp(first_ts, TZ) if first_ts else now
        return start, now
    start, end, _period_end = query_mod._window(range_key, now, min(offset, 0), continuous=False)
    return start, end


def _rows_period_label(range_key: str, offset: int, window_start: datetime, window_end: datetime, now: datetime) -> str:
    """Deutsches Zeitraum-Label für die Navigationsleiste — inhaltlich identisch
    zu formatPeriodLabel() in entity_detail.html, hier aber serverseitig, weil
    die Bereinigungsseite ihr Fenster per Formular-Roundtrip statt per
    Alpine-Reaktivität berechnet."""
    display_end = window_end - timedelta(seconds=1)  # window_end ist exklusiv
    if range_key == "hour":
        return f"{window_start.strftime('%d.%m.%Y')} · {window_start.strftime('%H:%M')}–{display_end.strftime('%H:%M')} Uhr"
    if range_key == "day":
        if offset == 0:
            return "Heute"
        if offset == -1:
            return "Gestern"
        return window_start.strftime("%d.%m.%Y")
    if range_key == "week":
        return f"{window_start.strftime('%d.%m.')}–{display_end.strftime('%d.%m.')} {display_end.year}"
    if range_key == "month":
        label = f"{_MONTH_NAMES_DE[window_start.month - 1]} {window_start.year}"
        return f"{label} (bis heute)" if window_end >= now else label
    if range_key == "year":
        return f"{window_start.year} (bis heute)" if window_end >= now else f"{window_start.year}"
    if range_key == "all":
        return f"Gesamter Zeitraum (seit {window_start.strftime('%d.%m.%Y')})"
    return ""


def _rows_fragment(
    request: Request, entity_id: str, filter_: str, range_key: str, offset: int = 0, page: int = 1, page_size: int = 50,
    mode: str = "cleanup",
) -> HTMLResponse:
    entity = index.get_entity(entity_id)
    decimals_int = decimals_to_int(entity["decimals"])
    now = datetime.now(TZ)
    if range_key not in CLEANUP_RANGE_KEYS:
        range_key = "day"
    offset = 0 if range_key == "all" else min(offset, 0)
    window_start, window_end = _rows_window(range_key, offset, now, entity["first_ts"])

    gap_threshold = entity["gap_threshold"]
    outlier_threshold = entity["outlier_threshold"]
    if range_key in _STREAMING_RANGE_KEYS:
        # Können Millionen Rohwerte umfassen: materialisiert nur die angeforderte Seite (zwei Streaming-Durchläufe).
        effective_page_size = 1000 if page_size <= 0 else min(page_size, 1000)

        def rows_factory():
            return cleanup.iter_raw_rows(
                DATA_DIR,
                index,
                entity_id,
                window_start.timestamp(),
                window_end.timestamp(),
                TZ,
                now=now,
            )

        analysis = cleanup.analyze_raw_rows_page(
            rows_factory,
            filter_=filter_,
            page=page,
            page_size=effective_page_size,
            gap_threshold_minutes=(
                None if gap_threshold == "off" else float(gap_threshold)
            ),
            outlier_threshold_percent=(
                None if outlier_threshold == "off" else float(outlier_threshold)
            ),
            decimals=entity["decimals"],
            counter_decrease_enabled=entity["state_class"] == "total_increasing",
        )
        counts = analysis["counts"]
        pagination = analysis["pagination"]
        display_rows = [
            {
                **row,
                "formatted_value": format_value(row["value"], decimals_int),
                "formatted_ts": datetime.fromtimestamp(row["ts"], TZ).strftime(
                    "%d.%m. %H:%M:%S"
                ),
            }
            for row in analysis["rows"]
        ]
    else:
        rows = cleanup.list_raw_rows(
            DATA_DIR,
            index,
            entity_id,
            window_start.timestamp(),
            window_end.timestamp(),
            TZ,
            now=now,
            max_rows=MAX_UI_ANALYSIS_ROWS,
        )
        outliers = cleanup.detect_outliers(
            rows, None if outlier_threshold == "off" else float(outlier_threshold)
        )
        gaps = cleanup.detect_gaps(
            rows, None if gap_threshold == "off" else float(gap_threshold)
        )
        duplicates = cleanup.detect_duplicates(rows)
        repetitions = cleanup.detect_repetitions(rows, entity["decimals"])
        counter_decreases = (
            cleanup.detect_counter_decreases(rows)
            if entity["state_class"] == "total_increasing"
            else {}
        )
        counts = {
            "all": len(rows),
            "outliers": len(outliers),
            "gaps": len(gaps),
            "duplicates": len(duplicates),
            "repetitions": len(repetitions),
            "counter_decreases": len(counter_decreases),
        }
        if filter_ == "outliers":
            rows = [(ts, value) for ts, value in rows if ts in outliers]
        elif filter_ == "gaps":
            rows = [(ts, value) for ts, value in rows if ts in gaps]
        elif filter_ == "duplicates":
            rows = [(ts, value) for ts, value in rows if ts in duplicates]
        elif filter_ == "repetitions":
            rows = [(ts, value) for ts, value in rows if ts in repetitions]
        elif filter_ == "counter_decreases":
            rows = [(ts, value) for ts, value in rows if ts in counter_decreases]

        rows = list(reversed(rows))
        page_rows, pagination = _paginate(rows, page, page_size)
        display_rows = [
            {
                "ts": ts,
                "value": value,
                "formatted_value": format_value(value, decimals_int),
                "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime(
                    "%d.%m. %H:%M:%S"
                ),
                "flags": [
                    {"label": label, "reason": reasons[ts]}
                    for label, reasons in (
                        ("Ausreißer", outliers),
                        ("Lücke", gaps),
                        ("Duplikat", duplicates),
                        ("Wiederholung", repetitions),
                        ("Zählerrückgang", counter_decreases),
                    )
                    if ts in reasons
                ],
            }
            for ts, value in page_rows
        ]
    if mode not in ("cleanup", "correct"):
        mode = "cleanup"

    period_label = _rows_period_label(range_key, offset, window_start, window_end, now)
    first_date = datetime.fromtimestamp(entity["first_ts"], TZ).strftime("%Y-%m-%d") if entity["first_ts"] else None
    last_date = datetime.fromtimestamp(entity["last_ts"], TZ).strftime("%Y-%m-%d") if entity["last_ts"] else None

    return templates.TemplateResponse(
        request,
        "_rows_table.html",
        {
            "entity_id": entity_id,
            "rows": display_rows,
            "filter": filter_,
            "mode": mode,
            "range": range_key,
            "offset": offset,
            "period_label": period_label,
            "window_start_ts": window_start.timestamp(),
            "window_end_ts": window_end.timestamp(),
            "is_current": offset == 0,
            "counts": counts,
            "range_row_count_label": format_int(counts['all']),
            "total_row_count_label": format_int(_visible_row_count(entity)),
            "pagination": pagination,
            "gap_detection_enabled": gap_threshold != "off",
            "outlier_detection_enabled": outlier_threshold != "off",
            "counter_decrease_enabled": entity["state_class"] == "total_increasing",
            "undo_available": bool(index.get_last_deleted_batch(entity_id)),
            "first_date": first_date,
            "last_date": last_date,
            "base": "../..",  # das Fragment wird immer in cleanup.html eingehängt, gleiche Tiefe
        },
    )


@app.get("/entities/{entity_id}/rows", response_class=HTMLResponse)
@_storage_locked(lambda args: args["entity_id"])
def entity_rows(
    request: Request,
    entity_id: str,
    filter: str = "all",
    range_key: str = Query("day", alias="range"),
    offset: int = 0,
    page: int = 1,
    page_size: int = 50,
    mode: str = "cleanup",
) -> HTMLResponse:
    _require_entity(entity_id)
    return _rows_fragment(request, entity_id, filter, range_key, offset, page, page_size, mode)


def _rows_form_common(form) -> tuple[str, str, int, int, int, str]:
    filter_ = str(form.get("filter", "all"))
    range_key = str(form.get("range", "day"))
    offset = int(form.get("offset", 0))
    page = int(form.get("page", 1))
    page_size = int(form.get("page_size", 50))
    mode = str(form.get("mode", "cleanup"))
    return filter_, range_key, offset, page, page_size, mode


@app.post("/entities/{entity_id}/rows/delete", response_class=HTMLResponse)
async def delete_rows(request: Request, entity_id: str) -> HTMLResponse:
    _require_entity(entity_id)
    form = await request.form()
    timestamps = [float(value) for key, value in form.multi_items() if key == "ts"]
    filter_, range_key, offset, page, page_size, mode = _rows_form_common(form)

    def delete_locked() -> HTMLResponse:
        with storage_coordinator.entity(entity_id):
            cleanup.soft_delete(index, entity_id, timestamps)
            return _rows_fragment(request, entity_id, filter_, range_key, offset, page, page_size, mode)

    return await run_in_threadpool(delete_locked)


class _AddValueBody(BaseModel):
    ts: float
    value: float


@app.post("/entities/{entity_id}/rows/add")
@_storage_locked(lambda args: args["entity_id"])
def add_row(entity_id: str, body: _AddValueBody) -> dict:
    """Bearbeitungsbereich, Reiter "Hinzufügen" — fügt einen einzelnen Rohwert
    nachträglich ein (Konzept-Erweiterung), z. B. um eine Lücke zu schließen.
    Reines JSON statt Formular/htmx-Fragment wie bei /rows/delete: der
    "Hinzufügen"-Reiter zeigt keine Zeilen-Tabelle, die neu geladen werden
    müsste — nur eine Erfolgsmeldung im Formular selbst (siehe cleanup.html)."""
    _require_entity(entity_id)
    now = datetime.now(TZ)
    if body.ts <= 0 or body.ts > now.timestamp() + 3600:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitstempel")
    cleanup.add_raw_value(DATA_DIR, index, entity_id, body.ts, body.value, TZ, now=now)
    return {"ok": True}


class _CorrectValueBody(BaseModel):
    ts: float
    old_value: float
    new_value: float


@app.post("/entities/{entity_id}/rows/correct")
@_storage_locked(lambda args: args["entity_id"])
def correct_row(entity_id: str, body: _CorrectValueBody) -> dict:
    """Bearbeitungsbereich, Reiter "Korrigieren" — ändert den Wert EINES
    vorhandenen Rohwerts, identifiziert über (ts, old_value) genau wie die
    Zeile, die der Reiter gerade anzeigt (siehe cleanup.py
    correct_raw_value() für die Duplikat-Vorsicht bei mehreren Vorkommen
    desselben Zeitstempels). Wie /rows/add reines JSON statt htmx-Fragment —
    der Aufrufer (cleanup.html) triggert nach Erfolg selbst ein Neuladen von
    #controls, damit die Tabelle den neuen Wert zeigt."""
    _require_entity(entity_id)
    changed = cleanup.correct_raw_value(
        DATA_DIR, index, entity_id, body.ts, body.old_value, body.new_value, TZ
    )
    if not changed:
        raise HTTPException(status_code=404, detail="Kein passender Rohwert gefunden (evtl. zwischenzeitlich geändert)")
    return {"ok": True}


@app.post("/entities/{entity_id}/rows/undo", response_class=HTMLResponse)
async def undo_rows(request: Request, entity_id: str) -> HTMLResponse:
    _require_entity(entity_id)
    form = await request.form()
    filter_, range_key, offset, page, page_size, mode = _rows_form_common(form)

    def undo_locked() -> HTMLResponse:
        with storage_coordinator.entity(entity_id):
            cleanup.undo_last_delete(index, entity_id)
            return _rows_fragment(request, entity_id, filter_, range_key, offset, page, page_size, mode)

    return await run_in_threadpool(undo_locked)


_UNDO_PREVIEW_LIMIT = 50


@app.post("/entities/{entity_id}/rows/undo-preview", response_class=HTMLResponse)
async def undo_preview(request: Request, entity_id: str) -> HTMLResponse:
    """Zeigt, was "Rückgängig" wiederherstellen würde, ohne etwas zu ändern —
    analog zu duplicates_preview() oben: der eigentliche Undo läuft weiterhin
    über /rows/undo, jetzt aber erst nach Bestätigung statt direkt beim ersten
    Klick. Zeigt genau die zuletzt weich gelöschte Charge (gleicher
    deleted_at-Zeitstempel, siehe Index.get_last_deleted_batch())."""
    entity = _require_entity(entity_id)
    decimals_int = decimals_to_int(entity["decimals"])
    form = await request.form()
    filter_, range_key, offset, page, page_size, mode = _rows_form_common(form)

    def load_preview() -> tuple[list[float], list[tuple[float, float]]]:
        with storage_coordinator.entity(entity_id):
            batch = index.get_last_deleted_batch(entity_id)
            return batch, cleanup.get_raw_values_for_timestamps(DATA_DIR, entity_id, batch, TZ)

    batch_timestamps, values = await run_in_threadpool(load_preview)
    preview_rows = [
        {
            "formatted_value": format_value(value, decimals_int),
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m. %H:%M:%S"),
        }
        for ts, value in list(reversed(values))[:_UNDO_PREVIEW_LIMIT]
    ]

    return templates.TemplateResponse(
        request,
        "_undo_preview.html",
        {
            "entity_id": entity_id,
            "total_to_restore": len(batch_timestamps),
            "preview_rows": preview_rows,
            "truncated": max(0, len(batch_timestamps) - _UNDO_PREVIEW_LIMIT),
            "filter": filter_,
            "range": range_key,
            "offset": offset,
            "page": page,
            "page_size": page_size,
            "base": "../..",
        },
    )


_DUPLICATES_PREVIEW_LIMIT = 50


@app.post("/entities/{entity_id}/rows/duplicates-preview", response_class=HTMLResponse)
async def duplicates_preview(request: Request, entity_id: str) -> HTMLResponse:
    """Berechnet, was "Duplikate automatisch entfernen" löschen würde, ohne
    etwas zu schreiben (Konzept Abschnitt 04) — zeigt die betroffenen Zeilen
    zur Bestätigung, bevor der eigentliche Löschvorgang (weiterhin über
    /rows/delete, mit denselben Zeitstempeln als versteckte Formularfelder)
    ausgelöst wird. Läuft immer über den ganzen aktuellen Zeitraum (range/offset),
    unabhängig vom gerade aktiven Filter-Chip — Duplikate müssen unter allen
    Zeilen gesucht werden, nicht nur den z. B. gerade nach "Ausreißer" gefilterten."""
    entity = _require_entity(entity_id)
    decimals_int = decimals_to_int(entity["decimals"])
    form = await request.form()
    filter_, range_key, offset, page, page_size, mode = _rows_form_common(form)
    now = datetime.now(TZ)
    offset = 0 if range_key == "all" else min(offset, 0)
    window_start, window_end = _rows_window(range_key, offset, now, entity["first_ts"])

    def load_rows() -> list[tuple[float, float]]:
        with storage_coordinator.entity(entity_id):
            return cleanup.list_raw_rows(
                DATA_DIR, index, entity_id, window_start.timestamp(), window_end.timestamp(), TZ,
                now=now, max_rows=MAX_UI_ANALYSIS_ROWS
            )

    rows = await run_in_threadpool(load_rows)
    # duplicate_rows_to_delete() braucht rows weiterhin chronologisch aufsteigend,
    # um korrekt das JEWEILS ÄLTESTE Vorkommen je Zeitstempel zu behalten — erst
    # für die Anzeige unten drehen wir auf neueste-zuerst um (Konzept: Listen mit
    # Werten generell neueste oben), all_timestamps bleibt davon unberührt (die
    # Reihenfolge der versteckten Formularfelder ist für den Löschvorgang egal).
    to_delete = cleanup.duplicate_rows_to_delete(rows)
    preview_rows = [
        {
            "ts": ts,
            "formatted_value": format_value(value, decimals_int),
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m. %H:%M:%S"),
        }
        for ts, value in list(reversed(to_delete))[:_DUPLICATES_PREVIEW_LIMIT]
    ]

    return templates.TemplateResponse(
        request,
        "_duplicates_preview.html",
        {
            "entity_id": entity_id,
            "total_to_delete": len(to_delete),
            "preview_rows": preview_rows,
            "truncated": max(0, len(to_delete) - _DUPLICATES_PREVIEW_LIMIT),
            "all_timestamps": [ts for ts, _ in to_delete],
            "filter": filter_,
            "range": range_key,
            "offset": offset,
            "page": page,
            "page_size": page_size,
            "base": "../..",
        },
    )


_REPETITIONS_PREVIEW_LIMIT = 50


@app.post("/entities/{entity_id}/rows/repetitions-preview", response_class=HTMLResponse)
async def repetitions_preview(request: Request, entity_id: str) -> HTMLResponse:
    """Zeigt gerundet gleiche Folgewerte vor dem Soft-Delete zur Bestätigung."""
    entity = _require_entity(entity_id)
    decimals_int = decimals_to_int(entity["decimals"])
    form = await request.form()
    filter_, range_key, offset, page, page_size, mode = _rows_form_common(form)
    now = datetime.now(TZ)
    offset = 0 if range_key == "all" else min(offset, 0)
    window_start, window_end = _rows_window(range_key, offset, now, entity["first_ts"])

    def load_preview() -> tuple[int, list[tuple[float, float]]]:
        with storage_coordinator.entity(entity_id):
            rows = cleanup.iter_raw_rows(
                DATA_DIR, index, entity_id,
                window_start.timestamp(), window_end.timestamp(), TZ, now=now
            )
            newest: deque[tuple[float, float]] = deque(maxlen=_REPETITIONS_PREVIEW_LIMIT)
            total = 0
            for row in cleanup.iter_repeated_rows(rows, entity["decimals"]):
                newest.append(row)
                total += 1
            return total, list(reversed(newest))

    total_to_delete, newest_rows = await run_in_threadpool(load_preview)
    preview_rows = [
        {
            "formatted_value": format_value(value, decimals_int),
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m. %H:%M:%S"),
        }
        for ts, value in newest_rows
    ]

    return templates.TemplateResponse(
        request,
        "_repetitions_preview.html",
        {
            "entity_id": entity_id,
            "total_to_delete": total_to_delete,
            "preview_rows": preview_rows,
            "truncated": max(0, total_to_delete - _REPETITIONS_PREVIEW_LIMIT),
            "filter": filter_,
            "range": range_key,
            "offset": offset,
            "page": page,
            "page_size": page_size,
            "base": "../..",
        },
    )


@app.post("/entities/{entity_id}/rows/repetitions-delete", response_class=HTMLResponse)
async def repetitions_delete(request: Request, entity_id: str) -> HTMLResponse:
    """Verdichtet den gewählten Zeitraum streaming-basiert und soft-delete-sicher."""
    entity = _require_entity(entity_id)
    form = await request.form()
    filter_, range_key, offset, page, page_size, mode = _rows_form_common(form)
    now = datetime.now(TZ)
    offset = 0 if range_key == "all" else min(offset, 0)
    window_start, window_end = _rows_window(range_key, offset, now, entity["first_ts"])

    def delete_locked() -> HTMLResponse:
        with storage_coordinator.entity(entity_id):
            rows = cleanup.iter_raw_rows(
                DATA_DIR, index, entity_id,
                window_start.timestamp(), window_end.timestamp(), TZ, now=now
            )
            batch: list[float] = []
            deleted_at = time.time()
            for ts, _value in cleanup.iter_repeated_rows(rows, entity["decimals"]):
                batch.append(ts)
                if len(batch) >= 10_000:
                    index.mark_deleted(entity_id, batch, deleted_at=deleted_at)
                    batch = []
            if batch:
                index.mark_deleted(entity_id, batch, deleted_at=deleted_at)
            return _rows_fragment(
                request, entity_id, filter_, range_key, offset, page, page_size, mode
            )

    return await run_in_threadpool(delete_locked)


def _load_symcon_names() -> dict[str, dict[str, str | None]]:
    """Lädt die zuletzt hochgeladene ID→{name, parent, unit}-Zuordnung (siehe
    SYMCON_NAMES_PATH) — leeres dict, falls noch keine settings.json importiert
    wurde oder die Datei nicht lesbar ist (z. B. manuell gelöscht). Normalisiert
    nebenbei eine ältere, noch flache {id: name}-Datei (vor der Parent-
    Auflösung geschrieben) auf dieselbe Form wie eine frische — ein einmal
    hochgeladener Stand soll nicht durch ein Code-Update ungültig werden."""
    if not SYMCON_NAMES_PATH.exists():
        return {}
    try:
        with SYMCON_NAMES_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, dict[str, str | None]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = {
                "name": value.get("name"),
                "parent": value.get("parent"),
                "unit": value.get("unit"),
            }
        elif isinstance(value, str):
            result[key] = {"name": value, "parent": None, "unit": None}
    return result


def _symcon_import_rows(
    variables: list[symcon_import.SymconVariable], names: dict[str, dict[str, str | None]]
) -> list[dict]:
    rows = []
    for v in variables:
        if v.first_ts and v.last_ts:
            period_start = datetime.fromtimestamp(v.first_ts, TZ).strftime("%d.%m.%Y")
            period_end = datetime.fromtimestamp(v.last_ts, TZ).strftime("%d.%m.%Y")
        else:
            period_start = period_end = None
        preview = (
            f"{format_value(v.min_value)} · {format_value(v.max_value)}"
            if v.min_value is not None and v.max_value is not None
            else "—"
        )
        info = names.get(v.variable_id, {})
        rows.append(
            {
                "variable_id": v.variable_id,
                "symcon_name": info.get("name"),
                "symcon_parent": info.get("parent"),
                "symcon_unit": info.get("unit"),
                "readable": v.readable,
                "error": v.error,
                "row_count": format_int(v.row_count),
                "period_start": period_start,
                "period_end": period_end,
                "preview": preview,
            }
        )
    return rows


class _ScanCache:
    """Cache für das Ergebnis von scan_source() (Konzept Abschnitt 04) — ohne
    das würde JEDER Seitenaufruf von /import (und jeder Dry-Run-/Import-Klick)
    den kompletten Symcon-Ordner neu einlesen und jede Rohdatenzeile neu parsen.
    Bei einem echten Export mit zehntausenden Dateien dauert das lange genug,
    dass die Seite ohne Cache bei jedem Reload minutenlang blockiert bzw. im
    Browser wie ein Hänger aussieht — genau das Problem, das die Fortschritts-
    anzeige beim Upload eigentlich schon lösen sollte, aber ohne Cache nur beim
    allerersten Scan half."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.variables: list[symcon_import.SymconVariable] | None = None


_scan_cache = _ScanCache()
# ZIP-Extraktion, Scan, Löschen und Import dürfen denselben Symcon-Quellordner
# nicht gleichzeitig lesen bzw. ersetzen. Diese Sperre ist absichtlich von den
# Archiv-Sperren getrennt; die feste Reihenfolge lautet immer Quelle, danach
# (falls nötig) StorageCoordinator, damit kein Lock-Zyklus entstehen kann.
_import_source_lock = threading.Lock()
_import_admission_lock = threading.Lock()


def _save_scan_cache(variables: list[symcon_import.SymconVariable]) -> None:
    """Schreibt das Scan-Ergebnis nach SYMCON_SCAN_CACHE_PATH — Path-Objekte
    sind nicht direkt JSON-fähig, deshalb je Variable auf Strings abgebildet."""
    data = {
        "import_row_limit": MAX_IMPORT_ROWS_PER_ENTITY,
        "variables": [
            {
                "variable_id": v.variable_id,
                "files": [str(p) for p in v.files],
                "row_count": v.row_count,
                "skipped_rows": v.skipped_rows,
                "first_ts": v.first_ts,
                "last_ts": v.last_ts,
                "min_value": v.min_value,
                "max_value": v.max_value,
                "readable": v.readable,
                "error": v.error,
            }
            for v in variables
        ],
    }
    with SYMCON_SCAN_CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f)


def _load_scan_cache() -> list[symcon_import.SymconVariable] | None:
    """Gegenstück zu _save_scan_cache() — None, wenn keine (oder eine defekte)
    Cache-Datei vorliegt, dann greift der reguläre Hintergrund-Scan als
    Fallback (siehe import_page())."""
    if not SYMCON_SCAN_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(SYMCON_SCAN_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("import_row_limit") != MAX_IMPORT_ROWS_PER_ENTITY:
            return None
        return [
            symcon_import.SymconVariable(
                variable_id=d["variable_id"],
                files=[Path(p) for p in d["files"]],
                row_count=d["row_count"],
                skipped_rows=d.get("skipped_rows", 0),
                first_ts=d["first_ts"],
                last_ts=d["last_ts"],
                min_value=d["min_value"],
                max_value=d["max_value"],
                readable=d["readable"],
                error=d.get("error"),
            )
            for d in data["variables"]
        ]
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        return None


def _import_page_context() -> dict:
    with _scan_cache.lock:
        variables = _scan_cache.variables or []
    entity_options = [
        (row["entity_id"], row["friendly_name"] or row["entity_id"], row["unit"] or "")
        for row in index.list_entities()
    ]
    names = _load_symcon_names()
    return {
        "source_exists": SYMCON_IMPORT_DIR.exists() and any(SYMCON_IMPORT_DIR.iterdir()),
        "rows": _symcon_import_rows(variables, names),
        "entity_options": entity_options,
        "settings_imported": bool(names),
    }


def _cached_variables() -> list[symcon_import.SymconVariable]:
    """Für /import/dry-run und /import/start: nutzt denselben Cache wie die
    Seite selbst statt erneut zu scannen. Der Fallback (synchroner Scan) greift
    nur, wenn beide Endpunkte ohne vorherigen Seitenaufruf angesprochen würden —
    im normalen Ablauf ist der Cache über GET /import längst warm."""
    with _scan_cache.lock:
        if _scan_cache.variables is not None:
            return _scan_cache.variables
    variables = symcon_import.scan_source(SYMCON_IMPORT_DIR)
    with _scan_cache.lock:
        _scan_cache.variables = variables
    _save_scan_cache(variables)
    return variables


class _UploadProgress:
    """Geteilter Fortschritts-Status für Entpacken + Scannen nach einem ZIP-
    Upload (Konzept Abschnitt 04) — beides passiert in einem Hintergrund-Thread,
    /import/upload-progress wird von der hand geschriebenen Upload-JS in
    import.html gepollt (JSON statt htmx-Fragment, weil das reine Byte-Upload
    schon eigenes XHR braucht und beides in einem Ablauf zusammengehört).
    Derselbe Zustand trägt auch den Nur-Scan-Fall (siehe _run_scan_background),
    wenn /import auf einen bereits entpackten, aber noch nicht gescannten
    Ordner trifft — z. B. nach einem Server-Neustart, der den Cache geleert hat."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.phase = ""  # "extracting" | "scanning" | "done" | "error"
        self.done = 0
        self.total = 0
        self.error = ""


_upload_progress = _UploadProgress()


def _run_scan_background() -> None:
    """Scannt SYMCON_IMPORT_DIR im Hintergrund und füllt den Cache — der
    "scanning"-Teil von _run_upload_background(), auch einzeln nutzbar, wenn
    schon entpackte Daten vorliegen, aber (noch) kein Cache existiert."""
    with _upload_progress.lock:
        _upload_progress.running = True
        _upload_progress.phase = "scanning"
        _upload_progress.done = 0
        _upload_progress.total = 0
        _upload_progress.error = ""

    def on_scan_progress(done: int, total: int) -> None:
        with _upload_progress.lock:
            _upload_progress.done = done
            _upload_progress.total = total

    def worker() -> None:
        try:
            with _import_source_lock:
                variables = symcon_import.scan_source(SYMCON_IMPORT_DIR, on_progress=on_scan_progress)
                with _scan_cache.lock:
                    _scan_cache.variables = variables
                _save_scan_cache(variables)
            with _upload_progress.lock:
                _upload_progress.phase = "done"
        except (OSError, ValueError) as exc:
            with _upload_progress.lock:
                _upload_progress.phase = "error"
                _upload_progress.error = f"Quelldaten konnten nicht gescannt werden: {exc}"
        finally:
            with _upload_progress.lock:
                _upload_progress.running = False

    threading.Thread(target=worker, daemon=True).start()


def _run_upload_background(tmp_zip: Path, source_meta: dict) -> None:
    with _upload_progress.lock:
        _upload_progress.running = True
        _upload_progress.phase = "extracting"
        _upload_progress.done = 0
        _upload_progress.total = 0
        _upload_progress.error = ""

    def on_extract_progress(done: int, total: int) -> None:
        with _upload_progress.lock:
            _upload_progress.done = done
            _upload_progress.total = total

    def on_scan_progress(done: int, total: int) -> None:
        with _upload_progress.lock:
            _upload_progress.done = done
            _upload_progress.total = total

    def worker() -> None:
        try:
            with _import_source_lock:
                symcon_import.extract_zip(tmp_zip, SYMCON_IMPORT_DIR, on_progress=on_extract_progress)
                with _upload_progress.lock:
                    _upload_progress.phase = "scanning"
                    _upload_progress.done = 0
                    _upload_progress.total = 0
                variables = symcon_import.scan_source(SYMCON_IMPORT_DIR, on_progress=on_scan_progress)
                with _scan_cache.lock:
                    _scan_cache.variables = variables
                _save_scan_cache(variables)
                temporary_meta = SYMCON_SOURCE_META_PATH.with_suffix(".json.part")
                try:
                    temporary_meta.write_text(
                        json.dumps(source_meta, ensure_ascii=False), encoding="utf-8"
                    )
                    temporary_meta.replace(SYMCON_SOURCE_META_PATH)
                finally:
                    temporary_meta.unlink(missing_ok=True)
            with _upload_progress.lock:
                _upload_progress.phase = "done"
        except (zipfile.BadZipFile, ValueError) as exc:
            with _upload_progress.lock:
                _upload_progress.phase = "error"
                _upload_progress.error = f"ZIP konnte nicht verarbeitet werden: {exc}"
        finally:
            tmp_zip.unlink(missing_ok=True)
            with _upload_progress.lock:
                _upload_progress.running = False

    threading.Thread(target=worker, daemon=True).start()


@app.get("/import", response_class=HTMLResponse)
def import_page(
    request: Request,
    tab: str = "symcon",
    source: str = "all",
    status: str = "all",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    sort: str = "finished_at",
    dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> HTMLResponse:
    """Symcon-Import-Assistent (Konzept Abschnitt 03) — der db-Ordner kommt als
    ZIP-Upload über die Oberfläche (kein Bind-Mount mehr nötig), entpackt unter
    SYMCON_IMPORT_DIR und bleibt dort liegen, bis er explizit über /import/delete
    entfernt wird. Keine Klarnamen im Ordner, deshalb Werte-Vorschau statt
    Namensvorschlägen; Zuordnung per Dropdown.

    Trifft der Aufruf auf bereits entpackte Daten, für die der In-Memory-Cache
    (noch) leer ist — z. B. direkt nach einem Server-Neustart, der ihn immer
    leert —, wird zuerst der auf Platte gesicherte Scan aus einem früheren
    Lauf geladen (schnell, synchron, kein erneutes Einlesen des ganzen
    Symcon-Ordners nötig). Nur wenn auch keine Cache-Datei vorliegt (z. B.
    wirklich der erste Scan), läuft der eigentliche Scan im Hintergrund und
    die Seite zeigt bis dahin dieselbe Fortschrittsanzeige wie nach einem
    Upload — synchron würde das bei einem großen Export die Seite für die
    volle Scan-Dauer blockieren."""
    active_tab = tab if tab in {"symcon", "csv", "reports"} else "symcon"
    common_context = {
        "active_import_tab": active_tab,
        **_reports_context(source, status, search, date_from, date_to, sort, dir, page, page_size),
    }
    source_exists = SYMCON_IMPORT_DIR.exists() and any(SYMCON_IMPORT_DIR.iterdir())
    if source_exists:
        with _scan_cache.lock:
            cached = _scan_cache.variables is not None
        if not cached:
            from_disk = _load_scan_cache()
            if from_disk is not None:
                with _scan_cache.lock:
                    _scan_cache.variables = from_disk
                cached = True
        if not cached:
            with _upload_progress.lock:
                already_running = _upload_progress.running
            if not already_running:
                _run_scan_background()
            return templates.TemplateResponse(
                request,
                "import.html",
                {
                    "scanning": True,
                    "source_exists": True,
                    "rows": [],
                    "entity_options": [],
                    "settings_imported": bool(_load_symcon_names()),
                    **_csv_import_context(),
                    **common_context,
                },
            )
    return templates.TemplateResponse(
        request,
        "import.html",
        {**_import_page_context(), **_csv_import_context(), **common_context},
    )


@app.post("/import/upload")
async def import_upload(file: UploadFile = File(...)) -> dict:
    """Nimmt das hochgeladene ZIP entgegen (der Byte-Transfer selbst zeigt über
    XHR-Upload-Events schon Fortschritt in import.html) und startet Entpacken +
    Scannen im Hintergrund — /import/upload-progress liefert von dort an den
    Fortschritt, damit ein ZIP mit tausenden Dateien nicht wie ein Hänger
    aussieht, während der Server noch beschäftigt ist."""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Bitte eine ZIP-Datei hochladen")
    with _import_admission_lock:
        with _upload_progress.lock:
            upload_running = _upload_progress.running
        with _import_progress.lock:
            import_running = _import_progress.running
        if upload_running or import_running:
            raise HTTPException(status_code=409, detail="Ein Upload, Scan oder Import läuft bereits")
        with _upload_progress.lock:
            _upload_progress.running = True
            _upload_progress.phase = "receiving"
    tmp_zip = DATA_DIR / "_symcon_upload.zip"
    try:
        await run_in_threadpool(
            _copy_upload_limited, file.file, tmp_zip, MAX_ZIP_UPLOAD_BYTES
        )
    except UploadLimitExceeded as exc:
        with _upload_progress.lock:
            _upload_progress.running = False
            _upload_progress.phase = "error"
            _upload_progress.error = str(exc)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception:
        with _upload_progress.lock:
            _upload_progress.running = False
            _upload_progress.phase = "error"
        raise
    with _scan_cache.lock:
        _scan_cache.variables = None  # Ein neuer Upload macht den bisherigen Cache ungültig.
    logger.info("Symcon-ZIP empfangen · Größe=%s", format_size(tmp_zip.stat().st_size))
    _run_upload_background(
        tmp_zip,
        {"filename": Path(file.filename).name, "size_bytes": tmp_zip.stat().st_size},
    )
    return {"ok": True}


@app.get("/import/upload-progress")
def import_upload_progress() -> dict:
    """Wird per fetch()-Polling aus import.html aufgerufen, solange Entpacken/
    Scannen im Hintergrund läuft (Konzept Abschnitt 04)."""
    with _upload_progress.lock:
        return {
            "running": _upload_progress.running,
            "phase": _upload_progress.phase,
            "done": _upload_progress.done,
            "total": _upload_progress.total,
            "error": _upload_progress.error,
        }


@app.post("/import/settings-upload")
async def import_settings_upload(file: UploadFile = File(...)) -> dict:
    """Optionaler Zusatz-Upload zum db-ZIP: Symcons settings.json (Objektbaum-
    Export) liefert Klarnamen je Variablen-ID (Konzept "Offene Punkte") — rein
    informativ für die Namensspalte im Import-Assistenten, ändert an Zuordnung/
    Import selbst nichts. Anders als der ZIP-Upload synchron: settings.json ist
    reiner JSON-Text, das Parsen dauert auch bei großen Symcon-Installationen
    nur Millisekunden, eine Fortschrittsanzeige wäre hier unnötige Komplexität."""
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Bitte eine JSON-Datei hochladen")
    tmp_json = DATA_DIR / "_symcon_settings_upload.json"
    try:
        await run_in_threadpool(
            _copy_upload_limited,
            file.file,
            tmp_json,
            MAX_SETTINGS_UPLOAD_BYTES,
        )
        raw = await run_in_threadpool(tmp_json.read_bytes)
    except UploadLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    finally:
        tmp_json.unlink(missing_ok=True)
    try:
        names = symcon_import.extract_variable_names(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Ungültiges JSON: {exc}") from exc
    if not names:
        raise HTTPException(
            status_code=400, detail="Keine Variablen-Namen gefunden — ist das wirklich die settings.json?"
        )
    def store_names() -> None:
        with _import_source_lock:
            with storage_coordinator.exclusive():
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                tmp_names = SYMCON_NAMES_PATH.with_suffix(".json.part")
                try:
                    with tmp_names.open("w", encoding="utf-8") as f:
                        json.dump(names, f)
                    tmp_names.replace(SYMCON_NAMES_PATH)
                finally:
                    tmp_names.unlink(missing_ok=True)

    await run_in_threadpool(store_names)
    return {"ok": True, "count": len(names)}


@app.post("/import/delete", response_class=RedirectResponse)
def import_delete(request: Request) -> RedirectResponse:
    """Entfernt die entpackten Symcon-Daten wieder (Konzept Abschnitt 03: bleiben
    sonst erhalten, damit Zuordnung/Dry Run beliebig oft wiederholbar sind, ohne
    jedes Mal neu hochladen zu müssen) — inklusive der aus einer settings.json
    abgeleiteten Namens-Zuordnung (Konzept "Offene Punkte"), falls eine
    importiert wurde: "Daten löschen" ist der bewusste, komplette Reset für
    diese Import-Sitzung, nicht nur für den db-Ordner."""
    with _import_source_lock:
        with storage_coordinator.exclusive():
            symcon_import.delete_source(SYMCON_IMPORT_DIR)
            SYMCON_NAMES_PATH.unlink(missing_ok=True)
            SYMCON_SOURCE_META_PATH.unlink(missing_ok=True)
            SYMCON_SCAN_CACHE_PATH.unlink(missing_ok=True)
            with _scan_cache.lock:
                _scan_cache.variables = None
    # Post/Redirect/Get: Die vollständige Importseite darf nicht direkt unter
    # /import/delete gerendert werden. Ihre relativen CSS-/JS-Pfade würden dort
    # zu /import/static/... aufgelöst und ein Reload würde den Lösch-POST erneut
    # absenden. app_root berücksichtigt dabei Home Assistants Ingress-Präfix.
    app_root = _app_root_context(request)["app_root"]
    return RedirectResponse(url=f"{app_root}/import", status_code=303)


class _ImportProgress:
    """Geteilter Fortschritts-Status für den im Hintergrund-Thread laufenden
    Import (Konzept Abschnitt 04) — /import/progress pollt das per htmx, damit
    der Import-Assistent nicht auf einen einzigen, potenziell lange blockierenden
    Request warten muss (z. B. hunderte Monate, Millionen Zeilen). Zwei Phasen,
    beide sichtbar: "planning" (plan_import() je Variable, liest dafür schon
    alle Rohdaten — bei vielen Variablen selbst nicht mehr trivial schnell) und
    "importing" (der eigentliche Schreibvorgang)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = False
        self.running = False
        self.phase = ""  # "planning" | "importing"
        self.total_variables = 0
        self.planned_variables = 0
        self.total_months = 0
        self.done_months = 0
        self.rows_imported = 0
        self.current_variable = ""
        self.results: list[symcon_import.ImportResult] = []
        self.errors: list[str] = []


_import_progress = _ImportProgress()


def _run_import_background(
    mapped: list[tuple[symcon_import.SymconVariable, str, float]]
) -> None:
    """Startet Planung und Schreibvorgang komplett im Hintergrund-Thread, damit
    /import/start sofort zurückkehrt — schon plan_import() liest für jede
    Variable alle Rohdaten neu ein und kann bei vielen zugeordneten Variablen
    spürbar dauern; würde das synchron vor der Antwort laufen, sähe der Import
    bei genug Variablen ohne jede Rückmeldung erst nach einer Weile "fertig" aus."""
    started_at = datetime.now(timezone.utc)
    names = _load_symcon_names()
    try:
        source_meta = json.loads(SYMCON_SOURCE_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        source_meta = {"filename": "Symcon-db-ZIP", "size_bytes": _dir_size(SYMCON_IMPORT_DIR)}
    target_units = {}
    for _, target, _ in mapped:
        entity = index.get_entity(target)
        target_units[target] = entity["unit"] if entity is not None else None
    configuration = {
        "settings_imported": bool(names),
        "mappings": [
            {
                "variable_id": variable.variable_id,
                "symcon_name": names.get(variable.variable_id, {}).get("name"),
                "symcon_parent": names.get(variable.variable_id, {}).get("parent"),
                "symcon_unit": names.get(variable.variable_id, {}).get("unit"),
                "entity_id": target,
                "target_unit": target_units[target],
                "factor": factor,
            }
            for variable, target, factor in mapped
        ],
    }

    # Erst nach erfolgreicher Vorbereitung als laufend markieren. Schlägt z. B.
    # das Auflösen einer Zielentität fehl, darf kein Phantom-Import im Status
    # hängen bleiben, der alle weiteren Startversuche bis zum Neustart blockiert.
    with _import_progress.lock:
        _import_progress.started = True
        _import_progress.running = True
        _import_progress.phase = "planning"
        _import_progress.total_variables = len(mapped)
        _import_progress.planned_variables = 0
        _import_progress.total_months = 0
        _import_progress.done_months = 0
        _import_progress.rows_imported = 0
        _import_progress.current_variable = ""
        _import_progress.results = []
        _import_progress.errors = []

    def worker() -> dict | None:
        plans: list[tuple[symcon_import.SymconVariable, str, float]] = []
        total_months = 0
        for variable, target_entity_id, factor in mapped:
            with _import_progress.lock:
                _import_progress.current_variable = variable.variable_id
            try:
                plan = symcon_import.plan_import(
                    DATA_DIR, index, variable, target_entity_id, TZ, factor=factor
                )
                total_months += len(plan.months_to_import) + len(plan.months_to_merge)
            except ValueError:
                pass  # scheitert gleich nochmal in der Import-Phase, landet dann in errors
            plans.append((variable, target_entity_id, factor))
            with _import_progress.lock:
                _import_progress.planned_variables += 1
                _import_progress.total_months = total_months

        with _import_progress.lock:
            _import_progress.phase = "importing"
            _import_progress.current_variable = ""

        for variable, target_entity_id, factor in plans:
            with _import_progress.lock:
                _import_progress.current_variable = variable.variable_id

            def on_month_done(label: str, row_count: int) -> None:
                with _import_progress.lock:
                    _import_progress.done_months += 1
                    _import_progress.rows_imported += row_count

            try:
                result = symcon_import.import_variable(
                    DATA_DIR,
                    index,
                    variable,
                    target_entity_id,
                    TZ,
                    on_month_done=on_month_done,
                    factor=factor,
                )
                with _import_progress.lock:
                    _import_progress.results.append(result)
            except ValueError:
                with _import_progress.lock:
                    _import_progress.errors.append(
                        f"{variable.variable_id} → {target_entity_id}: Entität nicht gefunden"
                    )
        return None

    def coordinated_import_worker() -> None:
        reconciliation_report = None
        try:
            with _import_source_lock:
                with storage_coordinator.exclusive():
                    worker()
                    reconciliation_report = _run_storage_reconciliation(
                        entity_ids=sorted({target for _, target, _ in mapped}), repair=True
                    )
        except Exception as exc:
            logger.exception("Symcon-Import unerwartet fehlgeschlagen")
            with _import_progress.lock:
                _import_progress.errors.append(f"Import abgebrochen: {exc}")
        finally:
            with _import_progress.lock:
                results = [dataclasses.asdict(result) for result in _import_progress.results]
                errors = list(_import_progress.errors)
            try:
                with storage_coordinator.exclusive():
                    import_reports.create(
                        DATA_DIR,
                        source_type="symcon",
                        started_at=started_at,
                        source=source_meta,
                        configuration=configuration,
                        results=results,
                        errors=errors,
                        reconciliation=reconciliation_report,
                    )
            except Exception:
                logger.exception("Symcon-Importreport konnte nicht gespeichert werden")
            finally:
                with _import_progress.lock:
                    _import_progress.running = False

    threading.Thread(target=coordinated_import_worker, daemon=True).start()


def _import_progress_context() -> dict:
    with _import_progress.lock:
        phase = _import_progress.phase
        total_vars = _import_progress.total_variables
        planned_vars = _import_progress.planned_variables
        total_months = _import_progress.total_months
        done_months = _import_progress.done_months
        if phase == "planning":
            percent = int(planned_vars / total_vars * 100) if total_vars else 0
        else:
            percent = int(done_months / total_months * 100) if total_months else 100
        return {
            "running": _import_progress.running,
            "phase": phase,
            "total_variables": total_vars,
            "planned_variables": planned_vars,
            "total_months": total_months,
            "done_months": done_months,
            "percent": percent,
            "rows_imported": format_int(_import_progress.rows_imported),
            "current_variable": _import_progress.current_variable,
            "results": list(_import_progress.results),
            "errors": list(_import_progress.errors),
        }


def _mapped_variables(form) -> list[tuple[symcon_import.SymconVariable, str, float]]:
    """Liest die map_<id>-Felder aus dem Formular und löst sie gegen die aktuell
    gescannten Variablen auf — "" und "__ignore__" heißen beide "überspringen".
    Nutzt den Scan-Cache (siehe _import_page_context) statt bei jedem Dry-Run-/
    Import-Klick neu zu scannen."""
    variables = {v.variable_id: v for v in _cached_variables()}
    mapped = []
    for key, value in form.multi_items():
        if not key.startswith("map_"):
            continue
        target_entity_id = str(value).strip()
        if not target_entity_id or target_entity_id == "__ignore__":
            continue
        variable_id = key[len("map_") :]
        variable = variables.get(variable_id)
        if variable is None or not variable.readable:
            continue
        raw_factor = str(form.get(f"factor_{variable_id}", "1"))
        try:
            factor = parse_localized_number(raw_factor)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Ungültiger Faktor für Symcon-ID {variable_id}"
            ) from exc
        if not math.isfinite(factor) or factor == 0 or abs(factor) > 1_000_000_000_000:
            raise HTTPException(
                status_code=400, detail=f"Ungültiger Faktor für Symcon-ID {variable_id}"
            )
        mapped.append((variable, target_entity_id, factor))
    return mapped


@app.post("/import/dry-run", response_class=HTMLResponse)
async def import_dry_run(request: Request) -> HTMLResponse:
    """Vorschau ohne Schreibvorgang (Konzept Abschnitt 03) — beliebig oft
    wiederholbar, z. B. nach einer geänderten Zuordnung."""
    form = await request.form()
    def plan_locked():
        with _import_source_lock:
            mapped = _mapped_variables(form)
            with storage_coordinator.entities([target for _, target, _ in mapped]):
                plans = []
                errors = []
                for variable, target_entity_id, factor in mapped:
                    try:
                        plans.append(
                            symcon_import.plan_import(
                                DATA_DIR,
                                index,
                                variable,
                                target_entity_id,
                                TZ,
                                factor=factor,
                            )
                        )
                    except ValueError:
                        errors.append(f"{variable.variable_id} → {target_entity_id}: Entität nicht gefunden")
                return plans, errors

    plans, errors = await run_in_threadpool(plan_locked)
    return templates.TemplateResponse(
        request, "_import_dry_run.html", {"plans": plans, "errors": errors}
    )


@app.post("/import/start", response_class=HTMLResponse)
async def import_start(request: Request) -> HTMLResponse:
    """Startet den Import der zugeordneten Variablen im Hintergrund (Konzept
    Abschnitt 04) und liefert sofort die Fortschrittsanzeige zurück, statt auf
    den kompletten Schreibvorgang zu warten — bei hunderten Monaten und
    Millionen Zeilen kann das sonst minutenlang blockieren. Nie destruktiv:
    import_variable() überspringt jeden Monat, der mit bereits vorhandenen
    Zeitarchiv-Daten überlappt oder für den schon eine Archivdatei existiert,
    statt sie zu überschreiben. Dieselbe Klassifizierung wie /import/dry-run,
    damit Vorschau und Ergebnis nie auseinanderlaufen."""
    form = await request.form()
    def admit_import() -> None:
        with _import_admission_lock:
            with _upload_progress.lock:
                upload_running = _upload_progress.running
            with _import_progress.lock:
                already_running = _import_progress.running
            if upload_running:
                raise HTTPException(status_code=409, detail="Ein Upload oder Scan läuft bereits")
            if not already_running:
                with _import_source_lock:
                    _run_import_background(_mapped_variables(form))

    await run_in_threadpool(admit_import)
    return templates.TemplateResponse(request, "_import_progress.html", _import_progress_context())


@app.get("/import/progress", response_class=HTMLResponse)
def import_progress(request: Request) -> HTMLResponse:
    """Wird per htmx-Polling alle 500ms aufgerufen, solange der Hintergrund-
    Import läuft (Konzept Abschnitt 04) — liefert entweder die Fortschritts-
    anzeige (löst weiteres Polling aus) oder, sobald fertig, das Endergebnis
    ohne hx-trigger, was das Polling automatisch stoppt."""
    with _import_progress.lock:
        started = _import_progress.started
    ctx = _import_progress_context()
    if not started:
        return HTMLResponse("")
    if ctx["running"]:
        return templates.TemplateResponse(request, "_import_progress.html", ctx)
    return templates.TemplateResponse(request, "_import_result.html", ctx)


# ---------------------------------------------------------------------------
# Eigener CSV-Import (Konzept "Offene Punkte") — bewusst als eigener, klar
# abgetrennter Abschnitt: eine Datei, freie Spalten-/Format-Zuordnung, ein
# Ziel-Entität, ganz anders im Ablauf als der Symcon-Assistent oben, auch wenn
# beide dieselbe nie-destruktive Monats-Klassifizierung teilen
# (symcon_import.plan_import_rows()/import_rows()).
# ---------------------------------------------------------------------------


def _csv_uploaded_path() -> Path | None:
    if not CSV_IMPORT_DIR.exists():
        return None
    files = sorted(p for p in CSV_IMPORT_DIR.iterdir() if p.is_file())
    return files[0] if files else None


def _csv_import_context(
    delimiter: str | None = None,
    has_header: bool | None = None,
    ts_col: int | None = None,
    value_col: int | None = None,
    ts_format: str = "unix_s",
    custom_pattern: str = "",
    entity_id: str = "",
) -> dict:
    entity_options = [
        (row["entity_id"], row["friendly_name"] or row["entity_id"], row["unit"] or "")
        for row in index.list_entities()
    ]
    base = {
        "entity_options": entity_options,
        "delimiter_options": list(csv_import.DELIMITERS.items()),
        "ts_format_options": list(csv_import.TIMESTAMP_FORMATS.items()),
    }
    path = _csv_uploaded_path()
    if path is None:
        return {**base, "csv_uploaded": False}

    # Nur beim allerersten Aufruf nach einem Upload wird geraten (delimiter/
    # has_header noch None) — sobald die Vorschau-Form einmal abgeschickt wurde,
    # gilt immer der explizit übergebene Wert, nie wieder die Schätzung.
    resolved_delimiter = delimiter if delimiter else csv_import.sniff_delimiter(path)
    resolved_has_header = (
        csv_import.sniff_has_header(path, resolved_delimiter) if has_header is None else has_header
    )
    prev = csv_import.preview(path, resolved_delimiter, resolved_has_header)
    default_value_col = 1 if len(prev.columns) > 1 else 0
    return {
        **base,
        "csv_uploaded": True,
        "csv_filename": path.name,
        "delimiter": resolved_delimiter,
        "has_header": resolved_has_header,
        "columns": prev.columns,
        "sample_rows": prev.sample_rows,
        "total_lines": prev.total_lines,
        "ts_col": ts_col if ts_col is not None else 0,
        "value_col": value_col if value_col is not None else default_value_col,
        "ts_format": ts_format,
        "custom_pattern": custom_pattern,
        "entity_id": entity_id,
    }


@app.post("/import/csv/upload", response_class=HTMLResponse)
async def import_csv_upload(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
    """Reiner Byte-Upload, keine Hintergrund-Verarbeitung nötig (anders als der
    Symcon-ZIP): eine einzelne CSV-Datei ist klein genug, dass Speichern +
    Trennzeichen-/Kopfzeilen-Schätzung synchron passieren können, ohne wie ein
    Hänger zu wirken — läuft deshalb direkt als htmx-Multipart-Post, keine
    eigene XHR-Fortschrittsanzeige wie beim (potenziell riesigen) Symcon-ZIP."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Bitte eine CSV-Datei hochladen")
    staging = DATA_DIR / "_csv_upload"
    try:
        await run_in_threadpool(
            _copy_upload_limited, file.file, staging, MAX_CSV_UPLOAD_BYTES
        )
    except UploadLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    def install_staging() -> None:
        with storage_coordinator.exclusive():
            if CSV_IMPORT_DIR.exists():
                shutil.rmtree(CSV_IMPORT_DIR)
            CSV_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
            # .name statt des rohen Dateinamens: derselbe Zip-Slip-Vorsichtsgedanke
            # wie bei extract_zip() — Upload-Dateinamen sind nicht vertrauenswürdig.
            dest = CSV_IMPORT_DIR / Path(file.filename).name
            staging.replace(dest)

    try:
        await run_in_threadpool(install_staging)
    finally:
        staging.unlink(missing_ok=True)
    installed_csv = next(CSV_IMPORT_DIR.iterdir(), None)
    logger.info(
        "CSV-Datei empfangen · Größe=%s",
        format_size(installed_csv.stat().st_size) if installed_csv and installed_csv.is_file() else "—",
    )
    return templates.TemplateResponse(request, "_csv_import_section.html", _csv_import_context())


@app.post("/import/csv/delete", response_class=HTMLResponse)
def import_csv_delete(request: Request) -> HTMLResponse:
    with storage_coordinator.exclusive():
        if CSV_IMPORT_DIR.exists():
            shutil.rmtree(CSV_IMPORT_DIR)
    return templates.TemplateResponse(request, "_csv_import_section.html", _csv_import_context())


def _csv_form_params(form) -> tuple[str, bool, int, int, str, str, str]:
    delimiter = str(form.get("delimiter") or ",")
    has_header = form.get("has_header") == "on"
    try:
        ts_col = int(form.get("ts_col", 0))
    except (TypeError, ValueError):
        ts_col = 0
    try:
        value_col = int(form.get("value_col", 0))
    except (TypeError, ValueError):
        value_col = 0
    ts_format = str(form.get("ts_format") or "unix_s")
    custom_pattern = str(form.get("custom_pattern") or "")
    entity_id = str(form.get("entity_id") or "").strip()
    return delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id


@app.post("/import/csv/preview", response_class=HTMLResponse)
async def import_csv_preview(request: Request) -> HTMLResponse:
    """Aktualisiert Spalten-Vorschau + Auswahlfelder live, wenn Trennzeichen/
    Kopfzeile/Format geändert werden — vor dem eigentlichen Dry Run/Import,
    damit die Spaltenzuordnung sichtbar richtig sitzt, bevor irgendetwas
    gelesen/geschrieben wird."""
    form = await request.form()
    delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id = _csv_form_params(form)
    def preview_locked() -> dict:
        with storage_coordinator.exclusive():
            return _csv_import_context(
                delimiter=delimiter,
                has_header=has_header,
                ts_col=ts_col,
                value_col=value_col,
                ts_format=ts_format,
                custom_pattern=custom_pattern,
                entity_id=entity_id,
            )

    ctx = await run_in_threadpool(preview_locked)
    return templates.TemplateResponse(request, "_csv_import_section.html", ctx)


@app.post("/import/csv/dry-run", response_class=HTMLResponse)
async def import_csv_dry_run(request: Request) -> HTMLResponse:
    """Vorschau ohne Schreibvorgang — reicht dieselbe ImportPlan-Vorlage wie
    der Symcon-Import (_import_dry_run.html), da plan_import_rows() dieselbe
    generische ImportPlan-Struktur zurückgibt."""
    form = await request.form()
    delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id = _csv_form_params(form)
    plans: list[symcon_import.ImportPlan] = []
    errors: list[str] = []
    if not entity_id:
        errors.append("Bitte eine Ziel-Entität auswählen.")
    else:
        def plan_csv_locked():
            with storage_coordinator.entity(entity_id):
                path = _csv_uploaded_path()
                if path is None:
                    return None
                parsed = csv_import.parse_rows(
                    path, delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, TZ
                )
                return symcon_import.plan_import_rows(
                    DATA_DIR, index, parsed.rows, entity_id, TZ,
                    source_label=path.name, skipped_rows=parsed.skipped
                )

        try:
            plan = await run_in_threadpool(plan_csv_locked)
            if plan is None:
                errors.append("Keine CSV-Datei hochgeladen.")
            else:
                plans.append(plan)
        except ValueError as exc:
            errors.append(str(exc))
    return templates.TemplateResponse(request, "_import_dry_run.html", {"plans": plans, "errors": errors})


@app.post("/import/csv/start", response_class=HTMLResponse)
async def import_csv_start(request: Request) -> HTMLResponse:
    """Schreibt synchron (anders als der Symcon-Import kein Hintergrund-Thread
    nötig): eine einzelne Datei/Entität ist vom Umfang her vergleichbar mit
    EINER Symcon-Variable, für die der Symcon-Import ebenfalls ohne spürbare
    Verzögerung durchläuft. Nie destruktiv — dieselbe Monats-Klassifizierung
    (import_rows()) wie beim Symcon-Import, derselbe Dry-Run vorher möglich."""
    started_at = datetime.now(timezone.utc)
    form = await request.form()
    delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id = _csv_form_params(form)
    results: list[symcon_import.ImportResult] = []
    errors: list[str] = []
    reconciliation_report = None
    source_path = _csv_uploaded_path()
    if not entity_id:
        errors.append("Bitte eine Ziel-Entität auswählen.")
    else:
        def execute_csv_import():
            with storage_coordinator.exclusive():
                path = _csv_uploaded_path()
                if path is None:
                    raise ValueError("Keine CSV-Datei hochgeladen.")
                parsed = csv_import.parse_rows(
                    path,
                    delimiter,
                    has_header,
                    ts_col,
                    value_col,
                    ts_format,
                    custom_pattern,
                    TZ,
                )
                result = symcon_import.import_rows(
                    DATA_DIR,
                    index,
                    parsed.rows,
                    entity_id,
                    TZ,
                    source_label=path.name,
                    skipped_rows=parsed.skipped,
                )
                reconciliation = _run_storage_reconciliation(entity_ids=[entity_id], repair=True)
                return result, reconciliation

        try:
            result, reconciliation_report = await run_in_threadpool(execute_csv_import)
            results.append(result)
        except ValueError as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.exception("CSV-Import unerwartet fehlgeschlagen")
            errors.append(f"Import abgebrochen: {exc}")
    try:
        with storage_coordinator.exclusive():
            import_reports.create(
                DATA_DIR,
                source_type="csv",
                started_at=started_at,
                source={
                    "filename": source_path.name if source_path else None,
                    "size_bytes": source_path.stat().st_size if source_path and source_path.is_file() else 0,
                },
                configuration={
                    "delimiter": delimiter,
                    "has_header": has_header,
                    "timestamp_column": ts_col,
                    "value_column": value_col,
                    "timestamp_format": ts_format,
                    "custom_pattern": custom_pattern if ts_format == "custom" else "",
                    "entity_id": entity_id,
                },
                results=[dataclasses.asdict(result) for result in results],
                errors=errors,
                reconciliation=reconciliation_report,
            )
    except Exception:
        logger.exception("CSV-Importreport konnte nicht gespeichert werden")
    return templates.TemplateResponse(request, "_import_result.html", {"results": results, "errors": errors})
