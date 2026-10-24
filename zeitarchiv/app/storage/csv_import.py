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

from ..i18n import N_, tr

import csv
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..limits import MAX_IMPORT_ROWS_PER_ENTITY

TIMESTAMP_FORMATS = {
    "unix_s": N_("Unix-Zeitstempel (Sekunden)"),
    "unix_ms": N_("Unix-Zeitstempel (Millisekunden)"),
    "iso": N_("ISO 8601 (z. B. 2024-01-31 10:00:00)"),
    "de_date": N_("TT.MM.JJJJ (z. B. 15.01.2026)"),
    "de_datetime": N_("TT.MM.JJJJ hh:mm (z. B. 15.01.2026 14:30)"),
    "de_datetime_s": N_("TT.MM.JJJJ hh:mm:ss (z. B. 15.01.2026 14:30:05)"),
    "custom": N_("Eigenes Format …"),
}

#: Fertige Formate als strptime-Muster; alles andere in TIMESTAMP_FORMATS wird
#: gesondert gelesen (Unix, ISO) oder kommt vom Nutzer ("custom").
PRESET_PATTERNS = {
    "de_date": "%d.%m.%Y",
    "de_datetime": "%d.%m.%Y %H:%M",
    "de_datetime_s": "%d.%m.%Y %H:%M:%S",
}

DELIMITERS = {",": N_("Komma (,)"), ";": N_("Semikolon (;)"), "\t": N_("Tab")}

