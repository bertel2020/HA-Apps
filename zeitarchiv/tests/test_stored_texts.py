"""Gespeicherte Texte (Fehler von Läufen, Import-Reports, Stummschaltungen) und Hintergrundsprache."""

from __future__ import annotations

import pytest

from _paths import APP
from app import i18n

SKIPPED_DE = "Übersprungen, weil bereits ein Backup läuft"
SKIPPED_EN = "Skipped because a backup is already running"


@pytest.fixture
def language():
    """Setzt die Sprache der „Anfrage“ und stellt sie danach wieder zurück."""
    tokens = []

    def use(name: str | None) -> None:
        tokens.append(i18n.current_language.set(name))

    yield use
    for token in reversed(tokens):
        i18n.current_language.reset(token)


def test_a_stored_fixed_text_is_shown_in_the_current_language(language) -> None:
    language("en")
    assert i18n.retranslate(SKIPPED_DE) == SKIPPED_EN
    assert i18n.retranslate(SKIPPED_EN) == SKIPPED_EN
    language("de")
    assert i18n.retranslate(SKIPPED_EN) == SKIPPED_DE
    assert i18n.retranslate(SKIPPED_DE) == SKIPPED_DE


def test_a_stored_text_with_values_keeps_its_values(language) -> None:
    language("en")
    assert i18n.retranslate("Import abgebrochen: Verbindung getrennt") == "Import aborted: Verbindung getrennt"
    language("de")
    assert i18n.retranslate("Import aborted: connection lost") == "Import abgebrochen: connection lost"


def test_the_most_specific_entry_wins(language) -> None:
    # Viele Schlüssel haben dieselbe Form "Text: {wert}"; der mit dem längsten festen Wortlaut gilt.
    language("en")
    assert i18n.retranslate("Retention fehlgeschlagen: Platte voll") == "Retention failed: Platte voll"


def test_unknown_texts_and_empty_values_stay_untouched(language) -> None:
    language("en")
    assert i18n.retranslate("sensor.x: [Errno 2] No such file or directory") == "sensor.x: [Errno 2] No such file or directory"
    assert i18n.retranslate("Wohnzimmer Temperatur") == "Wohnzimmer Temperatur"
    assert i18n.retranslate("") == ""
    assert i18n.retranslate(None) is None


class _Index:
    def __init__(self, **settings) -> None:
        self.settings = dict(settings)

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value) -> None:
        self.settings[key] = value


@pytest.fixture
def background(monkeypatch):
    monkeypatch.setattr(i18n, "_background_provider", None)
    monkeypatch.setattr(i18n, "_last_seen", None)
    return monkeypatch


def test_without_a_request_or_provider_the_language_is_german(background, language) -> None:
    language(None)
    assert i18n.active_language() == "de"


def test_a_fixed_language_setting_applies_to_background_work(background, language) -> None:
    language(None)
    i18n.set_background_language(lambda: _Index(language="en"))
    assert i18n.active_language() == "en"
    assert i18n.tr(SKIPPED_DE) == SKIPPED_EN


def test_automatic_uses_the_last_language_a_browser_asked_for(background, language) -> None:
    language(None)
    index = _Index(language="auto")
    i18n.set_background_language(lambda: index)
    assert i18n.active_language() == "de"
    i18n._remember_language(index, "auto", "en-GB,en;q=0.9")
    assert i18n.active_language() == "en"
    assert index.settings["language_last_seen"] == "en"


def test_the_last_language_survives_a_restart(background, language) -> None:
    language(None)
    index = _Index(language="auto", language_last_seen="en")
    i18n.set_background_language(lambda: index)
    assert i18n.active_language() == "en"


def test_a_script_without_a_header_does_not_overwrite_the_remembered_language(background, language) -> None:
    language(None)
    index = _Index(language="auto", language_last_seen="en")
    i18n._remember_language(index, "auto", "")
    i18n._remember_language(index, "auto", "fr-FR")
    assert index.settings["language_last_seen"] == "en"


def test_nothing_is_remembered_for_a_fixed_language(background) -> None:
    index = _Index(language="de")
    i18n._remember_language(index, "de", "en-US")
    assert "language_last_seen" not in index.settings


def test_the_remembered_language_is_written_only_when_it_changes(background) -> None:
    index = _Index(language="auto")
    writes = []
    original = index.set_setting
    index.set_setting = lambda key, value: (writes.append(value), original(key, value))
    i18n._remember_language(index, "auto", "en")
    i18n._remember_language(index, "auto", "en-GB")
    i18n._remember_language(index, "auto", "de")
    assert writes == ["en", "de"]


def test_the_pages_show_stored_texts_through_the_filter() -> None:
    for name in ("_housekeeping_activity_form.html", "_settings_backup_ready.html", "_settings_retention_form.html"):
        text = (APP / "templates" / name).read_text(encoding="utf-8")
        assert "error | retranslate" in text, name
    assert "error | retranslate" in (APP / "templates" / "report_detail.html").read_text(encoding="utf-8")
