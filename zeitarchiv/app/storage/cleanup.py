"""Bereinigungs-Werkzeug: Rohdaten lesen, Ausreißer/Lücken/Duplikate/Wiederholungen/
Zählerrückgänge markieren,
nie destruktiv löschen (Konzept Abschnitt 04).

Löschen ist ein Soft-Delete über index.deleted_points — Zeitstempel werden aus
allen Ansichten rausgefiltert. Ein Purge (purge_hot_buffer() für den
laufenden Monat, purge_archived_months() für bereits archivierte Monate)
entfernt sie danach auch physisch — per Klick in den Einstellungen, oder
automatisch im Hintergrund ab einem eingestellten Mindestalter der Markierung
(``older_than``, standardmäßig aus, siehe background.py).
"""

from __future__ import annotations

import statistics
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from ..formatting import decimals_to_int, format_value
from . import hotbuffer, rollup
from . import resolution as resolution_mod
from .hotbuffer import append as hot_append
from .hotbuffer import hot_path, month_key, read_rows
from .index import Index, filter_deleted_occurrences, resolution_seconds, should_accept_value
from .paths import entity_dir


def _format_val(value: float, decimals: str) -> str:
    return format_value(value, decimals_to_int(decimals))


def _format_ts(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y %H:%M:%S")


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


# Kennung der Ausreißer-REGEL, nicht der Schwelle. Sie steht in jedem
# gespeicherten Zählergebnis (index.set_cleanup_alltime_stats) und muss
# hochgezählt werden, sobald OutlierDetector anders rechnet.
#
# Der Anlass war ein echter Fehlanzeige: Die Umstellung von Prozent auf
# Vielfache hat die Schwellen "10", "50" und "100" auf ihrem Schlüssel gelassen
# (aus "50 %" wurde "50×"). Ein vor der Umstellung berechnetes Ergebnis passte
# damit weiterhin zur eingestellten Schwelle, und Housekeeping zeigte es als
# aktuelle Quote — gemessen: 2.406 von 52.194 markierten Werten an einer
# Entität, an der die neue Regel 0 findet. Die Schwelle allein kann eine
# Zählung also nicht ausweisen; erst Schwelle UND Regel zusammen tun das.
OUTLIER_RULE_VERSION = 2

# Ausreißer-Erkennung: Fenstergrößen der beiden Regeln (Begründung und
# Messwerte siehe OutlierDetector).
OUTLIER_WINDOW = 15
OUTLIER_MIN_VALUES = 5
COUNTER_OUTLIER_WINDOW = 50
COUNTER_OUTLIER_MIN_STEPS = 5


def _format_factor(factor: float) -> str:
    """Vielfaches für den Markierungsgrund. Unter 10 mit einer Nachkommastelle,
    darüber gerundet mit Tausenderpunkten — ein Ziffernfehler ergibt schnell
    siebenstellige Vielfache, und "56941875,0×" liest niemand."""
    if factor < 10:
        return f"{factor:.1f}".replace(".", ",") + "×"
    return f"{factor:,.0f}".replace(",", ".") + "×"


class OutlierDetector:
    """Die EINZIGE Ausreißer-Regel der App — beide Seitenpfade der
    Bereinigungs-/Korrektur-Ansicht und die Gesamt-Statistik speisen ihre
    Werte hier hinein (siehe analyze_raw_rows_page und detect_outliers).

    Gesucht sind unplausible Werte: Übertragungsfehler, Sensoraussetzer,
    verrutschte Ziffern. Die Schwelle ist deshalb ein VIELFACHES des für diese
    Entität Üblichen, kein Prozentsatz eines Werts. Ein Prozentsatz war die
    Vorgängerfassung und ist an derselben Stelle gescheitert: 5 % sind bei
    einem frischen Zähler (Stand 12) 0,6 und bei einem alten (Stand 1.200.000)
    60.000 — dieselbe Einstellung bedeutet auf zwei Zählern etwas völlig
    anderes, obwohl beide dasselbe messen.

    ``"counter"`` — Zähler (Verbrauch, Erzeugung, alles stetig steigende)
        Bezug ist der ZUWACHS, nicht der Stand: Δ > Faktor × Median der
        letzten ``COUNTER_OUTLIER_WINDOW`` positiven Zuwächse.

        Gemessen an einem echten Stromzähler (3.890 Zuwächse, 30 Tage): der
        größte ECHTE Zuwachs liegt beim 10,1-fachen des Medians, ein
        Ziffernfehler 10.123 → 101.230 beim 56.941.875-fachen. Zwischen beidem
        liegen sechs Größenordnungen, in denen jede Schwelle sitzen kann; 50×
        und 100× markierten auf gesunden Daten nichts und den eingebauten
        Fehler zuverlässig. Und der Punkt, um den es geht: derselbe Fehler bei
        den Ständen 12, 10.123 und 1.200.000 ergibt jeweils dasselbe Ergebnis.

        Negative Zuwächse gehen weder in den Bezug ein noch werden sie
        markiert — dafür gibt es die eigene Markierung "Zählerrückgang".

    ``"standard"`` — alle übrigen Sensoren
        Werte sind hier additiv und dürfen negativ sein, ein Vielfaches des
        Werts trägt also nicht. Bezug ist der robuste Abstand:
        |Wert − Median| > Faktor × MAD (Median der absoluten Abweichungen)
        über die letzten ``OUTLIER_WINDOW`` Werte.

        Das ist skalen- UND nullpunktunabhängig: dieselbe Kurve in °C, in
        Kelvin und um null herum ergibt exakt dieselben Markierungen — was
        die Prozentfassung nicht konnte (ein Sprung 20 → 60 sind in °C 200 %,
        in Kelvin 13,6 %).

    Was die Regel NICHT kann: ist der MAD null (15 identische Werte in Folge),
    gibt es kein "üblich", an dem sich ein Vielfaches messen ließe — dann wird
    übersprungen statt geraten. Dasselbe gilt für einen stillstehenden Zähler.
    """

    def __init__(
        self, *, factor: float | None, mode: str, decimals: str, tz: ZoneInfo
    ) -> None:
        self.factor = factor
        self.mode = "counter" if mode == "counter" else "standard"
        self.decimals = decimals
        self.tz = tz
        self._values: deque[float] = deque(maxlen=OUTLIER_WINDOW)
        self._steps: deque[float] = deque(maxlen=COUNTER_OUTLIER_WINDOW)
        self._previous: float | None = None
        self._previous_ts: float | None = None

    def check(self, ts: float, value: float) -> str | None:
        """Begründung, wenn `value` ein Ausreißer ist — sonst None. Muss für
        JEDEN Wert in zeitlicher Reihenfolge aufgerufen werden, auch wenn die
        Erkennung aus ist: die Aufrufe bilden den Bezug."""
        if self.mode == "counter":
            grund = self._check_counter(value)
        else:
            grund = self._check_standard(value)
        self._previous = value
        self._previous_ts = ts
        return grund

    def _vorwert(self) -> str:
        """Der unmittelbar vorhergehende Wert mit Zeitpunkt, als Nachsatz jeder
        Begründung. Die Kennzahl bezieht sich auf ein Fenster, nicht auf den
        Vorwert — aber die erste Frage vor der Zeile lautet trotzdem "und was
        stand vorher da?", und ohne ihn muss man dafür die Markierung
        wegklicken und in der Liste nachsehen. Derselbe Aufbau wie bei Lücken
        und Wiederholungen ("… Vorwert X um TT.MM.JJJJ hh:mm:ss")."""
        if self._previous is None or self._previous_ts is None:
            return ""
        return (
            f" — Vorwert {_format_val(self._previous, self.decimals)} "
            f"um {_format_ts(self._previous_ts, self.tz)}"
        )

    def _check_counter(self, value: float) -> str | None:
        if self._previous is None:
            return None
        zuwachs = value - self._previous
        if zuwachs <= 0:
            return None
        grund = None
        if self.factor is not None and len(self._steps) >= COUNTER_OUTLIER_MIN_STEPS:
            ueblich = statistics.median(self._steps)
            if ueblich > 0 and zuwachs > self.factor * ueblich:
                grund = (
                    f"Zuwachs {_format_val(zuwachs, self.decimals)} ist das "
                    f"{_format_factor(zuwachs / ueblich)} des üblichen Zuwachses "
                    f"({_format_val(ueblich, self.decimals)}, Median der letzten "
                    f"{len(self._steps)}){self._vorwert()}"
                )
        self._steps.append(zuwachs)
        return grund

    def _check_standard(self, value: float) -> str | None:
        grund = None
        if self.factor is not None and len(self._values) >= OUTLIER_MIN_VALUES:
            mitte = statistics.median(self._values)
            streuung = statistics.median([abs(v - mitte) for v in self._values])
            abstand = abs(value - mitte)
            if streuung > 0 and abstand > self.factor * streuung:
                grund = (
                    f"{_format_val(value, self.decimals)} liegt "
                    f"{_format_factor(abstand / streuung)} weiter vom Median der letzten "
                    f"{len(self._values)} Werte ({_format_val(mitte, self.decimals)}) "
                    f"entfernt als üblich (±{_format_val(streuung, self.decimals)})"
                    f"{self._vorwert()}"
                )
        self._values.append(value)
        return grund


class ResultLimitExceeded(ValueError):
    """Eine Abfrage würde mehr Zeilen als erlaubt materialisieren."""


def analyze_raw_rows_page(
    rows_factory: Callable[[], Iterator[tuple[float, float]]],
    *,
    filter_: str,
    page: int,
    page_size: int,
    gap_threshold_minutes: float | None,
    outlier_factor: float | None,
    tz: ZoneInfo,
    decimals: str = "auto",
    counter_decrease_enabled: bool = False,
    outlier_mode: str = "standard",
) -> dict:
    """Analysiert beliebig viele sortierte Rohwerte mit begrenztem Speicher.

    Der erste Durchlauf bestimmt die Anzahl. Der zweite berechnet Markierungen
    und behält nur so viele der neuesten Treffer, wie für die angeforderte
    Seite nötig sind. Damit funktioniert insbesondere der Zeitraum "Gesamt"
    auch oberhalb des UI-Materialisierungslimits.

    ``outlier_factor`` ist ein VIELFACHES des für diese Entität Üblichen (nicht
    mehr ein Prozentsatz), ``outlier_mode`` wählt die Bezugsgröße dafür —
    beides steckt vollständig in OutlierDetector, siehe dort.
    """
    total_rows = 0
    for _ts, _value in rows_factory():
        total_rows += 1

    page_size = max(1, min(int(page_size), 1000))
    upper_page = max(1, -(-total_rows // page_size))
    requested_page = max(1, min(int(page), upper_page))
    retained: deque[dict] = deque(maxlen=requested_page * page_size)
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
    # Ausreißer-Erkennung, zwei Regeln je nach Entitätstyp — dieselbe Klasse,
    # die auch der nicht-streamende Pfad über detect_outliers() benutzt.
    outlier_detector = OutlierDetector(
        factor=outlier_factor, mode=outlier_mode, decimals=decimals, tz=tz
    )

    selected_filter = filter_ if filter_ in {
        "all", "outliers", "gaps", "duplicates", "repetitions", "counter_decreases"
    } else "all"

    def flush_group() -> None:
        nonlocal total_matches, group_rows
        if not group_rows:
            return
        duplicate_reason = None
        if len(group_rows) > 1:
            formatted_values = " / ".join(
                _format_val(row["value"], decimals) for row in group_rows
            )
            duplicate_reason = f"{len(group_rows)}× derselbe Zeitstempel — Werte: {formatted_values}"
        if group_outlier is not None:
            counts["outliers"] += 1
        if group_gap is not None:
            counts["gaps"] += 1
        if duplicate_reason is not None:
            counts["duplicates"] += len(group_rows)

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
                    f"{_format_val(previous_value, decimals)} um {_format_ts(previous_ts, tz)} "
                    f"(Schwellwert: {_format_duration(gap_seconds)})"
                )
        # Immer aufrufen, auch bei ausgeschalteter Erkennung: der Detektor baut
        # dabei seinen Bezug auf. Ein bereits markierter Zeitstempel bleibt
        # markiert — bei mehreren Werten auf derselben Sekunde genügt einer.
        outlier_reason = outlier_detector.check(ts, value)
        if outlier_reason is not None and group_outlier is None:
            group_outlier = outlier_reason

        if should_accept_value(
            "decimals", decimals, last_kept_value, last_kept_ts, value, ts
        ):
            last_kept_ts, last_kept_value = ts, value
            repetition_reason = None
        else:
            repetition_reason = _repetition_reason(decimals, last_kept_ts, last_kept_value, tz)

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
    hot_rows_loader: Callable[[Path], list[tuple[float, float]]] | None = None,
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
            batches = [sorted((hot_rows_loader or read_rows)(path))]
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


def detect_duplicates(
    rows: list[tuple[float, float]], decimals: str = "auto"
) -> dict[float, str]:
    """Gibt je doppelt vorkommendem Zeitstempel eine Begründung zurück (Anzahl der
    Vorkommen plus alle betroffenen Werte) — die Rückgabe verhält sich wie ein Set
    (Mitgliedschaft/Iteration über die Keys), liefert für die Anzeige aber
    zusätzlich den Grund."""
    values_by_ts: dict[float, list[float]] = {}
    for ts, value in rows:
        values_by_ts.setdefault(ts, []).append(value)
    return {
        ts: (
            f"{len(values)}× derselbe Zeitstempel — Werte: "
            + " / ".join(_format_val(v, decimals) for v in values)
        )
        for ts, values in values_by_ts.items()
        if len(values) > 1
    }


def _repetition_reason(
    decimals: str, last_kept_ts: float, last_kept_value: float, tz: ZoneInfo
) -> str:
    precision = "3 (Automatisch)" if decimals == "auto" else decimals
    return (
        f"Gleicher gerundeter Folgewert bei {precision} Nachkommastellen — "
        f"wie Vorwert {_format_val(last_kept_value, decimals)} um {_format_ts(last_kept_ts, tz)}"
    )


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
    rows: list[tuple[float, float]], decimals: str, tz: ZoneInfo
) -> dict[float, str]:
    """Markiert die von ``repeated_rows_to_delete`` erkannten Zeilen, inkl. Bezug
    auf den beibehaltenen Vorwert, gegen den gerundet verglichen wurde."""
    reasons: dict[float, str] = {}
    last_kept_ts: float | None = None
    last_kept_value: float | None = None
    for ts, value in rows:
        if should_accept_value(
            "decimals", decimals, last_kept_value, last_kept_ts, value, ts
        ):
            last_kept_ts, last_kept_value = ts, value
        else:
            reasons[ts] = _repetition_reason(decimals, last_kept_ts, last_kept_value, tz)
    return reasons


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


def iter_duplicate_rows_to_delete(
    rows: Iterable[tuple[float, float]],
) -> Iterator[tuple[float, float]]:
    """Streaming-Variante von duplicate_rows_to_delete() mit konstantem statt mit
    der Zeilenzahl wachsendem Speicherbedarf: da rows nach Zeitstempel sortiert
    sind (list_raw_rows/iter_raw_rows), liegen alle Vorkommen desselben
    Zeitstempels direkt hintereinander — ein Vergleich mit nur dem Vorwert
    genügt, statt sich alle bisher gesehenen Zeitstempel in einem Set zu merken."""
    previous_ts: float | None = None
    for ts, value in rows:
        if ts == previous_ts:
            yield ts, value
        else:
            previous_ts = ts


def duplicate_rows_to_delete(rows: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Für "Duplikate automatisch entfernen" (Konzept Abschnitt 04): bei jedem
    mehrfach vorkommenden Zeitstempel bleibt genau EIN Vorkommen erhalten (das
    zeitlich erste in rows), alle weiteren werden zum Löschen vorgeschlagen.
    rows muss dieselbe stabile Reihenfolge haben wie beim tatsächlichen Löschen
    (list_raw_rows liefert bereits sortiert), sonst könnte hier ein anderes
    Vorkommen ausgewählt werden als später tatsächlich entfernt wird."""
    return list(iter_duplicate_rows_to_delete(rows))


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
            results.append({
                "entity_id": entity_id,
                "friendly_name": entity["custom_name"] or entity["friendly_name"],
                "count": count,
            })
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def detect_gaps(
    rows: list[tuple[float, float]],
    threshold_minutes: float | None,
    decimals: str,
    tz: ZoneInfo,
) -> dict[float, str]:
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
        prev_ts, prev_value = rows[i]
        delta = rows[i + 1][0] - prev_ts
        if delta > threshold_seconds:
            flagged[rows[i + 1][0]] = (
                f"{_format_duration(delta)} seit vorherigem Wert "
                f"{_format_val(prev_value, decimals)} um {_format_ts(prev_ts, tz)} "
                f"(Schwellwert: {_format_duration(threshold_seconds)})"
            )
    return flagged


def detect_outliers(
    rows: list[tuple[float, float]],
    factor: float | None,
    decimals: str,
    tz: ZoneInfo,
    mode: str = "standard",
) -> dict[float, str]:
    """Ausreißer einer bereits materialisierten Zeilenliste (kurze Zeiträume der
    Bereinigungs-/Korrektur-Ansicht, die nicht über den Streaming-Pfad laufen).

    Bewusst nur eine Hülle um OutlierDetector: hier stand früher eine ZWEITE,
    anders rechnende Regel, wodurch dieselbe Entität je nach gewähltem Zeitraum
    unterschiedlich viele Ausreißer zeigte (kurze Zeiträume: Sprung zum Vorwert,
    gemessen am Mittelwert der Beträge — "Jahr"/"Gesamt": die Regel von
    analyze_raw_rows_page). Es gibt jetzt genau eine Regel; die beiden Pfade
    unterscheiden sich nur noch darin, ob alle Zeilen im Speicher stehen.

    factor=None ("Aus" in der Konfiguration) liefert immer {}."""
    if factor is None or len(rows) < 2:
        return {}
    detector = OutlierDetector(factor=factor, mode=mode, decimals=decimals, tz=tz)
    flagged: dict[float, str] = {}
    for ts, value in rows:
        reason = detector.check(ts, value)
        if reason is not None and ts not in flagged:
            flagged[ts] = reason
    return flagged


def soft_delete(index: Index, entity_id: str, timestamps: list[float]) -> None:
    index.mark_deleted(entity_id, timestamps)


def undo_last_delete(index: Index, entity_id: str) -> int:
    """Macht die zuletzt gelöschte Charge rückgängig (ein Klick = ein 'Löschen'-Vorgang
    rückgängig, wie im Konzept-Mockup "Rückgängig" beschrieben)."""
    return index.undo_last_deleted_batch(entity_id)


def _group_by_month(ts_counts: dict[float, int], tz: ZoneInfo) -> dict[str, dict[float, int]]:
    """Gruppiert EINMAL nach Kalendermonat (dieselbe "YYYY-MM"-Zuordnung wie
    hotbuffer.month_key()/die Archiv-Dateinamen "YYYY-MM.parquet") — Grundlage
    für einen O(1)-Nachschlag je Archiv-Monat (path.stem direkt als Schlüssel)
    statt eines erneuten linearen Scans über ALLE markierten Zeitstempel für
    JEDEN einzelnen Archiv-Monat. Bei einer Entität mit sehr vielen markierten
    Löschungen UND vielen Archiv-Monaten (z. B. 620.000 Zeilen × 151 Monate)
    summierte sich das bisher zu zig Millionen Vergleichen und machte allein
    die (rein lesende!) Einstellungen-Vorschau mehrere Sekunden langsam."""
    by_month: dict[str, dict[float, int]] = {}
    for ts, count in ts_counts.items():
        by_month.setdefault(month_key(ts, tz), {})[ts] = count
    return by_month


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
            # Nur Monate öffnen, die überhaupt eine markierte Zeile enthalten
            # können (wie purge_archived_months()) — sonst würde jede
            # Vorschau alle Archiv-Monate der Entität vollständig lesen, auch
            # wenn nur ein einzelner Monat betroffen ist (ZP-005 in
            # PERFORMANCE.md). Einmal gruppiert statt pro Monat neu gescannt,
            # siehe _group_by_month().
            remaining_by_month = _group_by_month(remaining, tz)
            for path in sorted(archive_dir.glob("*.parquet")):
                if path.stem not in remaining_by_month:
                    continue
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


def purge_hot_buffer(
    data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None, older_than: float | None = None
) -> int:
    """Entfernt weich gelöschte Vorkommen physisch aus dem Hot Buffer (laufender
    Monat, unkomprimiertes CSV) — der laufende Monat hat keine Rollup-Datei,
    seine Aggregation wird bei jeder Abfrage ohnehin live aus dem Hot Buffer
    berechnet (siehe query.py), ein Purge hier ist deshalb ein reiner
    CSV-Rewrite ohne Rollup-Folgeaufwand. Für bereits archivierte Monate siehe
    purge_archived_months() (Parquet-Rewrite + Rollup-Neuberechnung).

    ``older_than`` (siehe Index.get_deleted_counts_for_entity()) beschränkt auf
    Markierungen, die mindestens so alt sind — für die automatische
    Bereinigung (background.py). Der manuelle Purge lässt es weg.

    Gibt die Anzahl tatsächlich physisch entfernter Zeilen zurück."""
    now = now or datetime.now(tz)
    current_month_start = datetime(now.year, now.month, 1, tzinfo=tz).timestamp()
    purged_total = 0
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        deleted = index.get_deleted_counts_for_entity(entity_id, older_than=older_than)
        relevant = {ts: count for ts, count in deleted.items() if ts >= current_month_start}
        if not relevant:
            continue
        path = hot_path(data_dir, entity_id, now.timestamp(), tz)
        records = hotbuffer.read_full_rows(path)
        if not records:
            continue
        remaining = dict(relevant)
        kept_records: list[hotbuffer.HotRecord] = []
        removed_timestamps: list[float] = []
        for record in records:
            ts = record[0]
            if remaining.get(ts, 0) > 0:
                remaining[ts] -= 1
                removed_timestamps.append(ts)
            else:
                kept_records.append(record)
        if not removed_timestamps:
            continue
        hotbuffer.write_records(path, kept_records)
        index.remove_deleted_points(entity_id, removed_timestamps, older_than=older_than)
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


def purge_archived_months(
    data_dir: Path,
    index: Index,
    tz: ZoneInfo,
    now: datetime | None = None,
    on_month: Callable[[int, str], None] | None = None,
    older_than: float | None = None,
) -> dict:
    """Entfernt weich gelöschte Vorkommen physisch aus bereits archivierten
    Monaten — schreibt die betroffene Parquet-Datei ohne die gelöschten
    Zeilen neu (Rest des Monats unverändert) und berechnet die zugehörigen
    Rollup-Zeilen (fein/Monat, bei Zähler-Entitäten ggf. ein bereits
    berechnetes Jahr) über rollup.replace_month() passend neu. Ergänzt
    purge_hot_buffer() um den bisher fehlenden Teil (Konzept, "Offene
    Punkte") — eine echte Archivdatei wird angefasst, deshalb läuft das immer
    unter der globalen Sperre (coordinator.exclusive()), egal ob der Aufrufer
    der manuelle Button oder die automatische Bereinigung (background.py) ist.

    ``older_than`` (siehe Index.get_deleted_counts_for_entity()) beschränkt auf
    Markierungen, die mindestens so alt sind — nur von der Automatik gesetzt,
    damit "Rückgängig" für eine gerade erst markierte Charge nicht ins Leere
    läuft. Der manuelle Purge lässt es weg und sieht wie bisher alles.

    ``on_month`` wird nach jedem tatsächlich neu geschriebenen Monat mit
    (Anzahl bisher, "entity_id 2024-03") gerufen — die Fortschrittsanzeige der
    Einstellungen hängt daran. Gemessen an einem echten Bestand entfallen von
    rund 20 Sekunden Gesamtdauer 15,6 auf die Rollup-Neuberechnung über 163
    Monate, der Zähler läuft also fein genug, um überhaupt etwas zu zeigen.
    Bewusst nur hier und nicht in purge_hot_buffer(): der Hot Buffer ist eine
    CSV je Entität für den laufenden Monat und in Bruchteilen einer Sekunde
    durch.

    Gibt eine Zusammenfassung zurück (rows_purged, months_purged)."""
    now = now or datetime.now(tz)
    rows_purged = 0
    months_purged = 0
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        aggregation_type = entity["aggregation_type"]
        hourly_rollup = bool(entity["hourly_rollup"])
        deleted = index.get_deleted_counts_for_entity(entity_id, older_than=older_than)
        if not deleted:
            continue
        archive_dir = entity_dir(data_dir, "archive", entity_id)
        if not archive_dir.exists():
            continue
        entity_had_emptied_month = False
        # Einmal gruppiert statt für jeden Archiv-Monat erneut über ALLE
        # markierten Zeitstempel dieser Entität zu scannen, siehe
        # _group_by_month().
        deleted_by_month = _group_by_month(deleted, tz)
        for path in sorted(archive_dir.glob("*.parquet")):
            relevant = deleted_by_month.get(path.stem)
            if not relevant:
                continue
            year_str, month_str = path.stem.split("-")
            year, month = int(year_str), int(month_str)

            rows = sorted(_read_archive_month_full(path), key=lambda r: (r[0], r[1]))
            kept = filter_deleted_occurrences(rows, relevant)
            removed = len(rows) - len(kept)
            if removed == 0:
                continue

            old_size = path.stat().st_size
            if kept:
                kept_table = pa.table({
                    "ts": [r[0] for r in kept],
                    "value": [r[1] for r in kept],
                    "min_value": [r[2] for r in kept],
                    "max_value": [r[3] for r in kept],
                })
                tmp_path = path.with_suffix(".tmp")
                pq.write_table(kept_table, tmp_path, compression="zstd")
                tmp_path.replace(path)
                rollup.replace_month(
                    data_dir, entity_id, aggregation_type, kept_table, year, month, tz,
                    hourly_rollup=hourly_rollup,
                )
                new_size = path.stat().st_size
            else:
                # Jeder Rohwert dieses Monats war weich gelöscht — Archivdatei
                # und zugehörige Rollup-Zeilen komplett entfernen statt eine
                # leere Parquet-Datei/eine Monats-Zeile ohne Grundlage zu behalten.
                path.unlink()
                rollup.remove_month(
                    data_dir, entity_id, aggregation_type, year, month, tz, hourly_rollup=hourly_rollup
                )
                new_size = 0
                entity_had_emptied_month = True

            index.add_row_count(entity_id, -removed)
            index.add_size_bytes(entity_id, new_size - old_size)
            removed_timestamps = [ts for ts, count in relevant.items() for _ in range(count)]
            index.remove_deleted_points(entity_id, removed_timestamps, older_than=older_than)

            rows_purged += removed
            months_purged += 1
            if on_month is not None:
                on_month(months_purged, f"{entity_id} {path.stem}")

        if entity_had_emptied_month:
            _update_first_ts_after_archive_purge(data_dir, index, entity_id, tz, now)

    return {"rows_purged": rows_purged, "months_purged": months_purged}


def read_values_for_timestamps(
    data_dir: Path, entity_id: str, timestamps: list[float], tz: ZoneInfo, now: datetime | None = None
) -> dict[float, float]:
    """Liest den Rohwert zu einer Menge bestimmter Zeitstempel einer Entität —
    rein lesend, für die Detailansicht markierter Datensätze (Housekeeping →
    Speicherplatz → "Markierte Datensätze anzeigen"): ein weich gelöschter
    Zeitstempel wird aus allen normalen Ansichten rausgefiltert (siehe
    Modul-Docstring), sein Wert steht sonst nirgends mehr. Liest NUR die
    Monate, die unter den übergebenen Zeitstempeln tatsächlich vorkommen —
    bei einer paginierten Detailseite (20-200 Zeilen) sind das meist nur eine
    Handvoll Dateien, nie die komplette Historie einer Entität.

    Ein Zeitstempel ohne Treffer (Rohdaten inzwischen anderweitig entfernt,
    z. B. durch einen bereits erfolgten Purge) fehlt im Ergebnis-dict statt
    mit None aufzutauchen — der Aufrufer zeigt dafür „—"."""
    now = now or datetime.now(tz)
    by_month = _group_by_month({ts: 1 for ts in timestamps}, tz)
    current_month = month_key(now.timestamp(), tz)
    values: dict[float, float] = {}
    for month, wanted in by_month.items():
        if month == current_month:
            path = hot_path(data_dir, entity_id, now.timestamp(), tz)
            if not path.exists():
                continue
            for ts, value in read_rows(path):
                if ts in wanted:
                    values[ts] = value
        else:
            archive_path = entity_dir(data_dir, "archive", entity_id) / f"{month}.parquet"
            if not archive_path.exists():
                continue
            table = pq.read_table(archive_path, columns=["ts", "value"])
            for ts, value in zip(table.column("ts").to_pylist(), table.column("value").to_pylist()):
                if ts in wanted:
                    values[ts] = value
    return values


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
    data_dir: Path, index: Index, entity_id: str, aggregation_type: str,
    rows: list[tuple[float, float, float | None, float | None]],
    year: int, month: int, tz: ZoneInfo, hourly_rollup: bool = False,
) -> None:
    """Schreibt einen archivierten Monat komplett neu aus `rows` (ts, value,
    min_value, max_value — bereits sortiert, inkl. der Änderung) und
    berechnet die Rollup-Zeilen dieses Monats neu — gemeinsam von
    add_raw_value()/correct_raw_value() genutzt, dieselbe atomare
    tmp-Datei-plus-rename-Technik wie überall sonst in diesem Modul, damit
    ein Absturz mittendrin nie eine halb geschriebene Archivdatei
    hinterlässt. min_value/max_value müssen mitgeführt werden, sonst würde
    jede Korrektur/jedes Hinzufügen in einem Monat, der Standard-
    Auflösungs-Zeilen (Ø je Zeitfenster) enthält, deren Min/Max-Spalten für
    alle unberührten Zeilen des Monats stillschweigend löschen."""
    archive_path = entity_dir(data_dir, "archive", entity_id) / f"{year:04d}-{month:02d}.parquet"
    old_size = archive_path.stat().st_size if archive_path.exists() else 0
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "ts": [r[0] for r in rows],
        "value": [r[1] for r in rows],
        "min_value": [r[2] for r in rows],
        "max_value": [r[3] for r in rows],
    })
    tmp_path = archive_path.with_suffix(".tmp")
    pq.write_table(table, tmp_path, compression="zstd")
    tmp_path.replace(archive_path)
    rollup.replace_month(
        data_dir, entity_id, aggregation_type, table, year, month, tz, hourly_rollup=hourly_rollup
    )
    new_size = archive_path.stat().st_size
    index.add_size_bytes(entity_id, new_size - old_size)


