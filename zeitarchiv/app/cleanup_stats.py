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

from .formatting import OUTLIER_THRESHOLD_LABELS, format_int, format_value
from .storage import cleanup
from .storage import query as query_mod
from .storage.index import Index, effective_outlier_threshold

# Zeiträume der Bereinigungsseite — dieselben Perioden wie im Chart
# (entity_detail.html/query._window(), Konsistenz zwischen beiden Werkzeugen),
# nur ohne "decade" (bei Rohwert-Zeilen wenig sinnvoll) und dafür mit "all" als
# Bereinigungs-spezifischer Ergänzung ohne Chart-Entsprechung.
CLEANUP_RANGE_KEYS = ("hour", "day", "week", "month", "year", "all")

ALLTIME_STATS_MAX_AGE_SECONDS = 15 * 60

# Ab welcher Markierungsquote eine Entität in Housekeeping → Ausreißer
# auftaucht. Gemessen an der Testinstallation (34 Entitäten, komplette
# Historie, jeweils eingestellte Schwelle): die höchste Quote liegt bei 1,65 %
# (PV-Leistung, sehr zackiges Signal an einer engen Schwelle), alle übrigen bei
# 0,44 % oder darunter. Eine Marke bei 1 % trennt damit genau den Fall ab, der
# gemeint ist — eine Schwelle, die für ihr Signal zu eng steht —, ohne die
# ruhigen Sensoren mitzunehmen.
#
# Die Marke ist bewusst niedrig: Ausreißer sind per Definition selten. Wer
# jeden hundertsten Wert markiert, sucht keine Fehler mehr, sondern schneidet
# das Signal.
OUTLIER_RATE_NOTABLE_PERCENT = 1.0

# Wie alt eine gecachte Gesamt-Zählung werden darf, bevor der Wartungsplaner
# sie im Hintergrund erneuert. Deutlich länger als ALLTIME_STATS_MAX_AGE_
# SECONDS (das gilt für die gerade angesehene Entität): ein Vollscan kostete an
# der Testinstallation 37 s für alle 34 Entitäten, bei einer je 30-Sekunden-
# Takt also gut eine Viertelstunde für einen Durchgang. Sechs Stunden lassen
# genug Luft, dass der Planer nicht dauernd scannt, und halten die Liste
# trotzdem tagesaktuell.
OUTLIER_RATE_REFRESH_AGE_SECONDS = 6 * 60 * 60


def outlier_mode(entity) -> str:
    """Bezugsgröße der Ausreißer-Erkennung (siehe cleanup.OutlierDetector).

    Eine einzige Stelle, weil beide Zeilen-Pfade der Bereinigungsseite und die
    Gesamt-Statistik sie brauchen — zwei Kopien, die auseinanderlaufen, waren
    genau der Fehler, den die Vereinheitlichung der Regel beseitigt hat."""
    return "counter" if entity["aggregation_type"] == "counter" else "standard"


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
        outlier_factor=None if outlier_threshold == "off" else float(outlier_threshold),
        tz=tz,
        decimals=entity["decimals"],
        counter_decrease_enabled=entity["state_class"] == "total_increasing",
        outlier_mode=outlier_mode(entity),
    )
    counts = analysis["counts"]
    index.set_cleanup_alltime_stats(
        entity_id, counts, outlier_threshold, cleanup.OUTLIER_RULE_VERSION
    )
    return counts


def outlier_rate(index: Index, entity) -> dict | None:
    """Markierungsquote der Ausreißer-Erkennung EINER Entität (Anzeige unter
    dem Schwellenfeld), oder None wenn keine belastbare Zahl vorliegt.

    None heißt hier ausdrücklich "noch nicht berechnet", nicht "null Prozent" —
    die Oberfläche bietet dann das Nachrechnen an, statt eine Zahl zu erfinden.
    Drei Gründe für None: der Typ kennt die Erkennung nicht bzw. sie steht auf
    "Aus", es gibt noch keinen Cache-Eintrag, oder der Eintrag wurde mit einer
    ANDEREN Schwelle gezählt als der jetzt eingestellten. Der letzte Fall ist
    der wichtige (siehe _rate_from_entry).

    Bewusst KEINE Altersgrenze: der Wert altert mit neuen Messwerten, aber
    langsam, und "vor 3 Stunden gerechnet" mit Zeitangabe ist ehrlicher als
    ein stiller Vollscan bei jedem Öffnen der Konfiguration."""
    return _rate_from_entry(entity, index.get_cleanup_alltime_stats(entity["entity_id"]))

