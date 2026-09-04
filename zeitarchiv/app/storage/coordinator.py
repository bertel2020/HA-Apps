"""Koordiniert parallele Zugriffe auf Hot-, Archiv- und Rollup-Dateien."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from .paths import validate_entity_id


class CoordinatorBusy(Exception):
    """Ein Storage-Lock wurde nicht innerhalb des angegebenen timeout erhalten.

    Analog zu IndexBusy/_TimeoutLock in storage/index.py, aber für den
    Zulassungs-Gate (Condition) plus ein oder mehrere Entitäts-Locks statt
    eines einzelnen Locks — siehe entity()/entities()/exclusive() unten.
    timeout=None (der Default überall) bewahrt das bisherige, unbegrenzte
    Warteverhalten; wird also nie ausgelöst, solange kein Aufrufer explizit
    ein Zeitbudget übergibt."""


def _wait_timeout(deadline: float | None) -> float | None:
    """timeout-Wert für Condition.wait_for() — None bedeutet dort "unbegrenzt"."""
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _acquire_timeout(deadline: float | None) -> float:
    """timeout-Wert für Lock.acquire() — dort bedeutet -1 "unbegrenzt", nicht None."""
    if deadline is None:
        return -1.0
    return max(0.0, deadline - time.monotonic())


class StorageCoordinator:
    """Entitätssperren plus globale Wartungssperre.

    Operationen verschiedener Entitäten dürfen parallel laufen. Backup,
    Retention, Rotation, Purge und Import verwenden ``exclusive()`` und warten,
    bis sämtliche Entitätsoperationen abgeschlossen sind. Sobald eine globale
    Wartung wartet, werden keine neuen Entitätsoperationen mehr vorgelassen;
    dadurch kann ein kontinuierlicher Schreibstrom das Backup nicht verhungern
    lassen.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._entity_locks: dict[str, threading.RLock] = {}
        self._active_entity_operations = 0
        self._exclusive_active = False
        self._exclusive_waiters = 0

    def _lock_for(self, entity_id: str) -> threading.RLock:
        with self._condition:
            return self._entity_locks.setdefault(entity_id, threading.RLock())

    def _finish_entity_operation(self) -> None:
        with self._condition:
            self._active_entity_operations -= 1
            if self._active_entity_operations == 0:
                self._condition.notify_all()

    @contextmanager
    def entity(self, entity_id: str, timeout: float | None = None) -> Iterator[None]:
        """Serialisiert Dateioperationen genau einer Entität.

        timeout=None (Default) wartet unbegrenzt wie bisher — richtig für
        Hintergrund-Jobs (Backup, Retention, Rotation, Purge, Import), wo ein
        Abbruch nichts gewinnt. Ein gesetztes Zeitbudget gilt gemeinsam für
        die Zulassungs-Wartezeit und den anschließenden Lock-Erwerb und wirft
        CoordinatorBusy, sobald es aufgebraucht ist — gedacht für synchrone
        HTTP-Anfragen (siehe storage_locked() in route_support.py), die sonst
        bei einem hängenden Halter für immer blockieren würden."""
        validate_entity_id(entity_id)
        entity_lock = self._lock_for(entity_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            acquired = self._condition.wait_for(
                lambda: not self._exclusive_active and self._exclusive_waiters == 0,
                timeout=_wait_timeout(deadline),
            )
            if not acquired:
                raise CoordinatorBusy(
                    f"Speicherzugriff für {entity_id!r} nicht innerhalb von {timeout:g}s erhalten"
                )
            self._active_entity_operations += 1
        if not entity_lock.acquire(timeout=_acquire_timeout(deadline)):
            self._finish_entity_operation()
            raise CoordinatorBusy(
                f"Speicherzugriff für {entity_id!r} nicht innerhalb von {timeout:g}s erhalten"
            )
        try:
            yield
        finally:
            entity_lock.release()
            self._finish_entity_operation()

    @contextmanager
    def entities(self, entity_ids: list[str], timeout: float | None = None) -> Iterator[None]:
        """Sperrt mehrere Entitäten deadlock-frei in sortierter Reihenfolge.

        timeout siehe entity(). Bei mehreren Locks gilt ein gemeinsames
        Zeitbudget über die gesamte Erwerbs-Schleife; schlägt der Erwerb
        eines Locks fehl, werden bereits erworbene Locks sofort wieder
        freigegeben, bevor CoordinatorBusy den Aufrufer erreicht."""
        ids = sorted(set(entity_ids))
        for entity_id in ids:
            validate_entity_id(entity_id)
        locks = [self._lock_for(entity_id) for entity_id in ids]
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            acquired = self._condition.wait_for(
                lambda: not self._exclusive_active and self._exclusive_waiters == 0,
                timeout=_wait_timeout(deadline),
            )
            if not acquired:
                raise CoordinatorBusy(f"Speicherzugriff nicht innerhalb von {timeout:g}s erhalten")
            self._active_entity_operations += 1
        held: list[threading.RLock] = []
        try:
            for lock in locks:
                if not lock.acquire(timeout=_acquire_timeout(deadline)):
                    raise CoordinatorBusy(f"Speicherzugriff nicht innerhalb von {timeout:g}s erhalten")
                held.append(lock)
        except CoordinatorBusy:
            for lock in reversed(held):
                lock.release()
            self._finish_entity_operation()
            raise
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()
            self._finish_entity_operation()

    @contextmanager
    def exclusive(self, timeout: float | None = None) -> Iterator[None]:
        """Stoppt für globale Wartung vorübergehend alle Dateioperationen.

        timeout siehe entity() — betrifft nur das Warten auf Zulassung
        (bis keine Entitätsoperation mehr aktiv ist), nicht die Dauer der
        eigentlichen exklusiven Arbeit selbst. Backup/Retention/Rotation/
        Purge/Import rufen dies unverändert ohne timeout auf und dürfen
        beliebig lange dauern."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            self._exclusive_waiters += 1
            try:
                acquired = self._condition.wait_for(
                    lambda: not self._exclusive_active
                    and self._active_entity_operations == 0,
                    timeout=_wait_timeout(deadline),
                )
                if not acquired:
                    raise CoordinatorBusy(
                        f"Exklusiver Speicherzugriff nicht innerhalb von {timeout:g}s erhalten"
                    )
                self._exclusive_active = True
            finally:
                self._exclusive_waiters -= 1
        try:
            yield
        finally:
            with self._condition:
                self._exclusive_active = False
                self._condition.notify_all()
