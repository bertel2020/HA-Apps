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
import hashlib
import json
import logging
import os
import platform
import secrets
import shutil
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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
from jinja2 import pass_context
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import Receive, Scope, Send

from .formatting import (
    BACKUP_KEEP_COUNT_LABELS,
    BACKUP_SCHEDULE_LABELS,
    DECIMALS_LABELS,
    DISPLAY_MODE_LABELS,
    FONT_SCALE_LABELS,
    GAP_THRESHOLD_LABELS,
    OUTLIER_BLOCKED_REASONS,
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
from . import demo_mode
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
from .background import BackgroundDependencies, BackgroundService
from .backup_scheduler import parse_schedule_time
from .storage import (
    backup,
    cleanup,
    entity_removal,
    hotbuffer,
    import_reports,
)
from .storage import query as query_mod
from .storage.index import (
    DEFAULT_DECIMALS,
    DEFAULT_GAP_THRESHOLD,
    DEFAULT_OUTLIER_THRESHOLD,
    DEFAULT_RESOLUTION,
    DEFAULT_RETENTION,
    DEFAULT_VALUE_FILTER,
    effective_outlier_threshold,
    outlier_detection_applies,
    should_raise_gap_threshold,
    MAX_CUSTOM_NAME_LENGTH,
    MAX_SAVED_NAME_LENGTH,
    DuplicateNameError,
    Index,
    IndexBusy,
    InvalidNameError,
)
from .storage.ingestion import IngestionService
from .storage.coordinator import CoordinatorBusy, StorageCoordinator
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
from .housekeeping_routes import (
    HousekeepingDependencies,
    create_housekeeping_router,
    demo_data_context,
)
from .report_routes import ReportDependencies, ReportService
from .energiedashboard_routes import (
    SETTING_HOURLY_BACKFILL_PENDING,
    EnergieDashboardDependencies,
    EnergieDashboardService,
    energiedashboard_role_count,
    entity_has_energiedashboard_role,
    is_energiedashboard_configured,
)
from . import cleanup_stats
from .route_support import UploadLimitExceeded, copy_upload_limited, dir_size, storage_locked
from . import notices as notices_mod
from . import version_check
from .notices import collect_notices
from .progress import activity_snapshot, register_source

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
BASE_DIR = Path(os.environ.get("ZEITARCHIV_DATA_DIR", "/data"))
_OPTIONS = demo_mode.load_options(BASE_DIR)  # Auflösung siehe demo_mode.py (DEMO_MODUS_PLAN.md)
DEMO_MODE = demo_mode.resolve_demo_mode(_OPTIONS)
DATA_DIR = demo_mode.demo_dir(BASE_DIR) if DEMO_MODE else BASE_DIR
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
# Die Schlüssel bleiben über Umbenennungen hinweg stabil, damit gespeicherte
# Einstellungen ohne Migration gültig bleiben. Die Randstufen "Kleiner" (0,9)
# und "Größer" (1,4) sind wieder entfallen — 0,9 unterschied sich zu wenig von
# "Klein", 1,4 verschärfte den Seitenüberlauf auf schmalen Viewports;
# LEGACY_FONT_SCALE bildet ihre gespeicherte Auswahl auf die nächstgelegene
# verbliebene Stufe ab statt pauschal auf "Normal".
FONT_SCALE = {"1": "1", "2": "1.125", "3": "1.25"}
DASHBOARD_ROW_HEIGHT = {"1": 210, "2": 218, "3": 228}
LEGACY_FONT_SCALE = {"0": "1", "4": "3"}
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
# Steuert, wohin "/" (Ingress-Root — was beim Öffnen von Zeitarchiv über die
# HA-Sidebar erscheint) weiterleitet. Die Übersicht selbst lebt dafür unter
# der eigenen URL "/uebersicht" statt weiter unter "/" — die Topnav
# verlinkt "Übersicht" fest auf "/uebersicht", damit dieser Link IMMER zur
# Übersicht führt, unabhängig von dieser Einstellung (sonst würde er bei
# startseite="energiedashboard" auf sich selbst zurückverweisen und die
# Übersicht wäre über die Topnav gar nicht mehr erreichbar).
STARTSEITE_LABELS = {"uebersicht": "Übersicht", "energiedashboard": "Energiedashboard"}


def _current_font_scale() -> str:
    """Gespeicherte Stufe als garantiert gültiger FONT_SCALE-Schlüssel — einzige
    Stelle, die den Rohwert aus der Settings-Tabelle auslegt. Kontextprozessor,
    Einstellungsformular und Diagnose lesen darüber, damit eine entfallene Stufe
    überall dieselbe Ersatzstufe ergibt statt im Formular als leeres Label
    durchzuschlagen."""
    font_scale = index.get_setting("font_scale", "1")
    font_scale = LEGACY_FONT_SCALE.get(font_scale, font_scale)
    if font_scale not in FONT_SCALE:
        font_scale = "2"
    return font_scale


def _font_scale_context(request: Request) -> dict:
    """Starlette context_processor: läuft für JEDE TemplateResponse automatisch
    mit, ohne dass jede einzelne Route den Skalierungsfaktor selbst in ihren
    Kontext aufnehmen müsste."""
    font_scale = _current_font_scale()
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
        "font_scale_value": FONT_SCALE[font_scale],
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


def _collect_all_notices() -> list[dict]:
    """Ein Aufhänger für drei gleichlautende collect_notices()-Aufrufstellen
    statt derselben elf Argumente je einmal (_background existiert erst
    später im Modul — unproblematisch, gebunden wird erst beim Aufruf)."""
    return collect_notices(
        index, DATA_DIR / "index.sqlite", TZ, _background.load_purge_preview()["totals"],
        _background.storage_reconcile_last, _background.stale_entity_count_cached, _background.last_scheduler_tick,
        _background.last_reconcile_tick, _background.reconcile_in_progress(), _background.host_disk_usage_cached,
        _background.last_backup_worker_tick, _background.backup_progress.running, _background.demo_dir_info_cached,
    )


def _notices_context(request: Request) -> dict:
    """Für das Hinweis-Center (Glocken-Icon) in _topnav.html — läuft wie
    _app_root_context automatisch für JEDE TemplateResponse mit, statt dass
    jede der ~10 Seiten, die _topnav.html einbinden, die Hinweisliste selbst
    in ihren Kontext aufnehmen müsste. collect_notices() fragt bewusst nur
    günstige Werte ab (PRAGMA-Stats, LIMIT-1-Queries), siehe notices.py."""
    return {
        "notices": _collect_all_notices(),
        "snooze_labels": notices_mod.SNOOZE_LABELS,
        # Rein im Arbeitsspeicher (siehe activity_snapshot()) — kein Datei-
        # oder Datenbankzugriff, deshalb tragbar in einem Kontextprozessor,
        # der bei JEDER TemplateResponse mitläuft. Serverseitig gerendert,
        # damit die Kopfleiste schon beim Seitenaufbau stimmt statt erst nach
        # dem ersten Poll; aktuell hält sie danach topnav-activity.js.
        "activity": activity_snapshot(),
    }


app = FastAPI(title="Zeitarchiv")
templates = Jinja2Templates(
    directory=str(APP_DIR / "templates"),
    context_processors=[_font_scale_context, _app_root_context, _nav_dashboards_context, _notices_context],
)


class RequestLoggingMiddleware:
    """Reine ASGI-Middleware statt BaseHTTPMiddleware (siehe ROADMAP.md,
    "Neu seit 0.76.1", Punkt 2) — BaseHTTPMiddleware führt die eigentliche
    Route in einem zweiten, über einen Memory-Stream verbundenen anyio-Task
    aus; bricht der Client mitten im Antwort-Versand ab, erzeugte genau das
    CancelledError/WouldBlock-Tracebacks ("Exception in ASGI application").
    Reines ASGI kennt diesen zweiten Task nicht, das Problem entfällt
    strukturell. request.state.request_id bleibt erhalten (dieselbe scope,
    api_routes.py liest es zur Ingest-Log-Korrelation) — muss VOR dem
    Aufruf der inneren App gesetzt werden, damit die Route es sieht."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_id = secrets.token_hex(6)
        scope.setdefault("state", {})["request_id"] = request_id
        status_code = 500

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log_http_request(
                scope["method"], scope["path"], 500,
                (time.perf_counter() - started) * 1000, request_id=request_id,
            )
            raise
        log_http_request(
            scope["method"], scope["path"], status_code,
            (time.perf_counter() - started) * 1000, request_id=request_id,
        )


class SecurityHeadersMiddleware:
    """Reine ASGI-Middleware statt BaseHTTPMiddleware — siehe
    RequestLoggingMiddleware oben für die Begründung."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; "
                    "img-src 'self' data:; connect-src 'self'; "
                    # Seit ZG-14 liegen IBM Plex Sans/Mono als WOFF2 unter
                    # static/fonts/ und werden in app.css per @font-face gebunden.
                    # Vorher musste hier fonts.gstatic.com (die Schriftdateien) und
                    # fonts.googleapis.com (deren Stylesheet) offen stehen.
                    "font-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; "
                    # Alpine.js' Standard-Build kompiliert x-* Ausdrücke über
                    # AsyncFunction und benötigt deshalb unsafe-eval. unsafe-inline wird
                    # für die bestehenden seitenlokalen Skripte/Handler benötigt. Externe
                    # Skriptquellen bleiben trotzdem vollständig auf 'self' begrenzt.
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
                )
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "SAMEORIGIN"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _CachedStaticFiles(StaticFiles):
    """StaticFiles liefert nur Last-Modified/ETag, kein Cache-Control — sicher
    lang cachebar, weil jede static/-Referenz einen Query-Parameter mit dem
    Inhalts-Hash der Datei trägt (asset() unten), der sich mit ihr mitändert.

    Eine Ausnahme trägt ihre Sicherheit anders: Die Schriften unter
    static/fonts/ werden aus app.css per url() referenziert und können dort
    keinen Parameter mitführen. Für sie gilt stattdessen die Regel, dass ein
    Update einen neuen Dateinamen bekommt (ZG-14)."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)  # bislang kein Content-Encoding irgendwo
app.mount("/static", _CachedStaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    # Browser fragen dieses Icon unabhängig vom aktuellen app_root-Präfix (Ingress)
    # immer unter dem Wurzelpfad an — ohne diese Route landet das als 404 im
    # Access-Log, obwohl das Addon-Icon längst existiert (addon/icon.png).
    return FileResponse(APP_DIR.parent / "icon.png", media_type="image/png")
class _AssetVersions:
    """Cache-Buster je Datei, aus ihrem Inhalt gebildet (ZG-05).

    Bis 0.84.0 waren css_v/js_v/vendor_v drei Zahlen: die jüngste mtime über
    alle Dateien eines Ordners. Das hatte zwei Fehler, und beide kosteten den
    Nutzer Bandbreite.

    Erstens speichert Git keine mtimes. Ein frischer CI-Checkout setzt alle
    Dateien auf die Checkout-Zeit, und COPY im Dockerfile übernimmt sie —
    vendor_v änderte sich damit bei JEDEM Release, auch wenn ECharts seit
    Monaten unverändert war. Jedes Add-on-Update ließ jeden Nutzer 1,0 MB
    ECharts neu laden, für nichts.

    Zweitens war es EINE Zahl über ALLE Dateien eines Ordners. Eine Änderung
    an einer der 30 JS-Dateien entwertete den Zwischenspeicher aller dreißig.

    Ein Inhalts-Hash behebt beides: Er ändert sich genau dann, wenn sich der
    Inhalt ändert, und nur für die betroffene Datei. Bei gleichem Inhalt
    liefert er nach jedem Rebuild denselben Wert.

    Gehasht wird nur, wenn (mtime, Größe) sich seit dem letzten Mal geändert
    haben — im Betrieb also einmal je Datei, danach kostet ein Aufruf einen
    stat() und einen Dict-Zugriff. Damit braucht es kein Zeitfenster mehr wie
    beim Vorgänger, und eine Änderung beim Entwickeln wirkt sofort statt erst
    nach einer Sekunde.

    Der Hash ist blake2b auf 8 Hexzeichen gekürzt. Das ist keine
    Sicherheitsfunktion, sondern eine Kennung: 32 Bit reichen, um zwei
    Fassungen derselben Datei zu unterscheiden, und ein kurzer Wert hält die
    URL lesbar.
    """

    def __init__(self, wurzel: Path) -> None:
        self._wurzel = wurzel
        self._cache: dict[str, tuple[tuple[int, int], str]] = {}

    def __call__(self, pfad: str) -> str:
        datei = self._wurzel / pfad
        bekannt = self._cache.get(pfad)
        try:
            zustand = datei.stat()
            kennung = (zustand.st_mtime_ns, zustand.st_size)
            if bekannt is not None and bekannt[0] == kennung:
                return bekannt[1]
            version = hashlib.blake2b(datei.read_bytes(), digest_size=4).hexdigest()
        except OSError:
            # Datei fehlt oder ist nicht lesbar: den zuletzt bekannten Wert
            # behalten, statt den Seitenaufbau an einem Cache-Buster scheitern
            # zu lassen. Dass ein Pfad überhaupt existiert, prüft
            # tests/test_asset_versions.py über alle Templates hinweg — zur
            # Laufzeit ist das der falsche Ort dafür.
            return bekannt[1] if bekannt else "0"
        self._cache[pfad] = (kennung, version)
        return version


asset_versions = _AssetVersions(APP_DIR / "static")


@pass_context
def asset(kontext, pfad: str) -> str:
    """Vollständige URL eines Assets unter static/, mit Cache-Buster.

    Ersetzt die Schreibweise "{{ app_root }}/static/<pfad>?v={{ css_v }}", die
    an 172 Stellen stand. Zwei Fehler waren dort jederzeit möglich und fielen
    beide erst beim Nutzer auf: den Präfix vergessen (ZG-03) oder den
    Cache-Buster vergessen (ZG-05, dann greift das immutable-Cache-Control von
    _CachedStaticFiles auf eine Datei, die sich noch ändert). Beides kann eine
    neue Zeile jetzt nicht mehr, weil sie nur noch den Pfad nennt.

    app_root kommt aus _app_root_context() und steht in jeder
    TemplateResponse. Der Rückfall auf "" ist für Tests, die ein Template
    direkt rendern.
    """
    return f"{kontext.get('app_root', '')}/static/{pfad}?v={asset_versions(pfad)}"


# Cache-Busting fürs geteilte Stylesheet (siehe app/static/css/README.md),
# macht das lange "immutable" Cache-Control von _CachedStaticFiles sicher.
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
# Eine Funktion statt der drei Zahlen css_v/js_v/vendor_v (ZG-05). Ein
# Template schreibt {{ asset('js/pages/statistik.js') }} und bekommt Präfix und
# Cache-Buster mitgeliefert; welcher der drei Ordner gemeint ist, muss es nicht
# mehr wissen. Die Begründung für den Inhalts-Hash steht bei _AssetVersions.
templates.env.globals["asset"] = asset
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

# Die gesamte Hintergrundarbeit — Wartungsplaner, Backup, Aufbewahrung,
# Speicherabgleich und die beiden teuren Vorschau-Zwischenspeicher — lebt seit
# 0.85.0 in background.py. Hier steht nur noch das Einhängen. Der Dienst wird
# bewusst SO FRÜH gebaut: Der Speicherabgleich läuft weiter unten noch vor dem
# ersten Request, also lange bevor es ein Energiedashboard oder Router gibt.
# count_stale_entities als Lambda, weil _count_stale_entities() weiter unten in
# dieser Datei steht — aufgelöst wird es erst beim Aufruf.
_background = BackgroundService(BackgroundDependencies(
    data_dir=DATA_DIR,
    tz=TZ,
    index=index,
    coordinator=storage_coordinator,
    base_dir=BASE_DIR, demo_mode_active=DEMO_MODE,
    backups_dir=BACKUPS_DIR,
    symcon_import_dir=SYMCON_IMPORT_DIR,
    csv_import_dir=CSV_IMPORT_DIR,
    backup_default_time=BACKUP_DEFAULT_TIME,
    backup_default_weekday=BACKUP_DEFAULT_WEEKDAY,
    retention_default_time=RETENTION_DEFAULT_TIME,
    retention_default_weekday=RETENTION_DEFAULT_WEEKDAY,
    count_stale_entities=lambda: _count_stale_entities(),
))






# Beim ersten Start (und defensiv auch nach einem manuell geleerten DB-Wert)
# muss vor dem Öffnen eines HTTP-Listeners ein nicht-leerer Token existieren.
# ZEITARCHIV_API_TOKEN ist ausschließlich der explizite Override für den
# lokalen Compose-/Virtualenv-Test; im Supervisor wird immer kryptografisch
# sicher generiert und anschließend in SQLite persistiert.
ensure_api_token(index, development_token=os.environ.get("ZEITARCHIV_API_TOKEN"))
ingestion_service = IngestionService(DATA_DIR, index, TZ, storage_coordinator)
_recovered_ingest_events = ingestion_service.recover_pending()
_requires_synchronous_reconciliation = bool(_restore_startup_result) or not _previous_shutdown_clean
_background.requires_synchronous_reconciliation = _requires_synchronous_reconciliation
if _requires_synchronous_reconciliation:
    with storage_coordinator.exclusive():
        _background.run_storage_reconciliation(repair=True)
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
            data_dir=DATA_DIR, index=index, tz=TZ,
            coordinator=storage_coordinator, ingestion=ingestion_service,
            api_token=_current_api_token,
            app_version=APP_VERSION,
            collect_notices=_collect_all_notices,
            latest_backup=lambda: notices_mod.latest_backup_info(index),
            demo_mode_active=DEMO_MODE,
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
# Nachgereicht statt in BackgroundDependencies: Der Wartungsplaner braucht den
# Energiedashboard-Dienst (Stunden-Rollup-Backfill, Heatmap-Cache), der
# Hintergrunddienst selbst muss aber schon vor ihm existieren — siehe oben.
_background.energiedashboard_service = _energiedashboard_service
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


@app.exception_handler(CoordinatorBusy)
async def _coordinator_busy_handler(_request: Request, exc: CoordinatorBusy) -> JSONResponse:
    """Wie _index_busy_handler, aber für den Storage-Coordinator statt des
    Index-Locks (siehe CoordinatorBusy/storage_locked() in route_support.py)."""
    return JSONResponse(
        status_code=503,
        content={"detail": "Speicherzugriff kurzzeitig ausgelastet — bitte in ein paar Sekunden erneut versuchen."},
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



























@app.get("/")
def app_root(request: Request) -> RedirectResponse:
    """Ingress-Root — was beim Öffnen von Zeitarchiv über die HA-Sidebar
    erscheint. Reine Weiche auf Basis der Einstellung "startseite"
    (Einstellungen → Darstellung), die eigentlichen Seiten leben unter
    ihrer jeweils eigenen URL (siehe STARTSEITE_LABELS-Kommentar oben)."""
    startseite = index.get_setting("startseite", "uebersicht")
    if startseite not in STARTSEITE_LABELS:
        startseite = "uebersicht"
    app_root = _app_root_context(request)["app_root"]
    target = "energiedashboard" if startseite == "energiedashboard" else "uebersicht"
    return RedirectResponse(url=f"{app_root}/{target}", status_code=307)


@app.get("/uebersicht", response_class=HTMLResponse)
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
    startseite = index.get_setting("startseite", "uebersicht")
    if startseite not in STARTSEITE_LABELS:
        startseite = "uebersicht"
    entity_defaults = _get_entity_chart_defaults()
    return {
        "font_scale": _current_font_scale(),
        "font_scale_options": list(FONT_SCALE_LABELS.items()),
        "font_scale_values": FONT_SCALE,
        "color_scheme": color_scheme,
        "color_scheme_options": list(COLOR_SCHEME_LABELS.items()),
        "color_mode": color_mode,
        "color_mode_options": list(COLOR_MODE_LABELS.items()),
        "dashboard_animation": dashboard_animation,
        "dashboard_animation_options": list(DASHBOARD_ANIMATION_LABELS.items()),
        "startseite": startseite,
        "startseite_options": list(STARTSEITE_LABELS.items()),
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
    Server-Logs sichtbar (siehe _maintenance_scheduler_loop() in
    background.py). Rein lesend,
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
    reconcile = _background.storage_reconcile_last or {}
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
            _background.load_retention_overview().get("generated_at"),
        ),
        row(
            "Löschvorschau", "Vorschau für weich gelöschte, noch nicht entfernte Werte · bei Bedarf",
            _background.load_purge_preview().get("generated_at"),
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

    # Kein row(): Der Stand ist eine Warteschlange, kein Zeitpunkt — dieselbe
    # Form wie beim Stunden-Rollup-Backfill unten. Ein "letzter Lauf" wäre hier
    # nichtssagend, weil in jedem Takt einer stattfindet, solange etwas offen
    # ist; die Frage ist, wie viel noch aussteht.
    outlier = cleanup_stats.outlier_rate_overview(index)
    outlier_gesamt = outlier["measured"] + outlier["pending"]
    rows.append({
        "name": "Ausreißer-Quoten",
        "hint": "Misst je Entität die Markierungsquote über die ganze Historie · 1 Entität/30s",
        "last_run": (
            f"{outlier['measured']} von {outlier_gesamt} gemessen" if outlier_gesamt
            else "keine Entität mit Ausreißer-Erkennung"
        ),
        "pill_class": "pending" if outlier["pending"] else "ok",
        "pill_label": f"{outlier['pending']} ausstehend" if outlier["pending"] else "OK",
    })

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
            **demo_data_context(index, BASE_DIR, DEMO_MODE),
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
                index, DATA_DIR / "index.sqlite", TZ, _background.load_purge_preview()["totals"],
                _background.storage_reconcile_last, _background.stale_entity_count_cached, _background.last_scheduler_tick,
                _background.last_reconcile_tick, _background.reconcile_in_progress(), _background.host_disk_usage_cached,
                demo_dir_info=_background.demo_dir_info_cached,
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
    remaining = _collect_all_notices()
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


@app.get("/notices/activity", response_class=HTMLResponse)
def notices_activity(request: Request) -> HTMLResponse:
    """Frischer Inhalt für #notice-activity (Glocken-Menü), Gegenstück zu
    notices_panel() — warum nur die LAUFENDEN Vorgänge und nichts über
    fertige, steht bei activity_snapshot() in progress.py."""
    return templates.TemplateResponse(request, "_activity_block.html", _notices_context(request))


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
    startseite = form.get("startseite")
    if startseite is not None and startseite not in STARTSEITE_LABELS:
        raise HTTPException(status_code=400, detail="Ungültige Startseite")
    if startseite is not None:
        index.set_setting("startseite", str(startseite))

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








# Anmeldung der beiden älteren Zustände bei der Kopfleisten-Registratur. Die
# Adapter selbst stehen in notices.py, wo auch der Rest der Glocken-Anzeige
# lebt — main.py hat ein Zeilenbudget (test_route_modules.py), und
# Anzeigelogik ist genau das, was hier nicht mehr dazukommen soll.
register_source("retention", "Aufbewahrung", lambda: notices_mod.retention_activity(_background.retention_progress))
register_source("backup", "Backup", lambda: notices_mod.backup_activity(_background.backup_progress))




















@asynccontextmanager
async def _lifespan(_: FastAPI):
    _background.start()
    yield
    _background.stop()


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
    with _background.backup_progress.lock:
        running = _background.backup_progress.running
        done = _background.backup_progress.done
        total = _background.backup_progress.total
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
        next_ts = _background.set_next_backup_run(datetime.now(TZ))

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
    _background.run_backup()
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
    _background.set_next_backup_run(datetime.now(TZ))
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

        def install_locked() -> Path:
            # Gehört zwingend in den Threadpool, obwohl die Arbeit selbst nur
            # ein replace() ist: exclusive() wartet unbegrenzt, bis keine
            # Entitätsoperation und keine andere Wartung mehr läuft. Im
            # Event-Loop ausgeführt hielte allein dieses Warten den gesamten
            # Server an — auch /api/health —, solange z. B. ein Import oder
            # ein Retention-Lauf den exklusiven Zugriff hält.
            with storage_coordinator.exclusive():
                return backup.install_validated_backup(staging, BACKUPS_DIR, datetime.now(TZ))

        destination = await run_in_threadpool(install_locked)
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
    """Speicherbedarf nach Kategorie — für "archive" die bereits inkrementell
    im Index gepflegte Summe (siehe Index.add_size_bytes(), aktualisiert bei
    Rotation/Import/Retention/Purge) statt eines vollständigen Dateisystem-
    Walks bei jedem Diagnose-Download (siehe ROADMAP.md, Performance ZP-011).
    "rollup" bleibt ein echter Walk: der Index führt Rollup-Dateigrößen nicht
    mit, nur Archiv-Parquet-Größen. Alle übrigen Kategorien (Hot Buffer,
    Import, Backups) sind ohnehin nicht im Index abgebildet."""
    index_path = DATA_DIR / "index.sqlite"
    return [
        {"key": "archive", "label": "Archiv", "bytes": index.get_overview()["total_size_bytes"]},
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
    audit = _background.storage_reconcile_last or {}
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
            "font_scale": _current_font_scale(),
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
            # Dieselbe Messung wie events_per_hour, nur auf einen Tag gerechnet:
            # der Zuwachs der letzten 24 Stunden als Anzahl statt als Rate. Erst
            # dadurch steht neben dem 7-Tage-Schnitt (events_per_day) eine
            # gleich benannte Zahl, die sich direkt mit ihm vergleichen lässt.
            "new_rows_24h": format_int(round(rate_per_hour * 86400)) if rate_per_hour is not None else None,
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
    page_size: int = 20,
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
    page_size: int = 20,
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
    page_size: int = 20,
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
    # Nur die tatsächlich sichtbaren Spalten formatieren statt immer alle elf
    # (siehe _entities_table.html, das jede optionale Spalte ohnehin schon per
    # visible_columns ein-/ausblendet) — bei page_size=1000 sonst bis zu 1000
    # unnötige format_*()-Aufrufe pro ausgeblendeter Spalte (PERFORMANCE.md,
    # ZP-014). entity_id/friendly_name/display_name/has_custom_name/
    # is_favorite werden immer gebraucht (Entität-Spalte, Favoriten-Stern).
    rows = []
    for row in page_matched:
        entry = {
            "entity_id": row["entity_id"],
            "friendly_name": row["friendly_name"],
            "display_name": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
            "has_custom_name": bool(row["custom_name"]),
            "is_favorite": bool(row["is_favorite"]),
        }
        if "type" in visible_columns:
            entry["aggregation_type"] = row["aggregation_type"]
            entry["type_label"] = format_type(row["aggregation_type"])
        if "first_ts" in visible_columns:
            entry["first_ts"] = format_timestamp(row["first_ts"], TZ)
            entry["first_ts_time"] = format_time(row["first_ts"], TZ)
        if "last_ts" in visible_columns:
            entry["last_ts"] = format_timestamp(row["last_ts"], TZ)
            entry["last_ts_time"] = format_time(row["last_ts"], TZ)
        if "resolution" in visible_columns:
            entry["resolution_label"] = format_resolution(row["resolution"])
        if "retention" in visible_columns:
            entry["retention_label"] = format_retention(row["retention"])
        if "unit" in visible_columns:
            entry["unit"] = row["unit"]
        if "rows" in visible_columns:
            entry["row_count"] = _visible_row_count(row)
        if "size" in visible_columns:
            entry["size"] = format_size(row["size_bytes"])
        if "value_filter" in visible_columns:
            # Kurzform statt der vollen Dropdown-Beschriftung ("Gleiche
            # gerundete Werte filtern") — die Spaltenüberschrift "Wertfilter"
            # gibt den Kontext schon vor, in der Tabellenzelle reicht An/Aus.
            entry["value_filter_label"] = "An" if row["value_filter"] == "decimals" else "Aus"
        if "gap_threshold" in visible_columns:
            entry["gap_threshold_label"] = GAP_THRESHOLD_LABELS.get(row["gap_threshold"], row["gap_threshold"])
        if "outlier_threshold" in visible_columns:
            # "—" statt der gespeicherten Zahl, wo die Erkennung strukturell
            # nicht greift: eine Zahl, die nichts bewirkt, in einer Spalte
            # neben Zahlen, die etwas bewirken, ist irreführender als ein
            # sichtbares "gilt hier nicht".
            entry["outlier_threshold_label"] = (
                OUTLIER_THRESHOLD_LABELS.get(row["outlier_threshold"], row["outlier_threshold"])
                if outlier_detection_applies(row["aggregation_type"])
                else "—"
            )
        rows.append(entry)

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
    page_size: int = 20,
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


# Gültige Tarife für should_raise_gap_threshold() (index.py kennt bewusst keine Anzeige-Labels).
_GAP_THRESHOLD_MINUTE_TIERS = [int(k) for k in GAP_THRESHOLD_LABELS if k != "off"]


def _gap_threshold_auto_adjust_message(
    reason: str, new_gap: str, resolution: str, *, label: str = "Lücken-Erkennung"
) -> str:
    """Erklärtext für appAlert() nach einer Anhebung durch
    should_raise_gap_threshold() (label="Standard-Lücken-Erkennung" für die
    globalen Standards)."""
    new_label = GAP_THRESHOLD_LABELS.get(new_gap, new_gap)
    cause = (
        f"Der Wertänderungsfilter überspringt unveränderte Werte und schreibt selbst erst "
        f"nach spätestens {new_label} wieder"
        if reason == "value_filter" else
        f"Die gewählte Auflösung „{RESOLUTION_LABELS.get(resolution, resolution)}“ lässt "
        f"ohnehin nur alle {new_label} einen neuen Wert zu"
    )
    return (
        f"{label} automatisch auf {new_label} gesetzt: {cause} — eine kürzere Lücken-Schwelle "
        "hätte das sonst laufend fälschlich als Lücke gemeldet. Lässt sich hier jederzeit "
        "wieder manuell verkleinern."
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
    # deque statt list_raw_rows(max_rows=...): sonst ResultLimitExceeded bei dichten Entitäten (Issue #4)
    last_rows: deque[tuple[float, float]] = deque(maxlen=10)
    for ts, value in cleanup.iter_raw_rows(DATA_DIR, index, entity_id, window_start, now.timestamp(), TZ, now=now):
        last_rows.append((ts, value))
    preview_rows = [
        {
            "formatted_ts": datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M:%S"),
            "formatted_value": format_value(value, decimals_int),
        }
        for ts, value in reversed(last_rows)
    ]

    return {
        "entity_id": entity_id,
        "friendly_name": entity["friendly_name"],
        "custom_name": entity["custom_name"] or "",
        # Für den Favoritenstern im Seitenkopf: die drei Entitätsseiten tragen
        # seit der Reiterzeile denselben Kopf, und dort gehört er dazu.
        "is_favorite": bool(entity["is_favorite"]),
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
        "outlier_blocked_reason": OUTLIER_BLOCKED_REASONS.get(entity["aggregation_type"]),
        "outlier_rate": _outlier_rate_labels(cleanup_stats.outlier_rate(index, entity)),
        "display_mode": entity["display_mode"],
        "resolution_options": list(RESOLUTION_LABELS.items()),
        "retention_options": list(RETENTION_LABELS.items()),
        "decimals_options": list(DECIMALS_LABELS.items()),
        "value_filter_options": list(VALUE_FILTER_LABELS.items()),
        "gap_threshold_options": list(GAP_THRESHOLD_LABELS.items()),
        "outlier_threshold_options": list(OUTLIER_THRESHOLD_LABELS.items()),
        "display_mode_options": list(DISPLAY_MODE_LABELS.items()),
        "preview_rows": preview_rows,
    }


def _outlier_rate_labels(rate: dict | None) -> dict | None:
    """Die gemeinsamen Beschriftungen (cleanup_stats.rate_labels) plus den
    Zeitpunkt — den braucht nur das Konfigurationsfeld, wo der Knopf zum
    Nachrechnen daneben steht."""
    if rate is None:
        return None
    return dict(
        cleanup_stats.rate_labels(rate),
        # Datum UND Uhrzeit: der Cache lebt 15 Minuten, "am 07.09.2026"
        # allein sagt nicht, ob das vor fünf Minuten oder heute früh war.
        computed_label=(
            f'{format_timestamp(rate["computed_at"], TZ)} {format_time(rate["computed_at"], TZ)}'
        ),
    )


@app.post("/entities/{entity_id}/outlier-rate", response_class=HTMLResponse)
def entity_outlier_rate(request: Request, entity_id: str) -> HTMLResponse:
    """Rechnet die Markierungsquote der Ausreißer-Erkennung EINMAL nach, auf
    Klick. Bewusst kein Hintergrundlauf über alle Entitäten: ein Vollscan je
    Entität ist teuer (siehe alltime_counts()), und gebraucht wird die Zahl
    genau dann, wenn jemand die Schwelle gerade einstellt."""
    entity = _require_entity(entity_id)
    cleanup_stats.alltime_counts(DATA_DIR, index, TZ, entity, datetime.now(TZ), force=True)
    return templates.TemplateResponse(
        request, "_entity_config_form.html", _entity_config_context(_require_entity(entity_id))
    )


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
    entity = _require_entity(entity_id)
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
    # Für Zähler/Schalter ist das Feld deaktiviert (siehe
    # outlier_detection_applies()). Ein trotzdem mitgeschickter Wert wird
    # verworfen statt abgelehnt: die Einstellung wirkt für diese Typen ohnehin
    # nicht, ein HTTP 400 würde ein Problem behaupten, wo keines ist.
    if not outlier_detection_applies(entity["aggregation_type"]):
        outlier_threshold = None
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
                _background.invalidate_retention_overview()
            # Nur bei ÄNDERUNG von resolution/value_filter auslösen, sonst würde ein späteres,
            # bewusstes Verkleinern der Lücken-Erkennung bei nächster Gelegenheit zurückgedreht.
            gap_threshold_auto_adjusted = False
            gap_threshold_auto_adjusted_message = None
            if value_filter == "decimals" or resolution is not None:
                current = _require_entity(entity_id)
                should_raise, new_gap = should_raise_gap_threshold(
                    current["gap_threshold"], current["resolution"], current["value_filter"], _GAP_THRESHOLD_MINUTE_TIERS
                )
                if should_raise:
                    index.set_config(entity_id, gap_threshold=new_gap)
                    gap_threshold_auto_adjusted = True
                    reason = "value_filter" if current["value_filter"] == "decimals" else "resolution"
                    gap_threshold_auto_adjusted_message = _gap_threshold_auto_adjust_message(reason, new_gap, current["resolution"])
            entity = _require_entity(entity_id)
            context = _entity_config_context(entity)
            context["saved"] = True
            context["gap_threshold_auto_adjusted"] = gap_threshold_auto_adjusted
            context["gap_threshold_auto_adjusted_message"] = gap_threshold_auto_adjusted_message
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
    """Vollständiger Schnappschuss des Optionen-Menüs.

    Jedes Feld des Menüs MUSS hier stehen: Pydantic verwirft unbekannte Felder
    stillschweigend, und set_entity_chart_options() speichert genau das, was
    model_dump() liefert. Ein vergessenes Feld wird also gesendet, angenommen
    (HTTP 200) und nie gespeichert — beim nächsten Laden steht es wieder auf
    dem globalen Default. Genau so ist show_marked seit seiner Einführung
    durchgefallen.
    """

    continuous: bool
    raw: bool
    chart_type: str
    show_points: bool
    show_values: bool
    dynamic_y_axis: bool
    chart_stats: bool
    show_marked: bool = False
    average_line: bool = False
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
    _background.invalidate_retention_overview()
    return {"ok": True}


@app.post("/entities/{entity_id}/delete")
@_storage_locked(lambda args: args["entity_id"])
def entity_delete(entity_id: str) -> dict:
    """Entfernt eine Entität einschließlich aller Werte und Indexmetadaten."""
    _require_entity(entity_id)
    entity_removal.delete_entity(DATA_DIR, index, entity_id)
    _background.invalidate_retention_overview()
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
    # Bereiche mit zur Löschung markierten Werten als Band im Chart. Standard aus:
    # der Normalfall ist eine Entität ohne offene Markierungen, und ein
    # Schalter, der bei fast allen nichts bewirkt, gehört nicht in die
    # Voreinstellung. Wer bereinigt, schaltet ihn für diese Entität ein — oder
    # kommt über den Link aus der Purge-Vorschau, der ihn per ?marked=1 für
    # den Besuch mitbringt, ohne ihn zu speichern.
    "show_marked": False,
    # Waagerechte Linie beim Durchschnitt der gezeigten Punkte. Standard aus:
    # sie beantwortet eine Zusatzfrage ("wo liegt der Schnitt im Bild"), und ein
    # Chart, das ungefragt eine zweite Linie zeichnet, erklärt sich schlechter
    # als eines mit nur der Messkurve. Bewusst NICHT an legend_metrics gekoppelt:
    # die Legende nennt die Zahl, die Linie zeigt die Lage — das eine will man
    # oft ohne das andere.
    "average_line": False,
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


# Kennung -> Beschriftung. Die Kennung ist die Grundform, nicht das Label: sie
# trägt auf /charts zusätzlich die Kachel-Klasse für den Kopfstreifen je Typ
# (siehe .is-typ-* in pages/charts.css). Aus dem zusammengesetzten Label
# "Linie + Balken" im Template eine Klasse abzuleiten wäre der umgekehrte,
# brüchige Weg.
_CHART_TYPE_LABELS = {
    "zeitstrahl": "Zeitstrahl",
    "linie": "Linie",
    "balken": "Balken",
    "gemischt": "Linie + Balken",
}


def _chart_type_key(chart: dict, aggregation_types: dict[str, str]) -> str:
    """Wie das Chart tatsächlich gezeichnet wird, für die Übersichtskachel.

    Gespeichert ist nur "auto" oder "timeline" — bei "auto" entscheidet der
    Aggregationstyp JEDER Entität einzeln (Zähler/Schalter → Balken, sonst
    Linie, dieselbe Regel wie _resolved_chart_type() in storage/query.py).
    Ein Chart kann deshalb beides zugleich enthalten. Ein Chart ohne
    Entitäten hat keinen Typ — leere Kennung, Kachel ohne Streifen."""
    if chart["chart_type"] == "timeline":
        return "zeitstrahl"
    vorhanden = {
        "balken" if aggregation_types.get(entity_id) in ("counter", "switch") else "linie"
        for entity_id in chart["entity_ids"]
    }
    return "gemischt" if len(vorhanden) > 1 else next(iter(vorhanden), "")


def _chart_type_label(chart: dict, aggregation_types: dict[str, str]) -> str:
    return _CHART_TYPE_LABELS.get(_chart_type_key(chart, aggregation_types), "")


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
            "type_key": _chart_type_key(c, aggregation_types),
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
        "average_line": chart["average_line"] if chart else False,
        "area_fill": chart["area_fill"] if chart else True,
        "entity_names": chart["entity_names"] if chart else {},
        "hidden_entity_ids": chart["hidden_entity_ids"] if chart else [],
        "entity_options": entity_options,
        "range_options": _CHART_RANGE_OPTIONS,
        "dashboard_usage": index.list_item_dashboards("chart", chart["id"]) if chart else [],
        # compare/compare_mode sind (wie im Chart-Editor selbst) reine
        # Laufzeit-Ansichtseinstellungen, kein gespeichertes Chart-Feld — nur
        # der Anfangszustand eines gerade erst über prefill eröffneten neuen
        # Charts kann sie daher überhaupt sinnvoll setzen.
        "compare": prefill.get("compare", False) if not chart else False,
        "compare_mode": prefill.get("compare_mode", "previous") if not chart else "previous",
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
    hidden_entity_ids: list[EntityId] = []
    resolution_preset: str = "auto"
    dynamic_y_axis: bool = True
    chart_stats: bool = True
    legend_metrics: list[str] = ["sum"]
    legend_style: str = "chips"
    chart_type: str = "auto"
    decimals: str = "auto"
    show_values: bool = False
    average_line: bool = False
    area_fill: bool = True


def _hidden_for(body: _SaveChartBody) -> list[str]:
    """Ausgeblendete Serien, auf die tatsächlich ausgewählten begrenzt.

    Der Editor filtert schon beim Absenden, hier noch einmal: eine ID, die
    nicht in entity_ids steht, hätte im Chart keine Entsprechung und würde
    stumm mitgespeichert, bis sie irgendwann wieder auftaucht."""
    erlaubt = set(body.entity_ids)
    return [eid for eid in body.hidden_entity_ids if eid in erlaubt]


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
        entity_names, hidden_entity_ids=_hidden_for(body),
        resolution_preset=body.resolution_preset, dynamic_y_axis=body.dynamic_y_axis,
        chart_stats=body.chart_stats, legend_metrics=body.legend_metrics,
        legend_style=body.legend_style, chart_type=body.chart_type,
        decimals=body.decimals, show_values=body.show_values,
        average_line=body.average_line, area_fill=body.area_fill,
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
        body.continuous, entity_names, hidden_entity_ids=_hidden_for(body),
        resolution_preset=body.resolution_preset,
        dynamic_y_axis=body.dynamic_y_axis, chart_stats=body.chart_stats, legend_metrics=body.legend_metrics,
        legend_style=body.legend_style, chart_type=body.chart_type,
        decimals=body.decimals, show_values=body.show_values,
        average_line=body.average_line, area_fill=body.area_fill,
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
        chart["entity_names"], hidden_entity_ids=chart["hidden_entity_ids"],
        resolution_preset=chart["resolution_preset"],
        dynamic_y_axis=chart["dynamic_y_axis"],
        chart_stats=chart["chart_stats"], legend_metrics=chart["legend_metrics"],
        legend_style=chart["legend_style"], chart_type=chart["chart_type"],
        decimals=chart["decimals"], show_values=chart["show_values"],
        average_line=chart["average_line"], area_fill=chart["area_fill"],
    )
    return {"id": new_id}


# Der Zeitraum-Text einer Werte-Kachel — je Zeitraum eine kalendarische und
# eine rollierende Beschriftung. Der Text ist die einzige Auskunft darüber,
# welche der beiden Varianten läuft; ein zusätzliches Symbol braucht es damit
# nicht. Die rollierenden Namen sind bewusst das TATSÄCHLICHE Fenster aus
# _window() in storage/query.py und nicht "Monat rollierend": dort sind es
# genau 30 Tage, kein Kalendermonat (Begründung steht als Kommentar an der
# Stelle). "30 Tage" macht diese Vereinfachung sichtbar, statt sie unter dem
# Wort "Monat" zu verstecken.
#
# Dieselbe Tabelle steht ein zweites Mal in static/js/dashboard-tiles.js —
# der Server beschriftet die erste Anzeige, der Browser beschriftet nach einer
# Änderung im Kachelmenü neu, ohne die Seite neu zu laden. Ein Test hält beide
# Kopien deckungsgleich (test_dashboard_value_tile_settings.py).
_TILE_RANGE_LABELS = {
    "hour": ("Std.", "60 Min."),
    "day": ("Tag", "24 Std."),
    "week": ("Woche", "7 Tage"),
    "month": ("Monat", "30 Tage"),
    "year": ("Jahr", "12 Monate"),
}
# Kennzeichen vor der großen Zahl, sobald diese nicht der aktuelle Wert ist —
# ohne das wäre "16,8 °C" nicht von einem Momentanwert zu unterscheiden.
# "last" trägt bewusst keins: der aktuelle Wert ist der Normalfall und braucht
# keine Erklärung.
_TILE_METRIC_LABELS = {"last": "", "min": "Min", "avg": "Ø", "max": "Max", "sum": "Σ"}


def _tile_metric_labels(aggregation_type: str | None) -> dict[str, str]:
    """Die Kürzel für einen Entitätstyp — genau eines hängt davon ab.

    Bei einem Zähler ist die Summe über den Zeitraum der Zuwachs des
    Zählerstands: „Σ 6,496 kWh" ist rechnerisch richtig und beschreibt einen
    Tagesertrag trotzdem falsch. Beim Schalter bleibt es Σ — dort summiert die
    Kennzahl Einschaltdauer, das ist kein Zuwachs, und „+2h 15m" wäre keine
    bessere Beschreibung. Bei Messwerten ist die Summe gar nicht erst wählbar
    (siehe _tile_available_metrics()).
    """
    if aggregation_type == "counter":
        return {**_TILE_METRIC_LABELS, "sum": "+"}
    return _TILE_METRIC_LABELS


def _tile_available_metrics(aggregation_type: str | None) -> list[str]:
    """Welche Kennzahlen für diesen Entitätstyp überhaupt eine Aussage sind.

    Dieselbe Regel wie in der Chart-Legende (``hasSum`` in entity_detail.html)
    und in _tile_aggregates() in api_routes.py, das die nicht angebotenen
    Werte gar nicht erst mitschickt:

    * Summe nur bei Zählern und Schaltern — 20 °C + 21 °C + … ist keine
      Temperatur.
    * Min/Max nicht bei Schaltern — deren Bucket-Werte sind Einschaltsekunden,
      "kleinster Wert" hieße dort "kürzeste Stunde".

    Das Menü zeigt die übrigen durchgestrichen und unklickbar an, statt sie
    wegzulassen: eine Reihe, die je nach Entität mal drei und mal vier Knöpfe
    hat, wirkt wie ein Fehler, ein durchgestrichener Knopf erklärt sich.
    """
    if aggregation_type == "switch":
        return ["sum"]
    if aggregation_type == "counter":
        return ["min", "avg", "max", "sum"]
    return ["min", "avg", "max"]


def _tile_metric_context(pin, aggregation_type: str | None = None) -> dict:
    """Zeitraum und Kennzahlen einer Werte-Kachel für das Template.

    Der als Hauptwert gewählte Eintrag fällt hier aus der Kennzahlen-Zeile
    heraus — derselbe Wert zweimal auf einer Kachel wäre nur Rauschen. Die
    Nachschläge sind absichtlich fehlertolerant: ein von Hand verbogener
    Datenbankwert soll die Dashboard-Seite nicht mit einem KeyError
    abschießen, sondern auf den Standard zurückfallen.
    """
    range_key = pin["range_key"] if pin["range_key"] in _TILE_RANGE_LABELS else "day"
    primary = pin["primary_metric"] if pin["primary_metric"] in _TILE_METRIC_LABELS else "last"
    verfuegbar = _tile_available_metrics(aggregation_type)
    labels = _tile_metric_labels(aggregation_type)
    # Auch gegen den Entitätstyp gefiltert, nicht nur gegen den Hauptwert: ein
    # Wechsel der Entität (dashboard/entity/{id}) lässt die gespeicherten
    # Kennzahlen stehen, und ein Σ, das für einen Messwert gespeichert wurde,
    # soll danach nicht als "–" auf der Kachel kleben bleiben.
    metrics = [
        m for m in (pin["stats_metrics"] or "").split(",")
        if m and m != primary and m in verfuegbar
    ]
    return {
        "range_key": range_key,
        "continuous": bool(pin["continuous"]),
        "range_label": _TILE_RANGE_LABELS[range_key][1 if pin["continuous"] else 0],
        "primary_metric": primary,
        "primary_label": labels[primary],
        # Auch die Kennzahlen-Zeile und das Menü beschriften sich hieraus,
        # damit dasselbe Σ/+ nicht an vier Stellen einzeln entschieden wird.
        "metric_labels": labels,
        "stats_metrics": metrics,
        # Der Zeitraum steht genau einmal auf der Kachel: in der
        # Kennzahlen-Zeile, wenn es sie gibt, sonst im Wert-Bereich an der
        # Stelle des Alters. Bei einem Hauptwert, der kein Momentanwert ist,
        # sagt das Alter des letzten Rohpunkts ohnehin nichts über einen
        # Monatsdurchschnitt aus — dort tritt der Zeitraum an seine Stelle.
        "show_period_in_value_row": not metrics and primary != "last",
        # Fürs Kachelmenü: nicht anwendbare Kennzahlen werden durchgestrichen
        # gezeigt statt weggelassen (siehe _tile_available_metrics()).
        "available_metrics": verfuegbar,
    }


def _dashboard_tiles_context(
    dashboard_id: int, auto_open_entity_id: str | None = None
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
    dabei still übersprungen statt einen Fehler zu werfen.

    URLs im Fragment laufen über app_root (Context-Processor, aus dem
    X-Ingress-Path-Header). Vorher stand hier ein relativer Rückweg, den der
    Aufrufer als "base" mitgeben musste — "." auf "/", ".." auf
    "/dashboards/{id}" —, weil dasselbe Fragment auf zwei Seitentiefen
    eingehängt wird und der Server die Tiefe sonst nicht kennt. Bei pin/unpin
    reiste der Wert sogar als Query-Parameter durch die URL. Mit app_root
    entfällt beides: der Präfix ist absolut und tiefenunabhängig (ZG-03)."""
    pins = index.list_dashboard_pins(dashboard_id)
    tiles = []
    # Sektionen (item_type='section') gruppieren die Kacheln rein über ihre
    # Position in derselben Liste — keine Kachel trägt eine section_id. Die
    # erste, kopflose Gruppe sammelt alles vor dem ersten Trenner ("ohne
    # Sektion"); jeder weitere Trenner eröffnet eine neue Gruppe mit eigenem
    # Mini-Raster (siehe _dashboard_tiles.html) statt eines gemeinsamen, vollen
    # Rasters — das hält Sektionsköpfe im präzisen Modus kompakt (kein
    # Rasterelement mit fester grid-auto-rows-Höhe) und "Lücken auffüllen" pro
    # Sektion begrenzt.
    groups: list[dict] = [{"title": None, "section_id": None, "tiles": []}]
    for p in pins:
        if p["item_type"] == "section":
            groups.append({"title": p["title"] or "", "section_id": p["id"], "tiles": []})
            continue
        if p["item_type"] == "chart":
            c = index.get_saved_chart(p["item_id"])
            if c is None:
                continue
            tiles.append({
                "kind": "chart", "id": c["id"], "name": c["name"],
                "entity_ids": c["entity_ids"], "range_key": c["range_key"],
                "continuous": c["continuous"], "entity_names": c["entity_names"],
                # Ausgeblendete Serien reichen bis in die Kachel: sie sollen
                # dort weder gezeichnet noch in der Legende gezählt werden.
                # Die Liste (statt einer schon gefilterten entity_ids) geht
                # mit, weil die Farbe an der Position in der VOLLEN Auswahl
                # hängt — sonst wären dieselben Serien auf Chart-Seite und
                # Kachel unterschiedlich eingefärbt.
                "hidden_entity_ids": c["hidden_entity_ids"],
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
                "average_line": c["average_line"], "area_fill": c["area_fill"],
            })
            groups[-1]["tiles"].append(tiles[-1])
        elif p["item_type"] == "table":
            t = index.get_saved_table(p["item_id"])
            if t is None:
                continue
            tiles.append({
                "kind": "table", "id": t["id"], "name": t["name"],
                "columns": t["columns"], "rows": t["rows"], "style": t["style"],
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
            })
            groups[-1]["tiles"].append(tiles[-1])
        elif p["item_type"] == "entity":
            e = index.get_entity(p["item_entity_id"])
            if e is None:
                continue
            is_switch = e["aggregation_type"] == "switch"
            # Kachel-Override hat Vorrang vor der entity-eigenen Einstellung,
            # "auto" (Feld-Default) bedeutet ausdrücklich "entity-eigenen Wert
            # übernehmen" statt selbst "auto" an format_value() zu reichen.
            effective_decimals = p["decimals"] if p["decimals"] != "auto" else (e["decimals"] or "auto")
            metric_context = _tile_metric_context(p, e["aggregation_type"])
            if metric_context["primary_metric"] != "last":
                # Der Hauptwert ist eine Aggregation über den Zeitraum, die
                # der Server hier ohne eigene Abfrage nicht kennt. Platzhalter
                # statt des aktuellen Werts: sonst stünde für den Bruchteil
                # einer Sekunde ein sichtbar falscher Wert auf der Kachel
                # (Momentanwert statt Monatsdurchschnitt), bis der erste
                # Fetch ihn ersetzt.
                value_text = "–"
            elif e["last_value"] is None:
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
                **metric_context,
            })
            groups[-1]["tiles"].append(tiles[-1])
    # Eine leere kopflose Erstgruppe (alle Kacheln liegen bereits hinter einem
    # Trenner) wird nicht mitgerendert — sonst stünde ein leeres, unbenanntes
    # Mini-Raster über der ersten echten Sektion. Bleibt sie die einzige
    # Gruppe (frisches Dashboard ganz ohne Sektionen/Kacheln), muss sie
    # stehen bleiben, sie trägt dann die "+"-Kachel.
    if not groups[0]["tiles"] and len(groups) > 1:
        groups.pop(0)
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
        "groups": groups,
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


