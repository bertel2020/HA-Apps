"""Code-Review Phase 6: Import, Restore und Export halten Teilfehler, fremde Kodierungen und Ausreißer aus."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import _paths  # noqa: F401
from app import export_data
from app.route_support import UPLOAD_OVERHEAD_BYTES, UploadSizeLimitMiddleware
from app.storage import backup, csv_import, import_reports, reconcile, symcon_import
from app.storage import restore_selective as rs
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
TS = 1_704_067_200.0


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ------------------------------------------------------------------ B14: Reports aller Quellen


@pytest.mark.parametrize("source_type", ["symcon", "csv", "ha", "vm"])
def test_a_report_of_every_source_can_be_listed_opened_and_deleted(tmp: Path, source_type: str) -> None:
    report = import_reports.create(
        tmp, source_type=source_type, started_at=datetime.now(TZ), source={}, configuration={}, results=[], errors=[],
    )
    assert [row["id"] for row in import_reports.list_all(tmp)] == [report["id"]]
    assert import_reports.load(tmp, report["id"]) is not None
    assert import_reports.download_path(tmp, report["id"]) is not None
    assert import_reports.delete(tmp, report["id"]) is True
    assert import_reports.list_all(tmp) == []


# ------------------------------------------------------------------ C5: Formel-Injection im Export


@pytest.mark.parametrize("text", ["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd"])
def test_text_cells_that_look_like_formulas_get_a_quote(text: str) -> None:
    fmt = export_data.CsvFormat()
    assert export_data._cell(text, fmt).lstrip('"').startswith("'")


def test_harmless_text_and_numbers_are_unchanged() -> None:
    fmt = export_data.CsvFormat()
    assert export_data._cell("sensor.wohnzimmer", fmt) == "sensor.wohnzimmer"
    assert export_data._cell("kWh", fmt) == "kWh"
    assert export_data._number("-12.5", fmt) == "-12.5"  # Zahlen laufen nicht über _cell


# ------------------------------------------------------------------ C9: „Alle Backups löschen“


def test_delete_all_backups_spares_the_ones_not_yet_transferred(tmp: Path) -> None:
    for name in ("zeitarchiv-backup-1.zip", "zeitarchiv-backup-2.zip", "zeitarchiv-backup-3.zip"):
        (tmp / name).write_bytes(b"x")
    assert backup.delete_all_backups(tmp, {"zeitarchiv-backup-2.zip"}) == 2
    assert [p.name for p in tmp.iterdir()] == ["zeitarchiv-backup-2.zip"]
    assert backup.delete_all_backups(tmp) == 1


# ------------------------------------------------------------------ B10: Kodierung der CSV


GERMAN = "Zeit;Größe;Wert\n15.01.2026 10:00;Äpfel;1,5\n15.01.2026 11:00;Übung;2,5\n"


@pytest.mark.parametrize(
    ("encoding", "expect_conversion"),
    [("cp1252", "cp1252"), ("utf-16", "utf-16"), ("utf-8-sig", None), ("utf-8", None)],
)
def test_ensure_utf8_converts_excel_encodings_and_leaves_utf8_alone(tmp: Path, encoding: str, expect_conversion) -> None:
    path = tmp / "daten.csv"
    path.write_bytes(GERMAN.encode(encoding))
    before = path.stat().st_mtime_ns
    assert csv_import.ensure_utf8(path) == expect_conversion
    assert path.read_text(encoding="utf-8-sig") == GERMAN
    if expect_conversion is None:
        assert path.stat().st_mtime_ns == before  # nicht neu geschrieben
    assert csv_import.sniff_delimiter(path) == ";"
    assert csv_import.preview(path, ";", True).columns == ["Zeit", "Größe", "Wert"]
    assert [p.name for p in tmp.iterdir()] == ["daten.csv"]  # keine Temp-Datei übrig


def test_a_windows_1252_file_could_not_be_read_before() -> None:
    with pytest.raises(UnicodeDecodeError):
        GERMAN.encode("cp1252").decode("utf-8")


def test_ensure_utf8_streams_large_files(tmp: Path) -> None:
    path = tmp / "gross.csv"
    line = "15.01.2026 10:00;Größe;1,5\n"
    path.write_bytes((line * 120_000).encode("cp1252"))  # > 3 MB, mehrere Blöcke
    assert csv_import.ensure_utf8(path) == "cp1252"
    assert path.read_text(encoding="utf-8").count("Größe") == 120_000


# ------------------------------------------------------------------ B11: Umstellung und Duplikate


def _write_csv(tmp: Path, lines: list[str]) -> Path:
    path = tmp / "tag.csv"
    path.write_text("Zeit;Wert\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def _parse(path: Path, fmt: str = "de_datetime"):
    return csv_import.parse_rows(path, ";", True, 0, 1, fmt, "", TZ)


def test_the_repeated_hour_of_the_clock_change_gets_distinct_timestamps(tmp: Path) -> None:
    """Am 27.10.2024 steht 02:00–02:59 zweimal in der Datei (Sommer- auf Winterzeit), ohne Zone."""
    path = _write_csv(tmp, [
        "27.10.2024 01:00;1", "27.10.2024 02:00;2", "27.10.2024 02:30;3",
        "27.10.2024 02:00;4", "27.10.2024 02:30;5", "27.10.2024 03:00;6",
    ])
    rows = _parse(path).rows
    assert [value for _ts, value in rows] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    stamps = [ts for ts, _value in rows]
    assert stamps == sorted(stamps) and len(set(stamps)) == 6
    assert stamps[3] - stamps[1] == 3600.0 and stamps[4] - stamps[2] == 3600.0


def test_a_sorted_file_without_a_clock_change_is_read_as_before(tmp: Path) -> None:
    path = _write_csv(tmp, ["15.06.2024 10:00;1", "15.06.2024 11:00;2", "15.06.2024 12:00;3"])
    stamps = [ts for ts, _ in _parse(path).rows]
    assert stamps == [datetime(2024, 6, 15, h, tzinfo=TZ).timestamp() for h in (10, 11, 12)]


def test_an_unsorted_file_is_not_shifted_by_the_fold_logic(tmp: Path) -> None:
    path = _write_csv(tmp, ["15.06.2024 12:00;3", "15.06.2024 11:30;2", "15.06.2024 10:00;1"])
    stamps = sorted(ts for ts, _ in _parse(path).rows)
    assert stamps == [datetime(2024, 6, 15, h, m, tzinfo=TZ).timestamp() for h, m in ((10, 0), (11, 30), (12, 0))]


def test_overlapping_exports_do_not_write_duplicate_timestamps(tmp: Path) -> None:
    index = Index(tmp / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.neu", "sensor", "measurement", "W")
        base = datetime(2023, 3, 10, tzinfo=TZ).timestamp()
        rows = [(base + 60 * i, float(i)) for i in range(10)] + [(base + 60 * i, float(i)) for i in range(5, 15)]
        plan = symcon_import.plan_import_rows(tmp, index, sorted(rows), "sensor.neu", TZ)
        assert plan.rows_to_import == 15
        result = symcon_import.import_rows(tmp, index, sorted(rows), "sensor.neu", TZ)
        assert result.rows_imported == 15 and result.duplicate_rows == 5
        written = pq.read_table(tmp / "archive" / "sensor.neu" / "2023-03.parquet").column("ts").to_pylist()
        assert len(written) == len(set(written)) == 15
    finally:
        index.close()


# ------------------------------------------------------------------ B9 / C10: selektiver Restore


def _entity(index: Index, data_dir: Path, entity_id: str, values: list[float]) -> Path:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W", entity_id)
    directory = data_dir / "archive" / entity_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "2024-01.parquet"
    pq.write_table(pa.table({"ts": [TS + 60 * i for i in range(len(values))], "value": values}), path)
    reconcile.audit_storage_metadata(data_dir, index, TZ, entity_ids=[entity_id], repair=True)
    return path


@pytest.fixture
def live(tmp: Path):
    index = Index(tmp / "index.sqlite")
    yield tmp, index, StorageCoordinator()
    index.close()


def test_a_failure_after_the_database_step_puts_files_and_database_back(live, monkeypatch) -> None:
    data_dir, index, coordinator = live
    path = _entity(index, data_dir, "sensor.a", [1.0, 2.0, 3.0])
    zip_path = data_dir / "backups" / "b.zip"
    backup.create_backup(data_dir, zip_path, consistent_sqlite=True)
    pq.write_table(pa.table({"ts": [TS], "value": [9.0]}), path)
    index.mark_deleted("sensor.a", [TS])
    reconcile.audit_storage_metadata(data_dir, index, TZ, entity_ids=["sensor.a"], repair=True)
    local_bytes = path.read_bytes()
    assert index.get_entity("sensor.a")["deleted_count"] == 1

    original_audit = rs.reconcile.audit_storage_metadata
    calls = {"n": 0}

    def failing_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Platte voll")  # die Datenbank ist zu diesem Zeitpunkt schon umgestellt
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(rs.reconcile, "audit_storage_metadata", failing_once)
    with rs.BackupSource(zip_path) as source, pytest.raises(OSError):
        rs.restore_entities(data_dir, index, coordinator, source, ["sensor.a"], TZ, source_label="b.zip")

    assert path.read_bytes() == local_bytes
    entity = index.get_entity("sensor.a")
    assert entity["deleted_count"] == 1 and entity["row_count"] == 1
    assert index.get_deleted_counts("sensor.a", TS - 1, TS + 1) == {TS: 1}
    assert not list(data_dir.glob(".zeitarchiv-selective-*")) and rs.list_rollbacks(data_dir) == []


def test_symcon_names_stay_untouched_when_the_settings_restore_fails(live, monkeypatch) -> None:
    data_dir, index, _ = live
    names = data_dir / "symcon_names.json"
    names.write_text(json.dumps({"1": {"name": "alt", "parent": None}}), encoding="utf-8")
    zip_path = data_dir / "backups" / "b.zip"
    backup.create_backup(data_dir, zip_path, consistent_sqlite=True)
    names.write_text(json.dumps({"1": {"name": "neu", "parent": None}}), encoding="utf-8")
    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        plan = rs.make_plan(overview, {"x:symcon"}, {})
        real_dumps = json.dumps

        def failing_dumps(obj, *args, **kwargs):
            if isinstance(obj, dict) and obj.get("kind") == "settings":
                raise OSError("Platte voll")  # das Schreiben der Rollback-Info scheitert
            return real_dumps(obj, *args, **kwargs)

        monkeypatch.setattr(rs.json, "dumps", failing_dumps)
        with pytest.raises(OSError):
            rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")

    assert json.loads(names.read_text())["1"]["name"] == "neu"  # nicht schon durch das Backup ersetzt
    assert rs.list_rollbacks(data_dir) == []


# ------------------------------------------------------------------ C7: Upload-Limit vor dem Einlesen


def _call(app, method: str, path: str, headers: dict[str, str]) -> tuple[int, bytes, list[str]]:
    sent: list[dict] = []
    scope = {
        "type": "http", "method": method, "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    reached: list[str] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        reached.append("app")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    asyncio.run(UploadSizeLimitMiddleware(inner, {"/import/csv/upload": 100})(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body, reached


def test_an_oversized_upload_is_refused_before_the_app_reads_it() -> None:
    status, body, reached = _call(None, "POST", "/import/csv/upload", {"content-length": str(100 + UPLOAD_OVERHEAD_BYTES + 1)})
    assert status == 413 and reached == []
    assert "detail" in json.loads(body)


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("POST", "/import/csv/upload", {"content-length": str(100 + UPLOAD_OVERHEAD_BYTES)}),  # genau am Limit
        ("POST", "/import/csv/upload", {}),  # ohne Content-Length (chunked)
        ("POST", "/import/csv/upload", {"content-length": "abc"}),
        ("POST", "/andere/route", {"content-length": "999999999"}),
        ("GET", "/import/csv/upload", {"content-length": "999999999"}),
    ],
)
def test_everything_else_passes_through(method: str, path: str, headers: dict) -> None:
    status, _body, reached = _call(None, method, path, headers)
    assert status == 200 and reached == ["app"]


def test_the_real_app_refuses_an_oversized_csv_upload(client) -> None:
    from app.limits import MAX_CSV_UPLOAD_BYTES

    response = client.post(
        "/import/csv/upload", content=b"x",
        headers={"content-length": str(MAX_CSV_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES + 1), "content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 413
    assert "detail" in response.json()


# ------------------------------------------------------------------ B10/B13 an der echten App


def _upload(client, data: bytes, name: str = "daten.csv"):
    return client.post("/import/csv/upload", files={"file": (name, io.BytesIO(data), "text/csv")})


@pytest.fixture
def csv_cleanup(client):
    yield
    client.post("/import/csv/delete")


def test_a_windows_1252_upload_works_and_the_import_page_stays_usable(client, csv_cleanup) -> None:
    response = _upload(client, GERMAN.encode("cp1252"))
    assert response.status_code == 200
    assert "Größe" in response.text
    assert client.get("/import").status_code == 200


def test_an_unreadable_stored_file_does_not_take_the_import_page_down(client, csv_cleanup) -> None:
    """Ein Feld über dem csv-Limit (131072 Zeichen) lässt den Leser mit csv.Error scheitern."""
    response = _upload(client, b"Zeit;Wert\n" + b"x" * 200_000 + b"\n")
    assert response.status_code == 200
    assert "upload-error" in response.text and "csv-dropzone" in response.text  # Hinweis, Datei entfernt
    page = client.get("/import")
    assert page.status_code == 200 and "csv-dropzone" in page.text


def test_upload_and_delete_are_refused_while_a_csv_job_runs(client, csv_cleanup) -> None:
    from app.main import _import_service as service

    assert _upload(client, GERMAN.encode("utf-8")).status_code == 200
    with service._csv_progress.lock:
        service._csv_progress.running = True
    try:
        assert _upload(client, GERMAN.encode("utf-8"), "andere.csv").status_code == 409
        assert client.post("/import/csv/delete").status_code == 409
    finally:
        with service._csv_progress.lock:
            service._csv_progress.running = False
    assert client.post("/import/csv/delete").status_code == 200
