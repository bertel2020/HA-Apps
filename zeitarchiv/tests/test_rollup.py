"""Tests für app/storage/rollup.py — Bucket-Aggregation je Typ (Konzept Abschnitt 05)."""

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

    from app.storage import rollup

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def test_counter_bucket_sums_positive_deltas_only() -> None:
    rows = [
        (_ts(2024, 7, 1, 8), 100.0),
        (_ts(2024, 7, 1, 14), 105.0),   # +5
        (_ts(2024, 7, 1, 20), 102.0),   # Reset (Rückgang) -> 0 statt -3
        (_ts(2024, 7, 2, 8), 110.0),    # +8 gegenüber 102
    ]
    fine, last_value = rollup.compute_fine_rollup(
        rows, "counter", "tag", TZ, boundary_value=None, window_end_ts=_ts(2024, 7, 3, 0)
    )
    assert len(fine) == 2  # zwei Kalendertage
    day1, day2 = sorted(fine, key=lambda r: r.bucket_start)
    assert day1.value == 5.0  # 100->100 (Referenz, delta 0) + 100->105 (+5) + 105->102 (0, Reset)
    assert day2.value == 8.0
    assert last_value == 110.0


def test_counter_uses_boundary_value_from_previous_month() -> None:
    rows = [(_ts(2024, 8, 1, 6), 1005.0)]
    fine, _ = rollup.compute_fine_rollup(
        rows, "counter", "tag", TZ, boundary_value=1000.0, window_end_ts=_ts(2024, 8, 2, 0)
    )
    assert len(fine) == 1
    assert fine[0].value == 5.0  # 1005 - 1000, nicht auf 0 unterschätzt


def test_standard_bucket_mean_min_max() -> None:
    rows = [
        (_ts(2024, 7, 1, 8), 18.0),
        (_ts(2024, 7, 1, 14), 22.0),
        (_ts(2024, 7, 1, 20), 20.0),
    ]
    fine, _ = rollup.compute_fine_rollup(
        rows, "standard", "tag", TZ, boundary_value=None, window_end_ts=_ts(2024, 7, 2, 0)
    )
    assert len(fine) == 1
    assert fine[0].value == 20.0
    assert fine[0].min_value == 18.0
    assert fine[0].max_value == 22.0


def test_switch_on_duration_per_bucket() -> None:
    rows = [
        (_ts(2024, 7, 1, 8, 0, 0), 1.0),   # an ab 08:00
        (_ts(2024, 7, 1, 8, 30, 0), 0.0),  # aus ab 08:30 -> 30 Min an
        (_ts(2024, 7, 1, 9, 0, 0), 1.0),   # an ab 09:00
        (_ts(2024, 7, 1, 9, 15, 0), 0.0),  # aus -> 15 Min an
    ]
    fine, _ = rollup.compute_fine_rollup(
        rows, "switch", "stunde", TZ, boundary_value=None, window_end_ts=_ts(2024, 7, 1, 10)
    )
    by_hour = {r.bucket_start: r.on_seconds for r in fine}
    hour8 = _ts(2024, 7, 1, 8)
    hour9 = _ts(2024, 7, 1, 9)
    assert by_hour[hour8] == 30 * 60
    assert by_hour[hour9] == 15 * 60


def test_switch_interval_spanning_multiple_buckets_is_split_not_dumped_into_one() -> None:
    """Regressionstest: ein Regensensor, der von 08:00 bis 08:50 durchgehend "an"
    meldet (ein einziges Rohdaten-Intervall über 3 Fünf-Minuten-Buckets hinweg),
    darf nicht die volle Dauer in einen einzigen Bucket packen — das hätte vorher
    einen Bucket mit mehr Einschaltsekunden gezeigt, als der Bucket selbst lang ist."""
    rows = [
        (_ts(2024, 7, 1, 8, 0, 0), 1.0),   # an ab 08:00, kein weiterer Wert bis 08:50
        (_ts(2024, 7, 1, 8, 50, 0), 0.0),  # aus
    ]
    fine, _ = rollup.compute_fine_rollup(
        rows, "switch", "tag", TZ, boundary_value=None, window_end_ts=_ts(2024, 7, 1, 9)
    )
    # Bucket-Größe hier: 5 Minuten (Sekunden-Bucket über query.seconds_bucket_key wäre
    # der reale Pfad; "tag" als benannte Stufe hat 1 Tag Bucket-Größe — für den reinen
    # Split-Test reicht das, siehe test_switch_interval_split_across_five_minute_buckets
    # unten für den tatsächlichen Fünf-Minuten-Fall aus der Stunde/Tag-Live-Ansicht.
    assert len(fine) == 1
    assert fine[0].on_seconds == 50 * 60  # alles im selben Tages-Bucket, aber korrekt begrenzt


