"""Die drei „So funktioniert …“-Anleitungen auf der Import-Seite.

Gemessen bei 375px nimmt die Symcon-Anleitung 660 von 812 px — 81 % einer
Telefonhöhe zwischen Reiterzeile und Formular, für einen Text, der eine
EINMALIGE Migration erklärt. Eingeklappt sind es 70 px.

Kein Info-Knopf wie sonst (_hints.html): eine nummerierte Schrittliste passt
nicht in einen einzeiligen Hinweis. Stattdessen dasselbe Muster wie die
Protokollierungs-Karte der Log-Seite.
"""

from __future__ import annotations

from _paths import APP, TEMPLATES


def test_all_three_how_to_blocks_are_marked_collapsible() -> None:
    quelle = (TEMPLATES / "import.html").read_text(encoding="utf-8")
    assert quelle.count('<div class="callout" data-collapsible>') == 3
    assert quelle.count('<strong class="callout-title">') == 3
    # Der Hinweis auf die fehlende settings.json ist KEINE Anleitung, sondern
    # eine Meldung zum aktuellen Zustand — er bleibt immer sichtbar.
    assert 'id="settings-hint"' in quelle
    hinweis = quelle[quelle.index('id="settings-hint"'):]
    assert "data-collapsible" not in hinweis[: hinweis.index("</div>")]


def test_the_desktop_stays_untouched() -> None:
    """Die Klasse wird nur unterhalb des Breakpoints gesetzt — am Schreibtisch
    bleibt der Block ohne Zustandslogik immer offen, wie bei logs.js."""
    js = (APP / "static/js/pages/import.js").read_text(encoding="utf-8")
    assert "matchMedia('(max-width:700px)')" in js
    assert "schmal.matches" in js
    css = (APP / "static/css/pages/import.css").read_text(encoding="utf-8")
    zuklappen = css[css.index("@media (max-width:700px)"):]
    assert ".callout[data-collapsible].is-collapsed > :not(.callout-title){display:none;}" in zuklappen
    # Und zwar NUR dort: außerhalb des Media Query darf nichts eingeklappt werden.
    assert "is-collapsed" not in css[: css.index("@media (max-width:700px)")]
