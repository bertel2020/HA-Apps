"""Tests für app/storage/cleanup.py — Ausreißer/Lücken/Duplikate, Soft-Delete/Undo."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


import pytest

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import cleanup, hotbuffer, query, resolution, rollup
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


def test_detect_outliers_flags_the_spike_and_the_way_back() -> None:
    """Bezug ist die übliche Schwankung der letzten Werte (Median + MAD, siehe
    cleanup.OutlierDetector), nicht der Vorwert. Ein einzelner Ausreißer
    erzeugt trotzdem ZWEI Markierungen — der Spitzenwert selbst liegt weit vom
    Median, und solange er im Fenster steckt, verschiebt er den Median nicht
    (das ist der Punkt am Median), der Rücksprung liegt danach aber ebenfalls
    außerhalb, weil der Ausreißer die Streuung nicht aufbläht."""
    rows = [(_ts(2024, 7, 1, 8 + i), v) for i, v in enumerate(
        [21.0, 21.4, 20.8, 21.1, 21.3, 21.2, 184.7, 21.2, 21.6]
    )]
    outliers = cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ)
    assert _ts(2024, 7, 1, 14) in outliers
    assert "weiter vom Median der letzten" in outliers[_ts(2024, 7, 1, 14)]


def test_detect_outliers_returns_nothing_when_threshold_is_off() -> None:
    rows = [(_ts(2024, 7, 1, 8 + i), v) for i, v in enumerate(
        [21.0, 21.4, 20.8, 21.1, 21.3, 21.2, 184.7, 21.2, 21.6]
    )]
    assert cleanup.detect_outliers(rows, None, decimals="auto", tz=TZ) == {}


def test_both_row_paths_use_the_same_outlier_rule() -> None:
    """Die kurzen Zeiträume der Bereinigungsseite gehen über detect_outliers(),
    "Jahr"/"Gesamt" über analyze_raw_rows_page(). Dort standen einmal zwei
    verschieden rechnende Regeln, wodurch dieselbe Entität je nach gewähltem
    Zeitraum unterschiedlich viele Ausreißer zeigte."""
    werte = [20.0, 20.2, 19.9, 20.1, 20.3, 20.0, 19.8, 250.0, 20.1, 20.0, 20.2]
    rows = [(float(i * 60), v) for i, v in enumerate(werte)]

    direkt = cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ)
    streaming = cleanup.analyze_raw_rows_page(
        lambda: iter(rows), filter_="outliers", page=1, page_size=50,
        gap_threshold_minutes=None, outlier_factor=10, tz=TZ,
    )
    assert streaming["counts"]["outliers"] == len(direkt)
    assert {row["ts"] for row in streaming["rows"]} == set(direkt)
    assert [row["flags"][0]["reason"] for row in streaming["rows"]] == [
        direkt[row["ts"]] for row in streaming["rows"]
    ]


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
        for ts, _value in [(dup_ts, 151.0), (dup_ts, 151.0), (_ts(2024, 7, 1, 9), 138.0)]:
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
            raise AssertionError("ResultLimitExceeded erwartet")
        except cleanup.ResultLimitExceeded:
            pass
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_iter_raw_rows_shares_hot_read_cache_across_streaming_passes() -> None:
    """analyze_raw_rows_page() ruft rows_factory() zweimal auf (siehe
    test_streaming_analysis_pages_complete_history_without_materializing_it)
    — ohne geteilten hot_rows_loader würde das bei einem Fenster, das den
    laufenden Monat einschließt, dieselbe Hot-CSV zweimal neu von Platte
    lesen (PERFORMANCE.md, ZP-012). main.py teilt dafür einen
    QueryReadCache über beide Durchläufe."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        entity_id = "sensor.a"
        index = Index(tmp / "index.sqlite")
        now = datetime(2026, 8, 20, 12, tzinfo=TZ)
        ts = _ts(2026, 8, 20, 10)
        hotbuffer.append(tmp, entity_id, ts, 1.0, TZ)

        read_cache = query.QueryReadCache()
        calls: list[Path] = []
        original_read_rows = query.read_rows

        def counting_read_rows(path):
            calls.append(path)
            return original_read_rows(path)

        def rows_factory():
            return cleanup.iter_raw_rows(
                tmp, index, entity_id, ts - 10, now.timestamp(), TZ, now=now,
                hot_rows_loader=read_cache.read_hot_rows,
            )

        query.read_rows = counting_read_rows
        try:
            # Simuliert die zwei Durchläufe von analyze_raw_rows_page().
            first_pass = list(rows_factory())
            second_pass = list(rows_factory())
        finally:
            query.read_rows = original_read_rows

        assert first_pass == second_pass == [(ts, 1.0)]
        assert len(calls) == 1
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
        outlier_factor=None,
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
        outlier_factor=None,
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


