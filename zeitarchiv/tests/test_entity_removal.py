"""Tests für das gezielte Löschen von Werten und Entitäten."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import entity_removal
from app.storage.index import Index


def _write_fixture_files(root: Path, entity_id: str) -> None:
    hot = root / "hot"
    archive = root / "archive" / entity_id
    rollup = root / "rollup" / entity_id
    hot.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    rollup.mkdir(parents=True, exist_ok=True)
    (hot / f"{entity_id}-2026-08.csv").write_text("1,2\n", encoding="utf-8")
    (archive / "2026-07.parquet").write_bytes(b"archive")
    (rollup / "stunde.parquet").write_bytes(b"rollup")


def test_delete_all_values_preserves_entity_configuration_and_other_entity() -> None:
    root = Path(tempfile.mkdtemp(prefix="zeitarchiv-entity-removal-"))
    try:
        index = Index(root / "index.sqlite")
        entity_id = "sensor.target"
        other_id = "sensor.other"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
        index.get_or_create_entity(other_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, resolution="5min", retention="1y", decimals="2")
        index.record_write(entity_id, 100.0)
        index.mark_deleted(entity_id, [100.0])
        index.claim_ingest_event("event-target", entity_id, 100.0)
        _write_fixture_files(root, entity_id)
        _write_fixture_files(root, other_id)

        entity_removal.delete_all_values(root, index, entity_id)

        entity = index.get_entity(entity_id)
        assert entity is not None
        assert entity["resolution"] == "5min"
        assert entity["retention"] == "1y"
        assert entity["decimals"] == "2"
        assert entity["row_count"] == 0
        assert entity["first_ts"] is None and entity["last_ts"] is None
        assert index.get_deleted_counts_for_entity(entity_id) == {}
        assert not (root / "hot" / f"{entity_id}-2026-08.csv").exists()
        assert not (root / "archive" / entity_id).exists()
        assert not (root / "rollup" / entity_id).exists()
        assert (root / "hot" / f"{other_id}-2026-08.csv").exists()
        assert (root / "archive" / other_id).exists()
        assert (root / "rollup" / other_id).exists()

        # Gelöschte Idempotenzdaten blockieren keine spätere Neuaufnahme.
        assert index.claim_ingest_event("event-target", entity_id, 100.0)["status"] == "processing"
        index.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_delete_entity_removes_values_and_index_entry() -> None:
    root = Path(tempfile.mkdtemp(prefix="zeitarchiv-entity-removal-"))
    try:
        index = Index(root / "index.sqlite")
        entity_id = "sensor.target"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
        index.record_write(entity_id, 100.0)
        _write_fixture_files(root, entity_id)

        entity_removal.delete_entity(root, index, entity_id)

        assert index.get_entity(entity_id) is None
        assert not (root / "hot" / f"{entity_id}-2026-08.csv").exists()
        assert not (root / "archive" / entity_id).exists()
        assert not (root / "rollup" / entity_id).exists()
        index.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_delete_refuses_archive_symlink_outside_data_directory() -> None:
    root = Path(tempfile.mkdtemp(prefix="zeitarchiv-entity-removal-"))
    outside = Path(tempfile.mkdtemp(prefix="zeitarchiv-entity-outside-"))
    try:
        index = Index(root / "index.sqlite")
        entity_id = "sensor.target"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
        (outside / "keep.txt").write_text("do not delete", encoding="utf-8")
        (root / "archive").mkdir(parents=True)
        (root / "archive" / entity_id).symlink_to(outside, target_is_directory=True)

        try:
            entity_removal.delete_entity(root, index, entity_id)
            assert False, "Unsicherer Symlink muss abgewiesen werden"
        except ValueError:
            pass

        assert (outside / "keep.txt").read_text(encoding="utf-8") == "do not delete"
        assert index.get_entity(entity_id) is not None
        index.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
