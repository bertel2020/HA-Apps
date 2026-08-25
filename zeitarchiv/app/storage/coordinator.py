"""Koordiniert parallele Zugriffe auf Hot-, Archiv- und Rollup-Dateien."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from .paths import validate_entity_id


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

    @contextmanager
    def entity(self, entity_id: str) -> Iterator[None]:
        """Serialisiert Dateioperationen genau einer Entität."""
        validate_entity_id(entity_id)
        entity_lock = self._lock_for(entity_id)
        with self._condition:
            self._condition.wait_for(
                lambda: not self._exclusive_active and self._exclusive_waiters == 0
            )
            self._active_entity_operations += 1
        entity_lock.acquire()
        try:
            yield
        finally:
            entity_lock.release()
            with self._condition:
                self._active_entity_operations -= 1
                if self._active_entity_operations == 0:
                    self._condition.notify_all()

    @contextmanager
    def entities(self, entity_ids: list[str]) -> Iterator[None]:
        """Sperrt mehrere Entitäten deadlock-frei in sortierter Reihenfolge."""
        ids = sorted(set(entity_ids))
        for entity_id in ids:
            validate_entity_id(entity_id)
        locks = [self._lock_for(entity_id) for entity_id in ids]
        with self._condition:
            self._condition.wait_for(
                lambda: not self._exclusive_active and self._exclusive_waiters == 0
            )
            self._active_entity_operations += 1
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()
            with self._condition:
                self._active_entity_operations -= 1
                if self._active_entity_operations == 0:
                    self._condition.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Stoppt für globale Wartung vorübergehend alle Dateioperationen."""
        with self._condition:
            self._exclusive_waiters += 1
            try:
                self._condition.wait_for(
                    lambda: not self._exclusive_active
                    and self._active_entity_operations == 0
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
