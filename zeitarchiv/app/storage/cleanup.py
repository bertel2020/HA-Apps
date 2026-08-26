"""Bereinigungs-Werkzeug: Rohdaten lesen, Ausreißer/Lücken/Duplikate/Wiederholungen/
Zählerrückgänge markieren,
nie destruktiv löschen (Konzept Abschnitt 04).

Löschen ist ein Soft-Delete über index.deleted_points — Zeitstempel werden aus
allen Ansichten rausgefiltert. Ein manueller Purge (purge_hot_buffer() für den
laufenden Monat, purge_archived_months() für bereits archivierte Monate,
beide nur bei explizitem Klick in den Einstellungen) entfernt sie danach auch
physisch.
"""

from __future__ import annotations

import calendar
import statistics
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from . import rollup
from .hotbuffer import append as hot_append
from .hotbuffer import hot_path, read_rows
from .index import Index, filter_deleted_occurrences, should_accept_value
from .paths import entity_dir


def _months_between(start_ts: float, end_ts: float, tz: ZoneInfo) -> list[tuple[int, int]]:
    start = datetime.fromtimestamp(start_ts, tz).replace(day=1)
    end = datetime.fromtimestamp(end_ts, tz)
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def list_raw_rows(
    data_dir: Path,
    index: Index,
    entity_id: str,
    start_ts: float,
    end_ts: float,
    tz: ZoneInfo,
    now: datetime | None = None,
    max_rows: int | None = None,
) -> list[tuple[float, float]]:
    """Liest alle Rohwerte im Zeitfenster aus Hot Buffer + Archiv, ohne soft-gelöschte.

    `now` ist injizierbar (wie in query.py) statt datetime.now() fest zu verdrahten —
    hält die Funktion ohne Zeitreise-Tricks testbar."""
    return list(
        iter_raw_rows(
            data_dir, index, entity_id, start_ts, end_ts, tz,
            now=now, max_rows=max_rows
        )
    )


class ResultLimitExceeded(ValueError):
    """Eine Abfrage würde mehr Zeilen als erlaubt materialisieren."""


