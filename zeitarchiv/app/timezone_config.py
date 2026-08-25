"""Robuste Auflösung der vom Add-on konfigurierten IANA-Zeitzone."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE_NAME = "Europe/Berlin"


def load_timezone(
    options: Mapping[str, object],
    *,
    environment: Mapping[str, str] | None = None,
    on_invalid: Callable[[str], None] | None = None,
) -> tzinfo:
    """Lädt die konfigurierte Zeitzone, ohne den App-Start zu gefährden.

    Das Supervisor-Schema kann das IANA-Format grob prüfen, kennt aber nicht
    die tatsächlich im Container verfügbare tzdata-Datenbank. Deshalb bleibt
    diese Laufzeitprüfung zwingend. Bei einem ungültigen Alt-/Manuellwert wird
    Europe/Berlin verwendet; fehlt wider Erwarten selbst dessen tzdata-Eintrag,
    bleibt als letzter, immer verfügbarer Schutz UTC.
    """
    env = os.environ if environment is None else environment
    configured = options.get("timezone") or env.get(
        "ZEITARCHIV_TIMEZONE", DEFAULT_TIMEZONE_NAME
    )

    try:
        if not isinstance(configured, str):
            raise TypeError("Zeitzonenname muss ein String sein")
        return ZoneInfo(configured)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        message = (
            f"Ungültige Zeitzone {configured!r}; "
            f"verwende {DEFAULT_TIMEZONE_NAME!r} als Fallback"
        )
        if on_invalid is not None:
            on_invalid(message)

    try:
        return ZoneInfo(DEFAULT_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        if on_invalid is not None:
            on_invalid(
                f"Zeitzonendaten für {DEFAULT_TIMEZONE_NAME!r} fehlen; verwende UTC"
            )
        return timezone.utc
