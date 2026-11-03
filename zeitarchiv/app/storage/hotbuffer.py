"""Der laufende Monat je Entität: unkomprimiertes CSV, append-only.

Absichtlich unkomprimiert (Konzept Abschnitt 02) — Parquet lässt sich nicht
beliebig fortlaufend anhängen, und ein Absturz mitten im Schreiben macht ein
CSV nicht unlesbar, eine Parquet-Datei ohne Footer dagegen schon.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Iterator
from zoneinfo import ZoneInfo

from .atomic import write_text_atomically
from .paths import hot_file_path, storage_area_dir, validate_entity_id

logger = logging.getLogger(__name__)

# (ts, value, event_id, min_value, max_value). min_value/max_value sind nur
# bei Zeilen gesetzt, die die Standard-Auflösung als Ø mehrerer Rohwerte
# eines Zeitfensters geschrieben hat (siehe resolution.py) — bei jeder
# anderen Zeile None. Alte Zeilen ohne die beiden Spalten werden beim Lesen
# genauso mit None aufgefüllt.
HotRecord = tuple[float, float, str | None, float | None, float | None]


def month_key(ts: float, tz: ZoneInfo) -> str:
    # Kalendermonat in der konfigurierten Zeitzone, NICHT UTC (Konzept: alle
    # Kalendergrenzen der App sind Europe/Berlin-korrekt, siehe query.py/
    # rollup.py/retention.py) — ein früherer Bug hier nutzte time.gmtime()
    # (immer UTC), was Schreib- und Staleness-Pfad zwar konsistent hielt
    # (beide UTC), aber Werte nahe Mitternacht am Monatsersten/-letzten in den
    # falschen Kalendermonat einsortierte.
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m")


def hot_path(data_dir: Path, entity_id: str, ts: float, tz: ZoneInfo) -> Path:
    return hot_file_path(data_dir, entity_id, month_key(ts, tz))


def _format_row(
    ts: float,
    value: float,
    event_id: str | None,
    min_value: float | None,
    max_value: float | None,
) -> str:
    # min_value/max_value nur anhängen, wenn mindestens eines gesetzt ist —
    # hält die weit überwiegende Mehrheit der Zeilen (Rohwerte ohne
    # Auflösung) im bisherigen, kürzeren 2/3-Spalten-Format.
    if min_value is None and max_value is None:
        suffix = f",{event_id}" if event_id else ""
        return f"{ts},{value}{suffix}"
    min_part = "" if min_value is None else min_value
    max_part = "" if max_value is None else max_value
    return f"{ts},{value},{event_id or ''},{min_part},{max_part}"


def format_record(record: HotRecord) -> str:
    """Eine Zeile der Hot-Datei (ohne Zeilenende) für einen Datensatz."""
    return _format_row(*record)


def append(
    data_dir: Path,
    entity_id: str,
    ts: float,
    value: float,
    tz: ZoneInfo,
    event_id: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
) -> None:
    path = hot_path(data_dir, entity_id, ts, tz)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_format_row(ts, value, event_id, min_value, max_value) + "\n")


def append_many(data_dir: Path, entity_id: str, rows: list[tuple[float, float]], tz: ZoneInfo) -> None:
    """Wie append(), aber öffnet die Datei nur einmal für mehrere Zeilen — für
    den Symcon-Import (Konzept Abschnitt 04), der beim Zusammenführen des
    laufenden Monats leicht tausende Zeilen auf einmal anhängt; append() dafür
    einzeln aufzurufen wäre ein Datei-Open pro Zeile. Alle Zeilen müssen zum
    selben Kalendermonat gehören (Aufrufer gruppiert vorher entsprechend)."""
    if not rows:
        return
    path = hot_path(data_dir, entity_id, rows[0][0], tz)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for ts, value in rows:
            handle.write(f"{ts},{value}\n")


def append_records(
    data_dir: Path, entity_id: str, records: list[HotRecord], tz: ZoneInfo
) -> None:
    """Bulk-Variante von append() inklusive optionaler Event-ID.

    Wird insbesondere benötigt, wenn eine irrtümliche Archivdatei des
    laufenden Monats zurück in den Hot Buffer geführt wird: bereits
    archivierte Live-Ereignisse dürfen dabei ihre Idempotenz-ID nicht
    verlieren.
    """
    if not records:
        return
    path = hot_path(data_dir, entity_id, records[0][0], tz)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for ts, value, event_id, min_value, max_value in records:
            handle.write(_format_row(ts, value, event_id, min_value, max_value) + "\n")


def read_rows(path: Path) -> list[tuple[float, float]]:
    """Liest eine Hot-CSV-Datei als (ts, value)-Paare — leer, falls die Datei fehlt.
    Ignoriert min_value/max_value bewusst — für Aufrufer, die nur den
    gespeicherten Wert brauchen (Korrektur-Matching, Bereinigungs-Anzeige)."""
    return [(ts, value) for ts, value, _event_id, _min_value, _max_value in iter_records(path)]


def read_full_rows(path: Path) -> list[HotRecord]:
    """Wie read_records(), als expliziter Name für Aufrufer, die min_value/
    max_value beim Neuschreiben einer Datei erhalten müssen (Archivierung,
    Korrektur/Hinzufügen bereits archivierter Monate) — sonst würden dort
    unbemerkt die Ø/Min/Max-Spalten der Standard-Auflösung verloren gehen."""
    return read_records(path)


def _parse_line(line: str) -> HotRecord | None:
    """Eine bereinigte, nicht leere Zeile als Datensatz; None bei zu wenigen Spalten, ValueError bei kaputten Zahlen."""
    parts = line.split(",", 4)
    if len(parts) < 2:
        return None
    event_id = (parts[2] or None) if len(parts) >= 3 else None
    min_value = float(parts[3]) if len(parts) >= 5 and parts[3] != "" else None
    max_value = float(parts[4]) if len(parts) >= 5 and parts[4] != "" else None
    return (float(parts[0]), float(parts[1]), event_id, min_value, max_value)


def iter_records(path: Path, corrupt_lines: list[str] | None = None) -> Iterator[HotRecord]:
    """Streamt Hot-Dateien in allen bisherigen Formaten: zwei Spalten (alt),
    drei Spalten (ts,value,event_id) oder fünf Spalten
    (ts,value,event_id,min_value,max_value — Standard-Auflösung). Fehlende
    Spalten werden als None aufgefüllt.

    ``corrupt_lines``: optionale Sammelliste, an die übersprungene kaputte
    Zeilen (siehe unten) zusätzlich zum Warn-Log angehängt werden — für
    Aufrufer, die eine gefundene Beschädigung sichtbar machen wollen (z. B.
    der Speicherindex-Abgleich, siehe reconcile.py), statt sie nur im Log
    verschwinden zu lassen. Die meisten Aufrufer brauchen das nicht und
    lassen den Parameter weg.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = _parse_line(line)
            except ValueError:
                # Nach einem unsauberen Absturz kann das Dateisystem den Rest einer
                # noch nicht geflushten Zeile als Nullbytes zurückliefern (ext4 u. a.
                # nach Stromausfall/hartem Kill) — eine einzelne so kaputte Zeile darf
                # weder die Abfrage noch den Wartungsplaner noch den Crash-Reconciliation-
                # Lauf für die ganze restliche Datei/Installation lahmlegen. Übersprungen
                # statt repariert: der ursprüngliche Wert ist unwiederbringlich verloren.
                logger.warning(
                    "Kaputte Hot-Buffer-Zeile übersprungen · event=hotbuffer_corrupt_line "
                    "path=%s snippet=%r",
                    path, line[:80],
                )
                if corrupt_lines is not None:
                    corrupt_lines.append(line[:80])
                continue
            if record is not None:
                yield record