def analyze_raw_rows_page(
    rows_factory: Callable[[], Iterator[tuple[float, float]]],
    *,
    filter_: str,
    page: int,
    page_size: int,
    gap_threshold_minutes: float | None,
    outlier_threshold_percent: float | None,
    decimals: str = "auto",
    counter_decrease_enabled: bool = False,
) -> dict:
    """Analysiert beliebig viele sortierte Rohwerte mit begrenztem Speicher.

    Der erste Durchlauf bestimmt Anzahl und Ausreißer-Basiswert. Der zweite
    berechnet Markierungen und behält nur so viele der neuesten Treffer, wie
    für die angeforderte Seite nötig sind. Damit funktioniert insbesondere
    der Zeitraum "Gesamt" auch oberhalb des UI-Materialisierungslimits.
    """
    total_rows = 0
    absolute_sum = 0.0
    for _ts, value in rows_factory():
        total_rows += 1
        absolute_sum += abs(value)

    page_size = max(1, min(int(page_size), 1000))
    upper_page = max(1, -(-total_rows // page_size))
    requested_page = max(1, min(int(page), upper_page))
    retained: deque[dict] = deque(maxlen=requested_page * page_size)
    baseline = absolute_sum / total_rows if total_rows else 0.0
    gap_seconds = (
        gap_threshold_minutes * 60 if gap_threshold_minutes is not None else None
    )

    counts = {
        "all": total_rows,
        "outliers": 0,
        "gaps": 0,
        "duplicates": 0,
        "repetitions": 0,
        "counter_decreases": 0,
    }
    total_matches = 0
    previous_ts: float | None = None
    previous_value: float | None = None
    group_ts: float | None = None
    group_rows: list[dict] = []
    group_outlier: str | None = None
    group_gap: str | None = None
    last_kept_ts: float | None = None
    last_kept_value: float | None = None
    counter_previous_ts: float | None = None
    counter_previous_value: float | None = None

    selected_filter = filter_ if filter_ in {
        "all", "outliers", "gaps", "duplicates", "repetitions", "counter_decreases"
    } else "all"

    def flush_group() -> None:
        nonlocal total_matches, group_rows
        if not group_rows:
            return
        duplicate_reason = (
            f"{len(group_rows)}× derselbe Zeitstempel" if len(group_rows) > 1 else None
        )
        if group_outlier is not None:
            counts["outliers"] += 1
        if group_gap is not None:
            counts["gaps"] += 1
        if duplicate_reason is not None:
            counts["duplicates"] += 1

        for row in group_rows:
            repetition_reason = row.pop("repetition_reason")
            counter_decrease_reason = row.pop("counter_decrease_reason")
            if repetition_reason is not None:
                counts["repetitions"] += 1
            if counter_decrease_reason is not None:
                counts["counter_decreases"] += 1
            row_matches = (
                selected_filter == "all"
                or (selected_filter == "outliers" and group_outlier is not None)
                or (selected_filter == "gaps" and group_gap is not None)
                or (selected_filter == "duplicates" and duplicate_reason is not None)
                or (selected_filter == "repetitions" and repetition_reason is not None)
                or (
                    selected_filter == "counter_decreases"
                    and counter_decrease_reason is not None
                )
            )
            if row_matches:
                row["flags"] = [
                    {"label": label, "reason": reason}
                    for label, reason in (
                        ("Ausreißer", group_outlier),
                        ("Lücke", group_gap),
                        ("Duplikat", duplicate_reason),
                        ("Wiederholung", repetition_reason),
                        ("Zählerrückgang", counter_decrease_reason),
                    )
                    if reason is not None
                ]
                retained.append(row)
                total_matches += 1
        group_rows = []

    for ts, value in rows_factory():
        if group_ts is None or ts != group_ts:
            flush_group()
            group_ts = ts
            group_outlier = None
            group_gap = None

        if previous_ts is not None and gap_seconds is not None:
            delta = ts - previous_ts
            if delta > gap_seconds:
                group_gap = (
                    f"{_format_duration(delta)} seit vorherigem Wert "
                    f"(Schwellwert: {_format_duration(gap_seconds)})"
                )
        if (
            previous_value is not None
            and outlier_threshold_percent is not None
            and baseline > 0
        ):
            jump_percent = abs(value - previous_value) / baseline * 100
            if jump_percent > outlier_threshold_percent:
                group_outlier = (
                    f"{jump_percent:.0f} % Sprung gegenüber Vorwert "
                    f"({previous_value:.3g})"
                )

        if should_accept_value(
            "decimals", decimals, last_kept_value, last_kept_ts, value, ts
        ):
            last_kept_ts, last_kept_value = ts, value
            repetition_reason = None
        else:
            repetition_reason = _repetition_reason(decimals)

        counter_decrease_reason = None
        if counter_decrease_enabled:
            if (
                counter_previous_ts is not None
                and counter_previous_value is not None
                and ts > counter_previous_ts
                and value < counter_previous_value
            ):
                counter_decrease_reason = _counter_decrease_reason(
                    counter_previous_value, value
                )
            # Bei identischem Zeitstempel bleibt das erste Vorkommen die
            # Referenz; weitere Vorkommen behandelt bereits der Duplikatfilter.
            if counter_previous_ts is None or ts > counter_previous_ts:
                counter_previous_ts, counter_previous_value = ts, value

        group_rows.append({
            "ts": ts,
            "value": value,
            "repetition_reason": repetition_reason,
            "counter_decrease_reason": counter_decrease_reason,
        })
        previous_ts = ts
        previous_value = value
    flush_group()

    total_pages = max(1, -(-total_matches // page_size))
    actual_page = max(1, min(requested_page, total_pages))
    newest_first = list(reversed(retained))
    start_index = (actual_page - 1) * page_size
    page_rows = newest_first[start_index : start_index + page_size]
    return {
        "rows": page_rows,
        "counts": counts,
        "pagination": {
            "page": actual_page,
            "page_size": page_size,
            "total": total_matches,
            "total_pages": total_pages,
            "start": start_index + 1 if total_matches else 0,
            "end": min(start_index + page_size, total_matches),
        },
    }


def iter_raw_rows(
    data_dir: Path,
    index: Index,
    entity_id: str,
    start_ts: float,
    end_ts: float,
    tz: ZoneInfo,
    now: datetime | None = None,
    max_rows: int | None = None,
):
    """Streamt Rohwerte monatsweise und wendet Soft-Deletes je Partition an."""
    now_month_key = (now or datetime.now(tz)).strftime("%Y-%m")
    emitted = 0

    for year, month in _months_between(start_ts, end_ts, tz):
        month_key = f"{year:04d}-{month:02d}"
        month_start = datetime(year, month, 1, tzinfo=tz).timestamp()
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        month_end = datetime(next_year, next_month, 1, tzinfo=tz).timestamp()
        deleted = index.get_deleted_counts(
            entity_id, max(start_ts, month_start), min(end_ts, month_end)
        )
        if month_key == now_month_key:
            path = hot_path(data_dir, entity_id, datetime(year, month, 15, tzinfo=tz).timestamp(), tz)
            batches = [sorted(read_rows(path))]
        else:
            archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month_key}.parquet"
            if not archive_path.exists():
                continue
            batches = (
                zip(batch.column("ts").to_pylist(), batch.column("value").to_pylist())
                for batch in pq.ParquetFile(archive_path).iter_batches(
                    batch_size=8192, columns=["ts", "value"]
                )
            )
        remaining_deleted = dict(deleted)
        for batch in batches:
            for ts, value in batch:
                if not (start_ts <= ts < end_ts):
                    continue
                if remaining_deleted.get(ts, 0) > 0:
                    remaining_deleted[ts] -= 1
                    continue
                emitted += 1
                if max_rows is not None and emitted > max_rows:
                    raise ResultLimitExceeded(
                        f"Ergebnis überschreitet {max_rows} Rohwerte"
                    )
                yield ts, value


def get_raw_values_for_timestamps(
    data_dir: Path, entity_id: str, timestamps: list[float], tz: ZoneInfo, now: datetime | None = None
) -> list[tuple[float, float]]:
    """Liest die Rohwerte zu bestimmten Zeitstempeln — unabhängig davon, ob sie
    im Hot Buffer oder einem bereits archivierten Monat liegen, und bewusst
    OHNE die Soft-Delete-Filterung von list_raw_rows(): für die "Rückgängig"-
    Vorschau, die genau die weich gelöschten Zeilen zeigen soll, nicht die
    gefilterte Sicht ohne sie. `now` injizierbar wie überall sonst in diesem
    Modul, statt datetime.now() fest zu verdrahten."""
    if not timestamps:
        return []
    wanted = set(timestamps)
    now_month_key = (now or datetime.now(tz)).strftime("%Y-%m")
    found: dict[float, float] = {}
    for year, month in _months_between(min(timestamps), max(timestamps) + 1, tz):
        month_key = f"{year:04d}-{month:02d}"
        if month_key == now_month_key:
            path = hot_path(data_dir, entity_id, datetime(year, month, 15, tzinfo=tz).timestamp(), tz)
            month_rows = read_rows(path)
        else:
            archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month_key}.parquet"
            if not archive_path.exists():
                continue
            table = pq.read_table(archive_path, columns=["ts", "value"])
            month_rows = list(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))
        for ts, value in month_rows:
            if ts in wanted:
                found[ts] = value
    return sorted(found.items())


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} Sek."
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} Min."
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} Std. {minutes} Min." if minutes else f"{hours} Std."
    days, hours = divmod(hours, 24)
    return f"{days} Tage {hours} Std." if hours else f"{days} Tage"


