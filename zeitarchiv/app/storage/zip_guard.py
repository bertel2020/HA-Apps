"""Eintragszahl eines ZIP-Archivs feststellen, ohne es zu öffnen.

`zipfile.ZipFile(path)` liest im Konstruktor das komplette Zentralverzeichnis
ein und baut daraus für jeden Eintrag ein `ZipInfo`-Objekt. Gemessen kostet das
**619 Byte je Eintrag** — und zwar bevor der Aufrufer irgendetwas prüfen kann.
Ein bis an `MAX_ZIP_UPLOAD_BYTES` (2 GiB) gefülltes Archiv aus möglichst
kleinen Einträgen (gemessen 87,6 Byte je Stück, also rund 24,5 Millionen)
bräuchte damit **14,2 GiB allein für die Liste**, die eine Obergrenze
anschließend zählen soll. Die Prüfung kam also immer zu spät, egal wie
niedrig die Grenze steht (ZG-26 in CODE_ANALYSE.md).

Die Zahl steht aber schon im Abschlusssatz des Zentralverzeichnisses am
Dateiende. Sie von dort zu lesen kostet nachgemessen 0,09 ms und keinen
messbaren Speicher, gegenüber 3,2 ms und einer wachsenden Liste beim Öffnen.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from ..formatting import format_int

logger = logging.getLogger(__name__)


def declared_entry_count(path: Path) -> int | None:
    """Eintragszahl aus dem Abschlusssatz, oder None wenn sie nicht lesbar ist.

    Nutzt `zipfile._EndRecData()`. Das ist eine private Funktion der
    Standardbibliothek — bewusst, denn die Alternative wäre, den Abschlusssatz
    selbst zu zerlegen, einschließlich Zip64-Sonderfall und
    Archivkommentar-Suche. Ein Fehler darin würde gültige Backups ablehnen,
    und das wäre schlimmer als das Problem, das hier gelöst wird.

    Abgesichert ist das in zwei Richtungen: Verschwindet die Funktion in einer
    künftigen Python-Version, liefert diese hier `None` und der Aufrufer fällt
    auf das bisherige Verhalten zurück (öffnen, dann zählen) statt zu brechen.
    Und `test_zip_guard.py` prüft den schnellen Weg gegen ein echtes Archiv,
    sodass ein Interpreter-Wechsel den Verlust als roten Test meldet statt als
    stille Rückkehr zum alten Zustand.
    """
    try:
        with path.open("rb") as handle:
            end_record = zipfile._EndRecData(handle)  # noqa: SLF001
        if end_record is None:
            return None
        count = end_record[zipfile._ECD_ENTRIES_TOTAL]  # noqa: SLF001
    except (AttributeError, IndexError, OSError, TypeError, ValueError):
        # AttributeError/IndexError: die private Schnittstelle hat sich
        # geändert. OSError/ValueError: die Datei ist kaputt oder gar kein ZIP
        # — dann meldet das der Aufrufer beim Öffnen ohnehin sauber.
        logger.debug("Eintragszahl von %s nicht vorab lesbar", path.name, exc_info=True)
        return None
    return count if isinstance(count, int) and count >= 0 else None


def ensure_entry_count_allowed(path: Path, limit: int, message: str) -> None:
    """Lehnt ein Archiv ab, bevor sein Verzeichnis im Speicher landet.

    Lässt sich die Zahl nicht vorab bestimmen, passiert hier nichts — die
    bestehende Prüfung nach dem Öffnen bleibt in beiden Aufrufern als Auffangnetz
    stehen.
    """
    count = declared_entry_count(path)
    if count is not None and count > limit:
        raise ValueError(
            f"{message} (enthält {format_int(count)} Einträge, "
            f"erlaubt sind {format_int(limit)})"
        )
