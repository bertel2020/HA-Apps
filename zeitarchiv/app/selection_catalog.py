"""Ablage des Entitäten-Katalogs, den die Integration schickt (ROADMAP 1.33).

Der Katalog besteht nur aus Metadaten (Name, Geräteklasse, Einheit, Gerät,
Bereich, Energie-Rolle …), nie aus Messwerten, und ersetzt jeweils den vorigen
vollständig. Er liegt als eine JSON-Datei im Datenverzeichnis und nicht in
SQLite: er ist ein Cache der Integration, im Zweifel schickt sie ihn neu, und er
soll weder Index noch Backups aufblähen.

Seit Phase 1 trägt er zusätzlich, wie jede Entität in der Auswahl ist (``via``,
``excluded_by``, Herkunft aus dem Ledger der Integration), das vom Assistenten
verwaltete Label, den Verlauf (``log``) und die ``entry_id`` des sendenden
Eintrags: der ist der **Besitzer**, nur er bekommt Aufträge (selection_jobs.py).
Die alte Form ``{"entities": [...]}`` bleibt gültig; ``phase1`` hält fest, ob die
Integration die neue Form schon spricht (sonst bleibt die Oberfläche lesend).

Phase 2 bringt die Include-Muster der Integration mit (``patterns``: Muster, Herkunft,
Treffer) und neue Verlaufsaktionen (Ausschließen, Wieder aufnehmen, Muster). ``phase2``
hält fest, ob die Integration schon ``patterns`` schickt: erst dann bietet die
Oberfläche Ausschließen, Wieder aufnehmen und Muster an, eine Integration aus Phase 1
kennt diese Aufträge nicht.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .storage.paths import ENTITY_ID_PATTERN

FILENAME = "selection_catalog.json"

#: Mehr Zeilen nimmt die App je Katalog nicht an.
MAX_ENTITIES = 20_000
#: Verlaufseinträge, IDs je Eintrag und Wege je Entität, die die App übernimmt.
MAX_LOG = 200
MAX_LOG_IDS = 50
MAX_WAYS = 20
#: Include-Muster im Katalog, Muster je Verlaufseintrag (Phase 2).
MAX_PATTERNS = 100
MAX_LOG_PATTERNS = 20

_ENTITY_ID_RE = re.compile(ENTITY_ID_PATTERN)
_ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOG_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: Entitäts-Muster wie in der Integration (``filtering.ENTITY_PATTERN_RE``): nur ``*`` und
#: ``?``, klein geschrieben, ohne Punkt gegen die Object-ID, mit Punkt gegen die ganze ID.
PATTERN_RE = re.compile(r"^[a-z0-9_*?]+(?:\.[a-z0-9_*?]+)?$")
MAX_PATTERN_LENGTH = 128

_TEXT_FIELDS = {
    "name": 200, "device_class": 40, "state_class": 40, "unit": 40, "platform": 80,
    "device": 200, "area": 200, "entity_category": 20, "energy_role": 40,
}
_FLAG_FIELDS = ("hidden", "selected", "excluded", "text", "drifted")
_VALUE_WAYS = {"pattern", "area", "device", "label"}
_LABEL_LEVELS = {"entity", "device", "area"}
_MODES = {"label", "ids"}
_ORIGINS = {"assistant", "manual"}
LOG_SOURCES = {"assistant", "manual_ui", "undo", "manual_ha"}
LOG_ACTIONS = {"add", "remove", "change", "exclude", "include", "add_patterns", "remove_patterns"}
PATTERN_ACTIONS = {"add_patterns", "remove_patterns"}
LOG_STATUSES = {"applied", "partial", "failed"}
RESULTS = {"ok", "already", "not_found", "excluded", "blocked", "unsupported", "invalid", "too_many", "error"}
_HA_DETAIL_COUNTS = ("added_entities", "removed_entities")
_HA_DETAIL_DELTAS = ("patterns", "areas", "devices", "labels", "excluded")


def _text(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] or None if isinstance(value, str) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _count(value: Any, limit: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return min(value, limit)


def valid_entity_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 255 and bool(_ENTITY_ID_RE.match(value))


def valid_pattern(value: Any) -> bool:
    """Ein Muster in der Form, die die Integration annimmt (schon normalisiert)."""
    return isinstance(value, str) and len(value) <= MAX_PATTERN_LENGTH and bool(PATTERN_RE.fullmatch(value))


def clean_result(value: Any) -> str | None:
    """Ergebniswert je ID: bekannte bleiben, unbekannte Texte gelten als ``error``
    (eine neuere Integration darf neue Werte schicken), alles andere fällt weg."""
    if value in RESULTS:
        return value
    return "error" if isinstance(value, str) and value else None


def clean_entry_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _ENTRY_ID_RE.match(value) else None


def _clean_via(raw: Any) -> list[dict[str, Any]]:
    ways: list[dict[str, Any]] = []
    for item in raw[:MAX_WAYS] if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "entity":
            way: dict[str, Any] = {"type": "entity"}
        elif kind in _VALUE_WAYS:
            value = _text(item.get("value"), 200)
            if value is None:
                continue
            way = {"type": kind, "value": value}
            if kind == "pattern" and item.get("assistant") is True:
                way["assistant"] = True
            if kind == "label":
                way["level"] = item.get("level") if item.get("level") in _LABEL_LEVELS else "entity"
        elif kind == "assistant":
            way = {
                "type": "assistant", "topic": _text(item.get("topic"), 40),
                "mode": item.get("mode") if item.get("mode") in _MODES else None,
            }
        else:
            continue
        if way not in ways:
            ways.append(way)
    return ways


def _clean_excluded_by(raw: Any) -> list[dict[str, Any]]:
    ways: list[dict[str, Any]] = []
    for item in raw[:MAX_WAYS] if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        if item.get("type") == "entity":
            way: dict[str, Any] = {"type": "entity"}
        elif item.get("type") == "pattern" and _text(item.get("value"), 200):
            way = {"type": "pattern", "value": _text(item.get("value"), 200)}
        else:
            continue
        if way not in ways:
            ways.append(way)
    return ways


def clean_entities(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Übernimmt nur bekannte Felder in der erwarteten Form. Zeilen mit ungültiger
    Entitäts-ID fallen weg, Texte werden gekürzt: der Inhalt kommt von außen und
    landet später in Seiten."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_ENTITIES]:
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not valid_entity_id(entity_id) or entity_id in seen:
            continue
        seen.add(entity_id)
        row: dict[str, Any] = {"entity_id": entity_id, "domain": entity_id.split(".", 1)[0]}
        for field, limit in _TEXT_FIELDS.items():
            row[field] = _text(item.get(field), limit)
        for field in _FLAG_FIELDS:
            row[field] = bool(item.get(field))
        row["via"] = _clean_via(item.get("via"))
        row["excluded_by"] = _clean_excluded_by(item.get("excluded_by"))
        row["origin"] = item.get("origin") if item.get("origin") in _ORIGINS else None
        row["topic"] = _text(item.get("topic"), 40)
        row["added_ts"] = _number(item.get("added_ts"))
        rows.append(row)
    return rows


def _clean_results(raw: Any, allowed: set[str]) -> dict[str, dict[str, Any]]:
    """Ergebnisse je Entitäts-ID oder (bei Musteraktionen) je Musterstring."""
    results: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return results
    for key, item in list(raw.items())[: len(allowed)]:
        result = clean_result(item.get("result")) if isinstance(item, dict) else None
        if key not in allowed or result is None:
            continue
        results[key] = {"result": result, "mode": item.get("mode") if item.get("mode") in _MODES else None}
    return results


def _clean_ha_detail(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    detail: dict[str, Any] = {"kind": _text(raw.get("kind"), 20) or "options"}
    for field in _HA_DETAIL_COUNTS:
        detail[field] = _count(raw.get(field))
    for field in _HA_DETAIL_DELTAS:
        value = raw.get(field)
        detail[field] = value.strip()[:12] if isinstance(value, str) and re.fullmatch(r"\s*[±+-]?\d{1,7}\s*", value) else None
    detail["summary"] = _text(raw.get("summary"), 300)
    return detail


def _log_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _LOG_ID_RE.match(value) else None


def clean_log(raw: Any) -> list[dict[str, Any]]:
    """Der Verlauf der Integration, streng auf die Felder des Vertrags reduziert."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_LOG] if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        entry_id, ts = _log_id(item.get("id")), _number(item.get("ts"))
        if entry_id is None or entry_id in seen or ts is None:
            continue
        if item.get("source") not in LOG_SOURCES or item.get("action") not in LOG_ACTIONS:
            continue
        seen.add(entry_id)
        ids = [eid for eid in item.get("entity_ids") or [] if valid_entity_id(eid)][:MAX_LOG_IDS] \
            if isinstance(item.get("entity_ids"), list) else []
        ids = list(dict.fromkeys(ids))
        patterns = list(dict.fromkeys(
            value for value in item.get("patterns") or [] if valid_pattern(value)
        ))[:MAX_LOG_PATTERNS] if isinstance(item.get("patterns"), list) else []
        entries.append({
            "id": entry_id, "ts": ts, "source": item["source"], "action": item["action"],
            "mode": item.get("mode") if item.get("mode") in _MODES else None,
            "topic": _text(item.get("topic"), 40),
            "entity_ids": ids,
            "patterns": patterns,
            "count": max(_count(item.get("count")), len(ids), len(patterns)),
            "status": item.get("status") if item.get("status") in LOG_STATUSES else "applied",
            "results": _clean_results(item.get("results"), set(ids) | set(patterns)),
            "undo_of": _log_id(item.get("undo_of")),
            "undone_by": _log_id(item.get("undone_by")),
            "ha_detail": _clean_ha_detail(item.get("ha_detail")),
        })
    entries.sort(key=lambda entry: entry["ts"], reverse=True)
    return entries


