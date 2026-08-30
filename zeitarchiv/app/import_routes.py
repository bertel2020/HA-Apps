"""Symcon-/CSV-/Home-Assistant-Import: Upload, Scan, Dry-Run/Start und der
Reports-Tab-Kontext für /import — ausgelagert aus main.py (Konzept
"main.py-Zeilenbudget", siehe test_route_modules.py), demselben Muster wie
report_routes.py/api_routes.py: ein *Dependencies-Frozen-Dataclass wird von
main.py befüllt, ein *Service kapselt sowohl privaten Zustand (Scan-Cache,
Hintergrund-Fortschritt, Quellordner-Sperren) als auch die Routen selbst
(router(), als verschachtelte Closures registriert statt gebundener
Methoden — dieselbe Technik wie ReportService.router())."""

from __future__ import annotations

import dataclasses
import io
import json
import logging
import math
import shutil
import threading
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

import pyarrow.parquet as pq

from .formatting import (
    format_int,
    format_size,
    format_time,
    format_timestamp,
    format_type,
    format_value,
    parse_localized_number,
)
from .limits import (
    MAX_CSV_UPLOAD_BYTES,
    MAX_IMPORT_ROWS_PER_ENTITY,
    MAX_SETTINGS_UPLOAD_BYTES,
    MAX_ZIP_UPLOAD_BYTES,
)
from .route_support import UploadLimitExceeded, copy_upload_limited, dir_size
from .storage import csv_import, ha_import, ha_statistics, hotbuffer, import_reports, symcon_import
from .storage.coordinator import StorageCoordinator
from .storage.index import Index
from .storage.paths import entity_dir
from .version import APP_VERSION

logger = logging.getLogger(__name__)


# Zeitraum-Voreinstellungen für den Home-Assistant-Import-Reiter (dd-picker,
# siehe _ha_import_section.html) — getrennt nach Quelle (history_source),
# weil sich die sinnvolle Obergrenze stark unterscheidet: Rohhistorie hält
# HA standardmäßig nur ~10 Tage vor (bei individuell verlängerter Recorder-
# Aufbewahrung, purge_keep_days, potenziell mehr, aber "max" bleibt trotzdem
# begrenzt — sonst könnte ein einzelner Klick über Jahre in HISTORY_CHUNK-
# Fenstern hunderte Requests auslösen). Langzeitstatistik dagegen wird von
# HA per Voreinstellung NIE automatisch bereinigt, "max" ist hier also
# bewusst großzügiger (siehe ha_statistics.py-Moduldocstring), aber ebenso
# aus Sicherheitsgründen nicht wirklich unbegrenzt.
HA_RANGE_PRESETS_RAW = {
    "max": "Verfügbare Historie (max.)",
    "10d": "Letzte 10 Tage",
    "30d": "Letzte 30 Tage",
    "custom": "Eigener Zeitraum …",
}
HA_RANGE_PRESET_DAYS_RAW = {"10d": 10, "30d": 30}
HA_MAX_RANGE_DAYS_RAW = 365
HA_RANGE_PRESETS_FULL_RAW = {
    key: HA_RANGE_PRESETS_RAW[key] for key in ("max", "30d", "10d")
}

HA_RANGE_PRESETS_STATS = {
    "max": "Verfügbare Statistik (max.)",
    "30d": "Letzte 30 Tage",
    "90d": "Letzte 90 Tage",
    "365d": "Letztes Jahr",
    "custom": "Eigener Zeitraum …",
}
HA_RANGE_PRESET_DAYS_STATS = {"30d": 30, "90d": 90, "365d": 365}
HA_MAX_RANGE_DAYS_STATS = 3650

# Der geführte Vollimport verwendet immer Stundenstatistik: Nur so kostet die
# saubere Schnittstelle zur Rohhistorie höchstens einen Teil einer Stunde statt
# bei Tageswerten potenziell fast einen ganzen Tag verfügbarer Rohdaten.
HA_FULL_IMPORT_PERIOD = "hour"
HA_RANGE_PRESETS_FULL_STATS = {
    key: HA_RANGE_PRESETS_STATS[key] for key in ("max", "365d", "90d", "30d")
}

# Ab wann eine gecachte Verfügbarkeitsprüfung (siehe _HaAvailabilityCache) als
# veraltet gilt und die Tabelle warnt — HA-Zustände ändern sich laufend, eine
# Stunden alte Prüfung könnte längst nicht mehr stimmen, ohne dass die
# Tabelle das erkennen lässt.
HA_AVAILABILITY_STALE_SECONDS = 15 * 60


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


