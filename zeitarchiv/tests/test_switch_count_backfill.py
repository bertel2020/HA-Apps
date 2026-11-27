"""Nachlauf der Einschaltungs-Anzahl für archivierte Monate (ROADMAP 1.74, Schritt 6)."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from app.storage import query, rollup

TZ = ZoneInfo("Europe/Berlin")
ENTITY = "binary_sensor.defrost"


def _ts(y, m, d, h, mi=0) -> float:
    return datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


def _archive(data_dir: Path, year: int, month: int, events: list[tuple[float, float]]) -> None:
    folder = data_dir / "archive" / ENTITY
    folder.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"ts": [e[0] for e in events], "value": [e[1] for e in events]}), folder / f"{year:04d}-{month:02d}.parquet")


def _old_rollup(data_dir: Path, year: int, month: int) -> None:
    """Ein Rollup aus der Zeit vor der Zählung: Segment ohne Spalte on_count."""
    for level in ("stunde", "monat"):
        folder = data_dir / "rollup" / ENTITY / f"{level}.parquet"
        folder.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"bucket_start": [_ts(year, month, 1, 0)], "on_seconds": [60.0]}), folder / f"{year:04d}-{month:02d}.parquet")


def _counts(data_dir: Path) -> dict[tuple[int, int], int | None]:
    table = rollup.read_rollup_table(rollup.rollup_path(data_dir, ENTITY, "monat"))
    rows = query._rollup_rows_from_table(table)
    return {(datetime.fromtimestamp(r.bucket_start, TZ).year, datetime.fromtimestamp(r.bucket_start, TZ).month): r.on_count for r in rows}


def test_months_without_a_count_are_found_and_filled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _archive(data_dir, 2024, 6, [(_ts(2024, 6, 3, 8), 1.0), (_ts(2024, 6, 3, 9), 0.0), (_ts(2024, 6, 4, 8), 1.0), (_ts(2024, 6, 4, 9), 0.0)])
        _archive(data_dir, 2024, 7, [(_ts(2024, 7, 2, 8), 1.0), (_ts(2024, 7, 2, 9), 0.0)])
        _old_rollup(data_dir, 2024, 6)
        _old_rollup(data_dir, 2024, 7)
        months, missing = rollup.switch_count_months(data_dir, ENTITY)
        assert months == missing == [(2024, 6), (2024, 7)]
        progress: list[tuple] = []
        assert rollup.backfill_switch_counts(data_dir, ENTITY, TZ, lambda *args: progress.append(args)) == 2
        assert progress == [(1, 2, 2024, 6), (2, 2, 2024, 7)]
        assert rollup.switch_count_months(data_dir, ENTITY)[1] == []
        assert _counts(data_dir) == {(2024, 6): 2, (2024, 7): 1}
        assert rollup.backfill_switch_counts(data_dir, ENTITY, TZ) == 0  # nichts mehr zu tun


def test_a_month_that_already_has_a_count_is_left_alone() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _archive(data_dir, 2024, 6, [(_ts(2024, 6, 3, 8), 1.0), (_ts(2024, 6, 3, 9), 0.0)])
        rollup.append_completed_month(data_dir, ENTITY, "switch", pq.read_table(data_dir / "archive" / ENTITY / "2024-06.parquet"), 2024, 6, TZ)
        assert rollup.switch_count_months(data_dir, ENTITY)[1] == []
        assert rollup.backfill_switch_counts(data_dir, ENTITY, TZ) == 0


def test_a_rollup_without_an_archive_survives() -> None:
    """Ein Gesamtneuaufbau aus dem Archiv würde den Monat verlieren, dessen Rohdaten es nicht mehr gibt."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _archive(data_dir, 2024, 7, [(_ts(2024, 7, 2, 8), 1.0), (_ts(2024, 7, 2, 9), 0.0)])
        _old_rollup(data_dir, 2024, 5)  # kein Archiv
        _old_rollup(data_dir, 2024, 7)
        assert rollup.backfill_switch_counts(data_dir, ENTITY, TZ) == 1
        assert _counts(data_dir) == {(2024, 5): None, (2024, 7): 1}


def test_no_archive_means_nothing_to_do() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert rollup.switch_count_months(Path(tmp), ENTITY) == ([], [])
        assert rollup.backfill_switch_counts(Path(tmp), ENTITY, TZ) == 0
