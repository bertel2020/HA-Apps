"""Seitenlokales CSS liegt in Dateien, nicht in <style>-Blöcken (ZG-04 Schritt 3).

Vor diesem Umbau trug jede Vollseite ihre eigenen Regeln als `<style>`-Block im
Template. Das kostete nichts an Wartbarkeit — die Regeln standen bei ihrer
Seite, und das war Absicht —, aber es kostete auf jeder Leitung: 251 KB CSS
reisten bei JEDEM Seitenaufruf erneut mit, statt einmal geladen und danach aus
dem Browser-Cache bedient zu werden (ZG-22; allein `/energiedashboard` sind
62 KB, gut die Hälfte des ganzen Dokuments).

Der Umbau ändert nur die Ablage, nicht die Wirkung: die Datei wird an genau der
Stelle verlinkt, an der der Block stand, also weiterhin nach `app.css` und
weiterhin nur auf dieser einen Seite. Belegt wurde das über 40 Seitenabzüge
(20 Seiten, je mit und ohne Ingress-Header): außerhalb des `page_css`-Blocks
byte-identisch, das gelieferte CSS inhaltsgleich.

Diese Datei hält den Zustand fest. Ohne sie wäre die naheliegende Regression
nicht der Rückbau, sondern der Zuwachs: die nächste neue Seite bekommt wieder
einen `<style>`-Block, weil das im Template bequemer ist.
"""

from __future__ import annotations

import re

from _paths import PAGE_CSS, TEMPLATES

#: Nur `statistik_index.html` darf noch inline stylen — dort hängen drei
#: Spaltenbreiten an `index_sizes_available` und müssen gerendert werden.
JINJA_INLINE = {"statistik_index.html"}

LINK = re.compile(
    r'<link rel="stylesheet" href="\{\{ asset\(\'css/pages/'
    r'(?P<file>[a-z_]+\.css)\'\) \}\}">'
)


def _pages() -> dict[str, str]:
    """Alle Vollseiten-Templates mit ihrem Quelltext."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES.glob("*.html"))
        if path.read_text(encoding="utf-8").startswith('{% extends "base.html" %}')
    }


def test_no_page_carries_its_stylesheet_inline_any_more() -> None:
    offenders = {
        name: source.count("<style>")
        for name, source in _pages().items()
        if "<style>" in source and name not in JINJA_INLINE
    }
    assert not offenders, (
        f"Diese Seiten stylen wieder inline: {offenders}. Der Block gehört nach "
        "static/css/pages/<seite>.css und wird im page_css-Block verlinkt."
    )


def test_the_one_inline_exception_holds_only_what_jinja_has_to_render() -> None:
    source = (TEMPLATES / "statistik_index.html").read_text(encoding="utf-8")
    block = source[source.index("<style>") : source.index("</style>")]
    rules = [line for line in block.splitlines() if line.strip().startswith(".")]
    assert rules, "Ausnahme ohne Regeln — dann kann der Block ganz weg"
    for rule in rules:
        assert "{{" in rule, (
            f"{rule.strip()!r} enthält kein Jinja und gehört damit in die Datei, "
            "nicht in den Ausnahmeblock"
        )


def test_every_page_links_a_file_that_exists_and_is_named_after_it() -> None:
    for name, source in _pages().items():
        match = LINK.search(source)
        if match is None:
            # Eine Seite ohne eigene Regeln ist erlaubt — sie darf dann aber
            # auch keinen leeren page_css-Block mitschleppen.
            assert "css/pages/" not in source, f"{name}: kaputter Verweis"
            continue
        expected = name.removesuffix(".html").lstrip("_") + ".css"
        assert match.group("file") == expected, (
            f"{name} verlinkt {match.group('file')}, erwartet wird {expected}"
        )
        assert (PAGE_CSS / expected).is_file(), f"{name}: {expected} fehlt"


def test_no_lifted_stylesheet_is_orphaned_or_shared() -> None:
    """Eine Datei, die keine oder mehrere Seiten verlinken, ist ein Fehler.

    Keine Seite: Karteileiche. Mehrere Seiten: dann sind es geteilte Regeln,
    und die gehören nach app.css statt in eine Seiten-Datei.
    """
    referenced: dict[str, list[str]] = {}
    for name, source in _pages().items():
        for file in LINK.findall(source):
            referenced.setdefault(file, []).append(name)
    on_disk = {path.name for path in PAGE_CSS.glob("*.css")}
    assert on_disk == set(referenced), (
        f"ohne Verweis: {sorted(on_disk - set(referenced))}, "
        f"ohne Datei: {sorted(set(referenced) - on_disk)}"
    )
    shared = {file: pages for file, pages in referenced.items() if len(pages) > 1}
    assert not shared, f"von mehreren Seiten verlinkt, gehört nach app.css: {shared}"


def test_lifted_stylesheets_contain_no_jinja() -> None:
    """Eine .css-Datei wird von StaticFiles ausgeliefert, nicht gerendert.

    Ein `{{ … }}` darin fiele nicht als Fehler auf, sondern als stumm falsche
    Regel — der Browser verwirft sie und die Seite sieht nur leicht daneben aus.
    """
    for path in sorted(PAGE_CSS.glob("*.css")):
        text = path.read_text(encoding="utf-8")
        assert "{{" not in text and "{%" not in text, f"{path.name} enthält Jinja"


def test_the_lifted_bytes_really_left_the_templates() -> None:
    """Gegenprobe zur Zusage aus ZG-22: die Seiten sind messbar kleiner.

    Ohne diese Zusage bliebe der Test oben auch dann grün, wenn jemand die
    Regeln zusätzlich in die Datei schreibt statt sie zu verschieben.
    """
    inline = sum(
        len(source[source.index("<style>") : source.index("</style>")])
        for name, source in _pages().items()
        if "<style>" in source
    )
    lifted = sum(path.stat().st_size for path in PAGE_CSS.glob("*.css"))
    assert lifted > 200_000, f"nur {lifted} B ausgelagert — der Umbau ist zurückgerollt"
    assert inline < 1_000, f"noch {inline} B inline, erwartet nur die Jinja-Ausnahme"