def detect_duplicates(rows: list[tuple[float, float]]) -> dict[float, str]:
    """Gibt je doppelt vorkommendem Zeitstempel eine Begründung zurück (Anzahl der
    Vorkommen) — die Rückgabe verhält sich wie ein Set (Mitgliedschaft/Iteration
    über die Keys), liefert für die Anzeige aber zusätzlich den Grund."""
    seen: dict[float, int] = {}
    for ts, _ in rows:
        seen[ts] = seen.get(ts, 0) + 1
    return {ts: f"{count}× derselbe Zeitstempel" for ts, count in seen.items() if count > 1}


def _repetition_reason(decimals: str) -> str:
    precision = "3 (Automatisch)" if decimals == "auto" else decimals
    return f"Gleicher gerundeter Folgewert bei {precision} Nachkommastellen"


def iter_repeated_rows(
    rows: Iterable[tuple[float, float]], decimals: str
) -> Iterator[tuple[float, float]]:
    """Findet aufeinanderfolgende Werte, die nach der Anzeige-Rundung gleich sind.

    Dieselbe Sechs-Stunden-Lebenszeichenregel wie im Live-Schreibpfad bleibt
    erhalten. Dadurch wird eine lange konstante Phase stark verdichtet, ohne
    vollständig aus dem Zeitverlauf zu verschwinden.
    """
    last_kept_ts: float | None = None
    last_kept_value: float | None = None
    for ts, value in rows:
        if should_accept_value(
            "decimals", decimals, last_kept_value, last_kept_ts, value, ts
        ):
            last_kept_ts, last_kept_value = ts, value
        else:
            yield ts, value


def repeated_rows_to_delete(
    rows: Iterable[tuple[float, float]], decimals: str
) -> list[tuple[float, float]]:
    """Materialisierte Variante für begrenzte Zeitfenster und Tests."""
    return list(iter_repeated_rows(rows, decimals))


