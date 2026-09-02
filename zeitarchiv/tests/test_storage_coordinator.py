"""Nebenläufigkeitstests für die zentrale Storage-Koordination."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.coordinator import StorageCoordinator


def test_same_entity_is_serialized() -> None:
    coordinator = StorageCoordinator()
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with coordinator.entity("sensor.a"):
            entered.set()
            release.wait(2)

    def second() -> None:
        entered.wait(2)
        with coordinator.entity("sensor.a"):
            second_entered.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    assert entered.wait(1)
    assert not second_entered.wait(0.05)
    release.set()
    t1.join(1)
    t2.join(1)
    assert second_entered.is_set()


def test_different_entities_can_run_in_parallel() -> None:
    coordinator = StorageCoordinator()
    first_entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()

    def worker(entity_id: str, entered: threading.Event) -> None:
        with coordinator.entity(entity_id):
            entered.set()
            release.wait(2)

    t1 = threading.Thread(target=worker, args=("sensor.a", first_entered))
    t2 = threading.Thread(target=worker, args=("sensor.b", second_entered))
    t1.start()
    t2.start()
    assert first_entered.wait(1)
    assert second_entered.wait(1)
    release.set()
    t1.join(1)
    t2.join(1)


def test_exclusive_waits_for_entity_and_blocks_new_operations() -> None:
    coordinator = StorageCoordinator()
    entity_entered = threading.Event()
    release_entity = threading.Event()
    exclusive_entered = threading.Event()
    release_exclusive = threading.Event()
    later_entity_entered = threading.Event()

    def entity_worker() -> None:
        with coordinator.entity("sensor.a"):
            entity_entered.set()
            release_entity.wait(2)

    def exclusive_worker() -> None:
        entity_entered.wait(2)
        with coordinator.exclusive():
            exclusive_entered.set()
            release_exclusive.wait(2)

    def later_entity_worker() -> None:
        entity_entered.wait(2)
        time.sleep(0.02)
        with coordinator.entity("sensor.b"):
            later_entity_entered.set()

    threads = [
        threading.Thread(target=entity_worker),
        threading.Thread(target=exclusive_worker),
        threading.Thread(target=later_entity_worker),
    ]
    for thread in threads:
        thread.start()
    assert entity_entered.wait(1)
    assert not exclusive_entered.wait(0.05)
    assert not later_entity_entered.is_set()
    release_entity.set()
    assert exclusive_entered.wait(1)
    assert not later_entity_entered.wait(0.05)
    release_exclusive.set()
    for thread in threads:
        thread.join(1)
    assert later_entity_entered.is_set()


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
