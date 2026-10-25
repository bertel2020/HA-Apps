"""„Auswahl & Verlauf“: Tabelle der effektiven Auswahl, Herkunft, Verlauf und
Rückgängig (ROADMAP 1.33, Phase 1).

Reine Funktionen über Katalog (Spiegel der Integration) und Aufträgen der App;
die Routen in selection_ui_routes.py setzen nur zusammen. Die Herkunft rechnet
die Integration (``origin``, ``via``, ``drifted``); hier wird sie nur gelesen,
gefiltert und in Wörter gefasst.
"""

from __future__ import annotations

from typing import Any

from .i18n import N_, tr
from .selection_topics import topic_label

PAGE_SIZE = 50
TIMELINE_LIMIT = 60

#: Filterchips in Anzeigereihenfolge.
FILTERS: tuple[tuple[str, Any], ...] = (
    ("alle", N_("Alle")),
    ("zeitarchiv", N_("Zeitarchiv")),
    ("manuell", N_("Manuell in HA")),
    ("muster", N_("Muster")),
    ("bereich", N_("Bereich")),
    ("geraet", N_("Gerät")),
    ("label", N_("Label")),
    ("ausgeschlossen", N_("Ausgeschlossen")),
    ("geaendert", N_("Von Hand geändert")),
)
FILTER_KEYS = tuple(key for key, _ in FILTERS)

_WAY_FOR_FILTER = {"muster": "pattern", "bereich": "area", "geraet": "device", "label": "label"}


