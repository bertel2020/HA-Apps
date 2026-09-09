"""Externe Zeitarchiv-API: Authentifizierung, Aufnahme und Abfragen."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import ha_integration
from .formatting import decimals_to_int, entity_display_name
from .limits import (
    MAX_EVENT_TEXT_LENGTH,
    MAX_MARKED_RANGES,
    MAX_MULTI_QUERY_ENTITIES,
    MAX_WRITE_EVENTS,
)
from .logging_setup import log_rate_limited
from .route_support import storage_locked
from .storage import query as query_mod
from .storage.coordinator import StorageCoordinator
from .storage.index import DASHBOARD_TILE_RANGES, Index
from .storage.ingestion import IngestEvent, IngestionService, legacy_event_id
from .storage.paths import ENTITY_ID_MAX_LENGTH, ENTITY_ID_PATTERN, validate_entity_id


logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("zeitarchiv.trace")

WRITE_CAPTURE_TTL_SECONDS = 60 * 60
INGEST_WARNING_MIN_EVENTS = 20
INGEST_DUPLICATE_WARNING_RATIO = 0.50
INGEST_DISCARDED_WARNING_RATIO = 0.95
INGEST_SLOW_BATCH_MS = 1_000.0

EntityId = Annotated[
    str,
    Field(min_length=3, max_length=ENTITY_ID_MAX_LENGTH, pattern=ENTITY_ID_PATTERN),
]

# Die Trennlinie in diesem Modell (ZG-24): was die NACHRICHT beschreibt, wird
# hier abgelehnt; was die MESSUNG beschreibt, nicht.
#
# Der Grund ist das Verhalten des einzigen echten Clients. Der Queue-Writer der
# Integration wiederholt einen Batch "bis zum Erfolg oder bis zum expliziten
# Stopp" und unterscheidet dabei nicht zwischen 4xx und 5xx. Eine Ablehnung
# hier trifft deshalb nie nur ein Event, sondern hält den kompletten Batch
# dauerhaft fest — und mit ihm die Messwerte aller anderen Entitäten darin.
#
# Für einen kaputten Zeitstempel oder ein NaN wäre das der falsche Tausch: ein
# HA-Sensor, dessen Zustand als "nan" rendert, ist ein Datenzustand, kein
# Protokollfehler, und er kommt wieder. Solche Events sortiert deshalb
# IngestionService._ingest_entity_locked() einzeln als "skipped" aus — der
# Batch läuft weiter, die Quote landet in der bestehenden
# INGEST_DISCARDED_WARNING_RATIO-Warnung.
#
# Ein 100.000 Zeichen langer friendly_name ist dagegen kein Datenzustand,
# sondern ein fehlerhafter Client. Dafür ist 422 die richtige Antwort.
EventText = Annotated[str, Field(max_length=MAX_EVENT_TEXT_LENGTH)]


class EventIn(BaseModel):
    event_id: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    )
    entity_id: EntityId
    domain: EventText
    ts: float
    value: float
    state_class: EventText | None = None
    unit: EventText | None = None
    friendly_name: EventText | None = None


class WriteRequest(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=MAX_WRITE_EVENTS)


class TableQueryColumn(BaseModel):
    range_key: str
    offset: int = 0
    year_over_year: bool = False
    # "Gleicher Zeitpunkt"-Vergleich (Konzept-Erweiterung Vergleichstabelle):
    # nur sinnvoll/wirksam für offset<0 — siehe query.query_series().
    same_elapsed: bool = False


class TableQueryRequest(BaseModel):
    """Alle Zeitfenster einer Vergleichstabelle in einem Storage-Snapshot."""

    entity_ids: list[EntityId] = Field(min_length=1, max_length=MAX_MULTI_QUERY_ENTITIES)
    columns: list[TableQueryColumn] = Field(min_length=1, max_length=100)


@dataclass
class ApiState:
    """Prozesslokaler Diagnosezustand, den auch die Settings-Routen anzeigen."""

    server_started_at: float = field(default_factory=time.time)
    connection_stats: dict = field(default_factory=lambda: {
        "write_requests_ok": 0,
        "auth_failures": 0,
        "last_auth_failure_ts": None,
    })
    write_capture_lock: threading.Lock = field(default_factory=threading.Lock)
    write_capture: dict = field(default_factory=lambda: {
        "armed": False, "captured_at": None, "expires_at": None, "payload": None,
    })
    entity_trace_lock: threading.Lock = field(default_factory=threading.Lock)
    entity_trace: dict = field(default_factory=lambda: {
        "entity_id": None, "started_at": None, "expires_at": None,
    })


@dataclass(frozen=True)
class ApiDependencies:
    data_dir: Path
    index: Index
    tz: ZoneInfo
    coordinator: StorageCoordinator
    ingestion: IngestionService
    api_token: Callable[[], str]
    app_version: str
    collect_notices: Callable[[], list[dict]]
    latest_backup: Callable[[], dict | None]
    # Grundlage für sensor.zeitarchiv_betriebsmodus (Integration) — siehe
    # DEMO_MODUS_PLAN.md Punkt 11. Default False, damit die zahlreichen
    # Test-Konstruktionsstellen dieser Dataclass (Schreib-/Lese-Endpunkte,
    # die mit Demo-Modus nichts zu tun haben) nicht alle angefasst werden
    # müssen — main.py setzt ihn explizit auf DEMO_MODE.
    demo_mode_active: bool = False


def expire_write_capture(capture: dict, now: float | None = None) -> bool:
    """Entfernt einen abgelaufenen scharfen oder bereits gefüllten Capture."""
    now = time.time() if now is None else now
    expires_at = capture.get("expires_at")
    if expires_at is None or expires_at > now:
        return False
    capture.update(armed=False, captured_at=None, expires_at=None, payload=None)
    return True


def expire_entity_trace(trace: dict, now: float | None = None) -> bool:
    """Setzt einen abgelaufenen Entity-Trace vollständig zurück."""
    now = time.time() if now is None else now
    expires_at = trace.get("expires_at")
    if expires_at is None or expires_at > now:
        return False
    trace.update(entity_id=None, started_at=None, expires_at=None)
    return True


def schedule_write_capture_expiry(state: ApiState) -> None:
    """Löscht einen Capture zum gesetzten Ablaufzeitpunkt auch ohne UI-Poll."""
    with state.write_capture_lock:
        expected_expires_at = state.write_capture.get("expires_at")
    if expected_expires_at is None:
        return

    def expire_if_current() -> None:
        with state.write_capture_lock:
            if state.write_capture.get("expires_at") == expected_expires_at:
                expire_write_capture(state.write_capture, expected_expires_at + 0.001)

    timer = threading.Timer(
        max(0.0, float(expected_expires_at) - time.time()) + 0.01,
        expire_if_current,
    )
    timer.daemon = True
    timer.start()


def _validate_entity_id_or_400(entity_id: str) -> None:
    try:
        validate_entity_id(entity_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _table_aggregates(result: dict) -> dict[str, float | None]:
    """Verdichtet eine Chart-Serie auf die fünf Tabellen-Aggregationen."""
    points = result["points"]
    total = sum((point["value"] or 0.0) for point in points)
    average = total / len(points) if points else 0.0
    minima = [
        point["min"] if point["min"] is not None else point["value"]
        for point in points
        if point["min"] is not None or point["value"] is not None
    ]
    maxima = [
        point["max"] if point["max"] is not None else point["value"]
        for point in points
        if point["max"] is not None or point["value"] is not None
    ]
    automatic = total if result["aggregation_type"] in ("counter", "switch") else average
    return {
        "auto": automatic,
        "avg": average,
        "min": min(minima) if minima else None,
        "max": max(maxima) if maxima else None,
        "sum": total,
    }


#: Sparkline-Auflösungen der Werte-Kachel als Bucket-Länge in Sekunden.
#: Spiegelt resampleSparklinePoints() in static/js/dashboard-tiles.js — das
#: Ausdünnen wandert mit /api/entity-stats auf den Server, damit nicht mehr
#: jeder Rohpunkt eines Tages zum Browser übertragen wird, nur um dort
#: verworfen zu werden.
_SPARKLINE_BUCKET_SECONDS = {"raw": 0, "5min": 300, "15min": 900, "30min": 1800, "1h": 3600}

#: Zeiträume, für die Rohwerte überhaupt abgefragt werden. Dieselbe Grenze wie
#: im Optionen-Menü der Entitätsseite (entity_detail.html): darüber sprengt die
#: Punktzahl MAX_RAW_QUERY_POINTS, und ein Monat als Sparkline braucht ohnehin
#: keine Sekundenauflösung — dort sind die Buckets der Abfrage (1 Tag bzw.
#: 1 Monat) die richtige Dichte.
_RAW_CAPABLE_RANGES = ("hour", "day", "week")


def _resample_sparkline(points: list[dict], resolution: str) -> list[dict]:
    """Dünnt Rohpunkte auf einen Punkt je Bucket aus — der jeweils LETZTE des
    Buckets, wie im Browser bisher: er passt gleichermaßen für Messwerte,
    kumulative Zähler und Schalter und lässt den aktuellen Stand intakt."""
    bucket_seconds = _SPARKLINE_BUCKET_SECONDS.get(resolution, 0)
    if not bucket_seconds or len(points) < 2:
        return points
    buckets: dict[int, dict] = {}
    for point in points:
        buckets[int(point["ts"] // bucket_seconds)] = point
    return [buckets[key] for key in sorted(buckets)]


def _duration_noun(seconds: int) -> str:
    """"Stunde", "5 Minuten", "2 Stunden" — für Tooltip-Text, nicht für Rechnen."""
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "Stunde" if hours == 1 else f"{hours} Stunden"
    minutes = max(1, seconds // 60)
    return "Minute" if minutes == 1 else f"{minutes} Minuten"


_ROLLUP_LEVEL_NOUNS = {"minute": "Minute", "stunde": "Stunde", "tag": "Tag", "monat": "Monat", "jahr": "Jahr"}


def _counter_bucket_label(range_key: str) -> str | None:
    """Wie lang ein Bucket bei einem ZÄHLER ist, als Wort für den Tooltip.

    Nur bei Zählern nötig, und nur dort ehrlich: dessen Min/Ø/Max beziehen
    sich auf Bucket-Deltas, "Max" heißt also "stärkster Tag" und nicht
    "größter Messwert". Bei Messwerten wäre der Hinweis irreführend — deren
    Bucket-Min/-Max sind die echten Extremwerte der Rohdaten (das Rollup führt
    min_value/max_value mit), und nur der Durchschnitt hängt überhaupt an der
    Bucket-Größe.

    Aus LIVE_BUCKET_SECONDS/BAR_RESOLUTION abgeleitet statt danebengeschrieben:
    eine zweite Tabelle würde beim nächsten Auflösungswechsel unbemerkt
    veralten und dann etwas Falsches erklären.
    """
    live_seconds = query_mod.LIVE_BUCKET_SECONDS["counter"].get(range_key)
    if live_seconds is not None:
        return _duration_noun(live_seconds)
    return _ROLLUP_LEVEL_NOUNS.get(query_mod.BAR_RESOLUTION.get(range_key, ""))


def _tile_aggregates(result: dict) -> dict[str, float | None]:
    """Die Kennzahlen einer Werte-Kachel aus einer gebucketen Serie.

    Aufsatz auf _table_aggregates(), aber mit zwei Nullungen: was für den
    Entitätstyp keine Aussage ist, kommt gar nicht erst beim Browser an, statt
    dort noch einmal ausgeblendet zu werden.

    * ``sum`` nur bei Zählern und Schaltern — dieselbe Regel wie in der
      Chart-Legende (``hasSum`` in entity_detail.html). Die Summe von
      Momentanwerten (20 °C + 21 °C + …) ist keine Temperatur.
    * ``min``/``max`` nicht bei Schaltern — deren Bucket-Werte sind
      Einschaltsekunden, "kleinster Wert" hieße dort "kürzeste Stunde" und
      wäre als bloße Zahl neben dem Zustand nicht lesbar.

    Bewusst NICHT aus den Rohwerten gerechnet, auch wo diese für die Sparkline
    ohnehin vorliegen: Rohwerte eines Zählers sind Zählerstände, ihre Summe
    wäre eine sinnlose Zahl in der Größenordnung "Zählerstand × Messpunkte".
    Erst query_series() bildet daraus die Bucket-Deltas, deren Summe den
    Verbrauch der Periode ergibt.
    """
    aggregation_type = result.get("aggregation_type")
    values = _table_aggregates(result)
    has_sum = aggregation_type in ("counter", "switch")
    is_switch = aggregation_type == "switch"
    return {
        "min": None if is_switch else values["min"],
        "avg": values["avg"],
        "max": None if is_switch else values["max"],
        "sum": values["sum"] if has_sum else None,
    }


def _table_comparison_aggregates(result: dict) -> dict[str, float | None] | None:
    """Fairer Vergleichswert für eine abgeschlossene "Vor-"Spalte (Vortag/
    Vorwoche/…): dieselben Punkte wie der jetzt immer vollständige
    Hauptwert, aber zusätzlich auf denselben Abstand vom Periodenanfang
    gekappt, den die noch laufende aktuelle Periode gerade hat
    (elapsed_seconds, siehe query_series()) — sonst vergliche z. B. "Tag
    bisher" unfair gegen den ganzen "Vortag" statt gegen "Vortag bis zur
    selben Uhrzeit". None, wenn kein fairer Vergleich angefordert wurde
    (kein same_elapsed, offset>=0, oder year_over_year)."""
    elapsed_seconds = result.get("elapsed_seconds")
    if elapsed_seconds is None:
        return None
    cutoff = result["window_start"] + elapsed_seconds
    capped_points = [point for point in result["points"] if point["ts"] < cutoff]
    return _table_aggregates({**result, "points": capped_points})


def create_api_router(deps: ApiDependencies, state: ApiState) -> APIRouter:
    router = APIRouter()
    def locked(getter):
        return storage_locked(deps.coordinator, getter)

    def check_auth(
        authorization: str | None,
        request_id: str = "-",
        integration_version: str | None = None,
    ) -> None:
        expected = f"Bearer {deps.api_token()}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            state.connection_stats["auth_failures"] += 1
            state.connection_stats["last_auth_failure_ts"] = time.time()
            log_rate_limited(
                logger,
                logging.WARNING,
                "api_auth_failure",
                "API-Authentifizierung fehlgeschlagen · event=api_auth_failure request_id=%s gesamt_seit_start=%d",
                request_id,
                state.connection_stats["auth_failures"],
                interval_seconds=300,
            )
            raise HTTPException(status_code=401, detail="Ungültiger oder fehlender API-Token")
        if integration_version:
            ha_integration.record_seen(deps.index, integration_version)

    def limited_multi_entity_ids(args: dict) -> list[str]:
        ids = [item.strip() for item in args["entity_ids"].split(",") if item.strip()]
        if len(ids) > MAX_MULTI_QUERY_ENTITIES:
            raise HTTPException(
                status_code=413,
                detail=f"Maximal {MAX_MULTI_QUERY_ENTITIES} Entitäten pro Abfrage",
            )
        return ids

    @router.get("/api/health")
    def health(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        check_auth(
            authorization,
            getattr(request.state, "request_id", "-"),
            x_zeitarchiv_integration_version,
        )
        return {"status": "ok", "version": deps.app_version}

    @router.get("/api/notices")
    def notices(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Aktuell aktive, nicht stummgeschaltete Meldungen (siehe notices.py)
        als stabile API — Grundlage für die HA-Integration (Repairs/Entities),
        bewusst dieselbe gefilterte Liste wie im Glocken-Icon der Zeitarchiv-
        UI, damit eine dort stummgeschaltete Meldung nicht in HA weiter nervt."""
        check_auth(
            authorization,
            getattr(request.state, "request_id", "-"),
            x_zeitarchiv_integration_version,
        )
        return {
            "notices": deps.collect_notices(),
            "latest_backup": deps.latest_backup(),
            "demo_mode": deps.demo_mode_active,
        }

    @router.post("/api/write")
    def write(
        payload: WriteRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        request_id = getattr(request.state, "request_id", "-")
        check_auth(authorization, request_id, x_zeitarchiv_integration_version)
        state.connection_stats["write_requests_ok"] += 1
        now = time.time()
        captured_now = False
        with state.write_capture_lock:
            expire_write_capture(state.write_capture, now)
            if state.write_capture["armed"]:
                state.write_capture.update(
                    armed=False,
                    captured_at=now,
                    expires_at=now + WRITE_CAPTURE_TTL_SECONDS,
                    payload=payload.model_dump(),
                )
                captured_now = True
        if captured_now:
            schedule_write_capture_expiry(state)

        with state.entity_trace_lock:
            expire_entity_trace(state.entity_trace, now)
            trace_entity = state.entity_trace["entity_id"]
            trace_active = bool(trace_entity) and (state.entity_trace["expires_at"] or 0) > now

        counts = {"written": 0, "skipped": 0, "filtered": 0, "duplicate": 0, "recovered": 0}
        started = time.perf_counter()
        try:
            for event in payload.events:
                event_data = event.model_dump()
                event_id = event.event_id or legacy_event_id(
                    {key: value for key, value in event_data.items() if key != "event_id"}
                )
                result = deps.ingestion.ingest(IngestEvent(event_id=event_id, **{
                    key: value for key, value in event_data.items() if key != "event_id"
                }))
                counts[result] += 1
                if trace_active and event.entity_id == trace_entity:
                    trace_logger.debug(
                        "Entity-Trace · event=entity_trace request_id=%s entity_id=%s "
                        "event_id=%s ts=%s time=%s value=%s unit=%s domain=%s result=%s",
                        request_id,
                        event.entity_id,
                        event_id[:12],
                        event.ts,
                        datetime.fromtimestamp(event.ts, deps.tz).isoformat(timespec="milliseconds"),
                        event.value,
                        event.unit or "—",
                        event.domain,
                        result,
                    )
        except Exception:
            logger.exception(
                "Ingest-Batch fehlgeschlagen · event=ingest_batch_failed request_id=%s events=%d",
                request_id,
                len(payload.events),
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        throughput = len(payload.events) / max(duration_ms / 1000, 0.001)
        logger.debug(
            "Schreibbatch verarbeitet · event=ingest_batch_completed request_id=%s "
            "events=%d written=%d skipped=%d filtered=%d duplicate=%d recovered=%d "
            "duration_ms=%.1f events_per_second=%.1f",
            request_id,
            len(payload.events),
            counts["written"],
            counts["skipped"],
            counts["filtered"],
            counts["duplicate"],
            counts["recovered"],
            duration_ms,
            throughput,
        )
        event_count = len(payload.events)
        if duration_ms >= INGEST_SLOW_BATCH_MS:
            log_rate_limited(
                logger,
                logging.WARNING,
                "ingest_slow_batch",
                "Langsamer Ingest-Batch · event=ingest_batch_slow request_id=%s events=%d duration_ms=%.1f",
                request_id,
                event_count,
                duration_ms,
                interval_seconds=300,
            )
        if event_count >= INGEST_WARNING_MIN_EVENTS:
            duplicate_ratio = counts["duplicate"] / event_count
            discarded_ratio = (counts["filtered"] + counts["skipped"]) / event_count
            if duplicate_ratio >= INGEST_DUPLICATE_WARNING_RATIO:
                log_rate_limited(
                    logger,
                    logging.WARNING,
                    "ingest_duplicate_ratio",
                    "Hohe Duplikatquote im Ingest · event=ingest_duplicate_ratio request_id=%s "
                    "events=%d duplicate=%d ratio=%.3f",
                    request_id,
                    event_count,
                    counts["duplicate"],
                    duplicate_ratio,
                    interval_seconds=300,
                )
            if discarded_ratio >= INGEST_DISCARDED_WARNING_RATIO:
                log_rate_limited(
                    logger,
                    logging.WARNING,
                    "ingest_discarded_ratio",
                    "Hohe Filterquote im Ingest · event=ingest_discarded_ratio request_id=%s "
                    "events=%d filtered=%d skipped=%d ratio=%.3f",
                    request_id,
                    event_count,
                    counts["filtered"],
                    counts["skipped"],
                    discarded_ratio,
                    interval_seconds=300,
                )
        return counts

    @router.get("/api/query")
    @locked(lambda args: args["entity_id"])
    def api_query(
        entity_id: str,
        range: str = Query("day", alias="range"),
        offset: int = 0,
        continuous: bool = False,
        compare: bool = False,
        compare_mode: str = "previous",
        raw: bool = False,
        chart_type: str | None = None,
        marked: bool = False,
    ) -> dict:
        _validate_entity_id_or_400(entity_id)
        if chart_type not in (None, "line", "bar"):
            raise HTTPException(status_code=400, detail="Ungültiger Diagrammtyp")
        now = datetime.now(deps.tz)
        # Ein Cache für beide Abfragen dieses Requests: Mit "Vergleichen" wird
        # dieselbe Entität zweimal abgefragt, nur mit verschobenem Fenster —
        # ohne ihn liest der zweite Aufruf jahr.parquet/monat.parquet erneut.
        # Gemessen bei Zeitraum "Dekade": 81,6 -> 6,9 ms. query_raw_series()
        # nimmt bewusst keinen: Rohwerte gibt es nur für kurze Zeiträume, die
        # keine groben Rollup-Stufen anfassen.
        read_cache = query_mod.QueryReadCache()
        # Kein frühes return im raw-Zweig mehr: die Markierungen unten gelten
        # für beide Pfade, und ein zweiter Ausgang hätte sie im
        # Rohwert-Modus stillschweigend übersprungen — ausgerechnet dort, wo
        # sie am genauesten sitzen.
        if raw:
            result = query_mod.query_raw_series(
                deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                offset=offset, continuous=continuous,
            )
        else:
            result = query_mod.query_series(
                deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                offset=offset, continuous=continuous, chart_type=chart_type,
                read_cache=read_cache,
            )
        if compare and not raw:
            compare_result = query_mod.query_series(
                deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                offset=offset if compare_mode == "year" else offset - 1,
                continuous=continuous,
                year_over_year=compare_mode == "year",
                chart_type=chart_type,
                read_cache=read_cache,
            )
            result.update(
                compare_points=compare_result["points"],
                compare_window_start=compare_result["window_start"],
                compare_window_end=compare_result["window_end"],
            )
        # Zur Löschung markierte Bereiche — nur auf Anforderung, obwohl sie
        # billig sind (reine Index-Abfrage, kein Zugriff auf Hot Buffer oder
        # Archiv): ein Feld, das fast immer leer ist, gehört nicht in jede
        # Antwort. Hier und nicht in query_series()/query_raw_series(), damit
        # beide Pfade dieselbe Ergänzung bekommen, ohne sie zweimal einzubauen.
        if marked:
            bereiche, gesamt = query_mod.marked_ranges_in_window(
                deps.index, entity_id,
                result["window_start"], result["window_end"], MAX_MARKED_RANGES,
            )
            result["marked_ranges"] = bereiche
            result["marked_total"] = gesamt
        return result

    @router.get("/api/query-multi")
    @locked(limited_multi_entity_ids)
    def api_query_multi(
        entity_ids: str,
        range: str = Query("day", alias="range"),
        offset: int = 0,
        continuous: bool = False,
        year_over_year: bool = False,
        compare: bool = False,
        compare_mode: str = "previous",
        raw: bool = False,
    ) -> dict:
        now = datetime.now(deps.tz)
        ids = limited_multi_entity_ids({"entity_ids": entity_ids})
        for entity_id in ids:
            _validate_entity_id_or_400(entity_id)
        series = []
        window_start = window_end = period_end = None
        is_current = True
        for entity_id in ids:
            entity = deps.index.get_entity(entity_id)
            if raw:
                result = query_mod.query_raw_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    offset=offset, continuous=continuous,
                )
            else:
                result = query_mod.query_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    offset=offset, continuous=continuous, year_over_year=year_over_year,
                )
            entry = {
                "entity_id": entity_id,
                "friendly_name": entity_display_name(
                    entity_id,
                    entity["friendly_name"] if entity else None,
                    entity["custom_name"] if entity else None,
                ),
                "unit": (entity["unit"] if entity else None) or "",
                "decimals": decimals_to_int(entity["decimals"]) if entity else None,
                "display_mode": (entity["display_mode"] if entity else None) or "onoff",
                "aggregation_type": result["aggregation_type"],
                "chart_type": result["chart_type"],
                "points": result["points"],
            }
            if compare and not raw:
                compare_result = query_mod.query_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    offset=offset if compare_mode == "year" else offset - 1,
                    continuous=continuous,
                    year_over_year=compare_mode == "year",
                )
                entry.update(
                    compare_points=compare_result["points"],
                    compare_window_start=compare_result["window_start"],
                    compare_window_end=compare_result["window_end"],
                )
            series.append(entry)
            if window_start is None:
                window_start, window_end = result["window_start"], result["window_end"]
                period_end, is_current = result["period_end"], result["is_current"]
        return {
            "series": series, "window_start": window_start, "window_end": window_end,
            "period_end": period_end, "is_current": is_current,
        }

    @router.get("/api/entity-stats")
    @locked(limited_multi_entity_ids)
    def api_entity_stats(
        entity_ids: str,
        range: str = Query("day", alias="range"),
        continuous: bool = False,
        resolution: str = "raw",
        stats: bool = False,
    ) -> dict:
        """Alles, was eine Werte-Kachel anzeigt, in einer Runde — für mehrere
        Kacheln gleichzeitig.

        Der Browser holte bisher je Kachel die kompletten Rohpunkte des Tages
        und rechnete daraus aktuellen Wert, Alter und Sparkline. Das skaliert
        weder auf viele Kacheln (ein Request je Kachel) noch auf Zeiträume
        jenseits einer Woche (Rohpunkte eines Jahres). Hier gruppiert der
        Client stattdessen nach (Zeitraum, rollierend, Auflösung) und holt je
        Gruppe einen Request; alle Entitäten einer Gruppe teilen sich denselben
        request-lokalen Lese-Cache, sodass eine gemeinsam genutzte Monats-CSV
        nur einmal geparst wird.

        ``stats=false`` (der Normalfall: Kachel ohne Kennzahlen) macht genau
        das, was der bisherige Roh-Request tat — die gebucketete Zweitabfrage
        entsteht nur für Kacheln, die auch wirklich eine Kennzahl zeigen.
        """
        if range not in DASHBOARD_TILE_RANGES:
            raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")
        if resolution not in _SPARKLINE_BUCKET_SECONDS:
            raise HTTPException(status_code=400, detail="Ungültige Sparkline-Auflösung")
        ids = list(dict.fromkeys(limited_multi_entity_ids({"entity_ids": entity_ids})))
        for entity_id in ids:
            _validate_entity_id_or_400(entity_id)

        now = datetime.now(deps.tz)
        read_cache = query_mod.QueryReadCache()
        # Rohwerte liefern zwei Dinge auf einmal: die Sparkline-Punkte und den
        # aktuellen Wert samt Alter. Jenseits einer Woche gibt es sie nicht,
        # dort übernehmen die Buckets die Sparkline — der aktuelle Wert kommt
        # dann aus einem eigenen, kleinen rollierenden 24-Stunden-Fenster.
        raw_capable = range in _RAW_CAPABLE_RANGES
        series = []
        window_start = window_end = None
        for entity_id in ids:
            entity = deps.index.get_entity(entity_id)
            aggregation_type = entity["aggregation_type"] if entity else None
            points: list[dict] = []
            last_value = last_ts = None

            if raw_capable:
                raw = query_mod.query_raw_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    continuous=continuous,
                )
                points = _resample_sparkline(
                    [p for p in raw["points"] if p["value"] is not None], resolution
                )
                if window_start is None:
                    window_start, window_end = raw["window_start"], raw["window_end"]

            aggregates = None
            if stats or not raw_capable:
                bucketed = query_mod.query_series(
                    deps.data_dir, deps.index, entity_id, range, deps.tz, now,
                    continuous=continuous, read_cache=read_cache,
                )
                if stats:
                    aggregates = _tile_aggregates(bucketed)
                if not raw_capable:
                    points = [p for p in bucketed["points"] if p["value"] is not None]
                    if window_start is None:
                        window_start, window_end = bucketed["window_start"], bucketed["window_end"]

            if points and raw_capable:
                last_value, last_ts = points[-1]["value"], points[-1]["ts"]
            else:
                # Bei Monat/Jahr wäre der letzte Bucket-Wert bei einem Zähler
                # ein Tages-/Monatsdelta, kein Zählerstand — der "aktuelle
                # Wert" muss deshalb immer aus Rohwerten kommen. Rollierend,
                # damit die Kachel nicht kurz nach Mitternacht ohne Wert
                # dasteht, nur weil der Kalendertag noch fast leer ist.
                recent = query_mod.query_raw_series(
                    deps.data_dir, deps.index, entity_id, "day", deps.tz, now, continuous=True,
                )
                fresh = [p for p in recent["points"] if p["value"] is not None]
                if fresh:
                    last_value, last_ts = fresh[-1]["value"], fresh[-1]["ts"]

            series.append({
                "entity_id": entity_id,
                "unit": (entity["unit"] if entity else None) or "",
                "decimals": decimals_to_int(entity["decimals"]) if entity else None,
                "aggregation_type": aggregation_type,
                # Nur bei Zählern gesetzt — dort erklärt der Kachel-Tooltip
                # damit, worauf sich Min/Ø/Max beziehen ("Ø je Tag").
                "bucket_label": (
                    _counter_bucket_label(range) if aggregation_type == "counter" else None
                ),
                "last": last_value,
                "last_ts": last_ts,
                "aggregates": aggregates,
                # Nur ts/value — min/max je Bucket trägt die Sparkline nicht,
                # und bei einem Monat wären es sonst 30 überflüssige Paare.
                "points": [{"ts": p["ts"], "value": p["value"]} for p in points],
            })

        return {
            "range": range,
            "continuous": continuous,
            # Womit die Sparkline tatsächlich gezeichnet wird: "raw" ist durch
            # resolution ausgedünnt, "buckets" kommt aus der gebucketen
            # Abfrage und ignoriert resolution. Die Kachel-Einstellungen grauen
            # die Auflösungs-Reihe danach aus, statt die Regel zu kennen.
            "sparkline_source": "raw" if raw_capable else "buckets",
            "window_start": window_start,
            "window_end": window_end,
            "series": series,
        }

    @router.post("/api/query-table")
    @locked(lambda args: args["body"].entity_ids)
    def api_query_table(body: TableQueryRequest) -> dict:
        """Lädt eine komplette Vergleichstabelle mit nur einer Sperr-/HTTP-Runde.

        Alle Spalten teilen denselben Zeitpunkt und denselben request-lokalen
        Lese-Cache. Besonders die laufende Monats-CSV jeder Entität wird damit
        nur einmal geparst, auch wenn Tag, Monat und Jahr nebeneinander stehen.
        """
        ids = list(dict.fromkeys(body.entity_ids))
        for entity_id in ids:
            _validate_entity_id_or_400(entity_id)
        for column in body.columns:
            if column.range_key not in query_mod.RANGE_KEYS:
                raise HTTPException(status_code=400, detail="Ungültiger Tabellenzeitraum")

        now = datetime.now(deps.tz)
        read_cache = query_mod.QueryReadCache()
        column_results = []
        for column in body.columns:
            series = []
            window_start = window_end = period_end = elapsed_seconds = None
            is_current = True
            for entity_id in ids:
                entity = deps.index.get_entity(entity_id)
                result = query_mod.query_series(
                    deps.data_dir,
                    deps.index,
                    entity_id,
                    column.range_key,
                    deps.tz,
                    now,
                    offset=column.offset,
                    year_over_year=column.year_over_year,
                    read_cache=read_cache,
                    same_elapsed=column.same_elapsed,
                )
                series.append({
                    "entity_id": entity_id,
                    "friendly_name": entity_display_name(
                        entity_id,
                        entity["friendly_name"] if entity else None,
                        entity["custom_name"] if entity else None,
                    ),
                    "unit": (entity["unit"] if entity else None) or "",
                    "decimals": decimals_to_int(entity["decimals"]) if entity else None,
                    "display_mode": (entity["display_mode"] if entity else None) or "onoff",
                    "aggregation_type": result["aggregation_type"],
                    # Tabellen brauchen nur einen Skalar je Zelle. Die teils
                    # tausenden Chart-Punkte nicht als JSON zum Browser zu
                    # schicken spart Transfer und dortige Reduktion.
                    "aggregates": _table_aggregates(result),
                    # Fairer Vergleichswert für same_elapsed-Spalten (Vortag
                    # bis zur selben Uhrzeit statt der ganze Vortag) — None,
                    # wenn kein fairer Vergleich angefordert wurde.
                    "comparison_aggregates": _table_comparison_aggregates(result),
                })
                if window_start is None and "window_start" in result:
                    window_start = result["window_start"]
                    window_end = result["window_end"]
                    period_end = result["period_end"]
                    is_current = result["is_current"]
                    elapsed_seconds = result.get("elapsed_seconds")
            column_results.append({
                "series": series,
                "window_start": window_start,
                "window_end": window_end,
                "period_end": period_end,
                "is_current": is_current,
                "elapsed_seconds": elapsed_seconds,
            })
        return {"columns": column_results}

    return router
