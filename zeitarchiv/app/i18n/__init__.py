"""Oberflächensprache: Deutsch ist der Quelltext, weitere Sprachen sind Kataloge.

Der deutsche Text selbst ist der Schlüssel (``_("Übersicht")``), wie bei gettext.
Das hält die Templates lesbar und macht die Umstellung schrittweise möglich: was
im Katalog fehlt, erscheint unverändert auf Deutsch — die App bleibt in jeder
Zwischenstufe benutzbar, und ein Test findet die Lücken.

Katalog ``<sprache>.json`` hat zwei Abschnitte:

- ``"app"``: Texte der Templates und (später) des Python-Codes.
- ``"js"``: Texte der Skripte. Sie werden nur für Nicht-Deutsch inline in die Seite
  geschrieben (``window.ZA_CATALOG``, siehe ``static/js/i18n.js``).

Sprachwahl: Einstellung ``language`` (``auto``/``de``/``en``, Standard ``auto``).
``auto`` folgt dem ``Accept-Language`` des Browsers; ohne passende Sprache bleibt es Deutsch.
"""

from __future__ import annotations

import contextvars
import json
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from jinja2 import pass_context
from markupsafe import Markup

from .. import currency, formats

SOURCE_LANGUAGE = "de"
LANGUAGES = {"de": "Deutsch", "en": "English"}
# Reihenfolge der Auswahl; "auto" ist keine Sprache, sondern die Regel.
LANGUAGE_CHOICES = {"auto": "Automatisch (Browser)", **LANGUAGES}
SETTING_KEY = "language"
DEFAULT_SETTING = "auto"

_CATALOG_DIR = Path(__file__).parent

# Sprache der gerade bearbeiteten Anfrage. Der Kontextprozessor setzt sie beim Rendern; Makros,
# die per `{% from … import … %}` OHNE Kontext geladen werden (z. B. _hints.html), sehen `lang`
# nicht im Seitenkontext und lesen sie deshalb hier. Ohne Anfrage (Hintergrundaufgaben, Scheduler)
# ist sie nicht gesetzt; active_language() fragt dann die Hintergrundsprache (siehe unten).
current_language: contextvars.ContextVar[str | None] = contextvars.ContextVar("zeitarchiv_language", default=None)

LAST_SEEN_KEY = "language_last_seen"
_background_provider: Callable[[], str] | None = None
_last_seen: str | None = None


def active_language() -> str:
    """Sprache der laufenden Anfrage; ohne Anfrage die Hintergrundsprache, sonst Deutsch."""
    language = current_language.get()
    if language:
        return language
    if _background_provider is not None:
        try:
            return _background_provider()
        except Exception:  # eine Anzeigesprache darf nie einen Hintergrundlauf abbrechen
            pass
    return SOURCE_LANGUAGE


def set_background_language(get_index: Callable[[], Any]) -> None:
    """Legt fest, in welcher Sprache Hintergrundaufgaben Texte schreiben (Fehlermeldungen von
    Backup-/Retention-Läufen u. Ä.): bei fester Spracheinstellung diese; bei „Automatisch“ die
    Sprache, die ein Browser zuletzt angefragt hat (es gibt dort keinen ``Accept-Language``),
    ohne bisherige Anfrage Deutsch."""

    def provider() -> str:
        index = get_index()
        setting = _selected_setting(index.get_setting(SETTING_KEY, DEFAULT_SETTING))
        if setting in LANGUAGES:
            return setting
        remembered = _last_seen or index.get_setting(LAST_SEEN_KEY, "")
        return remembered if remembered in LANGUAGES else SOURCE_LANGUAGE

    global _background_provider
    _background_provider = provider


def _remember_language(index, setting: str, accept_language: str) -> None:
    """Merkt sich bei „Automatisch“ die vom Browser gewünschte Sprache für Hintergrundaufgaben.

    Nur eine tatsächlich passende Browsersprache zählt — ein Skript ohne Header würde sonst die
    Sprache überschreiben, die jemand im Browser gewählt hat. Geschrieben wird nur bei Änderung."""
    global _last_seen
    if setting != "auto":
        return
    language = matched_language(accept_language)
    if language and language != _last_seen:
        _last_seen = language
        if index.get_setting(LAST_SEEN_KEY, "") != language:
            index.set_setting(LAST_SEEN_KEY, language)