def _read_archive_month_full(
    archive_path: Path,
) -> list[tuple[float, float, float | None, float | None]]:
    """Liest einen archivierten Monat als (ts, value, min_value, max_value).
    Ältere Archivdateien (vor der Standard-Auflösung) kennen die beiden
    letzten Spalten noch nicht — dann werden sie als None aufgefüllt, statt
    dass das Lesen mit KeyError abbricht."""
    table = pq.read_table(archive_path)
    ts_col = table.column("ts").to_pylist()
    value_col = table.column("value").to_pylist()
    min_col = (
        table.column("min_value").to_pylist()
        if "min_value" in table.column_names
        else [None] * len(ts_col)
    )
    max_col = (
        table.column("max_value").to_pylist()
        if "max_value" in table.column_names
        else [None] * len(ts_col)
    )
    return list(zip(ts_col, value_col, min_col, max_col))


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
        rows = _read_archive_month_full(archive_path) if archive_path.exists() else []
        # Manuell nachgetragener Wert ist kein Ø aus mehreren Rohwerten.
        rows.append((ts, value, None, None))
        # Nur nach (ts, value) sortieren, nicht per Tupel-Vergleich über alle
        # vier Felder — bei exakten (ts, value)-Duplikaten würde das sonst
        # None mit einem float vergleichen (TypeError) statt einfach die
        # bestehende Reihenfolge der Duplikate beizubehalten.
        rows.sort(key=lambda r: (r[0], r[1]))
        _rewrite_archive_month(
            data_dir, index, entity_id, entity["aggregation_type"], rows, ts_dt.year, ts_dt.month, tz,
            hourly_rollup=bool(entity["hourly_rollup"]),
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

    # Trifft der Vergleich die Zeile, die gerade korrigiert wird, werden
    # min_value/max_value mitgelöscht: sie gehörten zum alten (Ø-)Wert, der
    # jetzt durch eine bewusste manuelle Korrektur ersetzt wird — der neue
    # Wert ist kein Ø aus mehreren Rohwerten mehr.
    def _replace_first_match_full(
        rows: list[tuple[float, float, float | None, float | None]],
    ) -> tuple[list[tuple[float, float, float | None, float | None]], bool]:
        changed = False
        result = []
        for row_ts, row_value, min_value, max_value in rows:
            if not changed and row_ts == ts and row_value == old_value:
                result.append((row_ts, new_value, None, None))
                changed = True
            else:
                result.append((row_ts, row_value, min_value, max_value))
        return result, changed

    if ts_month_key == now_month_key:
        path = hot_path(data_dir, entity_id, ts, tz)
        records = hotbuffer.read_full_rows(path)
        changed = False
        new_records: list[hotbuffer.HotRecord] = []
        for row_ts, row_value, event_id, min_value, max_value in records:
            if not changed and row_ts == ts and row_value == old_value:
                new_records.append((row_ts, new_value, event_id, None, None))
                changed = True
            else:
                new_records.append((row_ts, row_value, event_id, min_value, max_value))
        if not changed:
            return False
        hotbuffer.write_records(path, new_records)
    else:
        archive_path = entity_dir(data_dir, "archive", entity_id) / f"{ts_month_key}.parquet"
        if not archive_path.exists():
            return False
        rows = sorted(_read_archive_month_full(archive_path), key=lambda r: (r[0], r[1]))
        new_rows, changed = _replace_first_match_full(rows)
        if not changed:
            return False
        _rewrite_archive_month(
            data_dir, index, entity_id, entity["aggregation_type"], new_rows, ts_dt.year, ts_dt.month, tz,
            hourly_rollup=bool(entity["hourly_rollup"]),
        )

    return True


# -- Verdichten: rückwirkende Reduktion bereits archivierter Monate --
#
# Ergänzt die Live-Auflösung (resolution.py) um die rückwirkende Variante für
# Monate, die schon in voller Auflösung archiviert wurden, bevor eine
# Auflösungs-Entscheidung getroffen wurde — oder für Fälle, in denen die
# Live-Auflösung bewusst auf "raw" bleibt, alte Daten aber trotzdem
# irgendwann reduziert werden sollen. Nutzt denselben Zeitraster-Begriff wie
# resolution.py (bucket_end(): Bucket-ENDE statt -Anfang), damit rückwirkend
# und live verdichtete Zeitstempel gleich interpretierbar sind.


class CompactionError(ValueError):
    """Verdichtung abgelehnt (Schalter, kein Verdichtungsziel, bereits
    verdichteter Monat) — main.py wandelt das in einen HTTP 400 um."""


def _compacted_rows_for_month(
    rows: list[tuple[float, float, float | None, float | None]],
    aggregation_type: str,
    interval: int,
) -> list[tuple[float, float, float | None, float | None]]:
    """Bucketet einen sortierten Monat auf `interval` und aggregiert je
    Bucket typabhängig. Gemeinsam von compact_raw_values() und
    preview_compact_raw_values() genutzt, damit die Vorschau exakt
    vorhersagt, was der tatsächliche Lauf schreibt.

    Zähler: letzter Wert je Bucket (Teleskopsumme bleibt exakt korrekt).
    Zusätzlich bleiben Reset-Zeitpunkte (detect_counter_decreases) als eigene
    Rohzeilen erhalten, unabhängig vom Raster — sonst würde ein echter
    Zählerrücksprung im Bucket-Mittendrin verschluckt.

    Standard: Ø der Bucket-Werte, Min/Max über value UND bereits vorhandene
    min_value/max_value (falls das Fenster teils schon durch die Live-
    Auflösung vor-aggregierte Zeilen enthält). Bewusst ein einfacher,
    ungewichteter Durchschnitt über die Bucket-Zeilen — ohne mitgeführte
    Stichprobenanzahl je Zeile lässt sich kein exakt gewichteter Durchschnitt
    bilden, dieselbe akzeptierte Vereinfachung wie bei der Live-Auflösung."""
    buckets: dict[float, list[tuple[float, float, float | None, float | None]]] = {}
    for row in rows:
        buckets.setdefault(resolution_mod.bucket_end(interval, row[0]), []).append(row)

    if aggregation_type == "counter":
        decreases = detect_counter_decreases([(r[0], r[1]) for r in rows])
        new_rows = [
            (bucket_ts, bucket_rows[-1][1], None, None)
            for bucket_ts, bucket_rows in buckets.items()
        ]
        new_rows.extend((r[0], r[1], None, None) for r in rows if r[0] in decreases)
    else:
        new_rows = []
        for bucket_ts, bucket_rows in buckets.items():
            values = [r[1] for r in bucket_rows]
            mins = [r[2] if r[2] is not None else r[1] for r in bucket_rows]
            maxs = [r[3] if r[3] is not None else r[1] for r in bucket_rows]
            new_rows.append((bucket_ts, sum(values) / len(values), min(mins), max(maxs)))
    new_rows.sort(key=lambda r: (r[0], r[1]))
    return new_rows


def _compactable_months(
    index: Index,
    entity_id: str,
    aggregation_type: str,
    target_resolution: str,
    interval: int,
    archive_dir: Path,
    start_ts: float,
    end_ts: float,
    current_month_start: float,
    tz: ZoneInfo,
) -> list[tuple[int, int, Path]]:
    """Archivierte Monate im Zeitraum, die tatsächlich verdichtet werden
    dürfen — gemeinsam von compact_raw_values() und
    preview_compact_raw_values() genutzt, damit beide exakt dieselben Monate
    berücksichtigen. Siehe compact_raw_values() für die Schutzregeln."""
    result = []
    for year, month in _months_between(start_ts, end_ts, tz):
        month_start = datetime(year, month, 1, tzinfo=tz).timestamp()
        if month_start >= current_month_start:
            continue
        archive_path = archive_dir / f"{year:04d}-{month:02d}.parquet"
        if not archive_path.exists():
            continue
        existing_marker = index.get_compacted_month(entity_id, year, month)
        if existing_marker is not None:
            if aggregation_type != "counter":
                continue
            existing_interval = resolution_seconds(existing_marker["target_resolution"])
            if existing_interval is None or interval <= existing_interval:
                continue
        result.append((year, month, archive_path))
    return result


def _validate_compaction_target(entity: dict, target_resolution: str) -> int:
    if entity["aggregation_type"] == "switch":
        raise CompactionError("Verdichten ist für Schalter nicht verfügbar")
    interval = resolution_seconds(target_resolution)
    if interval is None:
        raise CompactionError(f"Ungültiges Verdichtungsziel: {target_resolution}")
    return interval


def preview_compact_raw_values(
    data_dir: Path, index: Index, entity_id: str, start_ts: float, end_ts: float, target_resolution: str,
    tz: ZoneInfo, now: datetime | None = None,
) -> dict:
    """Zeilenzahl vorher/geschätzt danach, OHNE etwas zu schreiben — für die
    Vorschau im Bearbeitungsbereich, Reiter "Verdichten", bevor der Nutzer
    bestätigt."""
    now = now or datetime.now(tz)
    entity = index.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"Unbekannte Entität: {entity_id}")
    interval = _validate_compaction_target(entity, target_resolution)
    aggregation_type = entity["aggregation_type"]
    current_month_start = datetime(now.year, now.month, 1, tzinfo=tz).timestamp()
    archive_dir = entity_dir(data_dir, "archive", entity_id)

    rows_before = 0
    rows_after = 0
    months = _compactable_months(
        index, entity_id, aggregation_type, target_resolution, interval, archive_dir,
        start_ts, end_ts, current_month_start, tz,
    )
    for _year, _month, archive_path in months:
        rows = sorted(_read_archive_month_full(archive_path), key=lambda r: (r[0], r[1]))
        if not rows:
            continue
        rows_before += len(rows)
        rows_after += len(_compacted_rows_for_month(rows, aggregation_type, interval))
    return {"rows_before": rows_before, "rows_after": rows_after, "months": len(months)}


