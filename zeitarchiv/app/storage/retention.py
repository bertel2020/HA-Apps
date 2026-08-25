"""Aufbewahrungsfrist anwenden (Konzept "Offene Punkte"): entfernt Rohdaten-
und Rollup-Perioden, die älter als die je Entität konfigurierte
Aufbewahrungsfrist sind — bisher wurde die Frist nur gespeichert, nie
angewendet.

Arbeitet grundsätzlich auf GANZEN Monaten (dieselbe Partitionierungsgrenze wie
Archivierung/Rotation, siehe rotate.py): ein archivierter Monat wird nur
komplett gelöscht, nie teilweise umgeschrieben — dadurch bleibt eine Löschung
ein einfacher Datei-Löschvorgang statt eines riskanten Parquet-Rewrites, und
Rollup-Zeilen lassen sich anhand ihres bucket_start konsistent mitentfernen.

Anders als der Purge im Bereinigungs-Werkzeug (cleanup.py, nur für bereits
weich gelöschte Einzelwerte im laufenden Monat) betrifft Aufbewahrung ganze,
noch nie zuvor zum Löschen markierte Zeiträume — deshalb bewusst nur aktiv,
wenn im Einstellungen-Bereich ("Aufbewahrung") explizit eingeschaltet, mit
klarer Aufklärung in der Oberfläche, dass eine Anwendung endgültig ist."""

from __future__ import annotations

import calendar
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from .hotbuffer import hot_path, read_rows
from .index import Index
from .paths import entity_dir
from .rollup import rollup_path

RETENTION_DAYS = {
    "30d": 30,
    "90d": 90,
    "365d": 365,
    "2y": 730,
    "5y": 1825,
}

_FINE_LEVEL = {"counter": "tag", "standard": "stunde", "switch": "stunde"}


def _cutoff_ts(retention: str, now: datetime) -> float | None:
    """None heißt "keine Anwendung" — sowohl für retention="unlimited" als
    auch für einen (sollte nicht vorkommen) unbekannten Wert; ein unbekannter
    Wert versehentlich als "sofort alles löschen" zu behandeln wäre das genaue
    Gegenteil von "nie destruktiv"."""
    days = RETENTION_DAYS.get(retention)
    if days is None:
        return None
    return now.timestamp() - days * 86400


def _prune_rollup_file_by_month(path: Path, tz: ZoneInfo, deleted_months: set[tuple[int, int]]) -> None:
    """Entfernt Zeilen, deren bucket_start in einen gelöschten Monat fällt —
    bewusst dieselbe Monats-Menge wie die Archiv-Löschung (nicht einfach
    "bucket_start < cutoff_ts"): ein nur teilweise abgelaufener, deshalb
    unangetastet gebliebener Archiv-Monat (siehe enforce_retention_for_entity)
    darf sonst seine Rollup-Zeilen verlieren, obwohl die zugehörigen Rohdaten
    noch da sind — Roh- und Rollup-Daten würden auseinanderlaufen."""
    if not path.exists() or not deleted_months:
        return
    table = pq.read_table(path)
    starts = table.column("bucket_start").to_pylist()
    keep_mask = [
        (datetime.fromtimestamp(s, tz).year, datetime.fromtimestamp(s, tz).month) not in deleted_months
        for s in starts
    ]
    if all(keep_mask):
        return
    if not any(keep_mask):
        path.unlink()
        return
    pq.write_table(table.filter(keep_mask), path, compression="zstd")


def _prune_year_rollup(data_dir: Path, entity_id: str, tz: ZoneInfo, deleted_months: set[tuple[int, int]]) -> None:
    """jahr.parquet fasst ein ganzes Kalenderjahr zusammen — eine Jahres-Zeile
    wird deshalb erst entfernt, wenn KEIN archivierter Monat dieses Jahres mehr
    übrig ist, nicht schon weil ein einzelner Monat davon gelöscht wurde."""
    path = rollup_path(data_dir, entity_id, "jahr")
    if not path.exists() or not deleted_months:
        return
    affected_years = {year for year, _ in deleted_months}
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    remaining_years = {int(p.stem.split("-")[0]) for p in archive_dir.glob("*.parquet")} if archive_dir.exists() else set()
    fully_gone_years = affected_years - remaining_years
    if not fully_gone_years:
        return
    table = pq.read_table(path)
    starts = table.column("bucket_start").to_pylist()
    keep_mask = [datetime.fromtimestamp(s, tz).year not in fully_gone_years for s in starts]
    if all(keep_mask):
        return
    if not any(keep_mask):
        path.unlink()
        return
    pq.write_table(table.filter(keep_mask), path, compression="zstd")


