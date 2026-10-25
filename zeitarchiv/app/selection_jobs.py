"""Aufträge des Auswahl-Assistenten an die Integration (ROADMAP 1.33, Phase 1).

Die App ändert die Auswahl nie selbst: sie legt nach einer Bestätigung in der
Oberfläche einen Auftrag ab, die Integration holt ihn über ``GET
/api/selection/jobs`` (Token-Port) ab, wendet ihn an und quittiert ihn je ID über
``POST /api/selection/ack``. ``selection_rev`` zählt bei jedem angelegten,
quittierten oder abgelaufenen Auftrag hoch und steht in ``/api/notices``: so holt
die Integration nur, wenn sich etwas geändert hat.

Ablage wie der Katalog als JSON-Datei im Datenverzeichnis, atomar ersetzt und
hinter einer Sperre (Routen laufen in Threads). Offene Aufträge bleiben alle,
beendete höchstens ``KEEP_FINISHED``.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .i18n import tr
from .selection_catalog import RESULTS, valid_entity_id

FILENAME = "selection_jobs.json"

MAX_IDS = 200
TTL_SECONDS = 24 * 3600
KEEP_FINISHED = 200
MAX_MESSAGE = 300

SOURCES = ("assistant", "manual_ui", "undo")
ACTIONS = ("add", "remove")
ADD_MODES = ("label", "ids")
ACK_STATUSES = ("applied", "partial", "failed")
FINISHED = (*ACK_STATUSES, "expired")

#: Felder, die die Integration bekommt (der Vertrag); der Rest ist Buchführung der App.
PUBLIC_FIELDS = (
    "id", "created_ts", "expires_ts", "source", "action", "mode", "topic", "entity_ids", "undo_of", "force_ids",
)

_LOCK = threading.Lock()


class JobError(ValueError):
    """Ungültiger Auftrag; der Text ist für die Oberfläche bestimmt (schon übersetzt)."""


def _empty() -> dict[str, Any]:
    return {"rev": 0, "jobs": []}


def _read(data_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((data_dir / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list) or not isinstance(data.get("rev"), int):
        return _empty()
    data["jobs"] = [job for job in data["jobs"] if isinstance(job, dict) and isinstance(job.get("id"), str)]
    return data


def _write(data_dir: Path, data: dict[str, Any]) -> None:
    finished = [job for job in data["jobs"] if job.get("status") != "pending"]
    if len(finished) > KEEP_FINISHED:
        finished.sort(key=lambda job: job.get("finished_ts") or job.get("created_ts") or 0, reverse=True)
        keep = {job["id"] for job in finished[:KEEP_FINISHED]}
        data["jobs"] = [job for job in data["jobs"] if job.get("status") == "pending" or job["id"] in keep]
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=FILENAME, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, data_dir / FILENAME)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _expire(data: dict[str, Any], now: float) -> bool:
    changed = False
    for job in data["jobs"]:
        if job.get("status") == "pending" and float(job.get("expires_ts") or 0) <= now:
            job["status"], job["finished_ts"] = "expired", now
            changed = True
    if changed:
        data["rev"] += 1
    return changed


def _load_expired(data_dir: Path, now: float | None) -> dict[str, Any]:
    """Liest unter der Sperre und schreibt nur, wenn ein Auftrag gerade abgelaufen ist."""
    data = _read(data_dir)
    if _expire(data, time.time() if now is None else now):
        _write(data_dir, data)
    return data


def public(job: dict[str, Any]) -> dict[str, Any]:
    return {field: job.get(field) for field in PUBLIC_FIELDS}


def rev(data_dir: Path, now: float | None = None) -> int:
    with _LOCK:
        return int(_load_expired(data_dir, now)["rev"])


def all_jobs(data_dir: Path, now: float | None = None) -> list[dict[str, Any]]:
    """Alle gespeicherten Aufträge, neueste zuerst (für Verlauf und Statusanzeige)."""
    with _LOCK:
        jobs = _load_expired(data_dir, now)["jobs"]
    return sorted(jobs, key=lambda job: job.get("created_ts") or 0, reverse=True)


def get(data_dir: Path, job_id: str, now: float | None = None) -> dict[str, Any] | None:
    return next((job for job in all_jobs(data_dir, now) if job["id"] == job_id), None)


def _may_serve(job: dict[str, Any], requester: str | None) -> bool:
    """Ein Auftrag gehört dem Eintrag, der beim Anlegen Besitzer war. Ohne Header
    (alte Integration) oder ohne bekannten Besitzer gilt jeder Aufruf als passend."""
    return requester is None or job.get("entry_id") in (None, requester)


def create(
    data_dir: Path,
    *,
    source: str,
    action: str,
    entity_ids: Iterable[str],
    mode: str | None = None,
    topic: str | None = None,
    undo_of: str | None = None,
    force_ids: Iterable[str] = (),
    entry_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Prüft und legt einen Auftrag an; ``JobError`` mit lesbarem Text bei ungültigen Angaben."""
    if source not in SOURCES or action not in ACTIONS:
        raise JobError(tr("Unbekannte Art von Auftrag."))
    if action == "add" and mode not in ADD_MODES:
        raise JobError(tr("Unbekannter Modus für das Hinzufügen."))
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        raise JobError(tr("Keine Entität ausgewählt."))
    if len(ids) > MAX_IDS:
        raise JobError(tr("Höchstens {max} Entitäten je Auftrag.", max=MAX_IDS))
    invalid = [eid for eid in ids if not valid_entity_id(eid)]
    if invalid:
        raise JobError(tr("Ungültige Entitäts-ID: {entity_id}", entity_id=str(invalid[0])[:80]))
    now = time.time() if now is None else now
    job = {
        "id": uuid.uuid4().hex,
        "created_ts": now,
        "expires_ts": now + TTL_SECONDS,
        "source": source,
        "action": action,
        "mode": mode if action == "add" else "auto",
        "topic": (topic or None) and str(topic)[:40],
        "entity_ids": ids,
        "undo_of": undo_of,
        "force_ids": [eid for eid in dict.fromkeys(force_ids) if eid in ids] if action == "add" else [],
        "entry_id": entry_id,
        "status": "pending",
        "results": {},
        "message": None,
        "finished_ts": None,
    }
    with _LOCK:
        data = _read(data_dir)
        _expire(data, now)
        data["jobs"].append(job)
        data["rev"] += 1
        _write(data_dir, data)
    return job


