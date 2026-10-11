"""Daten- und Dateilogik des CSV-/ZIP-Exports (Routen: export_routes.py).

Der Export liefert je Entität eine CSV (``timestamp,unix_ts,value``), bei mehreren
Entitäten automatisch als ZIP mit je einer CSV. Zeitraum und Auflösung sind
optional: ohne beides kommt die komplette Rohdaten-Historie wie bisher.

Zeilenzahl vorab: Das Limit (MAX_EXPORT_ROWS) gilt je Datei. Für den Assistenten
und die Sperre vor dem Download braucht es die Zeilenzahl, bevor eine Zeile
gelesen wird. Ganze Monate liefert die Parquet-Metadaten-Zählung (archivierte
Monate) beziehungsweise die Zeilenzahl der Hot-Datei (laufender Monat) exakt;
nur ein angeschnittener Randmonat wird anteilig nach Zeit gerechnet. Zur Löschung
markierte Datensätze sind nicht abgezogen — die Zahl ist eine Schätzung, die das
Limit höchstens knapp überschätzt. Beim Streamen gilt das Limit zusätzlich hart
(ResultLimitExceeded)."""

from __future__ import annotations

import math
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from .i18n import tr
from .storage import cleanup
from .storage.hotbuffer import hot_path
from .storage.index import Index
from .storage.paths import entity_dir

RESOLUTIONS = ("raw", "minute", "hour", "day")
_BUCKET_SECONDS = {"minute": 60, "hour": 3600, "day": 86400}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CSV_HEADER = "timestamp,unix_ts,value\r\n"
_CHUNK_ROWS = 5000


def parse_day(text: str | None, tz: ZoneInfo) -> float | None:
    """'JJJJ-MM-TT' → Beginn dieses Tages (lokal) als Zeitstempel; leer → None."""
    if not text:
        return None
    if not _DATE_RE.match(text):
        raise ValueError(tr("Ungültiges Datum"))
    try:
        day = date.fromisoformat(text)
    except ValueError as err:
        raise ValueError(tr("Ungültiges Datum")) from err
    return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()


def parse_period(start: str | None, end: str | None, tz: ZoneInfo) -> tuple[float | None, float | None]:
    """Zeitraum als halboffenes Intervall [start, end): das Enddatum zählt noch mit."""
    start_ts = parse_day(start, tz)
    end_day = parse_day(end, tz)
    end_ts = None
    if end_day is not None:
        end_date = datetime.fromtimestamp(end_day, tz).date() + timedelta(days=1)
        end_ts = datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz).timestamp()
    if start_ts is not None and end_ts is not None and end_ts <= start_ts:
        raise ValueError(tr("Das Enddatum liegt vor dem Startdatum"))
    return start_ts, end_ts


def entity_window(entity, start_ts: float | None, end_ts: float | None) -> tuple[float, float] | None:
    """Zeitfenster der Entität im gewünschten Zeitraum; None, wenn nichts darin liegt."""
    first_ts, last_ts = entity["first_ts"], entity["last_ts"]
    if first_ts is None or last_ts is None:
        return None
    window_start = first_ts if start_ts is None else max(first_ts, start_ts)
    window_end = last_ts + 1 if end_ts is None else min(last_ts + 1, end_ts)
    if window_end <= window_start:
        return None
    return window_start, window_end


def _month_spans(start_ts: float, end_ts: float, tz: ZoneInfo) -> Iterator[tuple[int, int, float, float]]:
    first = datetime.fromtimestamp(start_ts, tz)
    last = datetime.fromtimestamp(end_ts, tz)
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        yield (
            year,
            month,
            datetime(year, month, 1, tzinfo=tz).timestamp(),
            datetime(next_year, next_month, 1, tzinfo=tz).timestamp(),
        )
        year, month = next_year, next_month


def _month_rows(data_dir: Path, entity_id: str, year: int, month: int, tz: ZoneInfo, now_month: str) -> int:
    key = f"{year:04d}-{month:02d}"
    if key == now_month:
        path = hot_path(data_dir, entity_id, datetime(year, month, 15, tzinfo=tz).timestamp(), tz)
        if not path.exists():
            return 0
        with path.open("rb") as handle:
            return sum(1 for line in handle if line.strip())
    archive = entity_dir(data_dir, "archive", entity_id) / f"{key}.parquet"
    if not archive.exists():
        return 0
    return int(pq.ParquetFile(archive).metadata.num_rows)


def estimate_raw_rows(
    data_dir: Path, entity_id: str, window: tuple[float, float], tz: ZoneInfo, now: datetime | None = None
) -> int:
    now_month = (now or datetime.now(tz)).strftime("%Y-%m")
    window_start, window_end = window
    total = 0.0
    for year, month, month_start, month_end in _month_spans(window_start, window_end, tz):
        overlap = min(window_end, month_end) - max(window_start, month_start)
        if overlap <= 0:
            continue
        rows = _month_rows(data_dir, entity_id, year, month, tz, now_month)
        total += rows * (1.0 if overlap >= month_end - month_start else overlap / (month_end - month_start))
    return math.ceil(total)