def remove_deleted_points_for_month(index: Index, entity_id: str, year: int, month: int, tz: ZoneInfo) -> int:
    """Entfernt alle deleted_points-Markierungen einer Entität, deren
    Zeitstempel in den angegebenen Kalendermonat fallen — für Vorgänge, die
    einen kompletten Monat ersetzen oder entfernen (compact_raw_values(),
    siehe dort, sowie retention.enforce_retention_for_entity() für per
    Aufbewahrung gelöschte Monate): danach existieren die ursprünglichen
    Rohzeitstempel des Monats nirgends mehr, eine Markierung dafür wäre
    sonst dauerhaft verwaist ("Löschmarkierungen ohne passende
    Rohdatenzeile" in der Bereinigungsvorschau). Auch von
    remove_deleted_points_for_already_compacted_months() genutzt, für Monate,
    die VOR diesem Fix verdichtet wurden. Gibt die Anzahl entfernter
    Markierungen zurück."""
    deleted = index.get_deleted_counts_for_entity(entity_id)
    if not deleted:
        return 0
    target_month_key = f"{year:04d}-{month:02d}"
    relevant = {ts: count for ts, count in deleted.items() if month_key(ts, tz) == target_month_key}
    if not relevant:
        return 0
    timestamps = [ts for ts, count in relevant.items() for _ in range(count)]
    index.remove_deleted_points(entity_id, timestamps)
    return len(timestamps)


