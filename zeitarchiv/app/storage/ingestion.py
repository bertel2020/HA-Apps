"""Serialisierte, crash-feste und idempotente Live-Aufnahme von Events."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from . import hotbuffer, rotate
from .coordinator import StorageCoordinator
from .index import Index, should_accept_value
from .paths import entity_dir, validate_entity_id


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestEvent:
    event_id: str
    entity_id: str
    domain: str
    ts: float
    value: float
    state_class: str | None = None
    unit: str | None = None
    friendly_name: str | None = None


def legacy_event_id(event: dict) -> str:
    """Deterministische Übergangs-ID für alte Integrationen ohne event_id."""
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "legacy-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_IDEMPOTENCY_RETENTION_SECONDS = 7 * 24 * 60 * 60
_PRUNE_EVERY_COMPLETIONS = 10_000


def _event_exists(
    data_dir: Path, entity_id: str, ts: float, event_id: str, tz: ZoneInfo
) -> bool:
    hot_path = hotbuffer.hot_path(data_dir, entity_id, ts, tz)
    if any(row_event_id == event_id for _ts, _value, row_event_id in hotbuffer.read_records(hot_path)):
        return True

    month = hotbuffer.month_key(ts, tz)
    archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month}.parquet"
    if not archive_path.exists():
        return False
    schema = pq.read_schema(archive_path)
    if "event_id" not in schema.names:
        return False
    return event_id in set(
        value for value in pq.read_table(archive_path, columns=["event_id"]).column("event_id").to_pylist() if value
    )


def _timestamp_exists(
    data_dir: Path,
    entity_id: str,
    ts: float,
    last_ts: float | None,
    tz: ZoneInfo,
) -> bool:
    """Prueft, ob fuer die Entitaet bereits ein Wert zum Zeitstempel liegt."""
    # Der normale Live-Pfad liefert steigende Zeitstempel. Solange der neue
    # Zeitstempel hinter dem Indexmaximum liegt, kann er noch nicht existieren
    # und wir vermeiden das Lesen einer stetig wachsenden Monatsdatei.
    if last_ts is None or ts > last_ts:
        return False

    hot_path = hotbuffer.hot_path(data_dir, entity_id, ts, tz)
    if any(row_ts == ts for row_ts, _value, _event_id in hotbuffer.read_records(hot_path)):
        return True

    month = hotbuffer.month_key(ts, tz)
    archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month}.parquet"
    if not archive_path.exists():
        return False
    return ts in set(pq.read_table(archive_path, columns=["ts"]).column("ts").to_pylist())


class IngestionService:
    """Single-Writer-Grenze für Hot-Datei, Rotation und Index-Metadaten."""

    def __init__(
        self,
        data_dir: Path,
        index: Index,
        tz: ZoneInfo,
        coordinator: StorageCoordinator | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._index = index
        self._tz = tz
        self._lock = threading.Lock()
        self._coordinator = coordinator
        self._completions_since_prune = 0

    def _complete(self, event: IngestEvent, *, recorded: bool) -> None:
        self._index.complete_ingest_event(
            event.event_id,
            event.entity_id,
            event.ts,
            recorded=recorded,
            value=event.value if recorded else None,
        )
        self._completions_since_prune += 1
        if self._completions_since_prune >= _PRUNE_EVERY_COMPLETIONS:
            self._index.prune_ingested_events(
                time.time() - _IDEMPOTENCY_RETENTION_SECONDS
            )
            self._completions_since_prune = 0

    def recover_pending(self) -> int:
        """Schließt nach einem Prozessabbruch bereits persistierte Events ab."""
        recovered = 0
        with self._lock:
            for claim in self._index.list_processing_ingest_events():
                if _event_exists(
                    self._data_dir,
                    claim["entity_id"],
                    claim["ts"],
                    claim["event_id"],
                    self._tz,
                ):
                    self._index.complete_ingest_event(
                        claim["event_id"], claim["entity_id"], claim["ts"], recorded=True
                    )
                    recovered += 1
            self._index.prune_ingested_events(
                time.time() - _IDEMPOTENCY_RETENTION_SECONDS
            )
        return recovered

    def ingest(self, event: IngestEvent) -> str:
        """Liefert ``written``, ``filtered``, ``duplicate`` oder ``recovered``."""
        if self._coordinator is not None:
            with self._coordinator.entity(event.entity_id):
                return self._ingest_locked(event)
        return self._ingest_locked(event)

    def _ingest_locked(self, event: IngestEvent) -> str:
        with self._lock:
            validate_entity_id(event.entity_id)
            self._index.get_or_create_entity(
                event.entity_id,
                event.domain,
                event.state_class,
                event.unit,
                event.friendly_name,
            )
            claim = self._index.claim_ingest_event(event.event_id, event.entity_id, event.ts)
            if claim["entity_id"] != event.entity_id or claim["ts"] != event.ts:
                raise ValueError("Event-ID wurde bereits für ein anderes Event verwendet")
            if claim["status"] == "done":
                return "duplicate"

            # Crash-Fenster: Dateizeile war bereits dauerhaft geschrieben, aber
            # der gemeinsame SQLite-Abschluss noch nicht committed.
            if _event_exists(
                self._data_dir, event.entity_id, event.ts, event.event_id, self._tz
            ):
                self._complete(event, recorded=True)
                return "recovered"

            # Eine neue Event-ID darf keinen bereits vorhandenen Messpunkt
            # erneut anhaengen (z. B. nach einem Neustart der Integration).
            entity = self._index.get_entity(event.entity_id)
            if _timestamp_exists(
                self._data_dir,
                event.entity_id,
                event.ts,
                entity["last_ts"],
                self._tz,
            ):
                self._complete(event, recorded=False)
                return "duplicate"

            if not should_accept_value(
                entity["value_filter"],
                entity["decimals"],
                entity["last_value"],
                entity["last_ts"],
                event.value,
                event.ts,
            ):
                self._complete(event, recorded=False)
                return "filtered"

            counter_decrease = (
                entity["state_class"] == "total_increasing"
                and entity["last_value"] is not None
                and entity["last_ts"] is not None
                and event.ts > entity["last_ts"]
                and event.value < entity["last_value"]
            )

            rotate.rotate_if_needed(
                self._data_dir, event.entity_id, event.ts, self._index, self._tz
            )
            hotbuffer.append(
                self._data_dir,
                event.entity_id,
                event.ts,
                event.value,
                self._tz,
                event_id=event.event_id,
            )
            self._complete(event, recorded=True)
            if counter_decrease:
                logger.warning(
                    "Zählerrückgang gespeichert · Entität=%s · Vorwert=%s · Wert=%s · Zeitstempel=%s",
                    event.entity_id,
                    entity["last_value"],
                    event.value,
                    event.ts,
                )
            return "written"