@lru_cache(maxsize=None)
def load_catalog(lang: str) -> dict[str, dict[str, str]]:
    """Katalog einer Sprache; für die Quellsprache und unbekannte Sprachen leer."""
    if lang == SOURCE_LANGUAGE or lang not in LANGUAGES:
        return {"app": {}, "js": {}}
    data = json.loads((_CATALOG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return {"app": data.get("app", {}), "js": data.get("js", {})}


def translate(text: str, lang: str, **values: Any) -> str:
    """Übersetzt ``text``; ohne Eintrag bleibt er deutsch. ``values`` füllen ``{name}``."""
    result = load_catalog(lang)["app"].get(text, text)
    return result.format(**_translated_values(values, lang)) if values else result


def _translated_values(values: dict[str, Any], lang: str) -> dict[str, Any]:
    """Eingesetzte ``Lazy``-Texte (Beschriftungen aus ``N_``) in der Zielsprache einsetzen."""
    return {key: (translate(str(v), lang) if isinstance(v, Lazy) else v) for key, v in values.items()}


def matched_language(header: str) -> str | None:
    """Erste unterstützte Sprache des ``Accept-Language``-Headers, ohne Treffer ``None``."""
    for part in header.split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in LANGUAGES:
            return code
    return None


def from_accept_language(header: str) -> str:
    """Erste unterstützte Sprache des ``Accept-Language``-Headers, sonst Deutsch."""
    return matched_language(header) or SOURCE_LANGUAGE


def resolve_language(setting: str | None, accept_language: str = "") -> str:
    if setting == "auto":
        return from_accept_language(accept_language)
    return setting if setting in LANGUAGES else SOURCE_LANGUAGE


def _selected_setting(raw: str | None) -> str:
    return raw if raw in LANGUAGE_CHOICES else DEFAULT_SETTING


def translate_html(text: str, lang: str, **values: Any) -> Markup:
    """Wie ``translate``, für Templates: der Schlüssel ist HTML-Quelltext (``&hellip;``,
    ``<strong>`` …) und das Ergebnis ein ``Markup`` — Kataloge sind vertrauenswürdig.
    Eingesetzte Werte werden dagegen maskiert, außer sie sind selbst ``Markup``."""
    result = Markup(load_catalog(lang)["app"].get(text, text))
    return result.format(**_translated_values(values, lang)) if values else result


@pass_context
def _gettext(context, text: str, **values: Any) -> Markup:
    """Jinja-Funktion ``_()``: Sprache aus dem Seitenkontext (siehe ``make_context_processor``)."""
    return translate_html(text, context.get("lang") or active_language(), **values)


def _language_for(request: Request, index) -> tuple[str, str]:
    """(Auswahl, Sprache) dieser Anfrage aus Einstellung und Browser-Header."""
    setting = _selected_setting(index.get_setting(SETTING_KEY, DEFAULT_SETTING))
    return setting, resolve_language(setting, request.headers.get("accept-language", ""))


def tr(text: str, **values: Any) -> str:
    """Übersetzt Python-Text für die Sprache der laufenden Anfrage (Klartext, kein HTML)."""
    return translate(text, active_language(), **values)


@lru_cache(maxsize=1)
def _stored_text_index() -> tuple[dict[str, str], list[tuple[re.Pattern[str], str]]]:
    """Rückwärts-Index für ``retranslate``: fertige Texte (deutscher Schlüssel oder Übersetzung jeder
    Sprache) → deutscher Schlüssel, bei Platzhaltern als Muster, das die Werte wieder herausholt."""
    exact: dict[str, str] = {}
    patterns: list[tuple[int, re.Pattern[str], str]] = []
    for lang in LANGUAGES:
        for key, value in load_catalog(lang)["app"].items():
            for text in {key, value}:
                if "{" not in text:
                    exact.setdefault(text, key)
                    continue
                parts = re.split(r"\{(\w+)\}", text)
                seen: set[str] = set()
                regex = ""
                for position, part in enumerate(parts):
                    if position % 2 == 0:
                        regex += re.escape(part)
                    elif part in seen:
                        regex += f"(?P={part})"
                    else:
                        seen.add(part)
                        regex += f"(?P<{part}>.+?)"
                literal = sum(len(part) for part in parts[::2])
                if literal >= 3:  # reine Platzhalter-Muster ("{a}: {b}") würden jeden Text verschlucken
                    patterns.append((literal, re.compile(regex, re.S), key))
    # Genaueste zuerst: ein Text mit viel festem Wortlaut gehört zum spezifischeren Eintrag.
    patterns.sort(key=lambda item: -item[0])
    return exact, [(pattern, key) for _, pattern, key in patterns]


@lru_cache(maxsize=2048)
def _retranslate(text: str, lang: str) -> str:
    exact, patterns = _stored_text_index()
    key = exact.get(text)
    if key is not None:
        return translate(key, lang)
    for pattern, key in patterns:
        match = pattern.fullmatch(text)
        if match:
            return translate(key, lang, **match.groupdict())
    return text


def retranslate(text: str | None) -> str | None:
    """Gespeicherten Freitext in der Sprache der Anfrage anzeigen.

    Fehlermeldungen von Läufen, Import-Reports und Stummschaltungen liegen als fertiger Text vor, in der
    Sprache, die beim Schreiben galt. Hier wird ein Text, der einem Katalogeintrag entspricht (deutsch
    oder übersetzt, ggf. mit Platzhalterwerten), über den deutschen Schlüssel in die Anzeigesprache
    gebracht — ohne das gespeicherte Format zu ändern und auch für ältere Einträge. Alles andere
    (rohe Ausnahmetexte, eigene Namen) bleibt unverändert."""
    if not text:
        return text
    return _retranslate(text, active_language())


class Lazy(str):
    """Ein deutscher Text, der erst beim Anzeigen übersetzt wird.

    Ist ein ``str`` mit dem deutschen Wortlaut (Vergleiche, ``in``, Dict-Zugriffe und JSON-Ausgabe
    sehen den deutschen Text), erscheint im Template aber in der Sprache der Anfrage: Jinja ruft
    bei ``{{ wert }}`` ``__html__`` auf."""

    __slots__ = ()

    def __html__(self) -> Markup:
        return translate_html(str(self), active_language())


def N_(text: str) -> Lazy:
    """Markiert einen Text als übersetzbar, ohne ihn sofort zu übersetzen (gettext-„noop“).

    Für Konstanten, die beim Import angelegt werden (Beschriftungslisten): die Sprache der Anfrage
    gibt es dann noch nicht. Im Template genügt ``{{ label }}`` — ``Lazy`` übersetzt beim Anzeigen;
    in Python-Code ``tr(label)``."""
    return Lazy(text)


def dependencies(get_index: Callable[[], Any]) -> list:
    """App-weite Abhängigkeit: setzt die Sprache für jede Anfrage, BEVOR die Route läuft.

    Eine ``async``-Abhängigkeit, damit der ContextVar-Wert in den Kontext übergeht, den FastAPI
    für synchrone Routen an den Threadpool weitergibt."""
    async def set_language(request: Request) -> None:
        index = get_index()
        setting, language = _language_for(request, index)
        current_language.set(language)
        _remember_language(index, setting, request.headers.get("accept-language", ""))
        formats.set_for_request(request, index, language)
        currency.set_for_request(index)

    return [Depends(set_language)]


def make_context_processor(get_index: Callable[[], Any]) -> Callable[[Request], dict]:
    """Kontextprozessor: ``lang``, die Auswahl für die Einstellungen und der JS-Katalog.

    ``get_index`` statt des Index selbst: die Templates werden in main.py angelegt, bevor der
    Index existiert."""

    def context(request: Request) -> dict:
        index = get_index()
        setting, lang = _language_for(request, index)
        current_language.set(lang)
        return {
            **formats.context_values(request, index, lang, translate),
            **currency.context_values(index, lang, translate),
            "lang": lang,
            "language_setting": setting,
            "language_options": [(key, translate(label, lang)) for key, label in LANGUAGE_CHOICES.items()],
            "js_catalog": load_catalog(lang)["js"],
        }

    return context


def make_router(get_index: Callable[[], Any]) -> APIRouter:
    router = APIRouter()
    router.include_router(formats.make_router(get_index))
    router.include_router(currency.make_router(get_index))

    @router.post("/settings/language")
    def set_language(language: str = Form(...)) -> Response:
        """Speichert die Sprache und lädt die Seite neu (alle Texte ändern sich)."""
        if language not in LANGUAGE_CHOICES:
            raise HTTPException(status_code=400, detail=tr("Ungültige Sprache"))
        get_index().set_setting(SETTING_KEY, language)
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    return router


def install(templates) -> None:
    """Macht ``_()`` in allen Templates verfügbar."""
    templates.env.globals["_"] = _gettext
