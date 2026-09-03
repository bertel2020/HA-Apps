"""Zeitarchiv-App: FastAPI-Anwendung und -Instanz (`app`).

Enthält die UI-Seitenrouten (Übersicht, Entitäten, Charts, Tabellen,
Dashboards, Einstellungen, Backup) sowie gemeinsam genutzten Zustand
(Index, StorageCoordinator, IngestionService, Zeitzone). `/api/*`,
Import-Reports und der Symcon-/CSV-/Home-Assistant-Import sind bewusst in
eigene Module ausgelagert (api_routes.py, report_routes.py,
import_routes.py) — siehe test_route_modules.py für den Architekturvertrag
dahinter. Details zum Gesamtaufbau: docs/architecture.md.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import platform
import secrets
import shutil
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (
    FileResponse,
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
    entity_display_name,
    format_int,
    format_resolution,
    format_retention,
    format_size,
    format_time,
    format_timestamp,
    format_type,
    format_uptime,
    format_value,
)
from .limits import (
    MAX_CSV_UPLOAD_BYTES,
    MAX_EXPORT_ROWS,
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
    cleanup,
    entity_removal,
    hotbuffer,
    import_reports,
    retention as retention_mod,
    reconcile,
    rotate,
)
from .storage import query as query_mod
from .storage.index import (
    DEFAULT_DECIMALS,
    DEFAULT_GAP_THRESHOLD,
    DEFAULT_OUTLIER_THRESHOLD,
    DEFAULT_RESOLUTION,
    DEFAULT_RETENTION,
    DEFAULT_VALUE_FILTER,
    VALUE_FILTER_HEARTBEAT_SECONDS,
    MAX_CUSTOM_NAME_LENGTH,
    MAX_SAVED_NAME_LENGTH,
    DuplicateNameError,
    Index,
    IndexBusy,
    InvalidNameError,
)
from .storage.ingestion import IngestionService
from .storage.coordinator import StorageCoordinator
from .storage.paths import ENTITY_ID_MAX_LENGTH, ENTITY_ID_PATTERN, validate_entity_id
from .timezone_config import load_timezone
from .version import APP_VERSION
from . import api_routes
from . import ha_integration
from .import_routes import ImportDependencies, ImportService
from .index_optimization import (
    build_index_detail_context,
    get_index_optimization_state,
    optimize_index,
)
from .report_routes import ReportDependencies, ReportService
from .energiedashboard_routes import (
    SETTING_HOURLY_BACKFILL_PENDING,
    EnergieDashboardDependencies,
    EnergieDashboardService,
    energiedashboard_role_count,
    is_energiedashboard_configured,
    process_pending_hourly_backfill,
    refresh_heatmap_weekday_cache_if_stale,
    sync_hourly_rollup_flags_for_current_config,
)
from .route_support import UploadLimitExceeded, copy_upload_limited, dir_size, storage_locked
from . import notices as notices_mod
from . import version_check
from .notices import collect_notices

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("zeitarchiv.trace")
# Bereits frühe Bootstrap-Fehler (insbesondere eine ungültige Zeitzone) sollen
# den redigierten Handler/Ringpuffer erreichen. Nach Öffnen des Index wird
# unten mit den gespeicherten Nutzerwerten erneut idempotent konfiguriert.
configure_logging(DEFAULT_LOG_LEVEL, DEFAULT_ACCESS_LOG_MODE)

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
RETENTION_DEFAULT_WEEKDAY = 6
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
    "modern": "Modern",
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


def _nav_dashboards_context(request: Request) -> dict:
    """Für das aufklappbare "Dashboards"-Menü in _topnav.html — läuft wie
    _font_scale_context automatisch für JEDE TemplateResponse mit, statt dass
    jede der ~10 Seiten, die _topnav.html einbinden, die Dashboard-Liste
    selbst in ihren Kontext aufnehmen müsste. list_dashboards() ist eine
    einfache, indexierte SELECT-Abfrage ohne Joins — auch bei vielen
    Dashboards auf jeder Seite unproblematisch.

    nav_energiedashboard_enabled kommt aus derselben settings-Tabelle wie das
    Energiedashboard selbst (energiedashboard_routes.py) — das Energiedashboard
    ist bewusst KEIN Eintrag in nav_dashboards_list (keine Zeile in
    dashboards), sondern ein fester, eigener Menüpunkt oberhalb der Liste."""
    return {
        "nav_dashboards_list": index.list_dashboards(),
        "nav_energiedashboard_enabled": index.get_setting("energiedashboard_enabled", "0") == "1",
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


def _notices_context(request: Request) -> dict:
    """Für das Hinweis-Center (Glocken-Icon) in _topnav.html — läuft wie
    _app_root_context automatisch für JEDE TemplateResponse mit, statt dass
    jede der ~10 Seiten, die _topnav.html einbinden, die Hinweisliste selbst
    in ihren Kontext aufnehmen müsste. collect_notices() fragt bewusst nur
    günstige Werte ab (PRAGMA-Stats, LIMIT-1-Queries), siehe notices.py."""
    return {
        "notices": collect_notices(
            index, DATA_DIR / "index.sqlite", TZ, _load_purge_preview()["totals"],
            _storage_reconcile_last, _stale_entity_count_cached, _last_scheduler_tick,
            _last_reconcile_tick, _reconcile_in_progress(), _host_disk_usage_cached,
        ),
        "snooze_labels": notices_mod.SNOOZE_LABELS,
    }


app = FastAPI(title="Zeitarchiv")
templates = Jinja2Templates(
    directory=str(APP_DIR / "templates"),
    context_processors=[_font_scale_context, _app_root_context, _nav_dashboards_context, _notices_context],
)


@app.middleware("http")
async def _request_logging(request: Request, call_next):
    started = time.perf_counter()
    request_id = secrets.token_hex(6)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        log_http_request(
            request.method,
            request.url.path,
            500,
            (time.perf_counter() - started) * 1000,
            request_id=request_id,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    log_http_request(
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
        request_id=request_id,
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


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    # Browser fragen dieses Icon unabhängig vom aktuellen app_root-Präfix (Ingress)
    # immer unter dem Wurzelpfad an — ohne diese Route landet das als 404 im
    # Access-Log, obwohl das Addon-Icon längst existiert (addon/icon.png).
    return FileResponse(APP_DIR.parent / "icon.png", media_type="image/png")
# Cache-Busting fürs geteilte Stylesheet (Design-System, siehe app/static/css/README.md):
# der Browser cacht static/-Antworten sonst über Deploys hinweg, weil StaticFiles
# keinen Cache-Control-Header setzt und nur Last-Modified/ETag liefert — je nach
# Heuristik reicht das nicht für ein zuverlässiges Update. Ein an die Datei-mtime
# gekoppelter Query-Parameter zwingt bei jeder Änderung eine frische Anfrage.
# format_int/format_value als Jinja-Filter statt jede Stelle einzeln in Python
# vorzuformatieren: hier gerenderte Zahlen (Import-Vorschau/-Ergebnis, siehe
# _import_dry_run.html/_import_result.html) stecken in Dataclass-Listen aus
# storage/symcon_import.py, für die ein eigener "_label"-Wrapper pro Feld
# unverhältnismäßig wäre. Einzige Ausnahme von der sonstigen Konvention
# "in Python formatieren, _label ans Template reichen" — bewusst, weil die
# Alternative (jede Dataclass-Zahl vor jedem Render manuell in ein Dict mit
# *_label-Kopien übersetzen) hier mehr Code für denselben Zweck wäre.
templates.env.filters["format_int"] = format_int
templates.env.filters["format_value"] = format_value
templates.env.globals["css_v"] = int((APP_DIR / "static" / "css" / "app.css").stat().st_mtime)
# Dieselbe Cache-Busting-Begründung wie oben, nur fürs JS (calendar-picker.js,
# confirm-dialog.js, …) — ohne das blieb z. B. ein Fix in calendar-picker.js im
# Browser-Cache unbemerkt hängen, obwohl der Server längst die neue Version
# ausliefert. Eine gemeinsame mtime über alle JS-Dateien statt einer pro Datei:
# einfacher als js_v-Kopien an jeder <script>-Stelle zu pflegen, und ändert sich
# ohnehin bei jedem Deploy dieses Verzeichnisses.
templates.env.globals["js_v"] = int(max(p.stat().st_mtime for p in (APP_DIR / "static" / "js").glob("*.js")))
# Namenslänge für Dashboards/Charts/Tabellen: als maxlength in die
# Eingabefelder, damit die Grenze schon beim Tippen gilt statt erst beim
# Speichern. Die verbindliche Prüfung bleibt serverseitig
# (_ensure_valid_name_locked() im Index) — maxlength ist nur die Bequemlichkeit.
templates.env.globals["max_name_length"] = MAX_SAVED_NAME_LENGTH
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
    logger.warning(
        "Unterbrochene Backup-Jobs erkannt · event=backup_jobs_interrupted jobs=%d",
        _interrupted_backup_jobs,
    )
_interrupted_retention_jobs = index.recover_interrupted_retention_jobs()
if _interrupted_retention_jobs:
    logger.warning(
        "Unterbrochene Retention-Jobs erkannt · event=retention_jobs_interrupted jobs=%d",
        _interrupted_retention_jobs,
    )
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


_storage_reconcile_last: dict | None = None
_storage_reconcile_thread: threading.Thread | None = None
_storage_reconcile_stop = threading.Event()
_storage_reconcile_completed = False
# Analog zu _last_scheduler_tick weiter unten, aber pro geprüfter Entität
# statt pro Schleifendurchlauf aktualisiert — der Hintergrundabgleich hat
# keinen festen Takt wie der Wartungsplaner (Laufzeit hängt von der
# Datenmenge je Entität ab), ein Deadlock am selben Index-Lock (siehe
# 0.76.0-Fund in Index.get_or_create_entity()) würde ihn aber genauso
# unsichtbar hängen lassen. Initial auf den Startzeitpunkt gesetzt, siehe
# Begründung bei _last_scheduler_tick.
_last_reconcile_tick = time.time()


def _reconcile_in_progress() -> bool:
    """True nur, während der Hintergrund-Thread tatsächlich noch laufen
    sollte. Im synchronen Modus (_requires_synchronous_reconciliation) wird
    er nie gestartet — dann bliebe _storage_reconcile_completed sonst
    dauerhaft False und eine Stall-Meldung würde fälschlich für immer aktiv
    bleiben, obwohl der Abgleich längst (synchron, vor dem ersten Request)
    passiert ist."""
    return _storage_reconcile_thread is not None and not _storage_reconcile_completed
# Beim ersten Start (und defensiv auch nach einem manuell geleerten DB-Wert)
# muss vor dem Öffnen eines HTTP-Listeners ein nicht-leerer Token existieren.
# ZEITARCHIV_API_TOKEN ist ausschließlich der explizite Override für den
# lokalen Compose-/Virtualenv-Test; im Supervisor wird immer kryptografisch
# sicher generiert und anschließend in SQLite persistiert.
ensure_api_token(index, development_token=os.environ.get("ZEITARCHIV_API_TOKEN"))
ingestion_service = IngestionService(DATA_DIR, index, TZ, storage_coordinator)
_recovered_ingest_events = ingestion_service.recover_pending()
_requires_synchronous_reconciliation = bool(_restore_startup_result) or not _previous_shutdown_clean
if _requires_synchronous_reconciliation:
    with storage_coordinator.exclusive():
        _run_storage_reconciliation(repair=True)
else:
    logger.info(
        "Speicherindex-Abgleich wird nach dem Start im Hintergrund ausgeführt · "
        "event=storage_reconcile_scheduled"
    )
logger.info(
    "Zeitarchiv gestartet · event=application_started data_dir=%s log_level=%s access_log=%s",
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
            collect_notices=lambda: collect_notices(
                index, DATA_DIR / "index.sqlite", TZ, _load_purge_preview()["totals"],
                _storage_reconcile_last, _stale_entity_count_cached, _last_scheduler_tick,
                _last_reconcile_tick, _reconcile_in_progress(), _host_disk_usage_cached,
            ),
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

_energiedashboard_service = EnergieDashboardService(EnergieDashboardDependencies(
    data_dir=DATA_DIR,
    index=index,
    tz=TZ,
    templates=templates,
    app_root_context=_app_root_context,
))
app.include_router(_energiedashboard_service.router())


@app.exception_handler(cleanup.ResultLimitExceeded)
async def _result_limit_handler(
    _request: Request, exc: cleanup.ResultLimitExceeded
) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(InvalidNameError)
async def _invalid_name_handler(
    _request: Request, exc: InvalidNameError
) -> JSONResponse:
    """Abgelehnter Dashboard-/Chart-/Tabellenname — zentral statt in jeder
    einzelnen Speichern-Route, weil die Prüfung selbst im Index sitzt (siehe
    _ensure_valid_name_locked()). 409 Conflict bei einer Namenskollision (die
    Anfrage ist wohlgeformt, sie kollidiert nur mit dem Bestand), 400 bei einem
    zu langen Namen. Die Editoren zeigen "detail" unverändert an."""
    status = 409 if isinstance(exc, DuplicateNameError) else 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(IndexBusy)
async def _index_busy_handler(_request: Request, exc: IndexBusy) -> JSONResponse:
    """Das Index-Lock war nicht innerhalb von INDEX_LOCK_TIMEOUT_SECONDS frei
    (siehe _TimeoutLock in index.py) — z. B. während eines laufenden VACUUM,
    im schlimmsten Fall ein Self-Deadlock, den dieser Timeout gerade heilt.
    503 statt der übrigen 4xx-Handler oben, weil das keine fehlerhafte
    Anfrage ist, sondern ein "gleich nochmal versuchen"-Zustand."""
    return JSONResponse(
        status_code=503,
        content={"detail": "Datenbank kurzzeitig ausgelastet — bitte in ein paar Sekunden erneut versuchen."},
    )


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
    started_at = time.time()
    index.update_retention_job(job_id, status="running", started_at=started_at)
    logger.info("Retention gestartet · event=retention_started job_id=%d", job_id)
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
    schedule = index.get_setting("retention_enforcement", "off")
    if schedule not in ("daily", "weekly"):
        schedule = "off"
    weekday = int(index.get_setting("retention_enforcement_weekday", str(RETENTION_DEFAULT_WEEKDAY)))
    next_run = next_scheduled_run(
        now,
        schedule,
        index.get_setting("retention_enforcement_time", RETENTION_DEFAULT_TIME),
        weekday,
    )
    index.set_setting("retention_enforcement_next_run", "" if next_run is None else str(next_run.timestamp()))
    return None if next_run is None else next_run.timestamp()


def _run_retention_enforcement_if_due(now: datetime) -> None:
    """Führt genau einen fälligen Lauf (täglich/wöchentlich) aus, unabhängig von Requests."""
    if index.get_setting("retention_enforcement", "off") not in ("daily", "weekly"):
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
        **_dashboard_tiles_context(index.get_default_dashboard_id()),
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
        "default_decimals": index.get_setting("default_decimals", DEFAULT_DECIMALS),
        "default_value_filter": index.get_setting("default_value_filter", DEFAULT_VALUE_FILTER),
        "default_gap_threshold": index.get_setting("default_gap_threshold", DEFAULT_GAP_THRESHOLD),
        "default_outlier_threshold": index.get_setting("default_outlier_threshold", DEFAULT_OUTLIER_THRESHOLD),
        "resolution_options": list(RESOLUTION_LABELS.items()),
        "retention_options": list(RETENTION_LABELS.items()),
        "decimals_options": list(DECIMALS_LABELS.items()),
        "value_filter_options": list(VALUE_FILTER_LABELS.items()),
        "gap_threshold_options": list(GAP_THRESHOLD_LABELS.items()),
        "outlier_threshold_options": list(OUTLIER_THRESHOLD_LABELS.items()),
        "saved": saved,
    }


def _count_stale_entities() -> int:
    """Für die Rotation-Sektion: Anzahl Entitäten mit mindestens einer noch
    nicht rotierten Hot-Datei aus einem vergangenen Monat — reines Zählen,
    ohne tatsächlich zu rotieren (das macht erst der Button-Klick).
    find_entities_with_stale_hot_files() statt find_stale_hot_files() je
    Entität in einer Schleife — EIN Verzeichnis-Listing statt N glob()-
    Aufrufen, die sonst bei jedem Laden von /settings erneut das komplette
    hot_dir je Entität durchsuchen (siehe Kommentar dort)."""
    now_ts = datetime.now(TZ).timestamp()
    entity_ids = [entity["entity_id"] for entity in index.list_entities()]
    with storage_coordinator.entities(entity_ids):
        return len(hotbuffer.find_entities_with_stale_hot_files(DATA_DIR, set(entity_ids), now_ts, TZ))


# Vom Wartungsplaner (alle 30s) gepflegter Zwischenstand für die Meldungen
# (housekeeping.rotation_pending). _count_stale_entities() selbst nimmt über
# storage_coordinator.entities() die Sperren ALLER Entitäten — ohne Timeout,
# wartend auf laufende Exklusiv-Wartung (Backup/VACUUM) und diese ihrerseits
# blockierend. Als Teil von _notices_context() lief das bisher bei JEDER
# Template-Antwort, auch bei jedem htmx-Such-Fragment pro Tastendruck — in
# Produktion mit laufender Ingestion (die dieselben Sperren je Entität hält)
# der Grund für sekundenlange Hänger beim Filtern der Entitätenliste. Nur
# _settings_rotation_context() (Housekeeping → Rotation, bewusster
# Seitenaufruf) zählt weiterhin live.
_stale_entity_count_cached = 0


def _refresh_stale_entity_count() -> None:
    global _stale_entity_count_cached
    _stale_entity_count_cached = _count_stale_entities()


# Für housekeeping.host_disk_space_low (notices.py) — Host-Speicherplatz statt
# Zeitarchivs eigener interner Aufschlüsselung. Wie _stale_entity_count_cached
# im Wartungsplaner statt pro Request aktualisiert; shutil.disk_usage() selbst
# ist zwar günstig genug für den Request-Pfad (siehe index_optimization.py),
# aber _notices_context() läuft bei JEDER Template-Antwort (siehe deren
# Docstring) — ein Syscall weniger pro Seitenaufruf ist der einfachere Weg,
# konsistent mit dem Rest dieser Cache-Gruppe zu bleiben.
_host_disk_usage_cached: dict | None = None


def _refresh_host_disk_usage() -> None:
    global _host_disk_usage_cached
    usage = shutil.disk_usage(DATA_DIR)
    _host_disk_usage_cached = {"free": usage.free, "total": usage.total}


def _host_disk_usage_context() -> dict:
    """Für die immer sichtbare Host-Speicherplatz-Zeile in housekeeping.html —
    andere Frage als Zeitarchivs eigene interne Aufschlüsselung (Speicherindex,
    Bereinigung), siehe notices.py housekeeping.host_disk_space_low."""
    usage = _host_disk_usage_cached
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
    return {"stale_count": _count_stale_entities(), "result": result}


_PURGE_PREVIEW_SETTING = "purge_preview_snapshot"
_PURGE_PREVIEW_MAX_AGE_SECONDS = 3600


def _empty_purge_preview() -> dict:
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


def _invalidate_purge_preview() -> None:
    """Erzwingt eine Aktualisierung der Bereinigungsvorschau beim nächsten
    Wartungsplaner-Durchlauf (binnen ~30s), NICHT synchron im aufrufenden
    Request — der Vollscan ist teuer (siehe _refresh_purge_preview_if_stale)
    und würde sonst jeden einzelnen Markier-Klick auf der Bereinigungsseite
    spürbar verlangsamen. Nach dem Markieren neuer Datensätze zur Löschung
    aufgerufen (delete_rows, Duplikate-/Wiederholungen-Löschung), damit
    Housekeeping → Speicherplatz nicht bis zu einer Stunde lang veraltete
    Zahlen zeigt, nur weil noch niemand "Jetzt bereinigen" geklickt hat."""
    current = _load_purge_preview()
    current["generated_at"] = 0
    index.set_setting(_PURGE_PREVIEW_SETTING, json.dumps(current, ensure_ascii=False, separators=(",", ":")))


def _load_purge_preview() -> dict:
    raw = index.get_setting(_PURGE_PREVIEW_SETTING, "")
    if not raw:
        return _empty_purge_preview()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _empty_purge_preview()
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        return _empty_purge_preview()
    return value


def _refresh_purge_preview_if_stale(*, force: bool = False) -> dict:
    """Aktualisiert die teure Bereinigungsvorschau (liest für jede Entität mit
    markierten Löschungen die betroffenen Archiv-Parquet-Dateien) höchstens
    einmal pro Stunde — dieselbe Zwischenspeicher-Konvention wie
    _refresh_retention_overview_if_stale(). Ohne diesen Cache lief
    preview_purge() bei JEDEM Aufruf von /settings neu; bei einer Entität mit
    sehr vielen markierten Zeilen und vielen Archiv-Monaten machte allein das
    die Einstellungen-Seite spürbar langsam (mehrere Sekunden)."""
    current = _load_purge_preview()
    generated_at = current.get("generated_at")
    now_ts = time.time()
    if (
        not force
        and isinstance(generated_at, (int, float))
        and now_ts - generated_at < _PURGE_PREVIEW_MAX_AGE_SECONDS
    ):
        return current
    entity_ids = [row["entity_id"] for row in index.get_deleted_points_by_entity()]
    with storage_coordinator.entities(entity_ids):
        preview = cleanup.preview_purge(DATA_DIR, index, TZ)
    preview["generated_at"] = now_ts
    index.set_setting(
        _PURGE_PREVIEW_SETTING,
        json.dumps(preview, ensure_ascii=False, separators=(",", ":")),
    )
    logger.debug(
        "Bereinigungsvorschau aktualisiert · entfernbare Zeilen=%d · Entitäten=%d",
        preview["totals"]["removable_rows"],
        preview["totals"]["entities_affected"],
    )
    return preview


def _settings_purge_context(result: str | None = None) -> dict:
    """Liefert die stets sichtbare, rein lesende Bereinigungsvorschau — aus
    dem Zwischenspeicher (siehe _refresh_purge_preview_if_stale()), NICHT bei
    jedem Aufruf neu berechnet. Die Aktualisierung übernimmt der
    Wartungsplaner (_maintenance_scheduler_loop()) im Hintergrund; nach einem
    tatsächlichen Purge-Klick erzwingt settings_purge() zusätzlich eine
    sofortige Aktualisierung, damit das Ergebnis nicht die alten Zahlen zeigt."""
    return {"result": result, "purge_preview": _load_purge_preview()}


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
    schedule = index.get_setting("retention_enforcement", "off")
    if schedule not in BACKUP_SCHEDULE_LABELS:
        schedule = "off"
    enabled = schedule in ("daily", "weekly")
    next_raw = index.get_setting("retention_enforcement_next_run", "")
    try:
        next_ts = float(next_raw) if next_raw else None
    except ValueError:
        next_ts = None
    if enabled and next_ts is None:
        next_ts = _set_next_retention_run(datetime.now(TZ))

    retention_overview = _load_retention_overview()
    retention_totals = retention_overview.get("totals", {})
    retention_history_30d = index.get_retention_job_totals(time.time() - 30 * 86400)
    retention_history_all = index.get_retention_job_totals(0.0)
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
        return f"{format_timestamp(ts, TZ)} {format_time(ts, TZ)}" if ts else "—"

    status_labels = {
        "queued": "Geplant", "running": "Läuft", "success": "Erfolgreich",
        "failed": "Fehlgeschlagen", "interrupted": "Abgebrochen", "skipped": "Übersprungen",
    }
    jobs = []
    for job in index.list_retention_jobs(8):
        jobs.append({
            "created_at": f"{format_timestamp(job['created_at'], TZ)} {format_time(job['created_at'], TZ)}",
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
    with _retention_progress.lock:
        running = _retention_progress.running
    last_success_raw = index.get_setting("retention_last_success", "")
    return {
        "retention_enforcement_enabled": enabled,
        "retention_enforcement_schedule": schedule,
        "retention_enforcement_options": list(BACKUP_SCHEDULE_LABELS.items()),
        "retention_enforcement_time": index.get_setting("retention_enforcement_time", RETENTION_DEFAULT_TIME),
        "retention_enforcement_weekday": int(
            index.get_setting("retention_enforcement_weekday", str(RETENTION_DEFAULT_WEEKDAY))
        ),
        "retention_weekday_options": BACKUP_WEEKDAY_OPTIONS,
        "retention_timezone": str(TZ),
        "retention_next_run": display_ts(str(next_ts) if next_ts is not None else None),
        "retention_last_success": display_ts(last_success_raw),
        "retention_last_failure": display_ts(index.get_setting("retention_last_failure", "")),
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
    entity_defaults = _get_entity_chart_defaults()
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
        # Globale Defaults für das Optionen-Menü der Entität-eigenen Chart-Seite
        # (entity_detail.html) — siehe _get_entity_chart_defaults()/
        # _resolve_entity_chart_options() weiter unten in dieser Datei. Bool-
        # Felder als "1"/"0"-Strings fürs Dropdown-Muster (wie dashboard_animation
        # oben), nicht als Python-bool.
        "entity_continuous": "1" if entity_defaults["continuous"] else "0",
        "entity_raw": "1" if entity_defaults["raw"] else "0",
        "entity_show_points": "1" if entity_defaults["show_points"] else "0",
        "entity_show_values": "1" if entity_defaults["show_values"] else "0",
        "entity_dynamic_y_axis": "1" if entity_defaults["dynamic_y_axis"] else "0",
        "entity_chart_stats": "1" if entity_defaults["chart_stats"] else "0",
        "entity_legend_metrics": entity_defaults["legend_metrics"],
        "entity_legend_metric_options": list(_ENTITY_LEGEND_METRIC_LABELS.items()),
        "entity_legend_style": entity_defaults["legend_style"],
        "entity_legend_style_options": list(_ENTITY_LEGEND_STYLE_LABELS.items()),
        "on_off_options": list(_ON_OFF_LABELS.items()),
        "saved": saved,
    }


def _settings_verbindung_context(saved: bool = False) -> dict:
    last_write_ts = index.get_last_write_ts()
    last_auth_failure_ts = _CONNECTION_STATS["last_auth_failure_ts"]
    integration_info = ha_integration.get_info(index)
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
        "integration_version": integration_info["version"] if integration_info else None,
        "integration_last_seen_at": (
            f"{format_timestamp(integration_info['last_seen'], TZ)} {format_time(integration_info['last_seen'], TZ)}"
            if integration_info
            else None
        ),
        "integration_outdated": (
            bool(integration_info) and ha_integration.is_outdated(integration_info["version"])
        ),
        **_integration_update_context(integration_info),
    }


def _integration_update_context(integration_info: dict | None) -> dict:
    latest = ha_integration.latest_known_integration_version(index)
    kind = (
        ha_integration.integration_update_kind(integration_info["version"], latest)
        if integration_info and latest
        else None
    )
    return {
        "integration_latest_version": latest if kind else None,
        "integration_update_kind": kind,
    }


def _settings_background_processes_context() -> dict:
    """Letzter Lauf + Status je Wartungsplaner-Hintergrundaufgabe (Einstellungen
    → Diagnose, Abschnitt "Hintergrundprozesse") — bisher nur in den
    Server-Logs sichtbar (siehe _maintenance_scheduler_loop()). Rein lesend,
    löst selbst nichts aus; der Wartungsplaner läuft unabhängig alle 30s
    weiter, unabhängig davon, ob diese Seite gerade geöffnet ist."""
    now = time.time()

    def row(name: str, hint: str, ts: float | None, *, error: bool = False) -> dict:
        if error:
            pill_class, pill_label = "error", "Fehler"
        elif ts is None:
            pill_class, pill_label = "none", "–"
        else:
            pill_class, pill_label = "ok", "OK"
        last_run = f"vor {format_uptime(now - ts)}" if ts is not None else "noch nie gelaufen"
        return {"name": name, "hint": hint, "last_run": last_run, "pill_class": pill_class, "pill_label": pill_label}

    duplicate_snapshot = index.get_duplicate_snapshot()
    version_state = version_check.get_cached_state(index)
    reconcile = _storage_reconcile_last or {}
    reconcile_ts = reconcile.get("checked_at") or reconcile.get("started_at")

    rows = [
        row(
            "Statistik-Snapshot", "Kennzahlen-Schnappschuss für die Statistik-Seite · stündlich",
            index.get_latest_stats_snapshot_ts(),
        ),
        row(
            "Arbeitsspeicher-Snapshot", "RAM-Verlauf für die Statistik-Seite · stündlich",
            index.get_latest_memory_snapshot_ts(),
        ),
        row(
            "Aufbewahrung-Übersicht", "Vorschau der von der Frist betroffenen Zeilen · bei Bedarf",
            _load_retention_overview().get("generated_at"),
        ),
        row(
            "Löschvorschau", "Vorschau für weich gelöschte, noch nicht entfernte Werte · bei Bedarf",
            _load_purge_preview().get("generated_at"),
        ),
        row(
            "Duplikat-Erkennung", "Zählt doppelte Zeitstempel je Entität vor · stündlich",
            duplicate_snapshot.get("checked_at") if duplicate_snapshot else None,
        ),
        row(
            "Versionsprüfung", "Prüft auf GitHub, ob eine neuere Version verfügbar ist · täglich",
            version_state.get("checked_at") if version_state else None,
        ),
        row(
            "Speicherindex-Abgleich", "Gleicht Zeilenzahl/Größe je Entität mit den Dateien ab · nach Neustart",
            reconcile_ts, error=bool(reconcile.get("errors")),
        ),
    ]

    if is_energiedashboard_configured(index):
        try:
            pending = json.loads(index.get_setting(SETTING_HOURLY_BACKFILL_PENDING, "[]"))
        except (TypeError, ValueError):
            pending = []
        pending_count = len(pending) if isinstance(pending, list) else 0
        rows.append({
            "name": "Energiedashboard · Stunden-Rollup-Backfill",
            "hint": "Baut die feinere Auflösung für neu zugeordnete Zähler-Rollen rückwirkend auf · 1 Entität/30s",
            "last_run": "Warteschlange leer" if not pending_count else "–",
            "pill_class": "pending" if pending_count else "ok",
            "pill_label": f"{pending_count} ausstehend" if pending_count else "OK",
        })

        heatmap_snapshots = [
            index.get_heatmap_weekday_snapshot("month"), index.get_heatmap_weekday_snapshot("year"),
        ]
        heatmap_ts_values = [s["checked_at"] for s in heatmap_snapshots if s and s.get("checked_at") is not None]
        rows.append(row(
            "Energiedashboard · Tageslastprofil-Cache",
            "Berechnet die Wochentags-Ansicht für Monat/Jahr im Voraus · täglich",
            min(heatmap_ts_values) if heatmap_ts_values else None,
        ))

    return {"background_processes": rows}


def _debug_tools_context() -> dict:
    """Zustand der beiden Debugging-Werkzeuge (Konzept "Debugging: nächsten
    Schreibvorgang aufzeichnen" / "Entity-Trace") — eigene Funktion statt Teil
    von _settings_logging_context(), damit das per htmx per Polling
    nachgeladene Fragment (settings/logging/debug) nur diesen kleinen
    Ausschnitt neu rendert, nicht die ganze Protokollierung-Sektion."""
    now = time.time()
    with _write_capture_lock:
        api_routes.expire_write_capture(_write_capture, now)
        capture_armed = _write_capture["armed"]
        captured_at = _write_capture["captured_at"]
        capture_expires_at = _write_capture["expires_at"]
        payload = _write_capture["payload"]
    with _entity_trace_lock:
        api_routes.expire_entity_trace(_entity_trace, now)
        trace_entity_id = _entity_trace["entity_id"]
        trace_expires_at = _entity_trace["expires_at"]

    trace_active = bool(trace_entity_id) and (trace_expires_at or 0) > now
    return {
        "capture_armed": capture_armed,
        "capture_captured_at": (
            f"{format_timestamp(captured_at, TZ)} {format_time(captured_at, TZ)}" if captured_at else None
        ),
        "capture_event_count": len(payload["events"]) if payload else None,
        "capture_payload_json": json.dumps(payload, indent=2, ensure_ascii=False) if payload else None,
        "capture_expires_in_minutes": (
            max(1, round((capture_expires_at - now) / 60)) if capture_expires_at else None
        ),
        "trace_entity_id": trace_entity_id if trace_active else None,
        "trace_expires_in_minutes": (
            max(1, round((trace_expires_at - now) / 60)) if trace_active else None
        ),
        "entity_options": [
            {
                "entity_id": row["entity_id"],
                "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
                "ha_name": row["friendly_name"] or row["entity_id"],
                "is_custom": bool(row["custom_name"]),
            }
            for row in index.list_entities()
        ],
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


def _settings_notices_context() -> dict:
    muted = [
        {
            **entry,
            "muted_at_text": (
                f"{format_timestamp(entry['muted_at'], TZ)} {format_time(entry['muted_at'], TZ)}"
                if entry["muted_at"] else "—"
            ),
            "until_text": (
                f"{format_timestamp(entry['until'], TZ)} {format_time(entry['until'], TZ)}"
                if entry["until"] else None
            ),
        }
        for entry in notices_mod.list_muted_notices(index)
        # Tipps nutzen ihr eigenes Ausblenden (hide_tip_today), nicht das
        # allgemeine Stummschalt-System — ein hier trotzdem noch
        # vorhandener "tips."-Eintrag wäre nur ein Überbleibsel aus der Zeit
        # vor dieser Trennung und soll auch dann nicht mehr auftauchen.
        if not entry["id"].startswith("tips.")
    ]
    return {
        "muted_notices": muted,
        "tips_enabled": "1" if notices_mod.tips_enabled(index) else "0",
        "tips_on_off_options": list(_ON_OFF_LABELS.items()),
        "tips_list": notices_mod.list_tips_with_status(index, TZ),
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
            "uptime": format_uptime(time.time() - _SERVER_STARTED_AT),
            "latest_version": version_check.latest_known_version(index),
            "update_available": version_check.update_available(index, APP_VERSION),
            **_settings_verbindung_context(),
            **_settings_darstellung_context(),
            **_settings_archivierung_context(),
            **_debug_tools_context(),
            **_settings_notices_context(),
            **_settings_background_processes_context(),
        },
    )


@app.post("/notices/{notice_id}/mute")
async def mute_notice_route(request: Request, notice_id: str) -> dict:
    """Stummschaltung nur für als "mutable" markierte Meldungen — main.py
    vertraut dabei nicht dem Client, sondern schlägt Titel/Text/Severity
    server-seitig in den gerade aktiven Meldungen nach (build_notices()),
    bevor irgendwas gespeichert wird. Prüft notice["mutable"] statt die
    Severity erneut gegen MUTABLE_SEVERITIES zu spiegeln: ein Fehler
    (severity "error") lässt sich so nie stumm schalten, selbst bei einem
    manipulierten Request — UND ein Tipp (severity "info", aber explizit
    mutable=False, siehe notices._current_tip_notice) landet nicht versehentlich
    im allgemeinen Stummschalt-System, obwohl seine Severity das erlauben
    würde. Tipps haben ihr eigenes Ausblenden (settings_tips_hide()). Die
    Dauer kommt aus einer festen Preset-Liste (SNOOZE_PRESETS) statt einem
    frei wählbaren Datum — "forever" (None) eingeschlossen, bleibt trotzdem
    sicher, siehe Kommentar dort (Fingerprint statt Ablaufdatum)."""
    notice = next(
        (
            n for n in notices_mod.build_notices(
                index, DATA_DIR / "index.sqlite", TZ, _load_purge_preview()["totals"],
                _storage_reconcile_last, _stale_entity_count_cached, _last_scheduler_tick,
                _last_reconcile_tick, _reconcile_in_progress(), _host_disk_usage_cached,
            )
            if n["id"] == notice_id
        ),
        None,
    )
    if notice is None:
        raise HTTPException(status_code=404, detail="Meldung nicht gefunden oder nicht mehr aktiv")
    if not notice["mutable"]:
        raise HTTPException(status_code=400, detail="Diese Meldung lässt sich nicht stumm schalten")
    form = await request.form()
    duration = form.get("duration")
    if duration not in notices_mod.SNOOZE_PRESETS:
        raise HTTPException(status_code=400, detail="Ungültige Dauer")
    seconds = notices_mod.SNOOZE_PRESETS[duration]
    until = time.time() + seconds if seconds is not None else None
    notices_mod.mute_notice(index, notice_id, notice["title"], notice["detail"], notice["meta"], until=until)
    remaining = collect_notices(
        index, DATA_DIR / "index.sqlite", TZ, _load_purge_preview()["totals"],
        _storage_reconcile_last, _stale_entity_count_cached, _last_scheduler_tick,
        _last_reconcile_tick, _reconcile_in_progress(), _host_disk_usage_cached,
    )
    return {"success": True, "remaining_count": len(remaining)}


@app.post("/notices/{notice_id}/unmute")
def unmute_notice_route(notice_id: str) -> dict:
    notices_mod.unmute_notice(index, notice_id)
    return {"success": True}


@app.get("/notices/panel", response_class=HTMLResponse)
def notices_panel(request: Request) -> HTMLResponse:
    """Frischer Inhalt für #notice-panel-body (Glocken-Menü) — von
    refreshNoticePanel() nach dem Zurückholen einer stummgeschalteten Meldung
    auf der Einstellungen-Seite abgerufen (_topnav.html), damit das Panel
    nicht bis zum nächsten vollständigen Seitenaufruf veraltet bleibt.
    _notices_context läuft zwar als globaler context_processor mit, aber nur
    für ganze TemplateResponses — hier explizit erneut aufgerufen, weil diese
    Route ganz bewusst nur das kleine Partial zurückgibt."""
    return templates.TemplateResponse(request, "_notice_panel_body.html", _notices_context(request))


@app.get("/settings/muted-notices", response_class=HTMLResponse)
def settings_muted_notices(request: Request) -> HTMLResponse:
    """Frischer Inhalt für #muted-notices-body (Einstellungen · Meldungen) —
    von refreshMutedNotices() nach dem Stummschalten einer Meldung im
    Glocken-Menü abgerufen (_topnav.html), Gegenstück zu notices_panel()."""
    return templates.TemplateResponse(request, "_muted_notices_body.html", _settings_notices_context())


@app.post("/settings/tips-enabled", response_class=HTMLResponse)
async def settings_tips_enabled(request: Request) -> HTMLResponse:
    """Globaler Schalter für den rotierenden Tipp im Meldungs-Center (siehe
    notices.tips_enabled/_current_tip_notice) — ausgeschaltet erscheint gar
    kein Tipp mehr, unabhängig vom Rotationsstand oder einem einzeln
    ausgeblendeten Tipp."""
    form = await request.form()
    enabled = form.get("tips_enabled")
    if enabled not in _ON_OFF_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Wert")
    notices_mod.set_tips_enabled(index, enabled == "1")
    return templates.TemplateResponse(request, "_settings_tips_form.html", _settings_notices_context())


@app.post("/settings/tips/hide", response_class=HTMLResponse)
async def settings_tips_hide(request: Request) -> HTMLResponse:
    """Blendet den HEUTE fälligen Tipp für den Rest des Tages aus (siehe
    notices.hide_tip_today) — vertraut dem übergebenen slug nicht blind,
    sondern lehnt ab, falls er nicht (mehr) dem tatsächlich heute fälligen
    Tipp entspricht (z. B. ein Klick aus einem seit Mitternacht offenen,
    veralteten Dialog)."""
    form = await request.form()
    slug = form.get("slug")
    today_tip, ordinal = notices_mod.resolve_today_tip(TZ)
    if slug != today_tip["slug"]:
        raise HTTPException(status_code=400, detail="Nur der heute fällige Tipp lässt sich ausblenden")
    notices_mod.hide_tip_today(index, slug, ordinal)
    return templates.TemplateResponse(request, "_tips_list_body.html", _settings_notices_context())


@app.post("/settings/tips/unhide", response_class=HTMLResponse)
def settings_tips_unhide(request: Request) -> HTMLResponse:
    notices_mod.unhide_tip_today(index)
    return templates.TemplateResponse(request, "_tips_list_body.html", _settings_notices_context())


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
            **_settings_logging_context(),
        },
    )


def _validate_log_request(
    level: str, search: str, limit: int, source: str = "local"
) -> tuple[str, str, int, str]:
    if level != "all" and level not in LOG_LEVEL_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Logfilter")
    if source not in {"local", "supervisor"}:
        raise HTTPException(status_code=400, detail="Ungültige Logquelle")
    return level, search[:200], max(50, min(limit, 5_000)), source


@app.get("/api/logs")
async def api_logs(
    level: str = "all",
    search: str = "",
    source: str = "local",
    limit: int = Query(default=500, ge=50, le=2_000),
) -> dict:
    level, search, limit, source = _validate_log_request(level, search, limit, source)
    result = await run_in_threadpool(
        lambda: load_log_lines(level=level, search=search, limit=limit, source=source)
    )
    return {
        **result,
        "count": len(result["lines"]),
        "generated_at": time.time(),
    }


@app.get("/logs/download", response_class=PlainTextResponse)
async def logs_download(
    level: str = "all", search: str = "", source: str = "local"
) -> PlainTextResponse:
    level, search, _, source = _validate_log_request(level, search, 5_000, source)
    result = await run_in_threadpool(
        lambda: load_log_lines(level=level, search=search, limit=5_000, source=source)
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

    # Globale Defaults für das Optionen-Menü der Entität-eigenen Chart-Seite
    # (entity_detail.html) — jedes Feld postet wie oben einzeln für sich,
    # _update_entity_chart_default() liest/schreibt das gemeinsame
    # "entity_chart_defaults"-Setting darum jedes Mal frisch statt es über
    # mehrere Requests hinweg im Speicher zu halten.
    bool_fields = {
        "entity_continuous": "continuous", "entity_raw": "raw",
        "entity_show_points": "show_points", "entity_show_values": "show_values",
        "entity_dynamic_y_axis": "dynamic_y_axis", "entity_chart_stats": "chart_stats",
    }
    for form_key, option_key in bool_fields.items():
        value = form.get(form_key)
        if value is None:
            continue
        if value not in _ON_OFF_LABELS:
            raise HTTPException(status_code=400, detail="Ungültiger Wert")
        _update_entity_chart_default(option_key, value == "1")
    entity_legend_style = form.get("entity_legend_style")
    if entity_legend_style is not None and entity_legend_style not in _CHART_LEGEND_STYLES:
        raise HTTPException(status_code=400, detail="Ungültiger Legenden-Stil")
    if entity_legend_style is not None:
        _update_entity_chart_default("legend_style", entity_legend_style)
    if "entity_legend_metrics" in form:
        entity_legend_metrics = form.getlist("entity_legend_metrics")
        if not set(entity_legend_metrics) <= _CHART_LEGEND_METRICS:
            raise HTTPException(status_code=400, detail="Ungültige Legenden-Kennzahl")
        _update_entity_chart_default("legend_metrics", entity_legend_metrics)

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
    logger.info(
        "Protokollierung geändert · event=logging_configuration_changed "
        "level=%s http_access=%s",
        level,
        access_mode,
    )
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
    now = time.time()
    with _write_capture_lock:
        _write_capture["armed"] = True
        _write_capture["captured_at"] = None
        _write_capture["expires_at"] = now + api_routes.WRITE_CAPTURE_TTL_SECONDS
        _write_capture["payload"] = None
    api_routes.schedule_write_capture_expiry(_api_state)
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/logging/capture-write/clear", response_class=HTMLResponse)
def settings_capture_write_clear(request: Request) -> HTMLResponse:
    with _write_capture_lock:
        _write_capture["armed"] = False
        _write_capture["captured_at"] = None
        _write_capture["expires_at"] = None
        _write_capture["payload"] = None
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.get("/settings/logging/capture-write/download")
def settings_capture_write_download() -> Response:
    with _write_capture_lock:
        api_routes.expire_write_capture(_write_capture)
        payload = _write_capture["payload"]
        captured_at = _write_capture["captured_at"]
    if payload is None:
        raise HTTPException(status_code=404, detail="Keine Aufzeichnung vorhanden")
    filename = f"zeitarchiv-write-capture-{datetime.fromtimestamp(captured_at, TZ).strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
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
    trace_logger.debug(
        "Trace gestartet · event=entity_trace_started entity_id=%s duration_minutes=%d",
        entity_id,
        _ENTITY_TRACE_DURATION_SECONDS // 60,
    )
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/logging/trace/stop", response_class=HTMLResponse)
def settings_trace_stop(request: Request) -> HTMLResponse:
    with _entity_trace_lock:
        entity_id = _entity_trace["entity_id"]
        _entity_trace["entity_id"] = None
        _entity_trace["started_at"] = None
        _entity_trace["expires_at"] = None
    if entity_id:
        trace_logger.debug("Trace beendet · event=entity_trace_stopped entity_id=%s", entity_id)
    return templates.TemplateResponse(request, "_settings_debug_tools.html", _debug_tools_context())


@app.post("/settings/archivierung", response_class=HTMLResponse)
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
    for key, (value, labels, error) in fields.items():
        if value is not None and value not in labels:
            raise HTTPException(status_code=400, detail=error)
    for key, (value, _labels, _error) in fields.items():
        if value is not None:
            index.set_setting(key, str(value))
    # Dieselbe Kopplung wie bei der einzelnen Entität (update_entity_config)
    # — nur wenn der Standard-Filter in DIESEM Request aktiviert wird, nicht
    # bei jedem Speichern, solange er schon aktiv ist.
    gap_threshold_auto_adjusted = False
    default_value_filter = fields["default_value_filter"][0]
    if default_value_filter == "decimals":
        current_gap = index.get_setting("default_gap_threshold", DEFAULT_GAP_THRESHOLD)
        if _should_raise_gap_threshold_for_value_filter(current_gap):
            index.set_setting("default_gap_threshold", _VALUE_FILTER_GAP_FLOOR_MINUTES)
            gap_threshold_auto_adjusted = True
    context = _settings_archivierung_context(saved=True)
    context["gap_threshold_auto_adjusted"] = gap_threshold_auto_adjusted
    return templates.TemplateResponse(request, "_settings_archivierung_form.html", context)


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
    logger.info(
        "Manuelle Rotation abgeschlossen · event=manual_rotation_completed files=%d",
        rotated,
    )
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
    logger.info(
        "Manuelle Bereinigung abgeschlossen · event=manual_cleanup_completed "
        "rows=%d months=%d",
        total_rows,
        months,
    )
    _refresh_purge_preview_if_stale(force=True)
    return templates.TemplateResponse(
        request, "_settings_purge_form.html", _settings_purge_context(result=result)
    )


@app.get("/settings/purge/marked", response_class=HTMLResponse)
def settings_purge_marked(
    request: Request,
    search: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
) -> HTMLResponse:
    """On-demand-Detailansicht der einzelnen Soft-Delete-Markierungen."""
    result = index.list_deleted_points(search=search, page=page, page_size=page_size)
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
    schedule = form.get("retention_enforcement")
    schedule_time = str(form.get("retention_enforcement_time", RETENTION_DEFAULT_TIME))
    weekday_raw = str(form.get("retention_enforcement_weekday", RETENTION_DEFAULT_WEEKDAY))
    if schedule not in BACKUP_SCHEDULE_LABELS:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitplan")
    try:
        parse_schedule_time(schedule_time)
        weekday = int(weekday_raw)
        if weekday not in range(7):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültige Uhrzeit") from exc
    index.set_setting("retention_enforcement", schedule)
    index.set_setting("retention_enforcement_time", schedule_time)
    index.set_setting("retention_enforcement_weekday", str(weekday))
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
    logger.info(
        "Backup gestartet · event=backup_started job_id=%d trigger=%s",
        job_id,
        trigger,
    )

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
                logger.exception(
                    "Alte Backups konnten nicht bereinigt werden · "
                    "event=backup_prune_failed job_id=%d",
                    job_id,
                )
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
# Zeitpunkt des letzten (versuchten) Wartungsplaner-Durchlaufs — unabhängig
# davon, ob er erfolgreich war (siehe try/except in _maintenance_scheduler_
# loop()). Erkennt einen Thread, der ganz aufgehört hat zu ticken (z. B. eine
# Endlosschleife oder ein blockierender Aufruf ohne eigenes Timeout), nicht
# nur einzelne fehlgeschlagene Durchläufe — die werden schon geloggt.
# Initial auf den Startzeitpunkt gesetzt, damit vor dem ersten Tick keine
# falsche "seit 1970 kein Tick"-Meldung entsteht.
_last_scheduler_tick = time.time()


def _background_storage_reconciliation() -> None:
    """Prüft einen normalen, sauber beendeten Bestand entitätsweise.

    Dadurch ist der HTTP-Listener sofort verfügbar und ein großer Bestand hält
    nie sämtliche Entitäten gleichzeitig an. Nach Restore/Crash wird dieser
    Pfad bewusst nicht verwendet; dort lief der Abgleich bereits synchron.
    """
    global _storage_reconcile_last, _storage_reconcile_completed, _last_reconcile_tick
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
        # Nach jeder Entität statt nur einmal am Ende — sonst würde ein Hänger
        # an der Entitäts-Sperre (with storage_coordinator.entity(...)) oder
        # im audit_storage_metadata()-Aufruf selbst nie sichtbar, weil der
        # Tick sowieso erst nach vollständigem Durchlauf käme.
        _last_reconcile_tick = time.time()
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
        "Speicherindex-Hintergrundabgleich beendet · event=storage_reconcile_completed "
        "entities=%d mismatches=%d errors=%d duration_s=%.1f",
        _storage_reconcile_last["entities_checked"],
        len(_storage_reconcile_last["mismatches"]),
        len(_storage_reconcile_last["errors"]),
        max(0.0, time.time() - started_at),
    )


def _refresh_duplicate_snapshot_if_stale() -> None:
    """Berechnet die Duplikat-Zählung für /housekeeping höchstens einmal pro
    Stunde im Hintergrund (ZP-002 in PERFORMANCE.md) — dieselbe teure
    Rohdaten-Prüfung wie zuvor, aber nicht mehr bei jedem Seitenaufruf."""
    if not index.is_duplicate_snapshot_stale():
        return
    rows = cleanup.count_duplicate_rows_by_entity(
        DATA_DIR, index, TZ, max_rows_per_entity=MAX_UI_ANALYSIS_ROWS
    )
    index.set_duplicate_snapshot(
        [{"entity_id": r["entity_id"], "friendly_name": r["friendly_name"], "count": r["count"]} for r in rows]
    )


def _maintenance_scheduler_loop() -> None:
    """Prüft interne Zeitpläne und schreibt Statistikpunkte ohne UI-Aufruf."""
    global _last_scheduler_tick
    while not _maintenance_scheduler_stop.is_set():
        try:
            if index.record_stats_snapshot_if_stale():
                logger.debug(
                    "Stündlicher Statistik-Schnappschuss gespeichert · "
                    "event=hourly_stats_snapshot_completed"
                )
            supervisor_stats.maybe_record_memory_snapshot(index)
            _refresh_retention_overview_if_stale()
            _refresh_purge_preview_if_stale()
            _refresh_duplicate_snapshot_if_stale()
            _refresh_stale_entity_count()
            _refresh_host_disk_usage()
            version_check.refresh_if_stale(index)
            ha_integration.refresh_integration_version_check_if_stale(index)
            process_pending_hourly_backfill(DATA_DIR, index, TZ, storage_coordinator)
            refresh_heatmap_weekday_cache_if_stale(_energiedashboard_service)
            _run_backup_schedule_if_due(datetime.now(TZ))
            _run_retention_enforcement_if_due(datetime.now(TZ))
        except Exception:
            logger.exception(
                "Wartungsplaner konnte den nächsten Lauf nicht prüfen · "
                "event=maintenance_scheduler_failed"
            )
        _last_scheduler_tick = time.time()
        _maintenance_scheduler_stop.wait(30)


def _start_maintenance_scheduler() -> None:
    global _maintenance_scheduler_thread, _storage_reconcile_thread
    # Einmalig beim Start: entities.hourly_rollup für eine bereits VOR diesem
    # Feature gespeicherte Energiedashboard-Konfiguration nachziehen, sonst
    # bräuchte jede bestehende Installation ein manuelles erneutes Speichern
    # des Setup-Formulars, damit der rückwirkende Backfill überhaupt anläuft.
    sync_hourly_rollup_flags_for_current_config(_energiedashboard_service)
    # Einmal vorab, damit die erste Seite nach dem Start nicht 30s lang
    # fälschlich "0 ausstehende Rotationen" meldet — zu diesem Zeitpunkt hält
    # noch niemand Entitäts-Sperren, der Aufruf ist hier ungefährlich.
    try:
        _refresh_stale_entity_count()
    except Exception:
        logger.exception("Rotation-Zähler beim Start nicht ermittelbar · event=stale_entity_count_failed")
    try:
        _refresh_host_disk_usage()
    except Exception:
        logger.exception("Host-Speicherplatz beim Start nicht ermittelbar · event=host_disk_usage_failed")
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


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _start_maintenance_scheduler()
    yield
    _stop_maintenance_scheduler()


# Nachträglich statt über FastAPI(lifespan=...) gesetzt: _start_/_stop_
# _maintenance_scheduler() referenzieren Module-Zustand (Scheduler-Threads,
# _energiedashboard_service, index), der erst nach der App-Instanziierung
# (Zeile ~326) definiert wird — app.router.lifespan_context wird laut
# Starlette erst beim tatsächlichen Start gelesen, nicht bei Zuweisung.
app.router.lifespan_context = _lifespan


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
            "created_at_ts": job["created_at"],
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
        await run_in_threadpool(copy_upload_limited, file.file, staging, MAX_ZIP_UPLOAD_BYTES)
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


def _storage_breakdown() -> list[dict]:
    """Speicherbedarf nach Kategorie, direkt aus dem Dateisystem gezählt (nicht
    aus dem Index) — als eigenständige, vom Index unabhängige Sicht auf den
    tatsächlichen Plattenverbrauch, auch für Verzeichnisse (Hot Buffer, Import,
    Backups), die der Index gar nicht mitführt."""
    index_path = DATA_DIR / "index.sqlite"
    return [
        {"key": "archive", "label": "Archiv", "bytes": dir_size(DATA_DIR / "archive")},
        {"key": "rollup", "label": "Rollups", "bytes": dir_size(DATA_DIR / "rollup")},
        {"key": "hot", "label": "Laufender Monat (Hot Buffer)", "bytes": dir_size(DATA_DIR / "hot")},
        {"key": "index", "label": "Index", "bytes": index_path.stat().st_size if index_path.exists() else 0},
        {"key": "backups", "label": "Backups", "bytes": dir_size(DATA_DIR / "backups")},
        {"key": "reports", "label": "Import-Reports", "bytes": dir_size(DATA_DIR / "reports")},
        {
            "key": "import",
            "label": "Import-Zwischendateien",
            "bytes": dir_size(DATA_DIR / "symcon_import") + dir_size(DATA_DIR / "csv_import"),
        },
    ]


def _index_optimization_state() -> dict:
    return get_index_optimization_state(index, DATA_DIR / "index.sqlite")


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


def _duplicate_rows_for_display() -> tuple[list[dict], list[dict], str]:
    """Liest den gecachten globalen Duplikat-Schnappschuss (siehe
    _refresh_duplicate_snapshot_if_stale, stündlich, 30-Tage-Fenster über alle
    Entitäten) und bereitet ihn für die Anzeige im Housekeeping-Bereich auf —
    eigene Funktion statt Inline-Code in housekeeping_view(), damit die
    Aufbereitung unabhängig von der Route testbar/lesbar bleibt."""
    duplicate_rows = (index.get_duplicate_snapshot() or {}).get("rows", [])
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
            "entity_count": format_int(row["entity_count"]),
            "total_rows": format_int(row['total_rows']),
            "total_size": format_size(row["total_size_bytes"]),
            "entity_count_raw": row["entity_count"],
            "total_rows_raw": row["total_rows"],
            "total_size_raw": row["total_size_bytes"],
        }
        for row in index.get_stats_by_type()
    ]
    by_resolution = [
        {
            "label": format_resolution(row["resolution"]),
            "entity_count": format_int(row["entity_count"]),
            "total_rows": format_int(row['total_rows']),
            "total_size": format_size(row["total_size_bytes"]),
            "entity_count_raw": row["entity_count"],
            "total_rows_raw": row["total_rows"],
            "total_size_raw": row["total_size_bytes"],
        }
        for row in index.get_stats_by_resolution()
    ]
    snapshots = index.get_stats_snapshots(time.time() - 30 * 86400)
    growth_points = [
        {"ts": s["ts"], "total_rows": s["total_rows"], "total_size_bytes": s["total_size_bytes"]}
        for s in snapshots
    ]
    storage_breakdown_raw = _storage_breakdown()
    index_optimization = _index_optimization_state()
    storage_total_bytes = sum(row["bytes"] for row in storage_breakdown_raw)
    storage_breakdown = [
        {
            "key": row["key"],
            "label": row["label"],
            "bytes": row["bytes"],
            "size": format_size(row["bytes"]),
            "percent": round(row["bytes"] / storage_total_bytes * 100, 1) if storage_total_bytes else 0,
            "href": {
                "index": "statistik/index",
                "backups": "backup",
                "import": "import",
                "reports": "import?tab=reports",
            }.get(row["key"]),
            "optimization_recommended": (
                row["key"] == "index" and index_optimization["recommended"]
            ),
        }
        for row in storage_breakdown_raw
    ]

    rate_per_hour = _ingestion_rate_per_second(growth_points, 24 * 3600)
    rate_per_day = _ingestion_rate_per_second(growth_points, 7 * 86400)
    dashboard_count = len(index.list_dashboards())
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
            "dashboard_count": dashboard_count,
            "dashboard_pin_count": dashboard_pin_count,
            "events_per_hour": format_int(round(rate_per_hour * 3600)) if rate_per_hour is not None else None,
            "events_per_day": format_int(round(rate_per_day * 86400)) if rate_per_day is not None else None,
            "by_type": by_type,
            "by_resolution": by_resolution,
            "growth_points": growth_points,
            "has_growth_history": len(growth_points) >= 2,
            "growth_range_options": _GROWTH_RANGE_OPTIONS,
            "storage_breakdown": storage_breakdown,
            "storage_total_size": format_size(storage_total_bytes),
        },
    )


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
    for entity in index.list_entities():
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
                datetime.fromtimestamp(last_ts, TZ).strftime("%d.%m.%Y, %H:%M") if last_ts is not None else "Nie empfangen"
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


@app.get("/housekeeping/stale-entities", response_class=HTMLResponse)
def housekeeping_stale_entities(request: Request, days: str = _STALE_ENTITIES_DEFAULT_DAYS) -> HTMLResponse:
    """Von refreshStaleEntities() bzw. dem hx-trigger="change" auf
    #stale-entities-form (housekeeping.html) abgerufen, wenn der Schwellwert
    im Dropdown geändert wird — rendert nur die Tabelle neu, nicht die ganze
    Seite."""
    return templates.TemplateResponse(request, "_stale_entities_body.html", _stale_entities_context(days))


@app.get("/housekeeping", response_class=HTMLResponse)
@_storage_locked(lambda _args: [row["entity_id"] for row in index.list_entities()])
def housekeeping_view(request: Request) -> HTMLResponse:
    """Sammelt Dinge, die niemandem auffallen, solange man nicht gezielt danach
    sucht: ungenutzte Charts/Tabellen (kein Dashboard-Pin), Entitäten mit
    erkannten Duplikaten (bestehender globaler Schnappschuss, siehe
    _duplicate_rows_for_display) und Entitäten ohne neue Werte. Wiederholungen
    sind bewusst noch nicht enthalten (siehe Diskussion zu Schwellwert/
    Kalibrierung)."""
    aggregation_types = {
        row["entity_id"]: row["aggregation_type"] for row in index.list_entities()
    }
    unused_charts = [
        {
            "id": c["id"],
            "name": c["name"],
            "entity_count": len(c["entity_ids"]),
            "range_label": dict(_CHART_RANGE_OPTIONS).get(c["range_key"], c["range_key"]),
            "type_label": _chart_type_label(c, aggregation_types),
        }
        for c in index.list_unused_saved_charts()
    ]
    unused_tables = [
        {"id": t["id"], "name": t["name"], "row_count": t["row_count"], "column_count": t["column_count"]}
        for t in index.list_unused_saved_tables()
    ]
    _, duplicates_by_entity, duplicates_total = _duplicate_rows_for_display()
    return templates.TemplateResponse(
        request,
        "housekeeping.html",
        {
            "unused_charts": unused_charts,
            "unused_tables": unused_tables,
            "chart_count": index.count_saved_charts(),
            "table_count": index.count_saved_tables(),
            "duplicates_by_entity": duplicates_by_entity,
            "duplicates_total": duplicates_total,
            **_stale_entities_context(),
            **_host_disk_usage_context(),
            **_settings_storage_index_context(),
            **_settings_purge_context(),
            **_settings_retention_context(),
            **_settings_rotation_context(),
        },
    )


_INDEX_DETAIL_GROUPS = [
    {
        "label": "Entitäten und Archivstatus",
        "description": (
            "Konfiguration, Anzeigenamen, Einheiten, letzter Wert sowie die vom "
            "Dateibestand abgeleiteten Zeilen- und Größenstände jeder Entität."
        ),
        "tables": ["entities"],
    },
    {
        "label": "Schreibsicherheit und Bereinigung",
        "description": (
            "Idempotenzstatus eingehender Ereignisse und vorgemerkte, noch nicht "
            "physisch entfernte Rohwerte."
        ),
        "tables": ["ingested_events", "deleted_points"],
    },
    {
        "label": "Charts, Tabellen und Dashboards",
        "description": (
            "Gespeicherte Ansichten, Tabellenaufbau, Dashboards sowie Position und "
            "Darstellungsoptionen ihrer Kacheln; keine Messwerte."
        ),
        "tables": [
            "saved_charts", "saved_tables", "table_columns", "table_rows",
            "dashboards", "dashboard_pins",
        ],
    },
    {
        "label": "Statistikverlauf",
        "description": (
            "Stündliche Schnappschüsse von Datenbestand, Speichergröße und optionalem "
            "RAM-Verbrauch für Verlaufsanzeigen."
        ),
        "tables": ["stats_snapshots", "memory_snapshots"],
    },
    {
        "label": "Einstellungen und Wartung",
        "description": (
            "App-Einstellungen sowie Ausführungsverläufe von Backups und "
            "Aufbewahrungsbereinigungen. Import-Reports selbst liegen als JSON-Dateien vor."
        ),
        "tables": ["settings", "backup_jobs", "retention_jobs"],
    },
]


def _statistik_index_context(optimization_result: dict | None = None) -> dict:
    return build_index_detail_context(
        index,
        DATA_DIR / "index.sqlite",
        _INDEX_DETAIL_GROUPS,
        optimization_result,
    )


@app.get("/statistik/index", response_class=HTMLResponse)
def statistik_index_detail(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "statistik_index.html", _statistik_index_context()
    )


@app.post("/statistik/index/optimize", response_class=HTMLResponse)
def statistik_index_optimize(request: Request) -> HTMLResponse:
    """Führt ein ausdrücklich angefordertes, abgesichertes VACUUM aus."""
    result = optimize_index(
        index, DATA_DIR / "index.sqlite", storage_coordinator
    )
    return templates.TemplateResponse(
        request,
        "statistik_index.html",
        _statistik_index_context(optimization_result=result),
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
    total = index.count_entities(search=search or None, type_filter=type_filter, unit_filter=unit_filter)
    pagination = _paginate_meta(total, page, page_size)
    page_matched = index.list_entities(
        search=search or None, type_filter=type_filter, unit_filter=unit_filter, sort=sort, direction=direction,
        limit=pagination["page_size"], offset=(pagination["page"] - 1) * pagination["page_size"],
    )
    rows = [
        {
            "entity_id": row["entity_id"],
            "friendly_name": row["friendly_name"],
            "display_name": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
            "has_custom_name": bool(row["custom_name"]),
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
    ("value_filter", "Wertfilter"),
    ("gap_threshold", "Lücken"),
    ("outlier_threshold", "Ausreißer"),
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
    total = index.count_entities(
        search=search or None, type_filter=type_filter, unit_filter=unit_filter, favorites_only=favorites_only,
    )
    pagination = _paginate_meta(total, page, page_size)
    page_matched = index.list_entities(
        search=search or None, type_filter=type_filter, unit_filter=unit_filter, sort=sort, direction=direction,
        favorites_only=favorites_only,
        limit=pagination["page_size"], offset=(pagination["page"] - 1) * pagination["page_size"],
    )
    rows = [
        {
            "entity_id": row["entity_id"],
            "friendly_name": row["friendly_name"],
            "display_name": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
            "has_custom_name": bool(row["custom_name"]),
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
            # Kurzform statt der vollen Dropdown-Beschriftung ("Gleiche
            # gerundete Werte filtern") — die Spaltenüberschrift "Wertfilter"
            # gibt den Kontext schon vor, in der Tabellenzelle reicht An/Aus.
            "value_filter_label": "An" if row["value_filter"] == "decimals" else "Aus",
            "gap_threshold_label": GAP_THRESHOLD_LABELS.get(row["gap_threshold"], row["gap_threshold"]),
            "outlier_threshold_label": OUTLIER_THRESHOLD_LABELS.get(row["outlier_threshold"], row["outlier_threshold"]),
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
        "value_filter": "col-value-filter",
        "gap_threshold": "col-gap-threshold",
        "outlier_threshold": "col-outlier-threshold",
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


# Der Wertänderungsfilter überspringt unveränderte Werte, garantiert aber
# spätestens alle VALUE_FILTER_HEARTBEAT_SECONDS ein Lebenszeichen (siehe
# should_accept_value() in storage/index.py) — eine kürzere Lücken-Schwelle
# würde dieses regelmäßige, harmlose Schweigen fälschlich als Lücke melden.
# Aus der Sekunden-Konstante abgeleitet statt separat gepflegt, damit beide
# nie auseinanderlaufen können.
_VALUE_FILTER_GAP_FLOOR_MINUTES = str(VALUE_FILTER_HEARTBEAT_SECONDS // 60)


def _should_raise_gap_threshold_for_value_filter(current_gap_threshold: str) -> bool:
    """True, wenn `current_gap_threshold` enger als der Wertänderungsfilter-
    Herzschlag ist UND nicht "off" — dann würde der Filter selbst regelmäßig
    Fehlalarme auf der Bereinigungs-Seite auslösen. Nur ein Vorschlag/Guard
    beim AKTIVIEREN des Filters (siehe update_entity_config/
    settings_archivierung), keine dauerhafte Sperre — danach lässt sich die
    Lücken-Erkennung jederzeit wieder manuell verkleinern."""
    return (
        current_gap_threshold != "off"
        and current_gap_threshold.isdigit()
        and int(current_gap_threshold) < int(_VALUE_FILTER_GAP_FLOOR_MINUTES)
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
        "custom_name": entity["custom_name"] or "",
        "custom_name_max_length": MAX_CUSTOM_NAME_LENGTH,
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
    custom_name = form.get("custom_name")
    if custom_name is not None:
        custom_name = custom_name.strip()
        if len(custom_name) > MAX_CUSTOM_NAME_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Der Anzeigename darf höchstens {MAX_CUSTOM_NAME_LENGTH} Zeichen lang sein",
            )
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
                custom_name=custom_name if custom_name is not None else None,
            )
            if retention is not None:
                _invalidate_retention_overview()
            # Nur auslösen, wenn der Filter in DIESEM Request aktiviert wird
            # (nicht bei jedem Speichern, solange er schon aktiv ist) — sonst
            # würde ein späteres, bewusstes Verkleinern der Lücken-Erkennung
            # bei der nächsten Gelegenheit einfach wieder zurückgedreht.
            gap_threshold_auto_adjusted = False
            if value_filter == "decimals":
                current_gap = _require_entity(entity_id)["gap_threshold"]
                if _should_raise_gap_threshold_for_value_filter(current_gap):
                    index.set_config(entity_id, gap_threshold=_VALUE_FILTER_GAP_FLOOR_MINUTES)
                    gap_threshold_auto_adjusted = True
            entity = _require_entity(entity_id)
            context = _entity_config_context(entity)
            context["saved"] = True
            context["gap_threshold_auto_adjusted"] = gap_threshold_auto_adjusted
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


class _EntityChartOptionsBody(BaseModel):
    continuous: bool
    raw: bool
    chart_type: str
    show_points: bool
    show_values: bool
    dynamic_y_axis: bool
    chart_stats: bool
    legend_metrics: list[str]
    legend_style: str
    decimals: str


@app.post("/entities/{entity_id}/chart-options")
def entity_set_chart_options(entity_id: str, body: _EntityChartOptionsBody) -> dict:
    """Speichert die aktuellen Chart-Optionen (Optionen-Menü, entity_detail.html)
    als vollständigen Snapshot für diese Entität — jede Änderung im Menü
    schickt sofort den gesamten aktuellen Stand, kein separater Speichern-
    Button (siehe Kommentar bei _resolve_entity_chart_options())."""
    _require_entity(entity_id)
    data = body.model_dump()
    _validate_entity_chart_options(data)
    index.set_entity_chart_options(entity_id, data)
    return {"ok": True}


@app.post("/entities/{entity_id}/chart-options/reset")
def entity_reset_chart_options(entity_id: str) -> dict:
    """"Auf Standard zurücksetzen" (Optionen-Menü) — wirft die individuelle
    Übersteuerung weg, die Entität folgt danach wieder live den globalen
    Defaults."""
    _require_entity(entity_id)
    index.set_entity_chart_options(entity_id, {})
    return {"ok": True}


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
_CHART_RESOLUTION_PRESETS = {"auto", "medium", "coarse", "full"}
_CHART_LEGEND_METRICS = {"last", "min", "max", "average", "sum"}
_CHART_LEGEND_STYLES = {"chips", "table"}
# "timeline" nur clientseitig erzwingbar, wenn tatsächlich alle Serien
# Schalter sind (siehe allSwitch-Getter in chart_editor.html) — hier nur
# generell als gültiger Wert zugelassen, dieselbe Konvention wie
# _ENTITY_CHART_TYPES oben.
_CHART_EDITOR_CHART_TYPES = {"auto", "timeline"}

# Optionen-Menü der Entität-eigenen Chart-Seite (entity_detail.html) — im
# Gegensatz zum Chart-Editor (saved_charts, ein Feld pro Chart) hier zweistufig:
# ein globaler Default (Setting "entity_chart_defaults", Einstellungen →
# Darstellung) plus eine optionale, vollständige Übersteuerung pro Entität
# (entities.chart_options), siehe _resolve_entity_chart_options() unten.
_ENTITY_CHART_TYPES = {"auto", "line", "bar", "timeline"}
_ENTITY_CHART_OPTION_DEFAULTS = {
    "continuous": False,
    "raw": False,
    "chart_type": "auto",
    "show_points": False,
    "show_values": False,
    "dynamic_y_axis": False,
    "chart_stats": True,
    "legend_metrics": ["last", "min", "max", "average", "sum"],
    "legend_style": "chips",
    "decimals": "auto",
}
_ENTITY_CHART_DECIMALS = {"auto", "0", "1", "2", "3"}
_ON_OFF_LABELS = {"1": "An", "0": "Aus"}
_ENTITY_LEGEND_STYLE_LABELS = {"chips": "Chips", "table": "Tabelle"}
_ENTITY_LEGEND_METRIC_LABELS = {"last": "Aktuell", "min": "Min", "max": "Max", "average": "Durchschnitt", "sum": "Summe"}


def _validate_entity_chart_options(data: dict) -> None:
    if "chart_type" in data and data["chart_type"] not in _ENTITY_CHART_TYPES:
        raise HTTPException(status_code=400, detail="Ungültiger Diagrammtyp")
    if "legend_metrics" in data and not set(data["legend_metrics"]) <= _CHART_LEGEND_METRICS:
        raise HTTPException(status_code=400, detail="Ungültige Legenden-Kennzahl")
    if "legend_style" in data and data["legend_style"] not in _CHART_LEGEND_STYLES:
        raise HTTPException(status_code=400, detail="Ungültiger Legenden-Stil")
    if "decimals" in data and data["decimals"] not in _ENTITY_CHART_DECIMALS:
        raise HTTPException(status_code=400, detail="Ungültige Nachkommastellen")


def _get_entity_chart_defaults() -> dict:
    defaults = dict(_ENTITY_CHART_OPTION_DEFAULTS)
    raw = index.get_setting("entity_chart_defaults")
    if raw:
        try:
            stored = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            stored = {}
        for key in defaults:
            if key in stored:
                defaults[key] = stored[key]
    return defaults


def _update_entity_chart_default(key: str, value) -> None:
    current = _get_entity_chart_defaults()
    current[key] = value
    index.set_setting("entity_chart_defaults", json.dumps(current))


def _resolve_entity_chart_options(entity) -> dict:
    """Effektive Chart-Optionen einer Entität — globale Defaults, von den
    (vollständigen, siehe set_entity_chart_options()) Overrides der Entität
    übersteuert, sobald diese mindestens einmal geändert wurden."""
    options = _get_entity_chart_defaults()
    raw = entity["chart_options"] if entity is not None else None
    if raw:
        try:
            overrides = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            overrides = {}
        for key in options:
            if key in overrides:
                options[key] = overrides[key]
    return options


def _chart_type_label(chart: dict, aggregation_types: dict[str, str]) -> str:
    """Wie das Chart tatsächlich gezeichnet wird, für die Übersichtskachel.

    Gespeichert ist nur "auto" oder "timeline" — bei "auto" entscheidet der
    Aggregationstyp JEDER Entität einzeln (Zähler/Schalter → Balken, sonst
    Linie, dieselbe Regel wie _resolved_chart_type() in storage/query.py).
    Ein Chart kann deshalb beides zugleich enthalten."""
    if chart["chart_type"] == "timeline":
        return "Zeitstrahl"
    vorhanden = {
        "Balken" if aggregation_types.get(entity_id) in ("counter", "switch") else "Linie"
        for entity_id in chart["entity_ids"]
    }
    # Feste Reihenfolge statt der ungeordneten Menge, damit ein gemischtes
    # Chart nicht mal "Linie + Balken" und mal "Balken + Linie" anzeigt.
    return " + ".join(typ for typ in ("Linie", "Balken") if typ in vorhanden)


@app.get("/charts", response_class=HTMLResponse)
def charts_list(request: Request) -> HTMLResponse:
    charts = index.list_saved_charts()
    # Einmal alle Entitätstypen holen statt je Chart einzeln nachzuschlagen.
    aggregation_types = {
        row["entity_id"]: row["aggregation_type"] for row in index.list_entities()
    }
    rows = [
        {
            "id": c["id"],
            "name": c["name"],
            "entity_count": len(c["entity_ids"]),
            "range_label": dict(_CHART_RANGE_OPTIONS).get(c["range_key"], c["range_key"]),
            "type_label": _chart_type_label(c, aggregation_types),
            # Nur für "Neueste/Älteste zuerst" im Browser (card-browser.js),
            # nicht zum Anzeigen — deshalb roh statt formatiert.
            "created_at": c["created_at"],
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
        "legend_metrics": chart["legend_metrics"] if chart else ["sum"],
        "legend_style": chart["legend_style"] if chart else "chips",
        "chart_type": chart["chart_type"] if chart else "auto",
        "decimals": chart["decimals"] if chart else "auto",
        "show_values": chart["show_values"] if chart else False,
        "entity_names": chart["entity_names"] if chart else {},
        "entity_options": entity_options,
        "range_options": _CHART_RANGE_OPTIONS,
        "dashboard_usage": index.list_item_dashboards("chart", chart["id"]) if chart else [],
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
    legend_metrics: list[str] = ["sum"]
    legend_style: str = "chips"
    chart_type: str = "auto"
    decimals: str = "auto"
    show_values: bool = False


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
    if not set(body.legend_metrics) <= _CHART_LEGEND_METRICS:
        raise HTTPException(status_code=400, detail="Ungültige Legenden-Kennzahl")
    if body.legend_style not in _CHART_LEGEND_STYLES:
        raise HTTPException(status_code=400, detail="Ungültiger Legenden-Stil")
    if body.chart_type not in _CHART_EDITOR_CHART_TYPES:
        raise HTTPException(status_code=400, detail="Ungültiger Diagrammtyp")
    if body.decimals not in _ENTITY_CHART_DECIMALS:
        raise HTTPException(status_code=400, detail="Ungültige Nachkommastellen")
    entity_names = {k: v.strip() for k, v in body.entity_names.items() if v.strip()}
    chart_id = index.create_saved_chart(
        body.name.strip(), body.entity_ids, body.range_key, body.continuous,
        entity_names, body.resolution_preset, body.dynamic_y_axis,
        chart_stats=body.chart_stats, legend_metrics=body.legend_metrics,
        legend_style=body.legend_style, chart_type=body.chart_type,
        decimals=body.decimals, show_values=body.show_values,
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
    if not set(body.legend_metrics) <= _CHART_LEGEND_METRICS:
        raise HTTPException(status_code=400, detail="Ungültige Legenden-Kennzahl")
    if body.legend_style not in _CHART_LEGEND_STYLES:
        raise HTTPException(status_code=400, detail="Ungültiger Legenden-Stil")
    if body.chart_type not in _CHART_EDITOR_CHART_TYPES:
        raise HTTPException(status_code=400, detail="Ungültiger Diagrammtyp")
    if body.decimals not in _ENTITY_CHART_DECIMALS:
        raise HTTPException(status_code=400, detail="Ungültige Nachkommastellen")
    entity_names = {k: v.strip() for k, v in body.entity_names.items() if v.strip()}
    index.update_saved_chart(
        chart_id, body.name.strip(), body.entity_ids, body.range_key,
        body.continuous, entity_names, body.resolution_preset,
        body.dynamic_y_axis, chart_stats=body.chart_stats, legend_metrics=body.legend_metrics,
        legend_style=body.legend_style, chart_type=body.chart_type,
        decimals=body.decimals, show_values=body.show_values,
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


@app.post("/charts/{chart_id}/duplicate")
def charts_duplicate(chart_id: int) -> dict:
    """Kopie bleibt bewusst unfavorisiert (create_saved_chart() setzt keinen
    is_favorite-Wert) — sonst gäbe es nach dem Duplizieren eines Favoriten
    zwei inhaltsgleiche favorisierte Karten."""
    chart = index.get_saved_chart(chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    new_id = index.create_saved_chart(
        index.copy_name_for("saved_charts", chart["name"]),
        chart["entity_ids"], chart["range_key"], chart["continuous"],
        chart["entity_names"], chart["resolution_preset"], chart["dynamic_y_axis"],
        chart_stats=chart["chart_stats"], legend_metrics=chart["legend_metrics"],
        legend_style=chart["legend_style"], chart_type=chart["chart_type"],
        decimals=chart["decimals"], show_values=chart["show_values"],
    )
    return {"id": new_id}


def _dashboard_tiles_context(
    dashboard_id: int, base: str = ".", auto_open_entity_id: str | None = None
) -> dict:
    """Für die Dashboard-Kacheln einer Dashboard-Seite (Konzept "Offene
    Punkte", erweitert um Vergleichstabellen UND um mehrere unabhängige
    Dashboards) — sowohl vom initialen Laden von "/"/"/dashboards/{id}" als
    auch von pin/unpin/reorder genutzt (alle geben dasselbe Fragment zurück,
    damit eine Änderung nicht per Extra-Request neu geladen werden muss).
    dashboard_pins kennt zwei item_type-Werte ('chart'/'table'), hier gegen
    die jeweilige Tabelle aufgelöst — ein verwaister Pin (Chart/Tabelle
    zwischenzeitlich gelöscht, sollte durch die Bereinigung in
    delete_saved_chart()/delete_saved_table() praktisch nie vorkommen) wird
    dabei still übersprungen statt einen Fehler zu werfen. base ist der
    relative Rückweg zur App-Wurzel (siehe "base"-Konvention in chart_editor()/
    table_editor()) — "." auf "/", ".." auf "/dashboards/{id}", damit dasselbe
    Fragment auf beiden Seitentiefen funktionierende Links erzeugt."""
    pins = index.list_dashboard_pins(dashboard_id)
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
                "show_legend": bool(p["show_legend"]),
                # Kachel-Legende übernimmt Aussehen UND Inhalte 1:1 von der
                # zugrundeliegenden Chart-Seite (chart_editor.html) — welche
                # Kennzahlen (Min/Max/Ø/Summe/Aktuell), ob überhaupt welche
                # gezeigt werden, und Chips vs. Tabelle sind dort konfiguriert,
                # nicht hier erneut.
                "chart_stats": c["chart_stats"], "legend_metrics": c["legend_metrics"],
                "legend_style": c["legend_style"], "chart_type": c["chart_type"],
                "show_values": c["show_values"], "decimals": c["decimals"],
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
        elif p["item_type"] == "entity":
            e = index.get_entity(p["item_entity_id"])
            if e is None:
                continue
            is_switch = e["aggregation_type"] == "switch"
            # Kachel-Override hat Vorrang vor der entity-eigenen Einstellung,
            # "auto" (Feld-Default) bedeutet ausdrücklich "entity-eigenen Wert
            # übernehmen" statt selbst "auto" an format_value() zu reichen.
            effective_decimals = p["decimals"] if p["decimals"] != "auto" else (e["decimals"] or "auto")
            if e["last_value"] is None:
                value_text = "–"
            elif is_switch:
                # Momentaner Zustand — anders als der chart-eigene
                # display_mode='time' (Summe der Einschaltdauer über einen
                # Zeitraum, siehe dashboard-tiles.js isDuration) geht es hier
                # um "gerade an oder aus", das display_mode nicht berührt.
                value_text = "An" if e["last_value"] else "Aus"
            else:
                value_text = format_value(e["last_value"], decimals_to_int(effective_decimals))
            seconds_ago = (time.time() - e["last_ts"]) if e["last_ts"] is not None else None
            # Zwei Schwellen (Konzept-Wunsch): 15 Minuten (gelb) und eine
            # Stunde (rot) — unabhängig vom Auflösungsintervall der Entität,
            # bewusst feste, für den Nutzer nachvollziehbare Werte statt einer
            # datenabhängigen Heuristik. Nur der Kartenrahmen zeigt das an
            # (siehe .dtile-entity.is-warn/is-stale), der Wert selbst bleibt
            # immer schwarz.
            if seconds_ago is None:
                staleness = "fresh"
            elif seconds_ago > 3600:
                staleness = "stale"
            elif seconds_ago > 900:
                staleness = "warn"
            else:
                staleness = "fresh"
            tiles.append({
                "kind": "entity", "entity_id": e["entity_id"],
                "name": p["title"] or entity_display_name(e["entity_id"], e["friendly_name"], e["custom_name"]),
                # Roher Override fürs Titel-Eingabefeld im Kachelmenü — anders
                # als "name" oben (mit friendly_name-Fallback) soll das Feld
                # leer bleiben, solange kein eigener Titel gesetzt ist.
                "custom_title": p["title"] or "",
                "value_text": value_text, "unit": "" if is_switch else (e["unit"] or ""),
                "is_switch": is_switch, "decimals": effective_decimals,
                "age_text": f"vor {format_uptime(seconds_ago)}" if seconds_ago is not None else "nie",
                "staleness": staleness,
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
                "show_sparkline": bool(p["show_sparkline"]), "show_age": bool(p["show_age"]),
                "sparkline_resolution": p["sparkline_resolution"],
            })
    pinned_chart_ids = {p["item_id"] for p in pins if p["item_type"] == "chart"}
    pinned_table_ids = {p["item_id"] for p in pins if p["item_type"] == "table"}
    pinned_entity_ids = {p["item_entity_id"] for p in pins if p["item_type"] == "entity"}
    dashboard = index.get_dashboard(dashboard_id)
    dashboard_locked = bool(dashboard["locked"]) if dashboard else False
    dashboard_precise = bool(dashboard["precise_mode"]) if dashboard else False
    dashboard_fill_gaps = bool(dashboard["fill_gaps"]) if dashboard else False
    all_entities = [
        {
            "entity_id": row["entity_id"],
            "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
            "ha_name": row["friendly_name"] or row["entity_id"],
            "is_custom": bool(row["custom_name"]),
        }
        for row in index.list_entities()
    ]
    return {
        "dashboard_id": dashboard_id,
        "dashboard_name": dashboard["name"] if dashboard else "Dashboard",
        "dashboard_locked": dashboard_locked,
        # Präziser Modus: Gitter/Zeilenhöhe halbiert (.dashboard-grid.is-precise),
        # Größen-Picker geht dann bis 6x6 statt 3x3 (siehe _dashboard_tile_menu.html).
        "dashboard_precise": dashboard_precise,
        # Lücken auffüllen: grid-auto-flow: dense (.dashboard-grid.is-dense) —
        # unabhängig vom Präzisen Modus, beide lassen sich frei kombinieren.
        "dashboard_fill_gaps": dashboard_fill_gaps,
        "base": base,
        "tiles": tiles,
        "auto_open_entity_id": auto_open_entity_id,
        "entity_pin_options": [
            {**row, "pinned": row["entity_id"] in pinned_entity_ids}
            for row in all_entities
        ],
        # Fixiertes Dashboard: keine neuen Kacheln anheften, siehe dashboard_locked
        # in _dashboard_tiles.html/_dashboard_tile_menu.html für die restlichen
        # Layout-Aktionen (Größe/Entfernen/Umsortieren).
        "can_add_tile": len(tiles) < index.DASHBOARD_TILE_LIMIT and not dashboard_locked,
        "unpinned_charts": [c for c in index.list_saved_charts() if c["id"] not in pinned_chart_ids],
        "unpinned_tables": [t for t in index.list_saved_tables() if t["id"] not in pinned_table_ids],
        # Werte-Kachel-Picker filtert client-seitig per Suchfeld (siehe
        # dashboard-tiles.js setupEntityPinSearch()) statt eines eigenen
        # Server-Roundtrips — dieselbe Größenordnung wie die Entitätenliste
        # anderswo in der App (Tabellen-Editor-Picker), kein Pagination-Bedarf.
        "unpinned_entities": [row for row in all_entities if row["entity_id"] not in pinned_entity_ids],
    }


def _require_dashboard_unlocked(dashboard_id: int) -> None:
    """Lehnt Layout-ändernde Aktionen (Pin/Unpin/Resize/Reorder) auf einem
    fixierten Dashboard serverseitig ab — nicht nur die Bedienelemente
    ausblenden, sonst könnte ein offener alter Tab oder ein direkter
    API-Aufruf die Sperre umgehen. 423 (Locked) statt 403/409, weil das
    genau diesen Fall beschreibt: die Ressource existiert und der Aufrufer
    ist berechtigt, ist aber aktiv gesperrt."""
    dashboard = index.get_dashboard(dashboard_id)
    if dashboard is not None and dashboard["locked"]:
        raise HTTPException(status_code=423, detail="Dashboard ist fixiert — Layout-Änderungen sind gesperrt")


def _get_dashboard_or_404(dashboard_id: int) -> dict:
    dashboard = index.get_dashboard(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return dashboard


@app.post("/charts/{chart_id}/pin", response_class=HTMLResponse)
def charts_pin(request: Request, chart_id: int, dashboard_id: int = 1, base: str = ".") -> HTMLResponse:
    if index.get_saved_chart(chart_id) is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.pin_item_to_dashboard(dashboard_id, "chart", chart_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id, base))


@app.post("/charts/{chart_id}/unpin", response_class=HTMLResponse)
def charts_unpin(request: Request, chart_id: int, dashboard_id: int = 1, base: str = ".") -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    index.unpin_item_from_dashboard(dashboard_id, "chart", chart_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id, base))


class _DashboardPinRef(BaseModel):
    item_type: str
    item_id: int
    item_entity_id: str | None = None


class _ReorderDashboardBody(BaseModel):
    dashboard_id: int = 1
    pins: list[_DashboardPinRef]


class _ResizeDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    item_type: str
    item_id: int
    # Obergrenze 6 (Präziser Modus) statt 3 — die eigentliche, vom aktuellen
    # Modus abhängige Grenze prüft index.set_dashboard_pin_size() (max_size).
    grid_cols: int = Field(ge=1, le=6)
    grid_rows: int = Field(ge=1, le=6)


@app.post("/dashboard/reorder")
def dashboard_reorder(body: _ReorderDashboardBody) -> dict:
    """Persistiert die per Drag&Drop auf einer Dashboard-Seite geänderte
    Kachel-Reihenfolge — das Frontend hat die Kacheln zu diesem Zeitpunkt
    schon live im DOM umsortiert (dashboard-tiles.js), dieser Aufruf schreibt
    das nur noch fest, ohne selbst ein neues Fragment zurückzugeben. Ein
    eigener Pfad statt "/charts/reorder"/"/tables/reorder", weil eine
    Kachel-Reihenfolge Charts UND Tabellen gemischt enthalten kann."""
    _require_dashboard_unlocked(body.dashboard_id)
    index.reorder_dashboard_pins(body.dashboard_id, [(p.item_type, p.item_id, p.item_entity_id) for p in body.pins])
    return {"ok": True}


@app.post("/dashboard/size")
def dashboard_size(body: _ResizeDashboardTileBody) -> dict:
    if body.item_type not in {"chart", "table"}:
        raise HTTPException(status_code=422, detail="Ungültiger Dashboard-Kacheltyp")
    _require_dashboard_unlocked(body.dashboard_id)
    dashboard = index.get_dashboard(body.dashboard_id)
    max_size = 6 if dashboard and dashboard["precise_mode"] else 3
    try:
        updated = index.set_dashboard_pin_size(
            body.dashboard_id, body.item_type, body.item_id, body.grid_cols, body.grid_rows, max_size=max_size
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if not updated:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "grid_cols": body.grid_cols, "grid_rows": body.grid_rows}


class _LegendDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    item_type: str
    item_id: int
    show_legend: bool


@app.post("/dashboard/legend")
def dashboard_legend(body: _LegendDashboardTileBody) -> dict:
    if body.item_type != "chart":
        raise HTTPException(status_code=422, detail="Legende ist nur für Charts verfügbar")
    _require_dashboard_unlocked(body.dashboard_id)
    if not index.set_dashboard_pin_legend(
        body.dashboard_id, body.item_type, body.item_id, body.show_legend
    ):
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "show_legend": body.show_legend}


# -- Werte-Kacheln (item_type='entity'): eine Entität direkt anheften, ohne
# zuerst ein Chart/eine Tabelle anzulegen (Konzept-Erweiterung). Eigene Routen
# statt die obigen chart/table-Endpunkte zu erweitern, weil eine entity_id
# (TEXT) statt einer Integer-item_id identifiziert wird. ---------------------

@app.post("/dashboard/pin-entity/{entity_id}", response_class=HTMLResponse)
def dashboard_pin_entity(request: Request, entity_id: str, dashboard_id: int = 1, base: str = ".") -> HTMLResponse:
    _require_entity(entity_id)
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.pin_entity_to_dashboard(dashboard_id, entity_id)
    return templates.TemplateResponse(
        request, "_dashboard_tiles.html",
        _dashboard_tiles_context(dashboard_id, base, auto_open_entity_id=entity_id),
    )


@app.post("/dashboard/entity/{entity_id}", response_class=HTMLResponse)
async def dashboard_entity_change(
    request: Request, entity_id: str, dashboard_id: int = 1, base: str = "."
) -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    form = await request.form()
    new_entity_id = str(form.get("new_entity_id", "")).strip()
    _require_entity(new_entity_id)
    try:
        updated = index.set_dashboard_entity_pin_entity(dashboard_id, entity_id, new_entity_id)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    if not updated:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return templates.TemplateResponse(
        request, "_dashboard_tiles.html",
        _dashboard_tiles_context(dashboard_id, base, auto_open_entity_id=new_entity_id),
    )


@app.post("/dashboard/unpin-entity/{entity_id}", response_class=HTMLResponse)
def dashboard_unpin_entity(request: Request, entity_id: str, dashboard_id: int = 1, base: str = ".") -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    index.unpin_entity_from_dashboard(dashboard_id, entity_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id, base))


class _ResizeDashboardEntityTileBody(BaseModel):
    dashboard_id: int = 1
    entity_id: str
    grid_cols: int = Field(ge=1, le=6)
    grid_rows: int = Field(ge=1, le=6)


@app.post("/dashboard/entity-size")
def dashboard_entity_size(body: _ResizeDashboardEntityTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    dashboard = index.get_dashboard(body.dashboard_id)
    max_size = 6 if dashboard and dashboard["precise_mode"] else 3
    try:
        updated = index.set_dashboard_entity_pin_size(
            body.dashboard_id, body.entity_id, body.grid_cols, body.grid_rows, max_size=max_size
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if not updated:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "grid_cols": body.grid_cols, "grid_rows": body.grid_rows}


class _SparklineDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    entity_id: str
    show_sparkline: bool


@app.post("/dashboard/sparkline")
def dashboard_sparkline(body: _SparklineDashboardTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    if not index.set_dashboard_entity_pin_sparkline(body.dashboard_id, body.entity_id, body.show_sparkline):
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "show_sparkline": body.show_sparkline}


class _SparklineResolutionDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    entity_id: str
    resolution: str


@app.post("/dashboard/sparkline-resolution")
def dashboard_sparkline_resolution(body: _SparklineResolutionDashboardTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    try:
        updated = index.set_dashboard_entity_pin_sparkline_resolution(
            body.dashboard_id, body.entity_id, body.resolution
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if not updated:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "resolution": body.resolution}


class _ShowAgeDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    entity_id: str
    show_age: bool


@app.post("/dashboard/entity-show-age")
def dashboard_entity_show_age(body: _ShowAgeDashboardTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    if not index.set_dashboard_entity_pin_show_age(body.dashboard_id, body.entity_id, body.show_age):
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "show_age": body.show_age}


class _DecimalsDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    entity_id: str
    decimals: str


@app.post("/dashboard/entity-decimals")
def dashboard_entity_decimals(body: _DecimalsDashboardTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    try:
        updated = index.set_dashboard_entity_pin_decimals(body.dashboard_id, body.entity_id, body.decimals)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if not updated:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "decimals": body.decimals}


class _TitleDashboardTileBody(BaseModel):
    dashboard_id: int = 1
    entity_id: str
    title: str = ""


@app.post("/dashboard/entity-title")
def dashboard_entity_title(body: _TitleDashboardTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    title = body.title.strip()
    if not index.set_dashboard_entity_pin_title(body.dashboard_id, body.entity_id, title or None):
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    return {"ok": True, "title": title}


# -- Dashboards (Konzept "Dashboards"-Menüpunkt: mehrere, unabhängige
# Dashboards zusätzlich zur festen Übersichtsseite "/", siehe dashboards-
# Tabelle in storage/index.py). ---------------------------------------------

class _DashboardCreateBody(BaseModel):
    name: str


class _DashboardRenameBody(BaseModel):
    name: str


@app.get("/dashboards", response_class=HTMLResponse)
def dashboards_list(request: Request) -> HTMLResponse:
    dashboards = index.list_dashboards()
    pin_counts = {d["id"]: index.count_dashboard_pins(d["id"]) for d in dashboards}
    return templates.TemplateResponse(
        request, "dashboards.html", {
            "dashboards": dashboards,
            "pin_counts": pin_counts,
            # Für die feste Energiedashboard-Kachel (kein echtes Dashboard,
            # siehe energiedashboard_routes.py) — "enabled" kommt bereits
            # global aus _nav_dashboards_context, "configured" nur hier.
            "energiedashboard_configured": is_energiedashboard_configured(index),
            "energiedashboard_role_count": energiedashboard_role_count(index),
        }
    )


@app.post("/dashboards")
def dashboards_create(body: _DashboardCreateBody) -> dict:
    # Ohne Eingabe einen freien Vorgabenamen wählen ("Neues Dashboard 2", …)
    # statt an der Eindeutigkeitsprüfung zu scheitern — der Nutzer hat hier
    # ja gerade KEINEN Namen genannt, den man ihm zurückweisen könnte.
    name = body.name.strip() or index.free_name_for("dashboards", "Neues Dashboard")
    dashboard_id = index.create_dashboard(name)
    return {"id": dashboard_id, "name": name}


@app.get("/dashboards/new", response_class=HTMLResponse)
def dashboards_new(request: Request) -> HTMLResponse:
    # Muss VOR "/dashboards/{dashboard_id}" registriert sein, sonst würde
    # dieser Pfad zuerst dort landen und an der int-Konvertierung von "new"
    # scheitern (siehe dasselbe Muster bei /charts/new vor /charts/{chart_id}).
    return templates.TemplateResponse(request, "dashboard_editor.html", {"dashboard": None, "base": ".."})


@app.get("/dashboards/{dashboard_id}", response_class=HTMLResponse)
def dashboard_detail(request: Request, dashboard_id: int) -> HTMLResponse:
    dashboard = _get_dashboard_or_404(dashboard_id)
    context = {
        "dashboard": dashboard,
        **_dashboard_tiles_context(dashboard_id, base=".."),
    }
    return templates.TemplateResponse(request, "dashboard_detail.html", context)


@app.get("/dashboards/{dashboard_id}/edit", response_class=HTMLResponse)
def dashboards_edit(request: Request, dashboard_id: int) -> HTMLResponse:
    dashboard = _get_dashboard_or_404(dashboard_id)
    return templates.TemplateResponse(
        request, "dashboard_editor.html", {"dashboard": dashboard, "base": "../.."}
    )


@app.post("/dashboards/{dashboard_id}/rename")
def dashboards_rename(dashboard_id: int, body: _DashboardRenameBody) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name darf nicht leer sein")
    if not index.rename_dashboard(dashboard_id, name):
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return {"ok": True, "name": name}


class _DashboardLockBody(BaseModel):
    locked: bool


@app.post("/dashboards/{dashboard_id}/lock")
def dashboards_lock(dashboard_id: int, body: _DashboardLockBody) -> dict:
    """Fixieren/Entfixieren bleibt unabhängig vom Sperrstatus selbst immer
    möglich (sonst gäbe es kein Zurück aus einem fixierten Dashboard) — nur
    Kachel-Layout-Aktionen (Pin/Unpin/Resize/Reorder) werden durch
    _require_dashboard_unlocked() blockiert, nicht diese Route hier."""
    if not index.set_dashboard_locked(dashboard_id, body.locked):
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return {"ok": True, "locked": body.locked}


class _DashboardPreciseModeBody(BaseModel):
    precise_mode: bool


@app.post("/dashboards/{dashboard_id}/precise-mode")
def dashboards_precise_mode(dashboard_id: int, body: _DashboardPreciseModeBody) -> dict:
    if not index.set_dashboard_precise_mode(dashboard_id, body.precise_mode):
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return {"ok": True, "precise_mode": body.precise_mode}


class _DashboardFillGapsBody(BaseModel):
    fill_gaps: bool


@app.post("/dashboards/{dashboard_id}/fill-gaps")
def dashboards_fill_gaps(dashboard_id: int, body: _DashboardFillGapsBody) -> dict:
    if not index.set_dashboard_fill_gaps(dashboard_id, body.fill_gaps):
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return {"ok": True, "fill_gaps": body.fill_gaps}


@app.post("/dashboards/{dashboard_id}/delete")
def dashboards_delete(dashboard_id: int) -> dict:
    if not index.delete_dashboard(dashboard_id):
        raise HTTPException(status_code=400, detail="Dashboard kann nicht gelöscht werden")
    return {"ok": True}


@app.post("/dashboards/{dashboard_id}/favorite")
def dashboards_favorite_toggle(dashboard_id: int) -> dict:
    dashboard = index.get_dashboard(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    new_state = not dashboard["is_favorite"]
    index.set_dashboard_favorite(dashboard_id, new_state)
    return {"is_favorite": new_state}


@app.post("/dashboards/{dashboard_id}/duplicate")
def dashboards_duplicate(dashboard_id: int) -> dict:
    new_id = index.duplicate_dashboard(dashboard_id)
    if new_id is None:
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return {"id": new_id}


@app.post("/dashboards/{dashboard_id}/set-default")
def dashboards_set_default(dashboard_id: int) -> dict:
    if not index.set_default_dashboard(dashboard_id):
        raise HTTPException(status_code=404, detail="Dashboard nicht gefunden")
    return {"ok": True}


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
    # Dieselbe Konvention wie das entity-eigene "Nachkommastellen"-Feld
    # (formatting.DECIMALS_LABELS, siehe _entity_config_form.html): "auto"
    # oder eine Ziffer als String, rein für die Anzeige in dieser Spalte.
    decimals: str = "auto"
    # Ausgeblendet heißt: nicht in der Vorschau/Kachel gerendert, ABER
    # weiterhin mitberechnet (siehe TableCompute.computeValues()) — eine
    # ausgeblendete Vergleichsspalte (Versatz/Vorjahr) liefert ihre
    # Abweichung so trotzdem an der Basisspalte.
    hidden: bool = False
    # Leer = keine übergreifende Kopfzeile. Zwei oder mehr direkt
    # aufeinanderfolgende Spalten mit demselben (nicht-leeren) group_label
    # bekommen in der Vorschau/Kachel eine gemeinsame, überspannende
    # Kopfzeile darüber (z. B. "2025" über mehreren Monatsspalten).
    group_label: str = ""
    # Färbt die Zellen dieser Spalte nach ihrem Wert ein (heller = niedrig,
    # kräftiger = hoch), relativ zu den anderen Zeilen DERSELBEN Spalte im
    # selben Abschnitt — bewusst spaltenweise statt zeilenweise (war
    # ursprünglich _TableRowBody.heatmap): eine Zeile enthält oft sehr
    # unterschiedliche Größenordnungen nebeneinander (Tag vs. Jahr), ein
    # Vergleich über die eigene Zeile hinweg wäre irreführend. Sinnvoll ist
    # der Vergleich mehrerer Zeilen INNERHALB derselben Spalte.
    heatmap: bool = False
    # Manuell gezogene Spaltenbreite in Pixeln (siehe table_editor.html
    # Ziehgriff am rechten Spaltenrand) — None/nicht gesetzt heißt automatische
    # Breite (Standard-Tabellenlayout, richtet sich nach dem Inhalt).
    width: int | None = None


_TABLE_ROW_AGGREGATIONS = ("auto", "avg", "min", "max", "sum")
# Zeilenbeschriftung/Abschnittsname — dieselbe Zahl wie das maxlength-Attribut
# des Felds in table_editor.html, hier zusätzlich serverseitig durchgesetzt
# (ein Request kann das clientseitige maxlength umgehen).
MAX_TABLE_ROW_LABEL_LENGTH = 30


class _TableRowBody(BaseModel):
    label: str
    row_type: str
    entity_ids: list[EntityId] = []
    formula: str = ""
    formula_unit: str = ""
    bold: bool = False
    # Nur für row_type "entity"/"group" relevant — "auto" ist das bisherige,
    # implizite Verhalten (Zähler/Schalter -> Summe, sonst Durchschnitt).
    aggregation: str = "auto"
    # Dieselbe Bedeutung wie bei _TableColumnBody.hidden — ausgeblendete
    # Zeilen bleiben Teil der Buchstaben-Zuordnung (rowLetters()), damit eine
    # Formel, die auf eine versteckte Hilfszeile verweist, nicht bricht. Gilt
    # auch für row_type "separator" (rein optisch, keine Berechnung betroffen).
    hidden: bool = False
    # Nur für row_type "separator" relevant — ob der Abschnittsname (label)
    # als Überschrift angezeigt wird. War früher ein einzelner globaler
    # Schalter (_TableStyleBody.separator_labels), jetzt pro Trennlinie
    # einstellbar statt für alle gleichzeitig.
    show_label: bool = False
    # Nur für row_type "formula" relevant — dieselbe optische Hervorhebung
    # wie die frühere globale _TableStyleBody.formula_row_accent, jetzt pro
    # Formelzeile einstellbar.
    accent: bool = False
    # Nur für row_type "entity"/"group" relevant — zeigt statt des absoluten
    # Werts den prozentualen Anteil an der Summe aller Entität-/Gruppen-Zeilen
    # derselben Spalte (bis zur vorherigen Trennlinie).
    percent_of_total: bool = False
    # Nur für row_type "entity"/"group" relevant — blendet die Zeile aus,
    # wenn sie in ALLEN sichtbaren Spalten entweder keinen Wert oder 0 hat
    # (z. B. ein stillgelegtes Gerät), ohne dass man sie manuell über
    # "hidden" ein-/ausschalten muss.
    hide_if_empty: bool = False


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
    sticky_first_col: bool = False
    sticky_header: bool = False
    comparison_columns: bool = False
    show_deviation: bool = False
    explicit_missing: bool = False
    show_units: bool = True
    align_units: bool = False
    small_units: bool = False
    align_numbers: bool = False
    # Dieselbe Bedeutung wie _TableColumnBody.width, nur für die
    # Beschriftungsspalte (die kein eigenes Spalten-Objekt hat) — None heißt
    # automatische Breite (so breit wie der längste Zeilentext).
    label_col_width: int | None = None
    # Ausrichtung der Kopfzeile (Zeiträume) bzw. der Werte-Zellen — Standard
    # bei beiden rechtsbündig (siehe table-compute.js styleClasses()).
    header_align: str = "right"
    value_align: str = "right"
    # Alle Werte-Spalten gleich breit (width:1%-Trick im CSS) — betrifft
    # bewusst nur die Werte-Spalten, nicht die Beschriftungsspalte (siehe
    # table_editor.html tbl-style-equal-cols).
    equal_value_cols: bool = False


_TABLE_BORDER_OPTIONS = ("horizontal", "grid", "none")
_TABLE_DENSITY_OPTIONS = ("comfortable", "compact")
_TABLE_ALIGN_OPTIONS = ("left", "center", "right")


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
        {
            "entity_id": row["entity_id"],
            "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
            "ha_name": row["friendly_name"] or row["entity_id"],
            "is_custom": bool(row["custom_name"]),
        }
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
        "dashboard_usage": index.list_item_dashboards("table", table["id"]) if table else [],
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
        if c.decimals not in DECIMALS_LABELS:
            raise HTTPException(status_code=400, detail="Ungültige Nachkommastellen-Option in einer Spalte")
        if c.width is not None and not (30 <= c.width <= 800):
            raise HTTPException(status_code=400, detail="Ungültige Spaltenbreite")
    if body.style.label_col_width is not None and not (30 <= body.style.label_col_width <= 800):
        raise HTTPException(status_code=400, detail="Ungültige Spaltenbreite")
    for r in body.rows:
        if r.row_type not in ("entity", "group", "formula", "separator", "summary"):
            raise HTTPException(status_code=400, detail="Ungültiger Zeilentyp")
        if len(r.label) > MAX_TABLE_ROW_LABEL_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Eine Zeilenbeschriftung darf höchstens {MAX_TABLE_ROW_LABEL_LENGTH} Zeichen lang sein",
            )
        if r.row_type in ("entity", "group") and not r.entity_ids:
            raise HTTPException(status_code=400, detail=f'Zeile "{r.label}" braucht mindestens eine Entität')
        if r.row_type == "formula" and not r.formula.strip():
            raise HTTPException(status_code=400, detail=f'Zeile "{r.label}" braucht eine Formel')
        # summary: dieselben zwei Werte wie die Aggregation von Entität-/
        # Gruppen-Zeilen (Summe/Durchschnitt), nur ohne "auto"/"min"/"max" —
        # eine automatische Summenzeile ist immer eindeutig Summe oder
        # Durchschnitt, nie kontextabhängig wie bei einer einzelnen Entität.
        if r.row_type == "summary" and r.aggregation not in ("sum", "avg"):
            raise HTTPException(status_code=400, detail=f'Zeile "{r.label}" braucht Summe oder Durchschnitt')
        if r.aggregation not in _TABLE_ROW_AGGREGATIONS:
            raise HTTPException(status_code=400, detail=f'Zeile "{r.label}" hat eine ungültige Aggregation')
    if body.style.borders not in _TABLE_BORDER_OPTIONS:
        raise HTTPException(status_code=400, detail="Ungültige Rahmen-Option")
    if body.style.density not in _TABLE_DENSITY_OPTIONS:
        raise HTTPException(status_code=400, detail="Ungültige Dichte-Option")
    if body.style.header_align not in _TABLE_ALIGN_OPTIONS or body.style.value_align not in _TABLE_ALIGN_OPTIONS:
        raise HTTPException(status_code=400, detail="Ungültige Ausrichtungs-Option")


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


@app.post("/tables/{table_id}/duplicate")
def tables_duplicate(table_id: int) -> dict:
    """Kopie bleibt bewusst unfavorisiert (create_saved_table() setzt keinen
    is_favorite-Wert) — sonst gäbe es nach dem Duplizieren eines Favoriten
    zwei inhaltsgleiche favorisierte Karten."""
    table = index.get_saved_table(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    new_id = index.create_saved_table(
        index.copy_name_for("saved_tables", table["name"]),
        table["columns"], table["rows"], table["style"],
    )
    return {"id": new_id}


@app.post("/tables/{table_id}/pin", response_class=HTMLResponse)
def tables_pin(request: Request, table_id: int, dashboard_id: int = 1, base: str = ".") -> HTMLResponse:
    if index.get_saved_table(table_id) is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.pin_item_to_dashboard(dashboard_id, "table", table_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id, base))


@app.post("/tables/{table_id}/unpin", response_class=HTMLResponse)
def tables_unpin(request: Request, table_id: int, dashboard_id: int = 1, base: str = ".") -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    index.unpin_item_from_dashboard(dashboard_id, "table", table_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id, base))


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
    chart_options = _resolve_entity_chart_options(entity)
    # "Nachkommastellen" im Optionen-Menü übersteuert, sofern nicht "Auto", die
    # globale Anzeige-Einstellung der Entität (entities.decimals, Konfiguration-
    # Seite) nur für diese Chart-Ansicht — dieselbe Override-Konvention wie die
    # Werte-Kachel-Übersteuerung auf Dashboards (dashboard_pins.decimals).
    effective_decimals = chart_options["decimals"] if chart_options["decimals"] != "auto" else entity["decimals"]
    return templates.TemplateResponse(
        request,
        "entity_detail.html",
        {
            "entity_id": entity_id,
            "friendly_name": entity["friendly_name"],
            "custom_name": entity["custom_name"] or "",
            "aggregation_type": entity["aggregation_type"],
            "type_label": format_type(entity["aggregation_type"]),
            "unit": entity["unit"],
            "decimals": decimals_to_int(effective_decimals),
            "display_mode": entity["display_mode"],
            "base": "..",
            "first_date": first_date,
            "last_date": last_date,
            "is_favorite": bool(entity["is_favorite"]),
            "chart_options": chart_options,
            "entity_chart_defaults": _get_entity_chart_defaults(),
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
            "custom_name": entity["custom_name"] or "",
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
    (Bereinigung u. a.), deren Gesamtmenge bereits vollständig im Speicher
    vorliegt. Die Obergrenze 1000 verhindert eine unbegrenzte Materialisierung
    durch alte ``page_size=0``-URLs oder manuell veränderte Requests. page
    wird auf den gültigen Bereich begrenzt, damit ein veralteter Seiten-Wert
    nach einem Filterwechsel nie eine leere Seite zeigt."""
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


def _paginate_meta(total: int, page: int, page_size: int) -> dict:
    """Berechnet dieselbe Seiteninfo wie `_paginate()`, aber für eine bereits
    bekannte Trefferzahl statt einer im Speicher vorliegenden Liste — für
    Aufrufer, die per SQL `LIMIT`/`OFFSET` paginieren, statt die komplette
    Ergebnismenge zu laden (ZP-004 in PERFORMANCE.md)."""
    _, pagination = _paginate([None] * total, page, page_size)
    return pagination


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


_CLEANUP_ALLTIME_STATS_MAX_AGE_SECONDS = 15 * 60


def _cleanup_alltime_counts(entity, now: datetime) -> dict:
    """Ausreißer/Lücken/Duplikate/Wiederholungen/Zählerrückgänge über die
    KOMPLETTE Historie der Entität, nicht nur den gerade gewählten Zeitraum
    (Bereinigungsseite, Kachel-Zeile "Gesamter Zeitraum") — gecacht für
    15 Minuten (index.is_cleanup_alltime_stats_stale), weil ein Vollscan bei
    Entitäten mit Millionen Rohwerten sonst bei jedem Seitenaufruf bzw. jedem
    Filterklick teuer wäre. Nutzt denselben speicherbegrenzten Streaming-Pfad
    wie der Zeitraum "Gesamt" im Chip (analyze_raw_rows_page, zwei Durchläufe
    ohne Materialisierung aller Zeilen), page_size=1 weil hier nur die
    counts gebraucht werden, keine Zeilenliste."""
    entity_id = entity["entity_id"]
    if not index.is_cleanup_alltime_stats_stale(entity_id, _CLEANUP_ALLTIME_STATS_MAX_AGE_SECONDS):
        cached = index.get_cleanup_alltime_stats(entity_id)
        if cached is not None:
            return cached["counts"]

    window_start, window_end = _rows_window("all", 0, now, entity["first_ts"])
    gap_threshold = entity["gap_threshold"]
    outlier_threshold = entity["outlier_threshold"]

    def rows_factory():
        return cleanup.iter_raw_rows(
            DATA_DIR, index, entity_id,
            window_start.timestamp(), window_end.timestamp(), TZ, now=now,
        )

    analysis = cleanup.analyze_raw_rows_page(
        rows_factory,
        filter_="all",
        page=1,
        page_size=1,
        gap_threshold_minutes=None if gap_threshold == "off" else float(gap_threshold),
        outlier_threshold_percent=None if outlier_threshold == "off" else float(outlier_threshold),
        tz=TZ,
        decimals=entity["decimals"],
        counter_decrease_enabled=entity["state_class"] == "total_increasing",
    )
    counts = analysis["counts"]
    index.set_cleanup_alltime_stats(entity_id, counts)
    return counts


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
            tz=TZ,
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
                    "%d.%m.%Y %H:%M:%S"
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
            rows, None if outlier_threshold == "off" else float(outlier_threshold),
            entity["decimals"], TZ,
        )
        gaps = cleanup.detect_gaps(
            rows, None if gap_threshold == "off" else float(gap_threshold),
            entity["decimals"], TZ,
        )
        duplicates = cleanup.detect_duplicates(rows, entity["decimals"])
        repetitions = cleanup.detect_repetitions(rows, entity["decimals"], TZ)
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
                    "%d.%m.%Y %H:%M:%S"
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

    if range_key == "all":
        # Zeitraum "Gesamt" deckt exakt dasselbe Fenster ab wie die Gesamt-
        # Zeitraum-Kachel — ein zweiter Vollscan wäre hier reine Verschwendung,
        # der Cache bleibt aber trotzdem aufgefrischt (nächster Aufruf mit
        # engerem Zeitraum-Chip muss dann nicht sofort neu scannen).
        alltime_counts = counts
        index.set_cleanup_alltime_stats(entity_id, counts)
    else:
        alltime_counts = _cleanup_alltime_counts(entity, now)

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
            "unit": entity["unit"] or "",
            "period_label": period_label,
            "window_start_ts": window_start.timestamp(),
            "window_end_ts": window_end.timestamp(),
            "is_current": offset == 0,
            "counts": counts,
            "alltime_counts": alltime_counts,
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

    result = await run_in_threadpool(delete_locked)
    if timestamps:
        _invalidate_purge_preview()
    return result


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
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M:%S"),
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

    def load_to_delete() -> list[tuple[float, float]]:
        with storage_coordinator.entity(entity_id):
            # iter_raw_rows() statt list_raw_rows(max_rows=...): Duplikate müssen
            # über den ganzen gewählten Zeitraum gesucht werden, auch wenn der weit
            # über MAX_UI_ANALYSIS_ROWS liegt (z. B. "Gesamt" bei Millionen Rohwerten)
            # — das Cap gilt nur für Pfade, die wirklich alle Zeilen materialisieren
            # müssten. Hier hält iter_duplicate_rows_to_delete() den Speicherbedarf
            # konstant, da Duplikate dank Sortierung immer direkt aufeinanderfolgen.
            rows = cleanup.iter_raw_rows(
                DATA_DIR, index, entity_id, window_start.timestamp(), window_end.timestamp(), TZ,
                now=now
            )
            return cleanup.duplicate_rows_to_delete(rows)

    # duplicate_rows_to_delete() braucht rows weiterhin chronologisch aufsteigend,
    # um korrekt das JEWEILS ÄLTESTE Vorkommen je Zeitstempel zu behalten — erst
    # für die Anzeige unten drehen wir auf neueste-zuerst um (Konzept: Listen mit
    # Werten generell neueste oben), all_timestamps bleibt davon unberührt (die
    # Reihenfolge der versteckten Formularfelder ist für den Löschvorgang egal).
    to_delete = await run_in_threadpool(load_to_delete)
    preview_rows = [
        {
            "ts": ts,
            "formatted_value": format_value(value, decimals_int),
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M:%S"),
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
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M:%S"),
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

    marked_any = False

    def delete_locked() -> HTMLResponse:
        nonlocal marked_any
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
                    marked_any = True
                    batch = []
            if batch:
                index.mark_deleted(entity_id, batch, deleted_at=deleted_at)
                marked_any = True
            return _rows_fragment(
                request, entity_id, filter_, range_key, offset, page, page_size, mode
            )

    result = await run_in_threadpool(delete_locked)
    if marked_any:
        _invalidate_purge_preview()
    return result


# Symcon-/CSV-/Home-Assistant-Import (Upload, Scan, Dry-Run/Start, /import
# selbst) — ausgelagert nach import_routes.py, gleiches Muster wie oben bei
# api_routes.py/report_routes.py (Konzept "main.py-Zeilenbudget", siehe
# test_route_modules.py). _import_service bleibt hier als Modulattribut
# erreichbar, falls andere Stellen (z. B. Tests) direkt darauf zugreifen wollen.
_import_service = ImportService(ImportDependencies(
    data_dir=DATA_DIR,
    tz=TZ,
    index=index,
    coordinator=storage_coordinator,
    templates=templates,
    app_root_context=_app_root_context,
    reports_context=_reports_context,
    run_storage_reconciliation=_run_storage_reconciliation,
    symcon_import_dir=SYMCON_IMPORT_DIR,
    csv_import_dir=CSV_IMPORT_DIR,
    symcon_names_path=SYMCON_NAMES_PATH,
    symcon_source_meta_path=SYMCON_SOURCE_META_PATH,
    symcon_scan_cache_path=SYMCON_SCAN_CACHE_PATH,
))
app.include_router(_import_service.router())
