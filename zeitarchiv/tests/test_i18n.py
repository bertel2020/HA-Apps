"""Mehrsprachigkeit (Roadmap 1.21): Deutsch ist der Quelltext, Englisch ein Katalog."""

from __future__ import annotations

import ast
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


def _python_literals() -> set[str]:
    """Erste Textargumente von tr(...)/N_(...) in app/**/*.py (Python-seitige Texte)."""
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        if "i18n" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"tr", "N_"}
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
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
    fehlend_app = (_literals("templates", (".html",), TEMPLATE_CALL) | _python_literals()) - set(CATALOG["app"])
    fehlend_js = (
        _literals("static/js", (".js",), SCRIPT_CALL) | _literals("templates", (".html",), SCRIPT_CALL)
    ) - set(CATALOG["js"])
    assert not fehlend_app, f"fehlt in en.json/app: {sorted(fehlend_app)}"
    assert not fehlend_js, f"fehlt in en.json/js: {sorted(fehlend_js)}"


SCRIPT_LITERAL = re.compile(r"""(?P<q>['"])(?P<text>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
GERMAN_HINT = re.compile(
    r"[äöüÄÖÜß]|\b(Stunde|Tag|Tage|Woche|Monat|Jahr|Minute|Sekunde|der|die|das|und|oder|nicht|kein|keine|"
    r"Fehler|Wert|Werte|Zeit|Auswahl|wird|werden|ist|mit|für|von|bei|Alle|Neu|Name|Einheit|Summe)\b"
)
# Bewusst deutsch gelassene Literale (Datei -> Texte); neue Einträge brauchen eine Begründung.
UNTRANSLATED_SCRIPT_TEXT_OK: dict[str, set[str]] = {}


def test_no_german_script_text_is_left_outside_t() -> None:
    """Deutsche Texte in den Skripten ohne t(...) fielen bisher erst im Browser auf.

    Gemeldet wird, was wie ein sichtbarer Text aussieht: ein Literal, das einem Katalogschlüssel
    gleicht, oder ein mehrwortiges mit deutschem Wort/Umlaut. Kommentarzeilen zählen nicht.
    """
    schluessel = set(CATALOG["js"]) | set(CATALOG["app"])
    gefunden: list[str] = []
    for path in sorted((APP / "static" / "js").rglob("*.js")):
        if "vendor" in path.parts or path.name == "i18n.js":
            continue
        erlaubt = UNTRANSLATED_SCRIPT_TEXT_OK.get(path.name, set())
        for nummer, zeile in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if zeile.strip().startswith(("//", "*", "/*")):
                continue
            code = re.sub(r"\s//[^'\"`]*$", "", zeile)
            for treffer in SCRIPT_LITERAL.finditer(code):
                text = treffer.group("text")
                if code[max(0, treffer.start() - 2) : treffer.start()].endswith("t("):
                    continue
                if len(text) < 3 or not re.search("[A-Za-zäöü]{3}", text) or text in erlaubt:
                    continue
                if text in schluessel or (GERMAN_HINT.search(text) and " " in text):
                    gefunden.append(f"{path.name}:{nummer}: {text[:60]!r}")
    assert not gefunden, "deutscher Text ohne t(): " + "; ".join(gefunden)


ALPINE_ATTRIBUTE = re.compile(r"""(?<![\w-])(?:x-[\w:.-]+|@[\w:.-]+|:[\w-]+)\s*=\s*(?:"(?P<d>[^"]*)"|'(?P<s>[^']*)')""", re.S)
WRAPPED_TEXT = re.compile(r"""(?<![A-Za-z0-9])[t_]\(\s*(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`)""")
# Bewusst deutsch gelassene Literale in Template-Attributen (Datei -> Texte), mit Begründung.
UNTRANSLATED_ATTRIBUTE_TEXT_OK: dict[str, set[str]] = {
    # Einheit in einer Zahlenklammer, "(12,3 kWh)" — nichts zu übersetzen; der Schlüssel stammt aus einem anderen Satz.
    "_energiedashboard_view.html": {" kWh)"},
}


def test_no_german_text_is_left_outside_t_in_template_attributes() -> None:
    """Alpine-Ausdrücke in x-text/@click/:title/... sehen weder der Katalogtest noch der Skripttest.

    Gemeldet wird derselbe Verdacht wie bei den Skripten: ein Literal, das einem Katalogschlüssel
    gleicht, oder ein mehrwortiges mit deutschem Wort/Umlaut — nach Abzug von t(...)/_(...).
    """
    schluessel = set(CATALOG["js"]) | set(CATALOG["app"])
    gefunden: list[str] = []
    for path in sorted((APP / "templates").rglob("*.html")):
        # Kommentare und Jinja-Blöcke fallen weg, ihre Zeilenumbrüche bleiben (Zeilennummern).
        quelltext = re.sub(
            r"<!--.*?-->|\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}",
            lambda m: "\n" * m.group().count("\n"),
            path.read_text(encoding="utf-8"), flags=re.S,
        )
        erlaubt = UNTRANSLATED_ATTRIBUTE_TEXT_OK.get(path.name, set())
        for attribut in ALPINE_ATTRIBUTE.finditer(quelltext):
            wert = attribut.group("d") if attribut.group("d") is not None else attribut.group("s")
            wert = re.sub(r"(?m)^\s*//.*$", "", wert)
            wert = WRAPPED_TEXT.sub("", wert)
            for treffer in SCRIPT_LITERAL.finditer(wert):
                text = treffer.group("text")
                if len(text) < 3 or not re.search("[A-Za-zäöü]{3}", text) or text in erlaubt:
                    continue
                if text in schluessel or (GERMAN_HINT.search(text) and " " in text):
                    nummer = quelltext.count("\n", 0, attribut.start()) + 1
                    gefunden.append(f"{path.name}:{nummer}: {text[:60]!r}")
    assert not gefunden, "deutscher Text ohne t() in Template-Attributen: " + "; ".join(gefunden)