def test_purge_hot_buffer_older_than_leaves_fresh_marks_untouched() -> None:
    """Grundlage der automatischen Bereinigung (background.py): older_than
    lässt eine gerade erst markierte Zeile stehen, obwohl sie physisch
    entfernbar wäre — sonst liefe "Rückgängig" für sie ins Leere, bevor
    jemand sie zurückholen konnte."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.leistung"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        old_ts, fresh_ts = _ts(2024, 8, 10, 8), _ts(2024, 8, 11, 9)
        for ts, value in [(old_ts, 100.0), (fresh_ts, 110.0)]:
            hotbuffer.append(tmp, entity_id, ts, value, TZ)
            index.record_write(entity_id, ts)
        # old_ts vor 40 Tagen markiert, fresh_ts gerade eben — nur old_ts hat
        # ein übliches 30-Tage-Mindestalter schon erreicht.
        index.mark_deleted(entity_id, [old_ts], deleted_at=now.timestamp() - 40 * 86400)
        index.mark_deleted(entity_id, [fresh_ts], deleted_at=now.timestamp())

        cutoff = now.timestamp() - 30 * 86400
        purged = cleanup.purge_hot_buffer(tmp, index, TZ, now=now, older_than=cutoff)
        assert purged == 1

        hot_file = hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ)
        remaining = hotbuffer.read_rows(hot_file)
        assert sorted(remaining) == [(fresh_ts, 110.0)]
        assert index.get_deleted_points_count() == 1  # fresh_ts bleibt markiert

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_read_values_for_timestamps_reads_hot_buffer_and_archive() -> None:
    """Grundlage der zweiten Ebene der "Markierte Datensätze"-Detailansicht
    (Housekeeping → Speicherplatz): der Wert eines weich gelöschten
    Zeitstempels steht sonst nirgends mehr, weil er aus allen normalen
    Ansichten rausgefiltert wird — read_values_for_timestamps() holt ihn
    gezielt aus Hot Buffer (laufender Monat) oder Archiv (älterer Monat)
    nach, je nachdem wo der Zeitstempel liegt. Ein Zeitstempel ohne
    Rohdaten-Treffer (hier: einer, der nie geschrieben wurde) fehlt im
    Ergebnis statt mit None aufzutauchen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        entity_id = "sensor.temp"
        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        hot_ts = _ts(2024, 8, 10, 8)
        hotbuffer.append(tmp, entity_id, hot_ts, 21.5, TZ)

        archive_ts = _ts(2024, 7, 5, 8)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        pq.write_table(pa.table({"ts": [archive_ts], "value": [19.0]}), archive_dir / "2024-07.parquet")

        missing_ts = _ts(2024, 6, 1, 8)  # nie geschrieben

        values = cleanup.read_values_for_timestamps(
            tmp, entity_id, [hot_ts, archive_ts, missing_ts], TZ, now=now
        )

        assert values == {hot_ts: 21.5, archive_ts: 19.0}
        assert missing_ts not in values
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


