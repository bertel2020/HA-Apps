"""Entitätsweise serialisierte, crash-feste und idempotente Live-Aufnahme."""

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

from . import hotbuffer, rollup, rotate
from .coordinator import StorageCoordinator
from .index import Index, should_accept_value, should_accept_write
from ..logging_setup import log_rate_limited
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
_PENDING_WARNING_SECONDS = 5 * 60


def _event_exists(
    data_dir: Path, entity_id: str, ts: float, event_id: str, tz: ZoneInfo
) -> bool:
    hot_path = hotbuffer.hot_path(data_dir, entity_id, ts, tz)
    if hotbuffer.contains_event_id(hot_path, event_id):
        return True

    month = hotbuffer.month_key(ts, tz)
    archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month}.parquet"
    if not archive_path.exists():
        return False
    schema = pq.read_schema(archive_path)
    if "event_id" not in schema.names:
        return False
    return pq.read_table(
        archive_path,
        columns=["event_id"],
        filters=[("event_id", "=", event_id)],
    ).num_rows > 0


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
    if hotbuffer.contains_timestamp(hot_path, ts):
        return True

    month = hotbuffer.month_key(ts, tz)
    archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month}.parquet"
    if not archive_path.exists():
        return False
    return pq.read_table(
        archive_path,
        columns=["ts"],
        filters=[("ts", "=", ts)],
    ).num_rows > 0


class IngestionService:
    """Entitätsweise Writer-Grenze für Hot-Datei, Rotation und Metadaten."""

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
        self._coordinator = coordinator or StorageCoordinator()
        self._completion_lock = threading.Lock()
        self._completions_since_prune = 0

    def _complete(self, event: IngestEvent, *, recorded: bool) -> None:
        self._index.complete_ingest_event(
            event.event_id,
            event.entity_id,
            event.ts,
            recorded=recorded,
            value=event.value if recorded else None,
        )
        should_prune = False
        with self._completion_lock:
            self._completions_since_prune += 1
            if self._completions_since_prune >= _PRUNE_EVERY_COMPLETIONS:
                self._completions_since_prune = 0
                should_prune = True
        if should_prune:
            pruned = self._index.prune_ingested_events(
                time.time() - _IDEMPOTENCY_RETENTION_SECONDS
            )
            logger.debug(
                "Ingest-Ledger bereinigt · event=ingest_ledger_pruned removed=%d retention_days=7",
                pruned,
            )

    def recover_pending(self) -> int:
        """Schließt nach einem Prozessabbruch bereits persistierte Events ab."""
        recovered = 0
        claims = self._index.list_processing_ingest_events()
        now = time.time()
        oldest_age = max(
            (now - float(claim.get("created_at") or now) for claim in claims),
            default=0.0,
        )
        old_claims = [
            claim for claim in claims
            if now - float(claim.get("created_at") or now) >= _PENDING_WARNING_SECONDS
        ]
        if old_claims:
            logger.warning(
                "Alte offene Ingest-Claims erkannt · event=ingest_pending_old "
                "claims=%d entities=%d oldest_age_seconds=%.1f",
                len(old_claims),
                len({claim["entity_id"] for claim in old_claims}),
                oldest_age,
            )
        for claim in claims:
            with self._coordinator.entity(claim["entity_id"]):
                try:
                    exists = _event_exists(
                        self._data_dir,
                        claim["entity_id"],
                        claim["ts"],
                        claim["event_id"],
                        self._tz,
                    )
                    if exists:
                        self._index.complete_ingest_event(
                            claim["event_id"], claim["entity_id"], claim["ts"], recorded=True
                        )
                        recovered += 1
                except Exception:
                    logger.exception(
                        "Ingest-Recovery fehlgeschlagen · event=ingest_recovery_failed "
                        "entity_id=%s event_id=%s",
                        claim["entity_id"],
                        claim["event_id"][:12],
                    )
        pruned = self._index.prune_ingested_events(
            time.time() - _IDEMPOTENCY_RETENTION_SECONDS
        )
        logger.debug(
            "Ingest-Recovery geprüft · event=ingest_recovery_checked pending=%d "
            "recovered=%d unresolved=%d pruned=%d oldest_age_seconds=%.1f",
            len(claims),
            recovered,
            len(claims) - recovered,
            pruned,
            oldest_age,
        )
        if recovered:
            logger.info(
                "Ingest-Recovery abgeschlossen · event=ingest_recovery_completed "
                "recovered=%d entities=%d oldest_age_seconds=%.1f",
                recovered,
                len({claim["entity_id"] for claim in claims}),
                oldest_age,
            )
        return recovered

    def ingest(self, event: IngestEvent) -> str:
        """Liefert ``written``, ``filtered``, ``duplicate`` oder ``recovered``."""
        with self._coordinator.entity(event.entity_id):
            return self._ingest_entity_locked(event)

    def _ingest_entity_locked(self, event: IngestEvent) -> str:
        validate_entity_id(event.entity_id)
        self._index.get_or_create_entity(
            event.entity_id,
            event.domain,
            event.state_class,
            event.unit,
            event.friendly_name,
            on_type_change=lambda _old, new, hourly_rollup: rollup.rebuild_entity_rollups(
                self._data_dir, event.entity_id, new, self._tz,
                hourly_rollup=hourly_rollup,
            ),
        )
        claim = self._index.claim_ingest_event(event.event_id, event.entity_id, event.ts)
        if claim["entity_id"] != event.entity_id or claim["ts"] != event.ts:
            raise ValueError("Event-ID wurde bereits für ein anderes Event verwendet")
        if claim["status"] == "done":
            return "duplicate"

        # Nur ein bereits vorhandener offener Claim kann aus dem Crash-Fenster
        # stammen. Ein frisch eingefügter Claim hat garantiert noch keine von
        # diesem Service geschriebene Dateizeile und darf den stetig wachsenden
        # Hot Buffer deshalb nicht vollständig durchsuchen.
        if not claim["is_new"] and _event_exists(
            self._data_dir, event.entity_id, event.ts, event.event_id, self._tz
        ):
            self._complete(event, recorded=True)
            return "recovered"

        # Eine neue Event-ID darf keinen bereits vorhandenen Messpunkt erneut
        # anhaengen. Für den normalen monotonen Live-Pfad beendet
        # _timestamp_exists() die Prüfung ohne Datei-I/O.
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

        if not should_accept_write(
            entity["resolution"],
            entity["last_ts"],
            event.ts,
        ):
            self._complete(event, recorded=False)
            return "skipped"

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
            log_rate_limited(
                logger,
                logging.WARNING,
                f"counter_decrease:{event.entity_id}",
                "Zählerrückgang gespeichert · event=counter_decrease entity_id=%s "
                "previous_value=%s value=%s timestamp=%s",
                event.entity_id,
                entity["last_value"],
                event.value,
                event.ts,
                interval_seconds=15 * 60,
            )
        return "written"
