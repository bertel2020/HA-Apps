"""Code-Review Phase 7: Live-Auflösung, Typwechsel, Vormonat, Tabellengrenzen, Event-Loop."""

from __future__ import annotations

import ast
import io
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from _paths import APP
from app.background import BackgroundDependencies, BackgroundService
from app.storage import hotbuffer, ingestion, resolution
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index
from app.storage.ingestion import IngestEvent, IngestionService

TZ = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


def _resolved(ts: float, value: float = 1.0, low: float = 0.5, high: float = 1.5):
    return (ts, value, None, low, high)


def _raw(ts: float, value: float = 1.0, event_id: str | None = None):
    return (ts, value, event_id, None, None)


# ------------------------------------------------------------------ B5: nur das Dateiende lesen


def _file(tmp_path: Path, records) -> Path:
    path = tmp_path / "hot" / "sensor.a-2026-11.csv"
    hotbuffer.write_records(path, records)
    return path


def test_the_tail_reader_returns_only_what_follows_the_last_resolved_row(tmp_path: Path) -> None:
    path = _file(tmp_path, [_raw(1.0), _resolved(30.0), _resolved(60.0), _raw(65.0, 2.0, "a"), _raw(75.0, 3.0, "b")])
    offset, tail = hotbuffer.read_tail_after(path, resolution._is_resolved)
    assert [record[0] for record in tail] == [65.0, 75.0]
    assert path.read_bytes()[offset:].decode().count("\n") == 2
    assert path.read_bytes()[:offset].decode().splitlines()[-1].startswith("60.0")


def test_without_a_resolved_row_the_tail_is_the_whole_file(tmp_path: Path) -> None:
    path = _file(tmp_path, [_raw(1.0), _raw(2.0)])
    offset, tail = hotbuffer.read_tail_after(path, resolution._is_resolved)
    assert offset == 0 and [record[0] for record in tail] == [1.0, 2.0]


@pytest.mark.parametrize("content", [None, ""])
def test_missing_and_empty_files_have_no_tail(tmp_path: Path, content) -> None:
    path = tmp_path / "x.csv"
    if content is not None:
        path.write_text(content)
    assert hotbuffer.read_tail_after(path, resolution._is_resolved) == (0, [])


def test_the_tail_reader_works_across_many_blocks(tmp_path: Path) -> None:
    """Hinter dem letzten aufgelösten Satz stehen mehr Zeilen, als in einen Block (64 KiB) passen."""
    records = [_resolved(float(i)) for i in range(2000)] + [_raw(5000.0 + i, float(i), f"e{i}") for i in range(9000)]
    path = _file(tmp_path, records)
    assert path.stat().st_size > 3 * 64 * 1024
    offset, tail = hotbuffer.read_tail_after(path, resolution._is_resolved)
    assert len(tail) == 9000 and tail[0][0] == 5000.0 and tail[-1][0] == 13999.0
    assert len(path.read_bytes()[:offset].splitlines()) == 2000


def test_corrupt_lines_and_a_missing_final_newline_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("1.0,1.0,,0.5,1.5\n\x00\x00kaputt\n2.0,2.0,a\n3.0,3.0,b", encoding="utf-8")
    offset, tail = hotbuffer.read_tail_after(path, resolution._is_resolved)
    assert [record[0] for record in tail] == [2.0, 3.0]


def test_pending_bucket_end_and_collapse_do_not_parse_the_whole_file(tmp_path: Path, monkeypatch) -> None:
    path = _file(tmp_path, [_resolved(30.0 * i) for i in range(1, 500)] + [_raw(15000.0 + 3, 1.0, "a"), _raw(15000.0 + 13, 3.0, "b")])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("die ganze Datei wurde gelesen")

    monkeypatch.setattr(hotbuffer, "iter_records", forbidden)
    monkeypatch.setattr(resolution, "iter_records", forbidden)
    assert resolution.pending_bucket_end(path, 30) == 15030.0
    assert resolution.collapse_pending_window(path, 30) == 1


def test_collapse_keeps_the_resolved_part_byte_for_byte_and_replaces_the_open_window(tmp_path: Path) -> None:
    head = [_resolved(30.0, 1.0, 0.5, 1.5), (60.0, 2.0, "evt", 1.0, 3.0)]
    path = _file(tmp_path, head + [_raw(65.0, 2.0, "a"), _raw(75.0, 4.0, "b")])
    before = path.read_bytes().split(b"\n")[:2]
    assert resolution.collapse_pending_window(path, 30) == 1
    after = path.read_bytes().split(b"\n")
    assert after[:2] == before
    assert hotbuffer.read_full_rows(path)[-1] == (90.0, 3.0, None, 2.0, 4.0)
    assert resolution.collapse_pending_window(path, 30) == 0  # zweiter Lauf: nichts mehr offen


