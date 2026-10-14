"""Housekeeping → Datenqualität: Befunde aller Entitäten in einer Matrix.

Alle drei Spalten zählen die GESAMTE Historie und kommen aus demselben Cache wie
die Trefferzahlen der Bereinigungsseite (``cleanup_alltime_stats``, je Entität
im Hintergrund erneuert, siehe cleanup_stats.next_entity_for_outlier_rate).
Die Matrix rechnet nichts nach und liest keine Rohdaten: dieselbe Zahl steht
damit hier und im Filter-Dropdown der Bereinigungsseite bei "Gesamt". Zwei
verschieden gemessene Zahlen für dasselbe wären genau die Verwirrung, die eine
Übersicht auflösen soll.

Die Abschnitte Duplikate und Zählerrückgänge in Housekeeping zählen dagegen
weiter die letzten 30 Tage (eigene Schnappschüsse, auch Grundlage der Meldungen).
Die Zahlen dort sind deshalb kleiner oder gleich denen der Matrix.

Ohne Routen-Bezug und ohne Zustand, wie cleanup_stats.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from . import cleanup_stats, formats
from .i18n import tr
from .formatting import decimals_to_int, format_int, format_value
from .storage import cleanup
from .storage.index import Index, effective_outlier_threshold

# Reihenfolge = Spaltenreihenfolge. "filter" und "range" sind die Startwerte der
# Bereinigungsseite (siehe initial_cleanup_view()).
COLUMNS: tuple[dict, ...] = (
    {"key": "duplicates", "filter": "duplicates", "range": "all", "severity": "info"},
    {"key": "counter_decreases", "filter": "counter_decreases", "range": "all", "severity": "crit"},
    {"key": "outliers", "filter": "outliers", "range": "all", "severity": "warn"},
    {"key": "gaps", "filter": "gaps", "range": "all", "severity": "warn"},
)

CLEANUP_FILTERS = ("all", "outliers", "gaps", "duplicates", "repetitions", "counter_decreases")
CLEANUP_RANGES = ("hour", "day", "week", "month", "year", "all")


def initial_cleanup_view(
    filter_: str, range_: str, *, outliers: bool, gaps: bool, counter_decreases: bool
) -> tuple[str, str]:
    """Startwerte (Filter, Zeitraum) der Bereinigungsseite aus ?filter=&range=.

    Unbekannte Werte fallen auf "all" bzw. "day" zurück. Ein Filter, dessen
    Erkennung für die Entität aus ist, wird ebenfalls verworfen: das Dropdown
    bietet ihn dort nicht an, und eine Auswahl, die man nicht mehr abwählen
    kann, wäre schlimmer als die Standardansicht."""
    available = {
        "outliers": outliers, "gaps": gaps, "counter_decreases": counter_decreases,
    }
    if filter_ not in CLEANUP_FILTERS or not available.get(filter_, True):
        filter_ = "all"
    if range_ not in CLEANUP_RANGES:
        range_ = "day"
    return filter_, range_


# Wie viele Einzelbefunde je Kategorie in der aufgeklappten Zeile stehen. Der
# Rest steht hinter dem Link auf die Bereinigungsseite.
DETAIL_LIMIT = 5


PAGE_SIZES = (10, 20, 50, 100, 500)
DEFAULT_PAGE_SIZE = 50


def paginate(rows: list[dict], page: int, page_size: int) -> tuple[list[dict], dict]:
    """Eine Seite der Matrix und die Angaben für den Pager (dieselben Felder wie
    die Zeilentabelle der Bereinigungsseite). Unbekannte Seitengrößen fallen auf
    DEFAULT_PAGE_SIZE zurück, eine Seite außerhalb des Bereichs wird eingeklemmt."""
    if page_size not in PAGE_SIZES:
        page_size = DEFAULT_PAGE_SIZE
    total = len(rows)
    total_pages = max(1, -(-total // page_size))
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return rows[start:start + page_size], {
        "page": page,
        "page_size": page_size,
        "page_sizes": PAGE_SIZES,
        "total": total,
        "total_pages": total_pages,
        "start": start + 1 if total else 0,
        "end": min(start + page_size, total),
    }


def cleanup_href(entity_id: str, filter_: str, range_: str) -> str:
    """Relativer Link (wie die übrigen Links auf der Housekeeping-Seite) auf die
    Bereinigungsseite einer Entität mit gesetztem Filter und Zeitraum."""
    query = urlencode({"filter": filter_, "range": range_})
    return f"entities/{quote(entity_id, safe='.')}/cleanup?{query}"


HEATMAP_DAYS = (30, 90, 180)
DEFAULT_HEATMAP_DAYS = 30
HEATMAP_KINDS = ("all", "duplicates", "counter_decreases", "outliers", "gaps")
HEATMAP_LEVELS = 4


def offset_for_day(day: str | None, today: date) -> int:
    """Wie viele Tage die Bereinigungsseite im Zeitraum "Tag" zurückgehen muss, um
    ``day`` (JJJJ-MM-TT) zu zeigen: 0 für heute, negativ davor. Ungültig oder in
    der Zukunft ergibt 0 (die Seite zeigt dann den heutigen Tag, wie ohne Angabe)."""
    if not day:
        return 0
    try:
        target = date.fromisoformat(day)
    except ValueError:
        return 0
    return min(0, (target - today).days)


def heatmap_href(entity_id: str, kind: str, day: str) -> str:
    """Link eines Heatmap-Feldes: Bereinigungsseite der Entität, Zeitraum "Tag" mit
    genau diesem Tag. Bei "alle Befunde" bleibt der Filter auf "Alle"."""
    query = urlencode({"filter": kind, "range": "day", "date": day})
    return f"entities/{quote(entity_id, safe='.')}/cleanup?{query}"


def _level(count: int, peak: int) -> int:
    """Farbstufe 1…HEATMAP_LEVELS, logarithmisch: Ausreißer-Tage mit Tausenden
    Treffern würden sonst alle übrigen Tage auf Stufe 1 drücken."""
    if count <= 0 or peak <= 0:
        return 0
    return max(1, min(HEATMAP_LEVELS, math.ceil(math.log1p(count) / math.log1p(peak) * HEATMAP_LEVELS)))


def build_heatmap(index: Index, *, days: int, kind: str, today: date) -> dict:
    """Heatmap Entität × Tag aus den Tageszählern des Gesamt-Caches.

    Liest nur den Cache (``daily``), keine Rohdaten. Dieselben Gültigkeitsregeln
    wie die Matrix: Ausreißer- und Lücken-Tage zählen nur, wenn die Zahl mit der
    aktuellen Schwelle gemessen wurde. Entitäten ohne ``daily`` (noch nicht
    seit der Einführung der Tageszähler neu gemessen) sind ``pending``.
    """
    if days not in HEATMAP_DAYS:
        days = DEFAULT_HEATMAP_DAYS
    if kind not in HEATMAP_KINDS:
        kind = "all"
    keys = [kind] if kind != "all" else [column["key"] for column in COLUMNS]
    day_list = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]

    entries = index.list_cleanup_alltime_stats()
    rates, _ = cleanup_stats.outlier_rates_measured(index)
    measured_outliers = {r["entity_id"] for r in rates}

    rows = []
    pending = 0
    peak = 0
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        if not entity["first_ts"]:
            continue
        entry = entries.get(entity_id)
        outliers_on = effective_outlier_threshold(
            entity["aggregation_type"], entity["outlier_threshold"]
        ) != "off"
        gaps_ok = cleanup_stats.gaps_measured(entity, entry)
        if (
            entry is None or "daily" not in entry
            or (outliers_on and entity_id not in measured_outliers) or not gaps_ok
        ):
            pending += 1
        if entry is None or "daily" not in entry:
            continue
        valid = {"duplicates", "counter_decreases"}
        if entity_id in measured_outliers:
            valid.add("outliers")
        if gaps_ok:
            valid.add("gaps")
        use = [key for key in keys if key in valid]
        daily = entry["daily"]
        cells = []
        total = 0
        for day in day_list:
            per_day = daily.get(day) or {}
            count = sum(per_day.get(key, 0) for key in use)
            total += count
            peak = max(peak, count)
            cells.append(count)
        if total:
            rows.append({
                "entity_id": entity_id,
                "friendly_name": entity["custom_name"] or entity["friendly_name"] or entity_id,
                "counts": cells,
                "total": total,
            })
    rows.sort(key=lambda r: (-r["total"], r["entity_id"]))
    for row in rows:
        row["cells"] = [
            {
                "day": day, "count": count, "label": format_int(count),
                "level": _level(count, peak),
                "href": heatmap_href(row["entity_id"], kind, day) if count else None,
            }
            for day, count in zip(day_list, row["counts"])
        ]
        row["total_label"] = format_int(row["total"])
    return {
        "kind": kind, "days": days, "day_list": day_list, "rows": rows,
        "pending": pending, "peak": peak,
    }


def build_matrix(index: Index) -> dict:
    """Matrix Entität × Befundtyp, absteigend nach der Summe der Befunde.

    - ``rows``: nur Entitäten mit mindestens einem Befund; je Zeile ``cells``
      (je Spalte ``count``/``label``/``href``, oder None ohne Befund) und ``total``
    - ``totals``: Summe je Spalte
    - ``affected``: Zahl der Entitäten mit Befund
    - ``pending``: Zahl der Entitäten mit Daten, die noch keine belastbare
      Gesamt-Zählung haben (nie gemessen, oder die Ausreißer-/Lücken-Zahl gehört
      zu einer anderen Schwelle). Ohne sie sähe "nichts gefunden" wie "noch nicht
      gemessen" aus.
    """
    entries = index.list_cleanup_alltime_stats()
    rates, _ = cleanup_stats.outlier_rates_measured(index)
    measured_outliers = {r["entity_id"]: r["marked"] for r in rates}

    counts: dict[str, dict[str, int]] = {column["key"]: {} for column in COLUMNS}
    names: dict[str, str] = {}
    # Zähler mit "Zählerrückgänge = Erlauben": ihre Rückgänge werden nie gezählt
    # (counter_decreases_flagged), die Zelle bliebe also leer und sähe wie
    # "geprüft, nichts gefunden" aus. Sie wird stattdessen als erlaubt gekennzeichnet.
    allowed: set[str] = set()
    pending = 0
    for entity in index.list_entities():
        entity_id = entity["entity_id"]
        names[entity_id] = entity["custom_name"] or entity["friendly_name"] or entity_id
        if not entity["first_ts"]:
            continue  # keine Daten, nichts zu messen
        if entity["state_class"] == "total_increasing" and entity["counter_decreases"] == "allow":
            allowed.add(entity_id)
        entry = entries.get(entity_id)
        outliers_on = effective_outlier_threshold(
            entity["aggregation_type"], entity["outlier_threshold"]
        ) != "off"
        gaps_ok = cleanup_stats.gaps_measured(entity, entry)
        if entry is None or (outliers_on and entity_id not in measured_outliers) or not gaps_ok:
            pending += 1
        if entry is not None:
            entry_counts = entry.get("counts") or {}
            counts["duplicates"][entity_id] = entry_counts.get("duplicates") or 0
            counts["counter_decreases"][entity_id] = entry_counts.get("counter_decreases") or 0
            if gaps_ok:
                counts["gaps"][entity_id] = entry_counts.get("gaps") or 0
        if entity_id in measured_outliers:
            counts["outliers"][entity_id] = measured_outliers[entity_id]

    entity_ids = {eid for column in counts.values() for eid, n in column.items() if n}
    rows = []
    for entity_id in entity_ids:
        cells = {}
        total = 0
        for column in COLUMNS:
            count = counts[column["key"]].get(entity_id, 0)
            total += count
            cells[column["key"]] = {
                "count": count,
                "label": format_int(count),
                "href": cleanup_href(entity_id, column["filter"], column["range"]),
            } if count else None
        rows.append({
            "entity_id": entity_id,
            "friendly_name": names.get(entity_id, entity_id),
            "cells": cells,
            "total": total,
            "counter_allowed": entity_id in allowed,
        })
    rows.sort(key=lambda r: (-r["total"], r["entity_id"]))

    totals = {}
    for column in COLUMNS:
        value = sum(counts[column["key"]].values())
        totals[column["key"]] = {"count": value, "label": format_int(value)}

    return {
        "columns": COLUMNS,
        "rows": rows,
        "totals": totals,
        "affected": len(rows),
        "affected_label": format_int(len(rows)),
        "entity_count": len(names),
        "pending": pending,
    }


# Beschriftung der Markierung je Spalte, wie sie cleanup.analyze_raw_rows_page vergibt.
def _flag_labels() -> dict[str, str]:
    return {"duplicates": "Duplikat", "counter_decreases": tr("Zählerrückgang"), "outliers": tr("Ausreißer"), "gaps": tr("Lücke")}


def _reasons_for(key: str, flags: list[dict]) -> list[str]:
    """Nur die Begründung der Markierung, um die es in der Spalte geht. Eine Zeile
    kann zugleich Duplikat und Wiederholung sein; beide Begründungen nebeneinander
    machen die Zeile unlesbar, und die übrigen stehen auf der Bereinigungsseite."""
    own = [flag["reason"] for flag in flags if flag["label"] == _flag_labels()[key]]
    return own or [flag["reason"] for flag in flags]


def findings_for_entity(data_dir: Path, index: Index, tz: ZoneInfo, entity, now: datetime | None = None) -> list[dict]:
    """Die jüngsten Einzelbefunde EINER Entität für die aufgeklappte Zeile —
    höchstens DETAIL_LIMIT je Kategorie, neueste zuerst.

    Liest die Rohdaten, deshalb nur auf Klick und nur für diese Entität, nicht
    im Hintergrund. Verwendet dieselbe Analyse wie die Bereinigungsseite
    (analyze_raw_rows_page), damit Zeitpunkte, Werte und Begründungen exakt
    dort wiederzufinden sind. Fenster ist die gesamte Historie, wie bei den Zahlen
    der Matrix. Kategorien ohne Befund fehlen im Ergebnis; solche, die der Cache
    schon mit 0 kennt, werden gar nicht erst gescannt."""
    now = now or datetime.now(tz)
    entity_id = entity["entity_id"]
    decimals = entity["decimals"]
    decimals_int = decimals_to_int(decimals)
    gap_threshold = entity["gap_threshold"]
    outlier_threshold = effective_outlier_threshold(
        entity["aggregation_type"], entity["outlier_threshold"]
    )
    counter_enabled = cleanup_stats.counter_decreases_flagged(entity)
    entry = index.get_cleanup_alltime_stats(entity_id)
    cached = (entry or {}).get("counts") or {}
    start = datetime.fromtimestamp(entity["first_ts"], tz) if entity["first_ts"] else now
    groups = []
    for column in COLUMNS:
        key = column["key"]
        if key == "counter_decreases" and not counter_enabled:
            continue
        if key == "outliers" and outlier_threshold == "off":
            continue
        if key == "gaps" and gap_threshold == "off":
            continue
        # Der Cache gilt für die Kategorie nur, wenn er mit der aktuellen Schwelle gezählt ist.
        cache_valid = cleanup_stats.gaps_measured(entity, entry) if key == "gaps" else True
        if cached and cache_valid and not cached.get(column["filter"]):
            continue

        def rows_factory():
            return cleanup.iter_raw_rows(
                data_dir, index, entity_id, start.timestamp(), now.timestamp(), tz, now=now
            )

        analysis = cleanup.analyze_raw_rows_page(
            rows_factory,
            filter_=column["filter"],
            page=1,
            # Duplikate: ein Zeitstempel belegt mindestens zwei Zeilen, deshalb mehr
            # holen und unten je Zeitstempel zu einem Eintrag zusammenfassen.
            page_size=1000 if key == "duplicates" else DETAIL_LIMIT,
            gap_threshold_minutes=None if gap_threshold == "off" else float(gap_threshold),
            outlier_factor=None if outlier_threshold == "off" else float(outlier_threshold),
            tz=tz,
            decimals=decimals,
            counter_decrease_enabled=counter_enabled,
            outlier_mode=cleanup_stats.outlier_mode(entity),
        )
        total = analysis["pagination"]["total"]
        if not total:
            continue
        found = analysis["rows"]
        if key == "duplicates":
            # Jedes Paar kommt als zwei Zeilen mit gleichem Zeitstempel (und gleicher
            # Begründung "2× derselbe Zeitstempel"); eine genügt. Gezählt bleiben
            # Zeilen, wie in der Matrix und im Filter der Bereinigungsseite.
            merged: list[dict] = []
            shown = 0
            for row in found:
                if merged and merged[-1]["ts"] == row["ts"]:
                    shown += 1
                    continue
                if len(merged) == DETAIL_LIMIT:
                    break
                merged.append(row)
                shown += 1
            found = merged
        else:
            shown = len(found)
        groups.append({
            "key": key,
            "severity": column["severity"],
            "total": total,
            "total_label": format_int(total),
            "shown": shown,
            "more": max(0, total - shown),
            "href": cleanup_href(entity_id, column["filter"], column["range"]),
            "rows": [
                {
                    "ts_label": formats.format_datetime(datetime.fromtimestamp(row["ts"], tz)),
                    "value_label": format_value(row["value"], decimals_int),
                    "reasons": _reasons_for(key, row["flags"]),
                }
                for row in found
            ],
        })
    return groups
