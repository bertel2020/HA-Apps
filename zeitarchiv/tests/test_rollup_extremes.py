"""Code-Review Phase 5 (B4): Rollups übernehmen die gespeicherten echten Min/Max-Werte.

Die Live-Auflösung und die Verdichtung speichern je Fenster den echten kleinsten und größten Rohwert neben dem
Mittel. Der Rollup bildete Min/Max aber aus den Mittelwerten: Ein kurzer Frost von −1 °C bei einem Stundenmittel
von +0,5 °C ging verloren, der Tag zählte nicht als Frosttag.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    import pyarrow as pa

    from app.storage import hotbuffer, query, rollup, rotate
    from app.storage.index import Index

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="pyarrow fehlt")

TZ = ZoneInfo("Europe/Berlin")


def _ts(m: int, d: int, h: int, mi: int = 0) -> float:
    return datetime(2024, m, d, h, mi, tzinfo=TZ).timestamp()


def _fine(rows) -> list:
    fine, _last = rollup.compute_fine_rollup(
        rows, "standard", "stunde", TZ, boundary_value=None, window_end_ts=_ts(7, 2, 0)
    )
    return sorted(fine, key=lambda row: row.bucket_start)


def test_a_bucket_takes_the_stored_extremes_instead_of_the_extremes_of_the_means() -> None:
    # Zwei 30-Minuten-Fenster derselben Stunde: Mittel +0,5 und +4, echte Tiefst-/Höchstwerte −1 und +6.
    rows = [(_ts(7, 1, 10, 30), 0.5, -1.0, 2.0), (_ts(7, 1, 10, 50), 4.0, 3.0, 6.0)]
    hour = _fine(rows)[0]
    assert hour.value == pytest.approx(2.25)
    assert (hour.min_value, hour.max_value) == (-1.0, 6.0)


def test_rows_without_stored_extremes_behave_as_before() -> None:
    rows = [(_ts(7, 1, 10, 5), 2.0), (_ts(7, 1, 10, 25), 6.0), (_ts(7, 1, 10, 45), 4.0)]
    hour = _fine(rows)[0]
    assert (hour.value, hour.min_value, hour.max_value) == (4.0, 2.0, 6.0)


def test_a_missing_extreme_falls_back_to_the_value_of_that_row() -> None:
    rows = [(_ts(7, 1, 10, 5), 2.0, None, None), (_ts(7, 1, 10, 25), 6.0, 5.0, 7.5)]
    hour = _fine(rows)[0]
    assert (hour.min_value, hour.max_value) == (2.0, 7.5)


def _month_table(with_extremes: bool):
    columns = {
        "ts": [_ts(7, 1, 10, 30), _ts(7, 1, 11, 0), _ts(7, 2, 12, 0)],
        "value": [0.5, 4.0, 20.0],
    }
    if with_extremes:
        columns |= {"min_value": [-1.0, 3.0, 19.0], "max_value": [2.0, 6.0, 22.0]}
    return pa.table(columns)


@pytest.mark.parametrize(("with_extremes", "day_min", "month_max"), [(True, -1.0, 22.0), (False, 0.5, 20.0)])
def test_a_completed_month_writes_the_real_extremes_to_every_rollup_level(
    with_extremes: bool, day_min: float, month_max: float
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        data_dir = Path(directory)
        rollup.append_completed_month(data_dir, "sensor.temp", "standard", _month_table(with_extremes), 2024, 7, TZ)

        hours = query._read_rollup_rows(data_dir, "sensor.temp", "stunde", _ts(7, 1, 0), _ts(7, 3, 0))
        first_hour = min(hours, key=lambda row: row.bucket_start)
        assert first_hour.min_value == day_min
        month = query._read_rollup_rows(data_dir, "sensor.temp", "monat", _ts(7, 1, 0), _ts(8, 1, 0))[0]
        assert (month.min_value, month.max_value) == (day_min, month_max)


def test_rotating_a_month_with_resolution_windows_keeps_the_real_frost() -> None:
    """Durch die Rotation hindurch: Hot-Datei mit Fenstermitteln und Extremen, danach der Rollup."""
    with tempfile.TemporaryDirectory() as directory:
        data_dir = Path(directory)
        index = Index(data_dir / "index.sqlite")
        try:
            entity_id = "sensor.aussen"
            index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
            hot = hotbuffer.hot_path(data_dir, entity_id, _ts(7, 1, 12), TZ)
            hotbuffer.write_records(hot, [
                (_ts(7, 1, 5, 30), 0.5, "a", -1.0, 2.0),
                (_ts(7, 1, 6, 0), 1.0, "b", 0.0, 2.5),
                (_ts(7, 15, 12, 0), 21.0, "c", 20.0, 22.0),
            ])
            index.record_write(entity_id, _ts(7, 1, 5, 30))
            rotate.rotate_if_needed(data_dir, entity_id, _ts(8, 2, 12), index, TZ)

            hours = query._read_rollup_rows(data_dir, entity_id, "stunde", _ts(7, 1, 0), _ts(7, 2, 0))
            assert min(row.min_value for row in hours) == -1.0
            assert min(row.value for row in hours) == 0.5  # das Mittel bleibt, was es war
        finally:
            index.close()


# ---- Der laufende Monat (Phase 11): dieselben Extremwerte wie nach dem Monatsabschluss ----


def _live_month_setup(data_dir: Path, index) -> str:
    entity_id = "sensor.aussen_live"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    hot = hotbuffer.hot_path(data_dir, entity_id, _ts(7, 5, 12), TZ)
    hotbuffer.write_records(hot, [
        (_ts(7, 1, 5, 30), 0.5, "a", -1.0, 2.0),    # aufgelöstes Fenster: Mittel +0,5, echter Tiefstwert −1
        (_ts(7, 1, 6, 0), 1.0, "b", 0.0, 2.5),
        (_ts(7, 3, 12, 0), 21.0, None, None, None),  # roher Wert ohne Extremwerte
    ])
    return entity_id


def test_the_running_month_shows_the_real_extremes_of_resolved_windows() -> None:
    with tempfile.TemporaryDirectory() as directory:
        data_dir = Path(directory)
        index = Index(data_dir / "index.sqlite")
        try:
            entity_id = _live_month_setup(data_dir, index)
            now = datetime(2024, 7, 5, 12, tzinfo=TZ)
            row = query._month_live_row(
                data_dir, index, entity_id, "standard", datetime(2024, 7, 1, tzinfo=TZ), datetime(2024, 8, 1, tzinfo=TZ),
                now, TZ,
            )
            assert (row.min_value, row.max_value) == (-1.0, 21.0)
            assert row.value == pytest.approx((0.5 + 1.0 + 21.0) / 3)  # das Mittel bleibt, was es war
        finally:
            index.close()


def test_the_extremes_of_resolved_windows_survive_the_hot_row_filter_and_the_read_cache() -> None:
    with tempfile.TemporaryDirectory() as directory:
        data_dir = Path(directory)
        index = Index(data_dir / "index.sqlite")
        try:
            entity_id = _live_month_setup(data_dir, index)
            args = (data_dir, entity_id, index, _ts(7, 5, 12), _ts(7, 1, 0), _ts(7, 6, 0), TZ)
            plain = query._read_hot_rows_filtered(*args)
            assert plain[0] == (_ts(7, 1, 5, 30), 0.5)  # ohne Wunsch bleibt es bei (ts, value)
            for cache in (None, query.QueryReadCache()):
                full = query._read_hot_rows_filtered(*args, read_cache=cache, extremes=True)
                assert full[0] == (_ts(7, 1, 5, 30), 0.5, -1.0, 2.0)
                assert full[-1] == (_ts(7, 3, 12), 21.0, None, None)
        finally:
            index.close()