def test_an_unterminated_last_resolved_line_does_not_swallow_the_collapsed_row(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("30.0,1.0,,0.5,1.5", encoding="utf-8")  # kein Zeilenende
    path.write_text(path.read_text() + "\n65.0,2.0,a\n75.0,4.0,b", encoding="utf-8")
    resolution.collapse_pending_window(path, 30)
    assert [record[0] for record in hotbuffer.read_full_rows(path)] == [30.0, 90.0]


# ------------------------------------------------------------------ B6: verspätete Werte


def test_the_open_window_is_the_latest_one_not_the_one_of_the_last_appended_row(tmp_path: Path) -> None:
    path = _file(tmp_path, [_resolved(1800.0), _raw(3900.0), _raw(4200.0), _raw(1000.0, 7.0, "spaet")])
    assert resolution.pending_bucket_end(path, 1800) == 5400.0


def test_a_late_value_in_a_window_without_a_row_is_collapsed_at_its_bucket_end(tmp_path: Path) -> None:
    path = _file(tmp_path, [_resolved(3600.0), _raw(7300.0, 10.0, "a"), _raw(7400.0, 20.0, "b"), _raw(1000.0, 7.0, "spaet")])
    resolution.collapse_pending_window(path, 1800)
    rows = hotbuffer.read_full_rows(path)
    assert [record[0] for record in rows] == [3600.0, 1800.0, 9000.0]
    assert rows[1] == (1800.0, 7.0, None, 7.0, 7.0)
    assert rows[2] == (9000.0, 15.0, None, 10.0, 20.0)


def test_a_late_value_in_a_window_that_already_has_a_row_stays_a_separate_point(tmp_path: Path) -> None:
    """Zwei Datensätze mit gleichem Zeitstempel (Bucket-Ende) soll es nicht geben."""
    path = _file(tmp_path, [_resolved(1800.0), _resolved(3600.0), _raw(5300.0, 10.0, "a"), _raw(1000.0, 7.0, "spaet")])
    resolution.collapse_pending_window(path, 1800)
    rows = hotbuffer.read_full_rows(path)
    assert [record[0] for record in rows] == [1800.0, 3600.0, 1000.0, 5400.0]
    assert rows[2] == (1000.0, 7.0, "spaet", None, None)
    stamps = [record[0] for record in rows]
    assert len(stamps) == len(set(stamps))


def test_a_value_added_by_hand_does_not_shift_the_mean_when_the_window_closes(tmp_path: Path) -> None:
    """Der Weg, auf dem eine Zeile aus einem früheren Fenster am Dateiende landet: ein von Hand nachgetragener
    Wert (verspätete Ereignisse filtert die Aufnahme schon vorher aus)."""
    from app.storage import cleanup

    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, UTC)
        index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
        index.set_config("sensor.t", resolution="15min")
        day = datetime(2026, 11, 3, tzinfo=UTC).timestamp()
        now = datetime(2026, 11, 3, 12, tzinfo=UTC)

        def event(event_id: str, offset: float, value: float) -> IngestEvent:
            return IngestEvent(event_id=event_id, entity_id="sensor.t", domain="sensor", ts=day + offset,
                               value=value, state_class="measurement", unit="°C")

        for event_id, offset, value in (("a", 3900, 10.0), ("b", 4200, 20.0)):
            assert service.ingest(event(event_id, offset, value)) == "written"
        cleanup.add_raw_value(tmp_path, index, "sensor.t", day + 1000, 7.0, UTC, now=now)
        assert service.ingest(event("c", 5000, 30.0)) == "written"  # schließt das Fenster bis 4500

        rows = hotbuffer.read_full_rows(hotbuffer.hot_path(tmp_path, "sensor.t", day, UTC))
        by_ts = {record[0] - day: record for record in rows}
        assert by_ts[4500][1:] == (15.0, None, 10.0, 20.0)  # Mittel des Fensters, unverändert durch den Nachtrag
        assert by_ts[1800][1:] == (7.0, None, 7.0, 7.0)       # der nachgetragene Wert hat sein eigenes Fenster
        assert by_ts[5000][2] == "c"                          # das neue Fenster ist noch offen
    finally:
        index.close()


# ------------------------------------------------------------------ B7: Typwechsel außerhalb des Index-Locks


def _event(
    entity_id: str, state_class: str, ts: float = 1_767_225_600.0, event_id: str = "e1", value: float = 1.0
) -> IngestEvent:
    return IngestEvent(event_id=event_id, entity_id=entity_id, domain="sensor", ts=ts, value=value,
                       state_class=state_class, unit="kWh")


