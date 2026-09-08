"""Seitengrund mit Verlauf, Schatten auf den Übersichtskarten.

Beides hängt an einer Entscheidung, die sich beim Nachrechnen ergeben hat und
die man beim Nachbessern leicht wieder umdreht: abgedunkelt wird über ein rohes
rgba je Schema, nicht über ein Farbtoken.
"""

from __future__ import annotations

import re

from _paths import APP


def _css(name: str) -> str:
    return (APP / f"static/css/{name}").read_text(encoding="utf-8")


def test_the_page_ground_darkens_towards_the_bottom() -> None:
    """--bg gegen --surface ergibt im Schema "modern" 1,071 — die Karten liest
    man am Rahmen, nicht an der Fläche. Der Verlauf bringt das auf 1,190."""
    css = _css("app.css")
    assert "body::before{" in css
    block = css[css.index("body::before{"):]
    block = block[: block.index("}")]
    assert "position:fixed" in block, "mitscrollend zöge er sich über die ganze Dokumenthöhe"
    assert "z-index:-1" in block
    assert "linear-gradient(180deg, transparent 0%, var(--bg-shade) 100%)" in block
    # background-attachment:fixed wäre das naheliegende Mittel, ruckelt aber auf
    # iOS — deshalb das Pseudoelement. (Die Eigenschaft selbst nutzt .tbl-wrap
    # für ihren Scroll-Hinweis, hier darf sie nur nicht stehen.)
    assert "background-attachment" not in block


def test_the_ground_shade_is_a_raw_rgba_per_scheme_not_a_colour_token() -> None:
    """Farbtoken kehren sich in dunklen Schemata um: der Grund wanderte dort zur
    Kartenfarbe HIN und fräße die Kante (gemessen 1,098 -> 1,028 bei 5 %
    Akzenttönung). Deshalb dieselbe Machart wie --shadow."""
    css = _css("app.css")
    werte = re.findall(r"--bg-shade:\s*([^;]+);", css)
    # Drei Schemata x hell/dunkel, dunkel jeweils zweimal (Media Query + Stempel).
    assert len(werte) == 9, werte
    for wert in werte:
        assert wert.startswith("rgba("), wert
        assert "var(" not in wert, wert


def test_the_report_page_stays_flat() -> None:
    """Sie ist als Dokument zum Drucken/als PDF gedacht und trägt bewusst keinen
    App-Rahmen."""
    assert "body::before{display:none;}" in _css("pages/energiedashboard_report.css")
    app = _css("app.css")
    druck = app[app.index("@media print{"):]
    assert "body::before{display:none;}" in druck[: druck.index("}\n}") + 3]


def test_only_the_clickable_overview_cards_get_a_shadow() -> None:
    """Die übrigen .card-Flächen (Einstellungen, Housekeeping, Import) sind
    Formularflächen, keine anklickbaren Kacheln — sie bleiben flach."""
    for seite in ("dashboards", "charts", "tables"):
        css = _css(f"pages/{seite}.css")
        assert "box-shadow:var(--shadow);" in css, seite
    # .card selbst bleibt ohne Schatten.
    app = _css("app.css")
    kartenregel = app[app.index(".config-card,.card{"):]
    kartenregel = kartenregel[: kartenregel.index("}")]
    assert "box-shadow" not in kartenregel


def test_the_remaining_standalone_surfaces_carry_it_too() -> None:
    """Abgesucht wurde Seite für Seite, welche abgesetzten Flächen noch flach
    auf dem Grund standen: freistehende Tabellen (Entitäten, Backup, Export),
    Status-Kacheln (Backup, Import) und die beiden Statistik-Karten. Alles
    andere liegt INNERHALB eines .settings-section/.card und bliebe sonst ein
    Schatten im Schatten."""
    app = _css("app.css")
    assert ".tbl-wrap,.status-card{box-shadow:var(--shadow);}" in app
    # Die Ausnahme ist der eigentliche Punkt der Regel — ohne sie bekämen die
    # Tabellen in den Housekeeping-/Einstellungen-Abschnitten einen zweiten.
    ausnahme = app[app.index(".card .tbl-wrap,.card .status-card,"):]
    ausnahme = ausnahme[: ausnahme.index("}")]
    assert ".settings-section .tbl-wrap" in ausnahme
    assert ".detail-dialog .tbl-wrap" in ausnahme

    # Unter 640px wird die Tabelle zu Zeilenkarten und .tbl-wrap gibt Rahmen
    # und Fläche ab — ein Schatten ohne Fläche schwebte frei über der Seite.
    karten = app[app.index(".tbl-wrap:has(table.dt-cards){"):]
    assert "box-shadow:none;" in karten[: karten.index("}")]

    # Statistik: die beiden Karten (.card trägt app-weit KEINEN Schatten, die
    # übrigen Kartenflächen sind Formularflächen). Die Zeitraum-Auswahl bleibt
    # flach, sie ist ein Bedienelement.
    statistik = _css("pages/statistik.css")
    assert ".card{box-shadow:var(--shadow);}" in statistik
    seg = statistik[statistik.index("  .seg{"):]
    assert "box-shadow" not in seg[: seg.index("}")]

    # Einstellungen und Housekeeping: die Sprungleiste.
    nav = app[app.index(".settings-nav{"):]
    assert "box-shadow:var(--shadow);" in nav[: nav.index("}")]
