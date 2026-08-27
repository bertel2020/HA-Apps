"""Der laufende Monat je Entität: unkomprimiertes CSV, append-only.

Absichtlich unkomprimiert (Konzept Abschnitt 02) — Parquet lässt sich nicht
beliebig fortlaufend anhängen, und ein Absturz mitten im Schreiben macht ein
CSV nicht unlesbar, eine Parquet-Datei ohne Footer dagegen schon.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from collections.abc import Iterator
from zoneinfo import ZoneInfo

from .paths import hot_file_path, storage_area_dir, validate_entity_id

HotRecord = tuple[float, float, str | None]


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


def append(
    data_dir: Path,
    entity_id: str,
    ts: float,
    value: float,
    tz: ZoneInfo,
    event_id: str | None = None,
) -> None:
    path = hot_path(data_dir, entity_id, ts, tz)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        suffix = f",{event_id}" if event_id else ""
        handle.write(f"{ts},{value}{suffix}\n")


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


def read_rows(path: Path) -> list[tuple[float, float]]:
    """Liest eine Hot-CSV-Datei als (ts, value)-Paare — leer, falls die Datei fehlt."""
    return [(ts, value) for ts, value, _event_id in iter_records(path)]


def iter_records(path: Path) -> Iterator[HotRecord]:
    """Streamt alte Zwei-Spalten- und neue Drei-Spalten-Hot-Dateien.

    Die optionale dritte Spalte trägt die stabile Event-ID des Live-
    Schreibpfads. Importierte/ältere Zeilen haben bewusst keine ID.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 2)
            if len(parts) < 2:
                continue
            event_id = (parts[2] or None) if len(parts) == 3 else None
            yield (float(parts[0]), float(parts[1]), event_id)


def read_records(path: Path) -> list[HotRecord]:
    """Materialisiert ``iter_records()`` für Aufrufer, die alle Zeilen brauchen."""
    return list(iter_records(path))


def contains_event_id(path: Path, event_id: str) -> bool:
    """Bricht die Suche ab, sobald die Event-ID gefunden wurde."""
    return any(row_event_id == event_id for _ts, _value, row_event_id in iter_records(path))


def contains_timestamp(path: Path, ts: float) -> bool:
    """Bricht die Suche ab, sobald der Zeitstempel gefunden wurde."""
    return any(row_ts == ts for row_ts, _value, _event_id in iter_records(path))


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