def clean_patterns(raw: Any) -> list[dict[str, Any]]:
    """Die Include-Muster der Integration (Phase 2): Muster, Herkunft, Thema, Zeit, Treffer."""
    patterns: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_PATTERNS] if isinstance(raw, list) else ():
        if not isinstance(item, dict) or not valid_pattern(item.get("pattern")) or item["pattern"] in seen:
            continue
        seen.add(item["pattern"])
        patterns.append({
            "pattern": item["pattern"],
            "origin": "assistant" if item.get("origin") == "assistant" else "manual",
            "topic": _text(item.get("topic"), 40),
            "added_ts": _number(item.get("added_ts")),
            "count": _count(item.get("count"), MAX_ENTITIES),
        })
    return patterns


def clean_label(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    label_id, name = _text(raw.get("id"), 100), _text(raw.get("name"), 200)
    return {"id": label_id, "name": name or label_id} if label_id else None


def save(
    data_dir: Path,
    entities: list[dict[str, Any]],
    *,
    label: Any = None,
    log: Any = None,
    ledger_count: Any = None,
    entry_id: Any = None,
    phase1: bool = False,
    patterns: Any = None,
    phase2: bool = False,
) -> int:
    """Ersetzt den Katalog atomar und gibt die Zahl der übernommenen Zeilen zurück."""
    rows = clean_entities(entities)
    payload = json.dumps({
        "received_ts": time.time(), "entities": rows, "label": clean_label(label), "log": clean_log(log),
        "ledger_count": _count(ledger_count), "entry_id": clean_entry_id(entry_id), "phase1": bool(phase1),
        "patterns": clean_patterns(patterns), "phase2": bool(phase2),
    }, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=FILENAME, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, data_dir / FILENAME)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return len(rows)


def load(data_dir: Path) -> dict[str, Any] | None:
    """Der zuletzt empfangene Katalog (``received_ts``, ``entities``, ``label``, ``log``,
    ``ledger_count``, ``entry_id``, ``phase1``, ``patterns``, ``phase2``) oder None, wenn noch
    keiner da oder die Datei unlesbar ist. Ältere Kataloge bekommen die neuen Felder mit Leerwerten."""
    try:
        data = json.loads((data_dir / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("entities"), list):
        return None
    data.setdefault("label", None)
    data.setdefault("log", [])
    data.setdefault("ledger_count", 0)
    data.setdefault("entry_id", None)
    data.setdefault("phase1", False)
    data.setdefault("patterns", [])
    data.setdefault("phase2", False)
    for entry in data["log"]:
        entry.setdefault("patterns", [])
    for row in data["entities"]:
        for field in ("text", "drifted"):
            row.setdefault(field, False)
        row.setdefault("via", [])
        row.setdefault("excluded_by", [])
        row.setdefault("origin", None)
        row.setdefault("topic", None)
        row.setdefault("added_ts", None)
    return data


def owner(data_dir: Path) -> str | None:
    """``entry_id`` des Eintrags, der den Katalog zuletzt geschickt hat (oder None)."""
    catalog = load(data_dir)
    return catalog.get("entry_id") if catalog else None