def test_the_rollup_rebuild_after_a_type_change_does_not_hold_the_index_lock(tmp_path: Path, monkeypatch) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, UTC)
        assert service.ingest(_event("sensor.x", "measurement", event_id="a")) == "written"
        seen: list[tuple] = []

        def lock_is_free(entity_id: str) -> bool:
            """Vom Index-Lock wissen nur andere Threads: Ein zweiter Thread kommt nur durch, wenn er frei ist
            (gehalten, wartete er bis zum Lock-Timeout von 8 s)."""
            outcome: list[bool] = []
            thread = threading.Thread(target=lambda: (index.set_setting("lock_probe", "1"), outcome.append(True)))
            thread.start()
            thread.join(1.0)
            return bool(outcome) and not thread.is_alive()

        def rebuild(data_dir, entity_id, aggregation_type, tz, hourly_rollup):
            seen.append((entity_id, aggregation_type, lock_is_free(entity_id)))

        monkeypatch.setattr(ingestion, "_rebuild_after_type_change", rebuild)
        assert service.ingest(_event("sensor.x", "total_increasing", ts=1_767_225_700.0, event_id="b", value=5.0)) == "written"
        assert seen == [("sensor.x", "counter", True)]
        assert index.get_entity("sensor.x")["aggregation_type"] == "counter"
    finally:
        index.close()


def test_a_failing_rebuild_leaves_the_stored_type_unchanged(tmp_path: Path, monkeypatch) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, UTC)
        service.ingest(_event("sensor.x", "measurement", event_id="a"))

        def broken(*_args, **_kwargs):
            raise OSError("Platte voll")

        monkeypatch.setattr(ingestion, "_rebuild_after_type_change", broken)
        with pytest.raises(OSError):
            service.ingest(_event("sensor.x", "total_increasing", ts=1_767_225_700.0, event_id="b", value=5.0))
        assert index.get_entity("sensor.x")["aggregation_type"] == "standard"
    finally:
        index.close()


def test_an_unchanged_type_triggers_no_rebuild(tmp_path: Path, monkeypatch) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, UTC)
        service.ingest(_event("sensor.x", "measurement", event_id="a"))
        monkeypatch.setattr(ingestion, "_rebuild_after_type_change", lambda *a, **k: pytest.fail("Neuaufbau ohne Typwechsel"))
        service.ingest(_event("sensor.x", "measurement", ts=1_767_225_700.0, event_id="b", value=5.0))
    finally:
        index.close()


# ------------------------------------------------------------------ B8: Vormonat ohne neues Event


