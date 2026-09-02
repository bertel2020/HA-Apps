"""Tests für die verpflichtende API-Token-Erzeugung der Zeitarchiv-App."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import TOKEN_BYTES, ensure_api_token, generate_api_token


class FakeSettings:
    def __init__(self, token: str | None = None) -> None:
        self.values = {} if token is None else {"api_token": token}
        self.writes: list[tuple[str, str]] = []

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.values[key] = value
        self.writes.append((key, value))


def test_generate_api_token_is_urlsafe_and_has_256_bit_source() -> None:
    token = generate_api_token()
    assert TOKEN_BYTES == 32
    assert len(token) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)


def test_first_start_generates_and_persists_token() -> None:
    settings = FakeSettings()
    token = ensure_api_token(settings)
    assert token
    assert settings.values["api_token"] == token
    assert settings.writes == [("api_token", token)]


def test_existing_token_is_preserved_without_write() -> None:
    settings = FakeSettings("already-configured")
    assert ensure_api_token(settings) == "already-configured"
    assert settings.writes == []


def test_empty_persisted_token_is_replaced() -> None:
    settings = FakeSettings("")
    token = ensure_api_token(settings)
    assert token
    assert settings.values["api_token"] == token


def test_explicit_development_token_is_supported_only_during_initialization() -> None:
    settings = FakeSettings()
    assert ensure_api_token(settings, development_token="devtoken") == "devtoken"
    assert ensure_api_token(settings, development_token="different") == "devtoken"


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
