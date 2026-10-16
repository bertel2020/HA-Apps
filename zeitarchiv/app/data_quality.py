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
import json
import math
from pathlib import Path
import time
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


def _heatmap_labels() -> dict[str, str]:
    return {
        "duplicates": tr("Duplikate"), "counter_decreases": tr("Zählerrückgänge"),
        "outliers": tr("Ausreißer"), "gaps": tr("Lücken"),
    }


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
    day_labels = {
        day: formats.format_date(datetime.combine(date.fromisoformat(day), datetime.min.time()))
        for day in day_list
    }

    entries = index.list_cleanup_alltime_stats()
    rates, _ = cleanup_stats.outlier_rates_measured(index)
    measured_outliers = {r["entity_id"] for r in rates}

    rows = []
    pending = 0
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
        per_days = [{k: n for k in use if (n := (daily.get(day) or {}).get(k, 0))} for day in day_list]
        total = sum(sum(per_day.values()) for per_day in per_days)
        if total:
            rows.append({
                "entity_id": entity_id,
                "friendly_name": entity["custom_name"] or entity["custom_name"] or entity["friendly_name"] or entity_id,
                "per_days": per_days,
                "total": total,
            })
    rows.sort(key=lambda r: (-r["total"], r["entity_id"]))
    labels = _heatmap_labels()
    for row in rows:
        # Stufe je ZEILE: die Heatmap soll zeigen, WANN es bei einer Entität auffällig
        # war. Absolute Größen stehen in der Summe; ein gemeinsamer Höchstwert ließe
        # alle Entitäten neben einer mit Hunderttausenden Lücken gleich blass aussehen.
        row_peak = max((sum(p.values()) for p in row["per_days"]), default=0)
        cells = []
        for day, per_day in zip(day_list, row["per_days"]):
            count = sum(per_day.values())
            cells.append({
                "day": day, "day_label": day_labels[day], "count": count, "label": format_int(count),
                "level": _level(count, row_peak),
                # Farbton = der häufigste Befundtyp des Tages
                "kind": max(per_day, key=per_day.get) if per_day else None,
                "detail": " · ".join(f"{labels[k]} {format_int(n)}" for k, n in per_day.items()),
                "first": day.endswith("-01"),
                "href": heatmap_href(row["entity_id"], kind, day) if count else None,
            })
        row["cells"] = cells
        row["total_label"] = format_int(row["total"])
        del row["per_days"]
    return {
        "kind": kind, "days": days, "day_list": day_list, "day_labels": day_labels,
        "rows": rows, "pending": pending,
        "legend": [{"key": key, "label": labels[key]} for key in keys],
    }


SORT_KEYS = ("total", "name") + tuple(column["key"] for column in COLUMNS)
KIND_KEYS = tuple(column["key"] for column in COLUMNS)


def filter_and_sort(
    rows: list[dict], *, q: str = "", kind: str = "all", sort: str = "total", direction: str = "desc"
) -> list[dict]:
    """Suche (Name oder Entity-ID), Befundtyp-Filter und Sortierung der Matrix-Zeilen.

    ``kind`` zeigt nur Entitäten mit Befund dieses Typs. Unbekannte Werte fallen auf die Standardansicht (alle, nach Summe absteigend)
    zurück. Gleichstand ordnet die Entity-ID, damit die Reihenfolge stabil bleibt."""
    q = q.strip().lower()
    if q:
        rows = [r for r in rows if q in r["friendly_name"].lower() or q in r["entity_id"].lower()]
    if kind in KIND_KEYS:
        rows = [r for r in rows if r["cells"].get(kind)]
    if sort not in SORT_KEYS:
        sort = "total"
    descending = direction != "asc"

    def value(row):
        if sort == "total":
            return row["total"]
        if sort == "name":
            return row["friendly_name"].lower()
        cell = row["cells"].get(sort)
        return cell["count"] if cell else 0

    if sort == "name":
        return sorted(rows, key=lambda r: (value(r), r["entity_id"]), reverse=descending)
    return sorted(rows, key=lambda r: (-value(r) if descending else value(r), r["entity_id"]))


# Zeitraum der Matrix: Schlüssel -> Tage (None = gesamte Historie). "all" ist der Standard.
PERIODS: dict[str, int | None] = {"all": None, "year": 365, "30d": 30, "7d": 7}
DEFAULT_PERIOD = "all"