def _update_first_ts(data_dir: Path, index: Index, entity_id: str, tz: ZoneInfo, now: datetime) -> None:
    """Nach dem Löschen der ältesten Archiv-Monate zeigt first_ts sonst weiter
    auf längst entfernte Daten — sucht den neuen frühesten Wert im ältesten
    verbliebenen Archiv-Monat, sonst (kein Archiv mehr übrig) im Hot Buffer."""
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


def enforce_retention_for_entity(
    data_dir: Path, index: Index, entity_id: str, retention: str, tz: ZoneInfo, now: datetime
) -> dict:
    """Löscht archivierte Monate + zugehörige Rollup-Zeilen sowie abgelaufene
    Zeilen im Hot Buffer, die älter als die konfigurierte Aufbewahrungsfrist
    dieser Entität sind. Gibt eine Zusammenfassung zurück."""
    cutoff_ts = _cutoff_ts(retention, now)
    if cutoff_ts is None:
        return {"rows_deleted": 0, "bytes_freed": 0, "months_deleted": 0}

    rows_deleted = 0
    bytes_freed = 0
    deleted_months: set[tuple[int, int]] = set()

    archive_dir = entity_dir(data_dir, "archive", entity_id)
    if archive_dir.exists():
        for path in sorted(archive_dir.glob("*.parquet")):
            year_str, month_str = path.stem.split("-")
            year, month = int(year_str), int(month_str)
            days_in_month = calendar.monthrange(year, month)[1]
            month_end = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=tz).timestamp()
            if month_end >= cutoff_ts:
                continue  # Monat reicht (auch nur teilweise) noch in die Aufbewahrungsfrist hinein
            table = pq.read_table(path, columns=["ts"])
            rows_deleted += table.num_rows
            bytes_freed += path.stat().st_size
            path.unlink()
            deleted_months.add((year, month))

    entity = index.get_entity(entity_id)
    aggregation_type = entity["aggregation_type"] if entity else "standard"
    _prune_rollup_file_by_month(rollup_path(data_dir, entity_id, _FINE_LEVEL[aggregation_type]), tz, deleted_months)
    _prune_rollup_file_by_month(rollup_path(data_dir, entity_id, "monat"), tz, deleted_months)
    _prune_year_rollup(data_dir, entity_id, tz, deleted_months)
    months_deleted = len(deleted_months)

    hot_file = hot_path(data_dir, entity_id, now.timestamp(), tz)
    if hot_file.exists():
        rows = read_rows(hot_file)
        kept = [(ts, v) for ts, v in rows if ts >= cutoff_ts]
        expired = len(rows) - len(kept)
        if expired:
            tmp_path = hot_file.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                for ts, v in kept:
                    f.write(f"{ts},{v}\n")
            tmp_path.replace(hot_file)
            rows_deleted += expired

    if rows_deleted:
        index.add_row_count(entity_id, -rows_deleted)
    if bytes_freed:
        index.add_size_bytes(entity_id, -bytes_freed)
    if months_deleted:
        _update_first_ts(data_dir, index, entity_id, tz, now)

    return {"rows_deleted": rows_deleted, "bytes_freed": bytes_freed, "months_deleted": months_deleted}


def enforce_retention_all(data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None) -> dict:
    """Durchläuft alle Entitäten mit einer begrenzten Aufbewahrungsfrist
    (retention != "unlimited") — aufgerufen sowohl vom manuellen Anstoß in den
    Einstellungen als auch vom persistenten täglichen Wartungsplaner."""
    now = now or datetime.now(tz)
    totals = {"rows_deleted": 0, "bytes_freed": 0, "months_deleted": 0, "entities_affected": 0}
    for entity in index.list_entities():
        retention = entity["retention"]
        if retention == "unlimited":
            continue
        result = enforce_retention_for_entity(data_dir, index, entity["entity_id"], retention, tz, now)
        if result["rows_deleted"] or result["months_deleted"]:
            totals["entities_affected"] += 1
        totals["rows_deleted"] += result["rows_deleted"]
        totals["bytes_freed"] += result["bytes_freed"]
        totals["months_deleted"] += result["months_deleted"]
    return totals


def preview_retention_for_entity(
    data_dir: Path, entity_id: str, retention: str, tz: ZoneInfo, now: datetime
) -> dict:
    """Ermittelt dieselben Löschgrenzen wie die echte Retention, ohne zu schreiben."""
    details = _inspect_retention_for_entity(data_dir, entity_id, retention, tz, now)
    return {
        key: details[key]
        for key in ("rows_deleted", "bytes_freed", "months_deleted")
    }


