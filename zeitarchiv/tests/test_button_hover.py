"""Gefüllte Knöpfe (`.btn.primary` und `.btn-primary`) behalten beim Darüberfahren ihren Hintergrund.

`.btn:hover` (Hintergrund = Fläche) ist spezifischer als `.btn-primary` (weiße Schrift): ohne eigene Hover-Regel stünde
weißer Text auf weißem Grund, der Knopf erschiene als leeres Feld. Die Stylesheet-Dokumentation nennt beide Klassen als
gleichwertig, und mehrere Vorlagen nutzen `btn-primary`."""

from __future__ import annotations

import re

from _paths import APP_CSS, TEMPLATES


def _rule(css: str, marker: str) -> str:
    start = css.index(marker)
    return css[start: css.index("}", start) + 1]


def test_both_primary_classes_have_a_hover_rule_that_keeps_the_fill() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    rule = _rule(css, ".btn.primary:hover:not(:disabled)")
    assert ".btn-primary:hover:not(:disabled)" in rule.split("{")[0]
    assert "background:var(--accent-line)" in rule
    # die allgemeine Hover-Regel steht davor, die Primär-Regel muss danach kommen und sie so überstimmen
    assert css.index(".btn:hover:not(:disabled)") < css.index(".btn.primary:hover:not(:disabled)")


def test_every_template_class_btn_primary_is_covered() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    users = [p.name for p in TEMPLATES.glob("*.html") if re.search(r'class="[^"]*\bbtn-primary\b', p.read_text(encoding="utf-8"))]
    assert users, "keine Vorlage nutzt btn-primary mehr: der Test und die Regel können entfallen"
    assert ".btn-primary:hover:not(:disabled)" in css