def test_remove_deleted_points_with_no_matching_row_cleans_up_only_orphans() -> None:
    """Fund vom 18.09.2026: retention.enforce_retention_for_entity() räumte
    deleted_points bisher nicht auf, wenn die Aufbewahrung einen kompletten
    Archiv-Monat löschte — für bereits VOR diesem Fix so entstandene
    Markierungen gibt es (anders als bei compact_raw_values(), siehe
    compacted_months oben) keine Tabelle, die festhält, welche Monate das
    waren. Dieser Nachzieh-Lauf prüft deshalb direkt gegen die Realität und
    entfernt nur, was nirgends mehr eine passende Rohdatenzeile hat — eine
    noch nicht purgte Markierung in Hot Buffer oder Archiv bleibt
    unangetastet."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        hot_ts = _ts(2024, 8, 10, 8)
        archive_ts = _ts(2024, 7, 5, 8)
        orphaned_ts = _ts(2024, 6, 1, 8)  # z. B. per Aufbewahrung gelöschter Monat

        hotbuffer.append(tmp, entity_id, hot_ts, 21.0, TZ)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        pq.write_table(
            pa.table({"ts": [archive_ts], "value": [19.5]}), archive_dir / "2024-07.parquet"
        )
        cleanup.soft_delete(index, entity_id, [hot_ts, archive_ts, orphaned_ts])
        assert index.get_deleted_points_count() == 3

        removed_first = cleanup.remove_deleted_points_with_no_matching_row(tmp, index, TZ, now=now)
        assert removed_first == 1
        remaining = index.get_deleted_counts_for_entity(entity_id)
        assert remaining == {hot_ts: 1, archive_ts: 1}

        removed_second = cleanup.remove_deleted_points_with_no_matching_row(tmp, index, TZ, now=now)
        assert removed_second == 0  # idempotent, nichts mehr zu tun

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


def test_purge_archived_months_older_than_leaves_fresh_marks_untouched() -> None:
    """Wie test_purge_hot_buffer_older_than_leaves_fresh_marks_untouched, nur
    für einen bereits archivierten Monat: die frisch markierte Zeile bleibt in
    der Parquet-Datei stehen, nur die alte wird tatsächlich entfernt."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        old_ts, fresh_ts = _ts(2024, 7, 5, 8), _ts(2024, 7, 20, 8)
        rows = [(old_ts, 20.0), (fresh_ts, 25.0)]
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]}), archive_path)
        index.add_row_count(entity_id, len(rows))
        index.set_first_ts(entity_id, old_ts)

        now = datetime(2024, 8, 15, tzinfo=TZ)
        index.mark_deleted(entity_id, [old_ts], deleted_at=now.timestamp() - 40 * 86400)
        index.mark_deleted(entity_id, [fresh_ts], deleted_at=now.timestamp())

        cutoff = now.timestamp() - 30 * 86400
        result = cleanup.purge_archived_months(tmp, index, TZ, now=now, older_than=cutoff)

        assert result == {"rows_purged": 1, "months_purged": 1}
        remaining = pq.read_table(archive_path).to_pylist()
        assert [(r["ts"], r["value"]) for r in remaining] == [(fresh_ts, 25.0)]
        assert index.get_deleted_points_count() == 1  # fresh_ts bleibt markiert

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


def test_purge_archived_months_preserves_min_max_of_untouched_resolution_rows() -> None:
    """Ein archivierter Monat kann Zeilen enthalten, die die Standard-
    Live-Auflösung (resolution.py) als Ø mehrerer Rohwerte geschrieben hat
    (min_value/max_value gesetzt). Ein Purge, der eine ANDERE Zeile dieses
    Monats entfernt, darf diese Spalten bei den unberührten Zeilen nicht
    stillschweigend verwerfen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        dup_ts = _ts(2024, 7, 5, 8)
        resolved_ts = _ts(2024, 7, 5, 9)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": [dup_ts, dup_ts, resolved_ts],
                "value": [20.0, 20.0, 21.85],
                "min_value": [None, None, 21.0],
                "max_value": [None, None, 24.8],
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 3)
        index.set_first_ts(entity_id, dup_ts)
        cleanup.soft_delete(index, entity_id, [dup_ts])  # nur EIN Vorkommen

        result = cleanup.purge_archived_months(tmp, index, TZ, now=datetime(2024, 8, 15, tzinfo=TZ))

        assert result == {"rows_purged": 1, "months_purged": 1}
        remaining = {r["ts"]: r for r in pq.read_table(archive_path).to_pylist()}
        assert remaining[resolved_ts]["min_value"] == 21.0
        assert remaining[resolved_ts]["max_value"] == 24.8

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_correct_raw_value_clears_min_max_only_on_the_corrected_row() -> None:
    """correct_raw_value() darf die Ø/Min/Max-Spalte einer per Auflösung
    zusammengefassten Zeile nur bei der WIRKLICH korrigierten Zeile löschen
    (der neue Wert ist kein Ø mehr) — alle anderen Zeilen desselben Archiv-
    Monats müssen ihre Min/Max-Werte behalten."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        wrong_ts = _ts(2024, 7, 5, 8)
        untouched_ts = _ts(2024, 7, 5, 9)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": [wrong_ts, untouched_ts],
                "value": [999.0, 21.85],
                "min_value": [20.5, 21.0],
                "max_value": [21.5, 24.8],
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 2)
        index.set_first_ts(entity_id, wrong_ts)

        changed = cleanup.correct_raw_value(
            tmp, index, entity_id, wrong_ts, 999.0, 21.2, TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
        )

        assert changed is True
        remaining = {r["ts"]: r for r in pq.read_table(archive_path).to_pylist()}
        assert remaining[wrong_ts]["value"] == 21.2
        assert remaining[wrong_ts]["min_value"] is None
        assert remaining[wrong_ts]["max_value"] is None
        assert remaining[untouched_ts]["min_value"] == 21.0
        assert remaining[untouched_ts]["max_value"] == 24.8

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_add_raw_value_preserves_min_max_of_existing_archive_rows() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        existing_ts = _ts(2024, 7, 5, 8)
        new_ts = _ts(2024, 7, 5, 9)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": [existing_ts],
                "value": [21.85],
                "min_value": [21.0],
                "max_value": [24.8],
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 1)
        index.set_first_ts(entity_id, existing_ts)

        cleanup.add_raw_value(
            tmp, index, entity_id, new_ts, 19.5, TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
        )

        remaining = {r["ts"]: r for r in pq.read_table(archive_path).to_pylist()}
        assert remaining[existing_ts]["min_value"] == 21.0
        assert remaining[existing_ts]["max_value"] == 24.8
        assert remaining[new_ts]["value"] == 19.5
        assert remaining[new_ts]["min_value"] is None

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


