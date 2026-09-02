"""Tests für app/storage/rotate.py — CSV→Parquet-Rundreise.

Braucht pyarrow (siehe requirements.txt) — in der normalen Python-Umgebung
dieses Repos nicht installiert, deshalb hier übersprungen statt fehlzuschlagen,
wenn pyarrow fehlt (z. B. außerhalb des .venv).
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
    import pyarrow.parquet as pq

    from app.storage import hotbuffer, rotate
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False


def test_rotate_preserves_rows_and_updates_index() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag_gesamt"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        # Drei Werte im Juli schreiben.
        july_rows = [(1719878400.0, 100.0), (1719964800.0, 105.5), (1720051200.0, 111.0)]
        event_ids = ["event-a", "event-b", "event-c"]
        for (ts, value), event_id in zip(july_rows, event_ids):
            hotbuffer.append(tmp, entity_id, ts, value, ZoneInfo("UTC"), event_id=event_id)
            index.record_write(entity_id, ts)

        hot_path = tmp / "hot" / f"{entity_id}-2024-07.csv"
        assert hot_path.exists()

        # Erster Schreibvorgang im August löst die Rotation des Juli-Monats aus.
        august_ts = 1722470400.0  # 2024-08-01
        rotate.rotate_if_needed(tmp, entity_id, august_ts, index, ZoneInfo("UTC"))

        assert not hot_path.exists(), "Hot-CSV sollte nach Rotation gelöscht sein"
        archive_path = tmp / "archive" / entity_id / "2024-07.parquet"
        assert archive_path.exists(), "Parquet-Archivdatei sollte existieren"

        table = pq.read_table(archive_path)
        assert table.num_rows == 3
        values = sorted(table.column("value").to_pylist())
        assert values == [100.0, 105.5, 111.0]
        assert table.column("event_id").to_pylist() == event_ids

        overview = index.get_overview()
        assert overview["entity_count"] == 1
        assert overview["total_rows"] == 3
        assert overview["total_size_bytes"] == archive_path.stat().st_size

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rotate_if_needed_does_nothing_for_current_month() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        ts = 1722470400.0  # 2024-08-01
        hotbuffer.append(tmp, entity_id, ts, 21.4, ZoneInfo("UTC"))
        index.record_write(entity_id, ts)

        rotate.rotate_if_needed(tmp, entity_id, ts, index, ZoneInfo("UTC"))

        hot_path = tmp / "hot" / f"{entity_id}-2024-08.csv"
        assert hot_path.exists(), "Aktueller Monat darf nicht rotiert werden"
        assert not (tmp / "archive" / entity_id).exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rotate_all_stale_rotates_entities_that_stopped_sending() -> None:
    """rotate_all_stale() (Einstellungen: manueller Anstoß) muss auch eine
    Entität rotieren, die seit dem vergangenen Monat KEINE neuen Werte mehr
    geschickt hat — ohne einen neuen Schreibvorgang würde rotate_if_needed()
    (lazy, nur beim nächsten Write) diese Hot-Datei nie von selbst anfassen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        stale_entity = "sensor.alter_sensor"
        fresh_entity = "sensor.aktueller_sensor"
        index.get_or_create_entity(stale_entity, "sensor", "measurement", "°C")
        index.get_or_create_entity(fresh_entity, "sensor", "measurement", "°C")

        july_ts = 1719878400.0  # 2024-07-02
        hotbuffer.append(tmp, stale_entity, july_ts, 21.0, ZoneInfo("UTC"))
        index.record_write(stale_entity, july_ts)

        august_ts = 1722470400.0  # 2024-08-01
        hotbuffer.append(tmp, fresh_entity, august_ts, 22.0, ZoneInfo("UTC"))
        index.record_write(fresh_entity, august_ts)

        now = datetime.fromtimestamp(august_ts, ZoneInfo("UTC"))
        rotated = rotate.rotate_all_stale(tmp, index, ZoneInfo("UTC"), now=now)

        assert rotated == 1
        assert not (tmp / "hot" / f"{stale_entity}-2024-07.csv").exists()
        assert (tmp / "archive" / stale_entity / "2024-07.parquet").exists()
        # der laufende Monat der frischen Entität bleibt unangetastet.
        assert (tmp / "hot" / f"{fresh_entity}-2024-08.csv").exists()

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