def detect_repetitions(
    rows: list[tuple[float, float]], decimals: str
) -> dict[float, str]:
    """Markiert die von ``repeated_rows_to_delete`` erkannten Zeilen."""
    reason = _repetition_reason(decimals)
    return {ts: reason for ts, _value in repeated_rows_to_delete(rows, decimals)}


def _counter_decrease_reason(previous_value: float, value: float) -> str:
    difference = previous_value - value
    if previous_value:
        percentage = difference / abs(previous_value) * 100
        return (
            f"Vorwert {previous_value:.12g} → {value:.12g}; "
            f"Rückgang um {difference:.12g} ({percentage:.1f} %)"
        )
    return (
        f"Vorwert {previous_value:.12g} → {value:.12g}; "
        f"Rückgang um {difference:.12g}"
    )


def detect_counter_decreases(
    rows: Iterable[tuple[float, float]],
) -> dict[float, str]:
    """Markiert den ersten niedrigeren Wert nach einem höheren Zählerstand.

    Ein Rückgang beginnt eine neue mögliche Zählerperiode. Deshalb wird nur
    die Rückgangskante markiert; anschließend steigende Werte werden nicht
    gegen das historische Maximum geprüft. Bei Zeitstempel-Duplikaten bleibt
    das erste Vorkommen die Referenz, passend zur Duplikatbereinigung.
    """
    decreases: dict[float, str] = {}
    previous_ts: float | None = None
    previous_value: float | None = None
    for ts, value in rows:
        if previous_ts is not None and ts > previous_ts:
            if previous_value is not None and value < previous_value:
                decreases[ts] = _counter_decrease_reason(previous_value, value)
            previous_ts, previous_value = ts, value
        elif previous_ts is None:
            previous_ts, previous_value = ts, value
    return decreases


