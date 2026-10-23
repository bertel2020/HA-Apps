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

from ..i18n import tr

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from . import cleanup, rollup
from .hotbuffer import hot_path, read_rows
from .index import STATE_TYPE, STATE_UNAVAILABLE_INDEX, Index, filter_deleted_occurrences
from .paths import entity_dir
from ..limits import MAX_COMPARE_YEARS, MAX_RAW_QUERY_POINTS

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

# Rollup-Stufen, die ein Request vollständig im Speicher halten darf (siehe
# QueryReadCache.read_rollup_rows). Eine Monatsstufe hat zwölf Zeilen je Jahr,
# eine Jahresstufe eine — auch ein Jahrzehnt Historie bleibt winzig. Die feinen
# Stufen sind bewusst NICHT dabei: gemessen wiegen monat+jahr über 86 Entitäten
# zusammen 627 KiB, eine einzelne stunde.parquet dagegen 5,7 MiB. Dort ist der
# gefilterte Read mit Row-Group-Pushdown die richtige Antwort, nicht ein Cache.
CACHEABLE_ROLLUP_LEVELS = ("monat", "jahr")


class QueryReadCache:
    """Request-lokaler Cache für unveränderliche Lese-Snapshots.

    Eine Vergleichstabelle fragt dieselbe Entität für mehrere Zeitfenster ab.
    Ohne diesen Cache wird ihre laufende Monats-CSV für jede Spalte erneut
    vollständig geparst. Der Cache lebt absichtlich nur für einen Request;
    dadurch braucht der Append-Pfad keine globale Cache-Invalidierung.
    """

    def __init__(self) -> None:
        self._hot_rows: dict[Path, list[tuple[float, float]]] = {}
        # Schlüssel (entity_id, level) statt Pfad: rollup_path() geht über
        # entity_dir(), und das ruft für jeden Pfad Path.resolve() auf
        # (Symlink-Schutz) — ein realpath-Syscall. Bei einer Vergleichstabelle
        # war genau das der größte Restposten, nachdem die wiederholten
        # Dateizugriffe weg waren; ein Pfad-Schlüssel hätte ihn stehenlassen.
        self._rollup_rows: dict[tuple[str, str], list[rollup.FineRow]] = {}
        # Ablage für beliebige weitere request-lokale Zwischenergebnisse
        # AUFRUFENDER Module — der Hot-Row-Cache oben spart nur das erneute
        # Parsen einer Datei, nicht die darauf aufbauende Aggregation. Wer
        # dieselbe (Entität, Zeitraum, Offset)-Kombination innerhalb eines
        # Requests mehrfach braucht (siehe _entity_series() im
        # Energiedashboard), legt das fertige Ergebnis hier ab. Schlüssel
        # bitte mit einem Modul-Präfix versehen, damit sich zwei Nutzer
        # dieses Caches nicht gegenseitig überschreiben. Lebensdauer und
        # Invalidierung wie beim Hot-Row-Cache: genau ein Request, deshalb
        # keine.
        self.memo: dict = {}

    def read_hot_rows(self, path: Path) -> list[tuple[float, float]]:
        if path not in self._hot_rows:
            self._hot_rows[path] = read_rows(path)
        return self._hot_rows[path]

    def read_rollup_rows(self, data_dir: Path, entity_id: str, level: str) -> list[rollup.FineRow]:
        """Liefert eine grobe Rollup-Stufe VOLLSTÄNDIG und liest sie je Request
        höchstens einmal.

        Ohne das öffnet eine Dekaden-Ansicht dieselbe jahr.parquet bis zu elfmal
        (einmal je Kalenderjahr, siehe _query_year_level) — und eine
        Vergleichstabelle wiederholt das für jede Entität in jeder Spalte.
        Vollständig statt gefiltert, weil sich der Nutzen erst über mehrere
        Fenster einstellt: Jede Spalte fragt einen anderen Zeitraum ab, ein nach
        Fenstergrenzen geschlüsselter Cache träfe deshalb nie.
        """
        key = (entity_id, level)
        if key not in self._rollup_rows:
            path = rollup.rollup_path(data_dir, entity_id, level)
            self._rollup_rows[key] = (
                _rollup_rows_from_table(pq.read_table(path)) if path.exists() else []
            )
        return self._rollup_rows[key]


def _hot_rows(path: Path, read_cache: QueryReadCache | None) -> list[tuple[float, float]]:
    return read_cache.read_hot_rows(path) if read_cache is not None else read_rows(path)