def build_matrix(index: Index, *, period: str = DEFAULT_PERIOD, today: date | None = None) -> dict:
    """Matrix Entität × Befundtyp, absteigend nach der Summe der Befunde.

    ``period``: "all" zählt die gesamte Historie aus ``counts``; die übrigen Zeiträume
    summieren die Tageszähler ``daily`` der letzten Tage (einschließlich heute). Entitäten
    ohne ``daily`` sind dort noch nicht gemessen (``pending``), wie in build_heatmap.

    - ``rows``: nur Entitäten mit mindestens einem Befund; je Zeile ``cells``
      (je Spalte ``count``/``label``/``href``, oder None ohne Befund) und ``total``
    - ``totals``: Summe je Spalte
    - ``affected``: Zahl der Entitäten mit Befund
    - ``pending``: Zahl der Entitäten mit Daten, die noch keine belastbare
      Gesamt-Zählung haben (nie gemessen, oder die Ausreißer-/Lücken-Zahl gehört
      zu einer anderen Schwelle). Ohne sie sähe "nichts gefunden" wie "noch nicht
      gemessen" aus.
    """
    if period not in PERIODS:
        period = DEFAULT_PERIOD
    window_days = PERIODS[period]
    window_start = (
        ((today or date.today()) - timedelta(days=window_days - 1)).isoformat() if window_days else None
    )
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
        names[entity_id] = entity["custom_name"] or entity["custom_name"] or entity["friendly_name"] or entity_id
        if not entity["first_ts"]:
            continue  # keine Daten, nichts zu messen
        if entity["state_class"] == "total_increasing" and entity["counter_decreases"] == "allow":
            allowed.add(entity_id)
        entry = entries.get(entity_id)
        outliers_on = effective_outlier_threshold(
            entity["aggregation_type"], entity["outlier_threshold"]
        ) != "off"
        gaps_ok = cleanup_stats.gaps_measured(entity, entry)
        if (
            entry is None or (outliers_on and entity_id not in measured_outliers) or not gaps_ok
            or (window_start and "daily" not in entry)
        ):
            pending += 1
        if entry is not None and window_start is not None:
            if "daily" in entry:
                window = {key: 0 for key in counts}
                for day, per_day in entry["daily"].items():
                    if day >= window_start:
                        for key in window:
                            window[key] += per_day.get(key, 0)
                counts["duplicates"][entity_id] = window["duplicates"]
                counts["counter_decreases"][entity_id] = window["counter_decreases"]
                if gaps_ok:
                    counts["gaps"][entity_id] = window["gaps"]
                if entity_id in measured_outliers:
                    counts["outliers"][entity_id] = window["outliers"]
        else:
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
        "period": period,
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


# --- Sammelaktion: Duplikate mehrerer Entitäten auf einmal entfernen -------------------------
# Löschlogik und Rückgängig sind die der Bereinigungsseite (duplicate_rows_to_delete,
# soft_delete, undo_last_delete); hier steht nur, was mehrere Entitäten zusammenführt.

BULK_BATCH = 10_000


def _alltime_rows(data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, entity, now: datetime):
    """Alle Rohzeilen der Entität über die gesamte Historie, wie die Zählung der Matrix."""
    start, end = cleanup_stats.rows_window("all", 0, now, entity["first_ts"], tz)
    return cleanup.iter_raw_rows(data_dir, index, entity_id, start.timestamp(), end.timestamp(), tz, now=now)


def duplicate_timestamps(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, entity, now: datetime | None = None
) -> list[float]:
    """Zeitstempel, die "Duplikate entfernen" in dieser Entität löschen würde (je Zeitstempel bleibt
    das älteste Vorkommen). Aufrufer hält den Entitäts-Lock."""
    now = now or datetime.now(tz)
    return [ts for ts, _value in cleanup.duplicate_rows_to_delete(_alltime_rows(data_dir, index, tz, entity_id, entity, now))]


def _stats_entry(index: Index, entity_id: str) -> dict | None:
    return index.get_cleanup_alltime_stats(entity_id)


def _write_stats_entry(index: Index, entity_id: str, entry: dict) -> None:
    index.set_setting(index._CLEANUP_ALLTIME_STATS_PREFIX + entity_id, json.dumps(entry))


def mark_duplicates_removed(index: Index, entity_id: str) -> None:
    """Gesamt-Zählung nach dem Entfernen angleichen: Duplikate stehen sofort auf 0, der Eintrag gilt
    aber als veraltet (computed_at = 0), damit der Wartungsplaner alles Übrige neu misst."""
    entry = _stats_entry(index, entity_id)
    if not entry:
        return
    entry.setdefault("counts", {})["duplicates"] = 0
    for day_counts in (entry.get("daily") or {}).values():
        day_counts.pop("duplicates", None)
    entry["computed_at"] = 0
    _write_stats_entry(index, entity_id, entry)


def mark_stats_unknown(index: Index, entity_id: str) -> None:
    """Nach "Rückgängig": die alten Zahlen stimmen nicht mehr. Ohne Tageszahlen gilt der Eintrag in der
    Matrix als nicht gemessen und wird im Hintergrund neu gerechnet."""
    entry = _stats_entry(index, entity_id)
    if not entry:
        return
    entry.pop("daily", None)
    entry["computed_at"] = 0
    _write_stats_entry(index, entity_id, entry)


def preview_duplicate_removal(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_ids: list[str],
    entity_lock, now: datetime | None = None,
) -> dict:
    """Je gewählter Entität die Zahl der Zeilen, die entfernt würden. Entitäten ohne Duplikate fallen
    heraus. `entity_lock(entity_id)` ist der Kontextmanager des StorageCoordinator."""
    now = now or datetime.now(tz)
    rows = []
    for entity_id in dict.fromkeys(entity_ids):
        entity = index.get_entity(entity_id)
        if entity is None:
            continue
        with entity_lock(entity_id):
            count = len(duplicate_timestamps(data_dir, index, tz, entity_id, entity, now))
        if count:
            rows.append({"entity_id": entity_id, "friendly_name": entity["custom_name"] or entity["friendly_name"] or entity_id, "count": count})
    return {"rows": rows, "total": sum(r["count"] for r in rows), "total_label": format_int(sum(r["count"] for r in rows))}


