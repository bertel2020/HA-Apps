"""Letzter Import je Entität (storage/import_last.py): Nachführung aus import_reports.create(),
einmaliger Aufbau aus vorhandenen Berichten, Überleben gelöschter Berichte."""

from __future__ import annotations

from datetime import datetime, timezone

from _paths import APP  # noqa: F401  (legt den App-Pfad auf sys.path)

from app.storage import import_last, import_reports


def _report(data_dir, source_type, entity_ids, *, configuration=None, errors=None):
    results = [{"entity_id": e, "rows_imported": 3, "rows_merged": 1} for e in entity_ids]
    return import_reports.create(
        data_dir, source_type=source_type, started_at=datetime.now(timezone.utc),
        source={"filename": "x", "size_bytes": 0}, configuration=configuration or {},
        results=results, errors=errors or [],
    )


def test_create_records_entities_with_mode(tmp_path) -> None:
    _report(tmp_path, "ha", ["sensor.a"], configuration={"history_source": "stats"})
    _report(tmp_path, "csv", ["sensor.b"])
    last = import_last.load(tmp_path)
    assert last["sensor.a"]["mode"] == "stats" and last["sensor.a"]["rows"] == 4
    assert last["sensor.b"]["mode"] == "csv"
    assert "sensor.c" not in last


def test_newer_import_replaces_older_one(tmp_path) -> None:
    _report(tmp_path, "ha", ["sensor.a"], configuration={"history_source": "raw"})
    first = import_last.load(tmp_path)["sensor.a"]["ts"]
    _report(tmp_path, "symcon", ["sensor.a"])
    entry = import_last.load(tmp_path)["sensor.a"]
    assert entry["mode"] == "symcon" and entry["ts"] >= first


def test_survives_deleted_reports(tmp_path) -> None:
    report = _report(tmp_path, "ha", ["sensor.a"], configuration={"history_source": "full"})
    assert import_reports.delete(tmp_path, report["id"])
    assert import_last.load(tmp_path)["sensor.a"]["mode"] == "full"


def test_builds_from_existing_reports_when_file_is_missing(tmp_path) -> None:
    report = _report(tmp_path, "ha", ["sensor.a"], configuration={"history_source": "raw"})
    (tmp_path / import_last.FILENAME).unlink()
    assert import_last.load(tmp_path)["sensor.a"]["ts"] == report["finished_at"]
    assert (tmp_path / import_last.FILENAME).is_file()


def test_empty_when_nothing_was_imported(tmp_path) -> None:
    assert import_last.load(tmp_path) == {}
