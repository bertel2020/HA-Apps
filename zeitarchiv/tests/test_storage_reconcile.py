"""Regressionstests für Prüfung und Reparatur des Speicherindex."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
from jinja2 import Environment, FileSystemLoader

TZ = ZoneInfo("Europe/Berlin")
ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT  / "app" / "templates"
sys.path.insert(0, str(ROOT ))

from app.storage import hotbuffer, reconcile  # noqa: E402
from app.storage.index import Index  # noqa: E402


def test_storage_audit_previews_and_atomically_repairs_derived_metadata(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    entity_id = "sensor.test_leistung"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W", "Testleistung")

    archive_dir = tmp_path / "archive" / entity_id
    archive_dir.mkdir(parents=True)
    archive_path = archive_dir / "2024-01.parquet"
    pq.write_table(pa.table({"ts": [1_704_067_200.0, 1_704_067_260.0], "value": [1.0, 2.0]}), archive_path)
    hot_ts = datetime(2026, 8, 25, 12, tzinfo=TZ).timestamp()
    hotbuffer.append(tmp_path, entity_id, hot_ts, 3.0, TZ)

    # Absichtlich falscher Cache und eine logische Löschmarkierung.
    index.record_write(entity_id, hot_ts)
    index.add_size_bytes(entity_id, 7)
    index.mark_deleted(entity_id, [1_704_067_200.0])

    preview = reconcile.audit_storage_metadata(tmp_path, index, TZ, repair=False)
    assert preview["repaired"] is False
    assert len(preview["mismatches"]) == 1
    mismatch = preview["mismatches"][0]
    assert mismatch["indexed_visible_rows"] == 0
    assert mismatch["actual_visible_rows"] == 2
    assert index.get_entity(entity_id)["row_count"] == 1

    repaired = reconcile.audit_storage_metadata(tmp_path, index, TZ, repair=True)
    assert repaired["repaired"] is True
    entity = index.get_entity(entity_id)
    assert entity["row_count"] == 3
    assert entity["size_bytes"] == archive_path.stat().st_size
    assert entity["first_ts"] == 1_704_067_200.0
    assert entity["last_ts"] == hot_ts
    assert index.get_overview()["total_rows"] == 2
    index.close()


def test_storage_audit_streams_hot_files_without_materializing_them(monkeypatch, tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    entity_id = "sensor.streaming"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W", "Streaming")
    hotbuffer.append(tmp_path, entity_id, 10.0, 1.0, TZ)
    hotbuffer.append(tmp_path, entity_id, 20.0, 2.0, TZ)

    monkeypatch.setattr(
        hotbuffer,
        "read_rows",
        lambda _path: (_ for _ in ()).throw(AssertionError("Hot-Datei wurde materialisiert")),
    )
    report = reconcile.audit_storage_metadata(tmp_path, index, TZ, repair=True)

    assert report["entities_checked"] == 1
    entity = index.get_entity(entity_id)
    assert entity["row_count"] == 2
    assert entity["first_ts"] == 10.0
    assert entity["last_ts"] == 20.0
    index.close()


def test_storage_index_settings_fragment_has_preview_and_confirmed_repair() -> None:
    source = (TEMPLATES / "_settings_storage_index_form.html").read_text(encoding="utf-8")
    # Speicherplatz/Indexkonsistenz lebt seit 0.75.0 in Housekeeping statt
    # Einstellungen — die Formular-Datei selbst blieb unverändert.
    housekeeping = (TEMPLATES / "housekeeping.html").read_text(encoding="utf-8")
    assert 'id="storage-index-form"' in housekeeping
    assert "Indexkonsistenz" in source
    assert 'hx-post="settings/storage-index/check"' in source
    assert 'hx-post="settings/storage-index/repair"' in source
    assert "hx-confirm=" in source
    assert 'data-confirm-label="Index reparieren"' in source
    Environment(loader=FileSystemLoader(TEMPLATES)).get_template("_settings_storage_index_form.html")


def test_reconciliation_runs_after_restore_startup_and_both_import_paths() -> None:
    main = (ROOT  / "app" / "main.py").read_text(encoding="utf-8")
    import_routes = (ROOT  / "app" / "import_routes.py").read_text(encoding="utf-8")
    restore = main.index("backup.apply_pending_restore(DATA_DIR, BACKUPS_DIR)")
    startup_repair = main.index("_run_storage_reconciliation(repair=True)")
    assert restore < startup_repair
    assert 'index.get_setting("storage_clean_shutdown", "0") == "1"' in main
    assert "_requires_synchronous_reconciliation" in main
    assert "target=_background_storage_reconciliation" in main
    assert "with storage_coordinator.entity(entity_id):" in main
    # Symcon-/CSV-Import (Reconciliation nach Import) leben seit der Extraktion
    # in import_routes.py (siehe test_route_modules.py), nicht mehr in main.py.
    assert "entity_ids=sorted({target for _, target, _ in mapped}), repair=True" in import_routes
    assert "self.deps.run_storage_reconciliation(entity_ids=[entity_id], repair=True)" in import_routes
