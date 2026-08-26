"""Query-Engine: Zeitraum → Bucket-Auflösung, Rollup+Live-Merge (Konzept Abschnitt 05).

Rollup-Dateien (rollup.py) enthalten nur abgeschlossene Perioden. Die aktuelle,
noch offene Periode wird hier live aus dem Hot Buffer berechnet und mit den
Rollup-Zeilen zusammengeführt (siehe Plan "Eine Lücke aus dem Konzept, die hier
geschlossen wird"). Kalendergrenzen (Tag/Monat/Jahr) richten sich nach der
konfigurierten Zeitzone (App-Option "timezone", Default Europe/Berlin).

Navigation (offset) und "kontinuierlich" (rollierend statt kalendarisch) wirken
ausschließlich über _window() — der Rollup/Live-Split darunter bleibt unverändert,
weil er sich immer an der TATSÄCHLICHEN aktuellen Periode orientiert (now_local),
nicht am angezeigten Fenster. Dadurch "funktioniert" das Navigieren in vergangene
Monate/Jahre automatisch korrekt, ohne dass die Split-Logik etwas davon wissen muss.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from . import cleanup, rollup
from .hotbuffer import hot_path, read_rows
from .index import Index, filter_deleted_occurrences
from .paths import entity_dir
from ..limits import MAX_RAW_QUERY_POINTS

RANGE_KEYS = ("hour", "day", "week", "month", "year", "decade")

# Sub-Tages-Auflösungen (Stunde/Tag) werden nie persistiert — Konzept Abschnitt 02:
# ein einzelner Monat im Hot Buffer ist klein genug, um live zu aggregieren.
LIVE_BUCKET_SECONDS = {
    "counter": {"hour": 5 * 60, "day": 60 * 60},
    "standard": {"hour": 60, "day": 5 * 60},
    "switch": {"hour": 60, "day": 5 * 60},
}

FINE_LEVEL = {"counter": "tag", "standard": "stunde", "switch": "stunde"}

# Gewünschte visuelle Dichte für Balkendiagramme. Linien behalten bewusst die
# bisherige feinere Auflösung; nur eine explizite bzw. vom Entitätstyp
# abgeleitete Balkenserie verwendet dieses Profil.
BAR_RESOLUTION = {
    "day": "stunde",
    "week": "tag",
    "month": "tag",
    "year": "monat",
    "decade": "jahr",
}


def _resolved_chart_type(aggregation_type: str, chart_type: str | None) -> str:
    if chart_type is not None:
        if chart_type not in ("line", "bar"):
            raise ValueError(f"Unbekannter Diagrammtyp: {chart_type}")
        return chart_type
    return "bar" if aggregation_type in ("counter", "switch") else "line"


def _shift_year(dt: datetime, years: int) -> datetime:
    """Verschiebt dt um ganze Kalenderjahre — für den Vorjahresvergleich
    (Konzept Abschnitt 06/10: "Tag heute mit Tag vor einem Jahr, aktueller Monat
    mit Vorjahresmonat"). ZoneInfo berechnet den UTC-Offset lazy anhand des
    tatsächlichen Datums, ein einfaches replace(year=...) bleibt dadurch auch
    über Sommer-/Winterzeit-Grenzen hinweg korrekt (anders als bei pytz). Einzige
    Ausnahme: 29. Februar in einem Nicht-Schaltjahr weicht auf den 28. aus, statt
    einen ValueError zu werfen — eine bewusst einfache, eindeutige Definition."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        return dt.replace(year=dt.year + years, day=28)


def _window(
    range_key: str, now_local: datetime, offset: int = 0, continuous: bool = False
) -> tuple[datetime, datetime, datetime]:
    """Berechnet [Anfang, Ende) für einen Zeitraum, sowie das ungekappte
    natürliche Periodenende (drittes Rückgabeelement).

    offset verschiebt um ganze Perioden (0 = aktuell, -1 = eine Periode zurück, …).
    Vorwärts über "jetzt" hinaus ergibt keinen Sinn, deshalb hart auf 0 gedeckelt.

    Jeder Zeitraum kennt zwei Modi (einheitlich für alle sechs Bereiche, nicht nur
    Tag/Monat/Jahr): standardmäßig kalendarisch/"komplett" (an der jeweiligen
    natürlichen Grenze ausgerichtet — volle Stunde/Kalenderwoche Mo–So/Mitternacht/
    Monatserster/1. Januar/Dekadengrenze, am aktuellen "jetzt" gedeckelt), mit
    continuous=True stattdessen ein rollierendes Fenster gleicher Länge, das genau
    bei "jetzt" (bzw. bei offset Perioden davor) endet statt an der Kalendergrenze.

    Das zweite Rückgabeelement (Ende) ist weiterhin bei "jetzt" gedeckelt — es
    steuert, welche Daten tatsächlich abgefragt werden (siehe Modul-Kommentar
    oben, Rollup/Live-Split). Das dritte Element (natürliches Ende) ist NIE
    gedeckelt und dient nur der Anzeige: eine laufende Woche/Monat/… soll auch
    ohne Daten in der Zukunft bis zur vollen Kalendergrenze angezeigt werden
    (z. B. Woche bis Sonntag), nicht nur bis "jetzt". Bei continuous ist es
    identisch zum gedeckelten Ende, da ein rollierendes Fenster keine
    Kalendergrenze hat, die es überschreiten könnte.
    """
    offset = min(offset, 0)
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    if range_key == "hour":
        if continuous:
            anchor = now_local + timedelta(hours=offset)
            return anchor - timedelta(hours=1), anchor, anchor
        hour_start = now_local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
        natural_end = hour_start + timedelta(hours=1)
        return hour_start, min(natural_end, now_local), natural_end

    if range_key == "day":
        if continuous:
            anchor = now_local + timedelta(days=offset)
            return anchor - timedelta(days=1), anchor, anchor
        start = midnight + timedelta(days=offset)
        natural_end = start + timedelta(days=1)
        return start, min(natural_end, now_local), natural_end

    if range_key == "week":
        if continuous:
            anchor = now_local + timedelta(days=7 * offset)
            return anchor - timedelta(days=7), anchor, anchor
        # Kalenderwoche Montag–Sonntag (ISO), nicht die rollierenden letzten 7 Tage.
        week_start = midnight - timedelta(days=midnight.weekday()) + timedelta(days=7 * offset)
        natural_end = week_start + timedelta(days=7)
        return week_start, min(natural_end, now_local), natural_end

    if range_key == "month":
        if continuous:
            # Kalendarisch "ein Monat zurück" ist an Monatsenden mehrdeutig
            # (was ist ein Monat vor dem 31. Januar?) — 30 Tage sind eine bewusst
            # einfache, eindeutige Definition für die rollierende Variante.
            anchor = now_local + timedelta(days=30 * offset)
            return anchor - timedelta(days=30), anchor, anchor
        total_months = (now_local.year * 12 + (now_local.month - 1)) + offset
        year, month = divmod(total_months, 12)
        month += 1
        start = midnight.replace(year=year, month=month, day=1)
        end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
        natural_end = start.replace(year=end_year, month=end_month)
        return start, min(natural_end, now_local), natural_end

    if range_key == "year":
        if continuous:
            anchor = now_local.replace(year=now_local.year + offset)
            return anchor.replace(year=anchor.year - 1), anchor, anchor
        year = now_local.year + offset
        start = midnight.replace(year=year, month=1, day=1)
        natural_end = start.replace(year=year + 1)
        return start, min(natural_end, now_local), natural_end

    if range_key == "decade":
        if continuous:
            anchor = now_local.replace(year=now_local.year + 10 * offset)
            return anchor.replace(year=anchor.year - 10), anchor, anchor
        decade_start_year = (now_local.year // 10) * 10 + 10 * offset
        start = midnight.replace(year=decade_start_year, month=1, day=1)
        natural_end = start.replace(year=decade_start_year + 10)
        return start, min(natural_end, now_local), natural_end

    raise ValueError(f"Unbekannter Zeitraum: {range_key}")


def _read_hot_rows_filtered(
    data_dir: Path, entity_id: str, index: Index, anchor_ts: float, start_ts: float, end_ts: float, tz: ZoneInfo
) -> list[tuple[float, float]]:
    rows = read_rows(hot_path(data_dir, entity_id, anchor_ts, tz))
    in_range = sorted((ts, v) for ts, v in rows if start_ts <= ts <= end_ts)
    deleted_counts = index.get_deleted_counts(entity_id, start_ts, end_ts)
    return filter_deleted_occurrences(in_range, deleted_counts)


def _boundary_value(data_dir: Path, entity_id: str, before_ts: float, tz: ZoneInfo) -> float | None:
    """Letzter Rohwert vor ``before_ts`` aus Hot Buffer oder Monatsarchiv."""
    before_local = datetime.fromtimestamp(before_ts, tz)
    rows = read_rows(hot_path(data_dir, entity_id, before_ts, tz))
    prior = sorted((ts, value) for ts, value in rows if ts < before_ts)
    if prior:
        return prior[-1][1]

    # Bei historischen Fenstern liegt auch der aktuelle Fenstermonat bereits
    # im Archiv. Rueckwaerts suchen, damit der letzte bekannte Wert selbst bei
    # einem oder mehreren Monaten ohne Messpunkt weiter gilt.
    current_month = before_local.strftime("%Y-%m")
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    if not archive_dir.exists():
        return None
    for path in sorted(archive_dir.glob("????-??.parquet"), reverse=True):
        if path.stem > current_month:
            continue
        table = pq.read_table(path, columns=["ts", "value"]).sort_by("ts")
        candidates = [
            (ts, value)
            for ts, value in zip(
                table.column("ts").to_pylist(), table.column("value").to_pylist()
            )
            if ts < before_ts
        ]
        if candidates:
            return candidates[-1][1]
    return None


def _query_live_fine(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    range_key: str,
    chart_type: str,
    window_start: datetime,
    window_end: datetime,
    tz: ZoneInfo,
    now_local: datetime,
) -> list[rollup.FineRow]:
    """Stunde/Tag lesen IMMER Rohdaten, nie Rollups (Konzept Abschnitt 02) — über
    cleanup.list_raw_rows, damit auch das Navigieren in einen bereits archivierten
    Vormonat funktioniert (liest dann aus dem Archiv statt aus dem Hot Buffer)."""
    bucket_seconds = (
        3600
        if chart_type == "bar" and BAR_RESOLUTION.get(range_key) == "stunde"
        else LIVE_BUCKET_SECONDS[aggregation_type][range_key]
    )
    rows = list(
        cleanup.iter_raw_rows(
            data_dir,
            index,
            entity_id,
            window_start.timestamp(),
            window_end.timestamp(),
            tz,
            now=now_local,
            max_rows=MAX_RAW_QUERY_POINTS,
        )
    )
    boundary = (
        _boundary_value(data_dir, entity_id, window_start.timestamp(), tz)
        if aggregation_type == "counter"
        else None
    )
    key_fn = rollup.seconds_bucket_key(tz, bucket_seconds)
    next_fn = rollup.seconds_bucket_next(bucket_seconds)
    fine, _ = rollup.compute_fine_rollup_with_key(
        rows, aggregation_type, key_fn, boundary, window_end.timestamp(), bucket_next_fn=next_fn
    )
    return fine


def _read_rollup_rows(
    data_dir: Path, entity_id: str, level: str, start_ts: float, end_ts: float
) -> list[rollup.FineRow]:
    path = rollup.rollup_path(data_dir, entity_id, level)
    if not path.exists():
        return []
    table = pq.read_table(
        path,
        filters=[("bucket_start", ">=", start_ts), ("bucket_start", "<", end_ts)],
    )
    rows = []
    columns = table.column_names
    for i in range(table.num_rows):
        bucket_start = table.column("bucket_start")[i].as_py()
        kwargs = {"bucket_start": bucket_start}
        for col in ("value", "min_value", "max_value", "on_seconds"):
            if col in columns:
                kwargs[col] = table.column(col)[i].as_py()
        rows.append(rollup.FineRow(**kwargs))
    return rows


def _aggregate_rollup_rows(
    rows: list[rollup.FineRow], aggregation_type: str, level: str, tz: ZoneInfo
) -> list[rollup.FineRow]:
    """Verdichtet vorhandene feinere Rollups auf eine gröbere Anzeigestufe."""
    key_fn = rollup.named_bucket_key(tz, level)
    grouped: dict[float, list[rollup.FineRow]] = {}
    for row in rows:
        bucket_start = key_fn(row.bucket_start).timestamp()
        grouped.setdefault(bucket_start, []).append(row)

    result: list[rollup.FineRow] = []
    for bucket_start, bucket_rows in sorted(grouped.items()):
        if aggregation_type == "counter":
            result.append(
                rollup.FineRow(
                    bucket_start=bucket_start,
                    value=sum(row.value or 0.0 for row in bucket_rows),
                )
            )
        elif aggregation_type == "switch":
            result.append(
                rollup.FineRow(
                    bucket_start=bucket_start,
                    on_seconds=sum(row.on_seconds or 0.0 for row in bucket_rows),
                )
            )
        else:
            values = [row.value for row in bucket_rows if row.value is not None]
            minima = [row.min_value for row in bucket_rows if row.min_value is not None]
            maxima = [row.max_value for row in bucket_rows if row.max_value is not None]
            result.append(
                rollup.FineRow(
                    bucket_start=bucket_start,
                    value=sum(values) / len(values) if values else None,
                    min_value=min(minima) if minima else None,
                    max_value=max(maxima) if maxima else None,
                )
            )
    return result


def _query_completed_plus_live_fine(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    chart_type: str,
    window_start: datetime,
    window_end: datetime,
    tz: ZoneInfo,
    now_local: datetime,
) -> list[rollup.FineRow]:
    """Für week/month: abgeschlossene Tage/Stunden aus dem Rollup + laufender
    Monat live aus dem Hot Buffer, an derselben Bucket-Größe (tag/stunde).

    current_month_start kommt bewusst aus now_local (der tatsächlichen Uhrzeit),
    nicht aus window_end — sonst würde eine Navigation in einen vergangenen Monat
    fälschlich versuchen, dessen (längst rotierten) Hot Buffer zu lesen."""
    level = (
        BAR_RESOLUTION["week"]
        if chart_type == "bar"
        else FINE_LEVEL[aggregation_type]
    )
    current_month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    completed_end = min(window_end, current_month_start)
    source_level = FINE_LEVEL[aggregation_type]
    completed = _read_rollup_rows(
        data_dir, entity_id, source_level,
        window_start.timestamp(), completed_end.timestamp()
    )
    if level != source_level:
        completed = _aggregate_rollup_rows(completed, aggregation_type, level, tz)

    live_start = max(window_start, current_month_start)
    live: list[rollup.FineRow] = []
    if live_start < window_end:
        rows = _read_hot_rows_filtered(
            data_dir, entity_id, index, now_local.timestamp(), live_start.timestamp(), window_end.timestamp(), tz
        )
        boundary = (
            _boundary_value(data_dir, entity_id, live_start.timestamp(), tz)
            if aggregation_type == "counter"
            else None
        )
        key_fn = rollup.named_bucket_key(tz, level)
        next_fn = rollup.named_bucket_next(tz, level)
        live, _ = rollup.compute_fine_rollup_with_key(
            rows, aggregation_type, key_fn, boundary, window_end.timestamp(), bucket_next_fn=next_fn
        )

    return sorted(completed + live, key=lambda r: r.bucket_start)


def _month_live_row(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    month_start: datetime,
    window_end: datetime,
    now_local: datetime,
    tz: ZoneInfo,
) -> rollup.FineRow | None:
    rows = _read_hot_rows_filtered(
        data_dir, entity_id, index, now_local.timestamp(), month_start.timestamp(), window_end.timestamp(), tz
    )
    if not rows:
        return None
    boundary = (
        _boundary_value(data_dir, entity_id, month_start.timestamp(), tz)
        if aggregation_type == "counter"
        else None
    )
    key_fn = rollup.named_bucket_key(tz, "monat")
    next_fn = rollup.named_bucket_next(tz, "monat")
    fine, _ = rollup.compute_fine_rollup_with_key(
        rows, aggregation_type, key_fn, boundary, window_end.timestamp(), bucket_next_fn=next_fn
    )
    return fine[0] if fine else None


def _query_year_level(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    range_key: str,
    chart_type: str,
    window_start: datetime,
    window_end: datetime,
    tz: ZoneInfo,
    now_local: datetime,
) -> list[rollup.FineRow]:
    """Für year: immer Monatswerte aus monat.parquet + der laufende Monat live
    aus dem Hot Buffer — jahr.parquet wird hier NIE konsultiert, auch nicht für
    Monate aus einem vorherigen Kalenderjahr. Grund: "Kontinuierlich" liefert ein
    rollierendes Fenster, das über eine Jahresgrenze hinausreichen kann (z. B.
    Aug 2025–Aug 2026) — jahr.parquet kennt aber nur GANZE Kalenderjahre und
    kann so ein angeschnittenes Vorjahr nicht liefern, die betroffenen Monate
    wären sonst spurlos verschwunden.

    Für decade-Balken sowie decade-Zähler ein Wert pro Kalenderjahr: ein Jahr, das VOLLSTÄNDIG
    im Fenster liegt, kommt aus jahr.parquet (schneller Pfad für abgeschlossene
    Jahre). Ein nur teilweise abgedecktes Jahr — das laufende Jahr am aktuellen
    Ende des Fensters, oder bei "Kontinuierlich" auch ein angeschnittenes Jahr am
    Fenster-Anfang — wird stattdessen aus den vorhandenen Monats-Rollups (+ live
    laufender Monat) zu einem einzigen Jahres-Balken aufsummiert, statt als
    mehrere schmale Monats-Balken neben den jahresbreiten Balken zu stehen (sah
    vorher "unsauber" aus). Standard/Schalter werden für Balken beim Lesen aus
    ihren Monatswerten auf Jahre verdichtet; Linien behalten ihre bisherige
    Monats-Granularität."""
    current_month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    completed_month_end = min(window_end, current_month_start)

    live_row = None
    if current_month_start < window_end:
        live_row = _month_live_row(
            data_dir,
            index,
            entity_id,
            aggregation_type,
            max(window_start, current_month_start),
            window_end,
            now_local,
            tz,
        )

    resolution = (
        BAR_RESOLUTION[range_key]
        if chart_type == "bar"
        else ("jahr" if range_key == "decade" and aggregation_type == "counter" else "monat")
    )
    if resolution == "monat":
        completed = _read_rollup_rows(
            data_dir, entity_id, "monat", window_start.timestamp(), completed_month_end.timestamp()
        )
        if live_row is not None:
            completed.append(live_row)
        return sorted(completed, key=lambda r: r.bucket_start)

    completed: list[rollup.FineRow] = []
    year = window_start.year
    while True:
        year_start = window_start.replace(year=year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if year_start >= window_end:
            break
        year_end = year_start.replace(year=year + 1)
        clip_start = max(window_start, year_start)
        clip_end = min(window_end, year_end)

        row = None
        if aggregation_type == "counter" and clip_start == year_start and clip_end == year_end:
            jahr_rows = _read_rollup_rows(data_dir, entity_id, "jahr", year_start.timestamp(), year_end.timestamp())
            if jahr_rows:
                row = jahr_rows[0]

        if row is None:
            monat_rows = _read_rollup_rows(
                data_dir, entity_id, "monat", clip_start.timestamp(), min(clip_end, completed_month_end).timestamp()
            )
            year_rows = list(monat_rows)
            if live_row is not None and clip_start.timestamp() <= live_row.bucket_start < clip_end.timestamp():
                year_rows.append(live_row)
            if year_rows:
                if aggregation_type == "counter":
                    row = rollup.FineRow(
                        bucket_start=year_start.timestamp(),
                        value=sum(r.value or 0.0 for r in year_rows),
                    )
                elif aggregation_type == "switch":
                    row = rollup.FineRow(
                        bucket_start=year_start.timestamp(),
                        on_seconds=sum(r.on_seconds or 0.0 for r in year_rows),
                    )
                else:
                    values = [r.value for r in year_rows if r.value is not None]
                    minima = [r.min_value for r in year_rows if r.min_value is not None]
                    maxima = [r.max_value for r in year_rows if r.max_value is not None]
                    row = rollup.FineRow(
                        bucket_start=year_start.timestamp(),
                        value=sum(values) / len(values) if values else None,
                        min_value=min(minima) if minima else None,
                        max_value=max(maxima) if maxima else None,
                    )

        if row is not None:
            completed.append(row)
        year += 1

    return sorted(completed, key=lambda r: r.bucket_start)


def query_series(
    data_dir: Path,
    index: Index,
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    offset: int = 0,
    continuous: bool = False,
    year_over_year: bool = False,
    chart_type: str | None = None,
) -> dict:
    if range_key not in RANGE_KEYS:
        raise ValueError(f"Unbekannter Zeitraum: {range_key}")

    entity = index.get_entity(entity_id)
    if entity is None:
        return {"entity_id": entity_id, "aggregation_type": None, "chart_type": "line", "points": []}

    aggregation_type = entity["aggregation_type"]
    resolved_chart_type = _resolved_chart_type(aggregation_type, chart_type)
    now_local = now.astimezone(tz)
    window_start, window_end, period_end = _window(range_key, now_local, offset, continuous)
    if year_over_year:
        # Vorjahresvergleich verschiebt nur das Fenster um ein Jahr zurück, NICHT
        # now_local — der Rollup/Live-Split (Datei-Modulgrenze in diesem Modul,
        # siehe Kopf-Kommentar) bleibt so unverändert an der tatsächlichen
        # aktuellen Periode orientiert, das verschobene Fenster liegt für offset<=0
        # immer schon vollständig in der Vergangenheit.
        window_start = _shift_year(window_start, -1)
        window_end = _shift_year(window_end, -1)
        period_end = _shift_year(period_end, -1)

    if range_key in ("hour", "day"):
        fine = _query_live_fine(
            data_dir, index, entity_id, aggregation_type, range_key, resolved_chart_type,
            window_start, window_end, tz, now_local
        )
    elif range_key in ("week", "month"):
        fine = _query_completed_plus_live_fine(
            data_dir, index, entity_id, aggregation_type, resolved_chart_type,
            window_start, window_end, tz, now_local
        )
    else:
        fine = _query_year_level(
            data_dir, index, entity_id, aggregation_type, range_key, resolved_chart_type,
            window_start, window_end, tz, now_local
        )

    points = [
        {
            "ts": row.bucket_start,
            "value": row.value if aggregation_type != "switch" else row.on_seconds,
            "min": row.min_value,
            "max": row.max_value,
        }
        for row in fine
    ]

    # Standard-Linien stellen einen Zustand dar: der letzte bekannte Rohwert
    # gilt bis zum naechsten Messpunkt. Ein Randpunkt am Fensteranfang verhindert
    # daher eine kuenstliche Luecke bis zum ersten Wert innerhalb des Fensters.
    if resolved_chart_type == "line" and aggregation_type == "standard":
        boundary_value = _boundary_value(
            data_dir, entity_id, window_start.timestamp(), tz
        )
        if boundary_value is not None and (
            not points or points[0]["ts"] > window_start.timestamp()
        ):
            points.insert(
                0,
                {
                    "ts": window_start.timestamp(),
                    "value": boundary_value,
                    "min": boundary_value,
                    "max": boundary_value,
                },
            )

    return {
        "entity_id": entity_id,
        "aggregation_type": aggregation_type,
        "chart_type": resolved_chart_type,
        "points": points,
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
    }


def query_raw_series(
    data_dir: Path,
    index: Index,
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    offset: int = 0,
    continuous: bool = False,
) -> dict:
    """Ungebucketes Gegenstück zu query_series() — "Hohe Dichte (raw)" (Konzept
    Abschnitt 06/10): liefert jeden einzelnen Rohwert im Fenster statt ihn in
    Buckets zu verdichten. Nutzt dieselbe Fenster-Berechnung (Navigation/
    Kontinuierlich-Modus verhalten sich identisch zur gebucketen Ansicht), aber
    immer als Linie — Balken pro Rohwert würden bei tausenden Punkten nur noch
    als flächige Masse erscheinen, nicht als lesbares Diagramm."""
    if range_key not in RANGE_KEYS:
        raise ValueError(f"Unbekannter Zeitraum: {range_key}")

    entity = index.get_entity(entity_id)
    if entity is None:
        return {"entity_id": entity_id, "aggregation_type": None, "chart_type": "line", "points": []}

    aggregation_type = entity["aggregation_type"]
    now_local = now.astimezone(tz)
    window_start, window_end, period_end = _window(range_key, now_local, offset, continuous)

    points = [
        {"ts": ts, "value": value, "min": None, "max": None}
        for ts, value in cleanup.iter_raw_rows(
            data_dir,
            index,
            entity_id,
            window_start.timestamp(),
            window_end.timestamp(),
            tz,
            now=now_local,
            max_rows=MAX_RAW_QUERY_POINTS,
        )
    ]

    boundary_value = _boundary_value(data_dir, entity_id, window_start.timestamp(), tz)
    if boundary_value is not None and (
        not points or points[0]["ts"] > window_start.timestamp()
    ):
        points.insert(
            0,
            {"ts": window_start.timestamp(), "value": boundary_value, "min": None, "max": None},
        )

    return {
        "entity_id": entity_id,
        "aggregation_type": aggregation_type,
        "chart_type": "line",
        "points": points,
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
    }
