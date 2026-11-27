"""Automatischer Nachlauf der Einschaltungs-Anzahl im Wartungstakt (ROADMAP 1.74)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.storage import rollup
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index
from app.switch_count_routes import SETTING_BACKFILL_PENDING, process_pending_backfill

TZ = ZoneInfo("Europe/Berlin")


def _ts(d, h) -> float:
    return datetime(2024, 6, d, h, tzinfo=TZ).timestamp()


def _old_switch(tmp_path: Path, index: Index, entity_id: str) -> None:
    index.get_or_create_entity(entity_id, "binary_sensor", None, None)
    folder = tmp_path / "archive" / entity_id
    folder.mkdir(parents=True)
    pq.write_table(pa.table({"ts": [_ts(3, 8), _ts(3, 9)], "value": [1.0, 0.0]}), folder / "2024-06.parquet")
    for level in ("stunde", "monat"):  # Rollup aus der Zeit vor der Zählung
        target = tmp_path / "rollup" / entity_id / f"{level}.parquet"
        target.mkdir(parents=True)
        pq.write_table(pa.table({"bucket_start": [_ts(1, 0)], "on_seconds": [3600.0]}), target / "2024-06.parquet")


@pytest.fixture
def setup(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    yield tmp_path, index, StorageCoordinator()
    index.close()


def _tick(setup) -> None:
    tmp_path, index, coordinator = setup
    process_pending_backfill(tmp_path, index, TZ, coordinator)


def test_the_first_tick_only_searches_and_the_next_ones_work_one_entity_each(setup) -> None:
    tmp_path, index, _ = setup
    _old_switch(tmp_path, index, "binary_sensor.a")
    _old_switch(tmp_path, index, "binary_sensor.b")
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")  # kein Schalter
    _tick(setup)
    assert json.loads(index.get_setting(SETTING_BACKFILL_PENDING)) == ["binary_sensor.a", "binary_sensor.b"]
    assert rollup.switch_count_months(tmp_path, "binary_sensor.a")[1] != []  # noch nichts gerechnet
    _tick(setup)
    assert rollup.switch_count_months(tmp_path, "binary_sensor.a")[1] == []
    assert rollup.switch_count_months(tmp_path, "binary_sensor.b")[1] != []  # höchstens eine je Takt
    _tick(setup)
    assert rollup.switch_count_months(tmp_path, "binary_sensor.b")[1] == []
    assert json.loads(index.get_setting(SETTING_BACKFILL_PENDING)) == []


def test_when_the_queue_is_done_a_tick_does_nothing_more(setup) -> None:
    tmp_path, index, _ = setup
    _tick(setup)  # nichts zu suchen
    assert json.loads(index.get_setting(SETTING_BACKFILL_PENDING)) == []
    _old_switch(tmp_path, index, "binary_sensor.late")  # taucht nach der Suche auf: kein erneutes Suchen
    _tick(setup)
    assert rollup.switch_count_months(tmp_path, "binary_sensor.late")[1] != []


def test_a_switch_that_was_deleted_meanwhile_is_skipped(setup) -> None:
    tmp_path, index, _ = setup
    index.set_setting(SETTING_BACKFILL_PENDING, json.dumps(["binary_sensor.gone"]))
    _tick(setup)
    assert json.loads(index.get_setting(SETTING_BACKFILL_PENDING)) == []


def test_a_failing_entity_is_not_retried_forever(setup, monkeypatch) -> None:
    tmp_path, index, _ = setup
    _old_switch(tmp_path, index, "binary_sensor.a")
    _tick(setup)

    def boom(*_args, **_kwargs):
        raise OSError("kaputt")

    monkeypatch.setattr(rollup, "backfill_switch_counts", boom)
    with pytest.raises(OSError):
        _tick(setup)
    assert json.loads(index.get_setting(SETTING_BACKFILL_PENDING)) == []  # nicht mehr in der Warteschlange