def test_switch_interval_split_across_five_minute_buckets() -> None:
    """Derselbe Fall wie in query.py für die Tag-Ansicht: 5-Minuten-Buckets über
    seconds_bucket_key/seconds_bucket_next — genau der Pfad, der den echten Bug zeigte."""
    key_fn = rollup.seconds_bucket_key(TZ, 300)
    next_fn = rollup.seconds_bucket_next(300)
    rows = [
        (_ts(2024, 7, 1, 8, 0, 0), 1.0),   # an ab 08:00:00
        (_ts(2024, 7, 1, 8, 12, 0), 0.0),  # aus ab 08:12:00 -> 12 Minuten an, über 3 Buckets verteilt
    ]
    fine, _ = rollup.compute_fine_rollup_with_key(
        rows, "switch", key_fn, boundary_value=None, window_end_ts=_ts(2024, 7, 1, 9), bucket_next_fn=next_fn
    )
    by_bucket = {r.bucket_start: r.on_seconds for r in fine}
    assert by_bucket[_ts(2024, 7, 1, 8, 0)] == 5 * 60   # 08:00–08:05 voll an
    assert by_bucket[_ts(2024, 7, 1, 8, 5)] == 5 * 60   # 08:05–08:10 voll an
    assert by_bucket[_ts(2024, 7, 1, 8, 10)] == 2 * 60  # 08:10–08:12 an, Rest des Buckets nicht
    # Kein einzelner Bucket darf länger "an" gemeldet werden, als er selbst lang ist.
    assert all(seconds <= 300 for seconds in by_bucket.values())


