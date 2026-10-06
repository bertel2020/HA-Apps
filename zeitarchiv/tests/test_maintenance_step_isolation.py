"""Wartungsplaner: ein scheiternder Schritt darf die übrigen nicht blockieren.

Anlass (Meldung zu 1.0.0): Der Duplikat-Scan scheiterte an einer dichten
Entität (ResultLimitExceeded). Er stand in einem gemeinsamen try mit allen
übrigen Schritten, also fielen bei jedem 30-s-Takt alle Schritte danach aus —
auch Backup-Zeitplan, Aufbewahrung, Kompaktierung und automatische Löschung.
"""

from __future__ import annotations

import ast
import logging
from zoneinfo import ZoneInfo

import pytest

from _paths import APP

try:
    from app.background import BackgroundDependencies, BackgroundService
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index

    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _DEPS_AVAILABLE, reason="pyarrow nicht installiert")

TZ = ZoneInfo("Europe/Berlin")
BACKGROUND_SOURCE = (APP / "background.py").read_text(encoding="utf-8")


@pytest.fixture()
def dienst(tmp_path):
    index = Index(tmp_path / "index.sqlite")
    service = BackgroundService(BackgroundDependencies(
        data_dir=tmp_path, tz=TZ, index=index, coordinator=StorageCoordinator(),
        base_dir=tmp_path, demo_mode_active=False,
        backups_dir=tmp_path / "backups", symcon_import_dir=tmp_path / "symcon",
        csv_import_dir=tmp_path / "csv", backup_default_time="03:00", backup_default_weekday=6,
        retention_default_time="04:00", retention_default_weekday=6,
        count_stale_entities=lambda: 0,
    ))
    try:
        yield service
    finally:
        index.close()


