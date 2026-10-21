"""Änderungsprotokoll des KI-Zugriffs: wer wann was freigegeben hat, soweit die App es weiß.

Die letzten ``MAX_ENTRIES`` Änderungen stehen als JSON in den Einstellungen und
überleben so Neustarts, anders als das Protokoll der App. Gespeichert wird nur
der Unterschied zum vorherigen Stand, ohne Notiztext und ohne Entitätsnamen. Wer
die Änderung vorgenommen hat, kennt die App nicht (ein Besucher der Seite ist für
sie nicht identifizierbar).

Ein Eintrag besteht aus kurzen Bausteinen (``kind`` plus Wert), nicht aus
fertigem Text: Sie werden erst bei der Anzeige übersetzt.
"""

from __future__ import annotations

import json
import time

from .ai_settings import AiAccess
from .storage.index import Index

SETTING_CHANGE_LOG = "ai_change_log"
MAX_ENTRIES = 50


def describe(before: AiAccess, after: AiAccess) -> list[dict]:
    """Die Unterschiede zwischen zwei Ständen als Bausteine; leer, wenn nichts anders ist."""
    parts: list[dict] = []
    if before.enabled != after.enabled:
        parts.append({"kind": "enabled", "value": after.enabled})
    if before.scope != after.scope:
        parts.append({"kind": "scope", "value": after.scope, "count": len(after.entities)})
    elif after.scope != "all" and before.entities != after.entities:
        parts.append({"kind": "entities", "count": len(after.entities)})
    if before.exclude_presence != after.exclude_presence:
        parts.append({"kind": "presence", "value": after.exclude_presence})
    for name in sorted(after.tools_disabled - before.tools_disabled):
        parts.append({"kind": "tool", "value": name, "on": False})
    for name in sorted(before.tools_disabled - after.tools_disabled):
        parts.append({"kind": "tool", "value": name, "on": True})
    if before.instructions != after.instructions:
        parts.append({"kind": "notes"})
    return parts


def load(index: Index) -> list[dict]:
    """Die gespeicherten Einträge, neueste zuerst."""
    try:
        items = json.loads(index.get_setting(SETTING_CHANGE_LOG, "[]") or "[]")
    except json.JSONDecodeError:
        return []
    entries = [
        item for item in items if isinstance(item, dict)
        and isinstance(item.get("ts"), (int, float)) and isinstance(item.get("parts"), list)
    ] if isinstance(items, list) else []
    return entries[::-1]


def record(index: Index, before: AiAccess, after: AiAccess, now: float | None = None) -> bool:
    """Hängt die Änderung an. Ohne Unterschied passiert nichts (Autosave speichert oft denselben Stand)."""
    parts = describe(before, after)
    if not parts:
        return False
    stored = load(index)[::-1]
    stored.append({"ts": time.time() if now is None else now, "parts": parts})
    index.set_setting(SETTING_CHANGE_LOG, json.dumps(stored[-MAX_ENTRIES:], separators=(",", ":")))
    return True
