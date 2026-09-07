"""Der gemeinsame Seitenrahmen (ZG-04, Schritt 1).

Vorher waren 23 Templates eigenständige HTML-Dokumente mit je eigenem
`<head>`. Fünf der acht Kopf-Bestandteile waren dabei über alle 23
byte-identisch — ein Fix am Kopf musste also 23-mal gepflegt werden, und die
`app.css`-Zeile lief in vier Schreibweisen auseinander (ZG-03).

Diese Datei hält fest, dass es dabei bleibt: **ein** Kopf, und jede Vollseite
erbt ihn. Ohne diese Zusage wächst der zweite `<head>` bei der nächsten neuen
Seite wieder nach — genau so ist der Bestand ja entstanden.
"""

from __future__ import annotations

from _paths import TEMPLATES

BASE = "base.html"
NUR_IM_RAHMEN = [
    "<!doctype html>",
    '<html lang="de"',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport"',
    # Bis ZG-14 stand hier "fonts.googleapis.com" — der Schriften-<link>, den
    # der Rahmen den Seiten abgenommen hat. Seit die Schriften lokal liegen
    # und in app.css gebunden sind, gibt es diese Zeile nicht mehr. An ihre
    # Stelle tritt der Marker, der jetzt rahmen-exklusiv ist: der <style>-Block,
    # über den der Rahmen die Schriftgröße an die Seite reicht.
    "<style>:root{--font-scale:",
    "app.css?v={{ css_v }}",
]


def _erben() -> list:
    return [
        p
        for p in sorted(TEMPLATES.glob("*.html"))
        if p.read_text(encoding="utf-8").startswith('{% extends "base.html" %}')
    ]


def test_the_page_frame_exists_exactly_once() -> None:
    treffer = {
        p.name
        for p in TEMPLATES.glob("*.html")
        if "<!doctype html>" in p.read_text(encoding="utf-8")
    }
    assert treffer == {BASE}, f"weitere Templates mit eigenem Dokumentkopf: {sorted(treffer - {BASE})}"


def test_every_full_page_inherits_the_frame_instead_of_repeating_it() -> None:
    seiten = _erben()
    assert len(seiten) >= 20, f"nur {len(seiten)} Templates erben von {BASE}"
    for p in seiten:
        quelle = p.read_text(encoding="utf-8")
        for teil in NUR_IM_RAHMEN:
            assert teil not in quelle, f"{p.name} wiederholt {teil!r} aus {BASE}"


def test_the_frame_addresses_assets_independently_of_the_url_depth() -> None:
    """`{{ app_root }}` statt eines relativen Pfades ist der Kern von ZG-03:
    absolut, aus dem X-Ingress-Path-Header, unabhängig davon, wie tief die
    Seite in der URL liegt. Eine neue Route auf einer neuen Tiefe kann ihren
    Präfix damit nicht mehr vergessen.

    Dass das auch WIRKT, prüft test_ingress_prefix.py — hier steht nur, dass
    der Rahmen es so schreibt."""
    quelle = (TEMPLATES / BASE).read_text(encoding="utf-8")
    assert '"{{ app_root }}/static/css/app.css?v={{ css_v }}"' in quelle
    assert "{{ base }}" not in quelle


def test_the_frame_only_offers_blocks_that_the_pages_actually_use() -> None:
    """Blöcke auf Vorrat sind toter Code, der wie eine Zusage aussieht. Jeder
    Block hier existiert, weil mindestens eine Seite ihn überschreibt — und
    `content` muss jede tun, sonst wäre die Seite leer."""
    rahmen = (TEMPLATES / BASE).read_text(encoding="utf-8")
    seiten = {p.name: p.read_text(encoding="utf-8") for p in _erben()}

    import re

    bloecke = set(re.findall(r"\{%\s*block\s+(\w+)\s*%\}", rahmen))
    assert bloecke == {"title", "root_vars", "page_css", "topnav", "content"}, bloecke

    for name in bloecke:
        nutzer = [d for d, q in seiten.items() if f"{{% block {name} %}}" in q]
        assert nutzer, f"Block {name!r} wird von keiner Seite überschrieben"

    fehlt = [d for d, q in seiten.items() if "{% block content %}" not in q]
    assert not fehlt, f"ohne content-Block bliebe die Seite leer: {fehlt}"
