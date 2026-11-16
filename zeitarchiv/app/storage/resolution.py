"""Live-Auflösung für Standard-Entitäten: Zeitfenster im Hot Buffer zu
Ø/Min/Max zusammenfassen, sobald das nächste Fenster beginnt.

Zähler und Switch brauchen das nicht — Zähler drosseln über
should_accept_write() (index.py, reines Zeitraster, kein Aggregieren),
Switch ist von der Auflösung ganz ausgenommen (siehe main.py-Konfigvalidierung).

Rohwerte werden immer normal in den Hot Buffer geschrieben, nie im RAM
gepuffert — ein Neustart mitten im Fenster verliert dadurch nichts, das
Zusammenfassen wird beim nächsten Schreibvorgang oder vom täglichen
Wartungslauf einfach nachgeholt (siehe background.py).

Zeitstempel der zusammengefassten Zeile ist das BUCKET-ENDE
(ceil(ts/interval)*interval), nicht der Bucket-Anfang: eine Zeile mit
ts=10:05:00 würde sonst behaupten, der Durchschnitt sei schon um 10:05:00
bekannt gewesen, obwohl die zugrundeliegenden Rohwerte erst bis 10:09:59
eintrafen. Bucket-Ende ist der einzige Zeitpunkt, zu dem das Fenster
tatsächlich vollständig bekannt ist.
"""

from __future__ import annotations

import math
from pathlib import Path

from .atomic import write_file_atomically
from .hotbuffer import HotRecord, _parse_line, format_record, iter_records, read_tail_after

_COPY_BLOCK = 1024 * 1024


def bucket_end(interval_seconds: int, ts: float) -> float:
    """Rundet ts auf das Ende seines Zeitfensters auf. Ein Rohwert exakt auf
    einer Rastergrenze gehört zum SCHLIESSENDEN Fenster, nicht zum neu
    beginnenden (Bucket-Grenzen sind (Ende-Intervall, Ende])."""
    return math.ceil(ts / interval_seconds) * interval_seconds


def _is_resolved(record: HotRecord) -> bool:
    return record[3] is not None or record[4] is not None


def _pending_tail(path: Path) -> tuple[int, list[HotRecord]]:
    """(Byte-Offset, trailing Rohzeilen, die noch keinem Fenster zugeschlagen wurden (min_value ist None)).

    Bei normalem Betrieb sind das genau die Zeilen des aktuell offenen Fensters, weil jedes Fenster beim
    Überschreiten der nächsten Grenze sofort aufgelöst wird. Gelesen wird nur das Ende der Datei."""
    return read_tail_after(path, _is_resolved)


def pending_bucket_end(path: Path, interval_seconds: int) -> float | None:
    """Bucket-Ende des aktuell offenen Fensters, oder None, wenn es keine
    unaufgelösten Rohzeilen gibt (frische Datei oder alles schon aufgelöst).

    Das offene Fenster ist das späteste unter den offenen Zeilen, nicht das der zuletzt angehängten: Eine
    nachträglich eingefügte oder verspätete Zeile liegt am Dateiende, gehört aber in ein früheres Fenster."""
    _offset, pending = _pending_tail(path)
    if not pending:
        return None
    return max(bucket_end(interval_seconds, record[0]) for record in pending)


def _resolved_timestamps(path: Path) -> set[float]:
    return {record[0] for record in iter_records(path) if _is_resolved(record)}


def _last_resolved_timestamp(path: Path, offset: int) -> float | None:
    """Zeitstempel der letzten aufgelösten Zeile vor ``offset`` (dem Beginn der offenen Rohzeilen).

    Gelesen wird nur ein Block vor dem Offset, nicht die ganze Datei: Die Prüfung läuft bei jedem Schließen
    eines Fensters, bei 1-min-Auflösung also jede Minute und Entität."""
    if offset <= 0:
        return None
    block = 4096
    with path.open("rb") as handle:
        while True:
            start = max(0, offset - block)
            handle.seek(start)
            data = handle.read(offset - start)
            lines = data.split(b"\n")
            if start > 0:
                lines = lines[1:]  # die erste Zeile im Block ist vermutlich angeschnitten
            for raw in reversed(lines):
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    record = _parse_line(text)
                except ValueError:
                    continue
                if record is not None and _is_resolved(record):
                    return record[0]
            if start == 0:
                return None
            block *= 2


