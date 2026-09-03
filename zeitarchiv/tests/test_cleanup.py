"""Tests für app/storage/cleanup.py — Ausreißer/Lücken/Duplikate, Soft-Delete/Undo."""

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

    from app.storage import cleanup, hotbuffer, rollup
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def test_detect_duplicates() -> None:
    rows = [(_ts(2024, 7, 1, 8), 21.0), (_ts(2024, 7, 1, 8), 21.5), (_ts(2024, 7, 1, 9), 22.0)]
    duplicates = cleanup.detect_duplicates(rows)
    assert set(duplicates) == {_ts(2024, 7, 1, 8)}
    assert "2×" in duplicates[_ts(2024, 7, 1, 8)]


def test_duplicate_rows_to_delete_keeps_first_occurrence_per_timestamp() -> None:
    dup_ts = _ts(2024, 7, 1, 8)
    rows = [
        (dup_ts, 21.0), (dup_ts, 21.5), (dup_ts, 21.9),  # dreifaches Duplikat
        (_ts(2024, 7, 1, 9), 22.0),  # kein Duplikat
    ]
    to_delete = cleanup.duplicate_rows_to_delete(rows)
    # das erste Vorkommen (21.0) bleibt erhalten, die beiden weiteren werden vorgeschlagen.
    assert to_delete == [(dup_ts, 21.5), (dup_ts, 21.9)]


def test_detect_gaps_flags_row_after_gap_exceeding_configured_threshold() -> None:
    rows = [
        (_ts(2024, 7, 1, 8, 0), 21.0),
        (_ts(2024, 7, 1, 8, 5), 21.1),
        (_ts(2024, 7, 1, 8, 10), 21.2),
        (_ts(2024, 7, 1, 12, 0), 21.5),  # ~3 Std. 50 Min. Pause — über dem 60-Min.-Schwellwert
        (_ts(2024, 7, 1, 12, 5), 21.6),
    ]
    gaps = cleanup.detect_gaps(rows, threshold_minutes=60, decimals="auto", tz=TZ)
    assert set(gaps) == {_ts(2024, 7, 1, 12, 0)}
    reason = gaps[_ts(2024, 7, 1, 12, 0)]
    assert "seit vorherigem Wert" in reason and "Schwellwert" in reason


def test_detect_gaps_returns_nothing_when_threshold_is_off() -> None:
    rows = [
        (_ts(2024, 7, 1, 8, 0), 21.0),
        (_ts(2024, 7, 1, 12, 0), 21.5),  # wäre bei jedem Schwellwert eine Lücke
    ]
    assert cleanup.detect_gaps(rows, threshold_minutes=None, decimals="auto", tz=TZ) == {}


def test_detect_outliers_flags_jump_into_and_back_out_of_a_spike() -> None:
    """Sprung-basiert (gegenüber dem Vorwert) statt Abweichung vom Fenster-Median
    (siehe cleanup.detect_outliers) — ein einzelner Ausreißer erzeugt deshalb
    ZWEI markierte Zeitstempel: den plötzlichen Sprung hinein UND den ebenso
    plötzlichen Rücksprung zum normalen Niveau danach."""
    rows = [
        (_ts(2024, 7, 1, 8), 21.0),
        (_ts(2024, 7, 1, 9), 21.4),
        (_ts(2024, 7, 1, 10), 20.8),
        (_ts(2024, 7, 1, 11), 184.7),  # plötzlicher Sprung
        (_ts(2024, 7, 1, 12), 21.2),  # ebenso plötzlicher Rücksprung
        (_ts(2024, 7, 1, 13), 21.6),
    ]
    outliers = cleanup.detect_outliers(rows, threshold_percent=50, decimals="auto", tz=TZ)
    assert set(outliers) == {_ts(2024, 7, 1, 11), _ts(2024, 7, 1, 12)}
    assert "Sprung gegenüber Vorwert" in outliers[_ts(2024, 7, 1, 11)]


