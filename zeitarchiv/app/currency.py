"""Währung der Oberfläche — getrennt von Sprache und Zahlenformat.

Vier Währungen: EUR, GBP, USD, CHF. Einstellung ``currency`` (``auto`` oder eine davon,
Standard ``auto``). ``auto`` übernimmt die Währung aus der Home-Assistant-Konfiguration
(``/core/api/config``); ohne Supervisor, ohne Antwort oder bei einer nicht unterstützten
Währung bleibt es bei EUR.

Eine Umstellung rechnet nichts um: Preise liegen ohne Währung als „Einheit je kWh“ vor.
Die Einstellung ändert nur Symbol, Stellung und die Bezeichnung der Untereinheit
(Cent, Pence, Rappen) — wer die Währung wechselt, trägt seine Preise neu ein.

Symbol und Stellung löst der Server auf und gibt sie der Seite als ``<html data-currency>``
mit (JSON); die Skripte lesen sie dort (``window.ZA_CURRENCY``, siehe ``static/js/i18n.js``),
damit Server und Browser einen Betrag gleich schreiben (kein ``Intl``-Währungsformat, das
z. B. in ``en-GB`` „US$“ ausgäbe).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import Response

from . import formats

logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = "EUR"
SETTING_KEY = "currency"
DEFAULT_SETTING = "auto"

# symbol: Zeichen im Betrag · spaced: Leerzeichen zwischen Symbol und Zahl, wenn das Symbol davor
# steht (Buchstabenkürzel wie „CHF 2.52“) · short: Kürzel der Untereinheit im Eingabefeld
# („Ct/kWh“) · subunit: Name der Untereinheit im Fließtext („Cent“, übersetzt über den Katalog).
CURRENCIES: dict[str, dict[str, Any]] = {
    "EUR": {"symbol": "€", "spaced": False, "short": "Ct", "subunit": "Cent"},
    "GBP": {"symbol": "£", "spaced": False, "short": "p", "subunit": "Pence"},
    "USD": {"symbol": "$", "spaced": False, "short": "¢", "subunit": "Cent"},
    "CHF": {"symbol": "CHF", "spaced": True, "short": "Rp", "subunit": "Rappen"},
}

CURRENCY_CHOICES = {
    "auto": "Automatisch (Home Assistant)",
    "EUR": "Euro (€)",
    "GBP": "Pfund Sterling (£)",
    "USD": "US-Dollar ($)",
    "CHF": "Schweizer Franken (CHF)",
}

# Währung der gerade bearbeiteten Anfrage (wie ``formats.current_format``).
current_currency: contextvars.ContextVar[str] = contextvars.ContextVar("zeitarchiv_currency", default=DEFAULT_CURRENCY)

# Die Home-Assistant-Währung ändert sich praktisch nie; ein Treffer gilt lange, ein Fehlschlag kurz
# (sonst fragte jede Seite einen nicht erreichbaren Supervisor erneut an).
_HA_CONFIG_URL = "http://supervisor/core/api/config"
_HA_TIMEOUT = 3
_TTL_OK = 3600
_TTL_FAILED = 300
_ha_cache: tuple[float, str] | None = None


def _fetch_ha_currency() -> str | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    request = urllib.request.Request(
        _HA_CONFIG_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "Zeitarchiv/Currency"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HA_TIMEOUT) as response:
            code = str(json.loads(response.read()).get("currency", "")).upper()
    except Exception as exc:  # Netzwerk, HTTP, JSON — für die Anzeige ist nur „nicht verfügbar“ wichtig
        logger.debug("HA-Währung nicht lesbar: %s", exc)
        return None
    return code if code in CURRENCIES else None


def home_assistant_currency() -> str:
    """Währung aus der HA-Konfiguration, gecacht; EUR, wenn sie fehlt oder nicht unterstützt wird."""
    global _ha_cache
    now = time.monotonic()
    if _ha_cache and now < _ha_cache[0]:
        return _ha_cache[1]
    code = _fetch_ha_currency()
    _ha_cache = (now + (_TTL_OK if code else _TTL_FAILED), code or DEFAULT_CURRENCY)
    return _ha_cache[1]


def resolve_currency(setting: str | None) -> str:
    return setting if setting in CURRENCIES else home_assistant_currency()


def selected_setting(raw: str | None) -> str:
    return raw if raw in CURRENCY_CHOICES else DEFAULT_SETTING


def position(locale: str | None = None) -> str:
    """Symbol hinter dem Betrag im deutschen Format (``2,52 €``), davor im englischen (``€2.52``)."""
    return "after" if (locale or formats.current_format.get()) == "de-DE" else "before"


def info(code: str | None = None, locale: str | None = None) -> dict[str, Any]:
    """Alles, was die Skripte zum Schreiben eines Betrags brauchen."""
    code = code or current_currency.get()
    spec = CURRENCIES[code]
    return {"code": code, "symbol": spec["symbol"], "spaced": spec["spaced"], "position": position(locale), "short": spec["short"]}


def format_money(value: float | None, decimals: int = 2) -> str:
    """Betrag im Format und in der Währung der laufenden Anfrage, z. B. ``2,52 €`` / ``€2.52``.

    Negative Beträge tragen das Minus vor dem Ganzen (``-€2.52``); ein auf null gerundeter
    Betrag bleibt ohne Vorzeichen."""
    from .formatting import format_value  # formatting importiert formats; hier erst bei Gebrauch

    if value is None:
        return "—"
    number = format_value(abs(value), decimals)
    sign = "-" if round(value, decimals) < 0 else ""
    spec = info()
    if spec["position"] == "after":
        return f"{sign}{number} {spec['symbol']}"
    return f"{sign}{spec['symbol']}{' ' if spec['spaced'] else ''}{number}"


# --- Anbindung an die Anfrage ------------------------------------------------------------------


def _currency_for(index) -> tuple[str, str]:
    setting = selected_setting(index.get_setting(SETTING_KEY, DEFAULT_SETTING))
    return setting, resolve_currency(setting)


def context_values(index, language: str, translate: Callable[[str, str], str]) -> dict:
    """Template-Variablen: ``currency_info`` (für ``<html data-currency>``), die Untereinheit
    für Hinweise und Eingabefelder und die Auswahl der Einstellungen."""
    setting, code = _currency_for(index)
    current_currency.set(code)
    spec = CURRENCIES[code]
    return {
        "currency_info": info(code),
        "currency_subunit": translate(spec["subunit"], language),
        "currency_subunit_short": spec["short"],
        "currency_setting": setting,
        "currency_options": [(key, translate(label, language)) for key, label in CURRENCY_CHOICES.items()],
    }


def set_for_request(index) -> None:
    current_currency.set(_currency_for(index)[1])


def make_router(get_index: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post("/settings/currency")
    def set_currency(currency: str = Form(...)) -> Response:
        """Speichert die Währung und lädt die Seite neu (alle Beträge ändern sich)."""
        if currency not in CURRENCY_CHOICES:
            from .i18n import tr  # erst hier: i18n importiert dieses Modul

            raise HTTPException(status_code=400, detail=tr("Ungültige Währung"))
        get_index().set_setting(SETTING_KEY, currency)
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    return router