def remove_duplicates(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_ids: list[str],
    entity_lock, now: datetime | None = None,
) -> dict:
    """Entfernt die Duplikate der gewählten Entitäten (Soft-Delete, source="duplicates") und rechnet
    die Zahlen je Entität neu aus dem Löschlauf: gelöscht wird, was jetzt gefunden wird, nicht was die
    Vorschau zeigte."""
    now = now or datetime.now(tz)
    rows = []
    for entity_id in dict.fromkeys(entity_ids):
        entity = index.get_entity(entity_id)
        if entity is None:
            continue
        deleted = 0
        with entity_lock(entity_id):
            timestamps = duplicate_timestamps(data_dir, index, tz, entity_id, entity, now)
            deleted_at = time.time()
            for start in range(0, len(timestamps), BULK_BATCH):
                batch = timestamps[start:start + BULK_BATCH]
                cleanup.soft_delete(index, entity_id, batch, deleted_at=deleted_at, source="duplicates")
                deleted += len(batch)
        if deleted:
            mark_duplicates_removed(index, entity_id)
            rows.append({"entity_id": entity_id, "friendly_name": entity["custom_name"] or entity["friendly_name"] or entity_id, "count": deleted})
    total = sum(r["count"] for r in rows)
    return {"rows": rows, "total": total, "total_label": format_int(total)}


def undo_duplicate_removal(index: Index, entity_ids: list[str], entity_lock) -> int:
    """Macht je Entität die zuletzt gelöschte Charge rückgängig, also den Lauf von eben."""
    restored = 0
    for entity_id in dict.fromkeys(entity_ids):
        with entity_lock(entity_id):
            count = cleanup.undo_last_delete(index, entity_id)
        if count:
            mark_stats_unknown(index, entity_id)
        restored += count
    return restored


# --- Hinweis auf neue Befunde seit dem letzten Besuch ------------------------------------------

SEEN_SETTING = "data_quality_seen"
_TOTALS_MAX_AGE = 120.0
_totals_cache: dict = {"at": 0.0, "key": None, "data": {}}


def current_totals(index: Index, *, max_age: float = _TOTALS_MAX_AGE) -> dict[str, int]:
    """Mitzählende Befunde (gesamte Historie) je gemessener Entität, für den Vergleich mit dem
    Stand des letzten Besuchs. Entitäten ohne Befund stehen mit 0 darin, damit "gemessen, nichts
    gefunden" von "noch nicht gemessen" unterscheidbar bleibt. Kurz zwischengespeichert, weil die
    Meldungen bei jeder Seite neu gebaut werden."""
    now = time.monotonic()
    key = id(index)
    if _totals_cache["key"] == key and now - _totals_cache["at"] < max_age:
        return _totals_cache["data"]
    totals = {entity_id: 0 for entity_id in index.list_cleanup_alltime_stats()}
    for row in build_matrix(index)["rows"]:
        totals[row["entity_id"]] = row["total"]
    _totals_cache.update(at=now, key=key, data=totals)
    return totals


def mark_seen(index: Index) -> None:
    """Merkt sich den aktuellen Stand als "gesehen" (beim Öffnen der Datenqualität-Seite)."""
    index.set_setting(SEEN_SETTING, json.dumps(current_totals(index, max_age=0)))


def new_findings(index: Index) -> dict | None:
    """Neue mitzählende Befunde seit dem letzten Besuch: ``{"new": n, "entities": k}`` oder None.

    Verglichen wird je Entität, nicht die Gesamtsumme: sonst ließe schon das Nachmessen bisher
    unvermessener Entitäten die Zahl springen, ohne dass etwas Neues passiert wäre. Entitäten, die
    beim letzten Besuch nicht gemessen waren, kommen ohne Meldung in den Stand. Sinkt eine Zahl
    (Bereinigung), sinkt auch der Stand mit, sonst blieben spätere neue Befunde unter ihm verborgen.
    Ohne Stand (nie besucht) wird der aktuelle als Anfang gemerkt."""
    raw = index.get_setting(SEEN_SETTING)
    if not raw:
        mark_seen(index)
        return None
    try:
        seen = {str(k): int(v) for k, v in json.loads(raw).items()}
    except (ValueError, TypeError, AttributeError):
        mark_seen(index)
        return None
    now_totals = current_totals(index)
    new = entities = 0
    updated = dict(seen)
    for entity_id, total in now_totals.items():
        base = seen.get(entity_id)
        if base is None:
            updated[entity_id] = total
        elif total > base:
            new += total - base
            entities += 1
        elif total < base:
            updated[entity_id] = total
    if updated != seen:
        index.set_setting(SEEN_SETTING, json.dumps(updated))
    return {"new": new, "entities": entities} if new else None
