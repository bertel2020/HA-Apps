"""Eigener CSV-Import: Zeitstempel/Wert-Import mit freier Spalten- und Format-
Zuordnung (Konzept "Offene Punkte") — anders als symcon_import.py (festes,
community-bekanntes Symcon-Format) für Daten aus beliebigen anderen Quellen,
bei denen Nutzer:in selbst festlegen muss, welche Spalte Zeitstempel/Wert ist
und in welchem Format/welcher Einheit der Zeitstempel steht. Teilt sich die
Monats-Klassifizierung und den Schreibvorgang mit dem Symcon-Import
(plan_import_rows()/import_rows() in symcon_import.py) — nur das Einlesen der
Rohdaten aus der Quelldatei unterscheidet sich.

Unklare/unlesbare Zeilen werden übersprungen statt die ganze Datei zu
verwerfen (dieselbe Haltung wie beim Symcon-Import)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..limits import MAX_IMPORT_ROWS_PER_ENTITY

TIMESTAMP_FORMATS = {
    "unix_s": "Unix-Zeitstempel (Sekunden)",
    "unix_ms": "Unix-Zeitstempel (Millisekunden)",
    "iso": "ISO 8601 (z. B. 2024-01-31 10:00:00)",
    "custom": "Eigenes Format …",
}

DELIMITERS = {",": "Komma (,)", ";": "Semikolon (;)", "\t": "Tab"}

_BOOL_VALUES = {"true": 1.0, "false": 0.0, "wahr": 1.0, "falsch": 0.0}


@dataclass
class CsvPreview:
    """Vorschau einer hochgeladenen CSV-Datei — reine Anzeige, um Spalten-
    Zuordnung/Trennzeichen/Kopfzeile vor dem eigentlichen Import zu prüfen."""

    columns: list[str] = field(default_factory=list)
    sample_rows: list[list[str]] = field(default_factory=list)
    column_count: int = 0
    total_lines: int = 0


def sniff_delimiter(path: Path) -> str:
    """Bestes Trennzeichen anhand der ersten Zeile schätzen — Komma als
    Rückfalloption, falls weder Semikolon noch Tab öfter vorkommen."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
    counts = {d: first_line.count(d) for d in DELIMITERS}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def sniff_has_header(path: Path, delimiter: str) -> bool:
    """Kopfzeile erkennen: erste Zeile enthält keine als Zahl parsbare Zelle,
    die zweite (falls vorhanden) schon — ein klassisches Erkennungsmerkmal für
    "erste Zeile ist Beschriftung, nicht Daten"."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        first = next(reader, None)
        second = next(reader, None)
    if not first:
        return False

    def _looks_numeric(row: list[str]) -> bool:
        return any(_parse_value(cell) is not None for cell in row)

    if _looks_numeric(first):
        return False
    return second is None or _looks_numeric(second)


def preview(path: Path, delimiter: str, has_header: bool, sample_size: int = 8) -> CsvPreview:
    """Liest die Datei streamend statt sie komplett als Liste zu materialisieren
    (siehe PERFORMANCE.md, ZP-013) — total_lines/column_count brauchen ohnehin
    einen vollständigen Durchlauf (jede Zeile trägt zu beidem bei), aber nur
    die ersten sample_size Datenzeilen müssen dabei im Speicher bleiben, nicht
    die komplette Datei."""
    header: list[str] | None = None
    sample_rows: list[list[str]] = []
    column_count = 0
    total_lines = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for i, row in enumerate(reader):
            column_count = max(column_count, len(row))
            if has_header and i == 0:
                header = row
                continue
            total_lines += 1
            if len(sample_rows) < sample_size:
                sample_rows.append(row)

    result = CsvPreview()
    if column_count == 0:
        return result
    if has_header:
        result.columns = (header or []) + [
            f"Spalte {i + 1}" for i in range(len(header or []), column_count)
        ]
    else:
        result.columns = [f"Spalte {i + 1}" for i in range(column_count)]
    result.column_count = column_count
    result.total_lines = total_lines
    result.sample_rows = sample_rows
    return result


def _parse_value(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    # Deutsches Dezimalformat ("21,5") — nur versuchen, wenn genau ein Komma
    # und kein Punkt vorkommt, sonst wäre ein Tausendertrennzeichen ("1,234.5")
    # nicht von einem Dezimalkomma zu unterscheiden.
    if raw.count(",") == 1 and "." not in raw:
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            pass
    return _BOOL_VALUES.get(raw.lower())


def _parse_timestamp(raw: str, ts_format: str, custom_pattern: str, tz: ZoneInfo) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    if ts_format == "unix_s":
        try:
            return float(raw)
        except ValueError:
            return None
    if ts_format == "unix_ms":
        try:
            return float(raw) / 1000.0
        except ValueError:
            return None
    if ts_format == "iso":
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        try:
            dt = datetime.strptime(raw, custom_pattern)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


@dataclass
class ParseResult:
    rows: list[tuple[float, float]] = field(default_factory=list)
    skipped: int = 0


def parse_rows(
    path: Path,
    delimiter: str,
    has_header: bool,
    ts_col: int,
    value_col: int,
    ts_format: str,
    custom_pattern: str,
    tz: ZoneInfo,
    max_rows: int = MAX_IMPORT_ROWS_PER_ENTITY,
) -> ParseResult:
    """Liest die Rohdaten mit der gewählten Spalten-/Format-Zuordnung ein.
    Jede Zeile, die sich nicht plausibel als (Zeitstempel, Wert) lesen lässt
    (zu wenige Spalten, kein gültiges Zeitstempel-/Wert-Format), zählt als
    übersprungen statt die ganze Datei zu verwerfen — sichtbar im Dry Run."""
    result = ParseResult()
    max_col = max(ts_col, value_col)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        if has_header:
            next(reader, None)
        for line in reader:
            if len(line) <= max_col:
                result.skipped += 1
                continue
            ts = _parse_timestamp(line[ts_col], ts_format, custom_pattern, tz)
            value = _parse_value(line[value_col])
            if ts is None or value is None:
                result.skipped += 1
                continue
            result.rows.append((ts, value))
            if len(result.rows) > max_rows:
                raise ValueError(
                    f"CSV enthält mehr als {max_rows:,} gültige Datenzeilen".replace(",", ".")
                )
    result.rows.sort()
    return result
