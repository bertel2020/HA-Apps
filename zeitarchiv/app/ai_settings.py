"""Einstellungen des KI-Zugriffs (ROADMAP 1.31, "App-Seite: Schalter und Einstellungen").

Alles liegt in der generischen settings-Tabelle (Index.get_setting/set_setting),
wie bei den übrigen Schaltern. Der Zugriff ist standardmäßig AUS (Opt-in): Über
die Freigabe der Daten entscheidet die Seite, der sie gehören, nicht die
Integration.

``ai_config_rev`` zählt jede Änderung hoch. Die Integration liest den Wert aus
/api/notices; ändert er sich, holt sie /api/ai/config neu und meldet ihre
LLM-API an, ab oder um — ein Umschalten wirkt so ohne Neustart von HA.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .storage.index import PRESENCE_DOMAINS, Index

SETTING_ENABLED = "ai_enabled"
SETTING_SCOPE = "ai_entity_scope"
SETTING_ENTITY_LIST = "ai_entity_list"
SETTING_EXCLUDE_PRESENCE = "ai_exclude_presence"
SETTING_CONFIG_REV = "ai_config_rev"
SETTING_API_IDS = "ai_api_ids"

#: Wer darf gesehen werden: alles, alles außer der Liste, nur die Liste.
SCOPES = ("all", "exclude", "include")
#: Obergrenze der Liste. Sie landet als JSON in einer Einstellung und wird bei
#: jeder KI-Anfrage gelesen; mehr als das ist kein Auswahlfilter mehr.
MAX_LIST_ENTITIES = 2000

_API_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@dataclass(frozen=True)
class AiAccess:
    enabled: bool = False
    scope: str = "all"
    entities: frozenset[str] = frozenset()
    exclude_presence: bool = True

    def allows(self, entity_id: str) -> bool:
        """Ob die KI die Entität sehen darf. Gilt in jedem Werkzeug, auch in
        der Suche: Eine gesperrte Entität ist für das Modell nicht vorhanden."""
        if self.exclude_presence and entity_id.split(".", 1)[0] in PRESENCE_DOMAINS:
            return False
        if self.scope == "exclude":
            return entity_id not in self.entities
        if self.scope == "include":
            return entity_id in self.entities
        return True


def load(index: Index) -> AiAccess:
    scope = index.get_setting(SETTING_SCOPE, "all")
    try:
        listed = json.loads(index.get_setting(SETTING_ENTITY_LIST, "[]") or "[]")
    except json.JSONDecodeError:
        listed = []
    return AiAccess(
        enabled=index.get_setting(SETTING_ENABLED, "0") == "1",
        scope=scope if scope in SCOPES else "all",
        entities=frozenset(item for item in listed if isinstance(item, str)),
        # Standard an: Wer Anwesenheit freigeben will, entscheidet das bewusst.
        exclude_presence=index.get_setting(SETTING_EXCLUDE_PRESENCE, "1") != "0",
    )


def config_rev(index: Index) -> int:
    try:
        return int(index.get_setting(SETTING_CONFIG_REV, "0") or 0)
    except ValueError:
        return 0


def save(
    index: Index, *, enabled: bool, scope: str, entities: list[str], exclude_presence: bool,
) -> AiAccess:
    """Speichert die Einstellungen und zählt ``ai_config_rev`` hoch."""
    if scope not in SCOPES:
        raise ValueError("scope")
    unique = list(dict.fromkeys(item for item in entities if item))[:MAX_LIST_ENTITIES]
    index.set_setting(SETTING_ENABLED, "1" if enabled else "0")
    index.set_setting(SETTING_SCOPE, scope)
    index.set_setting(SETTING_ENTITY_LIST, json.dumps(unique))
    index.set_setting(SETTING_EXCLUDE_PRESENCE, "1" if exclude_presence else "0")
    index.set_setting(SETTING_CONFIG_REV, str(config_rev(index) + 1))
    return load(index)


def remember_api_id(index: Index, api_id: str | None) -> None:
    """Die Integration nennt beim Abruf von /api/ai/config die ID ihrer LLM-API.
    Die App kennt die entry_id nicht, braucht die ID aber für die fertige URL
    /api/mcp/<id> auf der Einstellungsseite. Mehrere Verbindungen melden je
    ihre eigene."""
    if not api_id or not _API_ID.match(api_id):
        return
    ids = known_api_ids(index)
    if api_id in ids:
        return
    index.set_setting(SETTING_API_IDS, json.dumps([*ids, api_id][-10:]))


def known_api_ids(index: Index) -> list[str]:
    try:
        ids = json.loads(index.get_setting(SETTING_API_IDS, "[]") or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in ids if isinstance(item, str)]
