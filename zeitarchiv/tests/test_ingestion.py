"""Tests für idempotente und crash-feste Live-Aufnahme."""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import hotbuffer, ingestion as ingestion_mod, rollup
    from app.storage.index import Index
    from app.storage.ingestion import IngestEvent, IngestionService, legacy_event_id
    from app.logging_setup import configure_logging, local_log_lines

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False


def _event(event_id: str = "event-1") -> IngestEvent:
    return IngestEvent(
        event_id=event_id,
        entity_id="sensor.temp",
        domain="sensor",
        ts=1722470400.0,
        value=21.4,
        state_class="measurement",
        unit="°C",
    )


def test_retry_does_not_append_duplicate() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        assert service.ingest(_event()) == "written"
        assert service.ingest(_event()) == "duplicate"

        records = hotbuffer.read_records(
            hotbuffer.hot_path(tmp, "sensor.temp", _event().ts, ZoneInfo("UTC"))
        )
        assert records == [(_event().ts, 21.4, "event-1")]
        assert index.get_entity("sensor.temp")["row_count"] == 1
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_state_class_change_rebuilds_rollups_without_changing_raw_archive() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-type-change-"))
    index = Index(tmp / "index.sqlite")
    tz = ZoneInfo("UTC")
    entity_id = "sensor.energy"
    try:
        index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-01.parquet"
        archive_table = pa.table(
            {"ts": [1704067200.0, 1704153600.0], "value": [100.0, 108.0]}
        )
        pq.write_table(archive_table, archive_path, compression="zstd")
        raw_before = archive_path.read_bytes()
        rollup.append_completed_month(
            tmp, entity_id, "standard", archive_table, 2024, 1, tz
        )
        assert rollup.rollup_path(tmp, entity_id, "stunde").exists()

        service = IngestionService(tmp, index, tz)
        result = service.ingest(
            IngestEvent(
                event_id="type-change",
                entity_id=entity_id,
                domain="sensor",
                ts=1706745600.0,
                value=110.0,
                state_class="total_increasing",
                unit="kWh",
            )
        )

        assert result == "written"
        assert index.get_entity(entity_id)["aggregation_type"] == "counter"
        assert not rollup.rollup_path(tmp, entity_id, "stunde").exists()
        assert rollup.rollup_path(tmp, entity_id, "tag").exists()
        assert archive_path.read_bytes() == raw_before
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_fresh_monotonic_events_do_not_scan_existing_month_file() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-no-scan-"))
    index = Index(tmp / "index.sqlite")
    original_event_exists = ingestion_mod._event_exists
    original_contains_timestamp = hotbuffer.contains_timestamp

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("Frisches monotones Event darf die Monatsdatei nicht durchsuchen")

    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        ingestion_mod._event_exists = unexpected_scan
        hotbuffer.contains_timestamp = unexpected_scan
        first = _event("event-1")
        second = IngestEvent(
            event_id="event-2",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts + 1,
            value=22.0,
            state_class=first.state_class,
            unit=first.unit,
        )

        assert service.ingest(first) == "written"
        assert service.ingest(second) == "written"
    finally:
        ingestion_mod._event_exists = original_event_exists
        hotbuffer.contains_timestamp = original_contains_timestamp
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_different_entities_can_ingest_while_one_file_write_is_blocked() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-parallel-"))
    index = Index(tmp / "index.sqlite")
    service = IngestionService(tmp, index, ZoneInfo("UTC"))
    original_append = hotbuffer.append
    first_append_started = threading.Event()
    release_first_append = threading.Event()
    second_finished = threading.Event()
    results: dict[str, str] = {}

    def blocking_append(data_dir, entity_id, ts, value, tz, event_id=None):
        if entity_id == "sensor.a":
            first_append_started.set()
            if not release_first_append.wait(timeout=5):
                raise TimeoutError("Erster Test-Write wurde nicht freigegeben")
        return original_append(data_dir, entity_id, ts, value, tz, event_id=event_id)

    event_a = IngestEvent("event-a", "sensor.a", "sensor", 1722470400.0, 1.0)
    event_b = IngestEvent("event-b", "sensor.b", "sensor", 1722470400.0, 2.0)

    def ingest_a() -> None:
        results["a"] = service.ingest(event_a)

    def ingest_b() -> None:
        results["b"] = service.ingest(event_b)
        second_finished.set()

    try:
        hotbuffer.append = blocking_append
        first_thread = threading.Thread(target=ingest_a)
        second_thread = threading.Thread(target=ingest_b)
        first_thread.start()
        assert first_append_started.wait(timeout=5)
        second_thread.start()

        assert second_finished.wait(timeout=1), "sensor.a blockiert weiterhin sensor.b"
        assert results["b"] == "written"

        release_first_append.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert results["a"] == "written"
    finally:
        release_first_append.set()
        hotbuffer.append = original_append
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_same_measurement_with_new_event_id_is_not_appended() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        assert service.ingest(_event("event-1")) == "written"
        assert service.ingest(_event("event-2")) == "duplicate"

        records = hotbuffer.read_records(
            hotbuffer.hot_path(tmp, "sensor.temp", _event().ts, ZoneInfo("UTC"))
        )
        assert records == [(_event().ts, 21.4, "event-1")]
        assert index.get_entity("sensor.temp")["row_count"] == 1
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_same_timestamp_with_different_value_is_not_appended() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        first = _event("event-1")
        changed = IngestEvent(
            event_id="event-2",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts,
            value=22.0,
            state_class=first.state_class,
            unit=first.unit,
        )
        assert service.ingest(first) == "written"
        assert service.ingest(changed) == "duplicate"
        assert index.get_entity("sensor.temp")["row_count"] == 1
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_existing_timestamp_in_month_archive_is_not_appended() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        first = _event("event-1")
        next_month = IngestEvent(
            event_id="event-2",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts + 32 * 24 * 60 * 60,
            value=22.0,
            state_class=first.state_class,
            unit=first.unit,
        )
        duplicate = IngestEvent(
            event_id="event-3",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts,
            value=99.0,
            state_class=first.state_class,
            unit=first.unit,
        )

        assert service.ingest(first) == "written"
        assert service.ingest(next_month) == "written"
        assert service.ingest(duplicate) == "duplicate"
        assert index.get_entity("sensor.temp")["row_count"] == 2
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_same_value_at_different_timestamp_is_appended() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        first = _event("event-1")
        second = IngestEvent(
            event_id="event-2",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts + 1,
            value=first.value,
            state_class=first.state_class,
            unit=first.unit,
        )
        assert service.ingest(first) == "written"
        # Testet Anhängen-nach-Zeitstempel unabhängig vom Wertänderungsfilter
        # (seit 0.75.0 für neu erkannte Entitäten standardmäßig aktiv) —
        # ohne dies würde der zweite, wertgleiche Event gefiltert statt
        # geschrieben.
        index.set_config(first.entity_id, value_filter="off")
        assert service.ingest(second) == "written"
        assert index.get_entity("sensor.temp")["row_count"] == 2
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_configured_resolution_skips_events_inside_interval() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        first = _event("event-1")
        assert service.ingest(first) == "written"
        index.set_config(first.entity_id, resolution="15min")

        inside = IngestEvent(
            event_id="event-2",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts + 899,
            value=22.0,
            state_class=first.state_class,
            unit=first.unit,
        )
        boundary = IngestEvent(
            event_id="event-3",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts + 900,
            value=23.0,
            state_class=first.state_class,
            unit=first.unit,
        )

        assert service.ingest(inside) == "skipped"
        assert service.ingest(boundary) == "written"
        entity = index.get_entity(first.entity_id)
        assert entity["row_count"] == 2
        assert entity["last_ts"] == boundary.ts
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolution_change_keeps_existing_rows_and_uses_last_stored_timestamp() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        first = _event("event-1")
        assert service.ingest(first) == "written"
        index.set_config(first.entity_id, resolution="15min")

        after_15_minutes = IngestEvent(
            event_id="event-2",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=first.ts + 900,
            value=22.0,
            state_class=first.state_class,
            unit=first.unit,
        )
        assert service.ingest(after_15_minutes) == "written"

        index.set_config(first.entity_id, resolution="1h")
        too_early = IngestEvent(
            event_id="event-3",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=after_15_minutes.ts + 3599,
            value=23.0,
            state_class=first.state_class,
            unit=first.unit,
        )
        after_one_hour = IngestEvent(
            event_id="event-4",
            entity_id=first.entity_id,
            domain=first.domain,
            ts=after_15_minutes.ts + 3600,
            value=24.0,
            state_class=first.state_class,
            unit=first.unit,
        )

        assert service.ingest(too_early) == "skipped"
        assert index.get_entity(first.entity_id)["row_count"] == 2
        assert service.ingest(after_one_hour) == "written"
        assert index.get_entity(first.entity_id)["row_count"] == 3
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_retry_recovers_crash_after_file_append() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    original_append = hotbuffer.append

    def append_then_crash(*args, **kwargs) -> None:
        original_append(*args, **kwargs)
        raise RuntimeError("simulierter Prozessabbruch vor SQLite-Abschluss")

    try:
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        hotbuffer.append = append_then_crash
        try:
            service.ingest(_event("crash-event"))
            raise AssertionError("simulierter Abbruch wurde nicht ausgelöst")
        except RuntimeError:
            pass
        finally:
            hotbuffer.append = original_append

        assert service.ingest(_event("crash-event")) == "recovered"
        assert service.ingest(_event("crash-event")) == "duplicate"
        assert index.get_entity("sensor.temp")["row_count"] == 1
        path = hotbuffer.hot_path(tmp, "sensor.temp", _event().ts, ZoneInfo("UTC"))
        assert len(hotbuffer.read_records(path)) == 1
    finally:
        hotbuffer.append = original_append
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_startup_reconciles_persisted_open_claim() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-"))
    index = Index(tmp / "index.sqlite")
    event = _event("startup-event")
    try:
        index.get_or_create_entity(event.entity_id, event.domain, event.state_class, event.unit)
        index.claim_ingest_event(event.event_id, event.entity_id, event.ts)
        hotbuffer.append(
            tmp,
            event.entity_id,
            event.ts,
            event.value,
            ZoneInfo("UTC"),
            event_id=event.event_id,
        )

        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        assert service.recover_pending() == 1
        assert service.ingest(event) == "duplicate"
        assert index.get_entity(event.entity_id)["row_count"] == 1
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_recovery_warns_about_old_processing_claim_with_age() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ingestion-old-claim-"))
    index = Index(tmp / "index.sqlite")
    event = _event("old-open-claim")
    try:
        configure_logging("debug", "off")
        index.get_or_create_entity(event.entity_id, event.domain, event.state_class, event.unit)
        index.claim_ingest_event(event.event_id, event.entity_id, event.ts)
        with index._lock, index._conn:
            index._conn.execute(
                "UPDATE ingested_events SET created_at = ? WHERE event_id = ?",
                (time.time() - 600, event.event_id),
            )
        service = IngestionService(tmp, index, ZoneInfo("UTC"))
        assert service.recover_pending() == 0
        lines = local_log_lines(search="event=ingest_pending_old", limit=20)
        assert any("claims=1" in line and "oldest_age_seconds=" in line for line in lines)
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_legacy_event_id_is_stable_and_content_sensitive() -> None:
    first = {"entity_id": "sensor.temp", "ts": 1.0, "value": 21.4}
    reordered = {"value": 21.4, "ts": 1.0, "entity_id": "sensor.temp"}
    changed = {"entity_id": "sensor.temp", "ts": 1.0, "value": 21.5}
    assert legacy_event_id(first) == legacy_event_id(reordered)
    assert legacy_event_id(first) != legacy_event_id(changed)


def _run_all() -> None:
    if not _PYARROW_AVAILABLE:
        print("übersprungen: pyarrow nicht installiert (siehe requirements.txt)")
        return
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
