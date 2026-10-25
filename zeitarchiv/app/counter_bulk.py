"""Sammelaktion in Housekeeping → Zählerrückgänge: alle Rückgänge, die sofort wieder
zurückkehren (Einordnung „kehrt zurück“, wahrscheinlich Fehlwerte), in einem Schritt zur
Löschung markieren — und diese Sammelmarkierung wieder zurücknehmen.

Markiert wird nur der Fehlwert selbst, nie der Wert davor oder danach, und nichts wird
endgültig entfernt: die Werte landen im Reiter „Markiert“ der jeweiligen Entität. Alle
Entitäten einer Sammelmarkierung teilen sich EINEN ``deleted_at``-Wert; darüber findet das
Zurücknehmen genau diese Chargen und keine, die später dazukamen."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .i18n import tr
from .storage import cleanup
from .storage.index import Index


def returning_timestamps(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, now: datetime | None = None,
    window: tuple[float, float] | None = None,
) -> list[float]:
    """Zeitstempel der Rückgänge eines Zählers, die zurückkehren — mit denselben Regeln wie
    die Erkennung (scan_counter_decreases). Ohne ``window`` gilt deren Fenster (die letzten
    COUNTER_WINDOW_DAYS Tage), sonst der übergebene Zeitraum (Start, Ende als Zeitstempel)."""
    now = now or datetime.now(tz)
    window_start, window_end = window or (
        now.timestamp() - cleanup.COUNTER_WINDOW_DAYS * 86400, now.timestamp()
    )
    decreases = cleanup.classify_counter_decreases(
        cleanup.iter_raw_rows(data_dir, index, entity_id, window_start, window_end, tz, now=now), tz
    )
    return [item["ts"] for item in decreases if item["returns"]]


def refresh_snapshot(
    data_dir: Path, index: Index, tz: ZoneInfo, before: dict | None, entity_ids: set[str]
) -> None:
    """Stellt den Schnappschuss der Rückgänge nach dem Markieren/Zurücknehmen für genau
    die betroffenen Entitäten neu her, statt alle Zähler neu zu scannen (das macht der
    stündliche Wartungstakt). ``before`` ist der Stand VOR der Änderung: das Markieren
    entwertet den Schnappschuss, danach wäre er leer."""
    if before is None:
        return
    kept = [row for row in before.get("rows", []) if row["entity_id"] not in entity_ids]
    fresh = cleanup.scan_counter_decreases(data_dir, index, tz, entity_ids=entity_ids)
    rows = sorted(kept + fresh, key=lambda row: row["last"]["ts"], reverse=True)
    index.set_counter_decrease_snapshot(rows)


def mark_returning(data_dir: Path, index: Index, tz: ZoneInfo, now: datetime | None = None) -> dict:
    """Markiert bei allen Zählern die zurückkehrenden Rückgänge. Liefert die Chargenzeit und
    je Entität die Anzahl; ohne Treffer bleibt ``entities`` leer."""
    before = index.get_counter_decrease_snapshot()
    batch_at = time.time()
    marked: dict[str, int] = {}
    for row in (before or {}).get("rows", []):
        entity = index.get_entity(row["entity_id"])
        if not row.get("returning") or (entity is not None and entity["counter_auto_mark"]):
            continue  # Zähler mit eingeschalteter Regel markiert der stündliche Lauf selbst
        timestamps = returning_timestamps(data_dir, index, tz, row["entity_id"], now)
        if timestamps:
            cleanup.soft_delete(index, row["entity_id"], timestamps, deleted_at=batch_at, source="counter_bulk")
            marked[row["entity_id"]] = len(timestamps)
    refresh_snapshot(data_dir, index, tz, before, set(marked))
    return {"batch_at": batch_at, "entities": marked, "values": sum(marked.values())}


def mark_entity_returning(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, window: tuple[float, float]
) -> int:
    """Wie mark_returning, aber nur für einen Zähler und den gewählten Zeitraum der
    Bereinigungsansicht (Filter „Zählerrückgänge“) — nicht nur für die letzten 30 Tage, sonst
    bliebe alles Ältere trotz Anzeige unberührt. Rückgänge, die niedrig bleiben (Reset,
    Zählerwechsel), bleiben unberührt. Liefert die Anzahl markierter Werte."""
    before = index.get_counter_decrease_snapshot()
    timestamps = returning_timestamps(data_dir, index, tz, entity_id, window=window)
    if timestamps:
        cleanup.soft_delete(index, entity_id, timestamps, source="counter_bulk")
        refresh_snapshot(data_dir, index, tz, before, {entity_id})
    return len(timestamps)


def undo_batch(
    data_dir: Path, index: Index, tz: ZoneInfo, batch_at: float, entity_ids: list[str]
) -> int:
    """Nimmt die Sammelmarkierung ``batch_at`` bei den genannten Entitäten zurück."""
    before = index.get_counter_decrease_snapshot()
    restored = 0
    touched: set[str] = set()
    for entity_id in entity_ids:
        started_at = time.time()
        count = index.undo_deleted_batch(entity_id, batch_at)
        if count:
            restored += count
            touched.add(entity_id)
            index.log_entity_action(
                entity_id, "undo", "manual", started_at, time.time(), "success",
                rows_affected=count, detail=json.dumps({"mode": "batch"}),
            )
    if touched:
        index.invalidate_counter_decrease_snapshot()
        # Zurückgenommene Werte können wieder Rückgänge sein, die der Schnappschuss
        # nicht mehr kennt — also neu rechnen, auch wenn ``before`` Zeilen davon enthielt.
        refresh_snapshot(data_dir, index, tz, before, touched)
    return restored


#: So weit vor dem ersten und nach dem letzten angezeigten Rückgang wird gelesen, um den
#: Vorwert zu finden bzw. zu sehen, ob der Zähler zurückkehrt (siehe verdicts_for_rows).
_LOOKBACK_SECONDS = 3 * 86400
_LOOKAHEAD_SECONDS = 6 * 3600


def followup_note(item: dict, entity: dict) -> str:
    """Satz zur Einordnung eines Rückgangs: was danach mit dem Stand geschah. Dieselben Texte
    wie Housekeeping → Zählerrückgänge, damit beide Stellen dasselbe sagen."""
    from .notices import counter_value_label

    if item["returns"]:
        return tr(
            "Stand danach wieder bei {value} (nach {minutes} Min.)",
            minutes=max(1, round((item["recovered_after"] or 0) / 60)),
            value=counter_value_label(item["recovered_value"], dict(entity)).replace(" ", "\u00a0"),
        )
    if item["returns"] is False:
        return tr("Stand blieb danach niedrig.")
    return tr("Zu wenig Folgewerte für eine Einschätzung.")


def verdict_details_for_rows(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, rows: list[dict], now: datetime | None = None
) -> dict[float, dict]:
    """Einordnung der Zählerrückgänge einer Seite der Bereinigungsansicht: Zeitstempel →
    ``{"returns": True | False | None, "note": Satz}``. True = kehrt zurück (wahrscheinlich
    Fehlwert), False = bleibt niedrig (wahrscheinlich Zählerwechsel oder Reset), None = die Daten
    enden, bevor sich das entscheiden lässt. Ein Durchlauf über das Fenster der angezeigten
    Rückgänge statt je Zeile einer. Jede markierte Zeile kommt im Ergebnis vor; findet die
    Einordnung sie nicht, gilt sie als offen."""
    label = tr("Zählerrückgang")
    stamps = {row["ts"] for row in rows if any(flag["label"] == label for flag in row.get("flags") or [])}
    entity = index.get_entity(entity_id)
    if not stamps or entity is None:
        return {}
    now = now or datetime.now(tz)
    decreases = cleanup.classify_counter_decreases(
        cleanup.iter_raw_rows(
            data_dir, index, entity_id, min(stamps) - _LOOKBACK_SECONDS,
            min(max(stamps) + _LOOKAHEAD_SECONDS, now.timestamp()), tz, now=now,
        ),
        tz,
    )
    details = {
        item["ts"]: {"returns": item["returns"], "note": followup_note(item, entity)}
        for item in decreases if item["ts"] in stamps
    }
    # Eine markierte Zeile ohne Einordnung gibt es nicht: was die Einordnung nicht findet
    # (Markierung und Einordnung rechnen getrennt), steht als „Noch offen“ da.
    offen = {"returns": None, "note": followup_note({"returns": None}, entity)}
    return {ts: details.get(ts, offen) for ts in stamps}


def verdicts_for_rows(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, rows: list[dict], now: datetime | None = None
) -> dict[float, bool]:
    """Wie verdict_details_for_rows, aber nur das Urteil: Zeitstempel → True (Fehlwert) oder False
    (bleibt niedrig). Rückgänge ohne Entscheidung fehlen im Ergebnis."""
    details = verdict_details_for_rows(data_dir, index, tz, entity_id, rows, now)
    return {ts: item["returns"] for ts, item in details.items() if item["returns"] is not None}


def set_mode(data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, mode: str) -> None:
    """„report“ oder „allow“ für einen Zähler setzen und den Schnappschuss der Rückgänge für genau
    diese Entität nachführen (erlaubt: Zeilen fallen weg; wieder gemeldet: neu berechnet)."""
    before = index.get_counter_decrease_snapshot()
    index.set_counter_decreases(entity_id, mode)
    refresh_snapshot(data_dir, index, tz, before, {entity_id})