def test_compact_raw_values_counter_keeps_last_value_per_bucket_and_marks_reset_separately() -> None:
    """Zähler: letzter Wert je Bucket (Teleskopsumme bleibt exakt korrekt),
    plus Reset-Zeitpunkte (detect_counter_decreases) zusätzlich als eigene
    Rohzeile — unabhängig vom Raster, sonst würde ein echter Zählerrücksprung
    im Bucket-Mittendrin verschluckt."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.counter"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        # "+ 1": 08:00:00 selbst liegt zufällig exakt auf einer 5-Minuten-
        # Rastergrenze (bucket_end(300, t) == t dort) — ein Sekunde später
        # garantiert, dass alle vier Zeitstempel im SELBEN Fenster liegen.
        base = _ts(2024, 7, 5, 8) + 1
        t0, t1, t2, t3 = base, base + 10, base + 20, base + 30
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": [t0, t1, t2, t3],
                "value": [10.0, 20.0, 5.0, 8.0],  # t2 ist ein Rücksprung (5 < 20)
                "min_value": [None, None, None, None],
                "max_value": [None, None, None, None],
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 4)
        index.set_first_ts(entity_id, t0)

        # Vorbedingung: alle vier Zeitstempel liegen im selben 5-Minuten-Fenster
        # — unabhängig von der Epoch-Phase, deshalb über die echte Funktion
        # geprüft statt angenommen.
        bucket_ts = resolution.bucket_end(300, t3)
        assert resolution.bucket_end(300, t0) == bucket_ts

        result = cleanup.compact_raw_values(
            tmp, index, entity_id, t0, t3, "5min", TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
        )

        assert result == {
            "rows_before": 4, "rows_after": 2, "months_compacted": ["2024-07"], "stale_markers_removed": 0,
        }
        rows = {r["ts"]: r for r in pq.read_table(archive_path).to_pylist()}
        assert set(rows) == {bucket_ts, t2}
        assert rows[bucket_ts]["value"] == 8.0  # letzter Wert im Fenster
        assert rows[t2]["value"] == 5.0  # Reset-Punkt, unangetastet

        marker = index.get_compacted_month(entity_id, 2024, 7)
        assert marker["target_resolution"] == "5min"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compact_raw_values_standard_averages_values_and_composes_min_max() -> None:
    """Standard: Ø der Bucket-Werte, Min/Max über value UND ggf. bereits
    vorhandene min_value/max_value (Zeilen, die die Live-Auflösung schon
    vor-aggregiert hat)."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        # "+ 1", siehe test_compact_raw_values_counter_keeps_last_value_per_bucket…
        base = _ts(2024, 7, 5, 8) + 1
        t0, t1, t2 = base, base + 10, base + 20
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": [t0, t1, t2],
                "value": [20.0, 22.0, 24.0],
                "min_value": [None, 18.0, None],  # t1 schon Ø einer Auflösungs-Zeile
                "max_value": [None, 26.0, None],
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 3)
        index.set_first_ts(entity_id, t0)

        bucket_ts = resolution.bucket_end(300, t2)
        assert resolution.bucket_end(300, t0) == bucket_ts

        result = cleanup.compact_raw_values(
            tmp, index, entity_id, t0, t2, "5min", TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
        )

        assert result == {
            "rows_before": 3, "rows_after": 1, "months_compacted": ["2024-07"], "stale_markers_removed": 0,
        }
        rows = {r["ts"]: r for r in pq.read_table(archive_path).to_pylist()}
        assert rows[bucket_ts]["value"] == sum([20.0, 22.0, 24.0]) / 3
        assert rows[bucket_ts]["min_value"] == 18.0
        assert rows[bucket_ts]["max_value"] == 26.0

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compact_raw_values_removes_stale_markers_in_the_compacted_month_only() -> None:
    """Fund vom 18.09.2026: eine Löschmarkierung für einen Zeitstempel, der
    gerade verdichtet wird, existiert danach für keine Rohdatenzeile mehr
    (weder alt noch neu) — compact_raw_values() muss sie deshalb selbst
    aufräumen, sonst bliebe sie für immer als "Löschmarkierung ohne passende
    Rohdatenzeile" liegen. Eine Markierung in einem ANDEREN, nicht
    verdichteten Monat bleibt dagegen unangetastet."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        base = _ts(2024, 7, 5, 8) + 1
        t0, t1, t2 = base, base + 10, base + 20
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({"ts": [t0, t1, t2], "value": [20.0, 22.0, 24.0]}),
            archive_path,
        )
        index.add_row_count(entity_id, 3)
        index.set_first_ts(entity_id, t0)
        # t1 liegt im zu verdichtenden Juli, außerdem noch nicht bereinigt
        # markiert — und ein zweiter Marker in einem GANZ ANDEREN Monat
        # (Juni), der von dieser Verdichtung nicht betroffen sein darf.
        june_ts = _ts(2024, 6, 1, 8)
        index.mark_deleted(entity_id, [t1, june_ts])
        assert index.get_deleted_points_count() == 2

        result = cleanup.compact_raw_values(
            tmp, index, entity_id, t0, t2, "5min", TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
        )

        assert result["stale_markers_removed"] == 1
        assert index.get_deleted_points_count() == 1
        assert index.get_deleted_counts_for_entity(entity_id) == {june_ts: 1}

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_remove_deleted_points_for_already_compacted_months_is_a_retroactive_backfill() -> None:
    """Für Installationen, die schon VOR dem obigen Fix verdichtet haben:
    der einmalige Nachzieh-Lauf (background.py BackgroundService.start())
    räumt verwaiste Markierungen anhand der compacted_months-Tabelle auf,
    ohne erneut zu verdichten — und ist beim zweiten Lauf ein No-op."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        already_compacted_ts = _ts(2024, 5, 10, 8)  # Mai bereits früher verdichtet
        untouched_ts = _ts(2024, 6, 10, 8)  # Juni nie verdichtet
        index.mark_deleted(entity_id, [already_compacted_ts, untouched_ts])
        index.set_compacted_month(entity_id, 2024, 5, "1h", already_compacted_ts)

        removed_first = cleanup.remove_deleted_points_for_already_compacted_months(index, TZ)
        assert removed_first == 1
        assert index.get_deleted_counts_for_entity(entity_id) == {untouched_ts: 1}

        removed_second = cleanup.remove_deleted_points_for_already_compacted_months(index, TZ)
        assert removed_second == 0  # idempotent, nichts mehr zu tun

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compact_raw_values_standard_month_is_never_compacted_twice() -> None:
    """Doppel-Verdichtung-Schutz: ein Standard-Monat wird nie erneut
    verdichtet (Ø aus bereits gemittelten Ø-Werten wäre ohne mitgeführte
    Stichprobenanzahl verzerrt) — der zweite Aufruf ist ein vollständiges No-op."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        base = _ts(2024, 7, 5, 8)
        t0, t1 = base, base + 10
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({"ts": [t0, t1], "value": [20.0, 22.0], "min_value": [None, None], "max_value": [None, None]}),
            archive_path,
        )
        index.add_row_count(entity_id, 2)
        index.set_first_ts(entity_id, t0)
        now = datetime(2024, 8, 15, tzinfo=TZ)

        first = cleanup.compact_raw_values(tmp, index, entity_id, t0, t1, "5min", TZ, now=now)
        assert first["months_compacted"] == ["2024-07"]
        rows_after_first = pq.read_table(archive_path).to_pylist()

        second = cleanup.compact_raw_values(tmp, index, entity_id, t0, t1, "1h", TZ, now=now)
        assert second == {
            "rows_before": 0, "rows_after": 0, "months_compacted": [], "stale_markers_removed": 0,
        }
        assert pq.read_table(archive_path).to_pylist() == rows_after_first

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compact_raw_values_counter_month_allows_coarser_recompaction_but_not_finer() -> None:
    """Doppel-Verdichtung-Schutz bei Zählern: ein gröberes Ziel danach ist
    mathematisch exakt (letzter Wert im großen Bucket ist zwangsläufig auch
    der letzte unter den bereits verdichteten kleineren Buckets) und deshalb
    erlaubt — dasselbe oder ein feineres Ziel ist es nicht."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.counter"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        base = _ts(2024, 7, 5, 8)
        timestamps = [base + i * 20 for i in range(6)]
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": timestamps, "value": values,
                "min_value": [None] * 6, "max_value": [None] * 6,
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 6)
        index.set_first_ts(entity_id, timestamps[0])
        now = datetime(2024, 8, 15, tzinfo=TZ)

        first = cleanup.compact_raw_values(tmp, index, entity_id, timestamps[0], timestamps[-1], "1min", TZ, now=now)
        assert first["months_compacted"] == ["2024-07"]
        rows_after_first = len(pq.read_table(archive_path).to_pylist())
        assert index.get_compacted_month(entity_id, 2024, 7)["target_resolution"] == "1min"

        same_target = cleanup.compact_raw_values(
            tmp, index, entity_id, timestamps[0], timestamps[-1], "1min", TZ, now=now
        )
        assert same_target == {
            "rows_before": 0, "rows_after": 0, "months_compacted": [], "stale_markers_removed": 0,
        }

        coarser = cleanup.compact_raw_values(
            tmp, index, entity_id, timestamps[0], timestamps[-1], "5min", TZ, now=now
        )
        assert coarser["months_compacted"] == ["2024-07"]
        assert coarser["rows_before"] == rows_after_first
        assert index.get_compacted_month(entity_id, 2024, 7)["target_resolution"] == "5min"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compact_raw_values_rejects_switch_entities() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "binary_sensor.online"
        index.get_or_create_entity(entity_id, "binary_sensor", None, None)

        with pytest.raises(cleanup.CompactionError):
            cleanup.compact_raw_values(tmp, index, entity_id, 0.0, 1e12, "5min", TZ)

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_compact_raw_values_never_touches_the_current_month_even_if_archived() -> None:
    """Der laufende Monat wird stillschweigend übersprungen, nicht abgelehnt —
    ein bis "heute" reichender Zeitraum soll nur die bereits archivierten
    Monate darin verdichten. (Eine Archivdatei für den laufenden Monat ist ein
    künstlicher Randfall hier, um genau diese Schutzregel isoliert zu prüfen.)"""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.counter"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        current_month_ts = _ts(2024, 8, 5, 8)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        pq.write_table(
            pa.table({
                "ts": [current_month_ts], "value": [10.0],
                "min_value": [None], "max_value": [None],
            }),
            archive_dir / "2024-08.parquet",
        )
        index.add_row_count(entity_id, 1)
        index.set_first_ts(entity_id, current_month_ts)

        result = cleanup.compact_raw_values(
            tmp, index, entity_id, current_month_ts, current_month_ts + 3600, "5min", TZ,
            now=datetime(2024, 8, 15, tzinfo=TZ),
        )

        assert result == {
            "rows_before": 0, "rows_after": 0, "months_compacted": [], "stale_markers_removed": 0,
        }
        assert index.get_compacted_month(entity_id, 2024, 8) is None

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_compact_raw_values_matches_the_actual_run() -> None:
    """Die Vorschau (rein lesend) muss exakt vorhersagen, was der tatsächliche
    Lauf schreibt — beide teilen sich dieselbe Bucketing-Logik
    (_compacted_rows_for_month)."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-cleanup-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        base = _ts(2024, 7, 5, 8)
        t0, t1, t2 = base, base + 10, base + 20
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(
            pa.table({
                "ts": [t0, t1, t2], "value": [20.0, 22.0, 24.0],
                "min_value": [None, None, None], "max_value": [None, None, None],
            }),
            archive_path,
        )
        index.add_row_count(entity_id, 3)
        index.set_first_ts(entity_id, t0)
        now = datetime(2024, 8, 15, tzinfo=TZ)

        preview = cleanup.preview_compact_raw_values(tmp, index, entity_id, t0, t2, "5min", TZ, now=now)
        result = cleanup.compact_raw_values(tmp, index, entity_id, t0, t2, "5min", TZ, now=now)

        assert preview == {"rows_before": result["rows_before"], "rows_after": result["rows_after"], "months": 1}
        assert len(result["months_compacted"]) == 1

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
