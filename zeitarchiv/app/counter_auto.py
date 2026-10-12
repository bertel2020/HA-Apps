"""Regel „Fehlwerte automatisch markieren“ (je Zähler, Konfiguration → Zählerrückgänge, nur bei „Melden“).

Der stündliche Lauf markiert bei Zählern mit eingeschalteter Regel die Rückgänge, die sofort wieder
zurückkehren (Einordnung „kehrt zurück“, siehe cleanup.classify_counter_decreases) — nur den Fehlwert
selbst, nie Vor- oder Folgewert, und nichts endgültig: die Werte landen als eigene Charge im Reiter
„Markiert“ und lassen sich zurücknehmen. Eine zurückgenommene Charge wird nicht erneut markiert
(Tabelle counter_mark_ignored), sonst käme derselbe Wert beim nächsten Lauf sofort wieder.

Die Regel gilt nur für Rückgänge ab dem Einschalten (``counter_auto_since``), nie rückwirkend. Als
Sicherung markiert ein Lauf höchstens MAX_PER_RUN Werte je Zähler: bei mehr hält die Regel für diesen
Zähler an und meldet das, statt einen kaputten Datenstrom Stunde für Stunde wegzumarkieren."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .i18n import tr
from .storage import cleanup
from .storage.index import Index

MAX_PER_RUN = 50
RUN_INTERVAL_SECONDS = 3600
_LAST_RUN_KEY = "counter_auto_mark_last_run"
#: Wie weit vor ``since`` gelesen wird, damit der Vorwert des ersten Rückgangs bekannt ist.
_LOOKBACK_SECONDS = 3 * 86400
#: Meldung „Fehlwerte automatisch markiert“: an so vielen Tagen der letzten 14 wurde markiert.
INFO_MIN_DAYS = 3
INFO_WINDOW_DAYS = 14


def process_entity(data_dir: Path, index: Index, tz: ZoneInfo, entity, now: datetime) -> int:
    """Markiert die zurückkehrenden Rückgänge eines Zählers; Anzahl markierter Werte (0 bei
    angehaltener Regel oder wenn die Sicherung ausgelöst hat)."""
    if entity["counter_auto_halted_at"] is not None or entity["counter_auto_since"] is None:
        return 0
    since = entity["counter_auto_since"]
    decreases = cleanup.classify_counter_decreases(
        cleanup.iter_raw_rows(
            data_dir, index, entity["entity_id"], since - _LOOKBACK_SECONDS, now.timestamp(), tz, now=now
        ),
        tz,
    )
    ignored = index.list_counter_mark_ignored(entity["entity_id"])
    todo = [
        item["ts"] for item in decreases
        if item["returns"] is True and item["ts"] >= since and item["ts"] not in ignored
    ]
    if len(todo) > MAX_PER_RUN:
        index.set_counter_auto_halted(entity["entity_id"], now.timestamp())
        return 0
    if todo:
        cleanup.soft_delete(index, entity["entity_id"], todo, source="counter_rule", trigger="automatic")
    return len(todo)


def run_if_due(data_dir: Path, index: Index, tz: ZoneInfo, entity_lock, now: datetime | None = None) -> bool:
    """Stündlicher Lauf über alle Zähler mit Regel. ``entity_lock(entity_id)`` liefert den Kontextmanager
    des Speicher-Koordinators (derselbe Schutz wie bei den Routen, die Rohwerte lesen)."""
    now = now or datetime.now(tz)
    last = index.get_setting(_LAST_RUN_KEY)
    if last is not None and now.timestamp() - float(last) < RUN_INTERVAL_SECONDS:
        return False
    index.set_setting(_LAST_RUN_KEY, str(now.timestamp()))
    for entity in index.list_counter_auto_entities():
        with entity_lock(entity["entity_id"]):
            process_entity(data_dir, index, tz, entity, now)
    return True


def rule_marks(index: Index, entity_id: str, now: float, window_days: int) -> list[dict]:
    """Markierungen der Regel in den letzten ``window_days`` Tagen: [{"ts", "count"}], neueste zuerst."""
    import json

    marks = []
    for row in index.list_entity_actions_for_entity(
        entity_id, since_ts=now - window_days * 86400, actions=("mark",), limit=1000
    ):
        try:
            detail = json.loads(row["detail"]) if row["detail"] else {}
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and detail.get("source") == "counter_rule":
            marks.append({"ts": row["created_at"], "count": int(row["rows_affected"] or 0)})
    return marks


def summary(index: Index, now: float | None = None) -> dict[str, dict]:
    """Zähler mit Regel → {"marked": markierte Werte der letzten 7 Tage, "halted": bool} (Housekeeping)."""
    now = time.time() if now is None else now
    return {
        row["entity_id"]: {
            "marked": sum(m["count"] for m in rule_marks(index, row["entity_id"], now, 7)),
            "halted": row["counter_auto_halted_at"] is not None,
        }
        for row in index.list_counter_auto_entities()
    }


def notices(index: Index, tz: ZoneInfo, now: float | None = None) -> list[dict]:
    """Glocke: eine Info, wenn die Regel an mindestens INFO_MIN_DAYS von INFO_WINDOW_DAYS Tagen markiert
    hat (das spricht für ein Problem an der Quelle), und eine Warnung, wenn die Sicherung sie angehalten hat.
    Einzelne Markierungen arbeiten still, der Verlauf zeigt sie."""
    now = time.time() if now is None else now
    found = []
    for row in index.list_counter_auto_entities():
        name = row["custom_name"] or row["friendly_name"] or row["entity_id"]
        link = f"/entities/{row['entity_id']}/cleanup"
        if row["counter_auto_halted_at"] is not None:
            found.append({
                "id": f"housekeeping.counter_rule_halted.{row['entity_id']}", "severity": "warn",
                "title": tr("Regel „Fehlwerte markieren“ angehalten"),
                "detail": tr(
                    "Bei „{name}“ gab es in einem Lauf mehr als {limit} zurückkehrende Rückgänge. Die Regel markiert nichts mehr, bis sie in der Konfiguration neu eingeschaltet wird.",
                    name=name, limit=MAX_PER_RUN,
                ),
                "meta": tr("Zählerrückgänge"), "link": link,
            })
            continue
        marks = rule_marks(index, row["entity_id"], now, INFO_WINDOW_DAYS)
        days = {datetime.fromtimestamp(m["ts"], tz).date() for m in marks}
        if len(days) >= INFO_MIN_DAYS:
            found.append({
                "id": f"housekeeping.counter_rule.{row['entity_id']}", "severity": "info",
                "title": tr("„{name}“: Fehlwerte automatisch markiert", name=name),
                "detail": tr(
                    "An {days} der letzten {window} Tage wurden Fehlwerte automatisch markiert. Das spricht für ein Problem an der Quelle (Gerät oder Integration).",
                    days=len(days), window=INFO_WINDOW_DAYS,
                ),
                "meta": tr("Zählerrückgänge"), "link": link,
            })
    return found
