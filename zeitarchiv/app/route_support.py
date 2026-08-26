"""Gemeinsame FastAPI-Routenhilfen ohne Abhängigkeit vom Hauptmodul."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterable

from .storage.coordinator import StorageCoordinator


def storage_locked(
    coordinator: StorageCoordinator,
    entity_ids_getter: Callable[[dict], str | Iterable[str]],
):
    """Serialisiert einen synchronen Handler für seine betroffenen Entitäten."""

    def decorate(func):
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            resolved = entity_ids_getter(bound.arguments)
            entity_ids = [resolved] if isinstance(resolved, str) else list(resolved)
            with coordinator.entities(entity_ids):
                return func(*args, **kwargs)

        return wrapped

    return decorate