def _resolved_chart_type(aggregation_type: str, chart_type: str | None) -> str:
    if chart_type is not None:
        if chart_type not in ("line", "bar"):
            raise ValueError(tr("Unbekannter Diagrammtyp: {chart_type}", chart_type=chart_type))
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
    range_key: str, now_local: datetime, offset: int = 0, continuous: bool = False,
    elapsed: timedelta | None = None,
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

    elapsed: für den "Gleicher Zeitpunkt"-Vergleich einer Vergleichstabelle
    (Konzept-Erweiterung) — eine bereits VERGANGENE Periode (offset<0) wird
    normalerweise bis zu ihrem eigenen natürlichen Ende gefüllt (sie liegt ja
    schon ganz in der Vergangenheit, "jetzt" deckelt sie nicht). elapsed
    deckelt sie stattdessen auf denselben Abstand vom Periodenanfang wie die
    laufende Periode gerade hat (z. B. "gestern bis 14 Uhr" statt "gestern
    ganz"), damit ein Vergleich mit der noch laufenden aktuellen Periode fair
    bleibt. None = bisheriges Verhalten (Deckel bei "jetzt")."""
    offset = min(offset, 0)
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    def _cap(start: datetime, natural_end: datetime) -> datetime:
        limit = start + elapsed if elapsed is not None else now_local
        return min(natural_end, limit)

    if range_key == "hour":
        if continuous:
            anchor = now_local + timedelta(hours=offset)
            return anchor - timedelta(hours=1), anchor, anchor
        hour_start = now_local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
        natural_end = hour_start + timedelta(hours=1)
        return hour_start, _cap(hour_start, natural_end), natural_end

    if range_key == "day":
        if continuous:
            anchor = now_local + timedelta(days=offset)
            return anchor - timedelta(days=1), anchor, anchor
        start = midnight + timedelta(days=offset)
        natural_end = start + timedelta(days=1)
        return start, _cap(start, natural_end), natural_end

    if range_key == "week":
        if continuous:
            anchor = now_local + timedelta(days=7 * offset)
            return anchor - timedelta(days=7), anchor, anchor
        # Kalenderwoche Montag–Sonntag (ISO), nicht die rollierenden letzten 7 Tage.
        week_start = midnight - timedelta(days=midnight.weekday()) + timedelta(days=7 * offset)
        natural_end = week_start + timedelta(days=7)
        return week_start, _cap(week_start, natural_end), natural_end

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
        return start, _cap(start, natural_end), natural_end

    if range_key == "year":
        if continuous:
            anchor = now_local.replace(year=now_local.year + offset)
            return anchor.replace(year=anchor.year - 1), anchor, anchor
        year = now_local.year + offset
        start = midnight.replace(year=year, month=1, day=1)
        natural_end = start.replace(year=year + 1)
        return start, _cap(start, natural_end), natural_end

    if range_key == "decade":
        if continuous:
            anchor = now_local.replace(year=now_local.year + 10 * offset)
            return anchor.replace(year=anchor.year - 10), anchor, anchor
        decade_start_year = (now_local.year // 10) * 10 + 10 * offset
        start = midnight.replace(year=decade_start_year, month=1, day=1)
        natural_end = start.replace(year=decade_start_year + 10)
        return start, _cap(start, natural_end), natural_end

    raise ValueError(tr("Unbekannter Zeitraum: {range_key}", range_key=range_key))


def _read_hot_rows_filtered(
    data_dir: Path, entity_id: str, index: Index, anchor_ts: float, start_ts: float, end_ts: float, tz: ZoneInfo,
    read_cache: QueryReadCache | None = None,
) -> list[tuple[float, float]]:
    rows = _hot_rows(hot_path(data_dir, entity_id, anchor_ts, tz), read_cache)
    in_range = sorted((ts, v) for ts, v in rows if start_ts <= ts <= end_ts)
    deleted_counts = index.get_deleted_counts(entity_id, start_ts, end_ts)
    return filter_deleted_occurrences(in_range, deleted_counts)


def marked_ranges_in_window(
    index: Index,
    entity_id: str,
    start_ts: float,
    end_ts: float,
    limit: int,
) -> tuple[list[dict], int]:
    """Zusammenhängende Blöcke zur Löschung markierter Werte in einem Fenster.

    Gegenstück zur Filterung, die sonst überall greift: markierte Zeilen werden
    aus jeder Antwort entfernt, damit ein Chart nach dem Markieren sofort ohne
    sie aussieht. Sie sind damit aber auch aus jeder Ansicht verschwunden,
    obwohl sie bis zum Purge noch da und noch zu retten sind — nachsehen ließ
    sich das nur in einer Zeitstempel-Tabelle unter Housekeeping ->
    Speicherplatz. Hier kommt die Information zurück, damit der Chart die
    betroffenen Stellen als Band markieren kann.

    Gelesen wird ausschließlich der Index; Hot Buffer und Archiv werden nicht
    angefasst. Die WERTE der markierten Zeilen bräuchte es dafür nicht — und
    genau das macht diese Auskunft billig genug, um sie bei jeder Abfrage
    mitzuliefern.

    Benachbarte Markierungen werden zu EINEM Block zusammengefasst — 163
    aufeinanderfolgende Messungen sind ein Vorgang, kein
    Hundertdreiundsechzigfaches. Die Schwelle dafür ist an der ANZEIGE
    hergeleitet, nicht an den Daten: ein Chart ist rund 900 px breit, zwei
    Bänder näher als ein Hundertstel des Fensters (etwa neun Pixel) sind als
    getrennte Streifen nicht mehr zu unterscheiden.

    Der Wert ist gemessen, nicht geraten. Der erste Versuch stand bei einem
    Dreihundertstel (drei Pixel) und ist an echten Daten gescheitert: eine
    Tagesansicht ergibt damit 4,8 Minuten, ein Sensor im 5-Minuten-Takt liegt
    also um zwölf Sekunden darüber — aus einem zusammenhängenden Block wurden
    147 getrennte Bänder. Ein Hundertstel lässt für einen Tag 14,4 Minuten zu
    und fasst damit alle üblichen Melderhythmen zusammen, hält aber
    stündliche oder weiter entfernte Markierungen auseinander, die auf dem
    Schirm ohnehin klar getrennt liegen.

    Bewusst NICHT aus den Daten abgeleitet (etwa dem Median der Abstände):
    bei genau zwei Markierungen ist der Median ihr eigener Abstand, sie
    verschmelzen dann immer — egal wie weit sie auseinanderliegen.

    Aus 44.000 Einzelmarkierungen einer Jahresansicht werden so eine Handvoll
    Bänder — und zwar VOLLSTÄNDIG, während ein Kappen der Zeitstempel nur die
    frühesten gezeigt und damit den Eindruck erweckt hätte, der Rest des
    Jahres sei unberührt.

    Rückgabe ist ein Paar aus (höchstens ``limit`` Blöcken, tatsächlicher
    Gesamtzahl markierter Werte). Die Gesamtzahl ist auch dann richtig, wenn
    die Blockliste gekappt wurde.
    """
    counts = index.get_deleted_counts(entity_id, start_ts, end_ts)
    if not counts:
        return [], 0
    gesamt = sum(counts.values())
    luecke = max((end_ts - start_ts) / 100.0, 1.0)
    bloecke: list[dict] = []
    for ts in sorted(counts):
        if bloecke and ts - bloecke[-1]["end"] <= luecke:
            bloecke[-1]["end"] = ts
            bloecke[-1]["count"] += counts[ts]
            continue
        if len(bloecke) >= limit:
            break
        bloecke.append({"start": ts, "end": ts, "count": counts[ts]})
    return bloecke, gesamt


def _last_value_before(path: Path, before_ts: float) -> float | None:
    """Letzter Wert einer einzelnen Archiv-Monatsdatei mit ``ts < before_ts``.

    Liest pro Row-Group zuerst nur die Parquet-Statistik (min/max von ``ts``,
    ohne die eigentlichen Datenseiten zu laden) — eine Row-Group, die
    komplett auf oder nach ``before_ts`` liegt, wird so übersprungen, ohne
    ihre Werte einzulesen (ZP-001 in PERFORMANCE.md). Row-Groups ohne
    verwertbare Statistik (z. B. sehr alte/fremde Parquet-Dateien) werden wie
    bisher vollständig gelesen. Anders als zuvor wird nicht mehr die
    komplette Tabelle sortiert, bevor gefiltert wird — nur die tatsächlich
    infrage kommenden Kandidaten."""
    parquet_file = pq.ParquetFile(path)
    metadata = parquet_file.metadata
    ts_index = parquet_file.schema_arrow.get_field_index("ts")
    for group_index in range(metadata.num_row_groups - 1, -1, -1):
        row_group = metadata.row_group(group_index)
        stats = row_group.column(ts_index).statistics if ts_index >= 0 else None
        if stats is not None and stats.has_min_max and stats.min >= before_ts:
            continue
        table = parquet_file.read_row_group(group_index, columns=["ts", "value"])
        candidates = [
            (ts, value)
            for ts, value in zip(
                table.column("ts").to_pylist(), table.column("value").to_pylist()
            )
            if ts < before_ts
        ]
        if candidates:
            # Stabil nur nach ts sortieren (wie zuvor table.sort_by("ts")) —
            # bei doppelten Zeitstempeln bleibt so dieselbe Reihenfolge wie
            # in der Originaldatei maßgeblich für die Auswahl des letzten
            # Kandidaten.
            candidates.sort(key=lambda candidate: candidate[0])
            return candidates[-1][1]
    return None


def _boundary_value(
    data_dir: Path, entity_id: str, before_ts: float, tz: ZoneInfo,
    read_cache: QueryReadCache | None = None,
) -> float | None:
    """Letzter Rohwert vor ``before_ts`` aus Hot Buffer oder Monatsarchiv."""
    before_local = datetime.fromtimestamp(before_ts, tz)
    rows = _hot_rows(hot_path(data_dir, entity_id, before_ts, tz), read_cache)
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
        value = _last_value_before(path, before_ts)
        if value is not None:
            return value
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
    read_cache: QueryReadCache | None = None,
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
            hot_rows_loader=read_cache.read_hot_rows if read_cache is not None else read_rows,
        )
    )
    boundary = (
        _boundary_value(data_dir, entity_id, window_start.timestamp(), tz, read_cache)
        if aggregation_type == "counter"
        else None
    )
    key_fn = rollup.seconds_bucket_key(tz, bucket_seconds)
    next_fn = rollup.seconds_bucket_next(bucket_seconds)
    fine, _ = rollup.compute_fine_rollup_with_key(
        rows, aggregation_type, key_fn, boundary, window_end.timestamp(), bucket_next_fn=next_fn
    )
    return fine


def _rollup_rows_from_table(table) -> list[rollup.FineRow]:
    """Baut FineRow-Zeilen aus einer gelesenen Rollup-Tabelle.

    to_pylist() statt .column(col)[i].as_py() pro Zelle (siehe PERFORMANCE.md,
    ZP-007) — eine vektorisierte Konvertierung je Spalte statt table.num_rows
    einzelner Zugriffe je Spalte, analog zum bestehenden Muster in
    rollup.append_completed_month().
    """
    columns = table.column_names
    if "idx" in columns:
        return _state_rows_from_table(table)
    present = [col for col in ("value", "min_value", "max_value", "on_seconds") if col in columns]
    bucket_starts = table.column("bucket_start").to_pylist()
    values_by_col = {col: table.column(col).to_pylist() for col in present}
    rows = []
    for i, bucket_start in enumerate(bucket_starts):
        kwargs = {"bucket_start": bucket_start}
        for col in present:
            kwargs[col] = values_by_col[col][i]
        rows.append(rollup.FineRow(**kwargs))
    return rows


def _state_rows_from_table(table) -> list[rollup.FineRow]:
    """Langformat der Zustands-Rollups (eine Zeile je Bucket und Zustand)
    zurück zu einer FineRow je Bucket."""
    grouped: dict[float, dict[int, tuple[float, int]]] = {}
    for bucket_start, idx, seconds, entries in zip(
        table.column("bucket_start").to_pylist(),
        table.column("idx").to_pylist(),
        table.column("seconds").to_pylist(),
        table.column("entries").to_pylist(),
    ):
        grouped.setdefault(bucket_start, {})[idx] = (seconds or 0.0, entries or 0)
    return [
        rollup.FineRow(bucket_start=bucket_start, states=states)
        for bucket_start, states in sorted(grouped.items())
    ]


def _read_rollup_rows(
    data_dir: Path, entity_id: str, level: str, start_ts: float, end_ts: float,
    read_cache: QueryReadCache | None = None,
) -> list[rollup.FineRow]:
    """Liest einen Zeitausschnitt einer Rollup-Stufe.

    Die Stufenprüfung steht hier und nicht bei den Aufrufern: Ein Cache über
    eine feine Stufe wäre ein Speicherproblem (siehe CACHEABLE_ROLLUP_LEVELS),
    und eine solche Entscheidung soll nicht an jeder Aufrufstelle erneut
    richtig getroffen werden müssen.
    """
    if read_cache is not None and level in CACHEABLE_ROLLUP_LEVELS:
        return [
            row for row in read_cache.read_rollup_rows(data_dir, entity_id, level)
            if start_ts <= row.bucket_start < end_ts
        ]
    path = rollup.rollup_path(data_dir, entity_id, level)
    if not path.exists():
        return []
    table = pq.read_table(
        path,
        filters=[("bucket_start", ">=", start_ts), ("bucket_start", "<", end_ts)],
    )
    return _rollup_rows_from_table(table)


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


def _fine_rows_until(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    level: str,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    now_local: datetime,
    read_cache: QueryReadCache | None = None,
) -> list[rollup.FineRow]:
    """Feine Rollup-Zeilen (tag/stunde) für [start, end) — auch dann exakt, wenn
    ``end`` MITTEN in einem Bucket liegt.

    Ein Rollup kennt nur ganze Buckets: ein Fenster, das um 17:51 Uhr des 6.
    Oktobers endet, würde mit ``bucket_start < end`` den GANZEN 6. Oktober
    mitzählen. Das trifft jede Abfrage, deren Ende nicht auf einer Bucket-Grenze
    liegt und in einem abgeschlossenen Monat (also im Rollup) liegt: den
    Vorjahresvergleich ("2025 YTD bis 06.10.") und den "Gleichen Zeitpunkt"-
    Vergleich einer Vorperiode. Der angeschnittene letzte Bucket wird deshalb
    aus den Rohdaten bis ``end`` neu berechnet, alle davor kommen weiter aus dem
    Rollup. Liegt ``end`` auf einer Grenze, bleibt es beim reinen Rollup-Lesen."""
    key_fn = rollup.named_bucket_key(tz, level)
    tail_start = key_fn(end.timestamp())
    if tail_start >= end:
        return _read_rollup_rows(
            data_dir, entity_id, level, start.timestamp(), end.timestamp(), read_cache
        )
    rows = _read_rollup_rows(
        data_dir, entity_id, level, start.timestamp(), tail_start.timestamp(), read_cache
    )
    raw_start = max(start, tail_start)
    raw = list(
        cleanup.iter_raw_rows(
            data_dir,
            index,
            entity_id,
            raw_start.timestamp(),
            end.timestamp(),
            tz,
            now=now_local,
            max_rows=MAX_RAW_QUERY_POINTS,
            hot_rows_loader=read_cache.read_hot_rows if read_cache is not None else read_rows,
        )
    )
    boundary = (
        _boundary_value(data_dir, entity_id, raw_start.timestamp(), tz, read_cache)
        if aggregation_type == "counter"
        else None
    )
    tail, _ = rollup.compute_fine_rollup_with_key(
        raw, aggregation_type, key_fn, boundary, end.timestamp(),
        bucket_next_fn=rollup.named_bucket_next(tz, level),
    )
    return rows + tail


def _month_rows_until(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    now_local: datetime,
    read_cache: QueryReadCache | None = None,
) -> list[rollup.FineRow]:
    """Monats-Rollup-Zeilen für [start, end) — der angeschnittene letzte Monat
    wird aus feineren Daten bis ``end`` zusammengesetzt statt als GANZER Monat zu
    zählen (siehe _fine_rows_until). Er entsteht aus denselben Tages-/Stunden-
    Zeilen und derselben Verdichtung wie die Monatszeile im Rollup selbst."""
    tail_month = rollup.named_bucket_key(tz, "monat")(end.timestamp())
    if tail_month >= end:
        return _read_rollup_rows(
            data_dir, entity_id, "monat", start.timestamp(), end.timestamp(), read_cache
        )
    rows = _read_rollup_rows(
        data_dir, entity_id, "monat", start.timestamp(), tail_month.timestamp(), read_cache
    )
    if tail_month >= start:
        fine = _fine_rows_until(
            data_dir, index, entity_id, aggregation_type, FINE_LEVEL[aggregation_type],
            tail_month, end, tz, now_local, read_cache,
        )
        if fine:
            rows.append(rollup.aggregate_fine_to_month(fine, aggregation_type, tail_month.timestamp()))
    return rows


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
    read_cache: QueryReadCache | None = None,
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
    completed = _fine_rows_until(
        data_dir, index, entity_id, aggregation_type, source_level,
        window_start, completed_end, tz, now_local, read_cache,
    )
    if level != source_level:
        completed = _aggregate_rollup_rows(completed, aggregation_type, level, tz)

    live_start = max(window_start, current_month_start)
    live: list[rollup.FineRow] = []
    if live_start < window_end:
        rows = _read_hot_rows_filtered(
            data_dir, entity_id, index, now_local.timestamp(), live_start.timestamp(), window_end.timestamp(), tz,
            read_cache,
        )
        boundary = (
            _boundary_value(data_dir, entity_id, live_start.timestamp(), tz, read_cache)
            if aggregation_type == "counter"
            else None
        )
        key_fn = rollup.named_bucket_key(tz, level)
        next_fn = rollup.named_bucket_next(tz, level)
        live, _ = rollup.compute_fine_rollup_with_key(
            rows, aggregation_type, key_fn, boundary, window_end.timestamp(), bucket_next_fn=next_fn
        )

    return sorted(completed + live, key=lambda r: r.bucket_start)


def query_hourly_counter_series(
    data_dir: Path,
    index: Index,
    entity_id: str,
    window_start: datetime,
    window_end: datetime,
    tz: ZoneInfo,
    now_local: datetime,
    read_cache: QueryReadCache | None = None,
) -> dict[float, float]:
    """(Bucket-Zeitstempel -> Stunden-Delta) einer Zähler-Entität über ein
    beliebig langes Fenster (mehrere Monate/ein Jahr), aus der zusätzlichen
    Stunden-Rollup-Stufe (rollup/<entity>/stunde.parquet, siehe
    rollup.append_completed_month hourly_rollup) für abgeschlossene Monate +
    live aus dem Hot Buffer für den laufenden Monat — dieselbe
    Rollup/Live-Aufteilung wie _query_completed_plus_live_fine(), aber fest
    auf Stunden-Buckets und ohne dessen Beschränkung auf ein Fenster
    innerhalb des laufenden/vorherigen Monats.

    Nur für Zähler mit gesetztem entities.hourly_rollup sinnvoll — ohne
    Rollup-Datei liefert der abgeschlossene Teil einfach nichts (kein
    Fehler), Aufrufer (energiedashboard_routes.py) filtert die Rolle
    entsprechend vorher."""
    current_month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    completed_end = min(window_end, current_month_start)
    completed = _read_rollup_rows(
        data_dir, entity_id, "stunde", window_start.timestamp(), completed_end.timestamp()
    )

    live_start = max(window_start, current_month_start)
    live: list[rollup.FineRow] = []
    if live_start < window_end:
        rows = _read_hot_rows_filtered(
            data_dir, entity_id, index, now_local.timestamp(), live_start.timestamp(), window_end.timestamp(), tz,
            read_cache,
        )
        boundary = _boundary_value(data_dir, entity_id, live_start.timestamp(), tz, read_cache)
        key_fn = rollup.named_bucket_key(tz, "stunde")
        next_fn = rollup.named_bucket_next(tz, "stunde")
        live, _ = rollup.compute_fine_rollup_with_key(
            rows, "counter", key_fn, boundary, window_end.timestamp(), bucket_next_fn=next_fn
        )

    return {row.bucket_start: (row.value or 0.0) for row in completed + live}


def _month_live_row(
    data_dir: Path,
    index: Index,
    entity_id: str,
    aggregation_type: str,
    month_start: datetime,
    window_end: datetime,
    now_local: datetime,
    tz: ZoneInfo,
    read_cache: QueryReadCache | None = None,
) -> rollup.FineRow | None:
    rows = _read_hot_rows_filtered(
        data_dir, entity_id, index, now_local.timestamp(), month_start.timestamp(), window_end.timestamp(), tz,
        read_cache,
    )
    if not rows:
        return None
    boundary = (
        _boundary_value(data_dir, entity_id, month_start.timestamp(), tz, read_cache)
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
    read_cache: QueryReadCache | None = None,
) -> list[rollup.FineRow]:
    """Für year: immer Monatswerte aus monat.parquet + der laufende Monat live
    aus dem Hot Buffer — jahr.parquet wird hier NIE konsultiert, auch nicht für
    Monate aus einem vorherigen Kalenderjahr. Grund: "Rollierend" liefert ein
    rollierendes Fenster, das über eine Jahresgrenze hinausreichen kann (z. B.
    Aug 2025–Aug 2026) — jahr.parquet kennt aber nur GANZE Kalenderjahre und
    kann so ein angeschnittenes Vorjahr nicht liefern, die betroffenen Monate
    wären sonst spurlos verschwunden.

    Für decade-Balken sowie decade-Zähler ein Wert pro Kalenderjahr: ein Jahr, das VOLLSTÄNDIG
    im Fenster liegt, kommt aus jahr.parquet (schneller Pfad für abgeschlossene
    Jahre). Ein nur teilweise abgedecktes Jahr — das laufende Jahr am aktuellen
    Ende des Fensters, oder bei "Rollierend" auch ein angeschnittenes Jahr am
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
            read_cache,
        )

    resolution = (
        BAR_RESOLUTION[range_key]
        if chart_type == "bar"
        else ("jahr" if range_key == "decade" and aggregation_type == "counter" else "monat")
    )
    if resolution == "monat":
        completed = _month_rows_until(
            data_dir, index, entity_id, aggregation_type, window_start, completed_month_end,
            tz, now_local, read_cache,
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
            jahr_rows = _read_rollup_rows(
                data_dir, entity_id, "jahr", year_start.timestamp(), year_end.timestamp(), read_cache
            )
            if jahr_rows:
                row = jahr_rows[0]

        if row is None:
            monat_rows = _month_rows_until(
                data_dir, index, entity_id, aggregation_type, clip_start,
                min(clip_end, completed_month_end), tz, now_local, read_cache,
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


def _missing_entity_result(
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    offset: int,
    continuous: bool,
    year_over_year: bool = False,
    cap_to: timedelta | None = None,
) -> dict:
    """Antwort für eine Entität, die es nicht (mehr) gibt — z. B. weil sie gelöscht wurde, das
    gespeicherte Diagramm sie aber noch nennt. Gleiche Form wie eine normale Antwort (inkl.
    Zeitfenster), nur ohne Punkte: Aufrufer lesen ``window_start`` & Co. unbedingt, ein
    verkürztes Ergebnis endete dort als KeyError (HTTP 500) und das Diagramm hing auf „Lädt …"."""
    window_start, window_end, period_end = _window(range_key, now.astimezone(tz), offset, continuous, elapsed=cap_to)
    if year_over_year:
        window_start = _shift_year(window_start, -1)
        window_end = _shift_year(window_end, -1)
        period_end = _shift_year(period_end, -1)
    return {
        "entity_id": entity_id,
        "aggregation_type": None,
        "chart_type": "line",
        "points": [],
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
        "elapsed_seconds": None,
    }


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
    read_cache: QueryReadCache | None = None,
    same_elapsed: bool = False,
    cap_to: timedelta | None = None,
) -> dict:
    """cap_to: deckelt eine bereits vergangene Periode (offset<0) auf diesen
    Abstand vom Periodenanfang — nur für den fairen "Gleicher Zeitpunkt"-
    Vergleichswert (siehe unten), nicht für die Anzeige der Periode selbst."""
    if range_key not in RANGE_KEYS:
        raise ValueError(tr("Unbekannter Zeitraum: {range_key}", range_key=range_key))

    entity = index.get_entity(entity_id)
    if entity is None:
        return _missing_entity_result(entity_id, range_key, tz, now, offset, continuous, year_over_year, cap_to)

    aggregation_type = entity["aggregation_type"]
    if aggregation_type == STATE_TYPE:
        # Ein Mittelwert oder eine Summe über Zustandsindizes ergäbe keinen
        # Sinn. Bis zur Woche zeichnet die Oberfläche die einzelnen Wechsel als
        # Zeitstrahl, ab dem Monat die Dauer je Zustand als Anteile
        # (ROADMAP 1.29: ein Jahr Zeitstrahl wären zehntausende Rechtecke).
        if range_key in STATE_TIMELINE_RANGES:
            return query_raw_series(data_dir, index, entity_id, range_key, tz, now, offset, continuous)
        return query_state_shares(
            data_dir, index, entity_id, range_key, tz, now, offset, continuous, read_cache
        )
    resolved_chart_type = _resolved_chart_type(aggregation_type, chart_type)
    now_local = now.astimezone(tz)
    # "Gleicher Zeitpunkt"-Vergleich (Vergleichstabelle): wie viel von der
    # laufenden aktuellen Periode bereits vergangen ist — z. B. "Tag" steht
    # gerade bei 14:32 Uhr. Kappt NICHT mehr das Fenster/die Punkte einer
    # vergangenen Periode (offset<0): "Vortag" zeigt immer den vollständigen
    # Zeitraum, der Wert selbst darf nicht stillschweigend verkürzt werden.
    # elapsed_seconds fließt stattdessen nur noch in die Antwort ein, für
    # einen separat berechneten, fairen Vergleichswert (siehe
    # _table_aggregates()/"comparison" in api_routes.py) — "Tag bisher"
    # gegen "Vortag bis zur selben Uhrzeit" statt gegen den ganzen Vortag.
    # Nur für offset<0 ohne year_over_year: eine year_over_year-Spalte
    # bekommt einen fairen Vergleich bereits automatisch, da sie dieselbe
    # (für offset=0 schon gedeckelte) Fensterberechnung nur um ein Jahr
    # zurückschiebt.
    elapsed = None
    if same_elapsed and offset < 0 and not year_over_year:
        current_start, _current_end, _current_natural = _window(range_key, now_local, 0, continuous)
        elapsed = now_local - current_start
    window_start, window_end, period_end = _window(range_key, now_local, offset, continuous, elapsed=cap_to)
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
            window_start, window_end, tz, now_local, read_cache
        )
    elif range_key in ("week", "month"):
        fine = _query_completed_plus_live_fine(
            data_dir, index, entity_id, aggregation_type, resolved_chart_type,
            window_start, window_end, tz, now_local, read_cache
        )
    else:
        fine = _query_year_level(
            data_dir, index, entity_id, aggregation_type, range_key, resolved_chart_type,
            window_start, window_end, tz, now_local, read_cache
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
            data_dir, entity_id, window_start.timestamp(), tz, read_cache
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

    result = {
        "entity_id": entity_id,
        "aggregation_type": aggregation_type,
        "chart_type": resolved_chart_type,
        "points": points,
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
        "elapsed_seconds": elapsed.total_seconds() if elapsed is not None else None,
    }
    if elapsed is not None:
        # Fairer Vergleichswert: dieselbe Periode, aber WIRKLICH nur bis zum
        # selben Abstand vom Periodenanfang abgefragt. Früher wurden hier die
        # fertigen Buckets nach bucket_start gekappt — ein angeschnittener
        # Bucket (der 6. Oktober um 17:51 Uhr, der Oktober eines Jahresvergleichs)
        # zählte dann komplett mit.
        result["comparison_points"] = query_series(
            data_dir, index, entity_id, range_key, tz, now, offset=offset, continuous=continuous,
            chart_type=chart_type, read_cache=read_cache, cap_to=elapsed,
        )["points"]
    return result


def query_compare_cut(
    data_dir: Path,
    index: Index,
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    *,
    offset: int,
    continuous: bool,
    year_over_year: bool,
    chart_type: str | None = None,
    read_cache: QueryReadCache | None = None,
) -> dict | None:
    """Die Vergleichsperiode einer LAUFENDEN Periode, nur bis zum selben Abstand vom
    Periodenanfang — für die Summe „bis zum gleichen Tag“ in der Chart-Legende.

    Die Vergleichsreihe des Charts zeigt bewusst die ganze Vorperiode (bzw. beim Vorjahr
    ein Fenster, dessen letzter Bucket meist angeschnitten ist), und deren Summe steht
    neben der des laufenden, unvollständigen Zeitraums. Diese Abfrage liefert die faire
    Gegenzahl: dieselbe Kappung wie der Vergleichswert der Tabelle (comparison_points),
    der letzte Bucket also aus den Rohwerten bis zum Schnittpunkt.

    None, wenn es nichts zu kappen gibt: der Zeitraum ist schon abgeschlossen (offset<0)
    oder rollierend (continuous, immer volle Länge)."""
    if offset != 0 or continuous:
        return None
    now_local = now.astimezone(tz)
    current_start, _current_end, _current_natural = _window(range_key, now_local, 0, continuous)
    result = query_series(
        data_dir, index, entity_id, range_key, tz, now,
        offset=0 if year_over_year else -1, continuous=continuous, year_over_year=year_over_year,
        chart_type=chart_type, read_cache=read_cache, cap_to=now_local - current_start,
    )
    return {"points": result["points"], "window_end": result["window_end"]}


def query_previous_years(
    data_dir: Path,
    index: Index,
    entity_id: str,
    tz: ZoneInfo,
    now: datetime,
    *,
    offset: int,
    years: int,
    read_cache: QueryReadCache | None = None,
) -> list[dict]:
    """Die Kalenderjahre VOR dem angezeigten Jahr, jedes für sich als Zeitraum
    "Jahr" abgefragt — für den Mehrjahresvergleich im Chart-Editor ("Jahre"
    neben Laufsumme/Soll). years=0 heißt "alle", sonst die Zahl der Vorjahre.

    Neuestes Vorjahr zuerst. Jahre ohne einen einzigen Wert fehlen ganz (Lücken
    werden ausgeblendet, nicht als leere Linie gezeigt), und vor dem Jahr von
    first_ts wird gar nicht erst gesucht. Läuft das angezeigte Jahr noch
    (offset 0), bekommt jedes Vorjahr zusätzlich cut_points: dasselbe Jahr nur
    bis zum selben Abstand vom 1. Januar, wie query_compare_cut() es für das
    eine Vorjahr tut — die Gegenzahl für "bis zum gleichen Tag".

    Bewusst über query_series() statt über eine eigene Lesefunktion: so zeigen
    Mehrjahresvergleich und bestehender Vorjahresvergleich für dasselbe Jahr
    dieselben Monatswerte. monat.parquet liest der read_cache nur einmal.
    chart_type="bar" für alle Typen: Beim Zeitraum "Jahr" liest Linie wie
    Balken dieselbe Monatsstufe, nur hängt die Linie einer Standard-Entität
    einen Randpunkt am 1. Januar an (_boundary_value) — ein Jahr ganz ohne
    Messwerte sähe dadurch aus, als hätte es einen Januar, und fiele nicht
    als Lücke heraus."""
    entity = index.get_entity(entity_id)
    if entity is None or entity["first_ts"] is None:
        return []
    now_local = now.astimezone(tz)
    shown_start, _shown_end, _shown_natural = _window("year", now_local, offset, False)
    first_year = datetime.fromtimestamp(entity["first_ts"], tz).year
    count = MAX_COMPARE_YEARS if years <= 0 else min(years, MAX_COMPARE_YEARS)
    cap_to = now_local - shown_start if min(offset, 0) == 0 else None
    out = []
    for back in range(1, count + 1):
        year = shown_start.year - back
        if year < first_year:
            break
        result = query_series(
            data_dir, index, entity_id, "year", tz, now,
            offset=offset - back, chart_type="bar", read_cache=read_cache,
        )
        if not any(p["value"] is not None for p in result["points"]):
            continue
        entry = {"year": year, "points": result["points"], "cut_points": None}
        if cap_to is not None:
            entry["cut_points"] = query_series(
                data_dir, index, entity_id, "year", tz, now,
                offset=offset - back, chart_type="bar", read_cache=read_cache, cap_to=cap_to,
            )["points"]
        out.append(entry)
    return out


# Zeiträume, in denen eine Zustands-Entität ihre einzelnen Wechsel bekommt;
# darüber Anteile je Bucket (query_state_shares).
STATE_TIMELINE_RANGES = ("hour", "day", "week")

# Bucket der Anteils-Balken je Zeitraum: ein Balken je Tag, Monat bzw. Jahr.
STATE_SHARE_LEVEL = {"hour": "stunde", "day": "stunde", "week": "tag", "month": "tag", "year": "monat", "decade": "jahr"}


def _state_completed_rows(
    data_dir: Path, entity_id: str, start: datetime, end: datetime, tz: ZoneInfo,
    use_month_level: bool, read_cache: QueryReadCache | None,
) -> list[rollup.FineRow]:
    """Abgeschlossene Zustands-Rollups für [start, end).

    Ganze Monate kommen bei Jahr/Dekade aus monat.parquet, angeschnittene
    (rollierendes Fenster) und alle übrigen Zeiträume aus den Stunden — ein
    Jahrzehnt aus Stunden wären rund 90.000 Buckets je Zustand."""
    if end <= start:
        return []
    if not use_month_level:
        return _read_rollup_rows(data_dir, entity_id, "stunde", start.timestamp(), end.timestamp(), read_cache)
    month_key = rollup.named_bucket_key(tz, "monat")
    month_next = rollup.named_bucket_next(tz, "monat")
    first_full = month_key(start.timestamp())
    if first_full < start:
        first_full = month_next(first_full)
    last_full_end = month_key(end.timestamp())
    if last_full_end <= first_full:
        return _read_rollup_rows(data_dir, entity_id, "stunde", start.timestamp(), end.timestamp(), read_cache)
    return (
        _read_rollup_rows(data_dir, entity_id, "stunde", start.timestamp(), first_full.timestamp(), read_cache)
        + _read_rollup_rows(data_dir, entity_id, "monat", first_full.timestamp(), last_full_end.timestamp(), read_cache)
        + _read_rollup_rows(data_dir, entity_id, "stunde", last_full_end.timestamp(), end.timestamp(), read_cache)
    )


def query_state_shares(
    data_dir: Path,
    index: Index,
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    offset: int = 0,
    continuous: bool = False,
    read_cache: QueryReadCache | None = None,
) -> dict:
    """Dauer je Zustand und Bucket für die Anteils-Balken einer Zustands-Entität.

    Abgeschlossene Monate aus den Rollups, der laufende Monat live aus dem Hot
    Buffer — dieselbe Aufteilung wie bei den übrigen Typen, nur mit eigener
    Verdichtung, weil ein Bucket hier eine Verteilung ist und kein Wert.

    Jeder Punkt trägt ``states`` als Liste ``[idx, sekunden, beginne]``;
    ``value`` ist die erfasste Zeit des Buckets (Summe der Sekunden). Ein
    Bucket, aus dem gar nichts vorliegt, obwohl die Entität vorher schon einen
    Zustand hatte, bekommt diesen Zustand über die volle Länge: dort gab es
    einfach keinen Wechsel, und ohne Wechsel entsteht kein Archivmonat.
    """
    if range_key not in RANGE_KEYS:
        raise ValueError(tr("Unbekannter Zeitraum: {range_key}", range_key=range_key))
    entity = index.get_entity(entity_id)
    if entity is None:
        return _missing_entity_result(entity_id, range_key, tz, now, offset, continuous)
    now_local = now.astimezone(tz)
    window_start, window_end, period_end = _window(range_key, now_local, offset, continuous)
    level = STATE_SHARE_LEVEL[range_key]
    key_fn = rollup.named_bucket_key(tz, level)
    next_fn = rollup.named_bucket_next(tz, level)
    current_month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    completed_end = min(window_end, current_month_start)
    rows = _state_completed_rows(
        data_dir, entity_id, window_start, completed_end, tz, range_key in ("year", "decade"), read_cache
    )
    live_start = max(window_start, current_month_start)
    if live_start < window_end:
        hot = _read_hot_rows_filtered(
            data_dir, entity_id, index, now_local.timestamp(), live_start.timestamp(), window_end.timestamp(), tz,
            read_cache,
        )
        boundary = _boundary_value(data_dir, entity_id, live_start.timestamp(), tz, read_cache)
        live, _ = rollup.compute_fine_rollup_with_key(
            hot, STATE_TYPE, key_fn, boundary, window_end.timestamp(),
            bucket_next_fn=next_fn, window_start_ts=live_start.timestamp(),
        )
        rows += live

    grouped: dict[float, list[rollup.FineRow]] = {}
    for row in rows:
        grouped.setdefault(key_fn(row.bucket_start).timestamp(), []).append(row)
    buckets = {start: rollup.merge_state_rows(group, start) for start, group in grouped.items()}

    # Lücken ohne jeden Rollup füllen (siehe Docstring) — nur im
    # abgeschlossenen Teil und erst ab dem ersten Wert der Entität. Der
    # laufende Monat hat keine: dort trägt der Randwert bis zum ersten Wechsel.
    first_ts = entity["first_ts"]
    if first_ts is not None:
        carried: float | None = None
        cursor = key_fn(max(window_start.timestamp(), first_ts))
        limit = completed_end
        while cursor < limit:
            bucket_end = next_fn(cursor)
            start_ts = cursor.timestamp()
            if start_ts in buckets:
                carried = None
            else:
                if carried is None:
                    carried = _boundary_value(data_dir, entity_id, max(start_ts, first_ts), tz, read_cache)
                if carried is not None:
                    span_start = max(start_ts, window_start.timestamp(), first_ts)
                    span_end = min(bucket_end.timestamp(), limit.timestamp())
                    if span_end > span_start:
                        buckets[start_ts] = rollup.FineRow(
                            bucket_start=start_ts, states={int(round(carried)): (span_end - span_start, 0)}
                        )
            cursor = bucket_end

    points = []
    for start_ts in sorted(buckets):
        states = buckets[start_ts].states or {}
        points.append({
            "ts": start_ts,
            "value": sum(seconds for seconds, _entries in states.values()),
            "min": None,
            "max": None,
            "states": [[idx, seconds, entries] for idx, (seconds, entries) in sorted(states.items())],
        })
    return {
        "entity_id": entity_id,
        "aggregation_type": STATE_TYPE,
        "chart_type": "shares",
        "bucket": level,
        "points": points,
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
        "elapsed_seconds": None,
        "states": state_legend(index, entity_id),
    }


# Zeiträume der Zustands-Heatmap. In einer Woche gäbe es je Zelle nur eine
# Beobachtung (ein Wochentag kommt einmal vor), in einem Tag gar keine Wochentage.
STATE_HEATMAP_RANGES = ("month", "year", "decade")

# Eine Stunde zählt als Beobachtung, wenn mindestens so viele Sekunden davon
# in einem verfügbaren Zustand liegen. Sonst zählte die angebrochene Stunde am
# Fensterende (oder eine fast ganz ausgefallene) wie eine volle.
HEATMAP_MIN_COVERAGE_SECONDS = 1800.0


def _state_hour_rows(
    data_dir: Path, index: Index, first_ts: float | None, entity_id: str, tz: ZoneInfo, now_local: datetime,
    window_start: datetime, window_end: datetime, read_cache: QueryReadCache | None,
) -> list[rollup.FineRow]:
    """Eine Zeile je Stunde und Zustand für [window_start, window_end).

    Abgeschlossene Monate aus stunde.parquet, der laufende live aus dem Hot
    Buffer. Ein Archivmonat entsteht nur durch einen Wechsel: Monate dazwischen
    ohne jeden bekommen den vorher geltenden Zustand für jede ihrer Stunden —
    dieselbe Regel wie bei den Anteilen, nur eben stundenweise. Innerhalb eines
    Archivmonats verteilt der Rollup einen langen Zustand schon selbst auf alle
    Stunden, dort ist nichts aufzufüllen.
    """
    key_fn = rollup.named_bucket_key(tz, "stunde")
    next_fn = rollup.named_bucket_next(tz, "stunde")
    month_key = rollup.named_bucket_key(tz, "monat")
    month_next = rollup.named_bucket_next(tz, "monat")
    current_month_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    completed_end = min(window_end, current_month_start)

    rows: list[rollup.FineRow] = []
    if window_start < completed_end:
        rows += _read_rollup_rows(
            data_dir, entity_id, "stunde", window_start.timestamp(), completed_end.timestamp(), read_cache
        )
    live_start = max(window_start, current_month_start)
    if live_start < window_end:
        hot = _read_hot_rows_filtered(
            data_dir, entity_id, index, now_local.timestamp(), live_start.timestamp(), window_end.timestamp(), tz,
            read_cache,
        )
        boundary = _boundary_value(data_dir, entity_id, live_start.timestamp(), tz, read_cache)
        live, _ = rollup.compute_fine_rollup_with_key(
            hot, STATE_TYPE, key_fn, boundary, window_end.timestamp(),
            bucket_next_fn=next_fn, window_start_ts=live_start.timestamp(),
        )
        rows += live

    if first_ts is None:
        return rows
    covered = {month_key(row.bucket_start).timestamp() for row in rows}
    carried: float | None = None
    cursor = month_key(max(window_start.timestamp(), first_ts))
    while cursor < completed_end:
        month_end = month_next(cursor)
        if cursor.timestamp() in covered:
            carried = None
        else:
            span_start = max(cursor.timestamp(), window_start.timestamp(), first_ts)
            span_end = min(month_end.timestamp(), completed_end.timestamp())
            if carried is None:
                carried = _boundary_value(data_dir, entity_id, span_start, tz, read_cache)
            if carried is not None and span_end > span_start:
                idx = int(round(carried))
                hour = key_fn(span_start)
                while hour.timestamp() < span_end:
                    hour_end = next_fn(hour)
                    seconds = min(hour_end.timestamp(), span_end) - max(hour.timestamp(), span_start)
                    rows.append(rollup.FineRow(bucket_start=hour.timestamp(), states={idx: (seconds, 0)}))
                    hour = hour_end
        cursor = month_end
    return rows


def query_state_heatmap(
    data_dir: Path,
    index: Index,
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    offset: int = 0,
    continuous: bool = False,
    read_cache: QueryReadCache | None = None,
) -> dict:
    """Zeit je Zustand nach Wochentag und Stunde (Ortszeit) für die Heatmap
    einer Zustands-Entität (ROADMAP 1.29, Phase 3).

    Kein eigener Rollup: stunde.parquet hat Sekunden je Stunde und Zustand,
    Wochentag und Stunde ergeben sich beim Abfragen. ``cells`` ist eine Liste
    ``[wochentag, stunde, beobachtungen, verfügbare_sekunden, [[idx, sekunden], …]]``
    (Montag = 0), nur für Zellen mit Daten. Der Anteil eines Zustands in einer
    Zelle ist ``sekunden / verfügbare_sekunden``: "nicht verfügbar" steht in
    der Liste, zählt aber nicht in den Nenner — ein Ausfall soll keine falsche
    Ruhe vortäuschen.

    Sommer-/Winterzeit: die doppelte Stunde im Herbst zählt doppelt in
    derselben Zelle, die fehlende im Frühjahr gar nicht; der Nenner folgt den
    tatsächlich vorhandenen Sekunden. Ein rollierendes Fenster beginnt mitten
    in einer Stunde; deren angebrochener Anfang fehlt.
    """
    if range_key not in STATE_HEATMAP_RANGES:
        raise ValueError(tr("Unbekannter Zeitraum: {range_key}", range_key=range_key))
    entity = index.get_entity(entity_id)
    if entity is None:
        return _missing_entity_result(entity_id, range_key, tz, now, offset, continuous)
    now_local = now.astimezone(tz)
    window_start, window_end, period_end = _window(range_key, now_local, offset, continuous)
    rows = _state_hour_rows(
        data_dir, index, entity["first_ts"], entity_id, tz, now_local, window_start, window_end, read_cache
    )

    by_hour: dict[float, list[rollup.FineRow]] = {}
    for row in rows:
        by_hour.setdefault(row.bucket_start, []).append(row)
    cells: dict[tuple[int, int], dict] = {}
    for start_ts, group in by_hour.items():
        local = datetime.fromtimestamp(start_ts, tz)
        cell = cells.setdefault((local.weekday(), local.hour), {"seconds": {}, "available": 0.0, "n": 0})
        available = 0.0
        for idx, (seconds, _entries) in rollup.merge_state_rows(group, start_ts).states.items():
            cell["seconds"][idx] = cell["seconds"].get(idx, 0.0) + seconds
            if idx != STATE_UNAVAILABLE_INDEX:
                available += seconds
        cell["available"] += available
        if available >= HEATMAP_MIN_COVERAGE_SECONDS:
            # Der Rollup-Bucket der Herbst-Umstellung rechnet in Wanduhrzeit
            # und fasst die beiden 2-Uhr-Stunden zu einem Bucket mit 7200 s
            # zusammen — der Anteil stimmt, die Zahl der Beobachtungen
            # folgt deshalb den Sekunden.
            cell["n"] += max(1, int(available // 3600))

    return {
        "entity_id": entity_id,
        "aggregation_type": STATE_TYPE,
        "chart_type": "heatmap",
        "bucket": "stunde",
        "cells": [
            [weekday, hour, cell["n"], round(cell["available"], 1),
             [[idx, round(seconds, 1)] for idx, seconds in sorted(cell["seconds"].items())]]
            for (weekday, hour), cell in sorted(cells.items())
        ],
        "points": [],
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
        "elapsed_seconds": None,
        "states": state_legend(index, entity_id),
    }


# Zeiträume, für die der Server den Zeitstrahl einer Zustands-Entität in Spalten
# vorrechnet (Pixelverdichtung, ROADMAP 1.29 Schritt 2): dort können die
# Rohwechsel über MAX_RAW_QUERY_POINTS liegen, und die Rohabfrage bricht dann ab.
STATE_COLUMNS_RANGES = ("year", "decade")
STATE_COLUMNS_MIN = 50
STATE_COLUMNS_MAX = 2000


def query_state_columns(
    data_dir: Path,
    index: Index,
    entity_id: str,
    range_key: str,
    tz: ZoneInfo,
    now: datetime,
    px: int,
    offset: int = 0,
    continuous: bool = False,
    read_cache: QueryReadCache | None = None,
) -> dict:
    """Spalten für den Zeitstrahl einer Zustands-Entität über ein Jahr oder eine
    Dekade: das Fenster ``[window_start, period_end)`` in ``px`` gleich breite
    Spalten geteilt, je Spalte die Sekunden und Beginne je Zustand.

    Quelle sind die Stunden-Rollups (wie bei der Heatmap), also höchstens eine
    Stunde fein: eine Stunde, die über eine Spaltengrenze reicht, wird nach
    Zeitanteilen auf beide verteilt, ihre Beginne zählen in der Spalte, in der
    sie anfängt. Schmaler als eine Stunde wird eine Spalte damit nicht genauer,
    das reicht für Jahr (eine Spalte bei 900 Bildpunkten rund 10 Stunden) und
    Dekade (rund 4 Tage).

    ``columns`` ist eine Liste ``[beginn, [[idx, sekunden, beginne], …]]``,
    nur für Spalten mit Daten; ``dt`` die Breite einer Spalte in Sekunden.
    """
    if range_key not in STATE_COLUMNS_RANGES:
        raise ValueError(tr("Unbekannter Zeitraum: {range_key}", range_key=range_key))
    entity = index.get_entity(entity_id)
    if entity is None:
        return _missing_entity_result(entity_id, range_key, tz, now, offset, continuous)
    px = max(STATE_COLUMNS_MIN, min(STATE_COLUMNS_MAX, int(px)))
    now_local = now.astimezone(tz)
    window_start, window_end, period_end = _window(range_key, now_local, offset, continuous)
    rows = _state_hour_rows(
        data_dir, index, entity["first_ts"], entity_id, tz, now_local, window_start, window_end, read_cache
    )

    next_fn = rollup.named_bucket_next(tz, "stunde")
    start_ts = window_start.timestamp()
    dt = (period_end.timestamp() - start_ts) / px
    by_hour: dict[float, list[rollup.FineRow]] = {}
    for row in rows:
        by_hour.setdefault(row.bucket_start, []).append(row)

    columns: dict[int, dict[int, list[float]]] = {}

    def add(column: int, idx: int, seconds: float, begins: int) -> None:
        cell = columns.setdefault(column, {}).setdefault(idx, [0.0, 0])
        cell[0] += seconds
        cell[1] += begins

    for hour_start, group in by_hour.items():
        hour_end = next_fn(datetime.fromtimestamp(hour_start, tz)).timestamp()
        first = min(px - 1, max(0, int((hour_start - start_ts) // dt)))
        last = min(px - 1, max(first, int((hour_end - start_ts - 1e-6) // dt)))
        span = max(hour_end - hour_start, 1.0)
        for idx, (seconds, entries) in rollup.merge_state_rows(group, hour_start).states.items():
            if first == last:
                add(first, idx, seconds, entries)
                continue
            for column in range(first, last + 1):
                overlap = min(hour_end, start_ts + (column + 1) * dt) - max(hour_start, start_ts + column * dt)
                if overlap > 0:
                    add(column, idx, seconds * overlap / span, entries if column == first else 0)

    return {
        "entity_id": entity_id,
        "aggregation_type": STATE_TYPE,
        "chart_type": "columns",
        "bucket": "stunde",
        "dt": dt,
        "columns": [
            [start_ts + column * dt, [[idx, round(cell[0], 1), cell[1]] for idx, cell in sorted(states.items())]]
            for column, states in sorted(columns.items())
        ],
        "points": [],
        "window_start": start_ts,
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
        "elapsed_seconds": None,
        "states": state_legend(index, entity_id),
    }


def state_durations(result: dict) -> dict[int, float]:
    """Sekunden je Zustand aus einer Antwort von query_series() — egal ob sie
    Rohwechsel (bis zur Woche) oder Anteile je Bucket (ab dem Monat) trägt."""
    totals: dict[int, float] = {}
    points = result.get("points") or []
    if result.get("chart_type") == "shares":
        for point in points:
            for idx, seconds, _entries in point["states"]:
                totals[idx] = totals.get(idx, 0.0) + seconds
        return totals
    end = result.get("window_end") or 0.0
    for i, point in enumerate(points):
        if point["value"] is None:
            continue
        stop = points[i + 1]["ts"] if i + 1 < len(points) else end
        if stop > point["ts"]:
            idx = int(round(point["value"]))
            totals[idx] = totals.get(idx, 0.0) + (stop - point["ts"])
    return totals


def state_since(data_dir: Path, index: Index, entity_id: str, tz: ZoneInfo, now: datetime) -> float | None:
    """Seit wann der aktuelle Zustand gilt — für "seit 2 h 14 min" auf der
    Kachel. Gesucht wird in wachsenden Fenstern rückwärts, weil der letzte
    Wechsel meist Minuten, manchmal aber Wochen zurückliegt; ohne Wechsel im
    größten Fenster gilt der älteste gelesene Wert."""
    entity = index.get_entity(entity_id)
    if entity is None or entity["first_ts"] is None:
        return None
    now_ts = now.timestamp()
    earliest = None
    for days in (1, 7, 31, 366):
        start_ts = max(now_ts - days * 86400, entity["first_ts"])
        rows = list(cleanup.iter_raw_rows(
            data_dir, index, entity_id, start_ts, now_ts, tz, now=now.astimezone(tz), max_rows=MAX_RAW_QUERY_POINTS,
        ))
        if rows:
            current = int(round(rows[-1][1]))
            since = rows[-1][0]
            for ts, value in reversed(rows):
                if int(round(value)) != current:
                    return since
                since = ts
            earliest = since
        if start_ts <= entity["first_ts"]:
            break
    return earliest


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
    Rollierend-Modus verhalten sich identisch zur gebucketen Ansicht), aber
    immer als Linie — Balken pro Rohwert würden bei tausenden Punkten nur noch
    als flächige Masse erscheinen, nicht als lesbares Diagramm."""
    if range_key not in RANGE_KEYS:
        raise ValueError(tr("Unbekannter Zeitraum: {range_key}", range_key=range_key))

    entity = index.get_entity(entity_id)
    if entity is None:
        return _missing_entity_result(entity_id, range_key, tz, now, offset, continuous)

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

    result = {
        "entity_id": entity_id,
        "aggregation_type": aggregation_type,
        "chart_type": "line",
        "points": points,
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "period_end": period_end.timestamp(),
        "is_current": offset == 0,
    }
    if aggregation_type == STATE_TYPE:
        result["chart_type"] = "timeline"
        result["states"] = state_legend(index, entity_id)
    return result


def state_text(index: Index, entity_id: str, value: float) -> str:
    """Anzeigetext eines Zustandsindex: HA-Übersetzung, sonst Rohwert."""
    idx = int(round(value))
    if idx == STATE_UNAVAILABLE_INDEX:
        return tr("nicht verfügbar")
    for row in index.get_state_map(entity_id):
        if row["idx"] == idx:
            return row["label"] or row["text"]
    return str(idx)


def state_legend(index: Index, entity_id: str) -> list[dict]:
    """Zuordnung Index → Text einer Zustands-Entität für die Anzeige, nach
    Index sortiert, "nicht verfügbar" immer zuletzt. ``label`` ist die
    HA-Übersetzung oder None; die Oberfläche fällt dann auf ``text`` zurück."""
    legend = index.get_state_map(entity_id)
    legend.append({"idx": STATE_UNAVAILABLE_INDEX, "text": "unavailable", "label": None})
    return legend