def _ways(row: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [way for way in row.get("via") or () if way.get("type") == kind]


def _own_way(row: dict[str, Any], way: dict[str, Any], managed_label: str | None) -> bool:
    """Der Weg, über den der Assistent die Entität selbst eingetragen hat (sein Label
    oder seine Einzel-ID). Er steht als „Zeitarchiv · Thema“ und nicht noch einmal
    als Label oder als Hand-Eintrag."""
    assistant = _ways(row, "assistant")
    if not assistant:
        return False
    if way.get("type") == "label":
        return bool(managed_label) and way.get("value") == managed_label and way.get("level") in (None, "entity")
    if way.get("type") == "entity":
        return any(item.get("mode") == "ids" for item in assistant)
    return False


def _foreign_ways(row: dict[str, Any], kind: str, managed_label: str | None) -> list[dict[str, Any]]:
    return [way for way in _ways(row, kind) if not _own_way(row, way, managed_label)]


def in_table(row: dict[str, Any]) -> bool:
    """In der Tabelle steht, was ausgewählt, ausgeschlossen oder vom Assistenten verloren ist."""
    return bool(row.get("selected") or row.get("excluded") or row.get("drifted") or row.get("origin"))


def matches_filter(row: dict[str, Any], key: str, managed_label: str | None = None) -> bool:
    if key == "zeitarchiv":
        return row.get("origin") == "assistant"
    if key == "manuell":
        return row.get("origin") == "manual"
    if key in _WAY_FOR_FILTER:
        return bool(_foreign_ways(row, _WAY_FOR_FILTER[key], managed_label))
    if key == "ausgeschlossen":
        return bool(row.get("excluded"))
    if key == "geaendert":
        return bool(row.get("drifted"))
    return True


def manual_single_id(row: dict[str, Any]) -> bool:
    """Von Hand als Einzel-ID in den Optionen der Integration eingetragen."""
    return row.get("origin") != "assistant" and not row.get("drifted") and bool(_ways(row, "entity"))


def removable(row: dict[str, Any]) -> bool:
    """Entfernbar ist eine Entität des Assistenten (Ledger) oder eine von Hand gewählte
    Einzel-ID. Was nur über Muster, Bereich, Gerät oder ein fremdes Label drin ist,
    ändert man in Home Assistant (Ausschließen kommt mit Phase 2)."""
    return row.get("origin") == "assistant" or bool(row.get("drifted")) or manual_single_id(row)


def badges(row: dict[str, Any], managed_label: str | None = None) -> list[dict[str, str]]:
    """Herkunfts-Badges einer Zeile: ``kind`` steuert die Farbe, ``text`` ist übersetzt."""
    out: list[dict[str, str]] = []
    for way in row.get("via") or ():
        kind = way.get("type")
        if _own_way(row, way, managed_label):
            continue
        if kind == "assistant":
            if any(badge["kind"] == "assistant" for badge in out):
                continue
            topic = topic_label(way.get("topic") or row.get("topic"))
            out.insert(0, {"kind": "assistant", "text": tr("Zeitarchiv · {topic}", topic=topic) if topic else tr("Zeitarchiv")})
        elif kind == "entity" and row.get("origin") != "assistant":
            out.append({"kind": "manual", "text": tr("Manuell in HA")})
        elif kind == "pattern":
            out.append({"kind": "way", "text": tr("Muster: {value}", value=way["value"])})
        elif kind == "area":
            out.append({"kind": "way", "text": tr("Bereich: {value}", value=way["value"])})
        elif kind == "device":
            out.append({"kind": "way", "text": tr("Gerät: {value}", value=way["value"])})
        elif kind == "label":
            out.append({"kind": "way", "text": tr("Label: {value}", value=way["value"])})
    if row.get("origin") == "assistant" and not any(b["kind"] == "assistant" for b in out):
        topic = topic_label(row.get("topic"))
        out.insert(0, {"kind": "assistant", "text": tr("Zeitarchiv · {topic}", topic=topic) if topic else tr("Zeitarchiv")})
    if row.get("origin") == "manual" and not out:
        out.append({"kind": "manual", "text": tr("Manuell in HA")})
    if row.get("excluded"):
        out.append({"kind": "excluded", "text": tr("Ausgeschlossen")})
    if row.get("drifted"):
        out.append({"kind": "drifted", "text": tr("Von Hand geändert")})
    return out


def explain(row: dict[str, Any], managed_label: str | None = None) -> list[str]:
    """„Warum ist das drin?“ — ein Satz je Weg, über den die Entität in der Auswahl ist."""
    lines: list[str] = []
    for way in row.get("via") or ():
        kind = way.get("type")
        if _own_way(row, way, managed_label):
            continue
        if kind == "entity":
            lines.append(tr("Als Einzel-ID in den Optionen der Zeitarchiv-Integration eingetragen."))
        elif kind == "assistant":
            if way.get("mode") == "ids":
                lines.append(tr("Vom Zeitarchiv-Assistenten als Einzel-ID hinzugefügt."))
            else:
                lines.append(tr("Vom Zeitarchiv-Assistenten über das Label hinzugefügt."))
        elif kind == "pattern":
            lines.append(tr("Passt auf das Muster „{value}“.", value=way["value"]))
        elif kind == "area":
            lines.append(tr("Liegt im ausgewählten Bereich „{value}“.", value=way["value"]))
        elif kind == "device":
            lines.append(tr("Gehört zum ausgewählten Gerät „{value}“.", value=way["value"]))
        elif kind == "label":
            level = way.get("level")
            if level == "device":
                lines.append(tr("Das Gerät trägt das Label „{value}“.", value=way["value"]))
            elif level == "area":
                lines.append(tr("Der Bereich trägt das Label „{value}“.", value=way["value"]))
            else:
                lines.append(tr("Trägt das Label „{value}“.", value=way["value"]))
    for way in row.get("excluded_by") or ():
        if way.get("type") == "pattern":
            lines.append(tr("Ausgeschlossen über das Muster „{value}“.", value=way.get("value")))
        else:
            lines.append(tr("Als Einzel-ID ausgeschlossen."))
    if row.get("drifted"):
        lines.append(tr("Der Assistent hatte sie hinzugefügt, der Weg wurde inzwischen in Home Assistant geändert."))
    if not lines:
        lines.append(tr("Keine Angabe der Integration."))
    return lines


def table(
    entities: list[dict[str, Any]], archived: set[str], *, filter_key: str = "alle", query: str = "", page: int = 1,
    managed_label: str | None = None,
) -> dict[str, Any]:
    """Gefilterte, durchsuchte und geblätterte Tabelle samt Zählern je Filterchip.
    ``managed_label``: Name des Labels, das der Assistent verwaltet (aus dem Katalog)."""
    rows = [row for row in entities if in_table(row)]
    counts = {key: sum(1 for row in rows if matches_filter(row, key, managed_label)) for key in FILTER_KEYS}
    if filter_key not in FILTER_KEYS:
        filter_key = "alle"
    needle = query.strip().lower()[:100]
    shown = [
        row for row in rows
        if matches_filter(row, filter_key, managed_label)
        and (not needle or needle in row["entity_id"] or needle in (row.get("name") or "").lower())
    ]
    shown.sort(key=lambda row: ((row.get("name") or row["entity_id"]).lower(), row["entity_id"]))
    pages = max(1, -(-len(shown) // PAGE_SIZE))
    page = min(max(1, page), pages)
    items = []
    for row in shown[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]:
        if row.get("excluded"):
            status = "excluded"
        elif row.get("drifted") and not row.get("selected"):
            status = "drifted"
        elif row["entity_id"] in archived:
            status = "archived"
        else:
            status = "waiting"
        items.append({
            "entity_id": row["entity_id"], "name": row.get("name") or row["entity_id"],
            "badges": badges(row, managed_label), "explain": explain(row, managed_label), "status": status,
            "added_ts": row.get("added_ts"), "removable": removable(row), "manual": manual_single_id(row),
            "assistant": row.get("origin") == "assistant" or bool(row.get("drifted")),
            "only_in_ha": not removable(row) and bool(row.get("selected")),
        })
    return {
        "rows": items, "counts": counts, "filter": filter_key, "query": query.strip()[:100],
        "page": page, "pages": pages, "total": len(shown),
    }


# ---- Verlauf -------------------------------------------------------------------------------

SOURCE_TEXT = {
    "assistant": N_("Zeitarchiv-Assistent"),
    "manual_ui": N_("In Zeitarchiv entfernt"),
    "manual_ha": N_("In HA geändert"),
    "undo": N_("Rückgängig gemacht"),
}
STATUS_TEXT = {
    "pending": N_("wartet auf die Integration"),
    "applied": N_("übernommen"),
    "partial": N_("teilweise übernommen"),
    "failed": N_("fehlgeschlagen"),
    "expired": N_("abgelaufen"),
}
RESULT_TEXT = {
    "ok": N_("übernommen"),
    "already": N_("war schon so"),
    "not_found": N_("nicht gefunden"),
    "excluded": N_("ausgeschlossen"),
    "blocked": N_("gesperrt"),
    "unsupported": N_("nur in HA änderbar"),
    "error": N_("Fehler"),
}


def _summary(entry: dict[str, Any]) -> str:
    count = entry.get("count") or len(entry.get("entity_ids") or ())
    if entry.get("action") == "change":
        detail = entry.get("ha_detail") or {}
        return detail.get("summary") or tr("Auswahl in den Optionen der Integration geändert")
    if entry.get("action") == "add":
        if entry.get("mode") == "ids":
            return tr("{count} hinzugefügt (Einzel-IDs)", count=count)
        return tr("{count} hinzugefügt (Label)", count=count)
    return tr("{count} entfernt", count=count)


def _undo_blocked(entry: dict[str, Any], undo_targets: set[str]) -> bool:
    return (
        entry.get("source") == "manual_ha"
        or entry.get("action") not in {"add", "remove"}
        or entry.get("status") not in {"applied", "partial"}
        or bool(entry.get("undone_by"))
        or entry["id"] in undo_targets
        or not undo_ids(entry)
    )


def undo_ids(entry: dict[str, Any]) -> list[str]:
    """IDs, die ein Rückgängig betrifft: nur die tatsächlich geänderten (Ergebnis ``ok``).
    Ohne Ergebnisse je ID gelten alle gespeicherten IDs."""
    results = entry.get("results") or {}
    ids = entry.get("entity_ids") or []
    if not results:
        return list(ids)
    return [eid for eid in ids if (results.get(eid) or {}).get("result") == "ok"]


def timeline(catalog: dict[str, Any] | None, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verlauf der Integration plus die Aufträge der App, die dort (noch) nicht stehen:
    offene und abgelaufene, und quittierte, solange der Katalog danach nicht neu kam."""
    log = list((catalog or {}).get("log") or [])
    received = float((catalog or {}).get("received_ts") or 0)
    log_ids = {entry["id"] for entry in log}
    undo_targets = {job["undo_of"] for job in jobs if job.get("undo_of") and job.get("status") != "expired"
                    and job.get("status") != "failed"}
    undo_targets |= {entry["undo_of"] for entry in log if entry.get("undo_of")}
    items: list[dict[str, Any]] = []
    for entry in log:
        items.append({**entry, "origin": "log", "undoable": not _undo_blocked(entry, undo_targets)})
    for job in jobs:
        if job["id"] in log_ids:
            continue
        status = job.get("status")
        if status in {"applied", "partial", "failed"} and float(job.get("finished_ts") or 0) <= received:
            continue  # steht inzwischen im Verlauf der Integration (unter eigener ID)
        entry = {
            "id": job["id"], "ts": job.get("created_ts") or 0, "source": job.get("source"),
            "action": job.get("action"), "mode": job.get("mode"), "topic": job.get("topic"),
            "entity_ids": list(job.get("entity_ids") or [])[:50], "count": len(job.get("entity_ids") or ()),
            "status": status, "results": job.get("results") or {}, "undo_of": job.get("undo_of"),
            "undone_by": None, "ha_detail": None, "message": job.get("message"),
            "expires_ts": job.get("expires_ts"),
        }
        entry["undoable"] = status in {"applied", "partial"} and not _undo_blocked(entry, undo_targets)
        items.append({**entry, "origin": "job"})
    items.sort(key=lambda item: item.get("ts") or 0, reverse=True)
    for item in items[:TIMELINE_LIMIT]:
        item["source_text"] = tr(str(SOURCE_TEXT.get(item.get("source"), item.get("source") or "")))
        item["status_text"] = tr(str(STATUS_TEXT.get(item.get("status"), item.get("status") or "")))
        item["summary"] = _summary(item)
        item["topic_text"] = topic_label(item.get("topic")) if item.get("topic") else ""
        item["details"] = [
            {"entity_id": eid, "result": tr(str(RESULT_TEXT[(item["results"].get(eid) or {})["result"]]))
             if (item["results"].get(eid) or {}).get("result") in RESULT_TEXT else ""}
            for eid in item.get("entity_ids") or []
        ]
    return items[:TIMELINE_LIMIT]


def find_undo_source(catalog: dict[str, Any] | None, jobs: list[dict[str, Any]], target: str) -> dict | None:
    """Der Eintrag (Verlauf oder Auftrag), den ``target`` meint, mit ``undoable``."""
    return next((item for item in timeline(catalog, jobs) if item["id"] == target), None)


def undo_plan(entry: dict[str, Any]) -> dict[str, Any]:
    """Gegenauftrag zu einem Eintrag: Hinzufügen wird Entfernen, Entfernen wird wieder
    Hinzufügen, und zwar im selben Weg wie vorher (Einzel-IDs bleiben Einzel-IDs)."""
    ids = undo_ids(entry)
    if entry.get("action") == "add":
        return {"action": "remove", "mode": "auto", "entity_ids": ids, "force_ids": [], "topic": entry.get("topic")}
    results = entry.get("results") or {}
    force = [eid for eid in ids if (results.get(eid) or {}).get("mode") == "ids"]
    mode = "label" if any((results.get(eid) or {}).get("mode") == "label" for eid in ids) else "ids"
    return {"action": "add", "mode": mode, "entity_ids": ids, "force_ids": force, "topic": entry.get("topic")}
