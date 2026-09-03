"""Tests für app/storage/index.py — Typ-Update, Sortierung/Filterung der Entitäten-Tabelle."""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.storage.index import (
        Index,
        IndexBusy,
        _TimeoutLock,
        filter_deleted_occurrences,
        should_accept_write,
    )

    _PYARROW_AVAILABLE = True  # Index selbst braucht kein pyarrow, aber der Rest der Suite schon
except ImportError:
    _PYARROW_AVAILABLE = False


def test_timeout_lock_acquires_and_releases_normally() -> None:
    lock = _TimeoutLock(timeout=1.0)
    with lock:
        pass
    with lock:
        pass


def test_timeout_lock_heals_a_self_deadlock_via_exception_unwind() -> None:
    """Simuliert den tatsächlich gefundenen Bug: derselbe Thread versucht,
    ein von ihm selbst bereits gehaltenes Lock erneut zu erwerben (z. B. ein
    on_type_change-Callback, der intern eine weitere Index-Methode aufruft).
    Der innere Versuch scheitert nach dem Timeout mit IndexBusy — die
    Exception verlässt den äußeren with-Block, dessen __exit__ gibt das
    Lock frei. Ein Aufruf danach muss wieder normal funktionieren, sonst
    wäre die App dauerhaft blockiert statt sich zu erholen."""
    lock = _TimeoutLock(timeout=0.2)
    with lock:
        try:
            with lock:
                pass
            raise AssertionError("innerer Erwerb hätte scheitern müssen")
        except IndexBusy:
            pass
    # Nach Verlassen des äußeren with-Blocks ist das Lock wieder frei.
    with lock:
        pass


def test_timeout_lock_records_busy_events_for_notices() -> None:
    lock = _TimeoutLock(timeout=0.1)
    assert lock.recent_busy_events() == 0
    with lock:
        try:
            with lock:
                pass
        except IndexBusy:
            pass
    assert lock.recent_busy_events() == 1