def test_step_context_swallows_the_error_and_logs_it(dienst, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        with dienst._maintenance_step("beispiel"):
            raise RuntimeError("kaputt")
        erreicht = True
    assert erreicht
    assert any("event=maintenance_step_failed step=beispiel" in r.getMessage() for r in caplog.records)


def test_a_failing_duplicate_scan_does_not_block_backup_retention_compaction_or_purge(
    dienst, monkeypatch, caplog
) -> None:
    import app.background as background

    aufgerufen: list[str] = []

    def kaputt() -> None:
        raise RuntimeError("Ergebnis überschreitet 500000 Rohwerte")

    def merke(name):
        return lambda *args, **kwargs: aufgerufen.append(name)

    monkeypatch.setattr(dienst, "_refresh_duplicate_snapshot_if_stale", kaputt)
    for name in (
        "_run_backup_schedule_if_due", "_run_retention_enforcement_if_due",
        "_run_automatic_compaction_if_due", "_run_automatic_purge_if_due",
        "_flush_stale_resolution_windows", "_refresh_one_outlier_rate",
    ):
        monkeypatch.setattr(dienst, name, merke(name))
    # Netz-/Dateisystem-Schritte, die hier nichts zu tun haben sollen.
    monkeypatch.setattr(background.version_check, "refresh_if_stale", lambda *a, **k: None)
    monkeypatch.setattr(
        background.ha_integration, "refresh_integration_version_check_if_stale", lambda *a, **k: None
    )
    monkeypatch.setattr(background.supervisor_stats, "maybe_record_memory_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(background, "process_pending_hourly_backfill", lambda *a, **k: None)
    monkeypatch.setattr(background, "refresh_heatmap_weekday_cache_if_stale", lambda *a, **k: None)
    monkeypatch.setattr(background.notices_mod, "refresh_import_leftovers_if_stale", lambda *a, **k: None)

    # Genau ein Durchlauf: das Warten beendet die Schleife.
    stop = dienst._maintenance_scheduler_stop
    monkeypatch.setattr(stop, "wait", lambda _seconds: stop.set())

    with caplog.at_level(logging.ERROR):
        dienst._maintenance_scheduler_loop()

    assert aufgerufen == [
        "_refresh_one_outlier_rate", "_run_backup_schedule_if_due", "_run_retention_enforcement_if_due",
        "_flush_stale_resolution_windows", "_run_automatic_compaction_if_due", "_run_automatic_purge_if_due",
    ]
    assert any("step=duplicate_snapshot" in r.getMessage() for r in caplog.records)
    assert not any("event=maintenance_scheduler_failed" in r.getMessage() for r in caplog.records)


def test_every_statement_of_the_scheduler_loop_runs_in_its_own_step() -> None:
    """Gegenprobe zur Quelle: im try des Planers steht jeder Schritt in einem
    eigenen `with self._maintenance_step(...)`, mit eindeutigem Namen — ein
    später ergänzter, ungeschützter Aufruf fiele hier auf."""
    loop = next(
        node for node in ast.walk(ast.parse(BACKGROUND_SOURCE))
        if isinstance(node, ast.FunctionDef) and node.name == "_maintenance_scheduler_loop"
    )
    wiederholung = next(node for node in loop.body if isinstance(node, ast.While))
    versuch = next(node for node in wiederholung.body if isinstance(node, ast.Try))
    namen = []
    for statement in versuch.body:
        assert isinstance(statement, ast.With), f"Zeile {statement.lineno}: Schritt ohne _maintenance_step"
        aufruf = statement.items[0].context_expr
        assert isinstance(aufruf, ast.Call) and aufruf.func.attr == "_maintenance_step"
        namen.append(aufruf.args[0].value)
    assert len(namen) == len(set(namen))
    assert {"duplicate_snapshot", "backup_schedule", "automatic_purge"} <= set(namen)


def _schritt(dienst, name, fehler=None):
    with dienst._maintenance_step(name):
        if fehler is not None:
            raise fehler


def test_repeated_failures_log_one_traceback_then_at_most_one_short_line_per_hour(
    dienst, monkeypatch, caplog
) -> None:
    import app.background as background

    uhr = [1000.0]
    monkeypatch.setattr(background.time, "monotonic", lambda: uhr[0])

    with caplog.at_level(logging.INFO):
        for _ in range(5):  # 5 Takte à 30 s
            _schritt(dienst, "duplicate_snapshot", RuntimeError("kaputt"))
            uhr[0] += 30
        erste = [r for r in caplog.records if "event=maintenance_step_failed" in r.getMessage()]
        assert len(erste) == 1 and erste[0].exc_info
        assert not any("still_failing" in r.getMessage() for r in caplog.records)

        uhr[0] += 3600  # eine Stunde später
        _schritt(dienst, "duplicate_snapshot", RuntimeError("kaputt"))
        _schritt(dienst, "duplicate_snapshot", RuntimeError("kaputt"))  # gleicher Takt, nichts Neues

    kurz = [r for r in caplog.records if "event=maintenance_step_still_failing" in r.getMessage()]
    assert len(kurz) == 1
    assert not kurz[0].exc_info
    meldung = kurz[0].getMessage()
    assert "step=duplicate_snapshot" in meldung and "failures=6" in meldung
    assert "since_last_log=5" in meldung and "RuntimeError: kaputt" in meldung


def test_recovery_is_logged_once_and_resets_the_state(dienst, monkeypatch, caplog) -> None:
    import app.background as background

    monkeypatch.setattr(background.time, "monotonic", lambda: 1000.0)
    with caplog.at_level(logging.INFO):
        _schritt(dienst, "backup_schedule", RuntimeError("kaputt"))
        _schritt(dienst, "backup_schedule", RuntimeError("kaputt"))
        _schritt(dienst, "backup_schedule")
        _schritt(dienst, "backup_schedule")  # läuft weiter fehlerfrei: keine zweite Meldung
        _schritt(dienst, "backup_schedule", RuntimeError("wieder kaputt"))  # neuer Verlauf: wieder mit Traceback

    wieder = [r for r in caplog.records if "event=maintenance_step_recovered" in r.getMessage()]
    assert len(wieder) == 1 and "failures=2" in wieder[0].getMessage()
    traceback_zeilen = [r for r in caplog.records if "event=maintenance_step_failed" in r.getMessage()]
    assert len(traceback_zeilen) == 2


def test_throttling_is_tracked_per_step(dienst, caplog) -> None:
    with caplog.at_level(logging.INFO):
        _schritt(dienst, "a", RuntimeError("x"))
        _schritt(dienst, "b", RuntimeError("y"))
    gemeldet = [r.getMessage() for r in caplog.records if "event=maintenance_step_failed" in r.getMessage()]
    assert len(gemeldet) == 2
