"""Dateien so ersetzen, dass nach einem Absturz entweder der alte oder der neue Stand vorliegt.

Zielpfade nie direkt öffnen: Eine auf 0 gekürzte oder halb geschriebene Datei
ist nach Stromausfall, OOM-Kill oder SIGKILL für den ganzen Monat verloren.
Stattdessen neben dem Ziel eine ``.tmp``-Datei schreiben, auf die Platte
zwingen und erst dann umbenennen.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path


def temp_path(path: Path) -> Path:
    """Namensvetter neben dem Ziel, aus dem nie eine ``*.csv``/``*.parquet`` wird (Globs der Speicherschicht)."""
    return path.with_name(path.name + ".tmp")


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # Verzeichnis-fsync gibt es nicht überall (z. B. unter Windows); das Umbenennen gilt trotzdem.
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def replace_durably(temporary: Path, destination: Path) -> None:
    """Zwingt `temporary` auf die Platte und benennt es dann atomar in `destination` um."""
    _fsync_file(temporary)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def write_file_atomically(path: Path, write: Callable[[Path], None]) -> None:
    """Lässt `write` in eine Temp-Datei neben `path` schreiben und ersetzt `path` danach atomar.

    Wirft `write` oder das Umbenennen, bleibt `path` unverändert und die Temp-Datei wird entfernt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temp_path(path)
    try:
        write(temporary)
        replace_durably(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_text_atomically(path: Path, lines: Iterable[str]) -> None:
    """Schreibt `lines` (je mit abschließendem Zeilenumbruch) als UTF-8-Datei atomar nach `path`."""

    def write(temporary: Path) -> None:
        with temporary.open("w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line)

    write_file_atomically(path, write)
