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
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from jinja2 import pass_context
from markupsafe import Markup

from .. import formats

SOURCE_LANGUAGE = "de"
LANGUAGES = {"de": "Deutsch", "en": "English"}
# Reihenfolge der Auswahl; "auto" ist keine Sprache, sondern die Regel.
LANGUAGE_CHOICES = {"auto": "Automatisch (Browser)", **LANGUAGES}
SETTING_KEY = "language"
DEFAULT_SETTING = "auto"

_CATALOG_DIR = Path(__file__).parent

# Sprache der gerade bearbeiteten Anfrage. Der Kontextprozessor setzt sie beim Rendern; Makros,
# die per `{% from … import … %}` OHNE Kontext geladen werden (z. B. _hints.html), sehen `lang`
# nicht im Seitenkontext und lesen sie deshalb hier.
current_language: contextvars.ContextVar[str] = contextvars.ContextVar("zeitarchiv_language", default=SOURCE_LANGUAGE)


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


def from_accept_language(header: str) -> str:
    """Erste unterstützte Sprache des ``Accept-Language``-Headers, sonst Deutsch."""
    for part in header.split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in LANGUAGES:
            return code
    return SOURCE_LANGUAGE


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
    return translate_html(text, context.get("lang") or current_language.get(), **values)


def _language_for(request: Request, index) -> tuple[str, str]:
    """(Auswahl, Sprache) dieser Anfrage aus Einstellung und Browser-Header."""
    setting = _selected_setting(index.get_setting(SETTING_KEY, DEFAULT_SETTING))
    return setting, resolve_language(setting, request.headers.get("accept-language", ""))


def tr(text: str, **values: Any) -> str:
    """Übersetzt Python-Text für die Sprache der laufenden Anfrage (Klartext, kein HTML)."""
    return translate(text, current_language.get(), **values)


class Lazy(str):
    """Ein deutscher Text, der erst beim Anzeigen übersetzt wird.

    Ist ein ``str`` mit dem deutschen Wortlaut (Vergleiche, ``in``, Dict-Zugriffe und JSON-Ausgabe
    sehen den deutschen Text), erscheint im Template aber in der Sprache der Anfrage: Jinja ruft
    bei ``{{ wert }}`` ``__html__`` auf."""

    __slots__ = ()

    def __html__(self) -> Markup:
        return translate_html(str(self), current_language.get())


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
        language = _language_for(request, index)[1]
        current_language.set(language)
        formats.set_for_request(request, index, language)

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
            "lang": lang,
            "language_setting": setting,
            "language_options": [(key, translate(label, lang)) for key, label in LANGUAGE_CHOICES.items()],
            "js_catalog": load_catalog(lang)["js"],
        }

    return context


def make_router(get_index: Callable[[], Any]) -> APIRouter:
    router = APIRouter()
    router.include_router(formats.make_router(get_index))

    @router.post("/settings/language")
    def set_language(language: str = Form(...)) -> Response:
        """Speichert die Sprache und lädt die Seite neu (alle Texte ändern sich)."""
        if language not in LANGUAGE_CHOICES:
            raise HTTPException(status_code=400, detail="Invalid language")
        get_index().set_setting(SETTING_KEY, language)
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    return router


def install(templates) -> None:
    """Macht ``_()`` in allen Templates verfügbar."""
    templates.env.globals["_"] = _gettext