def read_records(path: Path) -> list[HotRecord]:
    """Materialisiert ``iter_records()`` für Aufrufer, die alle Zeilen brauchen."""
    return list(iter_records(path))


_TAIL_BLOCK = 64 * 1024


def read_tail_after(path: Path, is_boundary: Callable[[HotRecord], bool]) -> tuple[int, list[HotRecord]]:
    """Liest nur das Ende einer Hot-Datei: alle Datensätze NACH dem letzten, für den ``is_boundary`` zutrifft.

    Rückgabe: (Byte-Offset, an dem diese Zeilen beginnen, die Datensätze). Gibt es keinen solchen Datensatz,
    ist es die ganze Datei (Offset 0). Gelesen wird von hinten in wachsenden Blöcken statt die ganze Datei zu
    parsen: Die Live-Auflösung fragt bei jedem Messwert nach den noch offenen Zeilen, und eine Monatsdatei hat
    bei 30 s Auflösung am Monatsende rund 89 000 davon (gemessen: 50 ms je Messwert für das Parsen, unter der
    Entitätssperre). Kaputte Zeilen werden wie in iter_records() übersprungen."""
    if not path.exists():
        return 0, []
    size = path.stat().st_size
    if size == 0:
        return 0, []
    block = _TAIL_BLOCK
    with path.open("rb") as handle:
        while True:
            window = min(block, size)
            handle.seek(size - window)
            data = handle.read(window)
            base = size - window
            if window < size:
                # Die erste Zeile im Fenster ist vermutlich angeschnitten: erst ab der nächsten rechnen.
                cut = data.find(b"\n")
                if cut == -1:
                    block *= 2
                    continue
                data, base = data[cut + 1:], base + cut + 1
            lines: list[tuple[int, str]] = []
            position = base
            for raw in data.split(b"\n"):
                lines.append((position, raw.decode("utf-8", errors="replace")))
                position += len(raw) + 1
            for index in range(len(lines) - 1, -1, -1):
                text = lines[index][1].strip()
                if not text:
                    continue
                try:
                    record = _parse_line(text)
                except ValueError:
                    continue
                if record is not None and is_boundary(record):
                    start = lines[index + 1][0] if index + 1 < len(lines) else size
                    return start, _records_of(lines[index + 1:], path)
            if window >= size:
                return 0, _records_of(lines, path)
            block *= 2


