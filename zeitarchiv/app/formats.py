"""Zahlen-, Datums- und Uhrzeitformat der Oberfläche — getrennt von der Sprache.

Drei Formate: ``de-DE``, ``en-GB`` und ``en-US``. Einstellung ``regional_format``
(``auto`` oder eines der drei, Standard ``auto``). ``auto`` folgt dem ``Accept-Language``
des Browsers (``en-GB`` → britisch, ``en-US`` → amerikanisch, ``de-*`` → deutsch); ohne
passenden Eintrag richtet sich das Format nach der Oberflächensprache (Deutsch → ``de-DE``,
Englisch → ``en-GB``).

Der Server löst das Format einmal pro Anfrage auf und gibt es der Seite als
``<html data-format>`` mit. Die Skripte lesen es dort (``window.ZA_FORMAT``,
siehe ``static/js/i18n.js``) — Server und Browser formatieren dadurch immer gleich.
Maschinenlesbare Ausgaben (ISO-Daten, CSV, Dateinamen) sind davon nicht betroffen.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response

DEFAULT_FORMAT = "de-DE"
SETTING_KEY = "regional_format"
DEFAULT_SETTING = "auto"

# Je Format: Trenner und strftime-Muster. 24-Stunden-Uhr überall außer en-US (dort 12 h mit AM/PM,
# siehe format_clock()).
FORMATS: dict[str, dict[str, Any]] = {
    "de-DE": {"decimal": ",", "thousands": ".", "date": "%d.%m.%Y", "day_month": "%d.%m.", "hour12": False},
    "en-GB": {"decimal": ".", "thousands": ",", "date": "%d/%m/%Y", "day_month": "%d/%m", "hour12": False},
    "en-US": {"decimal": ".", "thousands": ",", "date": "%m/%d/%Y", "day_month": "%m/%d", "hour12": True},
}

# Auswahl in den Einstellungen (Beispiel im Label, damit der Unterschied sofort sichtbar ist).
FORMAT_CHOICES = {
    "auto": "Automatisch (Browser)",
    "de-DE": "Deutsch (1.234,5 · 07.10.2026)",
    "en-GB": "English UK (1,234.5 · 07/10/2026)",
    "en-US": "English US (1,234.5 · 10/07/2026)",
}

# Format der gerade bearbeiteten Anfrage (wie ``i18n.current_language``).
current_format: contextvars.ContextVar[str] = contextvars.ContextVar("zeitarchiv_format", default=DEFAULT_FORMAT)

# Sprachcodes ohne Region, die auf ein Format abgebildet werden.
_BARE_LANGUAGE_FORMAT = {"de": "de-DE", "en": "en-GB"}
# Regionen, die dem amerikanischen Format folgen (Datum Monat/Tag).
_US_REGIONS = {"US", "CA", "PH"}


def from_accept_language(header: str, language: str) -> str:
    """Format aus dem ``Accept-Language``-Header; ohne passenden Eintrag aus der Oberflächensprache."""
    for part in header.split(","):
        tag = part.split(";")[0].strip()
        if not tag:
            continue
        code, _, region = tag.partition("-")
        code, region = code.lower(), region.upper()
        if f"{code}-{region}" in FORMATS:
            return f"{code}-{region}"
        if code == "en" and region:
            return "en-US" if region in _US_REGIONS else "en-GB"
        if code in _BARE_LANGUAGE_FORMAT:
            return _BARE_LANGUAGE_FORMAT[code]
    return _BARE_LANGUAGE_FORMAT.get(language, DEFAULT_FORMAT)


def resolve_format(setting: str | None, accept_language: str = "", language: str = "de") -> str:
    if setting in FORMATS:
        return setting
    return from_accept_language(accept_language, language)


def selected_setting(raw: str | None) -> str:
    return raw if raw in FORMAT_CHOICES else DEFAULT_SETTING


def number_separators(locale: str | None = None) -> dict[str, Any]:
    return FORMATS[locale or current_format.get()]


# --- Datum und Uhrzeit -------------------------------------------------------------------------


def format_date(moment: datetime) -> str:
    """Kalendertag, z. B. ``07.10.2026`` / ``07/10/2026`` / ``10/07/2026``."""
    return moment.strftime(FORMATS[current_format.get()]["date"])


def format_day_month(moment: datetime) -> str:
    """Tag und Monat ohne Jahr, z. B. ``07.10.`` / ``07/10`` / ``10/07``."""
    return moment.strftime(FORMATS[current_format.get()]["day_month"])


def day_month_label(day: int, month_abbr: str) -> str:
    """Tag mit Monatskürzel für Kompaktlabels: ``7. Okt`` / ``7 Oct`` / ``Oct 7``."""
    locale = current_format.get()
    if locale == "de-DE":
        return f"{day}. {month_abbr}"
    return f"{month_abbr} {day}" if locale == "en-US" else f"{day} {month_abbr}"


def format_clock(moment: datetime, seconds: bool = False) -> str:
    """Uhrzeit: ``08:41`` (24 h) bzw. ``8:41 AM`` (en-US)."""
    spec = FORMATS[current_format.get()]
    clock = moment.strftime("%H:%M:%S" if seconds else "%H:%M")
    if not spec["hour12"]:
        return clock
    hour = moment.hour % 12 or 12
    rest = moment.strftime(":%M:%S" if seconds else ":%M")
    return f"{hour}{rest} {'AM' if moment.hour < 12 else 'PM'}"


def format_datetime(moment: datetime, seconds: bool = True, comma: bool = False) -> str:
    """Datum und Uhrzeit, z. B. ``07.10.2026 08:41:07`` (``comma`` → ``07.10.2026, 08:41``)."""
    return f"{format_date(moment)}{',' if comma else ''} {format_clock(moment, seconds)}"


# --- Anbindung an die Anfrage ------------------------------------------------------------------


def _format_for(request: Request, index, language: str) -> tuple[str, str]:
    """(Auswahl, Format) dieser Anfrage aus Einstellung, Browser-Header und Sprache."""
    setting = selected_setting(index.get_setting(SETTING_KEY, DEFAULT_SETTING))
    return setting, resolve_format(setting, request.headers.get("accept-language", ""), language)


def context_values(request: Request, index, language: str, translate: Callable[[str, str], str]) -> dict:
    """Template-Variablen: ``format_locale`` (für ``<html data-format>``) und die Auswahl der Einstellungen."""
    setting, locale = _format_for(request, index, language)
    current_format.set(locale)
    return {
        "format_locale": locale,
        "format_setting": setting,
        "format_options": [(key, translate(label, language)) for key, label in FORMAT_CHOICES.items()],
    }


def set_for_request(request: Request, index, language: str) -> None:
    current_format.set(_format_for(request, index, language)[1])


def make_router(get_index: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post("/settings/regional-format")
    def set_regional_format(regional_format: str = Form(...)) -> Response:
        """Speichert das Format und lädt die Seite neu (alle Zahlen und Daten ändern sich)."""
        if regional_format not in FORMAT_CHOICES:
            from .i18n import tr  # erst hier: i18n importiert dieses Modul

            raise HTTPException(status_code=400, detail=tr("Ungültiges Format"))
        get_index().set_setting(SETTING_KEY, regional_format)
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    return router