# -- Sektionen: Formular-POSTs statt JSON-Body, damit der "+ Sektion"-Reiter
# in _dashboard_tiles.html ein normales <form hx-post=…> bleiben kann wie die
# übrigen Picker-Einträge dort — kein zusätzlicher JS-Mechanismus nötig.
@app.post("/dashboard/section/add", response_class=HTMLResponse)
def dashboard_section_add(request: Request, dashboard_id: int = Form(1), name: str = Form(...)) -> HTMLResponse:
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name darf nicht leer sein")
    index.add_dashboard_section(dashboard_id, name)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


@app.post("/dashboard/section/{section_id}/rename", response_class=HTMLResponse)
def dashboard_section_rename(
    request: Request, section_id: int, dashboard_id: int = Form(1), name: str = Form(...)
) -> HTMLResponse:
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name darf nicht leer sein")
    if not index.rename_dashboard_section(dashboard_id, section_id, name):
        raise HTTPException(status_code=404, detail="Sektion nicht gefunden")
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


@app.post("/dashboard/section/{section_id}/remove", response_class=HTMLResponse)
def dashboard_section_remove(request: Request, section_id: int, dashboard_id: int = 1) -> HTMLResponse:
    """Löst die Sektion auf — die Kacheln bleiben erhalten, siehe
    index.remove_dashboard_section()."""
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.remove_dashboard_section(dashboard_id, section_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


@app.post("/charts/{chart_id}/pin", response_class=HTMLResponse)
def charts_pin(request: Request, chart_id: int, dashboard_id: int = 1) -> HTMLResponse:
    if index.get_saved_chart(chart_id) is None:
        raise HTTPException(status_code=404, detail="Chart nicht gefunden")
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.pin_item_to_dashboard(dashboard_id, "chart", chart_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


@app.post("/charts/{chart_id}/unpin", response_class=HTMLResponse)
def charts_unpin(request: Request, chart_id: int, dashboard_id: int = 1) -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    index.unpin_item_from_dashboard(dashboard_id, "chart", chart_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


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
def dashboard_pin_entity(request: Request, entity_id: str, dashboard_id: int = 1) -> HTMLResponse:
    _require_entity(entity_id)
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.pin_entity_to_dashboard(dashboard_id, entity_id)
    return templates.TemplateResponse(
        request, "_dashboard_tiles.html",
        _dashboard_tiles_context(dashboard_id, auto_open_entity_id=entity_id),
    )


@app.post("/dashboard/entity/{entity_id}", response_class=HTMLResponse)
async def dashboard_entity_change(
    request: Request, entity_id: str, dashboard_id: int = 1
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
        _dashboard_tiles_context(dashboard_id, auto_open_entity_id=new_entity_id),
    )


@app.post("/dashboard/unpin-entity/{entity_id}", response_class=HTMLResponse)
def dashboard_unpin_entity(request: Request, entity_id: str, dashboard_id: int = 1) -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    index.unpin_entity_from_dashboard(dashboard_id, entity_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


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


class _MetricsDashboardTileBody(BaseModel):
    """Zeitraum und Kennzahlen einer Werte-Kachel.

    Alle vier Felder optional, None heißt "unverändert lassen" — anders als
    bei den übrigen Kachel-Einstellungen bewusst EIN Endpunkt für vier Werte:
    sie hängen voneinander ab (der Hauptwert fällt aus der Kennzahlen-Zeile
    heraus, ein Entitätswechsel kann eine Kennzahl unanwendbar machen), und
    getrennte Endpunkte bräuchten dafür zwei Runden.
    """

    dashboard_id: int = 1
    entity_id: str
    range_key: str | None = None
    continuous: bool | None = None
    primary_metric: str | None = None
    stats_metrics: list[str] | None = None


@app.post("/dashboard/entity-metrics")
def dashboard_entity_metrics(body: _MetricsDashboardTileBody) -> dict:
    _require_dashboard_unlocked(body.dashboard_id)
    try:
        geaendert = index.set_dashboard_entity_pin_metrics(
            body.dashboard_id,
            body.entity_id,
            range_key=body.range_key,
            continuous=body.continuous,
            primary_metric=body.primary_metric,
            stats_metrics=body.stats_metrics,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not geaendert:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    pin = next(
        (p for p in index.list_dashboard_pins(body.dashboard_id)
         if p["item_type"] == "entity" and p["item_entity_id"] == body.entity_id),
        None,
    )
    if pin is None:
        raise HTTPException(status_code=404, detail="Dashboard-Kachel nicht gefunden")
    entity = index.get_entity(body.entity_id)
    # Den fertigen Anzeigezustand zurückgeben statt nur "ok": der Browser muss
    # das Zeitraum-Etikett und die um Hauptwert und Entitätstyp bereinigte
    # Kennzahlen-Liste sonst selbst nachbilden — genau die Regeln, die hier
    # schon stehen.
    return {
        "ok": True,
        **_tile_metric_context(pin, entity["aggregation_type"] if entity else None),
    }


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
            # Für das Startseiten-Symbol an der Energiedashboard-Kachel unten
            # (STARTSEITE_LABELS oben) — Default "uebersicht" spiegelt
            # dieselbe Rückfallregel wie überall sonst, wo diese Einstellung
            # gelesen wird.
            "startseite": index.get_setting("startseite", "uebersicht"),
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
    return templates.TemplateResponse(request, "dashboard_editor.html", {"dashboard": None})


@app.get("/dashboards/{dashboard_id}", response_class=HTMLResponse)
def dashboard_detail(request: Request, dashboard_id: int) -> HTMLResponse:
    dashboard = _get_dashboard_or_404(dashboard_id)
    context = {
        "dashboard": dashboard,
        **_dashboard_tiles_context(dashboard_id),
    }
    return templates.TemplateResponse(request, "dashboard_detail.html", context)


@app.get("/dashboards/{dashboard_id}/edit", response_class=HTMLResponse)
def dashboards_edit(request: Request, dashboard_id: int) -> HTMLResponse:
    dashboard = _get_dashboard_or_404(dashboard_id)
    return templates.TemplateResponse(
        request, "dashboard_editor.html", {"dashboard": dashboard}
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
def tables_pin(request: Request, table_id: int, dashboard_id: int = 1) -> HTMLResponse:
    if index.get_saved_table(table_id) is None:
        raise HTTPException(status_code=404, detail="Tabelle nicht gefunden")
    _get_dashboard_or_404(dashboard_id)
    _require_dashboard_unlocked(dashboard_id)
    index.pin_item_to_dashboard(dashboard_id, "table", table_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


@app.post("/tables/{table_id}/unpin", response_class=HTMLResponse)
def tables_unpin(request: Request, table_id: int, dashboard_id: int = 1) -> HTMLResponse:
    _require_dashboard_unlocked(dashboard_id)
    index.unpin_item_from_dashboard(dashboard_id, "table", table_id)
    return templates.TemplateResponse(request, "_dashboard_tiles.html", _dashboard_tiles_context(dashboard_id))


@app.get("/entities/{entity_id}", response_class=HTMLResponse)
@_storage_locked(lambda args: args["entity_id"])
def entity_detail(
    request: Request,
    entity_id: str,
    range_key: str | None = Query(None, alias="range"),
    offset: int = 0,
    marked: bool = False,
) -> HTMLResponse:
    # URLs dieser Seite laufen über app_root: unter Ingress hat sie einen
    # dynamischen Pfad-Präfix, ein fest absoluter Pfad ("/api/query") würde
    # daran vorbeizeigen (Konzept Abschnitt 06). Früher stand hier ein
    # relativer Rückweg je Verschachtelungstiefe; app_root ist tiefenunabhängig
    # und muss deshalb beim Anlegen einer Route nicht mitgedacht werden.
    entity = _require_entity(entity_id)
    # range/offset optional per Query-Parameter — z. B. vom Energiedashboard
    # aus verlinkt, mit dem dort gerade gewählten Zeitraum. Ungültiger/
    # fehlender range-Wert fällt auf das Alpine-eigene Standardverhalten
    # zurück ('day'/0, siehe entityChart()), statt eine Fehlerseite zu zeigen.
    initial_range = range_key if range_key in query_mod.RANGE_KEYS else None
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
            "first_date": first_date,
            "last_date": last_date,
            "is_favorite": bool(entity["is_favorite"]),
            "chart_options": chart_options,
            "entity_chart_defaults": _get_entity_chart_defaults(),
            "initial_range": initial_range,
            "initial_offset": offset if initial_range else 0,
            # ?marked=1 schaltet die Markierungs-Anzeige NUR für diesen Besuch
            # ein — der Link aus der Purge-Vorschau (Housekeeping ->
            # Speicherplatz -> Endgültige Bereinigung) führt hierher, und wer von dort kommt,
            # will sie sehen. Bewusst ohne Speichern: die Seite über einen Link
            # zu öffnen ist keine Einstellung, und beim nächsten Aufruf ohne
            # Parameter gilt wieder, was im Optionen-Menü steht.
            "initial_marked": marked,
            # Steuert den statischen "zurück zum Energiedashboard"-Link (nur
            # gezeigt, wenn der Rücksprung dorthin überhaupt sinnvoll ist) —
            # bewusst zusätzlich zum referrer-basierten dynamic-back-link.js,
            # das unter Home-Assistant-Ingress beim Sprung aus der
            # Sidebar-Kachel nicht zuverlässig funktioniert (document.referrer
            # fehlt dort).
            "used_in_energiedashboard": entity_has_energiedashboard_role(index, entity_id),
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
            # Für die Unterzeile: die drei Entitätsseiten tragen seit der
            # Reiterzeile denselben Kopf, und dort steht der Typ.
            "type_label": format_type(entity["aggregation_type"]),
            "first_date": first_date,
            "last_date": last_date,
            "gap_detection_enabled": entity["gap_threshold"] != "off",
            "outlier_detection_enabled": effective_outlier_threshold(
                entity["aggregation_type"], entity["outlier_threshold"]
            ) != "off",
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


# "Jahr" kann wie "Gesamt" MAX_UI_ANALYSIS_ROWS überschreiten (stiller 413, htmx swappt 4xx nicht ein) — beide laufen über den Streaming-Pfad.
_STREAMING_RANGE_KEYS = ("year", "all")

_MONTH_NAMES_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)




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
    request: Request, entity_id: str, filter_: str, range_key: str, offset: int = 0, page: int = 1, page_size: int = 20,
    mode: str = "cleanup", deleted_count: int | None = None,
) -> HTMLResponse:
    entity = index.get_entity(entity_id)
    decimals_int = decimals_to_int(entity["decimals"])
    now = datetime.now(TZ)
    if range_key not in cleanup_stats.CLEANUP_RANGE_KEYS:
        range_key = "day"
    offset = 0 if range_key == "all" else min(offset, 0)
    window_start, window_end = cleanup_stats.rows_window(range_key, offset, now, entity["first_ts"], TZ)

    gap_threshold = entity["gap_threshold"]
    outlier_threshold = effective_outlier_threshold(
        entity["aggregation_type"], entity["outlier_threshold"]
    )
    if range_key in _STREAMING_RANGE_KEYS:
        # Können Millionen Rohwerte umfassen: materialisiert nur die angeforderte Seite (zwei Streaming-Durchläufe).
        effective_page_size = 1000 if page_size <= 0 else min(page_size, 1000)
        # Beide Durchläufe lesen bei einem Fenster, das den laufenden Monat
        # einschließt, sonst dieselbe Hot-CSV zweimal neu von Platte (siehe
        # PERFORMANCE.md, ZP-012) — QueryReadCache ist request-lokal, macht
        # daraus nur einen Lesevorgang.
        read_cache = query_mod.QueryReadCache()

        def rows_factory():
            return cleanup.iter_raw_rows(
                DATA_DIR,
                index,
                entity_id,
                window_start.timestamp(),
                window_end.timestamp(),
                TZ,
                now=now,
                hot_rows_loader=read_cache.read_hot_rows,
            )

        analysis = cleanup.analyze_raw_rows_page(
            rows_factory,
            filter_=filter_,
            page=page,
            page_size=effective_page_size,
            gap_threshold_minutes=(
                None if gap_threshold == "off" else float(gap_threshold)
            ),
            outlier_factor=(
                None if outlier_threshold == "off" else float(outlier_threshold)
            ),
            tz=TZ,
            decimals=entity["decimals"],
            counter_decrease_enabled=entity["state_class"] == "total_increasing",
            outlier_mode=cleanup_stats.outlier_mode(entity),
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
            entity["decimals"], TZ, mode=cleanup_stats.outlier_mode(entity),
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
        index.set_cleanup_alltime_stats(
            entity_id, counts, outlier_threshold, cleanup.OUTLIER_RULE_VERSION
        )
    else:
        alltime_counts = cleanup_stats.alltime_counts(DATA_DIR, index, TZ, entity, now)

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
            "deleted_count": deleted_count,
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
    page_size: int = 20,
    mode: str = "cleanup",
) -> HTMLResponse:
    _require_entity(entity_id)
    return _rows_fragment(request, entity_id, filter, range_key, offset, page, page_size, mode)


def _rows_form_common(form) -> tuple[str, str, int, int, int, str]:
    filter_ = str(form.get("filter", "all"))
    range_key = str(form.get("range", "day"))
    offset = int(form.get("offset", 0))
    page = int(form.get("page", 1))
    page_size = int(form.get("page_size", 20))
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
            return _rows_fragment(
                request, entity_id, filter_, range_key, offset, page, page_size, mode,
                deleted_count=len(timestamps) or None,
            )

    result = await run_in_threadpool(delete_locked)
    if timestamps:
        _background.invalidate_purge_preview()
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
    window_start, window_end = cleanup_stats.rows_window(range_key, offset, now, entity["first_ts"], TZ)

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
    window_start, window_end = cleanup_stats.rows_window(range_key, offset, now, entity["first_ts"], TZ)

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
    window_start, window_end = cleanup_stats.rows_window(range_key, offset, now, entity["first_ts"], TZ)

    deleted_total = 0

    def delete_locked() -> HTMLResponse:
        nonlocal deleted_total
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
                    deleted_total += len(batch)
                    batch = []
            if batch:
                index.mark_deleted(entity_id, batch, deleted_at=deleted_at)
                deleted_total += len(batch)
            return _rows_fragment(
                request, entity_id, filter_, range_key, offset, page, page_size, mode,
                deleted_count=deleted_total or None,
            )

    result = await run_in_threadpool(delete_locked)
    if deleted_total:
        _background.invalidate_purge_preview()
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
    run_storage_reconciliation=_background.run_storage_reconciliation,
    symcon_import_dir=SYMCON_IMPORT_DIR,
    csv_import_dir=CSV_IMPORT_DIR,
    symcon_names_path=SYMCON_NAMES_PATH,
    symcon_source_meta_path=SYMCON_SOURCE_META_PATH,
    symcon_scan_cache_path=SYMCON_SCAN_CACHE_PATH,
))
app.include_router(_import_service.router())

# Housekeeping-Bereich (Aufbewahrung, Rotation, Speicherplatz, Duplikate) —
# ganz am Ende eingehängt wie die übrigen ausgelagerten Router. Die
# hereingereichten Funktionen bleiben bewusst hier: sie werden auch vom
# Hintergrund-Scheduler und von der Einstellungsseite gebraucht (siehe
# Modul-Docstring in housekeeping_routes.py).
app.include_router(create_housekeeping_router(HousekeepingDependencies(
    data_dir=DATA_DIR,
    tz=TZ,
    index=index,
    coordinator=storage_coordinator,
    base_dir=BASE_DIR, demo_mode_active=DEMO_MODE,
    templates=templates,
    retention_default_time=RETENTION_DEFAULT_TIME,
    retention_default_weekday=RETENTION_DEFAULT_WEEKDAY,
    chart_range_options=_CHART_RANGE_OPTIONS,
    gap_threshold_minute_tiers=_GAP_THRESHOLD_MINUTE_TIERS,
    backup_weekday_options=BACKUP_WEEKDAY_OPTIONS,
    retention_progress=_background.retention_progress,
    storage_locked=_storage_locked,
    settings_archivierung_context=_settings_archivierung_context,
    refresh_purge_preview_if_stale=_background.refresh_purge_preview_if_stale,
    refresh_retention_overview_if_stale=_background.refresh_retention_overview_if_stale,
    begin_retention_job=_background.begin_retention_job,
    finish_retention_job=_background.finish_retention_job,
    run_storage_reconciliation=_background.run_storage_reconciliation,
    gap_threshold_auto_adjust_message=_gap_threshold_auto_adjust_message,
    set_next_retention_run=_background.set_next_retention_run,
    chart_type_label=_chart_type_label,
    count_stale_entities=_count_stale_entities,
    load_purge_preview=_background.load_purge_preview,
    load_retention_overview=_background.load_retention_overview,
    # Lambdas statt der Werte: beide werden per global neu gebunden,
    # ein Feldwert wäre für immer das None vom Programmstart.
    host_disk_usage_cached=lambda: _background.host_disk_usage_cached,
    storage_reconcile_last=lambda: _background.storage_reconcile_last,
)))
