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
SETTING_TOOLS_DISABLED = "ai_tools_disabled"
SETTING_INSTRUCTIONS = "ai_instructions"

#: Wer darf gesehen werden: alles, alles außer der Liste, nur die Liste.
SCOPES = ("all", "exclude", "include")
#: Obergrenze der Liste. Sie landet als JSON in einer Einstellung und wird bei
#: jeder KI-Anfrage gelesen; mehr als das ist kein Auswahlfilter mehr.
MAX_LIST_ENTITIES = 2000

#: Länge der Hinweise für das Modell. Sie gehen in den API-Prompt und an die
#: Antwort der Suche und kosten dort jedes Mal Kontext.
MAX_INSTRUCTIONS_LENGTH = 1000

_API_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Steuerzeichen außer Zeilenumbruch und Tab: Der Text ist die eigene Eingabe des
# Besitzers, soll aber keine unsichtbaren Zeichen in den Modellkontext tragen.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


@dataclass(frozen=True)
class AiAccess:
    enabled: bool = False
    scope: str = "all"
    entities: frozenset[str] = frozenset()
    exclude_presence: bool = True
    #: Werkzeuge, die der Besitzer abgeschaltet hat (interne Namen ohne Präfix,
    #: z. B. "get_energy_overview"). Sie stehen nicht in der Liste der Integration.
    tools_disabled: frozenset[str] = frozenset()
    #: Freitext des Besitzers für das Modell, z. B. Strompreis, Geräte, Besonderheiten.
    instructions: str = ""

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
        tools_disabled=frozenset(_json_list(index.get_setting(SETTING_TOOLS_DISABLED, "[]"))),
        instructions=index.get_setting(SETTING_INSTRUCTIONS, "") or "",
    )


def _json_list(raw: str | None) -> list[str]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in items if isinstance(item, str)] if isinstance(items, list) else []


def clean_instructions(text: str | None) -> str:
    """Hinweise für das Modell: Steuerzeichen raus, Zeilenenden vereinheitlicht, gekürzt."""
    cleaned = _CONTROL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    return cleaned[:MAX_INSTRUCTIONS_LENGTH]


def config_rev(index: Index) -> int:
    try:
        return int(index.get_setting(SETTING_CONFIG_REV, "0") or 0)
    except ValueError:
        return 0


def save(
    index: Index, *, enabled: bool, scope: str, entities: list[str], exclude_presence: bool,
    tools_disabled: list[str] | None = None, instructions: str | None = None,
) -> AiAccess:
    """Speichert die Einstellungen und zählt ``ai_config_rev`` hoch."""
    if scope not in SCOPES:
        raise ValueError("scope")
    unique = list(dict.fromkeys(item for item in entities if item))[:MAX_LIST_ENTITIES]
    index.set_setting(SETTING_ENABLED, "1" if enabled else "0")
    index.set_setting(SETTING_SCOPE, scope)
    index.set_setting(SETTING_ENTITY_LIST, json.dumps(unique))
    index.set_setting(SETTING_EXCLUDE_PRESENCE, "1" if exclude_presence else "0")
    # None heißt "unverändert": Aufrufer, die nur den Schalter oder die Auswahl
    # setzen, überschreiben die V1.1-Einstellungen nicht.
    if tools_disabled is not None:
        index.set_setting(SETTING_TOOLS_DISABLED, json.dumps(sorted(set(tools_disabled))))
    if instructions is not None:
        index.set_setting(SETTING_INSTRUCTIONS, clean_instructions(instructions))
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