_THOUSANDS_DE = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")
_THOUSANDS_EN = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")

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
    """None heißt „nicht als Messwert lesbar" und zählt als übersprungene
    Zeile — sichtbar im Dry Run, statt still etwas Falsches zu importieren.

    Dazu gehört auch alles Nicht-Endliche: float() nimmt die Zeichenketten
    "nan", "inf" und "Infinity" klaglos an, und "1e400" läuft ohne Ausnahme
    nach inf über. Im Archiv wäre so ein Wert kein Messwert, sondern ein
    dauerhafter HTTP 500 auf jede Abfrage seines Zeitraums (ZG-24) — dieselbe
    Prüfung macht ha_import._parse_state() beim Home-Assistant-Import längst.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = float(raw)
        return value if math.isfinite(value) else None
    except ValueError:
        pass
    # Deutsches Dezimalformat ("21,5") — nur versuchen, wenn genau ein Komma
    # und kein Punkt vorkommt, sonst wäre ein Tausendertrennzeichen ("1,234.5")
    # nicht von einem Dezimalkomma zu unterscheiden.
    if raw.count(",") == 1 and "." not in raw:
        try:
            value = float(raw.replace(",", "."))
            return value if math.isfinite(value) else None
        except ValueError:
            pass
    # Tausendertrennzeichen: nur mit lückenlosen Dreiergruppen, damit "1.572,4"
    # (Excel, deutsch) und "1,572.4" (englisch) gelesen werden, ein Text wie
    # "1,2,3" aber weiter als unlesbar zählt.
    if _THOUSANDS_DE.match(raw):
        return float(raw.replace(".", "").replace(",", "."))
    if _THOUSANDS_EN.match(raw):
        return float(raw.replace(",", ""))
    return _BOOL_VALUES.get(raw.lower())


def _parse_timestamp(raw: str, ts_format: str, custom_pattern: str, tz: ZoneInfo) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    if ts_format == "unix_s":
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    if ts_format == "unix_ms":
        try:
            value = float(raw) / 1000.0
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    if ts_format == "iso":
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        try:
            dt = datetime.strptime(raw, PRESET_PATTERNS.get(ts_format, custom_pattern))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


#: Reihenfolge der Erkennung: das erste Format, das ALLE Probezellen liest, gewinnt.
#: Unix steht vorn und prüft die Ziffernzahl, weil float() sonst jede Zahl akzeptiert.
_DETECT_ORDER = ("unix_s", "unix_ms", "iso", "de_datetime_s", "de_datetime", "de_date")
_UNIX_S = re.compile(r"^\d{9,10}(\.\d+)?$")
_UNIX_MS = re.compile(r"^\d{12,13}$")
DETECT_SAMPLE_CELLS = 20


def sniff_ts_format(path: Path, delimiter: str, has_header: bool, ts_col: int) -> str | None:
    """Rät das Zeitformat der Zeitstempel-Spalte aus den ersten Zellen.

    None, wenn keines der bekannten Formate alle Zellen liest — dann bleibt es
    bei der Voreinstellung und die Nutzerin wählt selbst."""
    cells: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        if has_header:
            next(reader, None)
        for row in reader:
            if ts_col < len(row) and row[ts_col].strip():
                cells.append(row[ts_col].strip())
                if len(cells) >= DETECT_SAMPLE_CELLS:
                    break
    if not cells:
        return None
    utc = ZoneInfo("UTC")
    for candidate in _DETECT_ORDER:
        if candidate == "unix_s":
            ok = all(_UNIX_S.match(c) for c in cells)
        elif candidate == "unix_ms":
            ok = all(_UNIX_MS.match(c) for c in cells)
        else:
            ok = all(_parse_timestamp(c, candidate, "", utc) is not None for c in cells)
        if ok:
            return candidate
    return None


_AMBIGUOUS_THOUSANDS = re.compile(r"^[+-]?\d{1,3}[.,]\d{3}$")


def describe_sample(
    sample_rows: list[list[str]], ts_col: int, value_col: int,
    ts_format: str, custom_pattern: str, tz: ZoneInfo,
) -> list[dict]:
    """Zeigt je Vorschauzeile, WIE der Import Zeitstempel und Wert lesen würde.

    Die Rohtabelle allein verrät nicht, ob "1.572" als 1,572 oder 1572 gilt
    oder ob das Zeitformat passt; erst der Dry Run sagte es, und auch dort nur
    als Zahl übersprungener Zeilen. ``ambiguous`` markiert Werte mit genau einem
    Trenner vor drei Ziffern ("1.572", "1,572"): sie lassen sich als Dezimal-
    ODER Tausendertrenner lesen, der Import nimmt Dezimal."""
    out = []
    for row in sample_rows:
        raw_ts = row[ts_col] if ts_col < len(row) else ""
        raw_value = row[value_col] if value_col < len(row) else ""
        ts = _parse_timestamp(raw_ts, ts_format, custom_pattern, tz)
        value = _parse_value(raw_value)
        out.append({
            "ts": datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y %H:%M") if ts is not None else None,
            "value": _format_number(value) if value is not None else None,
            "ambiguous": value is not None and bool(_AMBIGUOUS_THOUSANDS.match(raw_value.strip())),
        })
    return out


def _format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".").replace(".", ",")


@dataclass
class ParseResult:
    rows: list[tuple[float, float]] = field(default_factory=list)
    skipped: int = 0
    #: Die ersten übersprungenen Zeilen als (Zeilennummer in der Datei, Grund,
    #: Rohtext der Zelle) — Grund ist "columns", "timestamp" oder "value". Nur
    #: für die Vorschau: ohne sie steht dort nur eine Zahl, und wer ein falsches
    #: Zeitformat gewählt hat, sieht nicht, woran es liegt.
    skipped_examples: list[tuple[int, str, str]] = field(default_factory=list)


#: Wie oft parse_rows() den Fortschritt meldet. 50.000 Zeilen sind bei
#: gemessenen rund 1,5 µs je Zeile etwa 75 ms — fein genug für eine Anzeige,
#: die alle 500 ms abgefragt wird, und selten genug, dass der Rückruf selbst
#: nicht ins Gewicht fällt.
PROGRESS_EVERY_ROWS = 50_000

#: Wie viele übersprungene Zeilen als Beispiel festgehalten werden.
SKIPPED_EXAMPLES = 3


def _note_skipped(result: ParseResult, line_num: int, reason: str, raw: str) -> None:
    if len(result.skipped_examples) < SKIPPED_EXAMPLES:
        result.skipped_examples.append((line_num, reason, raw.strip()[:40]))


def count_data_rows(path: Path, has_header: bool) -> int:
    """Schätzt die Zahl der Datenzeilen für die Fortschrittsanzeige.

    Zählt Zeilenumbrüche binär in Blöcken statt die Datei zu parsen: für eine
    gemessene 104-MB-Datei kostet das rund 50 ms, während das eigentliche
    Einlesen 6,0 Sekunden braucht. Der Preis dafür ist eine Näherung — ein
    Zeilenumbruch INNERHALB eines quotierten Feldes zählt hier mit, im Parser
    dagegen nicht. Für eine Gesamtzahl, gegen die ein Balken läuft, ist das
    unerheblich; für alles andere ist diese Funktion nicht gedacht.

    Bewusst kein Ersatz für ParseResult.rows: die echte, verlässliche Zahl
    steht nach parse_rows() und wird überall dort verwendet, wo sie zählt
    (Dry Run, Ergebnis, Importreport).
    """
    zeilen = 0
    with path.open("rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            zeilen += block.count(b"\n")
    if has_header and zeilen > 0:
        zeilen -= 1
    return max(0, zeilen)


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
    on_progress: Callable[[int], None] | None = None,
    state_mapper: Callable[[str, float], float | None] | None = None,
) -> ParseResult:
    """Liest die Rohdaten mit der gewählten Spalten-/Format-Zuordnung ein.
    Jede Zeile, die sich nicht plausibel als (Zeitstempel, Wert) lesen lässt
    (zu wenige Spalten, kein gültiges Zeitstempel-/Wert-Format), zählt als
    übersprungen statt die ganze Datei zu verwerfen — sichtbar im Dry Run.

    ``on_progress`` bekommt alle PROGRESS_EVERY_ROWS gelesenen Zeilen die
    bisherige Gesamtzahl GELESENER Zeilen (nicht nur der übernommenen) —
    sonst liefe der Balken bei einer Datei mit vielen übersprungenen Zeilen
    gegen eine Gesamtzahl, die er nie erreichen kann.

    ``state_mapper`` (Ziel ist eine Zustands-Entität): die Wertspalte ist dann
    Text und wird auf den Index abgebildet (ha_import.state_mapper_for()).
    Aufeinanderfolgende gleiche Zustände bleiben nur einmal stehen — der
    Live-Pfad schreibt auch nur Wechsel, und eine Probe pro Minute mit
    "charging" wären sonst tausende Zeilen ohne Aussage."""
    result = ParseResult()
    max_col = max(ts_col, value_col)
    gelesen = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        if has_header:
            next(reader, None)
        for line in reader:
            gelesen += 1
            if on_progress is not None and gelesen % PROGRESS_EVERY_ROWS == 0:
                on_progress(gelesen)
            if len(line) <= max_col:
                result.skipped += 1
                _note_skipped(result, reader.line_num, "columns", ",".join(line))
                continue
            ts = _parse_timestamp(line[ts_col], ts_format, custom_pattern, tz)
            value = (
                _parse_value(line[value_col]) if state_mapper is None or ts is None
                else state_mapper(line[value_col], ts)
            )
            if ts is None or value is None:
                result.skipped += 1
                if ts is None:
                    _note_skipped(result, reader.line_num, "timestamp", line[ts_col])
                else:
                    _note_skipped(result, reader.line_num, "value", line[value_col])
                continue
            result.rows.append((ts, value))
            if len(result.rows) > max_rows:
                raise ValueError(
                    tr("CSV enthält mehr als {limit} gültige Datenzeilen", limit=f"{max_rows:,}".replace(",", "."))
                )
    if on_progress is not None:
        on_progress(gelesen)
    result.rows.sort()
    if state_mapper is not None:
        result.rows = [
            row for i, row in enumerate(result.rows) if i == 0 or row[1] != result.rows[i - 1][1]
        ]
    return result
