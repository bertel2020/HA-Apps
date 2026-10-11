"""Externes Backup-Ziel: kopiert ein fertiges Backup-ZIP zusätzlich auf einen von
Home Assistant eingebundenen Netzwerkspeicher (NFS/CIFS unter /share oder /media).

Zeitarchiv bindet nichts selbst ein — im Add-on-Container wäre das ohne
erweiterte Rechte nicht möglich, und Home Assistant kann es bereits
(Einstellungen → System → Speicher → Netzwerkspeicher). Die App sieht nur ein
Verzeichnis. Konzept: BACKUP_EXTERN_KONZEPT.md.

Alles, was auf eine Netzwerkfreigabe zugreift, kann bei ausgefallenem Server
beliebig lange blockieren (hart eingebundenes NFS) und lässt sich dann nicht
abbrechen. Deshalb laufen Übertragung und Vorabprüfungen über
run_with_watchdog(): ein eigener Thread, den der Aufrufer bei ausbleibendem
Lebenszeichen aufgibt, statt selbst daran zu hängen.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from ..i18n import tr
from . import backup as backup_mod

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Nur unterhalb dieser Verzeichnisse darf das Ziel liegen (config.yaml: map).
EXTERNAL_ROOTS: tuple[Path, ...] = (Path("/share"), Path("/media"))
MOUNTINFO_PATH = Path("/proc/self/mountinfo")

BACKUP_GLOB = "zeitarchiv-backup-*.zip"
PART_GLOB = ".zeitarchiv-backup-*.part"
TEST_PREFIX = ".zeitarchiv-test-"
COPY_CHUNK_BYTES = 4 * 1024 * 1024
# So lange darf eine Operation auf dem Ziel ohne Lebenszeichen bleiben.
STALL_SECONDS = 120.0
PROBE_SECONDS = 5.0
STALE_PART_SECONDS = 86400.0

SUBFOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")

# Dateisystemtypen, bei denen ein Zugriff den Mount erst auslöst (systemd-
# Automount, den Home Assistant für Netzwerkspeicher anlegt).
AUTOMOUNT_FSTYPES = {"autofs"}
NETWORK_FSTYPES = {"nfs", "nfs4", "cifs", "smb3", "smbfs"}

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"


class ExternalTargetError(Exception):
    """Fehler beim externen Ziel, Text ist für die Oberfläche bestimmt."""


@dataclass(frozen=True)
class MountEntry:
    path: Path
    name: str
    root: Path
    mounted: bool
    fstype: str | None
    source: str | None
    free_bytes: int | None
    total_bytes: int | None

    @property
    def kind(self) -> str:
        return "share" if self.root.name == "share" else "media"

    @property
    def network(self) -> bool:
        return self.fstype in NETWORK_FSTYPES or self.fstype in AUTOMOUNT_FSTYPES


@dataclass(frozen=True)
class ExternalConfig:
    type: str = "off"
    mount: str = ""
    subfolder: str = ""
    keep: str = "same"

    @property
    def enabled(self) -> bool:
        return self.type == "dir" and bool(self.mount)


def load_config(index) -> ExternalConfig:
    return ExternalConfig(
        type=index.get_setting("backup_external_type", "off"),
        mount=index.get_setting("backup_external_mount", ""),
        subfolder=index.get_setting("backup_external_subfolder", ""),
        keep=index.get_setting("backup_external_keep", "same"),
    )


def run_with_watchdog(
    work: Callable[[Callable[[], None]], T],
    stall_seconds: float = STALL_SECONDS,
    poll_seconds: float = 0.5,
) -> T:
    """Führt work(beat) in einem eigenen Thread aus. work ruft beat() bei jedem
    Fortschritt. Bleibt beat() länger als stall_seconds aus, gibt der Aufrufer
    auf (ExternalTargetError); der hängende Thread bleibt als Daemon zurück."""
    state: dict = {"tick": time.monotonic(), "result": None, "error": None, "done": False}

    def beat() -> None:
        state["tick"] = time.monotonic()

    def target() -> None:
        try:
            state["result"] = work(beat)
        except BaseException as exc:  # an den Aufrufer weiterreichen
            state["error"] = exc
        finally:
            state["done"] = True

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    while not state["done"]:
        thread.join(poll_seconds)
        if not state["done"] and time.monotonic() - state["tick"] > stall_seconds:
            raise ExternalTargetError(
                tr("Das externe Ziel antwortet nicht. Ist der Netzwerkspeicher erreichbar?")
            )
    if state["error"] is not None:
        raise state["error"]
    return state["result"]


def _unescape_mountinfo(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def parse_mountinfo(text: str) -> dict[str, tuple[str, str]]:
    """Mountpunkt → (Dateisystemtyp, Quelle). Format siehe proc(5): Felder bis
    zum einzelnen „-“, danach fstype, Quelle, Super-Optionen."""
    mounts: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields = left.split()
        tail = right.split()
        if len(fields) < 5 or len(tail) < 2:
            continue
        mounts[_unescape_mountinfo(fields[4])] = (tail[0], _unescape_mountinfo(tail[1]))
    return mounts


def _usage(path: Path) -> tuple[int | None, int | None]:
    """(frei, gesamt) — None, wenn das Ziel keine brauchbaren Werte liefert
    (manche NAS melden 0)."""
    stat = os.statvfs(path)
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    if total <= 0:
        return None, None
    return free, total


def list_mounts(
    roots: tuple[Path, ...] = EXTERNAL_ROOTS,
    mountinfo_path: Path = MOUNTINFO_PATH,
    probe_seconds: float = PROBE_SECONDS,
) -> list[MountEntry]:
    """Unterverzeichnisse von /share und /media. Eingebundene Freigaben mit Typ,
    Quelle und freiem Platz; gewöhnliche Ordner als nicht eingebunden (Backups
    würden dort auf dem lokalen Datenträger landen).

    Ein systemd-Automount (Typ autofs) zählt als eingebunden; der Zugriff für
    den freien Platz löst ihn aus. Antwortet das Ziel nicht, bleibt der Platz
    unbekannt, die Liste erscheint trotzdem."""
    try:
        mounts = parse_mountinfo(mountinfo_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        mounts = {}
    entries: list[MountEntry] = []
    for root in roots:
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        for child in children:
            info = mounts.get(str(child))
            if info is None:
                entries.append(MountEntry(child, child.name, root, False, None, None, None, None))
                continue
            free = total = None
            try:
                free, total = run_with_watchdog(
                    lambda beat, p=child: _usage(p), stall_seconds=probe_seconds, poll_seconds=0.05
                )
            except (ExternalTargetError, OSError):
                pass
            entries.append(MountEntry(child, child.name, root, True, info[0], info[1], free, total))
    return entries


def validate_subfolder(value: str) -> str:
    """Leer oder ein einfacher Ordnername (kein Pfad, kein „..“)."""
    value = (value or "").strip()
    if not value:
        return ""
    if ".." in value or not SUBFOLDER_RE.match(value):
        raise ValueError(
            tr("Ungültiger Unterordner. Erlaubt sind Buchstaben, Ziffern, Punkt, Bindestrich, Unterstrich und Leerzeichen (kein Pfad).")
        )
    return value


def resolve_target(
    mount: str,
    subfolder: str,
    roots: tuple[Path, ...] = EXTERNAL_ROOTS,
    mountinfo_path: Path = MOUNTINFO_PATH,
) -> Path:
    """Zielverzeichnis aus Mount und Unterordner. Der Mount muss in der Liste
    der eingebundenen Freigaben stehen, sonst Fehler."""
    sub = validate_subfolder(subfolder)
    wanted = Path(mount)
    for entry in list_mounts(roots, mountinfo_path):
        if entry.path == wanted:
            if not entry.mounted:
                raise ExternalTargetError(
                    tr("{name} ist nicht eingebunden. Backups würden auf dem lokalen Datenträger landen.", name=entry.name)
                )
            return entry.path / sub if sub else entry.path
    raise ExternalTargetError(tr("Der gewählte Netzwerkspeicher ist nicht mehr vorhanden."))


def _explain(exc: OSError) -> ExternalTargetError:
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
        return ExternalTargetError(
            tr("Zugriff verweigert. Das Ziel ist eingebunden, aber nicht beschreibbar. Bei NFS den Export des NAS prüfen (root_squash), bei CIFS Benutzer und Freigabe-Rechte.")
        )
    if exc.errno == errno.ENOSPC:
        return ExternalTargetError(tr("Zu wenig Platz am Ziel."))
    if exc.errno == errno.EROFS:
        return ExternalTargetError(tr("Das Ziel ist schreibgeschützt eingebunden."))
    if exc.errno in (errno.ESTALE, errno.EIO, errno.ENOTCONN, errno.EHOSTDOWN, errno.ETIMEDOUT):
        return ExternalTargetError(tr("Das externe Ziel ist nicht erreichbar: {error}", error=exc.strerror or exc))
    return ExternalTargetError(tr("Fehler beim Schreiben auf das externe Ziel: {error}", error=exc.strerror or exc))


def _ensure_dir(target_dir: Path) -> None:
    try:
        target_dir.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise _explain(exc) from exc


def check_target(target_dir: Path, beat: Callable[[], None] = lambda: None) -> dict:
    """Testdatei schreiben, zurücklesen, löschen. Gibt freien Platz zurück."""
    _ensure_dir(target_dir)
    probe = target_dir / f"{TEST_PREFIX}{os.getpid()}-{int(time.time())}"
    payload = b"zeitarchiv"
    try:
        probe.write_bytes(payload)
        beat()
        if probe.read_bytes() != payload:
            raise ExternalTargetError(tr("Die Testdatei kam verändert zurück."))
        probe.unlink()
    except OSError as exc:
        raise _explain(exc) from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    try:
        free, _total = _usage(target_dir)
    except OSError:
        free = None
    return {"free_bytes": free}


def cleanup_stale_parts(target_dir: Path, now: float | None = None) -> int:
    """Liegengebliebene .part-Dateien dieser App, älter als einen Tag."""
    now = time.time() if now is None else now
    removed = 0
    try:
        for part in target_dir.glob(PART_GLOB):
            try:
                if now - part.stat().st_mtime > STALE_PART_SECONDS:
                    part.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


def transfer(
    source: Path,
    target_dir: Path,
    beat: Callable[[], None] = lambda: None,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Kopiert das Backup unter .part-Namen und benennt es erst nach der
    Größenprüfung um — unter dem Endnamen liegt nie eine halbe Datei.
    Gibt die übertragene Größe zurück."""
    size = source.stat().st_size
    _ensure_dir(target_dir)
    try:
        free, _total = _usage(target_dir)
    except OSError:
        free = None
    if free is not None and free < size:
        raise ExternalTargetError(tr("Zu wenig Platz am Ziel."))
    final = target_dir / source.name
    part = target_dir / f".{source.name}.part"
    try:
        copied = 0
        with source.open("rb") as src, part.open("wb") as dst:
            while True:
                block = src.read(COPY_CHUNK_BYTES)
                if not block:
                    break
                dst.write(block)
                copied += len(block)
                beat()
                if on_progress is not None:
                    on_progress(copied, size)
            dst.flush()
            os.fsync(dst.fileno())
        if part.stat().st_size != size:
            raise ExternalTargetError(tr("Die Datei wurde unvollständig übertragen."))
        os.replace(part, final)
        stat = source.stat()
        try:
            os.utime(final, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass  # Reihenfolge der Aufbewahrung ist best effort
    except OSError as exc:
        raise _explain(exc) from exc
    finally:
        try:
            part.unlink()
        except OSError:
            pass
    return size


def prune_external(target_dir: Path, keep_count: int | None, keep_days: int | None, now: float) -> int:
    """Wie lokal, aber nur Dateien nach dem Backup-Muster — nie Fremdes."""
    try:
        return backup_mod.prune_backups(target_dir, keep_count, keep_days, now)
    except OSError as exc:
        raise _explain(exc) from exc

