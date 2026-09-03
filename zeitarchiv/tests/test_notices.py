"""Tests für app/notices.py — Meldungs-Erzeugung anhand des Index-Zustands."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.notices import (
    RECONCILE_STALLED_SECONDS,
    SCHEDULER_STALLED_SECONDS,
    build_notices,
    gap_threshold_conflicts,
)
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


def _build_notices_for_entities(index, db_path) -> list[dict]:
    return build_notices(
        index, db_path, TZ,
        purge_totals={"removable_rows": 0, "entities_affected": 0},
        storage_reconcile=None,
        stale_entity_count=0,
        scheduler_last_tick=time.time(),
        reconcile_last_tick=time.time(),
        reconcile_in_progress=False,
    )


def test_gap_threshold_conflict_notice_fires_for_resolution_alone() -> None:
    """Vor der Generalisierung (nur main.py:3.1) erkannte dieser Guard nur
    den Wertänderungsfilter — resolution="1h" mit gap_threshold="15" (kein
    Filter) meldete bislang gar keine Lücken-Konflikt-Meldung, obwohl die
    Kombination strukturell garantiert jeden Zyklus fälschlich als Lücke
    zeigt."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)
        index.get_or_create_entity("sensor.a", "sensor", "measurement", "°C")
        index.set_config("sensor.a", resolution="1h", gap_threshold="15", value_filter="off")

        ids = [n["id"] for n in _build_notices_for_entities(index, db_path)]
        assert "entities.gap_threshold_conflict" in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gap_threshold_conflict_notice_still_fires_for_value_filter() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)
        index.get_or_create_entity("sensor.a", "sensor", "measurement", "°C")
        index.set_config("sensor.a", resolution="raw", gap_threshold="30", value_filter="decimals")

        ids = [n["id"] for n in _build_notices_for_entities(index, db_path)]
        assert "entities.gap_threshold_conflict" in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gap_threshold_conflict_notice_absent_for_consistent_config() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)
        index.get_or_create_entity("sensor.a", "sensor", "measurement", "°C")
        index.set_config("sensor.a", resolution="1h", gap_threshold="60", value_filter="off")

        ids = [n["id"] for n in _build_notices_for_entities(index, db_path)]
        assert "entities.gap_threshold_conflict" not in ids
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gap_threshold_conflicts_returns_row_details_for_housekeeping() -> None:
    """gap_threshold_conflicts() ist die gemeinsame Grundlage für die Zähler-
    Meldung oben UND die volle Liste in Housekeeping → Konfiguration
    (main.py) — hier wird die Zeilenstruktur direkt geprüft, inkl. des
    vorgeschlagenen Zielwerts."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-notices-test-"))
    try:
        db_path = tmp / "index.sqlite"
        index = Index(db_path)
        index.get_or_create_entity("sensor.a", "sensor", "measurement", "°C")
        index.set_config("sensor.a", resolution="1h", gap_threshold="15", value_filter="off", custom_name="Küche")
        index.get_or_create_entity("sensor.b", "sensor", "measurement", "°C")
        index.set_config("sensor.b", resolution="1h", gap_threshold="60", value_filter="off")

        rows = gap_threshold_conflicts(index)
        assert len(rows) == 1
        row = rows[0]
        assert row["entity_id"] == "sensor.a"
        assert row["friendly_name"] == "Küche"
        assert row["resolution_label"] == "1 Std."
        assert row["gap_threshold_label"] == "15 Minuten"
        assert row["suggested_gap_label"] == "1 Stunde"
        assert row["value_filter_active"] is False
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


