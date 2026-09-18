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

from .hotbuffer import HotRecord, read_full_rows, write_records


def bucket_end(interval_seconds: int, ts: float) -> float:
    """Rundet ts auf das Ende seines Zeitfensters auf. Ein Rohwert exakt auf
    einer Rastergrenze gehört zum SCHLIESSENDEN Fenster, nicht zum neu
    beginnenden (Bucket-Grenzen sind (Ende-Intervall, Ende])."""
    return math.ceil(ts / interval_seconds) * interval_seconds


def _pending_run(records: list[HotRecord]) -> list[HotRecord]:
    """Die trailing Rohzeilen, die noch keinem Fenster zugeschlagen wurden
    (min_value ist None) — bei normalem Betrieb genau die Zeilen des aktuell
    offenen Fensters, weil jedes Fenster beim Überschreiten der nächsten
    Grenze sofort aufgelöst wird."""
    pending: list[HotRecord] = []
    for record in reversed(records):
        if record[3] is not None or record[4] is not None:
            break
        pending.append(record)
    pending.reverse()
    return pending


def pending_bucket_end(path: Path, interval_seconds: int) -> float | None:
    """Bucket-Ende des aktuell offenen Fensters, oder None, wenn es keine
    unaufgelösten Rohzeilen gibt (frische Datei oder alles schon aufgelöst)."""
    records = read_full_rows(path)
    pending = _pending_run(records)
    if not pending:
        return None
    return bucket_end(interval_seconds, pending[-1][0])


def collapse_pending_window(path: Path, interval_seconds: int) -> None:
    """Fasst die trailing unaufgelösten Rohzeilen zu einer Ø/Min/Max-Zeile
    zusammen und schreibt die Datei neu. Kein Effekt, wenn es nichts
    Unaufgelöstes gibt (z. B. wenn der Wartungslauf zweimal über dieselbe
    Datei läuft)."""
    records = read_full_rows(path)
    pending = _pending_run(records)
    if not pending:
        return
    resolved_count = len(records) - len(pending)
    values = [record[1] for record in pending]
    end_ts = bucket_end(interval_seconds, pending[-1][0])
    avg_value = sum(values) / len(values)
    collapsed: HotRecord = (end_ts, avg_value, None, min(values), max(values))

    write_records(path, records[:resolved_count] + [collapsed])