def _rate_from_entry(entity, eintrag: dict | None) -> dict | None:
    """Gemeinsamer Kern von outlier_rate() und outlier_rate_overview(): aus
    einem Cache-Eintrag eine Quote machen — oder None, wenn er nicht zur
    aktuell eingestellten Schwelle gehört. Eine Zahl neben einer Schwelle
    anzuzeigen, zu der sie nicht gehört, wäre schlimmer als keine Zahl."""
    schwelle = effective_outlier_threshold(
        entity["aggregation_type"], entity["outlier_threshold"]
    )
    if schwelle == "off":
        return None
    if eintrag is None or eintrag.get("outlier_threshold") != schwelle:
        return None
    # Schwelle allein reicht nicht: die Umstellung von Prozent auf Vielfache
    # ließ "50" ein gültiger Wert bleiben, und ein damit gezähltes altes
    # Ergebnis sah danach aus wie ein aktuelles (siehe OUTLIER_RULE_VERSION).
    if eintrag.get("outlier_rule") != cleanup.OUTLIER_RULE_VERSION:
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
        "threshold": schwelle,
    }


def rate_labels(rate: dict) -> dict:
    """Fertige Beschriftungen einer Quote für die Vorlagen — Prozent mit Komma,
    Zahlen mit Tausenderpunkt, Schwelle als "50×". Hier statt in jeder Vorlage,
    weil Jinja das nicht sprachrichtig kann, und hier statt zweimal, weil
    dieselbe Quote am Konfigurationsfeld UND in Housekeeping steht."""
    return dict(
        rate,
        percent_label=format_value(rate["percent"], 2),
        marked_label=format_int(rate["marked"]),
        total_label=format_int(rate["total"]),
        threshold_label=OUTLIER_THRESHOLD_LABELS.get(
            rate["threshold"], rate["threshold"]
        ),
    )


def outlier_rate_overview(index: Index) -> dict:
    """Markierungsquoten ALLER Entitäten aus dem vorhandenen Cache — Grundlage
    für Housekeeping → Ausreißer und die zugehörige Meldung.

    Rechnet selbst nichts nach: die Zahlen sind exakt die, die auch am
    Konfigurationsfeld der jeweiligen Entität stehen (siehe outlier_rate()).
    Zwei Stellen mit zwei verschieden gemessenen "Markierungsquoten" wären
    genau die Verwirrung, die eine Übersicht auflösen soll.

    Gefüllt wird der Cache vom Wartungsplaner (eine Entität je Takt, siehe
    background.py) und nebenbei von jedem Besuch der Bereinigungsseite.

    - ``rows``: auffällige Entitäten, absteigend nach Quote
    - ``highest``: die höchste gemessene Quote überhaupt, auch unauffällig —
      damit ein leerer Abschnitt belegen kann, dass gemessen wurde
    - ``measured``/``pending``: wie weit der Hintergrundlauf ist. Ohne diese
      Zahl sähe "nichts gefunden" genauso aus wie "noch nichts gemessen".
    """
    eintraege = index.list_cleanup_alltime_stats()
    gemessen: list[dict] = []
    offen = 0
    for entity in index.list_entities():
        if effective_outlier_threshold(
            entity["aggregation_type"], entity["outlier_threshold"]
        ) == "off":
            continue
        quote = _rate_from_entry(entity, eintraege.get(entity["entity_id"]))
        if quote is None:
            offen += 1
            continue
        gemessen.append(rate_labels({
            "entity_id": entity["entity_id"],
            "friendly_name": (
                entity["custom_name"] or entity["friendly_name"] or entity["entity_id"]
            ),
            **quote,
        }))
    gemessen.sort(key=lambda r: r["percent"], reverse=True)
    return {
        "rows": [r for r in gemessen if r["percent"] >= OUTLIER_RATE_NOTABLE_PERCENT],
        "highest": gemessen[0] if gemessen else None,
        "measured": len(gemessen),
        "pending": offen,
    }


def next_entity_for_outlier_rate(index: Index, now: float):
    """Die eine Entität, deren Gesamt-Zählung der Wartungsplaner als Nächstes
    erneuern soll — oder None, wenn nichts ansteht.

    Reihenfolge: erst die nie gemessenen bzw. die mit veralteter Schwelle
    (deren Quote zeigt die Oberfläche gar nicht an), dann die ältesten. Eine je
    Takt statt alle auf einmal, weil ein Durchgang über alle Entitäten an der
    Testinstallation 37 s dauert — das gehört nicht in einen 30-Sekunden-Takt
    am Stück, wohl aber verteilt darauf."""
    eintraege = index.list_cleanup_alltime_stats()
    faellig = []
    for entity in index.list_entities():
        if effective_outlier_threshold(
            entity["aggregation_type"], entity["outlier_threshold"]
        ) == "off" or not entity["first_ts"]:
            continue
        eintrag = eintraege.get(entity["entity_id"])
        if _rate_from_entry(entity, eintrag) is None:
            faellig.append((0.0, entity))
            continue
        berechnet = eintrag.get("computed_at") or 0.0
        if now - berechnet >= OUTLIER_RATE_REFRESH_AGE_SECONDS:
            faellig.append((berechnet, entity))
    if not faellig:
        return None
    return min(faellig, key=lambda paar: paar[0])[1]
