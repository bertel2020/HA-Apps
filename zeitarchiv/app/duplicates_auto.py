"""„Duplikate automatisch entfernen“ (Housekeeping → Duplikate, globaler Schalter, standardmäßig aus).

Der stündliche Lauf markiert doppelte Zeitstempel der letzten 30 Tage zur Löschung (Soft-Delete wie der Knopf auf
der Bereinigungsseite, Herkunft ``duplicates_auto``): nichts wird endgültig entfernt, die Werte stehen im Reiter
„Markiert“ und lassen sich zurücknehmen. Jeder Lauf mit Fund hinterlässt einen Protokolleintrag (``entity_actions``,
Aktion ``duplicates_auto_run``) und eine Meldung; alle Entitäten eines Laufs teilen sich dasselbe ``deleted_at``, ein
Lauf lässt sich deshalb als Ganzes oder je Entität zurücknehmen.

Gebaut wie ``counter_auto``: eigener Takt, Sicherung je Entität (mehr als MAX_PER_ENTITY Treffer in einem Lauf heißt, an
der Quelle stimmt etwas nicht: die Entität wird angehalten statt Stunde für Stunde weggeräumt), eine Ignorierliste
für zurückgenommene Zeitstempel (``duplicate_auto_ignored``). Der erste Lauf nach dem Einschalten räumt die 30 Tage
ohne diese Sicherung auf (die Einschalt-Vorschau nannte die Zahl vorher); ein Rest wird in den Folgeläufen
nachgeholt (``duplicates_auto_backlog``).

Modi: ``identical`` (Standard) entfernt nur Zeilen mit gleichem Zeitstempel UND Wert, ``all`` lässt je Zeitstempel den
größten Wert stehen. Zustands-Entitäten fasst die Automatik nie an, Zähler mit „Rückgänge erlauben“ nur bei gleichem
Wert (am Reset-Zeitstempel können Endstand und 0 zusammenfallen)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import data_quality
from .formatting import DEFAULT_DUPLICATES_AUTO_ENABLED, DEFAULT_DUPLICATES_AUTO_MODE, DUPLICATES_AUTO_MODES, format_int
from .i18n import tr
from .storage import cleanup
from .storage.coordinator import CoordinatorBusy
from .storage.index import STATE_TYPE, Index

logger = logging.getLogger(__name__)

SOURCE = "duplicates_auto"
RUN_ACTION = "duplicates_auto_run"
WINDOW_DAYS = 30
RUN_INTERVAL_SECONDS = 3600
#: Mehr Treffer je Entität in einem Folgelauf: die Entität wird angehalten (nichts markiert) und gemeldet.
MAX_PER_ENTITY = 500
#: Obergrenze je Lauf über alle Entitäten; der Rest kommt im nächsten Lauf (begrenzt Laufzeit und Chargengröße).
MAX_PER_RUN = 20_000
#: So lange zeigt Housekeeping die Läufe an.
RUNS_WINDOW_DAYS = 7
#: So lange steht die Meldung „Duplikate automatisch entfernt“ höchstens in der Glocke.
NOTICE_DAYS = 14

_ENABLED = "duplicates_auto_enabled"
_MODE = "duplicates_auto_mode"
_SINCE = "duplicates_auto_since"
_LAST_RUN = "duplicates_auto_last_run"
_SEEN = "duplicates_auto_seen"
_BACKLOG = "duplicates_auto_backlog"


def _float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def entities_label(count: int) -> str:
    """„1 Entität“ / „{count} Entitäten“ (beide Texte stehen schon im Katalog)."""
    return tr("{count} Entität", count=count) if count == 1 else tr("{count} Entitäten", count=count)


def settings(index: Index) -> dict:
    """Schalter und Stand: enabled, mode, since, last_run, seen (Unix-Zeit oder None), backlog (erster Lauf offen)."""
    mode = index.get_setting(_MODE, DEFAULT_DUPLICATES_AUTO_MODE)
    return {
        "enabled": index.get_setting(_ENABLED, DEFAULT_DUPLICATES_AUTO_ENABLED) == "on",
        "mode": mode if mode in DUPLICATES_AUTO_MODES else DEFAULT_DUPLICATES_AUTO_MODE,
        "since": _float(index.get_setting(_SINCE)),
        "last_run": _float(index.get_setting(_LAST_RUN)),
        "seen": _float(index.get_setting(_SEEN)),
        "backlog": index.get_setting(_BACKLOG) == "1",
    }


def enable(index: Index, mode: str, now: float) -> None:
    """Einschalten: der erste Lauf kommt beim nächsten Takt und räumt die 30 Tage ohne Sicherung auf."""
    if mode not in DUPLICATES_AUTO_MODES:
        raise ValueError(mode)
    index.set_setting(_MODE, mode)
    index.set_setting(_SINCE, str(now))
    index.set_setting(_SEEN, str(now))
    index.set_setting(_BACKLOG, "1")
    index.set_setting(_LAST_RUN, "")
    index.set_setting(_ENABLED, "on")


def disable(index: Index) -> None:
    index.set_setting(_ENABLED, "off")


def set_mode(index: Index, mode: str) -> None:
    """Modus ändern, solange die Automatik an ist: wirkt ab dem nächsten Lauf."""
    if mode not in DUPLICATES_AUTO_MODES:
        raise ValueError(mode)
    index.set_setting(_MODE, mode)


def mark_seen(index: Index, now: float | None = None) -> None:
    """Die Meldung ist gesehen (Besuch von Housekeeping → Duplikate)."""
    index.set_setting(_SEEN, str(time.time() if now is None else now))


# --- Welche Entitäten ------------------------------------------------------------------------

def entity_status(entity, mode: str) -> tuple[str, str]:
    """(Status, wirksamer Modus) einer Entität: ``on`` (Automatik greift), ``exempt`` (Ausnahme), ``state``
    (Zustands-Entität, nur von Hand), ``halted`` (Sicherung hat ausgelöst)."""
    if entity["aggregation_type"] == STATE_TYPE:
        return "state", mode
    if entity["duplicates_auto"] == "off":
        return "exempt", mode
    if entity["duplicates_auto_halted_at"] is not None:
        return "halted", mode
    # Zähler mit erlaubten Rückgängen: „größter Wert bleibt“ würde den planmäßigen Reset verschlucken.
    return "on", "identical" if entity["counter_decreases"] == "allow" else mode


def _candidates(index: Index, state: dict) -> list[dict]:
    """Entitäten des Duplikat-Schnappschusses, die die Automatik bearbeitet, mit wirksamem Modus und der
    Zahl, die der Schnappschuss für diesen Modus nennt (``expected``)."""
    found = []
    for row in (index.get_duplicate_snapshot() or {}).get("rows", []):
        entity = index.get_entity(row["entity_id"])
        if entity is None:
            continue
        status, mode = entity_status(entity, state["mode"])
        if status != "on":
            continue
        identical = row.get("identical")
        expected = row["count"] if mode == "all" or identical is None else identical
        if expected:
            found.append({"entity": entity, "mode": mode, "expected": expected})
    return found


def preview(index: Index, mode: str) -> dict:
    """Einschalt-Dialog: was der erste Lauf entfernen würde, aus dem Schnappschuss (kein eigener Scan).
    ``rows`` = Entitäten mit Zahl, ``not_automatic`` = Entitäten mit Duplikaten, die die Automatik nicht anfasst."""
    rows, skipped = [], []
    for row in (index.get_duplicate_snapshot() or {}).get("rows", []):
        entity = index.get_entity(row["entity_id"])
        if entity is None:
            continue
        name = entity["custom_name"] or entity["friendly_name"] or row["entity_id"]
        status, effective = entity_status(entity, mode)
        identical = row.get("identical")
        count = row["count"] if effective == "all" or identical is None else identical
        if status == "on" and count:
            rows.append({
                "entity_id": row["entity_id"], "name": name, "count": count,
                "left": row["count"] - count if effective == "identical" and identical is not None else 0,
            })
        elif status == "state":
            skipped.append({"name": name, "reason": tr("Zustands-Entität, nur von Hand")})
        elif status == "exempt":
            skipped.append({"name": name, "reason": tr("Ausnahme")})
        elif status == "on":
            skipped.append({"name": name, "reason": tr("keine wertgleichen Duplikate")})
    total = sum(r["count"] for r in rows)
    return {
        "rows": rows, "skipped": skipped, "total": total, "total_label": format_int(total), "mode": mode,
        "entities_label": entities_label(len(rows)),
    }


# --- Lauf ------------------------------------------------------------------------------------

def _rows_to_mark(data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, mode: str, now: datetime) -> list[tuple[float, float]]:
    start = (now - timedelta(days=WINDOW_DAYS)).timestamp()
    rows = cleanup.iter_raw_rows(data_dir, index, entity_id, start, now.timestamp(), tz, now=now)
    todo = cleanup.iter_identical_duplicate_rows(rows) if mode == "identical" else cleanup.iter_duplicate_rows_to_delete(rows)
    ignored = index.list_duplicate_auto_ignored(entity_id)
    return [(ts, value) for ts, value in todo if ts not in ignored]


def process_entity(
    data_dir: Path, index: Index, tz: ZoneInfo, entity, mode: str, now: datetime, deleted_at: float,
    *, cap: int | None, budget: int,
) -> dict:
    """Markiert die Duplikate einer Entität (Aufrufer hält die Entitäts-Sperre). ``cap``: Höchstzahl je Entität
    (None = ohne Sicherung), ``budget``: wie viele Zeilen der Lauf insgesamt noch markieren darf.
    Ergebnis: {"marked": n, "halted": bool, "truncated": bool}."""
    entity_id = entity["entity_id"]
    todo = _rows_to_mark(data_dir, index, tz, entity_id, mode, now)
    if cap is not None and len(todo) > cap:
        index.set_duplicates_auto_halted(entity_id, now.timestamp())
        return {"marked": 0, "halted": True, "truncated": False, "found": len(todo)}
    truncated = len(todo) > budget
    todo = todo[:max(0, budget)]
    if todo:
        # Im Modus „identical“ mit dem Wert, damit genau die gemeinte Zeile getroffen wird; im Modus „all“ wie der
        # Knopf auf der Bereinigungsseite ohne Wert (die erste Zeile je Zeitstempel in der Reihenfolge, der größte bleibt).
        cleanup.soft_delete(
            index, entity_id, [ts for ts, _ in todo], deleted_at=deleted_at, source=SOURCE, trigger="automatic",
            values=[value for _, value in todo] if mode == "identical" else None,
        )
        data_quality.mark_stats_unknown(index, entity_id)
    return {"marked": len(todo), "halted": False, "truncated": truncated, "found": len(todo)}


def run(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_lock: Callable, now: datetime | None = None,
) -> dict | None:
    """Ein Lauf über alle Kandidaten (ohne Takt-Prüfung). Ergebnis ``None``, wenn nichts zu tun war oder kein
    Schnappschuss vorliegt, sonst das Protokoll des Laufs (auch bei angehaltenen oder belegten Entitäten)."""
    now = now or datetime.now(tz)
    state = settings(index)
    run_at = now.timestamp()
    backlog = state["backlog"]
    started = time.time()
    entities: dict[str, int] = {}
    halted: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    budget = MAX_PER_RUN
    leftover = False
    for candidate in _candidates(index, state):
        entity, mode = candidate["entity"], candidate["mode"]
        entity_id = entity["entity_id"]
        if budget <= 0:
            leftover = True
            break
        try:
            with entity_lock(entity_id):
                result = process_entity(
                    data_dir, index, tz, entity, mode, now, run_at, cap=None if backlog else MAX_PER_ENTITY, budget=budget
                )
        except CoordinatorBusy:
            skipped.append(entity_id)
            continue
        except Exception:
            # Eine Entität darf den Lauf nicht abbrechen (defekte Datei, Sperre): die übrigen laufen weiter.
            logger.exception("Duplikate automatisch entfernen fehlgeschlagen · event=duplicates_auto_failed entity_id=%s", entity_id)
            failed.append(entity_id)
            continue
        if result["halted"]:
            halted.append(entity_id)
            continue
        if result["marked"]:
            entities[entity_id] = result["marked"]
            budget -= result["marked"]
        if result["truncated"]:
            leftover = True
    total = sum(entities.values())
    # Der erste Lauf gilt erst als erledigt, wenn nichts mehr offen oder belegt war.
    if backlog and not (leftover or skipped or failed):
        index.set_setting(_BACKLOG, "0")
    if not (entities or halted or skipped or failed):
        return None
    index.log_entity_action(
        None, RUN_ACTION, "automatic", started, time.time(), "failed" if failed and not entities else "success",
        rows_affected=total,
        detail=json.dumps({
            "run_at": run_at, "mode": state["mode"], "first": backlog, "entities": entities,
            "halted": halted, "skipped_locked": skipped, "failed": failed,
        }),
    )
    logger.info(
        "Duplikate automatisch entfernt · event=duplicates_auto_run entities=%d rows=%d halted=%d skipped=%d duration_ms=%d",
        len(entities), total, len(halted), len(skipped), int((time.time() - started) * 1000),
    )
    return {"run_at": run_at, "entities": entities, "halted": halted, "skipped_locked": skipped, "failed": failed, "total": total}


def run_if_due(data_dir: Path, index: Index, tz: ZoneInfo, entity_lock: Callable, now: datetime | None = None) -> bool:
    """Stündlicher Lauf des Wartungsplaners; ohne eingeschalteten Schalter tut er nichts."""
    now = now or datetime.now(tz)
    state = settings(index)
    if not state["enabled"]:
        return False
    if state["last_run"] is not None and now.timestamp() - state["last_run"] < RUN_INTERVAL_SECONDS:
        return False
    index.set_setting(_LAST_RUN, str(now.timestamp()))
    run(data_dir, index, tz, entity_lock, now)
    return True


def resume_entity(
    data_dir: Path, index: Index, tz: ZoneInfo, entity_id: str, entity_lock: Callable, now: datetime | None = None,
) -> int:
    """„Fortsetzen“ bei einer angehaltenen Entität: hebt das Anhalten auf und räumt sie sofort ohne Sicherung auf
    (die Rückfrage dazu stellt die Oberfläche). Eigener Protokolleintrag, wie ein Lauf. Anzahl markierter Zeilen."""
    now = now or datetime.now(tz)
    entity = index.get_entity(entity_id)
    if entity is None:
        return 0
    state = settings(index)
    index.set_duplicates_auto_halted(entity_id, None)
    entity = index.get_entity(entity_id)
    status, mode = entity_status(entity, state["mode"])
    if status != "on":
        return 0
    started = time.time()
    run_at = now.timestamp()
    with entity_lock(entity_id):
        result = process_entity(data_dir, index, tz, entity, mode, now, run_at, cap=None, budget=MAX_PER_RUN)
    if result["marked"]:
        index.log_entity_action(
            None, RUN_ACTION, "manual", started, time.time(), "success", rows_affected=result["marked"],
            detail=json.dumps({"run_at": run_at, "mode": state["mode"], "first": False, "entities": {entity_id: result["marked"]},
                               "halted": [], "skipped_locked": [], "failed": []}),
        )
    return result["marked"]


# --- Läufe anzeigen und zurücknehmen -----------------------------------------------------------

def runs(index: Index, now: float | None = None, days: int = RUNS_WINDOW_DAYS) -> list[dict]:
    """Läufe der letzten ``days`` Tage, neueste zuerst: {run_at, total, entities: [{entity_id, name, count, left}], …}.
    ``left`` = Zeilen, die noch markiert sind (0 = zurückgenommen oder endgültig bereinigt)."""
    now = time.time() if now is None else now
    result = []
    for row in index.list_entity_actions_by_action(RUN_ACTION, since_ts=now - days * 86400):
        try:
            detail = json.loads(row["detail"]) if row["detail"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(detail, dict) or not isinstance(detail.get("run_at"), (int, float)):
            continue
        run_at = float(detail["run_at"])
        entities = []
        for entity_id, count in (detail.get("entities") or {}).items():
            entity = index.get_entity(entity_id)
            name = (entity["custom_name"] or entity["friendly_name"] or entity_id) if entity is not None else entity_id
            left = index.count_deleted_by_batch(entity_id, [run_at]).get(run_at, 0) if entity is not None else 0
            entities.append({"entity_id": entity_id, "name": name, "count": int(count), "left": left})
        total = sum(e["count"] for e in entities)
        result.append({
            "run_at": run_at, "created_at": row["created_at"], "total": total, "entities": entities,
            "halted": list(detail.get("halted") or []), "skipped_locked": list(detail.get("skipped_locked") or []),
            "first": bool(detail.get("first")), "manual": row["trigger"] == "manual",
            "undone": bool(entities) and all(e["left"] == 0 for e in entities),
        })
    return result


def undo_run(index: Index, run_at: float, entity_lock: Callable, entity_id: str | None = None) -> int:
    """Nimmt einen Lauf zurück (alle Entitäten oder nur ``entity_id``). Die Zeitstempel kommen in die Ignorierliste
    und werden nicht erneut markiert. Wiederholtes Absenden ist harmlos. Anzahl wiederhergestellter Zeilen."""
    targets: list[str] = []
    for row in index.list_entity_actions_by_action(RUN_ACTION, limit=500):
        try:
            detail = json.loads(row["detail"]) if row["detail"] else {}
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and detail.get("run_at") == run_at:
            targets = list((detail.get("entities") or {}).keys())
            break
    if entity_id is not None:
        targets = [entity_id] if entity_id in targets else []
    restored = 0
    for target in targets:
        if index.get_entity(target) is None:
            continue
        with entity_lock(target):
            # Auch bei ausgeschaltetem Schalter merken: der Nutzer hat diese Werte bewusst zurückgeholt.
            index.remember_duplicate_auto_ignored(target, run_at)
            count = cleanup.undo_delete_batch(index, target, run_at)
        if count:
            data_quality.mark_stats_unknown(index, target)
        restored += count
    return restored


# --- Meldungen -------------------------------------------------------------------------------

def _entity_name(index: Index, entity_id: str) -> str:
    entity = index.get_entity(entity_id)
    return (entity["custom_name"] or entity["friendly_name"] or entity_id) if entity is not None else entity_id


def notices(index: Index, tz: ZoneInfo, now: float | None = None) -> list[dict]:
    """Glocke: eine Info über alle noch nicht gesehenen Läufe (verschwindet nach dem Besuch von Housekeeping →
    Duplikate, spätestens nach NOTICE_DAYS Tagen) und eine Warnung je angehaltener Entität. Nur bei
    eingeschalteter Automatik; die Warnung steht, bis die Entität fortgesetzt oder ausgenommen wird."""
    now = time.time() if now is None else now
    state = settings(index)
    found = []
    if not state["enabled"]:
        return found
    seen = state["seen"] or 0.0
    fresh = [r for r in runs(index, now, NOTICE_DAYS) if r["created_at"] > seen and r["total"]]
    if fresh:
        total = sum(r["total"] for r in fresh)
        latest = fresh[0]
        parts = sorted(latest["entities"], key=lambda e: -e["count"])[:3]
        names = ", ".join(f"{e['name']} {format_int(e['count'])}" for e in parts)
        if len(latest["entities"]) > len(parts):
            names += ", …"
        when = datetime.fromtimestamp(latest["created_at"], tz).strftime("%H:%M")
        detail = tr(
            "Letzter Lauf {time}: {total} Werte bei {entities} ({names}). Zurücknehmen geht in Housekeeping unter Duplikate.",
            time=when, total=format_int(latest["total"]), entities=entities_label(len(latest["entities"])), names=names,
        )
        if len(fresh) > 1:
            detail += " " + tr("Seit deinem letzten Besuch gab es {runs} Läufe mit zusammen {total} Werten.", runs=len(fresh), total=format_int(total))
        found.append({
            "id": "housekeeping.duplicates_auto", "severity": "info", "title": tr("Duplikate automatisch entfernt"),
            "detail": detail, "meta": tr("Duplikate"), "link": "/housekeeping#duplikate",
        })
    for entity in index.list_duplicates_auto_halted():
        if entity["duplicates_auto"] == "off" or entity["aggregation_type"] == STATE_TYPE:
            continue
        found.append({
            "id": f"housekeeping.duplicates_auto_halted.{entity['entity_id']}", "severity": "warn",
            "title": tr("Duplikate-Automatik angehalten"),
            "detail": tr(
                "Bei „{name}“ gab es in einem Lauf mehr als {limit} Duplikate. Die Automatik fasst sie nicht mehr an, bis du sie in Housekeeping unter Duplikate fortsetzt: so viele neue Duplikate deuten meist auf ein Problem an der Quelle.",
                name=_entity_name(index, entity["entity_id"]), limit=MAX_PER_ENTITY,
            ),
            "meta": tr("Duplikate"), "link": "/housekeeping#duplikate",
        })
    return found
