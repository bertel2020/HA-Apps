"""Nebenläufigkeitstests für die zentrale Storage-Koordination."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.coordinator import CoordinatorBusy, StorageCoordinator


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


def test_entity_timeout_raises_coordinator_busy_and_recovers() -> None:
    """Ein Aufrufer mit timeout scheitert sichtbar statt für immer zu
    blockieren, wenn ein anderer Thread dieselbe Entität hält — und der
    Coordinator erholt sich danach normal (kein verwaister Zähler)."""
    coordinator = StorageCoordinator()
    entered = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with coordinator.entity("sensor.a"):
            entered.set()
            release.wait(2)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert entered.wait(1)
        try:
            with coordinator.entity("sensor.a", timeout=0.15):
                pass
            raise AssertionError("Erwerb hätte am Timeout scheitern müssen")
        except CoordinatorBusy:
            pass
    finally:
        release.set()
        t.join(1)
    # Nach Freigabe funktioniert der Zugriff wieder normal.
    with coordinator.entity("sensor.a", timeout=1):
        pass


def test_entity_timeout_while_exclusive_active() -> None:
    """timeout gilt auch für die Zulassungs-Wartezeit vor einer laufenden
    exclusive()-Wartung, nicht nur für den Entitäts-Lock-Erwerb danach."""
    coordinator = StorageCoordinator()
    exclusive_entered = threading.Event()
    release_exclusive = threading.Event()

    def exclusive_worker() -> None:
        with coordinator.exclusive():
            exclusive_entered.set()
            release_exclusive.wait(2)

    t = threading.Thread(target=exclusive_worker)
    t.start()
    try:
        assert exclusive_entered.wait(1)
        try:
            with coordinator.entity("sensor.a", timeout=0.15):
                pass
            raise AssertionError("Erwerb hätte am Timeout scheitern müssen")
        except CoordinatorBusy:
            pass
    finally:
        release_exclusive.set()
        t.join(1)
    with coordinator.entity("sensor.a", timeout=1):
        pass


def test_entities_rolls_back_already_acquired_locks_on_timeout() -> None:
    """entities() erwirbt in sortierter Reihenfolge ("sensor.a" vor
    "sensor.b"); scheitert der zweite Erwerb am Timeout, muss der bereits
    erworbene erste Lock sofort wieder frei sein — sonst bliebe "sensor.a"
    verwaist gesperrt, obwohl der ganze Aufruf fehlgeschlagen ist."""
    coordinator = StorageCoordinator()
    entered = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with coordinator.entity("sensor.b"):
            entered.set()
            release.wait(2)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert entered.wait(1)
        try:
            with coordinator.entities(["sensor.a", "sensor.b"], timeout=0.15):
                pass
            raise AssertionError("Erwerb hätte am Timeout scheitern müssen")
        except CoordinatorBusy:
            pass
        # Trotz des gescheiterten Gesamtaufrufs sofort wieder frei — kein
        # Rollback-Leak auf dem bereits erworbenen ersten Lock.
        with coordinator.entity("sensor.a", timeout=0.5):
            pass
    finally:
        release.set()
        t.join(1)


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