def pending_for(data_dir: Path, requester: str | None, owner: str | None, now: float | None = None) -> list[dict]:
    """Offene, nicht abgelaufene Aufträge für den aufrufenden Eintrag — leer, wenn er
    nicht der Besitzer des Katalogs ist."""
    if requester is not None and owner is not None and requester != owner:
        return []
    jobs = [job for job in reversed(all_jobs(data_dir, now)) if job.get("status") == "pending"]
    return [public(job) for job in jobs if _may_serve(job, requester)]


def ack(
    data_dir: Path,
    job_id: str,
    status: str,
    results: Any = None,
    message: Any = None,
    *,
    requester: str | None = None,
    owner: str | None = None,
    now: float | None = None,
) -> bool:
    """Quittiert einen offenen Auftrag. Ein zweites Ack desselben Auftrags ändert nichts
    und gilt als Erfolg; unbekannte, abgelaufene oder fremde Aufträge liefern False."""
    if status not in ACK_STATUSES:
        return False
    if requester is not None and owner is not None and requester != owner:
        return False
    now = time.time() if now is None else now
    with _LOCK:
        data = _read(data_dir)
        expired = _expire(data, now)
        job = next((item for item in data["jobs"] if item["id"] == job_id), None)
        if job is None or job.get("status") == "expired" or not _may_serve(job, requester):
            if expired:
                _write(data_dir, data)
            return False
        if job.get("status") in ACK_STATUSES:
            if expired:
                _write(data_dir, data)
            return True
        allowed = set(job["entity_ids"])
        cleaned: dict[str, dict[str, Any]] = {}
        for entity_id, item in (results.items() if isinstance(results, dict) else ()):
            if entity_id in allowed and isinstance(item, dict) and item.get("result") in RESULTS:
                mode = item.get("mode") if item.get("mode") in ADD_MODES else None
                cleaned[entity_id] = {"result": item["result"], "mode": mode}
        job.update({
            "status": status, "results": cleaned, "finished_ts": now,
            "message": message.strip()[:MAX_MESSAGE] or None if isinstance(message, str) else None,
        })
        data["rev"] += 1
        _write(data_dir, data)
    return True