def duplicate_rows_to_delete(rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Für "Duplikate automatisch entfernen" (Konzept Abschnitt 04): bei jedem
    mehrfach vorkommenden Zeitstempel bleibt genau EIN Vorkommen erhalten (das
    zeitlich erste in rows), alle weiteren werden zum Löschen vorgeschlagen.
    rows muss dieselbe stabile Reihenfolge haben wie beim tatsächlichen Löschen
    (list_raw_rows liefert bereits sortiert), sonst könnte hier ein anderes
    Vorkommen ausgewählt werden als später tatsächlich entfernt wird."""
    seen: set[float] = set()
    to_delete: list[tuple[float, float]] = []
    for ts, value in rows:
        if ts in seen:
            to_delete.append((ts, value))
        else:
            seen.add(ts)
    return to_delete


def count_duplicate_rows_by_entity(
    data_dir: Path,
    index: Index,
    tz: ZoneInfo,
    window_days: int = 30,
    now: datetime | None = None,
    max_rows_per_entity: int | None = None,
) -> list[dict]:
    """Aufschlüsselung erkannter Duplikate je Entität für die Statistik-
    Übersicht — bewusst auf ein Zeitfenster begrenzt (Standard: letzte 30
    Tage), nicht auf die komplette Historie: anders als weich gelöschte
    Vorkommen (eigene Tabelle, billig abzufragen) sind Duplikate nirgends
    persistiert, eine archiv-weite Suche über Jahre an Rohdaten für jede
    Entität wäre bei mehreren Millionen Zeilen pro Entität spürbar langsam.

    Gibt nur Entitäten mit mindestens einem gefundenen Duplikat zurück,
    sortiert nach Anzahl absteigend."""
    now = now or datetime.now(tz)
    window_start = (now - timedelta(days=window_days)).timestamp()
    window_end = now.timestamp()
    results: list[dict] = []
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        rows = list_raw_rows(
            data_dir, index, entity_id, window_start, window_end, tz,
            now=now, max_rows=max_rows_per_entity
        )
        count = len(duplicate_rows_to_delete(rows))
        if count:
            results.append({"entity_id": entity_id, "friendly_name": entity["friendly_name"], "count": count})
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def detect_gaps(rows: list[tuple[float, float]], threshold_minutes: float | None) -> dict[float, str]:
    """Markiert den Zeitstempel NACH einer Lücke, die den je Entität konfigurierten
    Minuten-Schwellwert überschreitet (Konfigurationsseite der Entität) — bewusst
    ein fester, vom Nutzer gewählter Schwellwert statt einer automatisch aus dem
    Median abgeleiteten Heuristik: der Nutzer kennt das erwartete Sendeintervall
    seiner Entität besser als ein Median, der bei vielen Nachzüglern selbst schon
    verzerrt sein kann. threshold_minutes=None ("Aus" in der Konfiguration)
    liefert immer {} (keine Lücken-Erkennung)."""
    if threshold_minutes is None or len(rows) < 2:
        return {}
    threshold_seconds = threshold_minutes * 60
    flagged: dict[float, str] = {}
    for i in range(len(rows) - 1):
        delta = rows[i + 1][0] - rows[i][0]
        if delta > threshold_seconds:
            flagged[rows[i + 1][0]] = (
                f"{_format_duration(delta)} seit vorherigem Wert (Schwellwert: {_format_duration(threshold_seconds)})"
            )
    return flagged


def detect_outliers(rows: list[tuple[float, float]], threshold_percent: float | None) -> dict[float, str]:
    """Markiert Werte, die um mehr als den je Entität konfigurierten Prozentsatz
    gegenüber dem UNMITTELBAR VORHERGEHENDEN Wert springen (Konfigurationsseite
    der Entität) — bewusst ein Sprung gegenüber dem Vorwert statt einer
    Abweichung vom Median des gesamten Fensters: bei natürlich stark
    schwankenden Entitäten (z. B. aktuelle Leistungsaufnahme, die über den Tag
    zwischen nahe 0 W und mehreren kW pendelt) würde eine Abweichung vom
    Fenster-Median einen Großteil der völlig normalen Werte als "Ausreißer"
    markieren — ein plötzlicher Sprung gegenüber dem Vorwert ist die deutlich
    zuverlässigere Definition für eine tatsächlich verdächtige Messung
    (Sensor-Aussetzer, Übertragungsfehler). Bezugsgröße für die Prozentangabe
    ist der mittlere Betrag aller Werte im Fenster (nicht der Vorwert selbst) —
    sonst würde ein Sprung von z. B. 0 W auf 50 W bei einem Vorwert nahe 0 eine
    riesige, aber im Kontext bedeutungslose Prozentzahl ergeben.
    threshold_percent=None ("Aus" in der Konfiguration) liefert immer {}
    (keine Ausreißer-Erkennung)."""
    if threshold_percent is None or len(rows) < 2:
        return {}
    values = [v for _, v in rows]
    baseline = statistics.mean(abs(v) for v in values)
    if baseline == 0:
        return {}
    flagged: dict[float, str] = {}
    for i in range(1, len(rows)):
        ts, value = rows[i]
        prev_value = rows[i - 1][1]
        jump_percent = abs(value - prev_value) / baseline * 100
        if jump_percent > threshold_percent:
            flagged[ts] = f"{jump_percent:.0f} % Sprung gegenüber Vorwert ({prev_value:.3g})"
    return flagged


def soft_delete(index: Index, entity_id: str, timestamps: list[float]) -> None:
    index.mark_deleted(entity_id, timestamps)


def undo_last_delete(index: Index, entity_id: str) -> int:
    """Macht die zuletzt gelöschte Charge rückgängig (ein Klick = ein 'Löschen'-Vorgang
    rückgängig, wie im Konzept-Mockup "Rückgängig" beschrieben)."""
    return index.undo_last_deleted_batch(entity_id)


def preview_purge(
    data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None
) -> dict:
    """Ermittelt exakt, welche Soft-Deletes der manuelle Purge entfernen kann.

    Es werden nur Zeitstempelspalten und der aktuelle Hot Buffer gelesen. Archiv,
    Rollups, Indexzähler und Löschmarkierungen bleiben unverändert.
    """
    now = now or datetime.now(tz)
    rows: list[dict] = []
    totals = {
        "marked_rows": 0,
        "removable_rows": 0,
        "hot_rows": 0,
        "archive_rows": 0,
        "archive_months": 0,
        "entities_affected": 0,
        "not_removable_rows": 0,
    }

    def consume(timestamps: list[float], remaining: dict[float, int]) -> int:
        removed = 0
        for ts in timestamps:
            if remaining.get(ts, 0) > 0:
                remaining[ts] -= 1
                removed += 1
        return removed

    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        deleted = index.get_deleted_counts_for_entity(entity_id)
        marked = sum(deleted.values())
        if not marked:
            continue
        remaining = dict(deleted)
        hot_rows = 0
        archive_rows = 0
        archive_months = 0

        hot_file = hot_path(data_dir, entity_id, now.timestamp(), tz)
        if hot_file.exists():
            hot_rows = consume([ts for ts, _ in read_rows(hot_file)], remaining)

        archive_dir = entity_dir(data_dir, "archive", entity_id)
        if archive_dir.exists():
            for path in sorted(archive_dir.glob("*.parquet")):
                table = pq.read_table(path, columns=["ts"])
                removed = consume(table.column("ts").to_pylist(), remaining)
                if removed:
                    archive_rows += removed
                    archive_months += 1

        removable = hot_rows + archive_rows
        not_removable = max(0, marked - removable)
        if removable:
            totals["entities_affected"] += 1
        totals["marked_rows"] += marked
        totals["removable_rows"] += removable
        totals["hot_rows"] += hot_rows
        totals["archive_rows"] += archive_rows
        totals["archive_months"] += archive_months
        totals["not_removable_rows"] += not_removable
        rows.append({
            "entity_id": entity_id,
            "friendly_name": entity["friendly_name"],
            "marked_rows": marked,
            "removable_rows": removable,
            "hot_rows": hot_rows,
            "archive_rows": archive_rows,
            "archive_months": archive_months,
            "not_removable_rows": not_removable,
        })

    rows.sort(key=lambda row: (-row["removable_rows"], row["entity_id"]))
    return {"totals": totals, "rows": rows}


def purge_hot_buffer(data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None) -> int:
    """Entfernt weich gelöschte Vorkommen physisch aus dem Hot Buffer (laufender
    Monat, unkomprimiertes CSV) — der laufende Monat hat keine Rollup-Datei,
    seine Aggregation wird bei jeder Abfrage ohnehin live aus dem Hot Buffer
    berechnet (siehe query.py), ein Purge hier ist deshalb ein reiner
    CSV-Rewrite ohne Rollup-Folgeaufwand. Für bereits archivierte Monate siehe
    purge_archived_months() (Parquet-Rewrite + Rollup-Neuberechnung).

    Gibt die Anzahl tatsächlich physisch entfernter Zeilen zurück."""
    now = now or datetime.now(tz)
    current_month_start = datetime(now.year, now.month, 1, tzinfo=tz).timestamp()
    purged_total = 0
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        deleted = index.get_deleted_counts_for_entity(entity_id)
        relevant = {ts: count for ts, count in deleted.items() if ts >= current_month_start}
        if not relevant:
            continue
        path = hot_path(data_dir, entity_id, now.timestamp(), tz)
        rows = read_rows(path)
        if not rows:
            continue
        remaining = dict(relevant)
        kept_rows: list[tuple[float, float]] = []
        removed_timestamps: list[float] = []
        for ts, value in rows:
            if remaining.get(ts, 0) > 0:
                remaining[ts] -= 1
                removed_timestamps.append(ts)
            else:
                kept_rows.append((ts, value))
        if not removed_timestamps:
            continue
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for ts, value in kept_rows:
                f.write(f"{ts},{value}\n")
        tmp_path.replace(path)
        index.remove_deleted_points(entity_id, removed_timestamps)
        index.add_row_count(entity_id, -len(removed_timestamps))
        purged_total += len(removed_timestamps)
    return purged_total


def _update_first_ts_after_archive_purge(data_dir: Path, index: Index, entity_id: str, tz: ZoneInfo, now: datetime) -> None:
    """Nur relevant, wenn ein Archiv-Purge den ältesten Monat einer Entität
    komplett geleert hat (jeder Rohwert des Monats war weich gelöscht) — dann
    zeigt first_ts sonst weiter auf eine nicht mehr existierende Datei."""
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    remaining = sorted(archive_dir.glob("*.parquet")) if archive_dir.exists() else []
    new_first_ts: float | None = None
    if remaining:
        table = pq.read_table(remaining[0], columns=["ts"])
        if table.num_rows:
            new_first_ts = min(table.column("ts").to_pylist())
    else:
        hot_file = hot_path(data_dir, entity_id, now.timestamp(), tz)
        rows = read_rows(hot_file) if hot_file.exists() else []
        if rows:
            new_first_ts = min(ts for ts, _ in rows)
    index.set_first_ts(entity_id, new_first_ts)


def purge_archived_months(data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None) -> dict:
    """Entfernt weich gelöschte Vorkommen physisch aus bereits archivierten
    Monaten — schreibt die betroffene Parquet-Datei ohne die gelöschten
    Zeilen neu (Rest des Monats unverändert) und berechnet die zugehörigen
    Rollup-Zeilen (fein/Monat, bei Zähler-Entitäten ggf. ein bereits
    berechnetes Jahr) über rollup.replace_month() passend neu. Ergänzt
    purge_hot_buffer() um den bisher fehlenden Teil (Konzept, "Offene
    Punkte") — bewusst weiterhin nur bei explizitem Klick in den
    Einstellungen, nie automatisch/lazy: anders als beim Hot Buffer wird hier
    eine echte Archivdatei angefasst.

    Gibt eine Zusammenfassung zurück (rows_purged, months_purged)."""
    now = now or datetime.now(tz)
    rows_purged = 0
    months_purged = 0
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        aggregation_type = entity["aggregation_type"]
        deleted = index.get_deleted_counts_for_entity(entity_id)
        if not deleted:
            continue
        archive_dir = entity_dir(data_dir, "archive", entity_id)
        if not archive_dir.exists():
            continue
        entity_had_emptied_month = False
        for path in sorted(archive_dir.glob("*.parquet")):
            year_str, month_str = path.stem.split("-")
            year, month = int(year_str), int(month_str)
            month_start = datetime(year, month, 1, tzinfo=tz).timestamp()
            days_in_month = calendar.monthrange(year, month)[1]
            month_end = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=tz).timestamp() + 1
            relevant = {ts: count for ts, count in deleted.items() if month_start <= ts < month_end}
            if not relevant:
                continue

            table = pq.read_table(path)
            rows = sorted(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))
            kept = filter_deleted_occurrences(rows, relevant)
            removed = len(rows) - len(kept)
            if removed == 0:
                continue

            old_size = path.stat().st_size
            if kept:
                kept_table = pa.table({"ts": [r[0] for r in kept], "value": [r[1] for r in kept]})
                tmp_path = path.with_suffix(".tmp")
                pq.write_table(kept_table, tmp_path, compression="zstd")
                tmp_path.replace(path)
                rollup.replace_month(data_dir, entity_id, aggregation_type, kept_table, year, month, tz)
                new_size = path.stat().st_size
            else:
                # Jeder Rohwert dieses Monats war weich gelöscht — Archivdatei
                # und zugehörige Rollup-Zeilen komplett entfernen statt eine
                # leere Parquet-Datei/eine Monats-Zeile ohne Grundlage zu behalten.
                path.unlink()
                rollup.remove_month(data_dir, entity_id, aggregation_type, year, month, tz)
                new_size = 0
                entity_had_emptied_month = True

            index.add_row_count(entity_id, -removed)
            index.add_size_bytes(entity_id, new_size - old_size)
            removed_timestamps = [ts for ts, count in relevant.items() for _ in range(count)]
            index.remove_deleted_points(entity_id, removed_timestamps)

            rows_purged += removed
            months_purged += 1

        if entity_had_emptied_month:
            _update_first_ts_after_archive_purge(data_dir, index, entity_id, tz, now)

    return {"rows_purged": rows_purged, "months_purged": months_purged}


# -- Bearbeitungsbereich: nachträgliches Hinzufügen/Korrigieren von Rohwerten --
#
# Ergänzt die reine Löschung oben um die beiden fehlenden Bausteine eines
# "richtigen" Editors (Konzept-Erweiterung "Bearbeitungsbereich") — z. B. um
# eine Lücke zu schließen, die ein Sensor-Aussetzer hinterlassen hat, oder um
# einen einzelnen, offensichtlich falschen Messwert zu korrigieren, ohne ihn
# erst löschen und den richtigen Wert separat nachtragen zu müssen. Beide
# respektieren dieselbe Zwei-Speicherorte-Aufteilung wie alles andere in
# diesem Modul: der laufende Monat (Hot Buffer, reines CSV) wird direkt
# angefasst, ein bereits archivierter Monat (Parquet, unveränderlich) wird
# komplett neu geschrieben plus die zugehörigen Rollup-Zeilen neu berechnet
# (derselbe Ablauf wie purge_archived_months() oben).


def _rewrite_archive_month(
    data_dir: Path, index: Index, entity_id: str, aggregation_type: str, rows: list[tuple[float, float]],
    year: int, month: int, tz: ZoneInfo,
) -> None:
    """Schreibt einen archivierten Monat komplett neu aus `rows` (bereits
    sortiert, inkl. der Änderung) und berechnet die Rollup-Zeilen dieses
    Monats neu — gemeinsam von add_raw_value()/correct_raw_value() genutzt,
    dieselbe atomare tmp-Datei-plus-rename-Technik wie überall sonst in
    diesem Modul, damit ein Absturz mittendrin nie eine halb geschriebene
    Archivdatei hinterlässt."""
    archive_path = entity_dir(data_dir, "archive", entity_id) / f"{year:04d}-{month:02d}.parquet"
    old_size = archive_path.stat().st_size if archive_path.exists() else 0
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]})
    tmp_path = archive_path.with_suffix(".tmp")
    pq.write_table(table, tmp_path, compression="zstd")
    tmp_path.replace(archive_path)
    rollup.replace_month(data_dir, entity_id, aggregation_type, table, year, month, tz)
    new_size = archive_path.stat().st_size
    index.add_size_bytes(entity_id, new_size - old_size)


def add_raw_value(
    data_dir: Path, index: Index, entity_id: str, ts: float, value: float, tz: ZoneInfo, now: datetime | None = None
) -> None:
    """Fügt einen einzelnen Rohwert nachträglich ein. Landet je nach Zeitpunkt
    entweder im Hot Buffer (laufender Monat, reiner CSV-Anhang — die Zeile
    muss dafür nicht einmal chronologisch letzte sein, list_raw_rows() sortiert
    beim Lesen ohnehin) oder in einem bereits archivierten Monat (Parquet
    komplett neu geschrieben, siehe _rewrite_archive_month()). Wirft
    ValueError bei unbekannter Entität — der Aufrufer (main.py) prüft das
    zwar meist schon vorher, hier trotzdem defensiv, weil aggregation_type
    für den Archiv-Zweig gebraucht wird."""
    now = now or datetime.now(tz)
    entity = index.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"Unbekannte Entität: {entity_id}")
    now_month_key = now.strftime("%Y-%m")
    ts_dt = datetime.fromtimestamp(ts, tz)
    ts_month_key = ts_dt.strftime("%Y-%m")

    if ts_month_key == now_month_key:
        hot_append(data_dir, entity_id, ts, value, tz)
    else:
        archive_path = entity_dir(data_dir, "archive", entity_id) / f"{ts_month_key}.parquet"
        if archive_path.exists():
            table = pq.read_table(archive_path)
            rows = list(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))
        else:
            rows = []
        rows.append((ts, value))
        rows.sort()
        _rewrite_archive_month(
            data_dir, index, entity_id, entity["aggregation_type"], rows, ts_dt.year, ts_dt.month, tz
        )

    index.add_row_count(entity_id, 1)
    index.bump_ts_bounds(entity_id, ts, value)


def correct_raw_value(
    data_dir: Path, index: Index, entity_id: str, ts: float, old_value: float, new_value: float, tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """Ändert den Wert EINES vorhandenen Rohwerts, ohne dessen Zeitstempel zu
    verschieben. Bei mehreren Vorkommen desselben Zeitstempels (Duplikate)
    trifft es gezielt das erste Vorkommen mit exakt old_value, alle anderen
    bleiben unangetastet — dieselbe Zeile, die die Bereinigungs-Tabelle dem
    Nutzer als (ts, formatted_value) anzeigt, ist damit eindeutig
    identifiziert. Gibt False zurück, wenn keine passende Zeile gefunden
    wurde (nichts geändert, kein Fehler — z. B. wenn der Wert zwischen Laden
    der Seite und Klick anderweitig schon geändert wurde)."""
    now = now or datetime.now(tz)
    entity = index.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"Unbekannte Entität: {entity_id}")
    now_month_key = now.strftime("%Y-%m")
    ts_dt = datetime.fromtimestamp(ts, tz)
    ts_month_key = ts_dt.strftime("%Y-%m")

    def _replace_first_match(rows: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], bool]:
        changed = False
        result = []
        for row_ts, row_value in rows:
            if not changed and row_ts == ts and row_value == old_value:
                result.append((row_ts, new_value))
                changed = True
            else:
                result.append((row_ts, row_value))
        return result, changed

    if ts_month_key == now_month_key:
        path = hot_path(data_dir, entity_id, ts, tz)
        rows = read_rows(path)
        new_rows, changed = _replace_first_match(rows)
        if not changed:
            return False
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for row_ts, row_value in new_rows:
                handle.write(f"{row_ts},{row_value}\n")
        tmp_path.replace(path)
    else:
        archive_path = entity_dir(data_dir, "archive", entity_id) / f"{ts_month_key}.parquet"
        if not archive_path.exists():
            return False
        table = pq.read_table(archive_path)
        rows = sorted(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))
        new_rows, changed = _replace_first_match(rows)
        if not changed:
            return False
        _rewrite_archive_month(
            data_dir, index, entity_id, entity["aggregation_type"], new_rows, ts_dt.year, ts_dt.month, tz
        )

    return True
