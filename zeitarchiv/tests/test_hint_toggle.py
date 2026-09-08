"""Der Info-Knopf, der einen erklärenden Hinweis aufklappt.

Die erklärenden Hinweise wurden einmal gelesen und belegten danach dauerhaft
Platz — auf der Entität-Konfiguration 557 von 2.806 px bei 375 px Breite, also
ein Fünftel der Seite. Sie stehen jetzt hinter einem Knopf im Label bzw. in der
Überschrift; Warnungen und Statuszeilen bleiben ungefragt sichtbar.

Aufklappen statt Popover, damit es keine zweite Überlagerungs-Mechanik neben
dd-picker gibt.

Seit 0.88.0 hängt der Standardzustand an der Breite: am Schreibtisch
aufgeklappt, unter 700px zu. Vorher war er überall gleich (immer zu), begründet
mit „ein Text für alle Bildschirme" — der Platzdruck, der das Wegklappen nötig
macht, besteht aber nur schmal. Dieselbe Aufteilung wie bei den beiden anderen
einklappbaren Blöcken der App (Protokollierungs-Karte, Import-Anleitungen).
"""

from __future__ import annotations

import re

from _paths import APP_CSS, APP_JS, TEMPLATES


JS = (APP_JS / "hint-toggle.js").read_text(encoding="utf-8")


def _templates() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.glob("*.html"))}


def _mit_knopf() -> dict[str, str]:
    return {n: q for n, q in _templates().items() if "hint_button(" in q and n != "_hints.html"}


def test_every_template_with_a_button_imports_the_macro() -> None:
    for name, quelle in _mit_knopf().items():
        assert '{% from "_hints.html" import hint_button, hint_body %}' in quelle, name


def test_every_button_has_a_body_to_open() -> None:
    """Ein Knopf ohne Hinweis dahinter wäre ein Knopf, der nichts tut — und das
    fiele nur dem auf, der ihn drückt.

    Mehr Körper als Knöpfe sind erlaubt: in statistik_index.html wählt ein
    {% if %} zwischen zwei Fassungen desselben Hinweises, gerendert wird immer
    genau eine.
    """
    for name, quelle in _mit_knopf().items():
        knoepfe = quelle.count("hint_button(")
        koerper = quelle.count("hint_body(")
        assert knoepfe >= 1, name
        assert koerper >= knoepfe, f"{name}: {knoepfe} Knöpfe, nur {koerper} Körper"


def test_the_pages_that_use_buttons_load_the_script() -> None:
    """Die Partials tragen die Knöpfe, geladen wird das Skript aber von der
    Seite, in die sie hineingerendert werden."""
    seiten = {
        "entity_config.html": ["_entity_config_form.html"],
        "energiedashboard.html": ["_energiedashboard_setup.html"],
        "backup.html": ["_settings_backup_ready.html"],
        "housekeeping.html": [
            "_settings_storage_index_form.html",
            "_settings_retention_form.html",
        ],
        "settings.html": [
            "_settings_storage_index_form.html",
            "_settings_darstellung_form.html",
            "_settings_tips_form.html",
        ],
        "statistik_index.html": [],
        "table_editor.html": [],
    }
    quellen = _templates()
    for seite in seiten:
        assert "hint-toggle.js" in quellen[seite], seite

    # Und umgekehrt: kein Template trägt Knöpfe, ohne dass eine dieser Seiten
    # es einbindet.
    getragen = set(seiten) | {p for teile in seiten.values() for p in teile}
    assert set(_mit_knopf()) <= getragen, set(_mit_knopf()) - getragen


def test_the_default_state_follows_the_width() -> None:
    """Am Schreibtisch aufgeklappt, unter 700px zu — derselbe Breakpoint wie bei
    den beiden anderen einklappbaren Blöcken. Der serverseitig gerenderte
    `hidden`-Zustand wird beim Laden korrigiert, auch nach einem htmx-Tausch."""
    assert "matchMedia('(max-width:700px)')" in JS
    assert "ziel.hidden = SCHMAL.matches;" in JS
    assert "htmx:afterSwap" in JS


def test_no_warning_and_no_status_line_can_be_folded_away() -> None:
    """Der Grund, warum es die Rollenklassen gibt: das Skript schließt sie
    ausdrücklich aus, und keine von ihnen steht in einem hint_body()."""
    assert ":not(.hint-warn):not(.hint-status)" in JS
    for name, quelle in _mit_knopf().items():
        for koerper in re.findall(r"\{% call hint_body\([^)]*\) %\}", quelle):
            assert "hint-warn" not in koerper and "hint-status" not in koerper, name


def test_the_hit_area_is_44_by_44_although_the_circle_is_16() -> None:
    """An zu kleinen Trefferflächen ist in dieser App schon der Aufklapppfeil
    der Kacheln gescheitert. Die Fläche wächst über ein Pseudoelement, damit
    sie im Layout keinen Platz belegt.

    Der Versatz nach oben ist nicht kosmetisch: mittig zentriert verliert der
    Knopf die unteren 6 px an das Bedienelement darunter (gemessen 44 x 38).
    """
    css = APP_CSS.read_text(encoding="utf-8")
    regel = re.search(r"\.hint-toggle::before\{([^}]*)\}", css)
    assert regel is not None
    rumpf = regel.group(1)
    assert "width:44px" in rumpf and "height:44px" in rumpf
    assert "top:calc(50% - 6px)" in rumpf, "ohne Versatz bleiben nur 44 x 38"

    knopf = re.search(r"\.hint-toggle\{([^}]*)\}", css)
    assert knopf is not None
    assert "width:16px" in knopf.group(1) and "height:16px" in knopf.group(1)


def test_the_control_below_wins_the_overlap() -> None:
    """Die 44 px reichen über die Labelzeile hinaus. Ohne position:relative auf
    dem Bedienelement läge die unsichtbare Fläche darüber und man träfe den
    Hinweis statt des Feldes."""
    css = APP_CSS.read_text(encoding="utf-8")
    assert ".field:has(> label > .hint-toggle) > input," in css
    assert ".field:has(> label > .hint-toggle) > .dd-picker-wrap," in css


def test_the_same_markup_serves_every_screen_width() -> None:
    """Ein Text für alle Breiten: der Knopf darf nicht in einem @media-Block
    auftauchen, sonst lesen Telefon und Schreibtisch Unterschiedliches."""
    css = APP_CSS.read_text(encoding="utf-8")
    for block in re.findall(r"@media[^{]*\{(.*?)\n\}", css, re.S):
        assert "hint-toggle" not in block


def test_the_body_starts_folded() -> None:
    """`hidden` steht im Markup, nicht im Skript — sonst blitzt der Text beim
    Laden einmal auf."""
    hints = (TEMPLATES / "_hints.html").read_text(encoding="utf-8")
    assert re.search(r'<p class="\{\{ klasse \}\}" hidden>', hints)


def test_the_toggle_survives_an_htmx_swap() -> None:
    """Die Formulare werden bei jeder Änderung komplett ersetzt und die
    Energiedashboard-Dialoge rollt Alpine je Zeile neu aus. Ein Listener je
    Knopf wäre danach weg — der Fehler fiele erst beim zweiten Klick auf."""
    assert "document.addEventListener('click'" in JS
    assert "closest('.hint-toggle')" in JS
