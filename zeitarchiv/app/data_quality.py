"""Housekeeping → Datenqualität: Befunde aller Entitäten in einer Matrix.

Reine Zusammenführung der drei vorhandenen Messungen (Duplikat-Schnappschuss,
Zählerrückgang-Schnappschuss, Ausreißer-Quoten). Rechnet nichts nach und liest
keine Rohdaten: dieselbe Zahl steht damit in der Matrix, im jeweiligen
Housekeeping-Abschnitt und am Konfigurationsfeld der Entität. Zwei verschieden
gemessene Zahlen für dasselbe wären genau die Verwirrung, die eine Übersicht
auflösen soll.

Die Fenster der Quellen sind verschieden (Duplikate und Zählerrückgänge:
30 Tage, Ausreißer: gesamte Historie). Die Spaltenköpfe nennen das. Der Link
einer Zelle öffnet die Bereinigungsseite der Entität mit dem passenden Filter
und dem Zeitraum "Gesamt": die Seite kennt nur Kalenderperioden, und ein
Kalendermonat würde am Monatsanfang Befunde der letzten 30 Tage verschweigen.
Der Filter hält die Tabelle trotzdem übersichtlich.

Ohne Routen-Bezug und ohne Zustand, wie cleanup_stats.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from . import cleanup_stats, formats
from .formatting import decimals_to_int, format_int, format_value
from .storage import cleanup
from .storage.index import Index, effective_outlier_threshold

# Reihenfolge = Spaltenreihenfolge. "filter" und "range" sind die Startwerte der
# Bereinigungsseite (siehe initial_cleanup_view()).
COLUMNS: tuple[dict, ...] = (
    {"key": "duplicates", "filter": "duplicates", "range": "all", "severity": "info"},
    {"key": "counter_decreases", "filter": "counter_decreases", "range": "all", "severity": "crit"},
    {"key": "outliers", "filter": "outliers", "range": "all", "severity": "warn"},
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
# Fenster der Duplikat- und Zählerrückgang-Schnappschüsse (Tage).
SNAPSHOT_WINDOW_DAYS = 30


def cleanup_href(entity_id: str, filter_: str, range_: str) -> str:
    """Relativer Link (wie die übrigen Links auf der Housekeeping-Seite) auf die
    Bereinigungsseite einer Entität mit gesetztem Filter und Zeitraum."""
    query = urlencode({"filter": filter_, "range": range_})
    return f"entities/{quote(entity_id, safe='.')}/cleanup?{query}"


def _snapshot_counts(snapshot: dict | None) -> dict[str, int] | None:
    """{entity_id: Anzahl} eines Duplikat- oder Zählerrückgang-Schnappschusses,
    None wenn es noch keinen gibt (vor dem ersten Lauf oder nach einem
    Regelwechsel) — das ist etwas anderes als "nichts gefunden"."""
    if snapshot is None:
        return None
    return {
        row["entity_id"]: row["count"]
        for row in snapshot.get("rows", [])
        if row.get("count")
    }


def build_matrix(index: Index) -> dict:
    """Matrix Entität × Befundtyp, absteigend nach der Summe der Befunde.

    - ``rows``: nur Entitäten mit mindestens einem Befund; je Zeile ``cells``
      (je Spalte ``count``/``label``/``href``, oder None ohne Befund) und ``total``
    - ``totals``: Summe je Spalte
    - ``affected``: Zahl der Entitäten mit Befund
    - ``pending``: Quellen ohne Messung, damit "nichts gefunden" nicht wie
      "noch nicht gemessen" aussieht — ``duplicates``/``counter_decreases`` sind
      True, solange ihr Schnappschuss fehlt, ``outliers`` ist die Zahl der
      Entitäten ohne belastbare Quote
    """
    counts: dict[str, dict[str, int]] = {}
    pending: dict = {}

    for key, snapshot in (
        ("duplicates", index.get_duplicate_snapshot()),
        ("counter_decreases", index.get_counter_decrease_snapshot()),
    ):
        per_entity = _snapshot_counts(snapshot)
        pending[key] = per_entity is None
        counts[key] = per_entity or {}

    rates, outliers_pending = cleanup_stats.outlier_rates_measured(index)
    pending["outliers"] = outliers_pending
    counts["outliers"] = {r["entity_id"]: r["marked"] for r in rates if r["marked"]}

    names = {
        e["entity_id"]: e["custom_name"] or e["friendly_name"] or e["entity_id"]
        for e in index.list_entities()
    }

    entity_ids = set().union(*(c.keys() for c in counts.values()))
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


def findings_for_entity(data_dir: Path, index: Index, tz: ZoneInfo, entity, now: datetime | None = None) -> list[dict]:
    """Die jüngsten Einzelbefunde EINER Entität für die aufgeklappte Zeile —
    höchstens DETAIL_LIMIT je Kategorie, neueste zuerst.

    Liest die Rohdaten, deshalb nur auf Klick und nur für diese Entität, nicht
    im Hintergrund. Verwendet dieselbe Analyse wie die Bereinigungsseite
    (analyze_raw_rows_page), damit Zeitpunkte, Werte und Begründungen exakt
    dort wiederzufinden sind. Das Fenster ist je Kategorie das der Zahl in der
    Matrix: 30 Tage für Duplikate und Zählerrückgänge, die gesamte Historie für
    Ausreißer. Kategorien ohne Befund fehlen im Ergebnis."""
    now = now or datetime.now(tz)
    entity_id = entity["entity_id"]
    decimals = entity["decimals"]
    decimals_int = decimals_to_int(decimals)
    gap_threshold = entity["gap_threshold"]
    outlier_threshold = effective_outlier_threshold(
        entity["aggregation_type"], entity["outlier_threshold"]
    )
    counter_enabled = cleanup_stats.counter_decreases_flagged(entity)
    groups = []
    for column in COLUMNS:
        key = column["key"]
        if key == "counter_decreases" and not counter_enabled:
            continue
        if key == "outliers" and outlier_threshold == "off":
            continue
        if key == "outliers":
            start = datetime.fromtimestamp(entity["first_ts"], tz) if entity["first_ts"] else now
        else:
            start = now - timedelta(days=SNAPSHOT_WINDOW_DAYS)

        def rows_factory(start=start):
            return cleanup.iter_raw_rows(
                data_dir, index, entity_id, start.timestamp(), now.timestamp(), tz, now=now
            )

        analysis = cleanup.analyze_raw_rows_page(
            rows_factory,
            filter_=column["filter"],
            page=1,
            page_size=DETAIL_LIMIT,
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
        groups.append({
            "key": key,
            "severity": column["severity"],
            "total": total,
            "more": max(0, total - len(analysis["rows"])),
            "href": cleanup_href(entity_id, column["filter"], column["range"]),
            "rows": [
                {
                    "ts_label": formats.format_datetime(datetime.fromtimestamp(row["ts"], tz)),
                    "value_label": format_value(row["value"], decimals_int),
                    "reasons": [flag["reason"] for flag in row["flags"]],
                }
                for row in analysis["rows"]
            ],
        })
    return groups