def _replace_tail(path: Path, offset: int, tail: list[HotRecord]) -> None:
    """Ersetzt alles ab ``offset`` durch ``tail`` (atomar). Der Anfang der Datei wird als Bytes kopiert, nicht
    neu geparst und formatiert: Das Neuschreiben der ganzen Datei dauerte bei 89 000 Zeilen 145 ms, das
    Kopieren der Bytes bleibt unter einer Millisekunde je MB."""

    def write(temporary: Path) -> None:
        with path.open("rb") as source, temporary.open("wb") as destination:
            remaining, last = offset, b""
            while remaining > 0:
                chunk = source.read(min(_COPY_BLOCK, remaining))
                if not chunk:
                    break
                destination.write(chunk)
                remaining -= len(chunk)
                last = chunk[-1:]
            if last and last != b"\n":
                destination.write(b"\n")  # die letzte Zeile des Anfangs war nicht abgeschlossen
            for record in tail:
                destination.write((format_record(record) + "\n").encode("utf-8"))

    write_file_atomically(path, write)


def collapse_pending_window(path: Path, interval_seconds: int) -> int:
    """Fasst die trailing unaufgelösten Rohzeilen je Zeitfenster zu einer Ø/Min/Max-Zeile
    zusammen und schreibt die Datei neu. Kein Effekt, wenn es nichts
    Unaufgelöstes gibt (z. B. wenn der Wartungslauf zweimal über dieselbe
    Datei läuft). Liefert, um wie viele Zeilen die Datei geschrumpft ist —
    der Aufrufer zieht damit den Zeilenzähler im Index nach.

    Normalerweise ist es ein Fenster. Liegen darunter Zeilen aus früheren Fenstern (ein nachträglich
    eingefügter Wert, ein verspätetes Ereignis, ein nie aufgelöstes Fenster nach einem Absturz), bekommt jedes
    Fenster seine eigene Zeile bei seinem Bucket-Ende. Früher wurden alle zu EINER Zeile am Ende der letzten
    Zeile zusammengeworfen: Das Mittel des aktuellen Fensters landete dann beim früheren, und der eingefügte
    Wert verschwand als eigener Messpunkt. Hat ein früheres Fenster schon eine aufgelöste Zeile mit demselben
    Zeitstempel, bleiben die nachgetragenen Rohzeilen als eigene Messpunkte stehen (wie ein von Hand in einen
    archivierten Monat eingefügter Wert), statt einen zweiten Datensatz mit gleichem Zeitstempel zu erzeugen."""
    offset, pending = _pending_tail(path)
    if not pending:
        return 0
    groups: dict[float, list[HotRecord]] = {}
    for record in pending:
        groups.setdefault(bucket_end(interval_seconds, record[0]), []).append(record)
    # Mehrere Fenster: immer prüfen. Nur ein Fenster: ein verspäteter Wert kann in eine Minute fallen, die schon
    # eine aufgelöste Zeile hat (Wartungslauf oder Absturz hat sie geschlossen, bevor der Wert eintraf). Dann
    # entstünde eine zweite Zeile mit gleichem Zeitstempel. Aufgelöste Zeilen stehen aufsteigend, deshalb ist
    # die Suche nur nötig, wenn das Fenster nicht hinter der letzten aufgelösten Zeile liegt (der Normalfall).
    if len(groups) > 1:
        taken = _resolved_timestamps(path)
    else:
        last_resolved = _last_resolved_timestamp(path, offset)
        taken = _resolved_timestamps(path) if last_resolved is not None and min(groups) <= last_resolved else set()
    tail: list[HotRecord] = []
    for end_ts in sorted(groups):
        rows = groups[end_ts]
        if end_ts in taken:
            tail.extend(rows)
            continue
        values = [record[1] for record in rows]
        tail.append((end_ts, sum(values) / len(values), None, min(values), max(values)))
    removed = len(pending) - len(tail)
    if removed == 0 and tail == pending:
        return 0
    _replace_tail(path, offset, tail)
    return removed
