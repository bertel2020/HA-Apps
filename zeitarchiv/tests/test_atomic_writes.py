"""Tests für das atomare Schreiben der Speicherschicht (app/storage/atomic.py).

Ein Absturz mitten im Schreiben darf weder die Hot-CSV des laufenden Monats noch ein
bereits archiviertes Monats-Parquet beschädigen.
"""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import atomic, backup, hotbuffer, retention, rotate
    from app.storage.index import Index
    from app.storage.ingestion import IngestEvent, IngestionService

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _PYARROW_AVAILABLE, reason="pyarrow nicht installiert")

UTC = ZoneInfo("UTC")
TZ = ZoneInfo("Europe/Berlin")


def test_failed_atomic_write_keeps_the_original_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "datei.csv"
    target.write_text("alt\n", encoding="utf-8")

    def exploding_lines():
        yield "neu\n"
        raise OSError("Platte voll")

    with pytest.raises(OSError):
        atomic.write_text_atomically(target, exploding_lines())

    assert target.read_text(encoding="utf-8") == "alt\n"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_replaces_the_target(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "datei.csv"
    atomic.write_text_atomically(target, ["a\n", "b\n"])
    assert target.read_text(encoding="utf-8") == "a\nb\n"
    assert not atomic.temp_path(target).exists()


def test_write_records_keeps_the_file_when_writing_fails(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "hot" / "sensor.temp-2024-08.csv"
    path.parent.mkdir()
    hotbuffer.write_records(path, [(1.0, 10.0, "a", None, None), (2.0, 11.0, "b", 10.5, 11.5)])
    before = path.read_bytes()

    def broken_format(*_args, **_kwargs):
        raise RuntimeError("Absturz mitten im Schreiben")

    monkeypatch.setattr(hotbuffer, "_format_row", broken_format)
    with pytest.raises(RuntimeError):
        hotbuffer.write_records(path, [(3.0, 12.0, "c", None, None)])

    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def _index_with_july_hot_file(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    entity_id = "sensor.pv_ertrag_gesamt"
    index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    for ts, value in [(1719878400.0, 100.0), (1719964800.0, 105.5)]:
        hotbuffer.append(tmp_path, entity_id, ts, value, UTC, event_id=f"e-{int(ts)}")
        index.record_write(entity_id, ts)
    return index, entity_id


def test_rotation_keeps_hot_csv_and_old_archive_when_the_parquet_write_fails(
    tmp_path: Path, monkeypatch
) -> None:
    index, entity_id = _index_with_july_hot_file(tmp_path)
    try:
        hot_csv = tmp_path / "hot" / f"{entity_id}-2024-07.csv"
        archive_dir = tmp_path / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive = archive_dir / "2024-07.parquet"
        old_table = pa.table({"ts": [1719800000.0], "value": [99.0]})
        pq.write_table(old_table, archive)
        old_bytes = archive.read_bytes()

        def failing_write(table, where, *args, **kwargs):
            Path(where).write_bytes(b"halbe Datei")
            raise OSError("Platte voll")

        monkeypatch.setattr(rotate.pq, "write_table", failing_write)
        with pytest.raises(OSError):
            rotate.rotate_month_file(hot_csv, tmp_path, entity_id, index, UTC)

        assert hot_csv.exists(), "Die Hot-CSV darf erst nach dem vollständigen Archiv verschwinden"
        assert archive.read_bytes() == old_bytes
        assert sorted(p.name for p in archive_dir.iterdir()) == ["2024-07.parquet"]
    finally:
        index.close()


def test_ingest_survives_a_failing_rotation_and_writes_to_the_new_month(
    tmp_path: Path, monkeypatch
) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, UTC)
        july_ts, august_ts = 1719878400.0, 1722470400.0

        def event(event_id: str, ts: float, value: float) -> IngestEvent:
            return IngestEvent(
                event_id=event_id, entity_id="sensor.temp", domain="sensor",
                ts=ts, value=value, state_class="measurement", unit="°C",
            )

        assert service.ingest(event("july", july_ts, 20.0)) == "written"

        def failing_rotation(*_args, **_kwargs):
            raise ValueError("Archiv beschädigt")

        monkeypatch.setattr(rotate, "rotate_month_file", failing_rotation)
        assert service.ingest(event("august", august_ts, 21.0)) == "written"

        july_csv = hotbuffer.hot_path(tmp_path, "sensor.temp", july_ts, UTC)
        august_csv = hotbuffer.hot_path(tmp_path, "sensor.temp", august_ts, UTC)
        assert july_csv.exists(), "Die Vormonatsdatei bleibt für einen neuen Versuch liegen"
        assert [r[1] for r in hotbuffer.read_records(august_csv)] == [21.0]
    finally:
        index.close()


def test_retention_rewrite_keeps_event_ids_and_min_max(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")
        now = datetime(2024, 8, 31, 12, tzinfo=TZ)  # Cutoff = 2024-08-01 12:00
        expired_ts = datetime(2024, 8, 1, 8, tzinfo=TZ).timestamp()
        kept_ts = datetime(2024, 8, 15, 8, tzinfo=TZ).timestamp()
        hot_file = hotbuffer.hot_path(tmp_path, entity_id, now.timestamp(), TZ)
        hotbuffer.write_records(
            hot_file,
            [(expired_ts, 20.0, "alt", None, None), (kept_ts, 21.0, "neu", 20.5, 21.5)],
        )
        index.add_row_count(entity_id, 2)

        retention.enforce_retention_for_entity(tmp_path, index, entity_id, "30d", TZ, now)

        assert hotbuffer.read_full_rows(hot_file) == [(kept_ts, 21.0, "neu", 20.5, 21.5)]
    finally:
        index.close()


def test_failed_sqlite_extraction_leaves_no_partial_file(tmp_path: Path, monkeypatch) -> None:
    backup_zip = tmp_path / "zeitarchiv-backup.zip"
    database = tmp_path / "index.sqlite"
    sqlite3.connect(database).close()
    with zipfile.ZipFile(backup_zip, "w") as zf:
        zf.write(database, "index.sqlite")
    database.unlink()

    def failing_copy(*_args, **_kwargs):
        raise OSError("Platte voll")

    monkeypatch.setattr(backup.shutil, "copyfileobj", failing_copy)
    with pytest.raises(OSError):
        backup.validate_backup(backup_zip)

    assert [p.name for p in tmp_path.iterdir()] == [backup_zip.name]