def test_detect_outliers_returns_nothing_when_threshold_is_off() -> None:
    rows = [
        (_ts(2024, 7, 1, 8), 21.0),
        (_ts(2024, 7, 1, 9), 21.4),
        (_ts(2024, 7, 1, 10), 20.8),
        (_ts(2024, 7, 1, 11), 184.7),
        (_ts(2024, 7, 1, 12), 21.2),
        (_ts(2024, 7, 1, 13), 21.6),
    ]
    assert cleanup.detect_outliers(rows, threshold_percent=None, decimals="auto", tz=TZ) == {}


def test_soft_delete_excludes_row_and_undo_restores_it() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        rows_in = [(_ts(2024, 7, 1, 8), 21.0), (_ts(2024, 7, 1, 9), 184.7), (_ts(2024, 7, 1, 10), 21.2)]
        for ts, value in rows_in:
            hotbuffer.append(tmp, entity_id, ts, value, TZ)
            index.record_write(entity_id, ts)

        now = datetime(2024, 7, 1, 12, tzinfo=TZ)
        window = (_ts(2024, 7, 1, 0), _ts(2024, 7, 2, 0))
        before = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert len(before) == 3

        cleanup.soft_delete(index, entity_id, [_ts(2024, 7, 1, 9)])
        after_delete = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert len(after_delete) == 2
        assert _ts(2024, 7, 1, 9) not in [ts for ts, _ in after_delete]

        undone = cleanup.undo_last_delete(index, entity_id)
        assert undone == 1
        after_undo = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert len(after_undo) == 3

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_soft_delete_removes_only_one_duplicate_occurrence_not_both() -> None:
    """Bei zwei Rohwerten mit exakt demselben Zeitstempel (Duplikat) muss sich
    gezielt nur EINES der beiden Vorkommen löschen lassen — soft_delete mit dem
    Zeitstempel einmal übergeben darf nicht beide Zeilen entfernen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.leistung"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

        dup_ts = _ts(2024, 7, 1, 8)
        hotbuffer.append(tmp, entity_id, dup_ts, 151.0, TZ)
        hotbuffer.append(tmp, entity_id, dup_ts, 151.0, TZ)  # exaktes Duplikat
        hotbuffer.append(tmp, entity_id, _ts(2024, 7, 1, 9), 138.0, TZ)
        for ts, value in [(dup_ts, 151.0), (dup_ts, 151.0), (_ts(2024, 7, 1, 9), 138.0)]:
            index.record_write(entity_id, ts)

        now = datetime(2024, 7, 1, 12, tzinfo=TZ)
        window = (_ts(2024, 7, 1, 0), _ts(2024, 7, 2, 0))
        before = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert len(before) == 3
        assert sum(1 for ts, _ in before if ts == dup_ts) == 2

        # Nur EIN Vorkommen des Duplikats löschen (wie eine ausgewählte Zeile,
        # nicht beide).
        cleanup.soft_delete(index, entity_id, [dup_ts])
        after = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert len(after) == 2
        assert sum(1 for ts, _ in after if ts == dup_ts) == 1  # eines bleibt übrig
        assert _ts(2024, 7, 1, 9) in [ts for ts, _ in after]

        undone = cleanup.undo_last_delete(index, entity_id)
        assert undone == 1
        restored = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert sum(1 for ts, _ in restored if ts == dup_ts) == 2

        # Beide Vorkommen löschen (zwei ausgewählte Zeilen -> zwei Einträge in der Liste).
        cleanup.soft_delete(index, entity_id, [dup_ts, dup_ts])
        after_both = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ, now=now)
        assert sum(1 for ts, _ in after_both if ts == dup_ts) == 0

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_raw_rows_spans_two_archived_months() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({"ts": [_ts(2024, 7, 31, 10)], "value": [19.0]}), archive_dir / "2024-07.parquet"
        )
        pq.write_table(
            pa.table({"ts": [_ts(2024, 8, 1, 9)], "value": [20.0]}), archive_dir / "2024-08.parquet"
        )

        window = (_ts(2024, 7, 1, 0), _ts(2024, 9, 1, 0))
        rows = cleanup.list_raw_rows(tmp, index, entity_id, *window, TZ)
        assert [v for _, v in rows] == [19.0, 20.0]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_raw_rows_stops_at_configured_result_limit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.limited"
        index.get_or_create_entity(entity_id, "sensor", "measurement", None)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table(
                {
                    "ts": [_ts(2024, 8, 1, 8), _ts(2024, 8, 1, 9), _ts(2024, 8, 1, 10)],
                    "value": [1.0, 2.0, 3.0],
                }
            ),
            archive_dir / "2024-08.parquet",
        )
        try:
            cleanup.list_raw_rows(
                tmp, index, entity_id, _ts(2024, 8, 1, 0), _ts(2024, 9, 1, 0), TZ,
                max_rows=2,
            )
            assert False, "ResultLimitExceeded erwartet"
        except cleanup.ResultLimitExceeded:
            pass
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_streaming_analysis_pages_complete_history_without_materializing_it() -> None:
    calls = 0

    def rows_factory():
        nonlocal calls
        calls += 1
        return ((float(i), float(i)) for i in range(1_000))

    analysis = cleanup.analyze_raw_rows_page(
        rows_factory,
        filter_="all",
        page=2,
        page_size=10,
        gap_threshold_minutes=None,
        outlier_threshold_percent=None,
        tz=TZ,
    )

    assert calls == 2
    assert analysis["counts"]["all"] == 1_000
    assert analysis["pagination"] == {
        "page": 2,
        "page_size": 10,
        "total": 1_000,
        "total_pages": 100,
        "start": 11,
        "end": 20,
    }
    assert [row["value"] for row in analysis["rows"]] == list(
        map(float, range(989, 979, -1))
    )


def test_streaming_analysis_preserves_duplicate_filter_semantics() -> None:
    rows = [(1.0, 10.0), (2.0, 20.0), (2.0, 21.0), (3.0, 30.0)]
    analysis = cleanup.analyze_raw_rows_page(
        lambda: iter(rows),
        filter_="duplicates",
        page=1,
        page_size=50,
        gap_threshold_minutes=None,
        outlier_threshold_percent=None,
        tz=TZ,
    )

    assert analysis["counts"]["duplicates"] == 2
    assert [row["value"] for row in analysis["rows"]] == [21.0, 20.0]
    assert all(row["flags"] == [{
        "label": "Duplikat",
        "reason": "2× derselbe Zeitstempel — Werte: 20 / 21",
    }] for row in analysis["rows"])


def test_purge_hot_buffer_removes_soft_deleted_rows_from_current_month_only() -> None:
    """purge_hot_buffer() entfernt weich gelöschte Vorkommen physisch aus dem
    laufenden Monat (Hot Buffer) und räumt die zugehörigen deleted_points-
    Einträge auf — ein Vorkommen in einem bereits ARCHIVIERTEN Monat bleibt
    dagegen unangetastet (nur weich gefiltert, kein Parquet-Rewrite in dieser
    ersten Fassung, siehe Konzept "Offene Punkte")."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.leistung"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        # laufender Monat (August): drei Werte im Hot Buffer, einer davon
        # doppelt (Duplikat) — nur EIN Vorkommen des Duplikats wird gelöscht.
        dup_ts = _ts(2024, 8, 10, 8)
        for ts, value in [(dup_ts, 100.0), (dup_ts, 100.0), (_ts(2024, 8, 11, 9), 110.0)]:
            hotbuffer.append(tmp, entity_id, ts, value, TZ)
            index.record_write(entity_id, ts)
        cleanup.soft_delete(index, entity_id, [dup_ts])

        # bereits archivierter Monat (Juli): ein weich gelöschter Wert, der
        # NICHT physisch entfernt werden darf.
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        july_ts = _ts(2024, 7, 20, 10)
        pq.write_table(pa.table({"ts": [july_ts], "value": [90.0]}), archive_dir / "2024-07.parquet")
        cleanup.soft_delete(index, entity_id, [july_ts])

        assert index.get_deleted_points_count() == 2

        purged = cleanup.purge_hot_buffer(tmp, index, TZ, now=now)
        assert purged == 1  # nur das eine Duplikat-Vorkommen im laufenden Monat

        # Hot Buffer ist jetzt physisch bereinigt: nur noch 2 Zeilen, keine
        # deleted_points-Filterung für den August-Zeitstempel mehr nötig.
        hot_file = hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ)
        remaining = hotbuffer.read_rows(hot_file)
        assert sorted(remaining) == sorted([(dup_ts, 100.0), (_ts(2024, 8, 11, 9), 110.0)])

        # Der Juli-Eintrag bleibt als Soft-Delete bestehen (Archiv unangetastet).
        assert index.get_deleted_points_count() == 1
        archive_rows = pq.read_table(archive_dir / "2024-07.parquet").to_pylist()
        assert len(archive_rows) == 1  # Datei selbst unverändert

        entity = index.get_entity(entity_id)
        assert entity["row_count"] == 3 - 1  # ursprüngliche 3 Schreibvorgänge minus 1 purged

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_purge_reports_hot_archive_and_missing_without_changes() -> None:
    """Die Vorschau zählt exakt, bleibt aber vollständig schreibfrei."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(
            entity_id, "sensor", "measurement", "°C", friendly_name="Temperatur"
        )
        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        hot_ts = _ts(2024, 8, 10, 8)
        archive_ts = _ts(2024, 7, 5, 8)
        missing_ts = _ts(2024, 6, 1, 8)

        hotbuffer.append(tmp, entity_id, hot_ts, 21.0, TZ)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(pa.table({"ts": [archive_ts], "value": [19.5]}), archive_path)
        cleanup.soft_delete(index, entity_id, [hot_ts, archive_ts, missing_ts])

        hot_file = hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ)
        hot_before = hot_file.read_bytes()
        archive_before = archive_path.read_bytes()
        preview = cleanup.preview_purge(tmp, index, TZ, now=now)

        assert preview["totals"] == {
            "marked_rows": 3,
            "removable_rows": 2,
            "hot_rows": 1,
            "archive_rows": 1,
            "archive_months": 1,
            "entities_affected": 1,
            "not_removable_rows": 1,
        }
        assert preview["rows"] == [{
            "entity_id": entity_id,
            "friendly_name": "Temperatur",
            "marked_rows": 3,
            "removable_rows": 2,
            "hot_rows": 1,
            "archive_rows": 1,
            "archive_months": 1,
            "not_removable_rows": 1,
        }]
        assert index.get_deleted_points_count() == 3
        assert hot_file.read_bytes() == hot_before
        assert archive_path.read_bytes() == archive_before

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_purge_does_not_open_archive_months_without_marked_rows(monkeypatch) -> None:
    """ZP-005 (PERFORMANCE.md): ein Archiv-Monat ohne markierte Zeitstempel
    darf in der Vorschau nicht geöffnet/gelesen werden, auch wenn andere
    Monate derselben Entität betroffen sind."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        now = datetime(2024, 9, 15, 12, tzinfo=TZ)

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        # Juli ist betroffen (markierter Zeitstempel), August nicht.
        july_ts = _ts(2024, 7, 5, 8)
        pq.write_table(
            pa.table({"ts": [july_ts], "value": [19.5]}), archive_dir / "2024-07.parquet"
        )
        pq.write_table(
            pa.table({"ts": [_ts(2024, 8, 5, 8)], "value": [20.0]}),
            archive_dir / "2024-08.parquet",
        )
        cleanup.soft_delete(index, entity_id, [july_ts])

        opened: list[str] = []
        original_read_table = cleanup.pq.read_table

        def recording_read_table(path, *args, **kwargs):
            opened.append(Path(path).name)
            return original_read_table(path, *args, **kwargs)

        monkeypatch.setattr(cleanup.pq, "read_table", recording_read_table)

        preview = cleanup.preview_purge(tmp, index, TZ, now=now)

        assert opened == ["2024-07.parquet"]
        assert preview["totals"]["archive_rows"] == 1
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_purge_archived_months_rewrites_file_and_recomputes_rollup() -> None:
    """Anders als purge_hot_buffer() muss purge_archived_months() eine echte
    Archivdatei neu schreiben UND die zugehörige Rollup-Zeile (hier: Stunde,
    Standard-Entität) passend neu berechnen — sonst würden Rohdaten und
    Rollup-Aggregation nach dem Purge auseinanderlaufen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        dup_ts = _ts(2024, 7, 5, 8)
        rows = [(dup_ts, 20.0), (dup_ts, 20.0), (_ts(2024, 7, 20, 8), 25.0)]
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]}), archive_path)
        index.add_row_count(entity_id, len(rows))
        index.set_first_ts(entity_id, rows[0][0])
        cleanup.soft_delete(index, entity_id, [dup_ts])  # nur EIN Vorkommen des Duplikats

        result = cleanup.purge_archived_months(tmp, index, TZ, now=datetime(2024, 8, 15, tzinfo=TZ))

        assert result == {"rows_purged": 1, "months_purged": 1}
        remaining = pq.read_table(archive_path).to_pylist()
        assert sorted((r["ts"], r["value"]) for r in remaining) == sorted(
            [(dup_ts, 20.0), (_ts(2024, 7, 20, 8), 25.0)]
        )
        assert index.get_entity(entity_id)["row_count"] == 2
        assert index.get_deleted_points_count() == 0

        stunde_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "stunde")).to_pylist()
        assert len(stunde_table) == 2  # zwei verschiedene Stunden-Buckets (5. und 20. Juli)
        monat_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "monat")).to_pylist()
        assert len(monat_table) == 1
        assert monat_table[0]["value"] == 22.5  # Mittelwert aus 20.0 und 25.0
        assert monat_table[0]["min_value"] == 20.0
        assert monat_table[0]["max_value"] == 25.0

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_purge_archived_months_removes_entirely_emptied_month_and_updates_first_ts() -> None:
    """Wenn JEDER Rohwert eines archivierten Monats weich gelöscht war, muss
    der Purge die Datei UND die Rollup-Zeilen dieses Monats komplett entfernen
    (keine leere Parquet-Datei/Monats-Zeile ohne Grundlage) und first_ts auf
    den neuen frühesten verbliebenen Wert (hier: Juli) nachziehen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)

        june_ts = _ts(2024, 6, 10, 8)
        pq.write_table(pa.table({"ts": [june_ts], "value": [18.0]}), archive_dir / "2024-06.parquet")
        july_ts = _ts(2024, 7, 5, 8)
        pq.write_table(pa.table({"ts": [july_ts], "value": [20.0]}), archive_dir / "2024-07.parquet")
        index.add_row_count(entity_id, 2)
        index.set_first_ts(entity_id, june_ts)
        cleanup.soft_delete(index, entity_id, [june_ts])  # der einzige Wert im Juni

        result = cleanup.purge_archived_months(tmp, index, TZ, now=datetime(2024, 8, 15, tzinfo=TZ))

        assert result == {"rows_purged": 1, "months_purged": 1}
        assert not (archive_dir / "2024-06.parquet").exists()
        assert (archive_dir / "2024-07.parquet").exists()
        assert not rollup.rollup_path(tmp, entity_id, "stunde").exists()
        assert index.get_entity(entity_id)["first_ts"] == july_ts

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_purge_archived_months_is_noop_when_nothing_soft_deleted() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        path = archive_dir / "2024-07.parquet"
        pq.write_table(pa.table({"ts": [_ts(2024, 7, 5, 8)], "value": [20.0]}), path)
        index.add_row_count(entity_id, 1)

        result = cleanup.purge_archived_months(tmp, index, TZ, now=datetime(2024, 8, 15, tzinfo=TZ))

        assert result == {"rows_purged": 0, "months_purged": 0}
        assert path.exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_count_duplicate_rows_by_entity_only_lists_affected_entities_within_window() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        now = datetime(2024, 8, 15, 12, tzinfo=TZ)

        dup_id = "sensor.dup"
        index.get_or_create_entity(dup_id, "sensor", "measurement", "°C", friendly_name="Dup")
        dup_ts = _ts(2024, 8, 10, 8)
        for value in (20.0, 20.0, 20.0):  # zwei überzählige Duplikate
            hotbuffer.append(tmp, dup_id, dup_ts, value, TZ)
            index.record_write(dup_id, dup_ts)

        clean_id = "sensor.clean"
        index.get_or_create_entity(clean_id, "sensor", "measurement", "°C")
        hotbuffer.append(tmp, clean_id, _ts(2024, 8, 10, 9), 5.0, TZ)
        index.record_write(clean_id, _ts(2024, 8, 10, 9))

        old_dup_id = "sensor.old_dup"
        index.get_or_create_entity(old_dup_id, "sensor", "measurement", "°C")
        old_ts = _ts(2024, 1, 10, 8)  # außerhalb des 30-Tage-Fensters vor "now" — bereits archiviert
        archive_dir = tmp / "archive" / old_dup_id
        archive_dir.mkdir(parents=True)
        pq.write_table(pa.table({"ts": [old_ts, old_ts], "value": [1.0, 1.0]}), archive_dir / "2024-01.parquet")
        index.add_row_count(old_dup_id, 2)

        results = cleanup.count_duplicate_rows_by_entity(tmp, index, TZ, window_days=30, now=now)

        assert results == [{"entity_id": dup_id, "friendly_name": "Dup", "count": 2}]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_raw_values_for_timestamps_reads_across_hot_buffer_and_archive() -> None:
    """Für die "Rückgängig"-Vorschau: findet die Werte zu bestimmten
    Zeitstempeln unabhängig davon, ob sie im Hot Buffer (laufender Monat) oder
    einem bereits archivierten Monat liegen — und OHNE Soft-Delete-Filterung,
    im Gegensatz zu list_raw_rows()."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        entity_id = "sensor.temp"
        # "jetzt" liegt im August -> August ist der Hot-Buffer-Monat.
        with_now = datetime(2024, 8, 15, tzinfo=TZ)
        hot_ts = _ts(2024, 8, 10, 8)
        hotbuffer.append(tmp, entity_id, hot_ts, 21.0, TZ)

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archived_ts = _ts(2024, 7, 5, 8)
        pq.write_table(pa.table({"ts": [archived_ts], "value": [19.5]}), archive_dir / "2024-07.parquet")

        # Simuliert: diese Zeitstempel sind weich gelöscht (deshalb NICHT über
        # list_raw_rows lesbar) — get_raw_values_for_timestamps() muss sie
        # trotzdem finden, unabhängig von deleted_points.
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.mark_deleted(entity_id, [hot_ts, archived_ts])

        with_now_ts = with_now.timestamp()
        found = cleanup.get_raw_values_for_timestamps(tmp, entity_id, [hot_ts, archived_ts], TZ, now=with_now)

        assert found == sorted([(archived_ts, 19.5), (hot_ts, 21.0)])
        # zur Kontrolle: list_raw_rows filtert dieselben Zeitstempel tatsächlich raus
        visible = cleanup.list_raw_rows(tmp, index, entity_id, archived_ts - 1, with_now_ts + 1, TZ, now=with_now)
        assert visible == []

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all() -> None:
    if not _PYARROW_AVAILABLE:
        print("übersprungen: pyarrow nicht installiert (siehe addon/requirements.txt)")
        return
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
