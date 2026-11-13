"""Fehlertext für die Oberfläche: ohne Dateipfade des Servers.

Ausnahmen tragen oft den vollständigen Pfad („[Errno 28] No space left on device: '/data/archive/…/2024-07.parquet'“).
Die Oberfläche (Aufträge, Bereinigung, Index-Optimierung) zeigt davon nur die Ursache und den Dateinamen; der volle
Text steht mit Stacktrace im Protokoll, wo ihn die Diagnose findet."""

from __future__ import annotations

import re

#: Ein absoluter Pfad mit mindestens einem Ordner: „/data/archive/x/2024-07.parquet“. Nicht mitten in einem Wort, in
#: einer Adresse („http://host/x“) oder in einem Bruch („1/2“).
_PATH = re.compile(r"(?<![\w.:/])/(?:[^\s/'\"<>|:]+/)+(?=[^\s/'\"<>|:]+)")


def user_message(error: BaseException, limit: int = 2000) -> str:
    """Der Text einer Ausnahme für die Anzeige: Pfade auf den Dateinamen gekürzt, nie leer (dann der Klassenname)."""
    text = str(error)
    if isinstance(error, OSError) and error.strerror:
        text = error.strerror if error.filename is None else f"{error.strerror}: {_PATH.sub('', str(error.filename))}"
    return _PATH.sub("…/", text)[:limit] or error.__class__.__name__
