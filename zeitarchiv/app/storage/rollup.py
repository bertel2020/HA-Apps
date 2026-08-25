"""Rollup-Engine: verdichtet abgeschlossene Perioden nach Konzept Abschnitt 05.

Wird von rotate.py direkt nach dem Archivieren eines Monats aufgerufen. Enthält
NUR abgeschlossene Perioden (fertige Tage/Stunden/Monate/Jahre) — die noch
offene, laufende Periode berechnet query.py live aus dem Hot Buffer (siehe
Plan "Eine Lücke aus dem Konzept, die hier geschlossen wird").

Bucket-Größen exakt nach den zwei Tabellen aus Konzept Abschnitt 05:
  Zähler:          Stunde=5min Tag=1h Woche/Monat=1Tag Jahr=1Monat Dekade=1Jahr
  Standard/Binär:  Stunde=1min Tag=5min Woche/Monat=1Std Jahr/Dekade=1Monat

Daraus folgen die persistierten Rollup-Stufen:
  Zähler:          tag.parquet (bedient Woche+Monat), monat.parquet (Jahr), jahr.parquet (Dekade)
  Standard/Binär:  stunde.parquet (bedient Woche+Monat), monat.parquet (Jahr+Dekade)
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from .paths import entity_dir

FINE_LEVEL = {"counter": "tag", "standard": "stunde", "switch": "stunde"}


@dataclass
class FineRow:
    bucket_start: float  # Unix-Timestamp (UTC) des Bucket-Anfangs
    value: float | None = None  # Zähler/Standard: Summe bzw. Mittelwert
    min_value: float | None = None
    max_value: float | None = None
    on_seconds: float | None = None  # Schalter: Einschaltdauer in Sekunden


def rollup_dir(data_dir: Path, entity_id: str) -> Path:
    return entity_dir(data_dir, "rollup", entity_id)


def rollup_path(data_dir: Path, entity_id: str, level: str) -> Path:
    return rollup_dir(data_dir, entity_id) / f"{level}.parquet"


def _month_str(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def last_value_before_month(data_dir: Path, entity_id: str, year: int, month: int) -> float | None:
    """Letzter Rohwert der unmittelbar vorherigen Archiv-Monatsdatei — Referenzwert
    für die Zähler-Delta-Berechnung am Monatsanfang (Konzept Abschnitt 05)."""
    prev_year, prev_month = _prev_month(year, month)
    path = entity_dir(data_dir, "archive", entity_id) / f"{_month_str(prev_year, prev_month)}.parquet"
    if not path.exists():
        return None
    table = pq.read_table(path, columns=["ts", "value"]).sort_by("ts")
    if table.num_rows == 0:
        return None
    return table.column("value")[-1].as_py()


def _bucket_key(ts: float, tz: ZoneInfo, level: str) -> datetime:
    """"tag"/"stunde" sind die persistierten Rollup-Stufen; "monat"/"jahr" werden
    zusätzlich von query.py genutzt, um die noch laufende Periode live aus dem
    Hot Buffer als einen einzelnen Bucket zu berechnen (siehe query.py)."""
    local = datetime.fromtimestamp(ts, tz)
    if level == "tag":
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    if level == "stunde":
        return local.replace(minute=0, second=0, microsecond=0)
    if level == "monat":
        return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if level == "jahr":
        return local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unbekanntes Bucket-Level: {level}")


def _named_bucket_next(level: str):
    """Start des jeweils nächsten Buckets, als Datumsarithmetik (nicht Sekunden) —
    damit Monate mit 28-31 Tagen und DST-Umstellungen bei Tag/Stunde korrekt bleiben."""
    if level == "tag":
        return lambda start: start + timedelta(days=1)
    if level == "stunde":
        return lambda start: start + timedelta(hours=1)
    if level == "monat":
        def _next(start: datetime) -> datetime:
            year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
            return start.replace(year=year, month=month)
        return _next
    if level == "jahr":
        return lambda start: start.replace(year=start.year + 1)
    raise ValueError(f"Unbekanntes Bucket-Level: {level}")


def named_bucket_key(tz: ZoneInfo, level: str):
    """Bucket-Funktion für die benannten Stufen tag/stunde/monat/jahr — öffentlicher
    Wrapper um _bucket_key, für query.py (Live-Berechnung der laufenden Periode)."""
    return lambda ts: _bucket_key(ts, tz, level)


def named_bucket_next(tz: ZoneInfo, level: str):
    """Passende bucket_next_fn zu named_bucket_key — für compute_fine_rollup_with_key,
    damit lang laufende Schalter-Intervalle korrekt über mehrere Buckets verteilt werden."""
    return _named_bucket_next(level)


def seconds_bucket_key(tz: ZoneInfo, bucket_seconds: int):
    """Bucket-Funktion für Sub-Tages-Auflösungen (Stunde/Tag-Ansicht in query.py,
    nie persistiert). Rundet auf lokale Kalendertag-Grenzen ab, nicht auf UTC —
    sonst würde z. B. ein 5-Minuten-Bucket um Mitternacht Europe/Berlin verrutschen."""

    def key_fn(ts: float) -> datetime:
        local = datetime.fromtimestamp(ts, tz)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_since_midnight = (local - day_start).total_seconds()
        bucket_index = int(seconds_since_midnight // bucket_seconds)
        return day_start + timedelta(seconds=bucket_index * bucket_seconds)

    return key_fn


def seconds_bucket_next(bucket_seconds: int):
    """Passende bucket_next_fn zu seconds_bucket_key."""
    return lambda start: start + timedelta(seconds=bucket_seconds)


def compute_fine_rollup(
    rows: list[tuple[float, float]],
    aggregation_type: str,
    level: str,
    tz: ZoneInfo,
    boundary_value: float | None,
    window_end_ts: float,
) -> tuple[list[FineRow], float | None]:
    """Verdichtet sortierte (ts, value)-Rohzeilen auf benannte Buckets (tag/stunde/monat/jahr).

    Gibt die Bucket-Zeilen zurück sowie (nur für Zähler relevant) den letzten
    gesehenen Rohwert, damit der Aufrufer ihn als boundary_value an den
    nächsten Monat/Aufruf weiterreichen kann.
    """
    return compute_fine_rollup_with_key(
        rows,
        aggregation_type,
        lambda ts: _bucket_key(ts, tz, level),
        boundary_value,
        window_end_ts,
        bucket_next_fn=_named_bucket_next(level),
    )


def compute_fine_rollup_with_key(
    rows: list[tuple[float, float]],
    aggregation_type: str,
    bucket_key_fn,
    boundary_value: float | None,
    window_end_ts: float,
    bucket_next_fn=None,
) -> tuple[list[FineRow], float | None]:
    """Wie compute_fine_rollup, aber mit einer frei wählbaren Bucket-Funktion —
    genutzt von query.py für die Sekunden-genauen Stunde/Tag-Ansichten.

    bucket_next_fn (Bucket-Start -> nächster Bucket-Start) wird nur für Schalter-
    Entitäten gebraucht: ein Intervall, das länger als ein Bucket "an" bleibt
    (z. B. ein Regensensor, der stundenlang unverändert meldet), muss korrekt auf
    alle betroffenen Buckets aufgeteilt werden statt komplett im Start-Bucket zu
    landen — sonst zeigt ein einzelner 5-Minuten-Bucket z. B. 3000 Sekunden "an".
    """
    if not rows:
        return [], boundary_value

    if aggregation_type == "counter":
        buckets: dict[datetime, float] = {}
        prev_value = boundary_value if boundary_value is not None else rows[0][1]
        for ts, value in rows:
            delta = max(0.0, value - prev_value)
            key = bucket_key_fn(ts)
            buckets[key] = buckets.get(key, 0.0) + delta
            prev_value = value
        fine = [
            FineRow(bucket_start=key.timestamp(), value=total)
            for key, total in sorted(buckets.items())
        ]
        return fine, prev_value

    if aggregation_type == "standard":
        sums: dict[datetime, list[float]] = {}
        for ts, value in rows:
            key = bucket_key_fn(ts)
            sums.setdefault(key, []).append(value)
        fine = [
            FineRow(
                bucket_start=key.timestamp(),
                value=sum(values) / len(values),
                min_value=min(values),
                max_value=max(values),
            )
            for key, values in sorted(sums.items())
        ]
        return fine, None

    if aggregation_type == "switch":
        if bucket_next_fn is None:
            raise ValueError("bucket_next_fn wird für Schalter-Entitäten benötigt")
        on_seconds: dict[datetime, float] = {}
        for i, (ts, value) in enumerate(rows):
            interval_end = rows[i + 1][0] if i + 1 < len(rows) else window_end_ts
            if value < 0.5:
                continue
            # Ein "an"-Intervall kann mehrere Buckets überspannen (z. B. ein
            # Regensensor, der stundenlang unverändert meldet) — deshalb wird
            # die Dauer an jeder Bucket-Grenze abgeschnitten und korrekt verteilt,
            # statt komplett dem Start-Bucket zugerechnet zu werden.
            current = ts
            while current < interval_end:
                bucket_start = bucket_key_fn(current)
                bucket_end_ts = bucket_next_fn(bucket_start).timestamp()
                segment_end = min(interval_end, bucket_end_ts)
                on_seconds[bucket_start] = on_seconds.get(bucket_start, 0.0) + max(
                    0.0, segment_end - current
                )
                current = segment_end
        fine = [
            FineRow(bucket_start=key.timestamp(), on_seconds=seconds)
            for key, seconds in sorted(on_seconds.items())
        ]
        return fine, None

    raise ValueError(f"Unbekannter Aggregationstyp: {aggregation_type}")


def _fine_schema(aggregation_type: str) -> pa.Schema:
    if aggregation_type == "counter":
        return pa.schema([("bucket_start", pa.float64()), ("value", pa.float64())])
    if aggregation_type == "standard":
        return pa.schema(
            [
                ("bucket_start", pa.float64()),
                ("value", pa.float64()),
                ("min_value", pa.float64()),
                ("max_value", pa.float64()),
            ]
        )
    return pa.schema([("bucket_start", pa.float64()), ("on_seconds", pa.float64())])


def _fine_rows_to_table(rows: list[FineRow], aggregation_type: str) -> pa.Table:
    if aggregation_type == "counter":
        return pa.table(
            {"bucket_start": [r.bucket_start for r in rows], "value": [r.value for r in rows]},
            schema=_fine_schema(aggregation_type),
        )
    if aggregation_type == "standard":
        return pa.table(
            {
                "bucket_start": [r.bucket_start for r in rows],
                "value": [r.value for r in rows],
                "min_value": [r.min_value for r in rows],
                "max_value": [r.max_value for r in rows],
            },
            schema=_fine_schema(aggregation_type),
        )
    return pa.table(
        {
            "bucket_start": [r.bucket_start for r in rows],
            "on_seconds": [r.on_seconds for r in rows],
        },
        schema=_fine_schema(aggregation_type),
    )


def _append_table(path: Path, new_rows: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pq.read_table(path)
        combined = pa.concat_tables([existing, new_rows])
    else:
        combined = new_rows
    combined = combined.sort_by("bucket_start")
    pq.write_table(combined, path, compression="zstd")


def _aggregate_fine_to_month(rows: list[FineRow], aggregation_type: str, month_start_ts: float) -> FineRow:
    if aggregation_type == "counter":
        return FineRow(bucket_start=month_start_ts, value=sum(r.value or 0.0 for r in rows))
    if aggregation_type == "standard":
        all_values = [r.value for r in rows if r.value is not None]
        all_min = [r.min_value for r in rows if r.min_value is not None]
        all_max = [r.max_value for r in rows if r.max_value is not None]
        return FineRow(
            bucket_start=month_start_ts,
            value=sum(all_values) / len(all_values) if all_values else None,
            min_value=min(all_min) if all_min else None,
            max_value=max(all_max) if all_max else None,
        )
    return FineRow(bucket_start=month_start_ts, on_seconds=sum(r.on_seconds or 0.0 for r in rows))


def append_completed_month(
    data_dir: Path,
    entity_id: str,
    aggregation_type: str,
    month_table: pa.Table,
    year: int,
    month: int,
    tz: ZoneInfo,
) -> None:
    """Wird direkt nach dem Archivieren eines Monats aufgerufen (siehe rotate.py).

    Schreibt die feine Rollup-Stufe (Tag bzw. Stunde) UND die Monats-Stufe fort.
    Für Zähler-Entitäten prüft es zusätzlich, ob damit ein vollständiges,
    vergangenes Kalenderjahr erreicht ist, und schreibt dann jahr.parquet fort.
    """
    level = FINE_LEVEL[aggregation_type]
    rows_raw = sorted(zip(month_table.column("ts").to_pylist(), month_table.column("value").to_pylist()))

    boundary_value = (
        last_value_before_month(data_dir, entity_id, year, month)
        if aggregation_type == "counter"
        else None
    )

    month_start = datetime(year, month, 1, tzinfo=tz)
    days_in_month = calendar.monthrange(year, month)[1]
    month_end = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=tz) + timedelta(seconds=1)

    fine_rows, _ = compute_fine_rollup(
        rows_raw, aggregation_type, level, tz, boundary_value, month_end.timestamp()
    )
    if fine_rows:
        _append_table(rollup_path(data_dir, entity_id, level), _fine_rows_to_table(fine_rows, aggregation_type))

    month_row = _aggregate_fine_to_month(fine_rows, aggregation_type, month_start.timestamp())
    _append_table(
        rollup_path(data_dir, entity_id, "monat"),
        _fine_rows_to_table([month_row], aggregation_type),
    )

    if aggregation_type == "counter":
        _maybe_append_year(data_dir, entity_id, year, tz)


def _maybe_append_year(data_dir: Path, entity_id: str, year: int, tz: ZoneInfo) -> None:
    now_year = datetime.now(tz).year
    if year >= now_year:
        return  # nur vergangene, vollständige Jahre verdichten

    monat_path = rollup_path(data_dir, entity_id, "monat")
    if not monat_path.exists():
        return
    table = pq.read_table(monat_path)
    year_start = datetime(year, 1, 1, tzinfo=tz).timestamp()
    year_end = datetime(year + 1, 1, 1, tzinfo=tz).timestamp()
    mask = [(year_start <= ts < year_end) for ts in table.column("bucket_start").to_pylist()]
    months_in_year = sum(mask)
    if months_in_year < 12:
        return  # Jahr noch nicht vollständig archiviert

    jahr_path = rollup_path(data_dir, entity_id, "jahr")
    if jahr_path.exists():
        existing_years = pq.read_table(jahr_path, columns=["bucket_start"]).column("bucket_start").to_pylist()
        if year_start in existing_years:
            return  # schon geschrieben

    total = sum(v for v, keep in zip(table.column("value").to_pylist(), mask) if keep)
    _append_table(
        jahr_path,
        pa.table({"bucket_start": [year_start], "value": [total]}, schema=_fine_schema("counter")),
    )


def _remove_rows_for_month(path: Path, tz: ZoneInfo, year: int, month: int) -> None:
    """Entfernt Zeilen mit bucket_start im angegebenen Kalendermonat aus einer
    Rollup-Datei — für einen nachträglichen Archiv-Purge (cleanup.py), der den
    Rohdaten-Bestand eines bereits archivierten Monats verändert hat."""
    if not path.exists():
        return
    table = pq.read_table(path)
    starts = table.column("bucket_start").to_pylist()
    keep_mask = [
        not (datetime.fromtimestamp(s, tz).year == year and datetime.fromtimestamp(s, tz).month == month)
        for s in starts
    ]
    if all(keep_mask):
        return
    if not any(keep_mask):
        path.unlink()
        return
    pq.write_table(table.filter(keep_mask), path, compression="zstd")


def _remove_row_for_year(path: Path, tz: ZoneInfo, year: int) -> None:
    if not path.exists():
        return
    table = pq.read_table(path)
    starts = table.column("bucket_start").to_pylist()
    keep_mask = [datetime.fromtimestamp(s, tz).year != year for s in starts]
    if all(keep_mask):
        return
    if not any(keep_mask):
        path.unlink()
        return
    pq.write_table(table.filter(keep_mask), path, compression="zstd")


def remove_month(data_dir: Path, entity_id: str, aggregation_type: str, year: int, month: int, tz: ZoneInfo) -> None:
    """Entfernt alle Rollup-Zeilen eines Monats ohne Ersatz — für den seltenen
    Fall, dass nach einem Archiv-Purge (cleanup.py) kein Rohwert des Monats
    mehr übrig ist (der komplette Monat war weich gelöscht)."""
    level = FINE_LEVEL[aggregation_type]
    _remove_rows_for_month(rollup_path(data_dir, entity_id, level), tz, year, month)
    _remove_rows_for_month(rollup_path(data_dir, entity_id, "monat"), tz, year, month)
    if aggregation_type == "counter":
        _remove_row_for_year(rollup_path(data_dir, entity_id, "jahr"), tz, year)


def replace_month(
    data_dir: Path, entity_id: str, aggregation_type: str, month_table: pa.Table, year: int, month: int, tz: ZoneInfo
) -> None:
    """Ersetzt die Rollup-Zeilen eines bereits archivierten Monats durch frisch
    aus month_table berechnete — für den nachträglichen Archiv-Purge
    (cleanup.py), bei dem sich der Rohdaten-Bestand des Monats geändert hat
    (weich gelöschte Zeilen wurden tatsächlich entfernt). Entfernt zuerst die
    veralteten Zeilen (append_completed_month() hängt sonst nur an, statt zu
    ersetzen — siehe _append_table), berechnet danach exakt wie beim ersten
    Archivieren neu, inklusive eines bereits vorhandenen Jahres-Werts bei
    Zähler-Entitäten."""
    remove_month(data_dir, entity_id, aggregation_type, year, month, tz)
    append_completed_month(data_dir, entity_id, aggregation_type, month_table, year, month, tz)