def test_get_or_create_backfills_unit_and_friendly_name_on_existing_entity() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", None)
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C", "Wohnzimmer Temperatur")

        row = index.get_entity("sensor.temp")
        assert row["unit"] == "°C"
        assert row["friendly_name"] == "Wohnzimmer Temperatur"
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_database_table_stats_list_index_contents() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-stats-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity(
            "sensor.temp", "sensor", "measurement", "°C", "Temperatur"
        )
        rows = {row["table"]: row for row in index.get_database_table_stats()}

        assert rows["entities"]["rows"] == 1
        assert rows["settings"]["rows"] >= 0
        assert rows["dashboard_pins"]["rows"] >= 0
        assert "sqlite_sequence" not in rows
        assert rows["entities"]["bytes"] is None or rows["entities"]["bytes"] > 0
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_database_table_stats_assigns_index_pages_to_owner_table() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-size-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        # Eine TEMP-Tabelle gleichen Namens macht den Test unabhängig davon,
        # ob das Python-SQLite der Testmaschine dbstat einkompiliert hat.
        index._conn.execute("CREATE TEMP TABLE dbstat (name TEXT, pgsize INTEGER)")
        index._conn.executemany(
            "INSERT INTO dbstat (name, pgsize) VALUES (?, ?)",
            [
                ("settings", 4096),
                ("settings", 4096),
                ("sqlite_autoindex_settings_1", 4096),
            ],
        )

        rows = {row["table"]: row for row in index.get_database_table_stats()}
        settings = rows["settings"]
        assert settings["data_bytes"] == 8192
        assert settings["index_bytes"] == 4096
        assert settings["bytes"] == 12288
        assert settings["index_count"] == 1
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_database_maintenance_stats_and_vacuum_reclaim_free_pages() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-vacuum-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)
        with index._conn:
            index._conn.execute(
                "CREATE TABLE vacuum_payload (id INTEGER PRIMARY KEY, payload BLOB)"
            )
            index._conn.executemany(
                "INSERT INTO vacuum_payload (payload) VALUES (?)",
                [(b"x" * 8192,) for _ in range(256)],
            )
            index._conn.execute("DELETE FROM vacuum_payload")

        before = index.get_database_maintenance_stats()
        assert before["reclaimable_bytes"] > 0
        result = index.vacuum_database()
        after = index.get_database_maintenance_stats()

        assert result["quick_check"] == "ok"
        assert after["reclaimable_bytes"] == 0
        assert after["database_bytes"] < before["database_bytes"]
        assert db_path.stat().st_size == after["database_bytes"]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_type_change_requires_successful_rollup_migration() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.energy"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")

        try:
            index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
            raise AssertionError("Typwechsel ohne Rollup-Migration wurde akzeptiert")
        except ValueError as exc:
            assert "Rollup-Migration erforderlich" in str(exc)
        assert index.get_entity(entity_id)["aggregation_type"] == "standard"

        calls = []
        result = index.get_or_create_entity(
            entity_id,
            "sensor",
            "total_increasing",
            "kWh",
            on_type_change=lambda old, new, hourly_rollup: calls.append((old, new, hourly_rollup)),
        )
        assert result == "counter"
        assert calls == [("standard", "counter", False)]
        entity = index.get_entity(entity_id)
        assert entity["aggregation_type"] == "counter"
        assert entity["state_class"] == "total_increasing"
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_search_matches_entity_id_or_friendly_name() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.pv_ertrag", "sensor", "total_increasing", "kWh", "PV Ertrag")
        index.get_or_create_entity("sensor.wohnzimmer_temp", "sensor", "measurement", "°C", "Wohnzimmer")

        by_id = index.list_entities(search="pv_ertrag")
        assert [r["entity_id"] for r in by_id] == ["sensor.pv_ertrag"]

        by_name = index.list_entities(search="Wohnzimmer")
        assert [r["entity_id"] for r in by_name] == ["sensor.wohnzimmer_temp"]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_type_filter() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.pv_ertrag", "sensor", "total_increasing", "kWh")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")
        index.get_or_create_entity("binary_sensor.tuer", "binary_sensor", None, None)

        counters = index.list_entities(type_filter="counter")
        assert [r["entity_id"] for r in counters] == ["sensor.pv_ertrag"]

        all_rows = index.list_entities(type_filter="all")
        assert len(all_rows) == 3

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_type_filter_accepts_multiple_types() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.pv_ertrag", "sensor", "total_increasing", "kWh")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")
        index.get_or_create_entity("binary_sensor.tuer", "binary_sensor", None, None)

        both = index.list_entities(type_filter=["counter", "switch"])
        assert sorted(r["entity_id"] for r in both) == ["binary_sensor.tuer", "sensor.pv_ertrag"]

        # "all" in der Liste wirkt wie ein No-Op-Wert, kein Override — zusammen mit
        # einem konkreten Typ verhält es sich wie nur dieser eine Typ.
        with_all = index.list_entities(type_filter=["all", "counter"])
        assert [r["entity_id"] for r in with_all] == ["sensor.pv_ertrag"]

        empty_list = index.list_entities(type_filter=[])
        assert len(empty_list) == 3

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_unit_filter() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.pv_ertrag", "sensor", "total_increasing", "kWh")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")
        index.get_or_create_entity("binary_sensor.tuer", "binary_sensor", None, None)

        kwh = index.list_entities(unit_filter="kWh")
        assert [r["entity_id"] for r in kwh] == ["sensor.pv_ertrag"]

        # Sentinel "__none__" filtert auf Entitäten ohne Einheit (unit IS NULL) —
        # eine leere Einheit lässt sich sonst nicht per exaktem Vergleich treffen.
        without_unit = index.list_entities(unit_filter="__none__")
        assert [r["entity_id"] for r in without_unit] == ["binary_sensor.tuer"]

        all_rows = index.list_entities(unit_filter="all")
        assert len(all_rows) == 3

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_distinct_units_returns_sorted_unique_units_including_none() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "total_increasing", "kWh")
        index.get_or_create_entity("sensor.b", "sensor", "total_increasing", "kWh")
        index.get_or_create_entity("sensor.c", "sensor", "measurement", "°C")
        index.get_or_create_entity("binary_sensor.d", "binary_sensor", None, None)

        units = index.list_distinct_units()
        assert units == [None, "kWh", "°C"]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_sort_by_row_count_descending() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "measurement", None)
        index.get_or_create_entity("sensor.b", "sensor", "measurement", None)
        for _ in range(5):
            index.record_write("sensor.a", 1.0)
        for _ in range(2):
            index.record_write("sensor.b", 1.0)

        rows = index.list_entities(sort="rows", direction="desc")
        assert [r["entity_id"] for r in rows] == ["sensor.a", "sensor.b"]

        rows_asc = index.list_entities(sort="rows", direction="asc")
        assert [r["entity_id"] for r in rows_asc] == ["sensor.b", "sensor.a"]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_default_sort_uses_friendly_name_over_entity_id() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        # entity_id-Reihenfolge wäre a, b — Anzeigename-Reihenfolge ist umgekehrt.
        index.get_or_create_entity("sensor.a_raw", "sensor", "measurement", None, "Zeta Sensor")
        index.get_or_create_entity("sensor.b_raw", "sensor", "measurement", None, "Alpha Sensor")
        index.get_or_create_entity("sensor.c_raw", "sensor", "measurement", None)  # kein Anzeigename

        rows = index.list_entities(sort="entity_id")
        assert [r["entity_id"] for r in rows] == ["sensor.b_raw", "sensor.c_raw", "sensor.a_raw"]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_unknown_sort_key_falls_back_to_entity_id() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.b", "sensor", "measurement", None)
        index.get_or_create_entity("sensor.a", "sensor", "measurement", None)

        rows = index.list_entities(sort="'; DROP TABLE entities; --")
        assert [r["entity_id"] for r in rows] == ["sensor.a", "sensor.b"]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_deleted_count_uses_join_not_correlated_subquery() -> None:
    """ZP-003 (PERFORMANCE.md): deleted_count kommt jetzt aus einem einmalig
    aggregierten LEFT JOIN statt einer pro Zeile ausgewerteten korrelierten
    Subquery — Query-Plan darf keinen "CORRELATED SCALAR SUBQUERY" mehr
    enthalten, das Ergebnis muss aber unverändert bleiben."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "measurement", None)
        index.get_or_create_entity("sensor.b", "sensor", "measurement", None)
        for ts in (1.0, 2.0, 3.0):
            index.record_write("sensor.a", ts)
        index.mark_deleted("sensor.a", [1.0, 2.0])

        rows = {r["entity_id"]: r["deleted_count"] for r in index.list_entities()}
        assert rows == {"sensor.a": 2, "sensor.b": 0}

        with index._lock, index._conn:
            plan = index._conn.execute(
                "EXPLAIN QUERY PLAN SELECT entities.*, COALESCE(dc.deleted_count, 0) "
                "AS deleted_count FROM entities LEFT JOIN ("
                "SELECT entity_id, COUNT(*) AS deleted_count FROM deleted_points "
                "GROUP BY entity_id) dc ON dc.entity_id = entities.entity_id "
                "ORDER BY entities.entity_id"
            ).fetchall()
        details = " | ".join(row["detail"] for row in plan)
        assert "CORRELATED" not in details.upper()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_entities_limit_offset_matches_python_side_pagination() -> None:
    """ZP-004 (PERFORMANCE.md): list_entities(limit=, offset=) muss über alle
    Seiten hinweg exakt dieselbe Reihenfolge liefern wie das frühere Muster
    "alles laden, dann in Python aufschneiden" — plus count_entities() als
    korrekte Gesamtzahl für dieselben Filter."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        for i in range(23):
            index.get_or_create_entity(f"sensor.s{i:02d}", "sensor", "measurement", None)

        full = index.list_entities(sort="entity_id")
        assert index.count_entities() == len(full) == 23

        page_size = 7
        collected: list[str] = []
        for page in range((len(full) + page_size - 1) // page_size):
            page_rows = index.list_entities(
                sort="entity_id", limit=page_size, offset=page * page_size
            )
            collected.extend(r["entity_id"] for r in page_rows)
        assert collected == [r["entity_id"] for r in full]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_duplicate_snapshot_cache_round_trip_and_staleness() -> None:
    """ZP-002 (PERFORMANCE.md): Cache-Grundlage für die im Wartungsplaner
    berechnete Duplikat-Zählung — vor dem ersten Schreiben "stale", danach
    innerhalb des Intervalls frisch, mit unverändertem Rundtrip der Zeilen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        assert index.get_duplicate_snapshot() is None
        assert index.is_duplicate_snapshot_stale() is True

        rows = [{"entity_id": "sensor.a", "friendly_name": "A", "count": 3}]
        index.set_duplicate_snapshot(rows)

        assert index.is_duplicate_snapshot_stale(min_interval_seconds=3600) is False
        assert index.is_duplicate_snapshot_stale(min_interval_seconds=0) is True
        snapshot = index.get_duplicate_snapshot()
        assert snapshot["rows"] == rows
        assert isinstance(snapshot["checked_at"], float)

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_set_config_updates_only_provided_fields() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")

        index.set_config("sensor.temp", resolution="5min")
        row = index.get_entity("sensor.temp")
        assert row["resolution"] == "5min"
        assert row["retention"] == "unlimited"  # unverändert

        index.set_config("sensor.temp", retention="90d")
        row = index.get_entity("sensor.temp")
        assert row["resolution"] == "5min"  # unverändert vom vorherigen Aufruf
        assert row["retention"] == "90d"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_set_config_decimals_defaults_to_auto_and_is_settable() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.temp", "sensor", "measurement", "°C")

        row = index.get_entity("sensor.temp")
        assert row["decimals"] == "auto"

        index.set_config("sensor.temp", decimals="2")
        row = index.get_entity("sensor.temp")
        assert row["decimals"] == "2"
        assert row["resolution"] == "raw"  # unverändert

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_should_accept_write_raw_always_accepts() -> None:
    assert should_accept_write("raw", last_ts=1000.0, new_ts=1000.5) is True
    assert should_accept_write("raw", last_ts=None, new_ts=1000.0) is True


def test_should_accept_write_throttles_by_configured_interval() -> None:
    # 5min-Auflösung: 100 Sekunden seit dem letzten akzeptierten Wert reichen nicht.
    assert should_accept_write("5min", last_ts=1000.0, new_ts=1100.0) is False
    # Erst ab 300 Sekunden (5 Minuten) wird der nächste Wert angenommen.
    assert should_accept_write("5min", last_ts=1000.0, new_ts=1300.0) is True
    # Kein bisheriger Wert (erster Schreibvorgang der Entität) wird immer angenommen.
    assert should_accept_write("5min", last_ts=None, new_ts=1000.0) is True


def test_should_accept_write_supports_every_configured_interval() -> None:
    intervals = {
        "30s": 30,
        "1min": 60,
        "5min": 300,
        "15min": 900,
        "1h": 3600,
    }
    for resolution, seconds in intervals.items():
        assert should_accept_write(resolution, last_ts=1000.0, new_ts=1000.0 + seconds - 0.001) is False
        assert should_accept_write(resolution, last_ts=1000.0, new_ts=1000.0 + seconds) is True


def test_should_accept_write_unknown_resolution_fails_open() -> None:
    assert should_accept_write("future-resolution", last_ts=1000.0, new_ts=1001.0) is True


def test_ingest_claim_reports_whether_it_was_freshly_created() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-claim-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        first = index.claim_ingest_event("event-1", "sensor.temp", 1000.0)
        repeated = index.claim_ingest_event("event-1", "sensor.temp", 1000.0)
        assert first["is_new"] is True
        assert repeated["is_new"] is False
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filter_deleted_occurrences_removes_only_marked_count_not_all() -> None:
    rows = [(1.0, 10.0), (1.0, 10.0), (1.0, 10.0), (2.0, 20.0)]
    # Nur 2 der 3 Vorkommen bei ts=1.0 sind als gelöscht markiert -> eines bleibt übrig.
    kept = filter_deleted_occurrences(rows, {1.0: 2})
    assert kept == [(1.0, 10.0), (2.0, 20.0)]

    # 0 (oder gar kein Eintrag) heißt "nichts gelöscht".
    assert filter_deleted_occurrences(rows, {}) == rows

    # Mehr markiert als tatsächlich vorhanden -> alle Vorkommen verschwinden, kein Fehler.
    assert filter_deleted_occurrences(rows, {1.0: 99}) == [(2.0, 20.0)]


def test_deleted_points_migrates_old_schema_without_losing_data() -> None:
    """Ältere index.sqlite-Dateien hatten PRIMARY KEY (entity_id, ts) auf
    deleted_points — die Migration muss auf die neue, ID-basierte Tabelle
    umstellen, ohne schon vorhandene gelöschte Zeitstempel zu verlieren."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        import sqlite3

        db_path = tmp / "index.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE deleted_points (
                entity_id TEXT NOT NULL, ts REAL NOT NULL, deleted_at REAL NOT NULL,
                PRIMARY KEY (entity_id, ts)
            )"""
        )
        conn.execute(
            "INSERT INTO deleted_points VALUES ('sensor.temp', 123.0, 1000.0)"
        )
        conn.commit()
        conn.close()

        index = Index(db_path)  # löst die Migration aus
        counts = index.get_deleted_counts("sensor.temp", 0.0, 999999.0)
        assert counts == {123.0: 1}

        # Tabelle erlaubt jetzt mehrere Vorkommen desselben Zeitstempels.
        index.mark_deleted("sensor.temp", [456.0, 456.0])
        counts_after = index.get_deleted_counts("sensor.temp", 0.0, 999999.0)
        assert counts_after[456.0] == 2

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_deleted_points_queries_use_covering_indexes_after_migration() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        indexes = {
            row["name"]
            for row in index._conn.execute("PRAGMA index_list(deleted_points)").fetchall()
        }
        assert "idx_deleted_points_entity_ts" in indexes
        assert "idx_deleted_points_entity_deleted_at" in indexes

        plans = [
            index._conn.execute(
                "EXPLAIN QUERY PLAN SELECT ts FROM deleted_points "
                "WHERE entity_id = ? AND ts >= ? AND ts < ?",
                ("sensor.test", 0.0, 1.0),
            ).fetchall(),
            index._conn.execute(
                "EXPLAIN QUERY PLAN SELECT MAX(deleted_at) FROM deleted_points "
                "WHERE entity_id = ?",
                ("sensor.test",),
            ).fetchall(),
        ]
        assert all(any("USING" in row["detail"] and "INDEX" in row["detail"] for row in plan) for plan in plans)
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_stats_snapshot_if_stale_respects_min_interval() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "measurement", None)
        index.record_write("sensor.a", 1.0)

        assert index.record_stats_snapshot_if_stale(min_interval_seconds=3600) is True
        snapshots = index.get_stats_snapshots(0.0)
        assert len(snapshots) == 1
        assert snapshots[0]["entity_count"] == 1
        assert snapshots[0]["total_rows"] == 1

        # Sofortiger zweiter Aufruf innerhalb des Intervalls -> kein neuer Schnappschuss.
        assert index.record_stats_snapshot_if_stale(min_interval_seconds=3600) is False
        assert len(index.get_stats_snapshots(0.0)) == 1

        # min_interval_seconds=0 erzwingt einen neuen Schnappschuss.
        assert index.record_stats_snapshot_if_stale(min_interval_seconds=0) is True
        assert len(index.get_stats_snapshots(0.0)) == 2

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_retention_job_totals_only_include_successful_runs_in_window() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-retention-jobs-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        first = index.create_retention_job("manual")
        index.update_retention_job(
            first, status="success", finished_at=100.0,
            rows_deleted=10, bytes_freed=1000, months_deleted=2, entities_affected=1,
        )
        second = index.create_retention_job("scheduled")
        index.update_retention_job(
            second, status="success", finished_at=200.0,
            rows_deleted=20, bytes_freed=2000, months_deleted=3, entities_affected=2,
        )
        failed = index.create_retention_job("scheduled")
        index.update_retention_job(failed, status="failed", finished_at=300.0)

        totals = index.get_retention_job_totals(150.0)
        assert totals == {
            "job_count": 1,
            "rows_deleted": 20,
            "bytes_freed": 2000,
            "months_deleted": 3,
            "entities_affected": 2,
        }
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_stats_by_type_and_resolution_group_correctly() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "total_increasing", None)  # counter
        index.get_or_create_entity("sensor.b", "sensor", "measurement", None)  # standard
        index.get_or_create_entity("sensor.c", "sensor", "measurement", None)  # standard
        index.record_write("sensor.a", 1.0)
        index.record_write("sensor.b", 1.0)
        index.set_config("sensor.a", resolution="5min")
        index.set_config("sensor.b", resolution="5min")
        # sensor.c bleibt bei "raw" (Default)

        by_type = {row["aggregation_type"]: row for row in index.get_stats_by_type()}
        assert by_type["counter"]["entity_count"] == 1
        assert by_type["standard"]["entity_count"] == 2
        assert by_type["standard"]["total_rows"] == 1  # nur sensor.b hat einen Schreibvorgang

        by_resolution = {row["resolution"]: row for row in index.get_stats_by_resolution()}
        assert by_resolution["5min"]["entity_count"] == 2
        assert by_resolution["raw"]["entity_count"] == 1

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_stats_by_retention_groups_correctly() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "total_increasing", None)
        index.get_or_create_entity("sensor.b", "sensor", "measurement", None)
        index.get_or_create_entity("sensor.c", "sensor", "measurement", None)
        index.record_write("sensor.a", 1.0)
        index.set_config("sensor.a", retention="90d")
        index.set_config("sensor.b", retention="90d")
        # sensor.c bleibt bei "unlimited" (Default)

        by_retention = {row["retention"]: row for row in index.get_stats_by_retention()}
        assert by_retention["90d"]["entity_count"] == 2
        assert by_retention["90d"]["total_rows"] == 1  # nur sensor.a hat einen Schreibvorgang
        assert by_retention["unlimited"]["entity_count"] == 1

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_deleted_points_by_entity_only_lists_affected_entities() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "measurement", None, friendly_name="A")
        index.get_or_create_entity("sensor.b", "sensor", "measurement", None, friendly_name="B")
        index.get_or_create_entity("sensor.c", "sensor", "measurement", None, friendly_name="C")
        index.mark_deleted("sensor.a", [1.0, 2.0, 3.0])
        index.mark_deleted("sensor.b", [1.0])
        # sensor.c bleibt ohne markierte Vorkommen — darf nicht in der Liste auftauchen.

        breakdown = index.get_deleted_points_by_entity()

        assert [row["entity_id"] for row in breakdown] == ["sensor.a", "sensor.b"]  # nach n absteigend sortiert
        by_id = {row["entity_id"]: row for row in breakdown}
        assert by_id["sensor.a"]["n"] == 3
        assert by_id["sensor.a"]["friendly_name"] == "A"
        assert by_id["sensor.b"]["n"] == 1

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_deleted_points_searches_and_paginates_individual_markers() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity(
            "sensor.alpha", "sensor", "measurement", "°C", friendly_name="Wohnzimmer"
        )
        index.get_or_create_entity(
            "sensor.beta", "sensor", "measurement", "W", friendly_name="Leistung"
        )
        index.mark_deleted("sensor.alpha", [float(i) for i in range(12)])
        index.mark_deleted("sensor.beta", [99.0])

        first = index.list_deleted_points(page=1, page_size=10)
        second = index.list_deleted_points(page=2, page_size=10)
        assert first["pagination"] == {
            "page": 1, "page_size": 10, "total": 13, "total_pages": 2,
            "start": 1, "end": 10,
        }
        assert second["pagination"]["start"] == 11
        assert second["pagination"]["end"] == 13
        assert len(first["rows"]) == 10
        assert len(second["rows"]) == 3

        by_name = index.list_deleted_points(search="wohnzimmer", page_size=50)
        assert by_name["pagination"]["total"] == 12
        assert {row["entity_id"] for row in by_name["rows"]} == {"sensor.alpha"}
        assert all("deleted_at" in row and "ts" in row for row in by_name["rows"])

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_setting_returns_default_when_unset_and_reflects_updates() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        assert index.get_setting("default_resolution") is None
        assert index.get_setting("default_resolution", "raw") == "raw"

        index.set_setting("default_resolution", "5min")
        assert index.get_setting("default_resolution") == "5min"

        # Erneutes Setzen überschreibt (ON CONFLICT), statt einen zweiten Eintrag anzulegen.
        index.set_setting("default_resolution", "1min")
        assert index.get_setting("default_resolution") == "1min"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_or_create_entity_uses_configured_default_resolution_and_retention() -> None:
    """Ein globaler Standardwert aus dem Einstellungen-Bereich (Konzept Abschnitt
    03) muss für NEU erkannte Entitäten gelten, ohne die Modulkonstante fest
    zu verdrahten — und ohne bereits archivierte Entitäten rückwirkend zu ändern."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.before", "sensor", "measurement", None)
        assert index.get_entity("sensor.before")["resolution"] == "raw"

        index.set_setting("default_resolution", "5min")
        index.set_setting("default_retention", "90d")
        index.get_or_create_entity("sensor.after", "sensor", "measurement", None)

        after = index.get_entity("sensor.after")
        assert after["resolution"] == "5min"
        assert after["retention"] == "90d"
        # Vorher angelegte Entität bleibt unverändert — der Standardwert wirkt
        # nur beim Neuanlegen, nie rückwirkend.
        assert index.get_entity("sensor.before")["resolution"] == "raw"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_saved_charts_create_list_get_update_delete() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")

        chart_id = index.create_saved_chart(
            "Wohnzimmer vs. Bad", ["sensor.a", "sensor.b"], "week", continuous=False
        )
        assert isinstance(chart_id, int)

        listed = index.list_saved_charts()
        assert len(listed) == 1
        assert listed[0]["name"] == "Wohnzimmer vs. Bad"
        assert listed[0]["entity_ids"] == ["sensor.a", "sensor.b"]
        assert listed[0]["range_key"] == "week"
        assert listed[0]["continuous"] is False

        fetched = index.get_saved_chart(chart_id)
        assert fetched["id"] == chart_id
        assert fetched["entity_ids"] == ["sensor.a", "sensor.b"]

        index.update_saved_chart(chart_id, "Neuer Name", ["sensor.c"], "month", continuous=True)
        updated = index.get_saved_chart(chart_id)
        assert updated["name"] == "Neuer Name"
        assert updated["entity_ids"] == ["sensor.c"]
        assert updated["range_key"] == "month"
        assert updated["continuous"] is True

        index.delete_saved_chart(chart_id)
        assert index.get_saved_chart(chart_id) is None
        assert index.list_saved_charts() == []

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_saved_chart_returns_none_for_unknown_id() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        assert index.get_saved_chart(999) is None
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_saved_charts_orders_newest_first() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        first_id = index.create_saved_chart("Erstes", ["sensor.a"], "day", continuous=False)
        # Erzwingt einen unterschiedlichen created_at-Wert, ohne auf die reale
        # Systemuhr zu warten — sqlite3 rundet time.time() sonst innerhalb
        # desselben Tests oft auf denselben Float.
        with index._lock, index._conn:
            index._conn.execute(
                "UPDATE saved_charts SET created_at = created_at - 10 WHERE id = ?", (first_id,)
            )
        second_id = index.create_saved_chart("Zweites", ["sensor.b"], "day", continuous=False)

        listed = index.list_saved_charts()
        assert [c["id"] for c in listed] == [second_id, first_id]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dashboard_tile_size_is_persisted_and_validated() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        chart_id = index.create_saved_chart("Dashboard", ["sensor.a"], "day", continuous=False)
        assert index.pin_item_to_dashboard(1, "chart", chart_id) is True
        assert index.set_dashboard_pin_size(1, "chart", chart_id, 2, 3) is True

        pin = index.list_dashboard_pins(1)[0]
        assert pin["grid_cols"] == 2
        assert pin["grid_rows"] == 3

        for cols, rows in [(0, 1), (1, 0), (4, 1), (1, 4)]:
            try:
                index.set_dashboard_pin_size(1, "chart", chart_id, cols, rows)
            except ValueError:
                pass
            else:
                raise AssertionError("Ungültige Dashboard-Größe wurde akzeptiert")
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dashboard_tile_limit_is_eighteen() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        assert index.DASHBOARD_TILE_LIMIT == 18
        chart_ids = [
            index.create_saved_chart(f"Chart {number}", ["sensor.a"], "day", continuous=False)
            for number in range(19)
        ]
        assert all(index.pin_item_to_dashboard(1, "chart", chart_id) for chart_id in chart_ids[:18])
        assert index.pin_item_to_dashboard(1, "chart", chart_ids[18]) is False
        assert len(index.list_dashboard_pins(1)) == 18
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dashboard_size_columns_are_migrated_with_one_by_one_default() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        db_path = tmp / "index.sqlite"
        connection = sqlite3.connect(db_path)
        connection.execute(
            "CREATE TABLE dashboard_pins ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, item_type TEXT NOT NULL, "
            "item_id INTEGER NOT NULL, position INTEGER NOT NULL, UNIQUE(item_type, item_id))"
        )
        connection.execute(
            "INSERT INTO dashboard_pins (item_type, item_id, position) VALUES ('chart', 7, 1)"
        )
        connection.commit()
        connection.close()

        index = Index(db_path)
        pin = index.list_dashboard_pins(1)[0]
        assert pin["grid_cols"] == 1
        assert pin["grid_rows"] == 1
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()


def test_list_entities_can_skip_the_deleted_points_join() -> None:
    """include_deleted_count=False darf deleted_points gar nicht erst
    aggregieren (bei 1,5 Mio. Löschmarkierungen ~75 ms je Aufruf) — nur
    Aufrufer, die deleted_count wirklich lesen, zahlen dafür. Der Sort
    "rows" hängt selbst an dc.deleted_count und erzwingt den Join weiterhin."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-index-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        index.get_or_create_entity("sensor.a", "sensor", "measurement", "°C")
        lean = index.list_entities(include_deleted_count=False)
        assert len(lean) == 1 and "deleted_count" not in lean[0].keys()
        full = index.list_entities()
        assert full[0]["deleted_count"] == 0
        by_rows = index.list_entities(sort="rows", include_deleted_count=False)
        assert by_rows[0]["deleted_count"] == 0
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
