"""Rotation eines Monats, für den es schon ein Archiv gibt (app/storage/rotate.py).

Verspätete Werte nach dem Monatswechsel (z. B. die Warteschlange der Integration nach einem Ausfall) legen die
Hot-Datei eines vergangenen Monats neu an. Die nächste Rotation ersetzte das Archiv früher durch diese wenigen
Zeilen; jetzt wird es ergänzt, und die Rollups des Monats werden neu berechnet statt doppelt angehängt."""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import hotbuffer, rollup, rotate
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne pyarrow
    _PYARROW_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _PYARROW_AVAILABLE, reason="pyarrow fehlt")

TZ = ZoneInfo("Europe/Berlin")
ENTITY = "sensor.zaehler"


def _ts(day: int, hour: int = 12) -> float:
    return dt.datetime(2026, 10, day, hour, tzinfo=TZ).timestamp()


@pytest.fixture()
def env():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rotate-merge-"))
    index = Index(tmp / "index.sqlite")
    index.get_or_create_entity(ENTITY, "sensor", "total_increasing", "kWh")
    yield tmp, index
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _write_hot(tmp: Path, index, rows, event_prefix: str | None = None) -> Path:
    for i, (ts, value) in enumerate(rows):
        hotbuffer.append(tmp, ENTITY, ts, value, TZ, event_id=f"{event_prefix}{i}" if event_prefix else None)
        index.record_write(ENTITY, ts)
    return hotbuffer.hot_path(tmp, ENTITY, rows[0][0], TZ)


def _archive_rows(tmp: Path) -> list[tuple]:
    table = pq.read_table(tmp / "archive" / ENTITY / "2026-10.parquet")
    return list(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))


def _first_rotation(tmp, index) -> None:
    path = _write_hot(tmp, index, [(_ts(1), 100.0), (_ts(2), 101.0), (_ts(3), 103.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)


def test_a_late_value_extends_the_archive_instead_of_replacing_it(env) -> None:
    tmp, index = env
    _first_rotation(tmp, index)
    path = _write_hot(tmp, index, [(_ts(31, 13), 150.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    assert _archive_rows(tmp) == [(_ts(1), 100.0), (_ts(2), 101.0), (_ts(3), 103.0), (_ts(31, 13), 150.0)]
    assert not path.exists()


def test_the_merge_is_sorted_by_time_even_if_the_late_value_is_older(env) -> None:
    tmp, index = env
    _first_rotation(tmp, index)
    path = _write_hot(tmp, index, [(_ts(2, 18), 102.0), (_ts(1, 6), 99.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    timestamps = [ts for ts, _ in _archive_rows(tmp)]
    assert timestamps == sorted(timestamps) and len(timestamps) == 5


def test_an_identical_row_is_kept_once_and_the_row_count_follows(env) -> None:
    """Dieselbe Zeile (Zeit, Wert, Event-ID) in Archiv und Hot-Datei, z. B. nach einem Rückführen aus dem Archiv."""
    tmp, index = env
    path = _write_hot(tmp, index, [(_ts(1), 100.0), (_ts(2), 101.0)], event_prefix="e")
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    before = index.get_entity(ENTITY)["row_count"]
    path = _write_hot(tmp, index, [(_ts(2), 101.0)], event_prefix="x")  # gleiche Zeit und Wert, andere Event-ID
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    assert len(_archive_rows(tmp)) == 3  # andere Event-ID: keine Dublette
    # Dieselbe Event-ID: Dublette.
    path = hotbuffer.hot_path(tmp, ENTITY, _ts(1), TZ)
    hotbuffer.append(tmp, ENTITY, _ts(1), 100.0, TZ, event_id="e0")
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    assert len(_archive_rows(tmp)) == 3
    assert index.get_entity(ENTITY)["row_count"] <= before + 1


def test_two_different_values_at_the_same_time_are_both_kept(env) -> None:
    tmp, index = env
    _first_rotation(tmp, index)
    path = _write_hot(tmp, index, [(_ts(2), 777.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    assert (_ts(2), 101.0) in _archive_rows(tmp) and (_ts(2), 777.0) in _archive_rows(tmp)


def test_an_older_archive_without_optional_columns_is_merged(env) -> None:
    tmp, index = env
    archive_dir = tmp / "archive" / ENTITY
    archive_dir.mkdir(parents=True)
    pq.write_table(pa.table({"ts": [_ts(1), _ts(2)], "value": [100.0, 101.0]}), archive_dir / "2026-10.parquet")
    path = _write_hot(tmp, index, [(_ts(31, 13), 150.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    table = pq.read_table(archive_dir / "2026-10.parquet")
    assert table.num_rows == 3
    assert table.column_names == ["ts", "value", "event_id", "min_value", "max_value"]
    assert table.column("event_id").to_pylist() == [None, None, None]


def test_rollups_of_the_month_are_recomputed_not_appended_twice(env) -> None:
    """Tages- und Monats-Rollup enthalten den Monat nach dem Ergänzen genau einmal, aus ALLEN Zeilen."""
    tmp, index = env
    _first_rotation(tmp, index)
    path = _write_hot(tmp, index, [(_ts(31, 13), 150.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    day = pq.read_table(rollup.rollup_path(tmp, ENTITY, "tag")).to_pylist()
    starts = [row["bucket_start"] for row in day]
    assert len(starts) == len(set(starts))
    assert dt.datetime(2026, 10, 31, tzinfo=TZ).timestamp() in starts  # der späte Wert ist im Tages-Rollup
    month = pq.read_table(rollup.rollup_path(tmp, ENTITY, "monat")).to_pylist()
    assert len(month) == 1
    # 100 → 150: Delta des Monats aus allen vier Zeilen (der erste Wert ist der Bezugspunkt).
    assert month[0]["value"] == pytest.approx(50.0)


def test_size_accounting_follows_the_new_file_not_the_sum(env) -> None:
    tmp, index = env
    _first_rotation(tmp, index)
    path = _write_hot(tmp, index, [(_ts(31, 13), 150.0)])
    rotate.rotate_month_file(path, tmp, ENTITY, index, TZ)
    assert index.get_entity(ENTITY)["size_bytes"] == (tmp / "archive" / ENTITY / "2026-10.parquet").stat().st_size


def test_the_first_rotation_is_unchanged(env) -> None:
    """Ohne vorhandenes Archiv verhält sich die Rotation wie bisher (anhängen, nicht ersetzen)."""
    tmp, index = env
    _first_rotation(tmp, index)
    assert len(_archive_rows(tmp)) == 3
    assert len(pq.read_table(rollup.rollup_path(tmp, ENTITY, "monat")).to_pylist()) == 1