class _HaAvailabilityCache:
    """Cache der zuletzt geprüften HA-Verfügbarkeit je Importkonfiguration
    (Quelle, Auflösung, Zeiträume und Vollimport-Statistikoption; siehe
    _ha_cache_key()) — ohne das ginge eine
    bereits geprüfte Verfügbarkeit bei jedem GET /import (Seitenwechsel,
    Neuladen) verloren: _ha_import_context() kannte sie bisher nur für die
    Dauer der jeweiligen POST-Antwort, die dieselbe Vorlage neu rendert.
    Wie _ScanCache rein im Prozessspeicher — geht bei einem Server-Neustart
    verloren, für eine reine Anzeige-Vorschau (kein Importzustand, jederzeit
    per Klick neu abrufbar) ausreichend."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # key: _ha_cache_key(...) -> {"checked_at": float,
        # "availability": dict[str, EntityAvailability], "error": str | None}
        self.entries: dict[str, dict] = {}


@dataclass(frozen=True)
class ImportDependencies:
    data_dir: Path
    tz: ZoneInfo
    index: Index
    coordinator: StorageCoordinator
    templates: Jinja2Templates
    app_root_context: Callable[[Request], dict]
    reports_context: Callable[..., dict]
    run_storage_reconciliation: Callable[..., dict]
    symcon_import_dir: Path
    csv_import_dir: Path
    symcon_names_path: Path
    symcon_source_meta_path: Path
    symcon_scan_cache_path: Path


class ImportService:
    def __init__(self, deps: ImportDependencies) -> None:
        self.deps = deps
        self._scan_cache = _ScanCache()
        # ZIP-Extraktion, Scan, Löschen und Import dürfen denselben Symcon-Quellordner
        # nicht gleichzeitig lesen bzw. ersetzen. Diese Sperre ist absichtlich von den
        # Archiv-Sperren getrennt; die feste Reihenfolge lautet immer Quelle, danach
        # (falls nötig) StorageCoordinator, damit kein Lock-Zyklus entstehen kann.
        self._import_source_lock = threading.Lock()
        self._import_admission_lock = threading.Lock()
        self._upload_progress = _UploadProgress()
        self._import_progress = _ImportProgress()
        self._ha_availability_cache = _HaAvailabilityCache()


    @staticmethod
    def _ha_cache_key(
        history_source: str,
        period: str,
        range_preset: str = "max",
        stats_range_preset: str = "max",
        include_long_term_stats: bool = True,
    ) -> str:
        return ":".join((
            history_source,
            period if history_source in ("stats", "full") else "-",
            range_preset,
            stats_range_preset if history_source == "full" else "-",
            "stats" if history_source == "full" and include_long_term_stats else "nostats",
        ))


    def _ha_cache_lookup(
        self,
        history_source: str,
        period: str,
        range_preset: str = "max",
        stats_range_preset: str = "max",
        include_long_term_stats: bool = True,
    ) -> dict | None:
        with self._ha_availability_cache.lock:
            return self._ha_availability_cache.entries.get(self._ha_cache_key(
                history_source, period, range_preset,
                stats_range_preset, include_long_term_stats,
            ))


    def _ha_cache_store(
        self, history_source: str, period: str,
        availability: dict[str, ha_import.EntityAvailability], error: str | None,
        range_preset: str = "max",
        stats_range_preset: str = "max",
        include_long_term_stats: bool = True,
    ) -> None:
        entry = {
            "checked_at": datetime.now(timezone.utc).timestamp(),
            "availability": availability,
            "error": error,
        }
        with self._ha_availability_cache.lock:
            self._ha_availability_cache.entries[self._ha_cache_key(
                history_source, period, range_preset,
                stats_range_preset, include_long_term_stats,
            )] = entry


    def _load_symcon_names(self) -> dict[str, dict[str, str | None]]:
        """Lädt die zuletzt hochgeladene ID→{name, parent, unit}-Zuordnung (siehe
        self.deps.symcon_names_path) — leeres dict, falls noch keine settings.json importiert
        wurde oder die Datei nicht lesbar ist (z. B. manuell gelöscht). Normalisiert
        nebenbei eine ältere, noch flache {id: name}-Datei (vor der Parent-
        Auflösung geschrieben) auf dieselbe Form wie eine frische — ein einmal
        hochgeladener Stand soll nicht durch ein Code-Update ungültig werden."""
        if not self.deps.symcon_names_path.exists():
            return {}
        try:
            with self.deps.symcon_names_path.open("r", encoding="utf-8") as f:
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



    def _symcon_import_rows(self, 
        variables: list[symcon_import.SymconVariable], names: dict[str, dict[str, str | None]]
    ) -> list[dict]:
        rows = []
        for v in variables:
            if v.first_ts and v.last_ts:
                period_start = datetime.fromtimestamp(v.first_ts, self.deps.tz).strftime("%d.%m.%Y")
                period_end = datetime.fromtimestamp(v.last_ts, self.deps.tz).strftime("%d.%m.%Y")
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
                    # Als Zahl an Jinja weitergeben: import.html formatiert den
                    # Wert zentral über den format_int-Filter. Vorformatierte
                    # Strings wie "53.663" würden dort beim zweiten int()-Aufruf
                    # einen ValueError und damit HTTP 500 auslösen.
                    "row_count": v.row_count,
                    "period_start": period_start,
                    "period_end": period_end,
                    "preview": preview,
                }
            )
        return rows



    def _save_scan_cache(self, variables: list[symcon_import.SymconVariable]) -> None:
        """Schreibt das Scan-Ergebnis nach self.deps.symcon_scan_cache_path — Path-Objekte
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
        with self.deps.symcon_scan_cache_path.open("w", encoding="utf-8") as f:
            json.dump(data, f)



    def _load_scan_cache(self) -> list[symcon_import.SymconVariable] | None:
        """Gegenstück zu self._save_scan_cache() — None, wenn keine (oder eine defekte)
        Cache-Datei vorliegt, dann greift der reguläre Hintergrund-Scan als
        Fallback (siehe import_page())."""
        if not self.deps.symcon_scan_cache_path.exists():
            return None
        try:
            data = json.loads(self.deps.symcon_scan_cache_path.read_text(encoding="utf-8"))
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



    def _import_page_context(self) -> dict:
        with self._scan_cache.lock:
            variables = self._scan_cache.variables or []
        entity_options = [
            (row["entity_id"], row["friendly_name"] or row["entity_id"], row["unit"] or "")
            for row in self.deps.index.list_entities()
        ]
        names = self._load_symcon_names()
        return {
            "source_exists": self.deps.symcon_import_dir.exists() and any(self.deps.symcon_import_dir.iterdir()),
            "rows": self._symcon_import_rows(variables, names),
            "entity_options": entity_options,
            "settings_imported": bool(names),
        }



    def _cached_variables(self) -> list[symcon_import.SymconVariable]:
        """Für /import/dry-run und /import/start: nutzt denselben Cache wie die
        Seite selbst statt erneut zu scannen. Der Fallback (synchroner Scan) greift
        nur, wenn beide Endpunkte ohne vorherigen Seitenaufruf angesprochen würden —
        im normalen Ablauf ist der Cache über GET /import längst warm."""
        with self._scan_cache.lock:
            if self._scan_cache.variables is not None:
                return self._scan_cache.variables
        variables = symcon_import.scan_source(self.deps.symcon_import_dir)
        with self._scan_cache.lock:
            self._scan_cache.variables = variables
        self._save_scan_cache(variables)
        return variables



    def _run_scan_background(self) -> None:
        """Scannt self.deps.symcon_import_dir im Hintergrund und füllt den Cache — der
        "scanning"-Teil von self._run_upload_background(), auch einzeln nutzbar, wenn
        schon entpackte Daten vorliegen, aber (noch) kein Cache existiert."""
        with self._upload_progress.lock:
            self._upload_progress.running = True
            self._upload_progress.phase = "scanning"
            self._upload_progress.done = 0
            self._upload_progress.total = 0
            self._upload_progress.error = ""

        def on_scan_progress(done: int, total: int) -> None:
            with self._upload_progress.lock:
                self._upload_progress.done = done
                self._upload_progress.total = total

        def worker() -> None:
            try:
                with self._import_source_lock:
                    variables = symcon_import.scan_source(self.deps.symcon_import_dir, on_progress=on_scan_progress)
                    with self._scan_cache.lock:
                        self._scan_cache.variables = variables
                    self._save_scan_cache(variables)
                with self._upload_progress.lock:
                    self._upload_progress.phase = "done"
            except (OSError, ValueError) as exc:
                with self._upload_progress.lock:
                    self._upload_progress.phase = "error"
                    self._upload_progress.error = f"Quelldaten konnten nicht gescannt werden: {exc}"
            finally:
                with self._upload_progress.lock:
                    self._upload_progress.running = False

        threading.Thread(target=worker, daemon=True).start()



    def _run_upload_background(self, tmp_zip: Path, source_meta: dict) -> None:
        with self._upload_progress.lock:
            self._upload_progress.running = True
            self._upload_progress.phase = "extracting"
            self._upload_progress.done = 0
            self._upload_progress.total = 0
            self._upload_progress.error = ""

        def on_extract_progress(done: int, total: int) -> None:
            with self._upload_progress.lock:
                self._upload_progress.done = done
                self._upload_progress.total = total

        def on_scan_progress(done: int, total: int) -> None:
            with self._upload_progress.lock:
                self._upload_progress.done = done
                self._upload_progress.total = total

        def worker() -> None:
            try:
                with self._import_source_lock:
                    symcon_import.extract_zip(tmp_zip, self.deps.symcon_import_dir, on_progress=on_extract_progress)
                    with self._upload_progress.lock:
                        self._upload_progress.phase = "scanning"
                        self._upload_progress.done = 0
                        self._upload_progress.total = 0
                    variables = symcon_import.scan_source(self.deps.symcon_import_dir, on_progress=on_scan_progress)
                    with self._scan_cache.lock:
                        self._scan_cache.variables = variables
                    self._save_scan_cache(variables)
                    temporary_meta = self.deps.symcon_source_meta_path.with_suffix(".json.part")
                    try:
                        temporary_meta.write_text(
                            json.dumps(source_meta, ensure_ascii=False), encoding="utf-8"
                        )
                        temporary_meta.replace(self.deps.symcon_source_meta_path)
                    finally:
                        temporary_meta.unlink(missing_ok=True)
                with self._upload_progress.lock:
                    self._upload_progress.phase = "done"
            except (zipfile.BadZipFile, ValueError) as exc:
                with self._upload_progress.lock:
                    self._upload_progress.phase = "error"
                    self._upload_progress.error = f"ZIP konnte nicht verarbeitet werden: {exc}"
            finally:
                tmp_zip.unlink(missing_ok=True)
                with self._upload_progress.lock:
                    self._upload_progress.running = False

        threading.Thread(target=worker, daemon=True).start()

    def _ha_import_context(self,
        selected_ids: set[str] | None = None,
        range_preset: str = "max",
        date_from: str = "",
        date_to: str = "",
        history_source: str = "full",
        period: str = ha_statistics.DEFAULT_PERIOD,
        include_existing_months: bool = False,
        stats_range_preset: str = "max",
        include_long_term_stats: bool = True,
    ) -> dict:
        """Auswahlliste für den Home-Assistant-Import-Reiter — bewusst NICHT über
        die HA-API entdeckt (/api/states), sondern self.deps.index.list_entities(): nur
        Entitäten, die die HA-Integration bereits konfiguriert/gefiltert und
        mindestens einmal live nach Zeitarchiv übertragen hat, stehen zur Wahl.
        Eine zweite, unabhängige Entdeckung über die Core-API würde diese
        Filterung umgehen und Entitäten anbieten, die der Nutzer in der
        Integration bewusst ausgeschlossen hat. type_label/aggregation_type
        kommen aus demselben Formatter wie die Entitäten-Übersicht
        (_entities_table.html) — die Spalte "Typ" zeigt hier also Zeitarchivs
        eigene Aggregationsklasse (Standard/Zähler/Schalter), nicht HAs
        state_class. Die separate Spalte "Art"/"Verfügbar" (Rohhistorie vs.
        keine Daten, Zeitraum, Punktanzahl) wird bewusst NICHT hier eager für
        alle Zeilen mitgeladen, sondern erst durch den expliziten "Verfügbarkeit
        prüfen"-Button (siehe /import/ha/availability) — sonst würde jeder
        Aufruf dieser Seite automatisch N HA-Requests auslösen. Einmal geprüft,
        kommt das Ergebnis IMMER aus self._ha_availability_cache (siehe dort) —
        ein einzelner Aufrufer übergibt kein frisches Ergebnis mehr direkt,
        das erspart eine zweite Quelle der Wahrheit: /import/ha/availability
        schreibt in den Cache, jeder Aufruf hier (auch GET /import, auch der
        reine Quellen-/Perioden-Wechsel über /import/ha/source) liest daraus.
        ha_available=False (rein lokale Prüfung, kein Netzwerk-Roundtrip) erlaubt
        der Vorlage, vorab auf eine fehlende Supervisor-Umgebung hinzuweisen —
        die Liste selbst bleibt trotzdem sichtbar, da sie keine HA-Verbindung
        braucht."""
        now = datetime.now(timezone.utc)
        default_from = (now - timedelta(days=10)).strftime("%Y-%m-%d")
        default_to = now.strftime("%Y-%m-%d")
        ha_available = ha_import.token_available()
        range_options = (
            HA_RANGE_PRESETS_STATS if history_source == "stats"
            else HA_RANGE_PRESETS_FULL_RAW if history_source == "full"
            else HA_RANGE_PRESETS_RAW
        )
        if history_source == "full":
            period = HA_FULL_IMPORT_PERIOD
        cache_entry = self._ha_cache_lookup(
            history_source, period, range_preset,
            stats_range_preset, include_long_term_stats,
        )
        availability: dict[str, ha_import.EntityAvailability] = cache_entry["availability"] if cache_entry else {}
        availability_error = cache_entry["error"] if cache_entry else None
        checked_at = cache_entry["checked_at"] if cache_entry else None
        stale = checked_at is not None and (now.timestamp() - checked_at) > HA_AVAILABILITY_STALE_SECONDS
        entities = []
        for row in self.deps.index.list_entities():
            entity_id = row["entity_id"]
            avail = availability.get(entity_id)
            not_supported = avail is not None and not avail.supported
            full_details = (
                self._ha_full_availability_context(avail.details)
                if avail is not None and history_source == "full" else {}
            )
            # Die Verfügbar-Spalte zeigt Zeitraum und Anzahl als zwei
            # getrennte Zeilen (available_range/available_count) statt einem
            # zusammengesetzten Text — available_label bleibt der Ein-Zeilen-
            # Fallback für die Fälle ohne Zeitraum/Anzahl (noch nicht
            # geprüft, nicht unterstützt, keine Daten gefunden).
            available_range = available_count = available_label = None
            if avail is not None:
                if not_supported:
                    available_label = "Führt keine Langzeitstatistik"
                elif avail.has_data and history_source != "full":
                    available_range, available_count = self._ha_availability_range_and_count(avail, history_source)
                elif not avail.has_data:
                    if history_source == "full":
                        available_label = "Keine importierbaren Daten im gewählten Zeitraum"
                    else:
                        kind = "Statistik" if history_source == "stats" else "Rohhistorie"
                        available_label = f"Keine {kind} im gewählten Zeitraum"
            entities.append({
                "entity_id": entity_id,
                "friendly_name": row["friendly_name"] or entity_id,
                "unit": row["unit"],
                "aggregation_type": row["aggregation_type"],
                "type_label": format_type(row["aggregation_type"]),
                "has_data": avail.has_data if avail is not None else None,
                "supported": avail.supported if avail is not None else None,
                "available_label": available_label,
                "available_range": available_range,
                "available_count": available_count,
                # Für die client-seitige Sortierung der Verfügbar-Spalte: ein
                # roher Unix-Zeitstempel statt des formatierten Labels, damit
                # "12.08.2025" nicht lexikografisch vor "9.08.2025" sortiert.
                "available_first_ts": avail.first_ts if avail is not None else None,
                "full_details": full_details,
            })
        return {
            "ha_available": ha_available,
            "ha_error": None if ha_available else "Supervisor ist in dieser Umgebung nicht verfügbar",
            "ha_entities": entities,
            "ha_selected_ids": selected_ids or set(),
            "ha_availability_checked": cache_entry is not None,
            "ha_availability_error": availability_error,
            "ha_availability_checked_at_label": (
                f"{format_timestamp(checked_at, self.deps.tz)} {format_time(checked_at, self.deps.tz)}"
                if checked_at is not None else None
            ),
            "ha_availability_stale": stale,
            "ha_history_source": history_source if history_source in ("full", "raw", "stats") else "full",
            "ha_period": period if period in ha_statistics.PERIODS else ha_statistics.DEFAULT_PERIOD,
            "ha_period_options": list(ha_statistics.PERIODS.items()),
            "ha_range_options": list(range_options.items()),
            "ha_range_preset": range_preset if range_preset in range_options else "max",
            "ha_date_from": date_from or default_from,
            "ha_date_to": date_to or default_to,
            "ha_include_existing_months": include_existing_months,
            "ha_stats_range_options": list(HA_RANGE_PRESETS_FULL_STATS.items()),
            "ha_stats_range_preset": (
                stats_range_preset if stats_range_preset in HA_RANGE_PRESETS_FULL_STATS else "max"
            ),
            "ha_include_long_term_stats": include_long_term_stats,
        }



    def _run_import_background(self, 
        mapped: list[tuple[symcon_import.SymconVariable, str, float]]
    ) -> None:
        """Startet Planung und Schreibvorgang komplett im Hintergrund-Thread, damit
        /import/start sofort zurückkehrt — schon plan_import() liest für jede
        Variable alle Rohdaten neu ein und kann bei vielen zugeordneten Variablen
        spürbar dauern; würde das synchron vor der Antwort laufen, sähe der Import
        bei genug Variablen ohne jede Rückmeldung erst nach einer Weile "fertig" aus."""
        started_at = datetime.now(timezone.utc)
        logger.info("Symcon-Import gestartet · Variablen=%d", len(mapped))
        names = self._load_symcon_names()
        try:
            source_meta = json.loads(self.deps.symcon_source_meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            source_meta = {"filename": "Symcon-db-ZIP", "size_bytes": dir_size(self.deps.symcon_import_dir)}
        target_units = {}
        for _, target, _ in mapped:
            entity = self.deps.index.get_entity(target)
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
        with self._import_progress.lock:
            self._import_progress.started = True
            self._import_progress.running = True
            self._import_progress.phase = "planning"
            self._import_progress.total_variables = len(mapped)
            self._import_progress.planned_variables = 0
            self._import_progress.total_months = 0
            self._import_progress.done_months = 0
            self._import_progress.rows_imported = 0
            self._import_progress.current_variable = ""
            self._import_progress.results = []
            self._import_progress.errors = []

        def worker() -> dict | None:
            plans: list[tuple[symcon_import.SymconVariable, str, float]] = []
            total_months = 0
            for variable, target_entity_id, factor in mapped:
                with self._import_progress.lock:
                    self._import_progress.current_variable = variable.variable_id
                try:
                    plan = symcon_import.plan_import(
                        self.deps.data_dir, self.deps.index, variable, target_entity_id, self.deps.tz, factor=factor
                    )
                    total_months += len(plan.months_to_import) + len(plan.months_to_merge)
                except ValueError:
                    pass  # scheitert gleich nochmal in der Import-Phase, landet dann in errors
                plans.append((variable, target_entity_id, factor))
                with self._import_progress.lock:
                    self._import_progress.planned_variables += 1
                    self._import_progress.total_months = total_months

            with self._import_progress.lock:
                self._import_progress.phase = "importing"
                self._import_progress.current_variable = ""

            for variable, target_entity_id, factor in plans:
                with self._import_progress.lock:
                    self._import_progress.current_variable = variable.variable_id

                def on_month_done(label: str, row_count: int) -> None:
                    with self._import_progress.lock:
                        self._import_progress.done_months += 1
                        self._import_progress.rows_imported += row_count

                try:
                    result = symcon_import.import_variable(
                        self.deps.data_dir,
                        self.deps.index,
                        variable,
                        target_entity_id,
                        self.deps.tz,
                        on_month_done=on_month_done,
                        factor=factor,
                    )
                    with self._import_progress.lock:
                        self._import_progress.results.append(result)
                except ValueError:
                    logger.warning(
                        "Symcon-Import: Ziel-Entität nicht gefunden · Variable=%s · Ziel=%s",
                        variable.variable_id, target_entity_id,
                    )
                    with self._import_progress.lock:
                        self._import_progress.errors.append(
                            f"{variable.variable_id} → {target_entity_id}: Entität nicht gefunden"
                        )
            return None

        def coordinated_import_worker() -> None:
            reconciliation_report = None
            try:
                with self._import_source_lock:
                    with self.deps.coordinator.exclusive():
                        worker()
                        reconciliation_report = self.deps.run_storage_reconciliation(
                            entity_ids=sorted({target for _, target, _ in mapped}), repair=True
                        )
            except Exception as exc:
                logger.exception("Symcon-Import unerwartet fehlgeschlagen")
                with self._import_progress.lock:
                    self._import_progress.errors.append(f"Import abgebrochen: {exc}")
            finally:
                with self._import_progress.lock:
                    results = [dataclasses.asdict(result) for result in self._import_progress.results]
                    errors = list(self._import_progress.errors)
                logger.info(
                    "Symcon-Import abgeschlossen · Variablen=%d · Zeilen importiert=%d · "
                    "Zeilen zusammengeführt=%d · Fehler=%d",
                    len(results),
                    sum(r["rows_imported"] for r in results),
                    sum(r["rows_merged"] for r in results),
                    len(errors),
                )
                try:
                    with self.deps.coordinator.exclusive():
                        import_reports.create(
                            self.deps.data_dir,
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
                    with self._import_progress.lock:
                        self._import_progress.running = False

        threading.Thread(target=coordinated_import_worker, daemon=True).start()



    def _import_progress_context(self) -> dict:
        with self._import_progress.lock:
            phase = self._import_progress.phase
            total_vars = self._import_progress.total_variables
            planned_vars = self._import_progress.planned_variables
            total_months = self._import_progress.total_months
            done_months = self._import_progress.done_months
            if phase == "planning":
                percent = int(planned_vars / total_vars * 100) if total_vars else 0
            else:
                percent = int(done_months / total_months * 100) if total_months else 100
            return {
                "running": self._import_progress.running,
                "phase": phase,
                "total_variables": total_vars,
                "planned_variables": planned_vars,
                "total_months": total_months,
                "done_months": done_months,
                "percent": percent,
                "rows_imported": format_int(self._import_progress.rows_imported),
                "current_variable": self._import_progress.current_variable,
                "results": list(self._import_progress.results),
                "errors": list(self._import_progress.errors),
            }



    def _mapped_variables(self, form) -> list[tuple[symcon_import.SymconVariable, str, float]]:
        """Liest die map_<id>-Felder aus dem Formular und löst sie gegen die aktuell
        gescannten Variablen auf — "" und "__ignore__" heißen beide "überspringen".
        Nutzt den Scan-Cache (siehe _import_page_context) statt bei jedem Dry-Run-/
        Import-Klick neu zu scannen."""
        variables = {v.variable_id: v for v in self._cached_variables()}
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



    def _csv_uploaded_path(self) -> Path | None:
        if not self.deps.csv_import_dir.exists():
            return None
        files = sorted(p for p in self.deps.csv_import_dir.iterdir() if p.is_file())
        return files[0] if files else None



    def _csv_import_context(self, 
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
            for row in self.deps.index.list_entities()
        ]
        base = {
            "entity_options": entity_options,
            "delimiter_options": list(csv_import.DELIMITERS.items()),
            "ts_format_options": list(csv_import.TIMESTAMP_FORMATS.items()),
        }
        path = self._csv_uploaded_path()
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



    def _csv_form_params(self, form) -> tuple[str, bool, int, int, str, str, str]:
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



    def _ha_form_params(self, form) -> tuple[list[str], str, str, str, bool, str, bool]:
        # range_preset wird bewusst NICHT hier gegen HA_RANGE_PRESETS_RAW/
        # _STATS validiert — welches der beiden Presets-Sets gilt, hängt von
        # history_source ab (siehe _ha_source_params()), das an dieser Stelle
        # noch nicht ausgewertet ist. Die endgültige Validierung übernimmt
        # _ha_import_context() (fällt bei einem für die aktuelle Quelle
        # ungültigen Wert auf "max" zurück).
        entity_ids = [str(v).strip() for v in form.getlist("entity_ids") if str(v).strip()]
        range_preset = str(form.get("range_preset") or "max")
        date_from = str(form.get("date_from") or "")
        date_to = str(form.get("date_to") or "")
        include_existing_months = form.get("include_existing_months") == "on"
        stats_range_preset = str(form.get("stats_range_preset") or "max")
        include_long_term_stats = form.get("include_long_term_stats") == "on"
        return (
            entity_ids, range_preset, date_from, date_to,
            include_existing_months, stats_range_preset, include_long_term_stats,
        )



    def _ha_source_params(self, form) -> tuple[str, str]:
        history_source = str(form.get("history_source") or "full")
        if history_source not in ("full", "raw", "stats"):
            history_source = "full"
        period = str(form.get("period") or ha_statistics.DEFAULT_PERIOD)
        if period not in ha_statistics.PERIODS:
            period = ha_statistics.DEFAULT_PERIOD
        if history_source == "full":
            period = HA_FULL_IMPORT_PERIOD
        return history_source, period



    def _known_ha_entity_ids(self, entity_ids: list[str]) -> tuple[list[str], list[str]]:
        """Nur Entitäten, die bereits in Zeitarchiv bekannt sind (siehe Modul-
        Docstring oben) — trennt bewusst zwischen bekannt/unbekannt, statt eine
        unbekannte ID erst beim Planen/Schreiben generisch scheitern zu lassen."""
        known, unknown = [], []
        for entity_id in entity_ids:
            (known if self.deps.index.get_entity(entity_id) is not None else unknown).append(entity_id)
        return known, unknown



    def _ha_date_range(
        self, range_preset: str, date_from: str, date_to: str, history_source: str = "raw"
    ) -> tuple[datetime, datetime]:
        """Löst die gewählte Zeitraum-Voreinstellung (HA_RANGE_PRESETS_RAW/
        _STATS je nach history_source) in ein UTC-Zeitfenster auf. Nur
        "custom" liest date_from/date_to aus dem Formular — date_to ist dabei
        inklusiv gemeint, deshalb +1 Tag als Fensterende; nie über "jetzt"
        hinaus."""
        now = datetime.now(timezone.utc)
        max_days = HA_MAX_RANGE_DAYS_STATS if history_source == "stats" else HA_MAX_RANGE_DAYS_RAW
        preset_days = HA_RANGE_PRESET_DAYS_STATS if history_source == "stats" else HA_RANGE_PRESET_DAYS_RAW
        if range_preset == "max":
            return now - timedelta(days=max_days), now
        if range_preset in preset_days:
            return now - timedelta(days=preset_days[range_preset]), now
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            start = now - timedelta(days=10)
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            end = now
        end = min(end, now)
        if end <= start:
            end = start + timedelta(days=1)
        return start, end


    def _ha_request_ranges(
        self,
        range_preset: str,
        date_from: str,
        date_to: str,
        history_source: str,
        stats_range_preset: str = "max",
    ) -> tuple[datetime, datetime, datetime]:
        """Liefert Roh-/Statistikbeginn und gemeinsames Ende eines Abrufs."""
        if history_source == "full":
            raw_start, end = self._ha_date_range(
                range_preset, date_from, date_to, "raw"
            )
            valid_stats_preset = (
                stats_range_preset if stats_range_preset in HA_RANGE_PRESETS_FULL_STATS else "max"
            )
            stats_start, _stats_end = self._ha_date_range(
                valid_stats_preset, "", "", "stats"
            )
            return raw_start, stats_start, end
        start, end = self._ha_date_range(
            range_preset, date_from, date_to, history_source
        )
        return start, start, end



    def _ha_available_label(self, history: ha_import.HistoryFetchResult, history_source: str = "raw") -> str:
        """Beschreibt, was tatsächlich in HA gefunden wurde — rows ist nach
        fetch_history_rows()/fetch_statistics_rows() aufsteigend sortiert, der
        erste/letzte Eintrag ist also der älteste/neueste tatsächlich
        abgerufene Messpunkt (nicht der angefragte Zeitraum, der ja über das
        hinausgehen kann, was HA noch vorhält)."""
        if history_source == "full":
            details = history.source_details
            raw = details.get("raw", {})
            stats = details.get("stats", {})
            return (
                f"{format_int(int(stats.get('used_count', 0)))} Statistik-Werte · "
                f"{format_int(int(raw.get('used_count', 0)))} Rohwerte"
            )
        noun = "Statistik-Werte" if history_source == "stats" else "Werte"
        if not history.rows:
            return f"Keine {'Langzeitstatistik' if history_source == 'stats' else 'Rohhistorie'} im gewählten Zeitraum gefunden"
        first_ts, last_ts = history.rows[0][0], history.rows[-1][0]
        return (
            f"{format_timestamp(first_ts, self.deps.tz)} – {format_timestamp(last_ts, self.deps.tz)} · "
            f"{format_int(len(history.rows))} {noun}"
        )


    def _ha_full_summary(self, history: ha_import.HistoryFetchResult) -> dict:
        """Für Dry Run/Ergebnis vorformatierte Details des kombinierten Abrufs."""
        details = history.source_details
        raw = details.get("raw", {})
        stats = details.get("stats", {})

        def range_label(part: dict, noun: str) -> str:
            first_ts = part.get("used_first_ts")
            last_ts = part.get("used_last_ts")
            count = int(part.get("used_count", 0))
            if first_ts is None or last_ts is None:
                return f"Keine {noun} verwendet"
            return (
                f"{format_timestamp(float(first_ts), self.deps.tz)} – "
                f"{format_timestamp(float(last_ts), self.deps.tz)} · "
                f"{format_int(count)} {noun}"
            )

        cutover_ts = details.get("cutover_ts")
        stats_enabled = bool(stats.get("enabled", False))
        stats_supported = stats.get("supported")
        stats_label = range_label(stats, "Statistik-Werte")
        if stats_enabled and stats_supported is False:
            stats_label = "Für diese Entität nicht unterstützt"
        elif not stats_enabled:
            stats_label = "Nicht angefordert"
        return {
            "stats_label": stats_label,
            "raw_label": range_label(raw, "Rohwerte"),
            "cutover_label": (
                f"{format_timestamp(float(cutover_ts), self.deps.tz)} "
                f"{format_time(float(cutover_ts), self.deps.tz)}"
                if cutover_ts is not None else None
            ),
            "raw_discarded_at_seam": int(raw.get("discarded_at_seam", 0)),
            "stats_discarded_at_seam": int(stats.get("discarded_at_seam", 0)),
            "boundary_anchor": raw.get("boundary_anchor"),
            "stats_enabled": stats_enabled,
            "stats_supported": stats_supported,
            "seam_status": details.get("seam_status"),
        }



    def _ha_availability_range_and_count(
        self, avail: ha_import.EntityAvailability, history_source: str = "raw"
    ) -> tuple[str, str]:
        """Kurzform für die Auswahltabelle — Pendant zu _ha_available_label()
        oben, nur auf Basis der gebündelten Verfügbarkeitsprüfung
        (EntityAvailability: first_ts/last_ts/count) statt bereits
        eingelesener Zeilen. Liefert Zeitraum und Anzahl getrennt (statt als
        ein zusammengesetzter String), weil die Tabelle sie auf zwei Zeilen
        darstellt — nur für avail.has_data == True aufgerufen, den Fall
        "keine Daten"/"nicht unterstützt" behandelt der Aufrufer
        (_ha_import_context()) bereits vorher separat."""
        noun = "Statistik-Werte" if history_source == "stats" else "Werte"
        range_label = f"{format_timestamp(avail.first_ts, self.deps.tz)} – {format_timestamp(avail.last_ts, self.deps.tz)}"
        count_label = f"{format_int(avail.count)} {noun}"
        return range_label, count_label


    def _ha_full_availability_context(self, details: dict) -> dict:
        """Formatiert die getrennten Quellbereiche für die Vollimport-Tabelle."""
        def source(raw: dict, noun: str) -> dict:
            count = int(raw.get("count", 0))
            first_ts = raw.get("first_ts")
            last_ts = raw.get("last_ts")
            supported = bool(raw.get("supported", True))
            if not supported:
                label = "Nicht unterstützt"
            elif count and first_ts is not None and last_ts is not None:
                label = (
                    f"{format_timestamp(float(first_ts), self.deps.tz)} – "
                    f"{format_timestamp(float(last_ts), self.deps.tz)} · "
                    f"{format_int(count)} {noun}"
                )
            else:
                label = "Keine Daten gefunden"
            return {"count": count, "supported": supported, "label": label}

        return {
            # Die Zeilen sind im Template bereits mit "Roh:" bzw.
            # "Statistik:" beschriftet. Dort genügt deshalb das neutrale
            # "Werte"; "Roh: … Rohwerte" war unnötig doppelt und benötigte
            # auf schmaleren Ansichten eine zusätzliche Zeile.
            "raw": source(details.get("raw", {}), "Werte"),
            "stats": source(details.get("stats", {}), "Werte"),
            "stats_enabled": bool(details.get("stats_enabled", False)),
        }


    def _fetch_ha_history(self,
        entity_ids: list[str], start: datetime, end: datetime,
        history_source: str = "raw", period: str = ha_statistics.DEFAULT_PERIOD,
    ) -> tuple[dict[str, ha_import.HistoryFetchResult], list[str]]:
        """Netzwerkteil komplett außerhalb jeder Datei-/Indexsperre (Konzept
        "Offene Punkte" zu HA-Import: Sperren dürfen nicht unter einem
        HTTP-/WS-Roundtrip zur HA-Instanz stehen) — wird per run_in_threadpool
        aufgerufen, danach folgt die eigentliche Planung/Schreibphase gesperrt.
        Domain kommt aus der Entitäts-ID selbst (Konvention wie überall sonst im
        Addon, z. B. ingestion.py) — kein zusätzlicher /api/states-Aufruf nötig,
        da nur bereits bekannte Zeitarchiv-Entitäten hier ankommen.
        history_source == "stats" holt stattdessen Langzeitstatistik über
        ha_statistics.py (WebSocket-API) statt Rohhistorie über ha_import.py
        (REST-API) — dieselbe Fehlerbehandlung/Logging-Struktur für beide."""
        label = "Langzeitstatistik" if history_source == "stats" else "HA-Historie"
        fetched: dict[str, ha_import.HistoryFetchResult] = {}
        errors: list[str] = []
        for entity_id in entity_ids:
            try:
                if history_source == "stats":
                    history = ha_statistics.fetch_statistics_rows(entity_id, start, end, period)
                else:
                    domain = entity_id.split(".", 1)[0]
                    history = ha_import.fetch_history_rows(entity_id, domain, start, end)
            except (ha_import.HaApiError, ValueError) as exc:
                # WARNING statt nur in errors sammeln: das ist bisher die
                # einzige Stelle, an der ein Fehlschlag beim HA-Abruf
                # überhaupt im Container-Log auftaucht — ohne sie war ein
                # solcher Fehlschlag nur über die UI sichtbar, nie über
                # `docker logs`/das Add-on-Protokoll nachvollziehbar.
                logger.warning("%s für %s nicht abrufbar · %s", label, entity_id, exc)
                errors.append(f"{entity_id}: {exc}")
                continue
            fetched[entity_id] = history
            # Ein nennenswerter Anteil übersprungener Punkte (nicht
            # numerisch/kein bekannter Schalter-Zustand bzw. kein mean/sum im
            # Statistik-Fenster) ist kein Fehler, aber ein Symptom für eine
            # Domain-/Datenqualitäts-Inkonsistenz — z. B. eine Entität, deren
            # Zustände HA zwischenzeitlich als Text statt Zahl liefert. Ohne
            # diese WARNING wäre "warum kommen nur halb so viele Punkte an
            # wie erwartet" nur über den Importreport nachvollziehbar, nie im
            # laufenden Log sichtbar.
            if history.skipped > 0 and history.skipped >= len(history.rows):
                logger.warning(
                    "%s für %s: %d von %d Punkten übersprungen (nicht numerisch/kein bekannter Zustand)",
                    label, entity_id, history.skipped, history.skipped + len(history.rows),
                )
        return fetched, errors


    @staticmethod
    def _ha_full_cutover(raw_first_ts: float, period: str = HA_FULL_IMPORT_PERIOD) -> float:
        """Nächste vollständige Statistikgrenze in UTC (Vollimport: Stunde)."""
        seconds = 86_400 if period == "day" else 3_600
        return math.ceil(raw_first_ts / seconds) * seconds


    def _combine_ha_full_history(
        self,
        raw: ha_import.HistoryFetchResult,
        stats: ha_import.HistoryFetchResult | None,
        raw_start: datetime,
        stats_start: datetime,
        end: datetime,
        period: str = HA_FULL_IMPORT_PERIOD,
        stats_supported: bool | None = None,
        stats_requested: bool | None = None,
    ) -> ha_import.HistoryFetchResult:
        """Vereinigt beide HA-Quellen mit einer echten halb-offenen Grenze.

        Statistik-Buckets werden als Intervalle behandelt, nicht nur als Punkte:
        Ein Stundenwert mit Zeitstempel 10:00 belegt [10:00, 11:00). Nur wenn
        der Bucket direkt vor der nächsten vollen Grenze vorhanden ist, werden
        Rohwerte vor dieser Grenze verworfen. Fehlt er, bleiben alle Rohwerte
        erhalten und nur vollständig davor endende Statistik-Buckets werden
        verwendet. Dadurch erzeugt Zeitarchiv selbst weder Überlappung noch eine
        künstliche Lücke.
        """
        stats_enabled = stats is not None if stats_requested is None else stats_requested
        stats = stats or ha_import.HistoryFetchResult()
        bucket_seconds = 86_400 if period == "day" else 3_600
        raw_rows = sorted(raw.rows)
        stats_rows = sorted(stats.rows)
        cutover_ts: float | None = None
        seam_status = "nur_rohhistorie"
        boundary_anchor: tuple[float, float] | None = None

        if raw_rows and stats_rows:
            candidate = self._ha_full_cutover(raw_rows[0][0], period)
            covering_bucket = any(
                ts < candidate and ts + bucket_seconds >= candidate
                for ts, _value in stats_rows
            )
            cutover_ts = candidate if covering_bucket else raw_rows[0][0]
            used_stats = [row for row in stats_rows if row[0] + bucket_seconds <= cutover_ts]
            used_raw = [row for row in raw_rows if row[0] >= cutover_ts]
            if covering_bucket and not any(ts == cutover_ts for ts, _value in used_raw):
                previous_raw = [row for row in raw_rows if row[0] < cutover_ts]
                if previous_raw:
                    # HA-Zustände gelten bis zur nächsten Änderung. Genau wie
                    # der Recorder bei einer period-Abfrage einen Zustand am
                    # Fensterbeginn synthetisiert, setzen wir den letzten
                    # bekannten Rohzustand auf die halb-offene Schnittstelle.
                    # So endet der Statistik-Bucket dort und der Rohbereich
                    # beginnt am selben Zeitpunkt, ohne Messintervalle zu
                    # überlagern oder einen leeren Abschnitt zu erzeugen.
                    boundary_anchor = (cutover_ts, previous_raw[-1][1])
                    used_raw.insert(0, boundary_anchor)
            seam_status = "nahtlos" if covering_bucket else "quellenluecke_nicht_vergroessert"
        elif stats_rows:
            used_stats = stats_rows
            used_raw = []
            seam_status = "nur_langzeitstatistik"
        else:
            used_stats = []
            used_raw = raw_rows

        combined_rows = sorted([*used_stats, *used_raw])
        if len(combined_rows) > MAX_IMPORT_ROWS_PER_ENTITY:
            raise ValueError(
                f"HA-Vollimport enthält mehr als {MAX_IMPORT_ROWS_PER_ENTITY:,} Datenpunkte".replace(",", ".")
            )

        discarded = [
            {**entry, "source": "raw"} for entry in raw.discarded
        ] + [
            {**entry, "source": "stats"} for entry in stats.discarded
        ]
        used_raw_set = set(used_raw)
        used_stats_set = set(used_stats)
        discarded.extend({
            "reason": "Rohwert liegt im vollständig durch Statistik abgedeckten Übergangsintervall",
            "source": "raw",
            "timestamp": ts,
            "value": value,
            "cutover_timestamp": cutover_ts,
        } for ts, value in raw_rows if (ts, value) not in used_raw_set)
        discarded.extend({
            "reason": "Statistik-Bucket überschreitet die Schnittstelle zur Rohhistorie",
            "source": "stats",
            "timestamp": ts,
            "value": value,
            "cutover_timestamp": cutover_ts,
        } for ts, value in stats_rows if (ts, value) not in used_stats_set)

        def bounds(rows: list[tuple[float, float]]) -> tuple[float | None, float | None]:
            return (rows[0][0], rows[-1][0]) if rows else (None, None)

        raw_first, raw_last = bounds(used_raw)
        stats_first, stats_last = bounds(used_stats)
        return ha_import.HistoryFetchResult(
            rows=combined_rows,
            skipped=raw.skipped + stats.skipped,
            discarded=discarded,
            source_details={
                "mode": "full",
                "period": period,
                "cutover_ts": cutover_ts,
                "seam_status": seam_status,
                "raw": {
                    "requested_start_utc": raw_start.isoformat(),
                    "requested_end_utc": end.isoformat(),
                    "fetched_count": len(raw_rows),
                    "used_count": len(used_raw),
                    "used_first_ts": raw_first,
                    "used_last_ts": raw_last,
                    "discarded_at_seam": sum(1 for row in raw_rows if row not in used_raw_set),
                    "boundary_anchor": (
                        {"timestamp": boundary_anchor[0], "value": boundary_anchor[1]}
                        if boundary_anchor else None
                    ),
                },
                "stats": {
                    "enabled": stats_enabled,
                    "supported": stats_supported,
                    "requested_start_utc": stats_start.isoformat(),
                    "requested_end_utc": end.isoformat(),
                    "fetched_count": len(stats_rows),
                    "used_count": len(used_stats),
                    "used_first_ts": stats_first,
                    "used_last_ts": stats_last,
                    "discarded_at_seam": len(stats_rows) - len(used_stats),
                },
            },
        )


    def _fetch_ha_full_history(
        self,
        entity_ids: list[str],
        raw_start: datetime,
        stats_start: datetime,
        end: datetime,
        include_long_term_stats: bool = True,
    ) -> tuple[dict[str, ha_import.HistoryFetchResult], list[str]]:
        """Rohhistorie zuerst, anschließend optional Stundenstatistik."""
        fetched: dict[str, ha_import.HistoryFetchResult] = {}
        errors: list[str] = []
        raw_by_entity: dict[str, ha_import.HistoryFetchResult | None] = {}

        # Wirklich zuerst alle Rohbereiche bestimmen: Erst deren frühester
        # verfügbarer Wert entscheidet, wo die Statistik später endet.
        for entity_id in entity_ids:
            try:
                raw_by_entity[entity_id] = ha_import.fetch_history_rows(
                    entity_id, entity_id.split(".", 1)[0], raw_start, end
                )
            except (ha_import.HaApiError, ValueError) as exc:
                raw_by_entity[entity_id] = None
                errors.append(f"{entity_id} · Rohhistorie: {exc}")
                logger.warning("HA-Vollimport: Rohhistorie für %s nicht abrufbar · %s", entity_id, exc)

        statistic_meta: dict[str, ha_statistics.StatisticMeta] = {}
        if include_long_term_stats:
            try:
                statistic_meta = ha_statistics.fetch_statistic_meta(entity_ids)
            except ha_import.HaApiError as exc:
                errors.append(f"Langzeitstatistik-Metadaten: {exc}")
                logger.warning("HA-Vollimport: Statistik-Metadaten nicht abrufbar · %s", exc)

        for entity_id in entity_ids:
            raw = raw_by_entity[entity_id]
            stats: ha_import.HistoryFetchResult | None = None
            meta = statistic_meta.get(entity_id)
            stats_supported = meta.supported if meta is not None else False
            if include_long_term_stats and stats_supported:
                try:
                    stats_end = end
                    if raw is not None and raw.rows:
                        stats_end = min(
                            end,
                            datetime.fromtimestamp(
                                self._ha_full_cutover(raw.rows[0][0]), timezone.utc
                            ),
                        )
                    stats = ha_statistics.fetch_statistics_rows(
                        entity_id, stats_start, stats_end, HA_FULL_IMPORT_PERIOD
                    )
                except (ha_import.HaApiError, ValueError) as exc:
                    errors.append(f"{entity_id} · Langzeitstatistik: {exc}")
                    logger.warning("HA-Vollimport: Statistik für %s nicht abrufbar · %s", entity_id, exc)

            if raw is None and stats is None:
                continue
            try:
                fetched[entity_id] = self._combine_ha_full_history(
                    raw or ha_import.HistoryFetchResult(),
                    stats,
                    raw_start,
                    stats_start,
                    end,
                    HA_FULL_IMPORT_PERIOD,
                    stats_supported=stats_supported if include_long_term_stats else None,
                    stats_requested=include_long_term_stats,
                )
            except ValueError as exc:
                errors.append(f"{entity_id} · Vollimport: {exc}")
        return fetched, errors


    def _fetch_ha_full_availability(
        self,
        entity_ids: list[str],
        raw_start: datetime,
        stats_start: datetime,
        end: datetime,
        include_long_term_stats: bool = True,
    ) -> tuple[dict[str, ha_import.EntityAvailability], str | None]:
        """Gebündelte, getrennt ausgewiesene Vorschau beider Vollimportquellen."""
        errors: list[str] = []
        try:
            domains = {entity_id: entity_id.split(".", 1)[0] for entity_id in entity_ids}
            raw = ha_import.fetch_availability(domains, raw_start, end)
        except ha_import.HaApiError as exc:
            raw = {entity_id: ha_import.EntityAvailability(entity_id) for entity_id in entity_ids}
            errors.append(f"Rohhistorie: {exc}")
        if include_long_term_stats:
            try:
                stats = ha_statistics.fetch_statistics_availability(
                    entity_ids, stats_start, end, HA_FULL_IMPORT_PERIOD
                )
            except ha_import.HaApiError as exc:
                stats = {entity_id: ha_import.EntityAvailability(entity_id) for entity_id in entity_ids}
                errors.append(f"Langzeitstatistik: {exc}")
        else:
            stats = {entity_id: ha_import.EntityAvailability(entity_id, supported=False) for entity_id in entity_ids}

        result: dict[str, ha_import.EntityAvailability] = {}
        for entity_id in entity_ids:
            raw_avail = raw.get(entity_id, ha_import.EntityAvailability(entity_id))
            stats_avail = stats.get(entity_id, ha_import.EntityAvailability(entity_id))
            timestamps = [
                ts for ts in (raw_avail.first_ts, stats_avail.first_ts) if ts is not None
            ]
            last_timestamps = [
                ts for ts in (raw_avail.last_ts, stats_avail.last_ts) if ts is not None
            ]
            result[entity_id] = ha_import.EntityAvailability(
                entity_id=entity_id,
                first_ts=min(timestamps) if timestamps else None,
                last_ts=max(last_timestamps) if last_timestamps else None,
                count=raw_avail.count + stats_avail.count,
                supported=True,
                details={
                    "raw": dataclasses.asdict(raw_avail),
                    "stats": dataclasses.asdict(stats_avail),
                    "stats_enabled": include_long_term_stats,
                },
            )
        return result, " · ".join(errors) if errors else None


    def _fetch_ha_availability(
        self, entity_ids: list[str], start: datetime, end: datetime,
        history_source: str = "raw", period: str = ha_statistics.DEFAULT_PERIOD,
    ) -> tuple[dict[str, ha_import.EntityAvailability], str | None]:
        """Wie _fetch_ha_history(), aber für die gebündelte Verfügbarkeits-
        Vorschau (EIN bis wenige Requests für ALLE übergebenen Entitäten
        zusammen) statt Rohdaten für den tatsächlichen Import. Ein einzelner
        HaApiError betrifft hier immer den gesamten Batch (nicht pro Entität
        wie bei _fetch_ha_history), da mehrere Entitäten denselben Request
        teilen."""
        label = "HA-Statistik-Verfügbarkeitsprüfung" if history_source == "stats" else "HA-Verfügbarkeitsprüfung"
        try:
            if history_source == "stats":
                return ha_statistics.fetch_statistics_availability(entity_ids, start, end, period), None
            domains = {entity_id: entity_id.split(".", 1)[0] for entity_id in entity_ids}
            return ha_import.fetch_availability(domains, start, end), None
        except ha_import.HaApiError as exc:
            logger.warning("%s fehlgeschlagen · Entitäten=%d · %s", label, len(entity_ids), exc)
            return {}, str(exc)


    def _ha_debug_row(self, ts: float, value: float) -> dict:
        return {
            "timestamp": ts,
            "utc": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            "local": datetime.fromtimestamp(ts, self.deps.tz).isoformat(),
            "value": value,
        }


    def _ha_debug_entity(
        self,
        entity_id: str,
        history: ha_import.HistoryFetchResult,
        include_existing_months: bool,
    ) -> dict:
        """Vollständiger, secrets-freier Diagnosezustand einer Entität.

        Enthält sämtliche vom HA-Abruf normalisierten Werte, alle verworfenen
        Minimalfelder samt Grund sowie die tatsächlich gespeicherten Rohwerte
        der betroffenen Hot-/Archivmonate. Auth-Header, Supervisor-Token und
        HA-Attribute werden an keiner Stelle übernommen.
        """
        entity = self.deps.index.get_entity(entity_id)
        if entity is None:
            return {"entity_id": entity_id, "error": "Entität ist nicht mehr in Zeitarchiv bekannt"}
        plan = symcon_import.plan_import_rows(
            self.deps.data_dir,
            self.deps.index,
            history.rows,
            entity_id,
            self.deps.tz,
            source_label=entity_id,
            skipped_rows=history.skipped,
            include_existing_months=include_existing_months,
        )
        by_month = symcon_import._group_by_month(history.rows, self.deps.tz)
        current_label = datetime.now(self.deps.tz).strftime("%Y-%m")
        months = []
        for (year, month), source_rows in sorted(by_month.items()):
            label = f"{year:04d}-{month:02d}"
            archive_path = entity_dir(self.deps.data_dir, "archive", entity_id) / f"{label}.parquet"
            hot_path = hotbuffer.hot_path(
                self.deps.data_dir, entity_id, source_rows[0][0], self.deps.tz
            )
            archive_rows: list[dict] = []
            archive_error = None
            if archive_path.exists():
                try:
                    table = pq.read_table(archive_path)
                    event_ids = (
                        table.column("event_id").to_pylist()
                        if "event_id" in table.column_names
                        else [None] * table.num_rows
                    )
                    archive_rows = [
                        {**self._ha_debug_row(ts, value), "event_id": event_id}
                        for ts, value, event_id in zip(
                            table.column("ts").to_pylist(),
                            table.column("value").to_pylist(),
                            event_ids,
                        )
                    ]
                except Exception as exc:
                    archive_error = f"{type(exc).__name__}: {exc}"
            hot_rows = [
                {
                    **self._ha_debug_row(ts, value),
                    "event_id": event_id,
                }
                for ts, value, event_id in hotbuffer.read_records(hot_path)
            ]
            month_start = datetime(year, month, 1, tzinfo=self.deps.tz)
            next_month = (
                month_start.replace(year=year + 1, month=1)
                if month == 12
                else month_start.replace(month=month + 1)
            )
            deleted_counts = self.deps.index.get_deleted_counts(
                entity_id, month_start.timestamp(), next_month.timestamp()
            )
            if label in plan.months_to_merge:
                action = "hot_buffer_ergaenzen"
            elif label in plan.months_to_update:
                action = "archiv_ergaenzen"
            elif label in plan.months_to_import:
                action = "archiv_neu_anlegen"
            else:
                action = "ueberspringen"
            months.append({
                "month": label,
                "is_current_month": label == current_label,
                "planned_action": action,
                "source_rows": [self._ha_debug_row(ts, value) for ts, value in source_rows],
                "archive": {
                    "exists": archive_path.exists(),
                    "relative_path": str(archive_path.relative_to(self.deps.data_dir)),
                    "error": archive_error,
                    "rows": archive_rows,
                },
                "hot_buffer": {
                    "exists": hot_path.exists(),
                    "relative_path": str(hot_path.relative_to(self.deps.data_dir)),
                    "rows": hot_rows,
                },
                "soft_deleted_timestamps": [
                    {"timestamp": ts, "occurrences": count}
                    for ts, count in sorted(deleted_counts.items())
                ],
            })
        return {
            "entity_id": entity_id,
            "entity_metadata": dict(entity),
            "fetch": {
                "accepted_count": len(history.rows),
                "skipped_count": history.skipped,
                "discarded": history.discarded,
                "source_details": history.source_details,
            },
            "plan": dataclasses.asdict(plan),
            "months": months,
        }


    def _ha_debug_zip(self, payload: dict) -> tuple[Path, str]:
        timestamp = datetime.now(self.deps.tz).strftime("%Y%m%d-%H%M%S")
        filename = f"zeitarchiv-ha-import-debug-{timestamp}.zip"
        temporary = tempfile.NamedTemporaryFile(
            prefix="zeitarchiv-ha-debug-", suffix=".zip", delete=False
        )
        path = Path(temporary.name)
        temporary.close()
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                with archive.open("debug.json", "w") as binary:
                    with io.TextIOWrapper(binary, encoding="utf-8") as text_stream:
                        json.dump(payload, text_stream, ensure_ascii=False, indent=2, default=str)
            return path, filename
        except Exception:
            path.unlink(missing_ok=True)
            raise




    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/import", response_class=HTMLResponse)
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
            self.deps.symcon_import_dir und bleibt dort liegen, bis er explizit über /import/delete
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
            active_tab = tab if tab in {"symcon", "csv", "ha", "reports"} else "symcon"
            common_context = {
                "active_import_tab": active_tab,
                **self.deps.reports_context(source, status, search, date_from, date_to, sort, dir, page, page_size),
                # Wie _csv_import_context() bei jedem /import-Aufruf geladen — die
                # Auswahlliste kommt aus index.list_entities() (reiner DB-Read),
                # keine HA-API-Anfrage nötig (siehe _ha_import_context()).
                **self._ha_import_context(),
            }
            source_exists = self.deps.symcon_import_dir.exists() and any(self.deps.symcon_import_dir.iterdir())
            if source_exists:
                with self._scan_cache.lock:
                    cached = self._scan_cache.variables is not None
                if not cached:
                    from_disk = self._load_scan_cache()
                    if from_disk is not None:
                        with self._scan_cache.lock:
                            self._scan_cache.variables = from_disk
                        cached = True
                if not cached:
                    with self._upload_progress.lock:
                        already_running = self._upload_progress.running
                    if not already_running:
                        self._run_scan_background()
                    return self.deps.templates.TemplateResponse(
                        request,
                        "import.html",
                        {
                            "scanning": True,
                            "source_exists": True,
                            "rows": [],
                            "entity_options": [],
                            "settings_imported": bool(self._load_symcon_names()),
                            **self._csv_import_context(),
                            **common_context,
                        },
                    )
            return self.deps.templates.TemplateResponse(
                request,
                "import.html",
                {**self._import_page_context(), **self._csv_import_context(), **common_context},
            )



        @router.post("/import/upload")
        async def import_upload(file: UploadFile = File(...)) -> dict:
            """Nimmt das hochgeladene ZIP entgegen (der Byte-Transfer selbst zeigt über
            XHR-Upload-Events schon Fortschritt in import.html) und startet Entpacken +
            Scannen im Hintergrund — /import/upload-progress liefert von dort an den
            Fortschritt, damit ein ZIP mit tausenden Dateien nicht wie ein Hänger
            aussieht, während der Server noch beschäftigt ist."""
            if not file.filename or not file.filename.lower().endswith(".zip"):
                raise HTTPException(status_code=400, detail="Bitte eine ZIP-Datei hochladen")
            with self._import_admission_lock:
                with self._upload_progress.lock:
                    upload_running = self._upload_progress.running
                with self._import_progress.lock:
                    import_running = self._import_progress.running
                if upload_running or import_running:
                    raise HTTPException(status_code=409, detail="Ein Upload, Scan oder Import läuft bereits")
                with self._upload_progress.lock:
                    self._upload_progress.running = True
                    self._upload_progress.phase = "receiving"
            tmp_zip = self.deps.data_dir / "_symcon_upload.zip"
            try:
                await run_in_threadpool(
                    copy_upload_limited, file.file, tmp_zip, MAX_ZIP_UPLOAD_BYTES
                )
            except UploadLimitExceeded as exc:
                with self._upload_progress.lock:
                    self._upload_progress.running = False
                    self._upload_progress.phase = "error"
                    self._upload_progress.error = str(exc)
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except Exception:
                with self._upload_progress.lock:
                    self._upload_progress.running = False
                    self._upload_progress.phase = "error"
                raise
            with self._scan_cache.lock:
                self._scan_cache.variables = None  # Ein neuer Upload macht den bisherigen Cache ungültig.
            logger.info("Symcon-ZIP empfangen · Größe=%s", format_size(tmp_zip.stat().st_size))
            self._run_upload_background(
                tmp_zip,
                {"filename": Path(file.filename).name, "size_bytes": tmp_zip.stat().st_size},
            )
            return {"ok": True}



        @router.get("/import/upload-progress")
        def import_upload_progress() -> dict:
            """Wird per fetch()-Polling aus import.html aufgerufen, solange Entpacken/
            Scannen im Hintergrund läuft (Konzept Abschnitt 04)."""
            with self._upload_progress.lock:
                return {
                    "running": self._upload_progress.running,
                    "phase": self._upload_progress.phase,
                    "done": self._upload_progress.done,
                    "total": self._upload_progress.total,
                    "error": self._upload_progress.error,
                }



        @router.post("/import/settings-upload")
        async def import_settings_upload(file: UploadFile = File(...)) -> dict:
            """Optionaler Zusatz-Upload zum db-ZIP: Symcons settings.json (Objektbaum-
            Export) liefert Klarnamen je Variablen-ID (Konzept "Offene Punkte") — rein
            informativ für die Namensspalte im Import-Assistenten, ändert an Zuordnung/
            Import selbst nichts. Anders als der ZIP-Upload synchron: settings.json ist
            reiner JSON-Text, das Parsen dauert auch bei großen Symcon-Installationen
            nur Millisekunden, eine Fortschrittsanzeige wäre hier unnötige Komplexität."""
            if not file.filename or not file.filename.lower().endswith(".json"):
                raise HTTPException(status_code=400, detail="Bitte eine JSON-Datei hochladen")
            tmp_json = self.deps.data_dir / "_symcon_settings_upload.json"
            try:
                await run_in_threadpool(
                    copy_upload_limited,
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
                with self._import_source_lock:
                    with self.deps.coordinator.exclusive():
                        self.deps.data_dir.mkdir(parents=True, exist_ok=True)
                        tmp_names = self.deps.symcon_names_path.with_suffix(".json.part")
                        try:
                            with tmp_names.open("w", encoding="utf-8") as f:
                                json.dump(names, f)
                            tmp_names.replace(self.deps.symcon_names_path)
                        finally:
                            tmp_names.unlink(missing_ok=True)

            await run_in_threadpool(store_names)
            return {"ok": True, "count": len(names)}



        @router.post("/import/delete", response_class=RedirectResponse)
        def import_delete(request: Request) -> RedirectResponse:
            """Entfernt die entpackten Symcon-Daten wieder (Konzept Abschnitt 03: bleiben
            sonst erhalten, damit Zuordnung/Dry Run beliebig oft wiederholbar sind, ohne
            jedes Mal neu hochladen zu müssen) — inklusive der aus einer settings.json
            abgeleiteten Namens-Zuordnung (Konzept "Offene Punkte"), falls eine
            importiert wurde: "Daten löschen" ist der bewusste, komplette Reset für
            diese Import-Sitzung, nicht nur für den db-Ordner."""
            with self._import_source_lock:
                with self.deps.coordinator.exclusive():
                    symcon_import.delete_source(self.deps.symcon_import_dir)
                    self.deps.symcon_names_path.unlink(missing_ok=True)
                    self.deps.symcon_source_meta_path.unlink(missing_ok=True)
                    self.deps.symcon_scan_cache_path.unlink(missing_ok=True)
                    with self._scan_cache.lock:
                        self._scan_cache.variables = None
            logger.info("Symcon-Quelldaten gelöscht")
            # Post/Redirect/Get: Die vollständige Importseite darf nicht direkt unter
            # /import/delete gerendert werden. Ihre relativen CSS-/JS-Pfade würden dort
            # zu /import/static/... aufgelöst und ein Reload würde den Lösch-POST erneut
            # absenden. app_root berücksichtigt dabei Home Assistants Ingress-Präfix.
            app_root = self.deps.app_root_context(request)["app_root"]
            return RedirectResponse(url=f"{app_root}/import", status_code=303)



        @router.post("/import/dry-run", response_class=HTMLResponse)
        async def import_dry_run(request: Request) -> HTMLResponse:
            """Vorschau ohne Schreibvorgang (Konzept Abschnitt 03) — beliebig oft
            wiederholbar, z. B. nach einer geänderten Zuordnung."""
            form = await request.form()
            def plan_locked():
                with self._import_source_lock:
                    mapped = self._mapped_variables(form)
                    with self.deps.coordinator.entities([target for _, target, _ in mapped]):
                        plans = []
                        errors = []
                        for variable, target_entity_id, factor in mapped:
                            try:
                                plans.append(
                                    symcon_import.plan_import(
                                        self.deps.data_dir,
                                        self.deps.index,
                                        variable,
                                        target_entity_id,
                                        self.deps.tz,
                                        factor=factor,
                                    )
                                )
                            except ValueError:
                                errors.append(f"{variable.variable_id} → {target_entity_id}: Entität nicht gefunden")
                        return plans, errors

            plans, errors = await run_in_threadpool(plan_locked)
            return self.deps.templates.TemplateResponse(
                request, "_import_dry_run.html", {"plans": plans, "errors": errors}
            )



        @router.post("/import/start", response_class=HTMLResponse)
        async def import_start(request: Request) -> HTMLResponse:
            """Startet den Import der zugeordneten Variablen im Hintergrund (Konzept
            Abschnitt 04) und liefert sofort die Fortschrittsanzeige zurück, statt auf
            den kompletten Schreibvorgang zu warten — bei hunderten Monaten und
            Millionen Zeilen kann das sonst minutenlang blockieren. Bestehende
            Archivmonate werden übersprungen; nur der tatsächliche laufende Monat
            wird duplikatsicher im Hot Buffer ergänzt. Dieselbe Klassifizierung wie
            /import/dry-run hält Vorschau und Ergebnis deckungsgleich."""
            form = await request.form()
            def admit_import() -> None:
                with self._import_admission_lock:
                    with self._upload_progress.lock:
                        upload_running = self._upload_progress.running
                    with self._import_progress.lock:
                        already_running = self._import_progress.running
                    if upload_running:
                        raise HTTPException(status_code=409, detail="Ein Upload oder Scan läuft bereits")
                    if not already_running:
                        with self._import_source_lock:
                            self._run_import_background(self._mapped_variables(form))

            await run_in_threadpool(admit_import)
            return self.deps.templates.TemplateResponse(request, "self._import_progress.html", self._import_progress_context())



        @router.get("/import/progress", response_class=HTMLResponse)
        def import_progress(request: Request) -> HTMLResponse:
            """Wird per htmx-Polling alle 500ms aufgerufen, solange der Hintergrund-
            Import läuft (Konzept Abschnitt 04) — liefert entweder die Fortschritts-
            anzeige (löst weiteres Polling aus) oder, sobald fertig, das Endergebnis
            ohne hx-trigger, was das Polling automatisch stoppt."""
            with self._import_progress.lock:
                started = self._import_progress.started
            ctx = self._import_progress_context()
            if not started:
                return HTMLResponse("")
            if ctx["running"]:
                return self.deps.templates.TemplateResponse(request, "self._import_progress.html", ctx)
            return self.deps.templates.TemplateResponse(request, "_import_result.html", ctx)


        # ---------------------------------------------------------------------------
        # Eigener CSV-Import (Konzept "Offene Punkte") — bewusst als eigener, klar
        # abgetrennter Abschnitt: eine Datei, freie Spalten-/Format-Zuordnung, ein
        # Ziel-Entität, ganz anders im Ablauf als der Symcon-Assistent oben, auch wenn
        # beide dieselbe nie-destruktive Monats-Klassifizierung teilen
        # (symcon_import.plan_import_rows()/import_rows()).
        # ---------------------------------------------------------------------------



        @router.post("/import/csv/upload", response_class=HTMLResponse)
        async def import_csv_upload(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
            """Reiner Byte-Upload, keine Hintergrund-Verarbeitung nötig (anders als der
            Symcon-ZIP): eine einzelne CSV-Datei ist klein genug, dass Speichern +
            Trennzeichen-/Kopfzeilen-Schätzung synchron passieren können, ohne wie ein
            Hänger zu wirken — läuft deshalb direkt als htmx-Multipart-Post, keine
            eigene XHR-Fortschrittsanzeige wie beim (potenziell riesigen) Symcon-ZIP."""
            if not file.filename or not file.filename.lower().endswith(".csv"):
                raise HTTPException(status_code=400, detail="Bitte eine CSV-Datei hochladen")
            staging = self.deps.data_dir / "_csv_upload"
            try:
                await run_in_threadpool(
                    copy_upload_limited, file.file, staging, MAX_CSV_UPLOAD_BYTES
                )
            except UploadLimitExceeded as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            def install_staging() -> None:
                with self.deps.coordinator.exclusive():
                    if self.deps.csv_import_dir.exists():
                        shutil.rmtree(self.deps.csv_import_dir)
                    self.deps.csv_import_dir.mkdir(parents=True, exist_ok=True)
                    # .name statt des rohen Dateinamens: derselbe Zip-Slip-Vorsichtsgedanke
                    # wie bei extract_zip() — Upload-Dateinamen sind nicht vertrauenswürdig.
                    dest = self.deps.csv_import_dir / Path(file.filename).name
                    staging.replace(dest)

            try:
                await run_in_threadpool(install_staging)
            finally:
                staging.unlink(missing_ok=True)
            installed_csv = next(self.deps.csv_import_dir.iterdir(), None)
            logger.info(
                "CSV-Datei empfangen · Größe=%s",
                format_size(installed_csv.stat().st_size) if installed_csv and installed_csv.is_file() else "—",
            )
            return self.deps.templates.TemplateResponse(request, "_csv_import_section.html", self._csv_import_context())



        @router.post("/import/csv/delete", response_class=HTMLResponse)
        def import_csv_delete(request: Request) -> HTMLResponse:
            with self.deps.coordinator.exclusive():
                if self.deps.csv_import_dir.exists():
                    shutil.rmtree(self.deps.csv_import_dir)
            logger.info("CSV-Quelldaten gelöscht")
            return self.deps.templates.TemplateResponse(request, "_csv_import_section.html", self._csv_import_context())



        @router.post("/import/csv/preview", response_class=HTMLResponse)
        async def import_csv_preview(request: Request) -> HTMLResponse:
            """Aktualisiert Spalten-Vorschau + Auswahlfelder live, wenn Trennzeichen/
            Kopfzeile/Format geändert werden — vor dem eigentlichen Dry Run/Import,
            damit die Spaltenzuordnung sichtbar richtig sitzt, bevor irgendetwas
            gelesen/geschrieben wird."""
            form = await request.form()
            delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id = self._csv_form_params(form)
            def preview_locked() -> dict:
                with self.deps.coordinator.exclusive():
                    return self._csv_import_context(
                        delimiter=delimiter,
                        has_header=has_header,
                        ts_col=ts_col,
                        value_col=value_col,
                        ts_format=ts_format,
                        custom_pattern=custom_pattern,
                        entity_id=entity_id,
                    )

            ctx = await run_in_threadpool(preview_locked)
            return self.deps.templates.TemplateResponse(request, "_csv_import_section.html", ctx)



        @router.post("/import/csv/dry-run", response_class=HTMLResponse)
        async def import_csv_dry_run(request: Request) -> HTMLResponse:
            """Vorschau ohne Schreibvorgang — reicht dieselbe ImportPlan-Vorlage wie
            der Symcon-Import (_import_dry_run.html), da plan_import_rows() dieselbe
            generische ImportPlan-Struktur zurückgibt."""
            form = await request.form()
            delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id = self._csv_form_params(form)
            plans: list[symcon_import.ImportPlan] = []
            errors: list[str] = []
            if not entity_id:
                errors.append("Bitte eine Ziel-Entität auswählen.")
            else:
                def plan_csv_locked():
                    with self.deps.coordinator.entity(entity_id):
                        path = self._csv_uploaded_path()
                        if path is None:
                            return None
                        parsed = csv_import.parse_rows(
                            path, delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, self.deps.tz
                        )
                        return symcon_import.plan_import_rows(
                            self.deps.data_dir, self.deps.index, parsed.rows, entity_id, self.deps.tz,
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
            return self.deps.templates.TemplateResponse(request, "_import_dry_run.html", {"plans": plans, "errors": errors})



        @router.post("/import/csv/start", response_class=HTMLResponse)
        async def import_csv_start(request: Request) -> HTMLResponse:
            """Schreibt synchron (anders als der Symcon-Import kein Hintergrund-Thread
            nötig): eine einzelne Datei/Entität ist vom Umfang her vergleichbar mit
            EINER Symcon-Variable, für die der Symcon-Import ebenfalls ohne spürbare
            Verzögerung durchläuft. Nie destruktiv — dieselbe Monats-Klassifizierung
            (import_rows()) wie beim Symcon-Import, derselbe Dry-Run vorher möglich."""
            started_at = datetime.now(timezone.utc)
            form = await request.form()
            delimiter, has_header, ts_col, value_col, ts_format, custom_pattern, entity_id = self._csv_form_params(form)
            results: list[symcon_import.ImportResult] = []
            errors: list[str] = []
            reconciliation_report = None
            source_path = self._csv_uploaded_path()
            if not entity_id:
                errors.append("Bitte eine Ziel-Entität auswählen.")
            else:
                logger.info("CSV-Import gestartet · Ziel=%s", entity_id)

                def execute_csv_import():
                    with self.deps.coordinator.exclusive():
                        path = self._csv_uploaded_path()
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
                            self.deps.tz,
                        )
                        result = symcon_import.import_rows(
                            self.deps.data_dir,
                            self.deps.index,
                            parsed.rows,
                            entity_id,
                            self.deps.tz,
                            source_label=path.name,
                            skipped_rows=parsed.skipped,
                        )
                        reconciliation = self.deps.run_storage_reconciliation(entity_ids=[entity_id], repair=True)
                        return result, reconciliation

                try:
                    result, reconciliation_report = await run_in_threadpool(execute_csv_import)
                    results.append(result)
                    logger.info(
                        "CSV-Import abgeschlossen · Ziel=%s · Zeilen importiert=%d · Zeilen zusammengeführt=%d",
                        entity_id, result.rows_imported, result.rows_merged,
                    )
                    # Wie bei HA (siehe _fetch_ha_history): eine hohe
                    # Übersprungen-Quote deutet eher auf falsch gewählte
                    # Spalten/Format als auf normale Datenlücken hin.
                    if result.skipped_rows > 0 and result.skipped_rows >= result.rows_imported + result.rows_merged:
                        logger.warning(
                            "CSV-Import: %d Zeile(n) übersprungen (nicht als Zeitstempel/Wert lesbar) · Ziel=%s",
                            result.skipped_rows, entity_id,
                        )
                except ValueError as exc:
                    errors.append(str(exc))
                except Exception as exc:
                    logger.exception("CSV-Import unerwartet fehlgeschlagen")
                    errors.append(f"Import abgebrochen: {exc}")
            try:
                with self.deps.coordinator.exclusive():
                    import_reports.create(
                        self.deps.data_dir,
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
            return self.deps.templates.TemplateResponse(request, "_import_result.html", {"results": results, "errors": errors})


        # ---------------------------------------------------------------------------
        # Home-Assistant-Import (Rohhistorie über die HA-Core-REST-API und
        # Langzeitstatistik über WebSocket) — kein Datei-Upload, Quelle und Ziel sind dieselbe
        # Entitäts-ID. Teilt sich wie der CSV-Import die generische Monats-
        # Klassifizierung mit dem Symcon-Import (plan_import_rows()/import_rows()).
        # Anders als bei Symcon/CSV muss die Ziel-Entität hier NICHT frei wählbar
        # sein: zur Auswahl stehen ausschließlich bereits in Zeitarchiv bekannte
        # Entitäten (index.list_entities(), siehe _ha_import_context()) — die HA-
        # Integration hat sie bereits konfiguriert/gefiltert, ein eigener
        # Entdeckungs-/Anlage-Mechanismus über die Core-API würde diese Filterung
        # umgehen. Ein trotzdem übermittelter unbekannter entity_id-Wert wird
        # verworfen, statt eine neue Entität anzulegen.
        # ---------------------------------------------------------------------------



        @router.post("/import/ha/availability", response_class=HTMLResponse)
        async def import_ha_availability(request: Request) -> HTMLResponse:
            """Gebündelte Verfügbarkeits-Vorschau für die Auswahltabelle
            ("Art"/"Verfügbar"-Spalten) — bewusst ein eigener, expliziter
            Button-Klick statt eines automatischen Ladens beim Öffnen des
            Reiters oder bei jedem Zeitraum-Wechsel: sonst würde jeder
            Seitenaufruf automatisch HA-Requests auslösen (siehe
            _ha_import_context()-Docstring). Geprüft werden ausschließlich
            die aktuell als entity_ids übermittelten, markierten Entitäten —
            dieselbe Auswahl, die anschließend auch Dry Run und Import nutzen.
            Rendert dieselbe _ha_import_section.html neu
            (komplettes outerHTML-Swap, wie auch die CSV-Sektion bei
            Steuerungsänderungen) und reicht die aktuell angehakten
            entity_ids als ha_selected_ids durch, damit die Auswahl beim
            Neuzeichnen erhalten bleibt."""
            form = await request.form()
            (
                selected_ids, range_preset, date_from, date_to,
                include_existing_months, stats_range_preset, include_long_term_stats,
            ) = self._ha_form_params(form)
            history_source, period = self._ha_source_params(form)
            known_ids, unknown_ids = self._known_ha_entity_ids(selected_ids)
            if unknown_ids:
                # Kommt normalerweise nicht vor (die Zeilen kommen alle aus
                # index.list_entities()) — deutet auf ein manipuliertes/
                # veraltetes Formular hin, deshalb WARNING statt DEBUG.
                logger.warning(
                    "HA-Verfügbarkeitsprüfung: unbekannte Entitäts-IDs verworfen · %s", unknown_ids
                )
            if known_ids:
                start, stats_start, end = self._ha_request_ranges(
                    range_preset, date_from, date_to, history_source, stats_range_preset
                )
                if history_source == "full":
                    availability, availability_error = await run_in_threadpool(
                        self._fetch_ha_full_availability,
                        known_ids, start, stats_start, end, include_long_term_stats,
                    )
                else:
                    availability, availability_error = await run_in_threadpool(
                        self._fetch_ha_availability, known_ids, start, end, history_source, period
                    )
                # Auch ein Fehlschlag wird gecacht (leeres availability-Dict) —
                # sonst würde ein Seitenwechsel nach einem Fehlschlag wieder
                # kommentarlos auf "noch nie geprüft" zurückfallen, statt den
                # zuletzt aufgetretenen Fehler weiter anzuzeigen.
                self._ha_cache_store(
                    history_source, period, availability, availability_error,
                    range_preset, stats_range_preset, include_long_term_stats,
                )
                if availability_error is None:
                    with_data = sum(1 for a in availability.values() if a.has_data)
                    logger.info(
                        "HA-Verfügbarkeit geprüft · Quelle=%s · Entitäten=%d · mit Daten=%d · Zeitraum=%s",
                        history_source, len(known_ids), with_data, range_preset,
                    )
            context = self._ha_import_context(
                    selected_ids=set(selected_ids), range_preset=range_preset,
                    date_from=date_from, date_to=date_to,
                    history_source=history_source, period=period,
                    include_existing_months=include_existing_months,
                    stats_range_preset=stats_range_preset,
                    include_long_term_stats=include_long_term_stats,
                )
            if not known_ids:
                context["ha_availability_error"] = "Bitte mindestens eine Entität markieren."
            return self.deps.templates.TemplateResponse(
                request, "_ha_import_section.html", context
            )


        @router.post("/import/ha/source", response_class=HTMLResponse)
        async def import_ha_source(request: Request) -> HTMLResponse:
            """Wechselt die Quelle (Rohhistorie/Langzeitstatistik) bzw. die
            Perioden-Auflösung — reiner Neuaufbau der Auswahltabelle OHNE
            HA-API-/WS-Aufruf: _ha_import_context() liest die zur neuen
            Quelle/Periode gehörende Verfügbarkeit automatisch aus
            self._ha_availability_cache (je Kombination separat gepflegt,
            siehe dort) und zeigt sie inkl. Zeitstempel/Veraltet-Warnung an,
            statt sie zu verwerfen — wurde diese Quelle/Periode noch nie
            geprüft, bleibt die Spalte "Verfügbar" schlicht leer. Wie
            /import/ha/availability bleiben aktuell angehakte entity_ids
            sowie die Zeitraum-Auswahl erhalten (range_preset wird dabei ggf.
            auf "max" zurückgesetzt, falls er für die neue Quelle nicht
            existiert, siehe _ha_import_context())."""
            form = await request.form()
            (
                selected_ids, range_preset, date_from, date_to,
                include_existing_months, stats_range_preset, include_long_term_stats,
            ) = self._ha_form_params(form)
            history_source, period = self._ha_source_params(form)
            return self.deps.templates.TemplateResponse(
                request, "_ha_import_section.html",
                self._ha_import_context(
                    selected_ids=set(selected_ids), range_preset=range_preset,
                    date_from=date_from, date_to=date_to,
                    history_source=history_source, period=period,
                    include_existing_months=include_existing_months,
                    stats_range_preset=stats_range_preset,
                    include_long_term_stats=include_long_term_stats,
                ),
            )


        @router.post("/import/ha/dry-run", response_class=HTMLResponse)
        async def import_ha_dry_run(request: Request) -> HTMLResponse:
            """Vorschau ohne Schreibvorgang — eigene Vorlage (_ha_import_dry_run.html)
            statt der mit Symcon/CSV geteilten _import_dry_run.html: zeigt zusätzlich
            den tatsächlich in HA gefundenen Zeitraum/Punkteanzahl je Entität, was nur
            beim HA-Import überhaupt einen Sinn ergibt (Symcon/CSV kennen ihre Quelle
            vollständig, ohne HAs begrenzte Recorder-Aufbewahrung)."""
            form = await request.form()
            (
                raw_entity_ids, range_preset, date_from, date_to,
                include_existing_months, stats_range_preset, include_long_term_stats,
            ) = self._ha_form_params(form)
            history_source, period = self._ha_source_params(form)
            entity_ids, unknown_ids = self._known_ha_entity_ids(raw_entity_ids)
            items: list[dict] = []
            errors: list[str] = [f"{entity_id}: nicht in Zeitarchiv bekannt" for entity_id in unknown_ids]
            context = {"history_source": history_source, "period": period}
            if not entity_ids:
                if not unknown_ids:
                    errors.append("Bitte mindestens eine Entität auswählen.")
                return self.deps.templates.TemplateResponse(
                    request, "_ha_import_dry_run.html", {**context, "items": items, "errors": errors}
                )

            start, stats_start, end = self._ha_request_ranges(
                range_preset, date_from, date_to, history_source, stats_range_preset
            )
            if history_source == "full":
                fetched, fetch_errors = await run_in_threadpool(
                    self._fetch_ha_full_history,
                    entity_ids, start, stats_start, end, include_long_term_stats,
                )
            else:
                fetched, fetch_errors = await run_in_threadpool(
                    self._fetch_ha_history, entity_ids, start, end, history_source, period
                )
            errors.extend(fetch_errors)

            def plan_locked() -> list[dict]:
                with self.deps.coordinator.entities(list(fetched.keys())):
                    result = []
                    for entity_id, history in fetched.items():
                        entity = self.deps.index.get_entity(entity_id)
                        try:
                            plan = symcon_import.plan_import_rows(
                                self.deps.data_dir, self.deps.index, history.rows, entity_id, self.deps.tz,
                                source_label=entity_id, skipped_rows=history.skipped,
                                include_existing_months=include_existing_months,
                            )
                        except ValueError as exc:
                            errors.append(f"{entity_id}: {exc}")
                            continue
                        result.append({
                            "entity_id": entity_id,
                            "friendly_name": (entity["friendly_name"] if entity else None) or entity_id,
                            "available_label": self._ha_available_label(history, history_source),
                            "plan": plan,
                            "full_summary": (
                                self._ha_full_summary(history) if history_source == "full" else None
                            ),
                        })
                    return result

            if fetched:
                items = await run_in_threadpool(plan_locked)
            # DEBUG statt INFO: Vorschauen ändern nichts (dieselbe Zurückhaltung
            # wie beim Symcon-/CSV-Dry-Run, die ebenfalls nicht loggen) — aber
            # anders als dort macht dieser Dry-Run einen echten HA-API-/WS-Aufruf,
            # dessen Planungsergebnis bei der Fehlersuche wertvoll sein kann.
            logger.debug(
                "HA-Dry-Run · Quelle=%s · Entitäten=%d · geplante Zeilen=%d · Fehler=%d",
                history_source,
                len(items),
                sum(
                    item["plan"].rows_to_import
                    + item["plan"].rows_to_merge
                    + item["plan"].rows_to_update
                    for item in items
                ),
                len(errors),
            )
            return self.deps.templates.TemplateResponse(
                request, "_ha_import_dry_run.html", {**context, "items": items, "errors": errors}
            )


        @router.post("/import/ha/debug")
        async def import_ha_debug(request: Request) -> FileResponse:
            """Erzeugt auf expliziten Klick eine vollständige Debug-ZIP.

            Der Dry Run selbst bleibt schnell und erzeugt keine liegenbleibende
            Datei. Der Download wiederholt den HA-Abruf mit denselben
            Formularparametern und löscht die temporäre ZIP unmittelbar nach
            der Übertragung. SUPERVISOR_TOKEN und HTTP-/WS-Header gelangen
            nicht in den Payload.
            """
            generated_at = datetime.now(timezone.utc)
            form = await request.form()
            (
                raw_entity_ids, range_preset, date_from, date_to,
                include_existing_months, stats_range_preset, include_long_term_stats,
            ) = self._ha_form_params(form)
            history_source, period = self._ha_source_params(form)
            entity_ids, unknown_ids = self._known_ha_entity_ids(raw_entity_ids)
            start, stats_start, end = self._ha_request_ranges(
                range_preset, date_from, date_to, history_source, stats_range_preset
            )
            fetched: dict[str, ha_import.HistoryFetchResult] = {}
            errors = [f"{entity_id}: nicht in Zeitarchiv bekannt" for entity_id in unknown_ids]
            if entity_ids:
                if history_source == "full":
                    fetched, fetch_errors = await run_in_threadpool(
                        self._fetch_ha_full_history,
                        entity_ids, start, stats_start, end, include_long_term_stats,
                    )
                else:
                    fetched, fetch_errors = await run_in_threadpool(
                        self._fetch_ha_history,
                        entity_ids, start, end, history_source, period,
                    )
                errors.extend(fetch_errors)
            elif not unknown_ids:
                errors.append("Keine Entität ausgewählt")

            def build_payload() -> dict:
                with self.deps.coordinator.entities(sorted(fetched)):
                    entities = []
                    for entity_id, history in fetched.items():
                        try:
                            entities.append(
                                self._ha_debug_entity(
                                    entity_id, history, include_existing_months
                                )
                            )
                        except Exception as exc:
                            entities.append({
                                "entity_id": entity_id,
                                "error": f"{type(exc).__name__}: {exc}",
                            })
                return {
                    "format": "zeitarchiv-ha-import-debug",
                    "format_version": 2,
                    "generated_at": generated_at.isoformat(),
                    "app_version": APP_VERSION,
                    "timezone": str(self.deps.tz),
                    "security_note": (
                        "Enthält Messwerte und Entitätsmetadaten, aber keine "
                        "Tokens, Authentifizierungsheader oder HA-Attribute."
                    ),
                    "request": {
                        "entity_ids": raw_entity_ids,
                        "range_preset": range_preset,
                        "date_from": date_from,
                        "date_to": date_to,
                        "resolved_start_utc": start.isoformat(),
                        "resolved_stats_start_utc": (
                            stats_start.isoformat() if history_source == "full" else None
                        ),
                        "resolved_end_utc": end.isoformat(),
                        "history_source": history_source,
                        "period": period if history_source in ("stats", "full") else None,
                        "stats_range_preset": (
                            stats_range_preset if history_source == "full" else None
                        ),
                        "include_long_term_stats": (
                            include_long_term_stats if history_source == "full" else None
                        ),
                        "include_existing_months": include_existing_months,
                    },
                    "current_month": datetime.now(self.deps.tz).strftime("%Y-%m"),
                    "errors": errors,
                    "entities": entities,
                }

            payload = await run_in_threadpool(build_payload)
            path, filename = await run_in_threadpool(self._ha_debug_zip, payload)
            return FileResponse(
                path,
                media_type="application/zip",
                filename=filename,
                background=BackgroundTask(path.unlink, missing_ok=True),
            )



        @router.post("/import/ha/start", response_class=HTMLResponse)
        async def import_ha_start(request: Request) -> HTMLResponse:
            """Schreibt synchron, wie der CSV-Import — der Netzwerk-Fetch (potenziell
            der langsamste Teil) läuft vorher außerhalb jeder Sperre; exclusive() hält
            danach nur noch den eigentlichen Schreib- und Indexabgleich-Teil. Eigene
            Vorlage (_ha_import_result.html) wie beim Dry Run, aus demselben Grund."""
            started_at = datetime.now(timezone.utc)
            form = await request.form()
            (
                raw_entity_ids, range_preset, date_from, date_to,
                include_existing_months, stats_range_preset, include_long_term_stats,
            ) = self._ha_form_params(form)
            history_source, period = self._ha_source_params(form)
            entity_ids, unknown_ids = self._known_ha_entity_ids(raw_entity_ids)
            items: list[dict] = []
            results: list[symcon_import.ImportResult] = []
            errors: list[str] = [f"{entity_id}: nicht in Zeitarchiv bekannt" for entity_id in unknown_ids]
            reconciliation_report = None
            start, end = (None, None)
            if not entity_ids:
                if not unknown_ids:
                    errors.append("Bitte mindestens eine Entität auswählen.")
            else:
                logger.info(
                    "Home-Assistant-Import gestartet · Quelle=%s · Entitäten=%d · Zeitraum=%s",
                    history_source, len(entity_ids), range_preset,
                )
                start, stats_start, end = self._ha_request_ranges(
                    range_preset, date_from, date_to, history_source, stats_range_preset
                )
                if history_source == "full":
                    fetched, fetch_errors = await run_in_threadpool(
                        self._fetch_ha_full_history,
                        entity_ids, start, stats_start, end, include_long_term_stats,
                    )
                else:
                    fetched, fetch_errors = await run_in_threadpool(
                        self._fetch_ha_history, entity_ids, start, end, history_source, period
                    )
                errors.extend(fetch_errors)

                def execute_ha_import() -> tuple[list[dict], dict]:
                    with self.deps.coordinator.exclusive():
                        run_items = []
                        for entity_id, history in fetched.items():
                            entity = self.deps.index.get_entity(entity_id)
                            try:
                                result = symcon_import.import_rows(
                                    self.deps.data_dir, self.deps.index, history.rows, entity_id, self.deps.tz,
                                    source_label=entity_id, skipped_rows=history.skipped,
                                    include_existing_months=include_existing_months,
                                )
                            except ValueError as exc:
                                errors.append(f"{entity_id}: {exc}")
                                continue
                            run_items.append({
                                "entity_id": entity_id,
                                "friendly_name": (entity["friendly_name"] if entity else None) or entity_id,
                                "available_label": self._ha_available_label(history, history_source),
                                "result": result,
                                "full_summary": (
                                    self._ha_full_summary(history) if history_source == "full" else None
                                ),
                            })
                        reconciliation = self.deps.run_storage_reconciliation(
                            entity_ids=sorted(fetched.keys()), repair=True
                        ) if fetched else None
                        return run_items, reconciliation

                if fetched:
                    try:
                        items, reconciliation_report = await run_in_threadpool(execute_ha_import)
                        results = [item["result"] for item in items]
                        rows_imported = sum(r.rows_imported for r in results)
                        rows_merged = sum(r.rows_merged for r in results)
                        rows_updated = sum(r.rows_updated for r in results)
                        rows_recovered = sum(r.rows_recovered for r in results)
                        logger.info(
                            "Home-Assistant-Import abgeschlossen · Quelle=%s · Entitäten=%d · Zeilen importiert=%d · "
                            "Zeilen zusammengeführt=%d · bestehende Monate ergänzt=%d · aus aktuellem Archiv gerettet=%d · Fehler=%d",
                            history_source, len(results), rows_imported, rows_merged, rows_updated, rows_recovered, len(errors),
                        )
                        repaired_months = sum(len(r.repaired_current_months) for r in results)
                        if repaired_months:
                            logger.warning(
                                "HA-Import reparierte %d unzulässige Archivdatei(en) des laufenden Monats",
                                repaired_months,
                            )
                        if results and not errors and rows_imported + rows_merged + rows_updated == 0:
                            # Kein Fehler, aber auch keine einzige neue Zeile
                            # geschrieben, obwohl Entitäten ausgewählt waren und
                            # HA tatsächlich geantwortet hat — unterscheidet den
                            # unauffälligen Fall "nichts Neues seit letztem
                            # Import" von einem stillen Fehlschlag, der in der
                            # Oberfläche wie ein normaler Erfolg aussieht.
                            logger.warning(
                                "Home-Assistant-Import ohne Fehler abgeschlossen, aber 0 Zeilen geschrieben · Entitäten=%d",
                                len(results),
                            )
                    except Exception as exc:
                        logger.exception("Home-Assistant-Import unerwartet fehlgeschlagen")
                        errors.append(f"Import abgebrochen: {exc}")
            try:
                with self.deps.coordinator.exclusive():
                    report_results = []
                    for item in items:
                        result_payload = dataclasses.asdict(item["result"])
                        result_payload["available_label"] = item["available_label"]
                        if item.get("full_summary") is not None:
                            result_payload["full_summary"] = item["full_summary"]
                        report_results.append(result_payload)
                    import_reports.create(
                        self.deps.data_dir,
                        source_type="ha",
                        started_at=started_at,
                        source={"filename": "Home Assistant", "size_bytes": 0},
                        configuration={
                            "entity_ids": raw_entity_ids,
                            "range_preset": range_preset,
                            "date_from": date_from,
                            "date_to": date_to,
                            "history_source": history_source,
                            "period": period if history_source in ("stats", "full") else None,
                            "stats_range_preset": (
                                stats_range_preset if history_source == "full" else None
                            ),
                            "include_long_term_stats": (
                                include_long_term_stats if history_source == "full" else None
                            ),
                            "include_existing_months": include_existing_months,
                        },
                        results=report_results,
                        errors=errors,
                        reconciliation=reconciliation_report,
                    )
            except Exception:
                logger.exception("Home-Assistant-Importreport konnte nicht gespeichert werden")
            return self.deps.templates.TemplateResponse(
                request, "_ha_import_result.html",
                {"history_source": history_source, "period": period, "items": items, "errors": errors},
            )


        return router
