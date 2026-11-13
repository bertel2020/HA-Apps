"""Markierte Werte: Verdichten, Duplikat-Vorschau und Rückgängig (Review 2026-11-13, H1–H4)."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from app.storage import cleanup, hotbuffer  # noqa: E402
from app.storage.index import Index, filter_deleted_occurrences  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0) -> float:
    return datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-test-"))
    index = Index(tmp / "index.sqlite")
    try:
        yield tmp, index
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_compaction_drops_marked_rows_instead_of_averaging_them_in(env) -> None:
    tmp, index = env
    entity_id = "sensor.temp"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    base = _ts(2024, 7, 5, 8) + 1
    t0, t1, t2 = base, base + 10, base + 20
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    path = archive_dir / "2024-07.parquet"
    pq.write_table(pa.table({"ts": [t0, t1, t2], "value": [20.0, 9999.0, 24.0]}), path)
    index.add_row_count(entity_id, 3)
    index.set_first_ts(entity_id, t0)
    cleanup.soft_delete(index, entity_id, [t1])

    now = datetime(2024, 8, 15, tzinfo=TZ)
    preview = cleanup.preview_compact_raw_values(tmp, index, entity_id, t0, t2, "5min", TZ, now=now)
    assert preview["rows_before"] == 3 and preview["rows_after"] == 1

    result = cleanup.compact_raw_values(tmp, index, entity_id, t0, t2, "5min", TZ, now=now)

    assert result["rows_before"] == 3
    rows = pq.read_table(path).to_pylist()
    assert len(rows) == 1
    assert rows[0]["value"] == 22.0  # Ø aus 20 und 24, der Ausreißer ist nicht enthalten
    assert rows[0]["max_value"] == 24.0
    assert index.get_deleted_points_count() == 0
    # row_count: 3 Zeilen in der Datei, 1 danach
    assert index.get_entity(entity_id)["row_count"] == 1


def test_counter_compaction_does_not_keep_marked_reset_as_raw_row(env) -> None:
    tmp, index = env
    entity_id = "sensor.zaehler"
    index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    base = _ts(2024, 7, 5, 8) + 1
    ts = [base, base + 10, base + 20]
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    path = archive_dir / "2024-07.parquet"
    pq.write_table(pa.table({"ts": ts, "value": [100.0, 0.0, 101.0]}), path)
    index.add_row_count(entity_id, 3)
    index.set_first_ts(entity_id, ts[0])
    cleanup.soft_delete(index, entity_id, [ts[1]])

    cleanup.compact_raw_values(
        tmp, index, entity_id, ts[0], ts[2], "5min", TZ, now=datetime(2024, 8, 15, tzinfo=TZ)
    )

    values = [r["value"] for r in pq.read_table(path).to_pylist()]
    assert 0.0 not in values
    assert values == [101.0]


def test_archive_rows_are_ordered_by_value_within_a_timestamp(env) -> None:
    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    ts = _ts(2024, 7, 5, 8)
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    # Dateireihenfolge: 5 vor 3
    pq.write_table(pa.table({"ts": [ts, ts, ts + 60], "value": [5.0, 3.0, 4.0]}), archive_dir / "2024-07.parquet")
    now = datetime(2024, 8, 15, tzinfo=TZ)

    rows = list(cleanup.iter_raw_rows(tmp, index, entity_id, ts - 1, ts + 100, TZ, now=now))
    assert rows == [(ts, 3.0), (ts, 5.0), (ts + 60, 4.0)]

    # Vorschau und Wirkung stimmen überein: die Vorschau nennt die 3, und nach dem
    # Markieren ist genau die 3 aus der Anzeige verschwunden.
    to_delete = cleanup.duplicate_rows_to_delete(rows)
    assert to_delete == [(ts, 3.0)]
    cleanup.soft_delete(index, entity_id, [t for t, _ in to_delete])
    after = list(cleanup.iter_raw_rows(tmp, index, entity_id, ts - 1, ts + 100, TZ, now=now))
    assert after == [(ts, 5.0), (ts + 60, 4.0)]


def test_hot_purge_removes_the_same_occurrence_the_view_hides(env) -> None:
    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    ts = _ts(2024, 8, 10, 8)
    for value in (5.0, 3.0):  # Schreibreihenfolge: 5 vor 3
        hotbuffer.append(tmp, entity_id, ts, value, TZ)
        index.record_write(entity_id, ts)
    cleanup.soft_delete(index, entity_id, [ts])

    visible = list(cleanup.iter_raw_rows(tmp, index, entity_id, ts - 1, ts + 1, TZ, now=now))
    assert visible == [(ts, 5.0)]

    assert cleanup.purge_hot_buffer(tmp, index, TZ, now=now) == 1
    remaining = hotbuffer.read_rows(hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ))
    assert remaining == [(ts, 5.0)]  # die 3 ist weg, nicht die 5


def test_filter_hides_first_occurrences_in_value_order() -> None:
    rows = [(1.0, 3.0), (1.0, 5.0)]
    assert filter_deleted_occurrences(rows, {1.0: 1}) == [(1.0, 5.0)]


def test_undo_batch_leaves_a_newer_batch_alone_and_is_idempotent(env) -> None:
    _tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    cleanup.soft_delete(index, entity_id, [10.0, 11.0], deleted_at=1000.0, source="duplicates")
    cleanup.soft_delete(index, entity_id, [20.0], deleted_at=2000.0, source="counter_rule")

    assert index.last_deleted_at(entity_id) == 2000.0
    assert cleanup.undo_delete_batch(index, entity_id, 1000.0) == 2
    assert index.get_deleted_counts_for_entity(entity_id) == {20.0: 1}  # fremde Charge bleibt
    # zweiter Aufruf nimmt nichts weiter zurück
    assert cleanup.undo_delete_batch(index, entity_id, 1000.0) == 0
    assert index.get_deleted_counts_for_entity(entity_id) == {20.0: 1}


def test_undo_duplicate_removal_uses_batch_of_the_bulk_run(env) -> None:
    from contextlib import contextmanager

    from app import data_quality

    _tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    cleanup.soft_delete(index, entity_id, [10.0], deleted_at=1000.0, source="duplicates")
    cleanup.soft_delete(index, entity_id, [20.0], deleted_at=2000.0, source="counter_rule")

    @contextmanager
    def lock(_entity_id):
        yield

    restored = data_quality.undo_duplicate_removal(index, [(entity_id, 1000.0), (entity_id, 1000.0)], lock)
    assert restored == 1
    assert index.get_deleted_counts_for_entity(entity_id) == {20.0: 1}


def test_refresh_snapshot_leaves_an_invalidated_snapshot_alone(env) -> None:
    from app import counter_bulk

    tmp, index = env
    index.set_counter_decrease_snapshot([{"entity_id": "sensor.x", "last": {"ts": 1.0}}])
    index.invalidate_counter_decrease_snapshot()
    before = index.get_counter_decrease_snapshot()
    assert before["checked_at"] == 0
    counter_bulk.refresh_snapshot(tmp, index, TZ, before, {"sensor.x"})
    assert index.get_counter_decrease_snapshot()["checked_at"] == 0  # nicht als frisch gestempelt


def test_alltime_counts_discards_result_when_marked_during_scan(env, monkeypatch) -> None:
    from app import cleanup_stats

    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    ts = _ts(2024, 8, 10, 8)
    hotbuffer.append(tmp, entity_id, ts, 1.0, TZ)
    index.record_write(entity_id, ts)
    entity = index.get_entity(entity_id)
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)

    real = cleanup.analyze_raw_rows_page

    def marks_during_scan(*args, **kwargs):
        result = real(*args, **kwargs)
        cleanup.soft_delete(index, entity_id, [ts])  # Nutzer markiert, während der Scan läuft
        return result

    monkeypatch.setattr(cleanup, "analyze_raw_rows_page", marks_during_scan)
    cleanup_stats.alltime_counts(tmp, index, TZ, entity, now, force=True)
    assert index.get_cleanup_alltime_stats(entity_id) is None  # veraltetes Ergebnis nicht gespeichert


def test_mark_unknown_makes_matrix_entry_pending_in_total_period(env) -> None:
    from app import data_quality

    _tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    index.set_first_ts_and_add_rows(entity_id, 1_700_000_000.0, 0)
    index.set_cleanup_alltime_stats(entity_id, {"all": 10, "duplicates": 0}, "off", cleanup.OUTLIER_RULE_VERSION, daily={}, gap_threshold="off")
    data_quality.mark_stats_unknown(index, entity_id)
    entry = index.get_cleanup_alltime_stats(entity_id)
    assert "counts" not in entry and "daily" not in entry and entry["computed_at"] == 0
    assert data_quality.build_matrix(index, period="all")["pending"] >= 1


def test_add_and_correct_invalidate_caches_and_last_value(env) -> None:
    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    ts = _ts(2024, 8, 10, 8)
    hotbuffer.append(tmp, entity_id, ts, 999.0, TZ)
    index.record_write(entity_id, ts)
    index.set_setting(index.VALUE_RANGE_KEY_PREFIX + entity_id, '{"computed_at": 4102444800, "deleted_count": 0}')
    index.set_cleanup_alltime_stats(entity_id, {"all": 1}, "off", cleanup.OUTLIER_RULE_VERSION)
    generation = index.finding_generation(entity_id)

    assert cleanup.correct_raw_value(tmp, index, entity_id, ts, 999.0, 5.0, TZ, now=now)

    assert index.get_entity(entity_id)["last_value"] == 5.0
    assert index.finding_generation(entity_id) == generation + 1
    assert index.get_cleanup_alltime_stats(entity_id)["computed_at"] == 0
    assert '"computed_at": 0' in index.get_setting(index.VALUE_RANGE_KEY_PREFIX + entity_id)


def test_archive_rewrite_is_atomic_and_leaves_no_residue_on_failure(env, monkeypatch) -> None:
    tmp, index = env
    entity_id = "sensor.a"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    ts = _ts(2024, 7, 5, 8)
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    path = archive_dir / "2024-07.parquet"
    pq.write_table(pa.table({"ts": [ts], "value": [1.0]}), path)
    original = path.read_bytes()

    def failing_write(*_args, **_kwargs):
        raise OSError("Platte voll")

    monkeypatch.setattr(cleanup.pq, "write_table", failing_write)
    with pytest.raises(OSError):
        cleanup.add_raw_value(tmp, index, entity_id, ts + 60, 2.0, TZ, now=datetime(2024, 8, 15, tzinfo=TZ))

    assert path.read_bytes() == original  # Archivmonat unverändert
    assert [p.name for p in archive_dir.iterdir()] == ["2024-07.parquet"]  # kein Temp-Rest


def _entity_with_duplicates(tmp, index, entity_id: str) -> None:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    ts = datetime.now(TZ).replace(day=1, hour=0, minute=1, second=0, microsecond=0).timestamp()
    for _ in range(2):
        hotbuffer.append(tmp, entity_id, ts, 5.0, TZ)
        index.record_write(entity_id, ts)


def test_bulk_duplicate_removal_survives_a_failing_entity(env) -> None:
    from contextlib import contextmanager

    from app import data_quality

    tmp, index = env
    _entity_with_duplicates(tmp, index, "sensor.kaputt")
    _entity_with_duplicates(tmp, index, "sensor.gut")

    @contextmanager
    def lock(entity_id):
        if entity_id == "sensor.kaputt":
            raise TimeoutError("Speicher gesperrt")
        yield

    preview = data_quality.preview_duplicate_removal(tmp, index, TZ, ["sensor.kaputt", "sensor.gut"], lock)
    assert [r["entity_id"] for r in preview["rows"]] == ["sensor.gut"]
    assert preview["failed"] == ["sensor.kaputt"]

    done = data_quality.remove_duplicates(tmp, index, TZ, ["sensor.kaputt", "sensor.gut"], lock)
    assert [r["entity_id"] for r in done["rows"]] == ["sensor.gut"]  # mit Kennung für „Rückgängig“
    assert done["rows"][0]["batch_at"]
    assert done["failed"] == ["sensor.kaputt"]
    assert done["total"] == 1


def test_counter_rule_continues_after_a_failing_entity_and_reads_only_new_data(env, monkeypatch) -> None:
    from contextlib import nullcontext

    from app import counter_auto

    tmp, index = env
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    for entity_id in ("sensor.z1", "sensor.z2"):
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    seen = []

    def fake_process(data_dir, idx, tz, entity, current, last_ok=None):
        seen.append((entity["entity_id"], last_ok))
        if entity["entity_id"] == "sensor.z1":
            raise RuntimeError("defekte Datei")
        return 0

    monkeypatch.setattr(counter_auto, "process_entity", fake_process)
    monkeypatch.setattr(index, "list_counter_auto_entities", lambda: [{"entity_id": "sensor.z1"}, {"entity_id": "sensor.z2"}])

    assert counter_auto.run_if_due(tmp, index, TZ, lambda _e: nullcontext(), now=now) is True
    assert [e for e, _ in seen] == ["sensor.z1", "sensor.z2"]  # der zweite läuft trotz Fehler beim ersten
    assert index.get_setting(counter_auto._LAST_OK_KEY) is None  # nicht fehlerfrei: nächster Lauf liest weiter alles

    monkeypatch.setattr(counter_auto, "process_entity", lambda *args, **kwargs: 0)
    later = datetime(2024, 8, 15, 14, tzinfo=TZ)
    assert counter_auto.run_if_due(tmp, index, TZ, lambda _e: nullcontext(), now=later) is True
    assert index.get_setting(counter_auto._LAST_OK_KEY) == str(later.timestamp())


def test_counter_rule_reads_from_last_ok_not_from_since(env, monkeypatch) -> None:
    from app import counter_auto

    tmp, index = env
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    since = (now.timestamp() - 200 * 86400)
    entity = {"entity_id": "sensor.z1", "counter_auto_halted_at": None, "counter_auto_since": since}
    starts = []

    def fake_rows(_data_dir, _index, _entity_id, start, end, _tz, now=None, **_kw):
        starts.append(start)
        return iter(())

    monkeypatch.setattr(cleanup, "iter_raw_rows", fake_rows)
    monkeypatch.setattr(index, "list_counter_mark_ignored", lambda _e: set())
    counter_auto.process_entity(tmp, index, TZ, entity, now)
    counter_auto.process_entity(tmp, index, TZ, entity, now, last_ok=now.timestamp() - 3600)
    assert starts[0] == since - counter_auto._LOOKBACK_SECONDS
    assert starts[1] == now.timestamp() - 3600 - counter_auto._REPLAY_SECONDS - counter_auto._LOOKBACK_SECONDS


def test_marking_a_non_counter_leaves_the_counter_snapshot_alone(env) -> None:
    _tmp, index = env
    index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.zaehler", "sensor", "total_increasing", "kWh")
    index.set_counter_decrease_snapshot([])
    cleanup.soft_delete(index, "sensor.temp", [1.0])
    assert index.get_counter_decrease_snapshot()["checked_at"] != 0
    cleanup.soft_delete(index, "sensor.zaehler", [1.0])
    assert index.get_counter_decrease_snapshot()["checked_at"] == 0


def _counter_rows(values, start=1_700_000_000.0, step=60.0):
    return [(start + i * step, v) for i, v in enumerate(values)]


def test_counter_outlier_does_not_flag_the_good_value_after_a_bad_one() -> None:
    steady = [10000.0 + i for i in range(10)]
    rows = _counter_rows(steady + [0.0, 10010.0, 10011.0])
    flagged = cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ, mode="counter")
    assert flagged == {}  # 10010 ist ein Zuwachs von 1 zum Stand vor dem Fehlwert, kein Zuwachs von 10010


def test_counter_outlier_still_catches_a_digit_error() -> None:
    steady = [10000.0 + i for i in range(10)]
    rows = _counter_rows(steady + [101230.0])
    assert list(cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ, mode="counter")) == [rows[-1][0]]


def test_counter_outlier_accepts_a_proportional_step_after_a_pause() -> None:
    # Minütlich +1; dann 6 Stunden Pause (360 Minuten) und +360: derselbe Verbrauch, kein Ausreißer.
    rows = _counter_rows([float(i) for i in range(10)])
    last_ts, last_value = rows[-1]
    rows.append((last_ts + 360 * 60, last_value + 360))
    assert cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ, mode="counter") == {}
    # Dasselbe Zeitloch mit einem Zuwachs, der um Größenordnungen darüber liegt, fällt weiter auf.
    rows[-1] = (last_ts + 360 * 60, last_value + 360_000)
    assert list(cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ, mode="counter")) == [rows[-1][0]]


def test_counter_outlier_rebases_after_a_lasting_reset() -> None:
    # Reset auf niedrigen Stand, der bleibt: nach einiger Zeit ist er die neue Bezugsgröße, kein Dauer-Ausreißer.
    rows = _counter_rows([10000.0 + i for i in range(10)] + [float(i) for i in range(40)])
    assert cleanup.detect_outliers(rows, 10, decimals="auto", tz=TZ, mode="counter") == {}


def test_streaming_analysis_agrees_with_detect_for_a_midnight_glitch() -> None:
    midnight = datetime(2026, 10, 6, 0, 0, tzinfo=TZ).timestamp()
    rows = [(midnight - 300, 1000.0), (midnight + 60, 0.0), (midnight + 360, 1000.5)]
    result = cleanup.analyze_raw_rows_page(
        lambda: iter(rows), filter_="counter_decreases", page=1, page_size=50,
        gap_threshold_minutes=None, outlier_factor=None, tz=TZ, counter_decrease_enabled=True,
    )
    assert result["counts"]["counter_decreases"] == 1
    assert list(cleanup.detect_counter_decreases(rows, TZ)) == [midnight + 60]


@pytest.mark.parametrize("filter_", ["all", "outliers", "duplicates"])
def test_deep_pages_use_a_match_window_and_show_the_same_rows(filter_, monkeypatch) -> None:
    start = 1_700_000_000.0
    rows = []
    for i in range(237):
        value = 20.0 + (i % 5) * 0.1
        if i % 17 == 0:
            value = 500.0  # Ausreißer
        rows.append((start + i * 60.0, value))
    rows.insert(40, rows[40])  # ein Duplikat (gleicher Zeitstempel)
    rows.insert(120, rows[120])

    def analyze(page):
        return cleanup.analyze_raw_rows_page(
            lambda: iter(rows), filter_=filter_, page=page, page_size=10,
            gap_threshold_minutes=None, outlier_factor=10.0, tz=TZ,
        )

    reference = {page: analyze(page) for page in (1, 2, 5, 12, 99)}  # 99 wird auf die letzte Seite geklemmt
    monkeypatch.setattr(cleanup, "MAX_RETAINED_MATCHES", 15)
    for page, expected in reference.items():
        deep = analyze(page)
        assert [row["ts"] for row in deep["rows"]] == [row["ts"] for row in expected["rows"]]
        assert deep["pagination"] == expected["pagination"]
        assert deep["counts"] == expected["counts"]


def test_bulk_preview_names_removed_and_affected_rows(env) -> None:
    from contextlib import nullcontext

    from app import data_quality

    tmp, index = env
    entity_id = "sensor.dup"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    base = datetime.now(TZ).replace(day=1, hour=0, minute=1, second=0, microsecond=0).timestamp()
    for ts in (base, base + 60, base + 120):  # drei Zeitstempel, jeweils doppelt
        for _ in range(2):
            hotbuffer.append(tmp, entity_id, ts, 5.0, TZ)
            index.record_write(entity_id, ts)

    preview = data_quality.preview_duplicate_removal(tmp, index, TZ, [entity_id], lambda _e: nullcontext())
    row = preview["rows"][0]
    assert row["count"] == 3  # würden entfernt
    assert row["affected"] == 6  # so viele Zeilen zählt die Matrix


def test_day_month_label_follows_the_format_without_a_trailing_slash() -> None:
    from app import formats
    from app.formatting import format_day_month_timestamp

    ts = datetime(2026, 10, 7, 12, tzinfo=TZ).timestamp()
    for name, expected in (("de-DE", "07.10."), ("en-GB", "07/10"), ("en-US", "10/07")):
        token = formats.current_format.set(name)
        try:
            assert format_day_month_timestamp(ts, TZ) == expected
        finally:
            formats.current_format.reset(token)
    assert format_day_month_timestamp(None, TZ) == "—"


def test_outlier_factor_and_durations_follow_the_format() -> None:
    from app import formats

    token = formats.current_format.set("en-GB")
    try:
        assert cleanup._format_factor(2.5) == "2.5×"
        assert cleanup._format_factor(56941875.0) == "56,941,875×"
    finally:
        formats.current_format.reset(token)
    assert cleanup._format_factor(2.5) == "2,5×"
    assert cleanup._format_factor(56941875.0) == "56.941.875×"
    assert cleanup._format_duration(95) == "1 Min."


def test_last_batch_size_counts_only_the_newest_batch(env) -> None:
    _tmp, index = env
    index.get_or_create_entity("sensor.a", "sensor", "measurement", "W")
    assert index.last_deleted_batch_size("sensor.a") == 0
    cleanup.soft_delete(index, "sensor.a", [1.0, 2.0, 3.0], deleted_at=1000.0)
    cleanup.soft_delete(index, "sensor.a", [4.0, 5.0], deleted_at=2000.0)
    assert index.last_deleted_batch_size("sensor.a") == 2
    assert index.last_deleted_batch_size("sensor.unbekannt") == 0


def test_mark_seen_from_a_built_matrix_does_not_rebuild_it(env, monkeypatch) -> None:
    from app import data_quality

    _tmp, index = env
    index.get_or_create_entity("sensor.a", "sensor", "measurement", "W")
    index.get_or_create_entity("sensor.b", "sensor", "measurement", "W")
    index.set_cleanup_alltime_stats("sensor.a", {"all": 10, "duplicates": 2}, "off", cleanup.OUTLIER_RULE_VERSION)
    index.set_cleanup_alltime_stats("sensor.gelöscht", {"all": 10, "duplicates": 5}, "off", cleanup.OUTLIER_RULE_VERSION)

    def must_not_run(*_a, **_k):
        raise AssertionError("Matrix wurde ein zweites Mal gebaut")

    monkeypatch.setattr(data_quality, "build_matrix", must_not_run)
    data_quality.mark_seen(index, [{"entity_id": "sensor.a", "total": 2}])
    import json as _json

    assert _json.loads(index.get_setting(data_quality.SEEN_SETTING)) == {"sensor.a": 2}  # ohne die gelöschte Entität
