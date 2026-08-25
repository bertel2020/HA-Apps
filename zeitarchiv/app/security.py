"""Kleine, unabhängig testbare Sicherheitshelfer der Zeitarchiv-App."""

from __future__ import annotations

import secrets
from typing import Protocol

TOKEN_BYTES = 32  # 256 Bit Entropie vor URL-safe Base64-Kodierung.


class SettingsStore(Protocol):
    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def set_setting(self, key: str, value: str) -> None: ...


def generate_api_token() -> str:
    """Erzeugt einen für Bearer-Header geeigneten Token mit 256 Bit Entropie."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def ensure_api_token(
    settings: SettingsStore, *, development_token: str | None = None
) -> str:
    """Liefert den persistenten Token und erzeugt ihn einmalig, falls nötig.

    ``development_token`` ist ein bewusster Override ausschließlich für lokale
    Tests. Der Supervisor setzt ihn nicht; dort wird immer kryptografisch sicher
    generiert. Auch ein vorhandener leerer DB-Wert wird repariert, damit kein
    Betriebszustand ohne Authentifizierung entstehen kann.
    """
    current = settings.get_setting("api_token")
    if current:
        return current

    token = development_token or generate_api_token()
    settings.set_setting("api_token", token)
    return token
