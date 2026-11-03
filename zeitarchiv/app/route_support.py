"""Gemeinsame FastAPI-Routenhilfen ohne Abhängigkeit vom Hauptmodul."""

from __future__ import annotations

from .i18n import tr

import functools
import inspect
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

from starlette.responses import JSONResponse

from .storage.coordinator import StorageCoordinator

# Default-Zeitbudget für storage_locked() — deutlich großzügiger als
# INDEX_LOCK_TIMEOUT_SECONDS (8s), weil ein Coordinator-Lock legitim länger
# von einem laufenden Backup/Import gehalten werden kann als das SQLite-Lock.
STORAGE_LOCKED_TIMEOUT_SECONDS = 30.0

# Wartezeit auf exclusive() in UI-Routen (Löschen eines Reports/Backups). Ein wartender exclusive()-Aufruf hält
# neue Entitätsoperationen an, auch die Aufnahme aus HA — deshalb kurz, danach 503 statt eines Staus hinter einem
# langen Import.
UI_EXCLUSIVE_TIMEOUT_SECONDS = 5.0

# Starlette lässt in request.form() standardmäßig nur 1000 Felder zu. Sammelaktionen
# schicken je gewähltem Eintrag ein Feld (entity_id, ts); eine ganze Seite mit 1000
# Zeilen plus Zusatzfeldern scheiterte sonst mit "400 Too many fields", ohne dass
# etwas geschah.
MAX_FORM_FIELDS = 10_000


class UploadLimitExceeded(ValueError):
    """Ein Upload überschreitet sein festgelegtes Größenlimit."""


def format_upload_limit(max_bytes: int) -> str:
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    if max_bytes % gib == 0:
        return f"{max_bytes // gib} GiB"
    return f"{max_bytes // mib} MiB"


#: Zuschlag auf das Dateilimit für den Multipart-Rahmen (Grenzen, Feldnamen, Dateiname) in Content-Length.
UPLOAD_OVERHEAD_BYTES = 1024 * 1024


class UploadSizeLimitMiddleware:
    """Weist einen Upload anhand von Content-Length ab, bevor Starlette das Formular einliest.

    Starlette legt den ganzen Multipart-Body in einer Temp-Datei ab, bevor die Route überhaupt läuft; copy_upload_limited()
    greift erst danach. Ein versehentlich gewählter 2-GB-Upload an die CSV-Route (Limit 256 MiB) füllte so erst die
    Platte und brach dann ab. Eine Anfrage ohne Content-Length (chunked) lässt die Middleware durch; nginx davor
    erlaubt für die Oberfläche höchstens 2050 MB. Reines ASGI wie die übrigen Middlewares (kein zweiter Task)."""

    def __init__(self, app, limits: dict[str, int]) -> None:
        self.app = app
        self.limits = limits

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST":
            limit = self.limits.get(scope["path"].rstrip("/"))
            if limit is not None:
                raw = dict(scope["headers"]).get(b"content-length")
                if raw is not None and raw.isdigit() and int(raw) > limit + UPLOAD_OVERHEAD_BYTES:
                    response = JSONResponse(
                        {"detail": tr("Upload ist größer als {format_upload_limit}", format_upload_limit=format_upload_limit(limit))},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


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
                        tr("Upload ist größer als {format_upload_limit}", format_upload_limit=format_upload_limit(max_bytes))
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


def dir_size_and_newest_mtime(paths: Iterable[Path]) -> tuple[int, float | None]:
    """Gesamtgröße und jüngste Änderungszeit über mehrere Bäume, in EINEM Walk.

    Für housekeeping.import_leftovers (notices.py): die Größe sagt, ob es sich
    überhaupt zu erwähnen lohnt, die Änderungszeit, ob die Import-Sitzung
    vorbei ist. Zweimal getrennt zu laufen wäre bei den gemessenen 47.494
    Dateien der Testinstanz (278 ms je Durchlauf) die doppelte Arbeit für
    dieselben stat()-Aufrufe.

    Anders als dir_size() genügt ein stat() je Eintrag statt is_file() plus
    stat() — bei dieser Dateizahl der Unterschied zwischen einem und zwei
    Syscalls pro Datei."""
    total_bytes = 0
    newest_mtime = 0.0
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            try:
                info = path.stat()
            except OSError:
                # Ein Import, der parallel aufräumt, ist hier der Normalfall
                # und nicht die Ausnahme — eine zwischenzeitlich verschwundene
                # Datei darf den Walk nicht abbrechen.
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            total_bytes += info.st_size
            newest_mtime = max(newest_mtime, info.st_mtime)
    return total_bytes, (newest_mtime or None)


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