def _inspect_retention_for_entity(
    data_dir: Path, entity_id: str, retention: str, tz: ZoneInfo, now: datetime
) -> dict:
    """Nicht-destruktive Detailprüfung inklusive nächstem Ablaufzeitpunkt."""
    cutoff_ts = _cutoff_ts(retention, now)
    days = RETENTION_DAYS.get(retention)
    if cutoff_ts is None or days is None:
        return {
            "rows_deleted": 0,
            "bytes_freed": 0,
            "months_deleted": 0,
            "next_expiration_ts": None,
        }
    rows_deleted = 0
    bytes_freed = 0
    months_deleted = 0
    next_expiration_ts: float | None = None
    now_ts = now.timestamp()
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    if archive_dir.exists():
        for path in sorted(archive_dir.glob("*.parquet")):
            year_str, month_str = path.stem.split("-")
            year, month = int(year_str), int(month_str)
            days_in_month = calendar.monthrange(year, month)[1]
            month_end = datetime(year, month, days_in_month, 23, 59, 59, tzinfo=tz).timestamp()
            if month_end < cutoff_ts:
                rows_deleted += pq.read_metadata(path).num_rows
                bytes_freed += path.stat().st_size
                months_deleted += 1
            else:
                expires_at = month_end + days * 86400
                if expires_at > now_ts:
                    next_expiration_ts = (
                        expires_at if next_expiration_ts is None
                        else min(next_expiration_ts, expires_at)
                    )
    hot_file = hot_path(data_dir, entity_id, now.timestamp(), tz)
    if hot_file.exists():
        for ts, _ in read_rows(hot_file):
            if ts < cutoff_ts:
                rows_deleted += 1
            else:
                expires_at = ts + days * 86400
                if expires_at > now_ts:
                    next_expiration_ts = (
                        expires_at if next_expiration_ts is None
                        else min(next_expiration_ts, expires_at)
                    )
    return {
        "rows_deleted": rows_deleted,
        "bytes_freed": bytes_freed,
        "months_deleted": months_deleted,
        "next_expiration_ts": next_expiration_ts,
    }


def preview_retention_all(data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None) -> dict:
    """Nicht-destruktive Vorschau für alle Entitäten mit begrenzter Frist."""
    now = now or datetime.now(tz)
    totals = {"rows_deleted": 0, "bytes_freed": 0, "months_deleted": 0, "entities_affected": 0}
    for entity in index.list_entities():
        if entity["retention"] == "unlimited":
            continue
        result = preview_retention_for_entity(
            data_dir, entity["entity_id"], entity["retention"], tz, now
        )
        if result["rows_deleted"] or result["months_deleted"]:
            totals["entities_affected"] += 1
        for key in ("rows_deleted", "bytes_freed", "months_deleted"):
            totals[key] += result[key]
    return totals


def preview_retention_overview(
    data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None
) -> dict:
    """Persistierbare Gesamt- und Fristenübersicht für die Statistikseite.

    Der Aufrufer entscheidet über das Cache-Intervall. Diese Funktion liest nur
    Index und Datendateien und verändert keine Archivdaten.
    """
    now = now or datetime.now(tz)
    totals = {
        "rows_deleted": 0,
        "bytes_freed": 0,
        "months_deleted": 0,
        "entities_affected": 0,
    }
    groups: dict[str, dict] = {}
    for entity in index.list_entities():
        retention = entity["retention"]
        if retention == "unlimited":
            continue
        group = groups.setdefault(
            retention,
            {
                "retention": retention,
                "entity_count": 0,
                "total_rows": 0,
                "total_size_bytes": 0,
                "rows_due": 0,
                "bytes_due": 0,
                "months_due": 0,
                "entities_due": 0,
                "next_expiration_ts": None,
            },
        )
        group["entity_count"] += 1
        group["total_rows"] += entity["row_count"] or 0
        group["total_size_bytes"] += entity["size_bytes"] or 0
        details = _inspect_retention_for_entity(
            data_dir, entity["entity_id"], retention, tz, now
        )
        if details["rows_deleted"] or details["months_deleted"]:
            group["entities_due"] += 1
            totals["entities_affected"] += 1
        group["rows_due"] += details["rows_deleted"]
        group["bytes_due"] += details["bytes_freed"]
        group["months_due"] += details["months_deleted"]
        next_ts = details["next_expiration_ts"]
        if next_ts is not None:
            group["next_expiration_ts"] = (
                next_ts if group["next_expiration_ts"] is None
                else min(group["next_expiration_ts"], next_ts)
            )
        totals["rows_deleted"] += details["rows_deleted"]
        totals["bytes_freed"] += details["bytes_freed"]
        totals["months_deleted"] += details["months_deleted"]

    ordered_groups = sorted(
        groups.values(), key=lambda row: RETENTION_DAYS.get(row["retention"], 10**9)
    )
    return {"generated_at": now.timestamp(), "totals": totals, "groups": ordered_groups}