FABRIK = re.compile(r"^(create_\w*router|register\w*)$")
PYTHON_TEXT_KEYS = {"label", "title", "message", "detail", "error", "msg", "text", "description", "hint"}
# Bewusst unübersetzte Python-Texte (Datei -> Texte); Produktname oder in beiden Sprachen gleich.
UNTRANSLATED_PYTHON_TEXT_OK: dict[str, set[str]] = {
    "main.py": {"Zeitarchiv", "Rollups", "Backups"},
}


def _python_modules():
    for path in sorted(APP.rglob("*.py")):
        if "i18n" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for knoten in ast.walk(tree):
            for kind in ast.iter_child_nodes(knoten):
                kind.parent = knoten  # type: ignore[attr-defined]
        yield path, tree


def _enclosing_function(knoten: ast.AST):
    while hasattr(knoten, "parent"):
        knoten = knoten.parent  # type: ignore[attr-defined]
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return knoten
    return None


def test_tr_is_not_called_when_the_module_is_loaded() -> None:
    """tr() liefert den Text in der Sprache der laufenden Anfrage — beim Laden des Moduls oder beim
    Aufbau des Routers gibt es keine, es käme immer Deutsch heraus und bliebe so stehen (so erschienen
    die Housekeeping-Filter in der englischen Oberfläche deutsch). Dort gehört N_() hin."""
    gefunden: list[str] = []
    for path, tree in _python_modules():
        for knoten in ast.walk(tree):
            if not (isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) and knoten.func.id == "tr"):
                continue
            funktion = _enclosing_function(knoten)
            beim_laden = funktion is None
            if isinstance(funktion, (ast.FunctionDef, ast.AsyncFunctionDef)) and FABRIK.match(funktion.name):
                beim_laden = isinstance(getattr(funktion, "parent", None), ast.Module)
            if beim_laden:
                gefunden.append(f"{path.name}:{knoten.lineno}")
    assert not gefunden, "tr() beim Laden/Router-Aufbau (stattdessen N_()): " + ", ".join(gefunden)


def _text_of(wert: ast.AST) -> str | None:
    if isinstance(wert, ast.Constant) and isinstance(wert.value, str):
        return wert.value
    if isinstance(wert, ast.JoinedStr):
        return "".join(t.value if isinstance(t, ast.Constant) else "{}" for t in wert.values)
    return None


def test_python_texts_for_the_page_go_through_tr() -> None:
    """Ein Literal als label=/title=/message=/detail=/... ohne tr()/N_() bleibt in jeder Sprache deutsch.

    Gemeldet wird derselbe Verdacht wie bei Skripten und Template-Attributen: mehrwortig, mit Großbuchstaben
    am Anfang oder gleich einem Katalogschlüssel. Zeichenketten in tr()/N_() zählen nicht, weil sie als
    Aufruf keine Konstante sind.
    """
    schluessel = set(CATALOG["js"]) | set(CATALOG["app"])
    gefunden: list[str] = []
    for path, tree in _python_modules():
        erlaubt = UNTRANSLATED_PYTHON_TEXT_OK.get(path.name, set())
        for knoten in ast.walk(tree):
            kandidaten: list[tuple[ast.AST, str]] = []
            if isinstance(knoten, ast.keyword) and knoten.arg in PYTHON_TEXT_KEYS:
                kandidaten.append((knoten.value, knoten.arg))
            if isinstance(knoten, ast.Dict):
                kandidaten += [
                    (wert, schl.value) for schl, wert in zip(knoten.keys, knoten.values)
                    if isinstance(schl, ast.Constant) and schl.value in PYTHON_TEXT_KEYS
                ]
            for wert, name in kandidaten:
                text = _text_of(wert)
                if text is None or len(re.findall("[A-Za-zäöüÄÖÜ]", text)) < 3 or text in erlaubt:
                    continue
                if text in schluessel or " " in text.strip() or text[:1].isupper():
                    gefunden.append(f"{path.name}:{wert.lineno}: {name}={text[:50]!r}")
    assert not gefunden, "Python-Text ohne tr()/N_(): " + "; ".join(gefunden)


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


def test_python_texts_follow_the_language_of_the_request(client, german_again) -> None:
    """Meldungen und Tipps entstehen in Python (tr()/N_()) — sie folgen der Anfrage-Sprache."""
    client.post("/settings/language", data={"language": "en"})
    panel = client.get("/notices/panel").text
    assert "Tip:" in panel and "Tipp:" not in panel
    client.post("/settings/language", data={"language": "de"})
    panel = client.get("/notices/panel").text
    assert "Tipp:" in panel and "Tip:" not in panel
