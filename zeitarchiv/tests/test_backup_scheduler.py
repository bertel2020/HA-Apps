"""Kalender- und Neustartverhalten des persistenten Backup-Planers."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backup_scheduler import next_scheduled_run, parse_schedule_time
from app.storage.index import Index


BERLIN = ZoneInfo("Europe/Berlin")


def test_daily_schedule_preserves_wall_clock_time() -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=BERLIN)
    assert next_scheduled_run(now, "daily", "03:30") == datetime(2026, 8, 25, 3, 30, tzinfo=BERLIN)


def test_weekly_schedule_uses_configured_weekday() -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=BERLIN)  # Montag
    assert next_scheduled_run(now, "weekly", "04:00", 6) == datetime(2026, 8, 30, 4, 0, tzinfo=BERLIN)


def test_dst_gap_moves_to_first_valid_minute() -> None:
    now = datetime(2026, 3, 28, 12, 0, tzinfo=BERLIN)
    assert next_scheduled_run(now, "daily", "02:30") == datetime(2026, 3, 29, 3, 0, tzinfo=BERLIN)


def test_time_parser_rejects_non_clock_values() -> None:
    try:
        parse_schedule_time("25:00")
    except ValueError:
        pass
    else:
        raise AssertionError("25:00 wurde fälschlich akzeptiert")


def test_backup_jobs_survive_and_running_job_is_recovered() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="zeitarchiv-scheduler-test-")) / "index.sqlite"
    first = Index(db_path)
    job_id = first.create_backup_job("scheduled", 123.0)
    first.update_backup_job(job_id, status="running", started_at=124.0)

    second = Index(db_path)
    assert second.recover_interrupted_backup_jobs(now=200.0) == 1
    job = second.list_backup_jobs(1)[0]
    assert job["status"] == "interrupted"
    assert job["finished_at"] == 200.0


def test_retention_jobs_persist_results_and_recover_interruption() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-job-test-")) / "index.sqlite"
    index = Index(db_path)
    completed_id = index.create_retention_job("manual")
    index.update_retention_job(
        completed_id,
        status="success",
        started_at=100.0,
        finished_at=101.0,
        rows_deleted=12,
        bytes_freed=2048,
        months_deleted=2,
        entities_affected=1,
    )
    running_id = index.create_retention_job("scheduled", 200.0)
    index.update_retention_job(running_id, status="running", started_at=201.0)

    reopened = Index(db_path)
    assert reopened.recover_interrupted_retention_jobs(now=300.0) == 1
    jobs = reopened.list_retention_jobs(2)
    assert jobs[0]["status"] == "interrupted"
    assert jobs[1]["status"] == "success"
    assert jobs[1]["rows_deleted"] == 12


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
