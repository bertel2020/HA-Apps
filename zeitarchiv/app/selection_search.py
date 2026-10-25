"""Freitext für den Auswahl-Assistenten (ROADMAP 1.33, Phase 2).

„Was willst du archivieren?“ — zuerst eine kleine Synonymliste (deutsch und
englisch, klein geschrieben): passt jeder Begriff der Eingabe auf dasselbe Thema
(„strom“, „solar pv“, „heizung“), zeigt die Seite dieses Thema. Sonst eine
Volltextsuche über Name, ID, Gerät, Bereich und Plattform, bei der alle Begriffe
passen müssen. Die Treffer erscheinen als Pseudo-Thema „Suche“ in derselben
Listenform wie ein Thema, aber ohne Vorauswahl.

Reine Funktionen über dem Katalog, wie ``selection_suggest``.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from typing import Any

from .i18n import tr
from .selection_suggest import result, suggestion
from .selection_topics import rule_anwesenheit

#: Name des Pseudo-Themas für die Volltextsuche (kein Eintrag in ``TOPICS``).
SEARCH_TOPIC = "suche"
#: Mehr Treffer zeigt die Seite nicht; der Rest wird als Zahl genannt.
MAX_RESULTS = 300
MAX_QUERY = 100

#: Begriff → Thema. Nur ganze Begriffe zählen, keine Teilwörter.
SYNONYMS: dict[str, str] = {
    **dict.fromkeys((
        "energie", "energy", "strom", "stromverbrauch", "solar", "pv", "photovoltaik", "akku", "batterie",
        "battery", "verbrauch", "electricity", "power", "leistung", "kwh",
    ), "energie"),
    **dict.fromkeys((
        "temperatur", "temperature", "heizung", "heating", "klima", "climate", "thermostat", "luftfeuchte",
        "luftfeuchtigkeit", "humidity", "raumklima",
    ), "klima"),
    **dict.fromkeys(("wetter", "weather", "regen", "rain", "wind", "niederschlag"), "wetter"),
    **dict.fromkeys(("wasser", "water", "gas", "zähler", "zaehler", "meter"), "wasser_gas"),
    **dict.fromkeys(("router", "netzwerk", "network", "wlan", "wifi", "ping", "internet"), "netzwerk"),
    **dict.fromkeys(("anwesenheit", "zuhause", "person", "presence", "präsenz", "praesenz"), "anwesenheit"),
}

_SPLIT = re.compile(r"[\s,;&+/]+")


def clean_query(query: str | None) -> str:
    """Eingabe gekürzt, ohne doppelte Leerzeichen (so steht sie auch wieder im Feld)."""
    return " ".join((query or "").split())[:MAX_QUERY]


def terms(query: str | None) -> list[str]:
    return [term for term in _SPLIT.split(clean_query(query).lower()) if term]


def understood_topic(query: str | None) -> str | None:
    """Das Thema, wenn jeder Begriff ein Synonym desselben Themas ist, sonst None."""
    found = {SYNONYMS.get(term) for term in terms(query)}
    if len(found) == 1 and None not in found:
        return found.pop()
    return None


def _haystack(row: dict[str, Any]) -> str:
    parts = (row.get("name"), row["entity_id"], row.get("device"), row.get("area"), row.get("platform"))
    return " ".join(str(part) for part in parts if part).lower()


def search(query: str, entities: Iterable[dict[str, Any]], archived: Collection[str] = ()) -> dict[str, Any]:
    """Volltextsuche als Pseudo-Thema (Form wie ``suggest``): alle Treffer unter „more“,
    nichts vorangehakt. Zusätzlich ``truncated`` (nicht gezeigte Treffer) und ``presence``
    (Anwesenheits-Entitäten unter den Treffern, für einen Hinweis)."""
    needles = terms(query)
    hits = [
        row for row in entities
        if needles and not row.get("hidden") and row.get("entity_category") != "config"
        and all(needle in _haystack(row) for needle in needles)
    ]
    hits.sort(key=lambda row: ((row.get("name") or row["entity_id"]).lower(), row["entity_id"]))
    reason = tr("Treffer in Name/Gerät/Bereich/Plattform")
    rows = [suggestion(row, reason, archived) for row in hits[:MAX_RESULTS]]
    out = result(SEARCH_TOPIC, [], rows, 0)
    out["truncated"] = max(0, len(hits) - MAX_RESULTS)
    out["presence"] = sum(1 for row in hits if rule_anwesenheit(row) is not None)
    return out
