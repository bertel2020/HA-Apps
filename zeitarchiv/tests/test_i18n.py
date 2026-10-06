"""Mehrsprachigkeit (Roadmap 1.21): Deutsch ist der Quelltext, Englisch ein Katalog."""

from __future__ import annotations

import json
import re

import pytest

from _paths import APP
from app import i18n

CATALOG = json.loads((APP / "i18n" / "en.json").read_text(encoding="utf-8"))
TEMPLATE_CALL = re.compile(r"""\b_\(\s*(?P<q>['"])(?P<text>(?:\\.|(?!(?P=q)).)*)(?P=q)""", re.S)
SCRIPT_CALL = re.compile(r"""(?<![\w.$])t\(\s*(?P<q>['"])(?P<text>.+?)(?P=q)\s*[,)]""")


def _literals(folder: str, suffixes: tuple[str, ...], pattern: re.Pattern[str]) -> set[str]:
    found: set[str] = set()
    for path in (APP / folder).rglob("*"):
        if path.suffix in suffixes and "vendor" not in path.parts:
            found.update(re.sub(r"\\(.)", r"\1", m.group("text")) for m in pattern.finditer(path.read_text(encoding="utf-8")))
    return found


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def test_untranslated_text_stays_german_and_values_are_filled() -> None:
    assert i18n.translate("Gibt es nicht im Katalog", "en") == "Gibt es nicht im Katalog"
    assert i18n.translate("Übersicht", "de") == "Übersicht"
    assert i18n.translate("Übersicht", "en") == "Overview"
    assert i18n.translate("{done} von {total}", "en", done="1", total="2") == "1 of 2"
    assert i18n.translate("Übersicht", "fr") == "Übersicht"


@pytest.mark.parametrize("header,expected", [
    ("", "de"), ("en-US,en;q=0.9", "en"), ("fr-FR,fr;q=0.9,en;q=0.8", "en"),
    ("de-DE,de;q=0.9", "de"), ("fr", "de"), ("EN", "en"),
])
def test_accept_language_picks_the_first_supported_language(header: str, expected: str) -> None:
    assert i18n.from_accept_language(header) == expected


def test_language_setting_resolution() -> None:
    assert i18n.resolve_language("en") == "en"
    assert i18n.resolve_language("de", "en-US") == "de"
    assert i18n.resolve_language("auto", "en-GB") == "en"
    assert i18n.resolve_language("xx", "en-GB") == "de"
    assert i18n.resolve_language(None) == "de"


def test_every_text_marked_for_translation_has_an_english_entry() -> None:
    fehlend_app = _literals("templates", (".html",), TEMPLATE_CALL) - set(CATALOG["app"])
    fehlend_js = (
        _literals("static/js", (".js",), SCRIPT_CALL) | _literals("templates", (".html",), SCRIPT_CALL)
    ) - set(CATALOG["js"])
    assert not fehlend_app, f"fehlt in en.json/app: {sorted(fehlend_app)}"
    assert not fehlend_js, f"fehlt in en.json/js: {sorted(fehlend_js)}"


def test_catalog_has_no_dead_entries() -> None:
    quellen = "\n".join(
        path.read_text(encoding="utf-8").replace('\\"', '"').replace("\\'", "'")
        for path in APP.rglob("*")
        if path.suffix in {".html", ".js", ".py"} and "vendor" not in path.parts
    )
    tot = [key for section in CATALOG.values() for key in section if key not in quellen]
    assert not tot, f"Einträge ohne Verwendung: {tot}"


def test_translations_keep_the_placeholders_of_the_german_text() -> None:
    for section in CATALOG.values():
        for german, english in section.items():
            assert _placeholders(german) == _placeholders(english), german
            assert english.strip(), german


@pytest.fixture()
def german_again(client):
    yield
    client.post("settings/language", data={"language": "de"})


def test_default_language_is_german_and_ships_no_script_catalog(client, german_again) -> None:
    html = client.get("/uebersicht").text
    assert '<html lang="de"' in html
    assert "ZA_CATALOG" not in html
    assert ">Übersicht<" in html


def test_setting_english_translates_pages_and_ships_the_script_catalog(client, german_again) -> None:
    response = client.post("/settings/language", data={"language": "en"})
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"
    html = client.get("/uebersicht").text
    assert '<html lang="en"' in html
    assert "</svg>Overview</a>" in html and "</svg>Entities</a>" in html
    assert "</svg>Entitäten</a>" not in html and "</svg>Einstellungen</a>" not in html
    assert "window.ZA_CATALOG" in html and "tasks running" in html
    # Makros ohne Seitenkontext (hint_button aus _hints.html) übersetzen über die Anfrage-Sprache.
    settings = client.get("/settings").text
    assert "Hint about" in settings and "Hinweis zu" not in settings


def test_automatic_language_follows_the_browser(client, german_again) -> None:
    client.post("/settings/language", data={"language": "auto"})
    assert '<html lang="en"' in client.get("/uebersicht", headers={"Accept-Language": "en-US,en;q=0.9"}).text
    assert '<html lang="de"' in client.get("/uebersicht", headers={"Accept-Language": "fr-FR"}).text


def test_unknown_language_is_rejected(client) -> None:
    assert client.post("/settings/language", data={"language": "klingon"}).status_code == 400


def test_settings_page_offers_the_language_choice(client, german_again) -> None:
    html = client.get("/settings").text
    assert 'id="language-input"' in html and 'hx-post="settings/language"' in html
    client.post("/settings/language", data={"language": "en"})
    assert "English" in client.get("/settings").text
