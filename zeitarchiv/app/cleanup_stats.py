"""Zeitfenster und Gesamtzahlen der Bereinigungsseite.

Beides ohne jeden Routen-Bezug und ohne Zustand — hier statt in main.py, weil
es reine Analyse ist und weil die Datei unter einer Zeilengrenze steht
(tests/test_route_modules.py). Nicht in storage/cleanup.py: das darf
storage/query.py nicht importieren, weil query seinerseits cleanup importiert
(Zyklus). Dieses Modul liegt eine Ebene darüber und darf beide kennen.

Die Abhängigkeiten (Datenverzeichnis, Index, Zeitzone) kommen als Argumente
herein statt als Modul-Globals — dieselbe Form wie route_support.py.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import cleanup
from .storage import query as query_mod
from .storage.index import Index, effective_outlier_threshold

# Zeiträume der Bereinigungsseite — dieselben Perioden wie im Chart
# (entity_detail.html/query._window(), Konsistenz zwischen beiden Werkzeugen),
# nur ohne "decade" (bei Rohwert-Zeilen wenig sinnvoll) und dafür mit "all" als
# Bereinigungs-spezifischer Ergänzung ohne Chart-Entsprechung.
CLEANUP_RANGE_KEYS = ("hour", "day", "week", "month", "year", "all")

ALLTIME_STATS_MAX_AGE_SECONDS = 15 * 60


def rows_window(
    range_key: str, offset: int, now: datetime, first_ts: float | None, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """[Anfang, Ende) für die Bereinigungsseite. Kalendarische Zeiträume kommen
    1:1 aus query._window() — dieselbe Perioden-Logik wie im Chart (offset 0 =
    aktuelle, kalendarisch verankerte Periode bis "jetzt", -1 = eine Periode
    zurück, …). "all" hat dort keine Entsprechung: deckt stattdessen den
    kompletten Datenbestand der Entität ab (seit dem ersten Rohwert) und kennt
    keine Navigation (offset wird vom Aufrufer immer auf 0 gehalten)."""
    if range_key not in CLEANUP_RANGE_KEYS:
        range_key = "day"
    if range_key == "all":
        start = datetime.fromtimestamp(first_ts, tz) if first_ts else now
        return start, now
    start, end, _period_end = query_mod._window(range_key, now, min(offset, 0), continuous=False)
    return start, end


_CLEANUP_ALLTIME_STATS_MAX_AGE_SECONDS = 15 * 60


def alltime_counts(
    data_dir: Path, index: Index, tz: ZoneInfo, entity, now: datetime, *, force: bool = False
) -> dict:
    """Ausreißer/Lücken/Duplikate/Wiederholungen/Zählerrückgänge über die
    KOMPLETTE Historie der Entität, nicht nur den gerade gewählten Zeitraum
    (Bereinigungsseite, Kachel-Zeile "Gesamter Zeitraum") — gecacht für
    15 Minuten (index.is_cleanup_alltime_stats_stale), weil ein Vollscan bei
    Entitäten mit Millionen Rohwerten sonst bei jedem Seitenaufruf bzw. jedem
    Filterklick teuer wäre. Nutzt denselben speicherbegrenzten Streaming-Pfad
    wie der Zeitraum "Gesamt" im Chip (analyze_raw_rows_page, zwei Durchläufe
    ohne Materialisierung aller Zeilen), page_size=1 weil hier nur die
    counts gebraucht werden, keine Zeilenliste."""
    entity_id = entity["entity_id"]
    if not force and not index.is_cleanup_alltime_stats_stale(
        entity_id, ALLTIME_STATS_MAX_AGE_SECONDS
    ):
        cached = index.get_cleanup_alltime_stats(entity_id)
        if cached is not None:
            return cached["counts"]

    window_start, window_end = rows_window("all", 0, now, entity["first_ts"], tz)
    gap_threshold = entity["gap_threshold"]
    outlier_threshold = effective_outlier_threshold(
        entity["aggregation_type"], entity["outlier_threshold"]
    )
    # Siehe _rows_fragment(): analyze_raw_rows_page() ruft rows_factory()
    # zweimal auf, ohne Cache liest das bei "Gesamt" die laufende Monats-CSV
    # doppelt (PERFORMANCE.md, ZP-012).
    read_cache = query_mod.QueryReadCache()

    def rows_factory():
        return cleanup.iter_raw_rows(
            data_dir, index, entity_id,
            window_start.timestamp(), window_end.timestamp(), tz, now=now,
            hot_rows_loader=read_cache.read_hot_rows,
        )

    analysis = cleanup.analyze_raw_rows_page(
        rows_factory,
        filter_="all",
        page=1,
        page_size=1,
        gap_threshold_minutes=None if gap_threshold == "off" else float(gap_threshold),
        outlier_threshold_percent=None if outlier_threshold == "off" else float(outlier_threshold),
        tz=tz,
        decimals=entity["decimals"],
        counter_decrease_enabled=entity["state_class"] == "total_increasing",
        outlier_mode="counter" if entity["aggregation_type"] == "counter" else "standard",
    )
    counts = analysis["counts"]
    index.set_cleanup_alltime_stats(entity_id, counts, outlier_threshold)
    return counts


def outlier_rate(index: Index, entity) -> dict | None:
    """Markierungsquote der Ausreißer-Erkennung, oder None wenn keine
    belastbare Zahl vorliegt.

    None heißt hier ausdrücklich "noch nicht berechnet", nicht "null Prozent" —
    die Oberfläche bietet dann das Nachrechnen an, statt eine Zahl zu erfinden.
    Drei Gründe für None:

    - für diesen Entitätstyp gilt die Erkennung gar nicht (Zähler/Schalter),
    - es gibt noch keinen Cache-Eintrag (niemand war auf der Bereinigungsseite),
    - der Eintrag wurde mit einer ANDEREN Schwelle gezählt als der jetzt
      eingestellten. Das ist der wichtige Fall: eine Quote neben einer Schwelle
      anzuzeigen, zu der sie nicht gehört, wäre schlimmer als keine Quote.

    Bewusst KEINE Altersgrenze: der Wert altert mit neuen Messwerten, aber
    langsam, und "vor 3 Stunden gerechnet" mit Zeitangabe ist ehrlicher als
    ein stiller Vollscan bei jedem Öffnen der Konfiguration.
    """
    schwelle = effective_outlier_threshold(
        entity["aggregation_type"], entity["outlier_threshold"]
    )
    if schwelle == "off":
        return None
    eintrag = index.get_cleanup_alltime_stats(entity["entity_id"])
    if eintrag is None or eintrag.get("outlier_threshold") != schwelle:
        return None
    counts = eintrag.get("counts") or {}
    gesamt = counts.get("all") or 0
    if not gesamt:
        return None
    markiert = counts.get("outliers") or 0
    return {
        "marked": markiert,
        "total": gesamt,
        "percent": markiert / gesamt * 100,
        "computed_at": eintrag.get("computed_at"),
    }