def remove_deleted_points_for_already_compacted_months(index: Index, tz: ZoneInfo) -> int:
    """Einmaliger Nachzieh-Lauf beim Start (siehe background.py
    BackgroundService.start()): compact_raw_values() räumte deleted_points
    bisher nicht auf (Fund vom 18.09.2026, Housekeeping → Speicherplatz →
    "Löschmarkierungen ohne passende Rohdatenzeile") — für jeden VOR diesem
    Fix bereits verdichteten Monat (compacted_months) können deshalb noch
    verwaiste Markierungen übrig sein. Idempotent: findet bei jedem weiteren
    Lauf nichts mehr, kostet dann nur einen Tabellen-Scan. Gibt die Anzahl
    insgesamt entfernter Markierungen zurück."""
    total = 0
    for row in index.list_all_compacted_months():
        total += remove_deleted_points_for_month(index, row["entity_id"], row["year"], row["month"], tz)
    return total


def remove_deleted_points_with_no_matching_row(
    data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None
) -> int:
    """Einmaliger Nachzieh-Lauf beim Start (siehe background.py
    BackgroundService.start()): retention.enforce_retention_for_entity()
    räumte deleted_points bisher nicht auf, wenn die Aufbewahrungsfrist einen
    kompletten Archiv-Monat löschte (Fund vom 18.09.2026, derselbe Befund wie
    bei compact_raw_values() oben) — anders als dort gibt es für bereits VOR
    diesem Fix per Aufbewahrung gelöschte Monate aber keine Tabelle, die
    festhält, welche Monate das waren. Prüft deshalb stattdessen direkt gegen
    die Realität, mit derselben Abgleichslogik wie preview_purge() (siehe
    dort) — nur, dass hier tatsächlich entfernt statt nur gezählt wird, was
    nirgends mehr eine passende Rohdatenzeile hat. Fängt dadurch nebenbei
    jede andere, noch unbekannte Ursache für verwaiste Markierungen mit auf.
    Idempotent: findet bei jedem weiteren Lauf nichts mehr. Gibt die Anzahl
    insgesamt entfernter Markierungen zurück."""
    now = now or datetime.now(tz)
    total_removed = 0

    def consume(timestamps: Iterable[float], remaining: dict[float, int]) -> None:
        for ts in timestamps:
            if remaining.get(ts, 0) > 0:
                remaining[ts] -= 1

    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        deleted = index.get_deleted_counts_for_entity(entity_id)
        if not deleted:
            continue
        remaining = dict(deleted)

        hot_file = hot_path(data_dir, entity_id, now.timestamp(), tz)
        if hot_file.exists():
            consume((ts for ts, _ in read_rows(hot_file)), remaining)

        archive_dir = entity_dir(data_dir, "archive", entity_id)
        if archive_dir.exists():
            remaining_by_month = _group_by_month(remaining, tz)
            for path in sorted(archive_dir.glob("*.parquet")):
                if path.stem not in remaining_by_month:
                    continue
                table = pq.read_table(path, columns=["ts"])
                consume(table.column("ts").to_pylist(), remaining)

        orphaned = [ts for ts, count in remaining.items() for _ in range(count)]
        if orphaned:
            index.remove_deleted_points(entity_id, orphaned)
            total_removed += len(orphaned)
    return total_removed


