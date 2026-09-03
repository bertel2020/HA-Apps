"""Tests für app/notices.py — Meldungs-Erzeugung anhand des Index-Zustands."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.notices import RECONCILE_STALLED_SECONDS, SCHEDULER_STALLED_SECONDS, build_notices
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
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time(),
            reconcile_in_progress=False,
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
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time(),
            reconcile_in_progress=False,
        )

        ids = [n["id"] for n in notices]
        assert "housekeeping.purge_available" not in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_stalled_notice_appears_when_tick_is_old() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time() - SCHEDULER_STALLED_SECONDS - 1,
            reconcile_last_tick=time.time(),
            reconcile_in_progress=False,
        )

        ids = [n["id"] for n in notices]
        assert "system.scheduler_stalled" in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scheduler_stalled_notice_absent_when_tick_is_recent() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time(),
            reconcile_in_progress=False,
        )

        ids = [n["id"] for n in notices]
        assert "system.scheduler_stalled" not in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconcile_stalled_notice_appears_when_tick_is_old_and_in_progress() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time() - RECONCILE_STALLED_SECONDS - 1,
            reconcile_in_progress=True,
        )

        ids = [n["id"] for n in notices]
        assert "system.storage_reconcile_stalled" in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconcile_stalled_notice_absent_when_tick_is_recent() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time(),
            reconcile_in_progress=True,
        )

        ids = [n["id"] for n in notices]
        assert "system.storage_reconcile_stalled" not in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reconcile_stalled_notice_absent_when_not_in_progress_despite_old_tick() -> None:
    """Deckt den Grund für reconcile_in_progress ab: nach normalem Abschluss
    (oder im synchronen Restore-/Crash-Modus, siehe main.py's
    _reconcile_in_progress()) bleibt der letzte Tick stehen — ohne dieses
    Flag würde die Meldung Stunden/Tage später fälschlich weiter feuern."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time() - RECONCILE_STALLED_SECONDS - 1,
            reconcile_in_progress=False,
        )

        ids = [n["id"] for n in notices]
        assert "system.storage_reconcile_stalled" not in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_index_lock_contention_notice_reflects_recent_busy_events() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)

        notices_before = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time(),
            reconcile_in_progress=False,
        )
        assert "system.index_lock_contention" not in [n["id"] for n in notices_before]

        # IndexBusy simulieren, ohne echte Nebenläufigkeit/Timeouts im Test
        # zu brauchen — direkt am internen _TimeoutLock ausgelöst.
        index._lock._busy_events.append(time.time())

        notices_after = build_notices(
            index, db_path, TZ,
            purge_totals={"removable_rows": 0, "entities_affected": 0},
            storage_reconcile=None,
            stale_entity_count=0,
            scheduler_last_tick=time.time(),
            reconcile_last_tick=time.time(),
            reconcile_in_progress=False,
        )
        ids_after = [n["id"] for n in notices_after]
        assert "system.index_lock_contention" in ids_after
        assert "1×" in next(n for n in notices_after if n["id"] == "system.index_lock_contention")["detail"]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


