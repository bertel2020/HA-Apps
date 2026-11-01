"""Minimum/Maximum der Kennzahlen-Leiste (value_range.py): rechnet über alle
Rohwerte, merkt sich das Ergebnis und verwirft es, sobald Werte zur Löschung
markiert wurden — sonst stünde ein gerade markierter Ausreißer noch eine
Stunde als Maximum da."""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from app import value_range
from app.storage import hotbuffer
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
NOW = _dt.datetime(2024, 3, 15, 12, 0, 0, tzinfo=TZ)


def _ts(stunde: float) -> float:
    return _dt.datetime(2024, 3, 15, tzinfo=TZ).timestamp() + stunde * 3600


def _anlage(tmp_path: Path) -> Index:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.temperatur", "sensor", "measurement", "°C")
    for stunde, wert in enumerate([11.0, -3.5, 10.5, 38.1, 14.0]):
        hotbuffer.append(tmp_path, "sensor.temperatur", _ts(stunde), wert, TZ)
    return index


def _entity(index: Index, first_ts: float = _ts(0), deleted_count: int = 0):
    """Zeile mit gesetztem first_ts — der Hot-Buffer-Pfad schreibt ihn im
    laufenden Betrieb über die Ingestion, hier genügt ein Ersatz."""
    row = dict(index.get_entity("sensor.temperatur"))
    row["first_ts"] = first_ts
    row["deleted_count"] = deleted_count
    return row


def test_compute_finds_min_and_max_over_all_raw_values(tmp_path: Path) -> None:
    index = _anlage(tmp_path)
    entry = value_range.compute(tmp_path, index, TZ, _entity(index), NOW)
    assert (entry["min"], entry["max"]) == (-3.5, 38.1)
    index.close()


def test_cached_entry_is_reused_until_values_are_marked_for_deletion(tmp_path: Path) -> None:
    index = _anlage(tmp_path)
    value_range.compute(tmp_path, index, TZ, _entity(index), NOW)
    assert value_range.cached(index, _entity(index)) is not None
    # Ein markierter Ausreißer macht den Eintrag ungültig.
    assert value_range.cached(index, _entity(index, deleted_count=1)) is None
    index.close()


def test_cached_entry_expires_with_age(tmp_path: Path, monkeypatch) -> None:
    index = _anlage(tmp_path)
    value_range.compute(tmp_path, index, TZ, _entity(index), NOW)
    spaeter = time.time() + value_range.MAX_AGE_SECONDS + 1
    monkeypatch.setattr(value_range.time, "time", lambda: spaeter)
    assert value_range.cached(index, _entity(index)) is None
    index.close()


def test_entity_without_values_has_no_range(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.temperatur", "sensor", "measurement", "°C")
    entry = value_range.compute(tmp_path, index, TZ, _entity(index, first_ts=None), NOW)
    assert entry["min"] is None and entry["max"] is None
    index.close()


def test_last_raw_value_reads_the_data_when_the_index_has_none(tmp_path: Path) -> None:
    """Nach Import, Restore und Abgleich fehlt `last_value` im Index — der
    Zählerstand der Leiste muss dann trotzdem erscheinen."""
    index = _anlage(tmp_path)
    entity = _entity(index)
    entity["last_ts"] = _ts(4)
    assert value_range.last_raw_value(tmp_path, index, TZ, entity, NOW) == 14.0
    entity["last_ts"] = None
    assert value_range.last_raw_value(tmp_path, index, TZ, entity, NOW) is None
    index.close()