@pytest.fixture
def dienst(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    service = BackgroundService(BackgroundDependencies(
        data_dir=tmp_path, tz=TZ, index=index, coordinator=StorageCoordinator(),
        base_dir=tmp_path, demo_mode_active=False,
        backups_dir=tmp_path / "backups", symcon_import_dir=tmp_path / "symcon",
        csv_import_dir=tmp_path / "csv", backup_default_time="03:00", backup_default_weekday=6,
        retention_default_time="04:00", retention_default_weekday=6,
        count_stale_entities=lambda: 0,
    ))
    yield service, index, tmp_path
    index.close()


def test_the_previous_month_of_a_silent_entity_is_archived_by_the_maintenance(dienst) -> None:
    service, index, data_dir = dienst
    index.get_or_create_entity("sensor.tuer", "sensor", "measurement", "")
    october = datetime(2026, 10, 20, 12, tzinfo=TZ).timestamp()
    hotbuffer.append(data_dir, "sensor.tuer", october, 1.0, TZ, event_id="o1")
    index.record_write("sensor.tuer", october)
    now = datetime(2026, 11, 3, 12, tzinfo=TZ)

    service._rotate_stale_months_if_due(now)

    assert (data_dir / "archive" / "sensor.tuer" / "2026-10.parquet").is_file()
    assert not hotbuffer.hot_path(data_dir, "sensor.tuer", october, TZ).exists()
    assert pq.read_table(data_dir / "archive" / "sensor.tuer" / "2026-10.parquet").num_rows == 1


def test_the_rotation_step_runs_at_most_once_an_hour_and_skips_current_months(dienst) -> None:
    service, index, data_dir = dienst
    index.get_or_create_entity("sensor.a", "sensor", "measurement", "")
    now = datetime(2026, 11, 3, 12, tzinfo=TZ)
    hotbuffer.append(data_dir, "sensor.a", now.timestamp(), 1.0, TZ, event_id="n1")
    service._rotate_stale_months_if_due(now)
    assert hotbuffer.hot_path(data_dir, "sensor.a", now.timestamp(), TZ).exists()  # laufender Monat bleibt

    october = datetime(2026, 10, 20, tzinfo=TZ).timestamp()
    hotbuffer.append(data_dir, "sensor.a", october, 1.0, TZ, event_id="o1")
    index.record_write("sensor.a", october)
    service._rotate_stale_months_if_due(now.replace(minute=30))  # nur 30 Minuten später: gedrosselt
    assert hotbuffer.hot_path(data_dir, "sensor.a", october, TZ).exists()
    service._rotate_stale_months_if_due(now.replace(hour=14))
    assert not hotbuffer.hot_path(data_dir, "sensor.a", october, TZ).exists()


# ------------------------------------------------------------------ B15: Tabellengrenzen


def _table_body(columns: int = 1, entities: int = 1) -> dict:
    return {
        "name": "T",
        "columns": [{"label": f"S{i}", "range_key": "day", "offset": 0, "year_over_year": False} for i in range(columns)],
        "rows": [
            {"label": f"Z{i}", "row_type": "entity", "entity_ids": [f"sensor.e{i}"], "formula": "", "bold": False}
            for i in range(entities)
        ],
    }


def test_a_table_over_the_query_limits_is_refused_when_saving(client) -> None:
    from app.limits import MAX_MULTI_QUERY_ENTITIES, MAX_TABLE_COLUMNS

    too_many_entities = client.post("/tables", json=_table_body(entities=MAX_MULTI_QUERY_ENTITIES + 1))
    assert too_many_entities.status_code == 400 and str(MAX_MULTI_QUERY_ENTITIES) in too_many_entities.json()["detail"]
    too_many_columns = client.post("/tables", json=_table_body(columns=MAX_TABLE_COLUMNS + 1))
    assert too_many_columns.status_code == 400 and str(MAX_TABLE_COLUMNS) in too_many_columns.json()["detail"]


def test_one_entity_in_many_rows_counts_once_and_a_table_at_the_limit_is_accepted(client) -> None:
    from app.limits import MAX_MULTI_QUERY_ENTITIES, MAX_TABLE_COLUMNS

    body = _table_body(columns=MAX_TABLE_COLUMNS, entities=MAX_MULTI_QUERY_ENTITIES)
    body["rows"] += [dict(body["rows"][0], label="Wiederholung")] * 5
    response = client.post("/tables", json=body)
    assert response.status_code == 200, response.text
    client.post(f"/tables/{response.json()['id']}/delete")


def test_the_card_table_answers_400_instead_of_500_for_an_oversized_table(client) -> None:
    source = (APP / "api_routes.py").read_text(encoding="utf-8")
    assert "def card_table_query" in source and "except ValidationError" in source


# ------------------------------------------------------------------ B17: Event-Loop


BLOCKING_ROUTES = (
    "mute_notice_route", "settings_darstellung", "settings_logging", "backup_schedule_save",
    "dashboard_entity_change", "backup_import",
)


def _async_function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse((APP / "main.py").read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


def _calls_outside_nested_functions(node: ast.AsyncFunctionDef) -> set[str]:
    nested = {id(inner) for child in ast.walk(node) if isinstance(child, ast.FunctionDef) for inner in ast.walk(child)}
    return {
        ast.unparse(call.func)
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and id(call) not in nested
    }


@pytest.mark.parametrize("name", BLOCKING_ROUTES)
def test_blocking_work_of_async_routes_runs_in_the_thread_pool(name: str) -> None:
    """Index-Zugriffe (Lock bis 8 s), Kontextaufbau (rglob über den Datenbestand) und das Aufbauen aller Meldungen
    gehören nicht in den Event-Loop: Dort hielten sie /api/write und /api/health an."""
    calls = _calls_outside_nested_functions(_async_function(name))
    blocking = {c for c in calls if c.startswith(("index.", "_background.", "_backup_context", "notices_mod.", "_collect_all_notices"))}
    assert blocking == set(), f"{name}: {sorted(blocking)}"
    assert "run_in_threadpool" in calls


def test_the_converted_routes_still_work(client) -> None:
    assert client.post("/settings/darstellung", data={"font_scale": "100"}).status_code in (200, 400)
    response = client.post("/settings/logging", data={"log_level": "info", "access_log_mode": "off"})
    assert response.status_code in (200, 400)
    assert client.post("/settings/darstellung", data={"font_scale": "ungültig"}).status_code == 400
    assert client.post("/settings/logging", data={"log_level": "geheim"}).status_code == 400
    assert client.post("/notices/gibt-es-nicht/mute", data={"duration": "1h"}).status_code == 404
    page = client.post("/backup/schedule", data={"backup_schedule": "off"})
    assert page.status_code == 200
    bad = client.post("/backup/import", files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")})
    assert bad.status_code == 200 and "ZIP" in bad.text