def _records_of(lines: list[tuple[int, str]], path: Path) -> list[HotRecord]:
    records: list[HotRecord] = []
    for _position, raw in lines:
        text = raw.strip()
        if not text:
            continue
        try:
            record = _parse_line(text)
        except ValueError:
            logger.warning(
                "Kaputte Hot-Buffer-Zeile übersprungen · event=hotbuffer_corrupt_line path=%s snippet=%r",
                path, text[:80],
            )
            continue
        if record is not None:
            records.append(record)
    return records


def write_records(path: Path, records: list[HotRecord]) -> None:
    """Schreibt eine Hot-Datei komplett neu aus `records` — für die
    Live-Auflösung (resolution.py), die die trailing Rohzeilen eines
    abgeschlossenen Fensters durch eine Ø/Min/Max-Zeile ersetzt."""
    write_text_atomically(
        path,
        (
            _format_row(ts, value, event_id, min_value, max_value) + "\n"
            for ts, value, event_id, min_value, max_value in records
        ),
    )


def contains_event_id(path: Path, event_id: str) -> bool:
    """Bricht die Suche ab, sobald die Event-ID gefunden wurde."""
    return any(
        row_event_id == event_id
        for _ts, _value, row_event_id, _min_value, _max_value in iter_records(path)
    )


def contains_timestamp(path: Path, ts: float) -> bool:
    """Bricht die Suche ab, sobald der Zeitstempel gefunden wurde."""
    return any(
        row_ts == ts for row_ts, _value, _event_id, _min_value, _max_value in iter_records(path)
    )


def find_stale_hot_files(data_dir: Path, entity_id: str, current_ts: float, tz: ZoneInfo) -> list[Path]:
    """Findet Hot-Dateien der Entität, die zu einem früheren Monat gehören als current_ts."""
    validate_entity_id(entity_id)
    hot_dir = storage_area_dir(data_dir, "hot")
    if not hot_dir.exists():
        return []
    current_month = month_key(current_ts, tz)
    prefix = f"{entity_id}-"
    stale = []
    for path in hot_dir.glob(f"{prefix}*.csv"):
        file_month = path.stem[len(prefix) :]
        if file_month < current_month:
            stale.append(path)
    return stale


def find_entities_with_stale_hot_files(
    data_dir: Path, entity_ids: set[str], current_ts: float, tz: ZoneInfo
) -> set[str]:
    """Wie find_stale_hot_files(), aber für ALLE Entitäten auf einmal, mit
    EINEM Verzeichnis-Listing statt eines glob() je Entität. Für die
    Einstellungen-Seite (Rotation-Zähler) rief find_stale_hot_files() bisher
    pro Entität separat glob(f"{entity_id}-*.csv") auf — jeder dieser Aufrufe
    durchsucht erneut das komplette hot_dir, macht die Gesamtkosten also
    proportional zu Entitätenzahl MAL Dateizahl im hot_dir statt nur zur
    Dateizahl. Bei vielen Entitäten (jede mit eigener Hot-Datei) summierte
    sich das zu einer spürbaren Verzögerung beim Laden von /settings, die bei
    jedem Aufruf neu anfiel (kein Zwischenspeicher). Dateiname ist immer
    "{entity_id}-{YYYY-MM}.csv" — der Monats-Suffix hat fest 7 Zeichen, daher
    robust von HINTEN abgeschnitten statt nach einem Trennzeichen zu suchen
    (ein entity_id mit Bindestrich würde sonst falsch aufgeteilt)."""
    hot_dir = storage_area_dir(data_dir, "hot")
    if not hot_dir.exists():
        return set()
    current_month = month_key(current_ts, tz)
    stale_entities: set[str] = set()
    for path in hot_dir.glob("*.csv"):
        stem = path.stem
        if len(stem) < 9 or stem[-8] != "-":
            continue
        entity_id, file_month = stem[:-8], stem[-7:]
        if file_month < current_month and entity_id in entity_ids:
            stale_entities.add(entity_id)
    return stale_entities
