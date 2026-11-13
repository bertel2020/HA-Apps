"""Markierte Werte: Verdichten, Duplikat-Vorschau und Rückgängig (Review 2026-11-13, H1–H4)."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from app.storage import cleanup, hotbuffer  # noqa: E402
from app.storage.index import Index, filter_deleted_occurrences  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0) -> float:
    return datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-test-"))
    index = Index(tmp / "index.sqlite")
    try:
        yield tmp, index
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_compaction_drops_marked_rows_instead_of_averaging_them_in(env) -> None:
    tmp, index = env
    entity_id = "sensor.temp"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    base = _ts(2024, 7, 5, 8) + 1
    t0, t1, t2 = base, base + 10, base + 20
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    path = archive_dir / "2024-07.parquet"
    pq.write_table(pa.table({"ts": [t0, t1, t2], "value": [20.0, 9999.0, 24.0]}), path)
    index.add_row_count(entity_id, 3)
    index.set_first_ts(entity_id, t0)
    cleanup.soft_delete(index, entity_id, [t1])

    now = datetime(2024, 8, 15, tzinfo=TZ)
    preview = cleanup.preview_compact_raw_values(tmp, index, entity_id, t0, t2, "5min", TZ, now=now)
    assert preview["rows_before"] == 3 and preview["rows_after"] == 1

    result = cleanup.compact_raw_values(tmp, index, entity_id, t0, t2, "5min", TZ, now=now)

    assert result["rows_before"] == 3
    rows = pq.read_table(path).to_pylist()
    assert len(rows) == 1
    assert rows[0]["value"] == 22.0  # Ø aus 20 und 24, der Ausreißer ist nicht enthalten
    assert rows[0]["max_value"] == 24.0
    assert index.get_deleted_points_count() == 0
    # row_count: 3 Zeilen in der Datei, 1 danach
    assert index.get_entity(entity_id)["row_count"] == 1


def test_counter_compaction_does_not_keep_marked_reset_as_raw_row(env) -> None:
    tmp, index = env
    entity_id = "sensor.zaehler"
    index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    base = _ts(2024, 7, 5, 8) + 1
    ts = [base, base + 10, base + 20]
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    path = archive_dir / "2024-07.parquet"
    pq.write_table(pa.table({"ts": ts, "value": [100.0, 0.0, 101.0]}), path)
    index.add_row_count(entity_id, 3)
    index.set_first_ts(entity_id, ts[0])
    cleanup.soft_delete(index, entity_id, [ts[1]])

    cleanup.compact_raw_values(
        tmp, index, entity_id, ts[0], ts[2], "5min", TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
    )

    values = [r["value"] for r in pq.read_table(path).to_pylist()]
    assert 0.0 not in values
    assert values == [101.0]


def test_archive_rows_are_ordered_by_value_within_a_timestamp(env) -> None:
    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    ts = _ts(2024, 7, 5, 8)
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    # Dateireihenfolge: 5 vor 3
    pq.write_table(pa.table({"ts": [ts, ts, ts + 60], "value": [5.0, 3.0, 4.0]}), archive_dir / "2024-07.parquet")
    now = datetime(2024, 8, 15, tzinfo=TZ)

    rows = list(cleanup.iter_raw_rows(tmp, index, entity_id, ts - 1, ts + 100, TZ, now=now))
    assert rows == [(ts, 3.0), (ts, 5.0), (ts + 60, 4.0)]

    # Vorschau und Wirkung stimmen überein: die Vorschau nennt die 3, und nach dem
    # Markieren ist genau die 3 aus der Anzeige verschwunden.
    to_delete = cleanup.duplicate_rows_to_delete(rows)
    assert to_delete == [(ts, 3.0)]
    cleanup.soft_delete(index, entity_id, [t for t, _ in to_delete])
    after = list(cleanup.iter_raw_rows(tmp, index, entity_id, ts - 1, ts + 100, TZ, now=now))
    assert after == [(ts, 5.0), (ts + 60, 4.0)]


def test_hot_purge_removes_the_same_occurrence_the_view_hides(env) -> None:
    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    ts = _ts(2024, 8, 10, 8)
    for value in (5.0, 3.0):  # Schreibreihenfolge: 5 vor 3
        hotbuffer.append(tmp, entity_id, ts, value, TZ)
        index.record_write(entity_id, ts)
    cleanup.soft_delete(index, entity_id, [ts])

    visible = list(cleanup.iter_raw_rows(tmp, index, entity_id, ts - 1, ts + 1, TZ, now=now))
    assert visible == [(ts, 5.0)]

    assert cleanup.purge_hot_buffer(tmp, index, TZ, now=now) == 1
    remaining = hotbuffer.read_rows(hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ))
    assert remaining == [(ts, 5.0)]  # die 3 ist weg, nicht die 5


def test_filter_hides_first_occurrences_in_value_order() -> None:
    rows = [(1.0, 3.0), (1.0, 5.0)]
    assert filter_deleted_occurrences(rows, {1.0: 1}) == [(1.0, 5.0)]


def test_undo_batch_leaves_a_newer_batch_alone_and_is_idempotent(env) -> None:
    _tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    cleanup.soft_delete(index, entity_id, [10.0, 11.0], deleted_at=1000.0, source="duplicates")
    cleanup.soft_delete(index, entity_id, [20.0], deleted_at=2000.0, source="counter_rule")

    assert index.last_deleted_at(entity_id) == 2000.0
    assert cleanup.undo_delete_batch(index, entity_id, 1000.0) == 2
    assert index.get_deleted_counts_for_entity(entity_id) == {20.0: 1}  # fremde Charge bleibt
    # zweiter Aufruf nimmt nichts weiter zurück
    assert cleanup.undo_delete_batch(index, entity_id, 1000.0) == 0
    assert index.get_deleted_counts_for_entity(entity_id) == {20.0: 1}


def test_undo_duplicate_removal_uses_batch_of_the_bulk_run(env) -> None:
    from contextlib import contextmanager

    from app import data_quality

    _tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    cleanup.soft_delete(index, entity_id, [10.0], deleted_at=1000.0, source="duplicates")
    cleanup.soft_delete(index, entity_id, [20.0], deleted_at=2000.0, source="counter_rule")

    @contextmanager
    def lock(_entity_id):
        yield

    restored = data_quality.undo_duplicate_removal(index, [(entity_id, 1000.0), (entity_id, 1000.0)], lock)
    assert restored == 1
    assert index.get_deleted_counts_for_entity(entity_id) == {20.0: 1}
