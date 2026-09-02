"""Persistenz, Statusermittlung und Pfadsicherheit der Import-Reports."""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import import_reports


TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def _result(**overrides) -> dict:
    result = {
        "entity_id": "sensor.test",
        "variable_id": "123",
        "imported_months": ["2024-01"],
        "merged_months": [],
        "skipped_months": [],
        "rows_imported": 10,
        "rows_merged": 0,
        "skipped_rows": 0,
        "source_rows": 10,
        "duplicate_rows": 0,
    }
    result.update(overrides)
    return result


def test_report_is_written_atomically_and_can_be_listed_and_loaded() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-report-test-"))
    try:
        report = import_reports.create(
            tmp,
            source_type="symcon",
            started_at=datetime.now(timezone.utc),
            source={"filename": "db.zip", "size_bytes": 42},
            configuration={"mappings": []},
            results=[_result()],
            errors=[],
        )
        assert report["status"] == "success"
        assert report["summary"]["rows_imported"] == 10
        assert import_reports.load(tmp, report["id"]) == report
        assert [item["id"] for item in import_reports.list_all(tmp)] == [report["id"]]
        assert not list(tmp.rglob("*.part"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_report_status_distinguishes_partial_failed_and_no_changes() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-report-test-"))
    try:
        common = {"data_dir": tmp, "started_at": datetime.now(timezone.utc), "source": {}, "configuration": {}}
        partial = import_reports.create(source_type="csv", results=[_result(skipped_rows=2)], errors=[], **common)
        failed = import_reports.create(source_type="csv", results=[], errors=["kaputt"], **common)
        unchanged = import_reports.create(source_type="symcon", results=[_result(rows_imported=0, imported_months=[])], errors=[], **common)
        assert partial["status"] == "partial"
        assert failed["status"] == "failed"
        assert unchanged["status"] == "no_changes"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_report_counts_archive_updates_and_recovered_rows_as_changes() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-report-test-"))
    try:
        report = import_reports.create(
            tmp,
            source_type="ha",
            started_at=datetime.now(timezone.utc),
            source={"filename": "Home Assistant", "size_bytes": 0},
            configuration={"history_source": "full"},
            results=[_result(
                rows_imported=0,
                imported_months=[],
                rows_updated=4,
                rows_recovered=2,
            )],
            errors=[],
        )
        assert report["format_version"] == 2
        assert report["status"] == "success"
        assert report["summary"]["rows_updated"] == 4
        assert report["summary"]["rows_recovered"] == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_invalid_report_ids_cannot_escape_report_directory() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-report-test-"))
    try:
        assert import_reports.load(tmp, "../../index") is None
        assert import_reports.download_path(tmp, "not-a-report") is None
        assert import_reports.delete(tmp, "../../index") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ha_report_detail_reflects_current_full_import_fields() -> None:
    source = (TEMPLATES / "report_detail.html").read_text(encoding="utf-8")

    assert "'full': 'Vollimport'" in source
    assert "Statistikzeitraum" in source
    assert "Archivlücken füllen" in source
    assert "result.full_summary.stats_label" in source
    assert "result.full_summary.raw_label" in source
    assert "result.rows_updated_label" in source
    assert "result.rows_recovered_label" in source