def estimate_rows(
    data_dir: Path, entity, start_ts: float | None, end_ts: float | None, tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, int]:
    """Zeilen je Auflösung für den Zeitraum. Aggregiert gibt es höchstens so viele
    Zeilen wie Rohwerte und höchstens so viele wie Intervalle im Fenster."""
    window = entity_window(entity, start_ts, end_ts)
    if window is None:
        return dict.fromkeys(RESOLUTIONS, 0)
    raw = estimate_raw_rows(data_dir, entity["entity_id"], window, tz, now)
    span = window[1] - window[0]
    result = {"raw": raw}
    for name, seconds in _BUCKET_SECONDS.items():
        result[name] = min(raw, math.ceil(span / seconds) + 1)
    return result


def _bucket_bounds(ts: float, resolution: str, tz: ZoneInfo) -> tuple[float, float]:
    moment = datetime.fromtimestamp(ts, tz)
    if resolution == "minute":
        start = moment.replace(second=0, microsecond=0)
        end = start + timedelta(minutes=1)
    elif resolution == "hour":
        start = moment.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
    else:
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def aggregate_rows(
    rows: Iterable[tuple[float, float]], resolution: str, aggregation_type: str, tz: ZoneInfo
) -> Iterator[tuple[float, float]]:
    """Fasst zeitlich sortierte Rohwerte je Intervall (lokale Zeit) zusammen:
    Standard-Entitäten als Mittelwert, Zähler und Schalter als letzter Wert des
    Intervalls. Das Ergebnis trägt den Beginn des Intervalls als Zeitstempel."""
    mean = aggregation_type == "standard"
    bucket_start = bucket_end = 0.0
    open_bucket = False
    total = 0.0
    count = 0
    last = 0.0
    for ts, value in rows:
        if not (open_bucket and bucket_start <= ts < bucket_end):
            if open_bucket:
                yield bucket_start, (total / count if mean else last)
            bucket_start, bucket_end = _bucket_bounds(ts, resolution, tz)
            open_bucket = True
            total = 0.0
            count = 0
        total += value
        count += 1
        last = value
    if open_bucket:
        yield bucket_start, (total / count if mean else last)


def _format_value(value: float, aggregated_mean: bool) -> str:
    return f"{value:.10g}" if aggregated_mean else f"{value}"


def csv_chunks(
    data_dir: Path,
    index: Index,
    entity,
    start_ts: float | None,
    end_ts: float | None,
    resolution: str,
    tz: ZoneInfo,
    coordinator,
    max_rows: int,
) -> Iterator[str]:
    """Die CSV einer Entität in Textblöcken. Hält die Entitätssperre, solange sie
    gelesen wird (wie das Schreiben in archivierten Monaten)."""
    entity_id = entity["entity_id"]
    with coordinator.entity(entity_id):
        yield CSV_HEADER
        window = entity_window(entity, start_ts, end_ts)
        if window is None:
            return
        raw = cleanup.iter_raw_rows(
            data_dir, index, entity_id, window[0], window[1], tz,
            # Aggregiert zählt das Limit die Zeilen der Datei, nicht die gelesenen Rohwerte.
            max_rows=max_rows if resolution == "raw" else None,
        )
        mean = resolution != "raw" and entity["aggregation_type"] == "standard"
        rows = raw if resolution == "raw" else aggregate_rows(raw, resolution, entity["aggregation_type"], tz)
        buffer: list[str] = []
        emitted = 0
        for ts, value in rows:
            emitted += 1
            if emitted > max_rows:
                raise cleanup.ResultLimitExceeded(
                    tr("CSV-Export ist auf {MAX_EXPORT_ROWS} Zeilen begrenzt", MAX_EXPORT_ROWS=max_rows)
                )
            stamp = datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S")
            buffer.append(f"{stamp},{ts:.3f},{_format_value(value, mean)}\r\n")
            if len(buffer) >= _CHUNK_ROWS:
                yield "".join(buffer)
                buffer.clear()
        if buffer:
            yield "".join(buffer)


class _Sink:
    """Nicht rücklesbarer Schreibpuffer: ZipFile erkennt das (kein seek) und
    schreibt Datenbeschreiber statt Kopfdaten nachträglich zu korrigieren."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        self.buffer += data
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data


def zip_stream(files: list[tuple[str, Callable[[], Iterator[str]]]], now: datetime) -> Iterator[bytes]:
    """Baut das ZIP beim Lesen und reicht es sofort weiter, ohne Zwischendatei.
    Die erste Antwort kommt damit schon nach den ersten Zeilen, nicht erst nach
    der letzten Entität (wichtig hinter dem Ingress-Proxy)."""
    sink = _Sink()
    stamp = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as archive:  # type: ignore[arg-type]
        for name, make_chunks in files:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, "w", force_zip64=True) as handle:
                for chunk in make_chunks():
                    handle.write(chunk.encode("utf-8"))
                    data = sink.take()
                    if data:
                        yield data
        data = sink.take()
        if data:
            yield data
    tail = sink.take()
    if tail:
        yield tail


def period_suffix(start: str | None, end: str | None) -> str:
    """Dateinamen-Zusatz: '_2025', '_2025-09' oder '_von_bis' (leer ohne Zeitraum)."""
    if not start and not end:
        return ""
    if start and end:
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            return ""
        if first.month == 1 and first.day == 1 and last == date(first.year, 12, 31):
            return f"_{first.year}"
        if first.year == last.year and first.month == last.month and first.day == 1:
            next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            if last == next_month - timedelta(days=1):
                return f"_{first.year}-{first.month:02d}"
    return f"_{start or 'anfang'}_bis_{end or 'ende'}"


def file_suffix(start: str | None, end: str | None, resolution: str) -> str:
    return period_suffix(start, end) + ("" if resolution == "raw" else f"_{resolution}")