def test_append_completed_month_writes_tag_and_monat_rollup() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rollup-test-"))
    try:
        entity_id = "sensor.pv_ertrag_gesamt"
        month_table = pa.table(
            {
                "ts": [_ts(2024, 7, 1, 8), _ts(2024, 7, 2, 8)],
                "value": [100.0, 108.0],
            }
        )
        rollup.append_completed_month(tmp, entity_id, "counter", month_table, 2024, 7, TZ)

        tag_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "tag"))
        assert tag_table.num_rows == 2
        assert sorted(tag_table.column("value").to_pylist()) == [0.0, 8.0]

        monat_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "monat"))
        assert monat_table.num_rows == 1
        assert monat_table.column("value").to_pylist()[0] == 8.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_append_completed_month_appends_across_multiple_calls() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rollup-test-"))
    try:
        entity_id = "sensor.pv_ertrag_gesamt"
        july = pa.table({"ts": [_ts(2024, 7, 15, 8)], "value": [50.0]})
        rollup.append_completed_month(tmp, entity_id, "counter", july, 2024, 7, TZ)

        # last_value_before_month() liest aus der Archiv-Datei — die legt im echten
        # Betrieb rotate.rotate_month_file() an, bevor es append_completed_month()
        # aufruft; hier für den isolierten Test von Hand nachgebildet.
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(july, archive_dir / "2024-07.parquet")

        # August-Wert knüpft an den letzten Juli-Wert (50.0) als Referenz an.
        august = pa.table({"ts": [_ts(2024, 8, 15, 8)], "value": [65.0]})
        rollup.append_completed_month(tmp, entity_id, "counter", august, 2024, 8, TZ)

        monat_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "monat")).sort_by("bucket_start")
        assert monat_table.num_rows == 2
        assert monat_table.column("value").to_pylist() == [0.0, 15.0]
        assert rollup.rollup_path(tmp, entity_id, "monat").is_dir()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_append_completed_month_does_not_read_existing_rollup_history(monkeypatch) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rollup-test-"))
    try:
        entity_id = "sensor.temperatur"
        july = pa.table({"ts": [_ts(2024, 7, 15, 8)], "value": [20.0]})
        august = pa.table({"ts": [_ts(2024, 8, 15, 8)], "value": [21.0]})
        rollup.append_completed_month(tmp, entity_id, "standard", july, 2024, 7, TZ)

        def fail_read(*args, **kwargs):
            raise AssertionError("Bestehende Rollup-Historie darf beim Append nicht gelesen werden")

        monkeypatch.setattr(rollup.pq, "read_table", fail_read)
        rollup.append_completed_month(tmp, entity_id, "standard", august, 2024, 8, TZ)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_failed_entity_rollup_rebuild_keeps_previous_rollups() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rollup-test-"))
    try:
        entity_id = "sensor.energy"
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        valid = pa.table({"ts": [_ts(2024, 1, 15, 8)], "value": [10.0]})
        pq.write_table(valid, archive_dir / "2024-01.parquet")
        rollup.append_completed_month(tmp, entity_id, "standard", valid, 2024, 1, TZ)
        active_path = rollup.rollup_path(tmp, entity_id, "monat")
        before = {
            path.relative_to(active_path): path.read_bytes()
            for path in active_path.rglob("*.parquet")
        }
        (archive_dir / "2024-02.parquet").write_bytes(b"kein parquet")

        try:
            rollup.rebuild_entity_rollups(tmp, entity_id, "counter", TZ)
            raise AssertionError("Defektes Archiv hätte den Neuaufbau abbrechen müssen")
        except Exception as exc:
            assert "hätte den Neuaufbau" not in str(exc)

        after = {
            path.relative_to(active_path): path.read_bytes()
            for path in active_path.rglob("*.parquet")
        }
        assert after == before
        assert not list(tmp.glob(f".{entity_id}-type-rebuild-*"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_year_rollup_only_after_twelve_complete_months_in_the_past() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rollup-test-"))
    try:
        entity_id = "sensor.pv_ertrag_gesamt"
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        for month in range(1, 13):
            table = pa.table({"ts": [_ts(2020, month, 15, 8)], "value": [float(month * 10)]})
            # Archivdatei von Hand anlegen (im echten Betrieb macht das
            # rotate.rotate_month_file() vor dem Rollup-Aufruf, siehe oben).
            pq.write_table(table, archive_dir / f"2020-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2020, month, TZ)

        jahr_path = rollup.rollup_path(tmp, entity_id, "jahr")
        assert jahr_path.exists(), "Nach 12 vollständigen, vergangenen Monaten sollte jahr.parquet existieren"
        jahr_table = pq.read_table(jahr_path)
        assert jahr_table.num_rows == 1
        # Jeder Monat hatte boundary_value = Vormonatswert -> Delta = 10 je Monat, außer Monat 1 (Delta 0).
        assert jahr_table.column("value").to_pylist()[0] == 10 * 11
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_replace_month_recomputes_existing_year_total() -> None:
    """replace_month() (Archiv-Purge, cleanup.py) muss ein bereits berechnetes
    Jahr NEU aufsummieren statt es unverändert stehen zu lassen — sonst bliebe
    der Jahres-Wert nach einem nachträglichen Purge falsch/veraltet."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-rollup-test-"))
    try:
        entity_id = "sensor.pv_ertrag_gesamt"
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        for month in range(1, 13):
            table = pa.table({"ts": [_ts(2020, month, 15, 8)], "value": [float(month * 10)]})
            pq.write_table(table, archive_dir / f"2020-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2020, month, TZ)

        jahr_path = rollup.rollup_path(tmp, entity_id, "jahr")
        original_total = pq.read_table(jahr_path).column("value").to_pylist()[0]
        assert original_total == 110  # wie im Test oben: 10 Monate * Delta 10 (+ Monat 1 = 0)

        # Monat 6 nachträglich "purgen": derselbe Zeitstempel, aber ein
        # niedrigerer Wert (simuliert eine entfernte, zu hohe Zeile) — das
        # Delta gegenüber Monat 5 (50.0) sinkt von 10 auf 5.
        replaced_table = pa.table({"ts": [_ts(2020, 6, 15, 8)], "value": [55.0]})
        pq.write_table(replaced_table, archive_dir / "2020-06.parquet")
        rollup.replace_month(tmp, entity_id, "counter", replaced_table, 2020, 6, TZ)

        new_total = pq.read_table(jahr_path).column("value").to_pylist()[0]
        assert new_total == original_total - 5

        monat_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "monat"))
        assert monat_table.num_rows == 12  # weiterhin genau 12 Monats-Zeilen, keine Duplikate
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
