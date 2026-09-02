"""Tests für Entitäts-ID-Validierung und Pfad-Traversal-Schutz."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.paths import entity_dir, hot_file_path, validate_entity_id
from app.storage.index import Index


def _assert_rejected(entity_id: str) -> None:
    try:
        validate_entity_id(entity_id)
        raise AssertionError(f"Gefährliche Entitäts-ID wurde akzeptiert: {entity_id!r}")
    except ValueError:
        pass


def test_accepts_normal_home_assistant_entity_ids() -> None:
    for entity_id in (
        "sensor.wohnzimmer_temperatur",
        "binary_sensor.tuer_1",
        "input_number.sollwert2",
    ):
        assert validate_entity_id(entity_id) == entity_id


def test_rejects_path_traversal_and_non_entity_names() -> None:
    for entity_id in (
        "../index.sqlite",
        "sensor../../index",
        "sensor.temp/../../index",
        "/etc/passwd",
        "sensor.temp\\..\\index",
        "sensor..temp",
        ".hidden",
        "Sensor.Temp",
        "sensor.temp-name",
        "sensor.",
    ):
        _assert_rejected(entity_id)


def test_entity_directory_stays_below_storage_area() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-paths-"))
    try:
        path = entity_dir(tmp, "archive", "sensor.temp")
        assert path == (tmp / "archive").resolve() / "sensor.temp"
        assert path.resolve().is_relative_to((tmp / "archive").resolve())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rejects_entity_symlink_pointing_outside_data_dir() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-paths-"))
    outside = Path(tempfile.mkdtemp(prefix="zeitarchiv-outside-"))
    try:
        archive = tmp / "archive"
        archive.mkdir()
        (archive / "sensor.temp").symlink_to(outside, target_is_directory=True)
        try:
            entity_dir(tmp, "archive", "sensor.temp")
            raise AssertionError("Symlink-Ausbruch wurde akzeptiert")
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_rejects_storage_area_symlink_pointing_outside_data_dir() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-paths-"))
    outside = Path(tempfile.mkdtemp(prefix="zeitarchiv-outside-"))
    try:
        (tmp / "hot").symlink_to(outside, target_is_directory=True)
        try:
            hot_file_path(tmp, "sensor.temp", "2026-08")
            raise AssertionError("Storage-Symlink-Ausbruch wurde akzeptiert")
        except ValueError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_index_rejects_invalid_id_before_inserting_entity() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-paths-"))
    index = Index(tmp / "index.sqlite")
    try:
        try:
            index.get_or_create_entity("../../escape", "sensor", None, None)
            raise AssertionError("Index hat eine gefährliche Entitäts-ID akzeptiert")
        except ValueError:
            pass
        assert index.list_entities() == []
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
