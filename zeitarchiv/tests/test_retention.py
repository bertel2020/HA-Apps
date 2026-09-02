"""Tests für app/storage/retention.py — Aufbewahrung durchsetzen (Konzept
"Offene Punkte": bisher wurde die Frist nur gespeichert, nie durchgesetzt).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import hotbuffer, retention, rollup
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def _write_archive_month(tmp: Path, entity_id: str, year: int, month: int, rows: list[tuple[float, float]]) -> Path:
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{year:04d}-{month:02d}.parquet"
    table = pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]})
    pq.write_table(table, path, compression="zstd")
    return path


def _write_rollup(tmp: Path, entity_id: str, level: str, bucket_starts: list[float]) -> Path:
    path = rollup.rollup_path(tmp, entity_id, level)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "bucket_start": bucket_starts,
            "value": [1.0] * len(bucket_starts),
            "min_value": [1.0] * len(bucket_starts),
            "max_value": [1.0] * len(bucket_starts),
        }
    )
    pq.write_table(table, path, compression="zstd")
    return path


def test_enforce_retention_deletes_whole_expired_months_only_and_updates_index() -> None:
    """Ein Monat wird nur gelöscht, wenn er KOMPLETT vor dem Cutoff liegt — ein
    nur teilweise abgelaufener Monat (hier Juli, dessen erste Tage zwar älter
    als 30 Tage sind, dessen letzter Tag aber noch innerhalb der Frist liegt)
    bleibt unangetastet, das ist die bewusste Monats-Granularität (kein
    Parquet-Rewrite bei der Durchsetzung)."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")

        june_rows = [(_ts(2024, 6, 10, 8), 20.0), (_ts(2024, 6, 20, 8), 21.0)]
        july_rows = [(_ts(2024, 7, 5, 8), 22.0), (_ts(2024, 7, 25, 8), 23.0)]
        june_path = _write_archive_month(tmp, entity_id, 2024, 6, june_rows)
        july_path = _write_archive_month(tmp, entity_id, 2024, 7, july_rows)
        index.add_row_count(entity_id, len(june_rows) + len(july_rows))
        index.set_first_ts(entity_id, june_rows[0][0])

        _write_rollup(tmp, entity_id, "stunde", [_ts(2024, 6, 10, 8), _ts(2024, 7, 5, 8)])
        _write_rollup(tmp, entity_id, "monat", [_ts(2024, 6, 1, 0), _ts(2024, 7, 1, 0)])

        now = datetime(2024, 8, 15, 12, tzinfo=TZ)  # Cutoff (30 Tage zurück) = 2024-07-16
        result = retention.enforce_retention_for_entity(tmp, index, entity_id, "30d", TZ, now)

        assert result["months_deleted"] == 1
        assert result["rows_deleted"] == 2
        assert not june_path.exists()
        assert july_path.exists(), "Juli reicht noch in die Frist hinein — darf nicht gelöscht werden"

        stunde_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "stunde"))
        assert stunde_table.column("bucket_start").to_pylist() == [_ts(2024, 7, 5, 8)]
        monat_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "monat"))
        assert monat_table.column("bucket_start").to_pylist() == [_ts(2024, 7, 1, 0)]

        entity = index.get_entity(entity_id)
        assert entity["row_count"] == 2  # 4 ursprünglich - 2 gelöschte Juni-Zeilen
        assert entity["first_ts"] == july_rows[0][0]  # neuer frühester Wert: erste verbliebene Juli-Zeile

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_retention_prunes_expired_hot_buffer_rows() -> None:
    """Bei sehr kurzer Frist (30 Tage) UND "now" nahe am Monatsende kann der
    Cutoff auch mitten in den laufenden Monat fallen — dann müssen einzelne
    Hot-Buffer-Zeilen physisch entfernt werden, nicht nur ganze Archiv-Monate."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")

        now = datetime(2024, 8, 31, 12, tzinfo=TZ)  # Cutoff (30 Tage zurück) = 2024-08-01 12:00
        expired_ts = _ts(2024, 8, 1, 8)  # vor dem Cutoff
        kept_ts = _ts(2024, 8, 15, 8)  # nach dem Cutoff
        hotbuffer.append(tmp, entity_id, expired_ts, 20.0, TZ)
        hotbuffer.append(tmp, entity_id, kept_ts, 21.0, TZ)
        index.add_row_count(entity_id, 2)

        result = retention.enforce_retention_for_entity(tmp, index, entity_id, "30d", TZ, now)

        assert result["rows_deleted"] == 1
        hot_file = hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ)
        assert hotbuffer.read_rows(hot_file) == [(kept_ts, 21.0)]
        assert index.get_entity(entity_id)["row_count"] == 1

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_retention_skips_unlimited_retention() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        assert index.get_entity(entity_id)["retention"] == "unlimited"

        old_path = _write_archive_month(tmp, entity_id, 2020, 1, [(_ts(2020, 1, 1, 0), 1.0)])
        index.add_row_count(entity_id, 1)

        now = datetime(2024, 8, 15, tzinfo=TZ)
        result = retention.enforce_retention_for_entity(tmp, index, entity_id, "unlimited", TZ, now)

        assert result == {"rows_deleted": 0, "bytes_freed": 0, "months_deleted": 0}
        assert old_path.exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_retention_all_sums_across_entities_and_skips_unlimited() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        limited_id = "sensor.limited"
        unlimited_id = "sensor.unlimited"
        index.get_or_create_entity(limited_id, "sensor", "measurement", "°C")
        index.set_config(limited_id, retention="30d")
        index.get_or_create_entity(unlimited_id, "sensor", "measurement", "°C")

        _write_archive_month(tmp, limited_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0)])
        _write_archive_month(tmp, unlimited_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0)])
        index.add_row_count(limited_id, 1)
        index.add_row_count(unlimited_id, 1)

        now = datetime(2024, 8, 15, tzinfo=TZ)
        totals = retention.enforce_retention_all(tmp, index, TZ, now=now)

        assert totals["entities_affected"] == 1
        assert totals["rows_deleted"] == 1
        assert totals["months_deleted"] == 1
        assert not (tmp / "archive" / limited_id / "2024-01.parquet").exists()
        assert (tmp / "archive" / unlimited_id / "2024-01.parquet").exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_matches_enforcement_without_deleting_files() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-preview-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.preview"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")
        old_path = _write_archive_month(
            tmp, entity_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0), (_ts(2024, 1, 11, 8), 2.0)]
        )
        index.add_row_count(entity_id, 2)
        now = datetime(2024, 8, 15, tzinfo=TZ)

        preview = retention.preview_retention_all(tmp, index, TZ, now=now)
        assert preview["rows_deleted"] == 2
        assert preview["months_deleted"] == 1
        assert preview["entities_affected"] == 1
        assert old_path.exists(), "Die Vorschau darf keine Datei verändern"
        assert index.get_entity(entity_id)["row_count"] == 2

        actual = retention.enforce_retention_all(tmp, index, TZ, now=now)
        assert actual == preview
        assert not old_path.exists()
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_overview_groups_due_data_and_next_expiration_by_policy() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-overview-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        due_id = "sensor.due"
        future_id = "sensor.future"
        index.get_or_create_entity(due_id, "sensor", "measurement", "°C")
        index.set_config(due_id, retention="30d")
        index.get_or_create_entity(future_id, "sensor", "measurement", "°C")
        index.set_config(future_id, retention="90d")
        _write_archive_month(tmp, due_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0)])
        _write_archive_month(tmp, future_id, 2024, 7, [(_ts(2024, 7, 10, 8), 1.0)])
        index.add_row_count(due_id, 1)
        index.add_row_count(future_id, 1)

        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        overview = retention.preview_retention_overview(tmp, index, TZ, now=now)
        groups = {row["retention"]: row for row in overview["groups"]}

        assert overview["totals"]["rows_deleted"] == 1
        assert overview["totals"]["entities_affected"] == 1
        assert groups["30d"]["rows_due"] == 1
        assert groups["30d"]["entities_due"] == 1
        assert groups["90d"]["rows_due"] == 0
        assert groups["90d"]["next_expiration_ts"] is not None
        assert overview["generated_at"] == now.timestamp()
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
