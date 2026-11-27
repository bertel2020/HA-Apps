"""Einschaltvorgänge bei Schaltern (ROADMAP 1.74, Schritt 1): Zählung im Rollup, Speicherung, Lesen, Summen.

Gezählt wird nur der Wechsel auf "an". Ein vom Fensteranfang übernommenes "an" und ein doppelt gemeldetes "an" sind
keine Vorgänge; eine Anzahl aus der Zeit vor der Zählung ist "nicht erfasst" (None), nie 0.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from app.storage import query, rollup

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def _fine(rows, boundary=None, level="stunde", end=_ts(2024, 7, 1, 12)):
    fine, _ = rollup.compute_fine_rollup(rows, "switch", level, TZ, boundary_value=boundary, window_end_ts=end)
    return {datetime.fromtimestamp(r.bucket_start, TZ).hour: r for r in fine}


def test_each_switch_on_is_one_start_in_its_bucket() -> None:
    rows = [
        (_ts(2024, 7, 1, 8, 5), 1.0), (_ts(2024, 7, 1, 8, 10), 0.0),
        (_ts(2024, 7, 1, 8, 40), 1.0), (_ts(2024, 7, 1, 8, 50), 0.0),
        (_ts(2024, 7, 1, 9, 5), 1.0), (_ts(2024, 7, 1, 9, 9), 0.0),
    ]
    by_hour = _fine(rows)
    assert by_hour[8].on_count == 2 and by_hour[8].on_seconds == 900.0
    assert by_hour[9].on_count == 1 and by_hour[9].on_seconds == 240.0


def test_a_state_carried_in_from_the_window_start_is_not_a_start() -> None:
    rows = [(_ts(2024, 7, 1, 8, 10), 1.0), (_ts(2024, 7, 1, 8, 20), 0.0)]
    assert _fine(rows, boundary=1.0)[8].on_count == 0  # lief schon vor dem Fenster
    assert _fine(rows, boundary=0.0)[8].on_count == 1
    assert _fine(rows, boundary=None)[8].on_count == 1  # ohne Vorzustand zählt das erste "an"


def test_a_repeated_on_is_not_another_start() -> None:
    rows = [(_ts(2024, 7, 1, 8, 5), 1.0), (_ts(2024, 7, 1, 8, 15), 1.0), (_ts(2024, 7, 1, 8, 25), 0.0)]
    assert _fine(rows)[8].on_count == 1


def test_an_interval_over_a_bucket_boundary_counts_once_where_it_began() -> None:
    rows = [(_ts(2024, 7, 1, 8, 50), 1.0), (_ts(2024, 7, 1, 9, 10), 0.0)]
    by_hour = _fine(rows)
    assert by_hour[8].on_count == 1
    assert by_hour[9].on_count == 0 and by_hour[9].on_seconds == 600.0  # Dauer wird weiter geteilt


def test_a_pulse_without_duration_still_gets_a_row() -> None:
    at = _ts(2024, 7, 1, 8, 5)
    by_hour = _fine([(at, 1.0), (at, 0.0)])
    assert by_hour[8].on_count == 1 and by_hour[8].on_seconds == 0.0


def test_buckets_without_on_time_or_start_stay_absent() -> None:
    assert _fine([(_ts(2024, 7, 1, 8, 5), 0.0)]) == {}


def test_counts_add_up_to_the_month_row() -> None:
    rows = [rollup.FineRow(1.0, on_seconds=60.0, on_count=2), rollup.FineRow(2.0, on_seconds=30.0, on_count=3)]
    month = rollup.aggregate_fine_to_month(rows, "switch", 0.0)
    assert (month.on_seconds, month.on_count) == (90.0, 5)


def test_a_sum_with_an_unknown_part_is_unknown() -> None:
    """Eine Teilsumme wäre falsch niedrig und sähe richtig aus."""
    rows = [rollup.FineRow(1.0, on_seconds=60.0, on_count=2), rollup.FineRow(2.0, on_seconds=30.0)]
    assert rollup.sum_counts(rows) is None
    assert rollup.aggregate_fine_to_month(rows, "switch", 0.0).on_count is None
    assert query._aggregate_rollup_rows(rows, "switch", "monat", TZ)[0].on_count is None


def _write(directory: Path, name: str, table: pa.Table) -> None:
    pq.write_table(table, directory / name)


def test_the_column_survives_when_the_oldest_segment_does_not_have_it() -> None:
    """pyarrow nimmt bei einem Verzeichnis das Schema der ersten Datei und ließe die Spalte sonst weg."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "stunde.parquet"
        directory.mkdir()
        _write(directory, "2024-06.parquet", pa.table({"bucket_start": [1.0], "on_seconds": [60.0]}))
        _write(directory, "2024-07.parquet", rollup._fine_rows_to_table(
            [rollup.FineRow(2.0, on_seconds=30.0, on_count=4)], "switch"))
        table = rollup.read_rollup_table(directory)
        rows = query._rollup_rows_from_table(table)
        assert [(r.on_seconds, r.on_count) for r in rows] == [(60.0, None), (30.0, 4)]


def test_a_single_file_is_read_as_before() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "monat.parquet"
        _write(Path(tmp), "monat.parquet", rollup._fine_rows_to_table([rollup.FineRow(2.0, on_seconds=30.0, on_count=4)], "switch"))
        assert rollup.read_rollup_table(path).column("on_count").to_pylist() == [4]


def test_a_month_archive_counts_from_the_value_before_the_month() -> None:
    """append_completed_month übergibt den Zustand vom Monatsanfang: lief der Schalter schon, ist die erste 1 keiner."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        entity = "binary_sensor.test"
        previous = data_dir / "archive" / entity
        previous.mkdir(parents=True)
        _write(previous, "2024-06.parquet", pa.table({"ts": [_ts(2024, 6, 30, 23)], "value": [1.0]}))
        july = pa.table({"ts": [_ts(2024, 7, 1, 8), _ts(2024, 7, 1, 9)], "value": [1.0, 0.0]})
        rollup.append_completed_month(data_dir, entity, "switch", july, 2024, 7, TZ)
        rows = query._rollup_rows_from_table(rollup.read_rollup_table(rollup.rollup_path(data_dir, entity, "monat")))
        assert rows[-1].on_count == 0 and rows[-1].on_seconds == 3600.0
