"""Tests für app/notices.py — Meldungs-Erzeugung anhand des Index-Zustands."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.notices import build_notices
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def test_purge_available_notice_appears_when_rows_are_removable() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 42, "entities_affected": 3},
            storage_reconcile=None,
            stale_entity_count=0,
        )

        ids = [n["id"] for n in notices]
        assert "housekeeping.purge_available" in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_purge_available_notice_absent_when_nothing_removable() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
        )

        ids = [n["id"] for n in notices]
        assert "housekeeping.purge_available" not in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