def compact_raw_values(
    data_dir: Path, index: Index, entity_id: str, start_ts: float, end_ts: float, target_resolution: str,
    tz: ZoneInfo, now: datetime | None = None,
) -> dict:
    """Verdichtet bereits archivierte Monate im Zeitraum [start_ts, end_ts]
    auf target_resolution — rückwirkend, im Gegensatz zur Live-Auflösung
    (resolution.py). Nur Zähler und Standard (siehe CompactionError bei
    Schalter); nur Monate, für die schon eine Archivdatei existiert — der
    laufende, noch nicht rotierte Monat wird stillschweigend übersprungen,
    nicht abgelehnt, sonst würde ein bis "heute" reichender Zeitraum komplett
    scheitern statt die bereits archivierten Monate darin zu verdichten.

    Schutz vor doppelter Verdichtung (compacted_months-Tabelle): Zähler
    dürfen erneut auf ein GRÖBERES Ziel verdichtet werden (mathematisch
    exakt — der letzte Wert im großen Bucket ist zwangsläufig auch der
    letzte unter den bereits verdichteten kleineren Buckets), Standard-Monate
    dagegen nie erneut (Ø aus bereits gemittelten Ø-Werten wäre ohne
    mitgeführte Stichprobenanzahl verzerrt, siehe _compacted_rows_for_month()).

    Locking ist Sache des Aufrufers (main.py, wie bei correct_raw_value()/
    add_raw_value()/purge_*): diese Funktion nimmt selbst keine Sperre.

    Räumt außerdem deleted_points-Markierungen im verdichteten Monat auf
    (siehe remove_deleted_points_for_month()) — deren ursprüngliche
    Rohzeitstempel existieren danach nicht mehr, sie blieben sonst dauerhaft
    als "Löschmarkierungen ohne passende Rohdatenzeile" liegen.

    Gibt rows_before/rows_after/months_compacted/stale_markers_removed zurück."""
    now = now or datetime.now(tz)
    entity = index.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"Unbekannte Entität: {entity_id}")
    interval = _validate_compaction_target(entity, target_resolution)
    aggregation_type = entity["aggregation_type"]
    hourly_rollup = bool(entity["hourly_rollup"])
    current_month_start = datetime(now.year, now.month, 1, tzinfo=tz).timestamp()
    archive_dir = entity_dir(data_dir, "archive", entity_id)

    rows_before = 0
    rows_after = 0
    stale_markers_removed = 0
    months_compacted: list[str] = []
    months = _compactable_months(
        index, entity_id, aggregation_type, target_resolution, interval, archive_dir,
        start_ts, end_ts, current_month_start, tz,
    )
    for year, month, archive_path in months:
        rows = sorted(_read_archive_month_full(archive_path), key=lambda r: (r[0], r[1]))
        if not rows:
            continue
        new_rows = _compacted_rows_for_month(rows, aggregation_type, interval)
        rows_before += len(rows)
        rows_after += len(new_rows)
        _rewrite_archive_month(
            data_dir, index, entity_id, aggregation_type, new_rows, year, month, tz,
            hourly_rollup=hourly_rollup,
        )
        index.add_row_count(entity_id, len(new_rows) - len(rows))
        index.set_compacted_month(entity_id, year, month, target_resolution, now.timestamp())
        months_compacted.append(f"{year:04d}-{month:02d}")
        stale_markers_removed += remove_deleted_points_for_month(index, entity_id, year, month, tz)

    return {
        "rows_before": rows_before,
        "rows_after": rows_after,
        "months_compacted": months_compacted,
        "stale_markers_removed": stale_markers_removed,
    }
