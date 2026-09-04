"""Gemeinsame FastAPI-Routenhilfen ohne Abhängigkeit vom Hauptmodul."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterable
from pathlib import Path

from .storage.coordinator import StorageCoordinator

# Default-Zeitbudget für storage_locked() — deutlich großzügiger als
# INDEX_LOCK_TIMEOUT_SECONDS (8s), weil ein Coordinator-Lock legitim länger
# von einem laufenden Backup/Import gehalten werden kann als das SQLite-Lock.
STORAGE_LOCKED_TIMEOUT_SECONDS = 30.0


class UploadLimitExceeded(ValueError):
    """Ein Upload überschreitet sein festgelegtes Größenlimit."""


def format_upload_limit(max_bytes: int) -> str:
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    if max_bytes % gib == 0:
        return f"{max_bytes // gib} GiB"
    return f"{max_bytes // mib} MiB"


def copy_upload_limited(source, destination: Path, max_bytes: int) -> int:
    """Kopiert einen Upload atomar und bricht oberhalb von max_bytes ab."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_suffix(destination.suffix + ".part")
    written = 0
    try:
        with part_path.open("wb") as handle:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise UploadLimitExceeded(
                        f"Upload ist größer als {format_upload_limit(max_bytes)}"
                    )
                handle.write(chunk)
        part_path.replace(destination)
        return written
    finally:
        part_path.unlink(missing_ok=True)


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def storage_locked(
    coordinator: StorageCoordinator,
    entity_ids_getter: Callable[[dict], str | Iterable[str]],
    timeout: float | None = STORAGE_LOCKED_TIMEOUT_SECONDS,
):
    """Serialisiert einen synchronen Handler für seine betroffenen Entitäten.

    timeout begrenzt hier bewusst, anders als bei den Hintergrund-Jobs
    (Backup/Retention/Rotation/Purge/Import, die coordinator.entities()
    weiterhin ohne timeout aufrufen): ein hängender Halter des Coordinator-
    Locks würde sonst den anfragenden HTTP-Worker-Thread für immer blockieren
    statt nach STORAGE_LOCKED_TIMEOUT_SECONDS mit CoordinatorBusy (503, siehe
    main.py) sichtbar und wiederholbar zu scheitern."""

    def decorate(func):
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            resolved = entity_ids_getter(bound.arguments)
            entity_ids = [resolved] if isinstance(resolved, str) else list(resolved)
            with coordinator.entities(entity_ids, timeout=timeout):
                return func(*args, **kwargs)

        return wrapped

    return decorate
