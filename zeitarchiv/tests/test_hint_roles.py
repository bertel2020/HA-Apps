"""Die drei Sorten Text, die lange alle `.hint` hießen.

Bis 0.83.1 trug jeder erklärende Satz, jede Warnung und jede Statuszeile
dieselbe Klasse — 121 Stück. Damit war nicht adressierbar, was hinter einen
Info-Knopf verschwinden darf und was ungefragt dastehen muss; genau diese
Unterscheidung ist der Zweck der Rollenklassen.

Die Rollen kommen ZUSÄTZLICH zur Kontextklasse (`hint`, `tbl-hint`,
`settings-compact-hint`), weil beide Achsen unabhängig sind: der Kontext
bestimmt Größe und Abstände, die Rolle bestimmt, ob der Text weggeklappt
werden darf.
"""

from __future__ import annotations

import re

from _paths import APP_CSS, TEMPLATES


ROLLEN = ("hint-warn", "hint-status")
KONTEXTE = ("hint", "tbl-hint", "settings-compact-hint", "bgproc-hint")


def _templates() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.glob("*.html"))}


def _klassenlisten(quelle: str) -> list[str]:
    """Jede class="…"-Angabe, die überhaupt eine Hinweisklasse enthält."""
    return [
        m.group(1)
        for m in re.finditer(r'class="([^"]*)"', quelle)
        if any(k in m.group(1).split() for k in KONTEXTE + ROLLEN)
    ]


def test_every_warning_is_marked_as_one() -> None:
    """Die sechs Sätze, die eine nicht umkehrbare Folge nennen.

    Sie stehen hier namentlich, weil ein vergessenes `hint-warn` niemandem
    auffällt: der Text sieht unverändert aus und verschwindet erst dann
    unbemerkt, wenn jemand später „alle erklärenden Hinweise" wegklappt.
    """
    warnungen = {
        "_settings_backup_schedule_form.html": "Backups liegen nur lokal",
        "_settings_purge_form.html": "Entfernt markierte Datensätze endgültig",
        "_settings_retention_form.html": "Löscht Werte nach Ablauf ihrer Aufbewahrungsfrist",
    }
    quellen = _templates()
    for name, satz in warnungen.items():
        stelle = quellen[name].index(satz)
        davor = quellen[name].rfind("<p", 0, stelle)
        assert "hint-warn" in quellen[name][davor:stelle], f"{name}: {satz}"

    # Beide Diagnose-Werkzeuge warnen vor dem, was sie aufzeichnen.
    debug = quellen["_settings_debug_tools.html"]
    assert debug.count("hint-warn") == 2


def test_no_warning_is_also_a_status_line() -> None:
    """Eine Rolle je Text — sonst ist die Frage „darf das weg?" nicht
    beantwortbar."""
    for name, quelle in _templates().items():
        for klassen in _klassenlisten(quelle):
            gesetzt = [r for r in ROLLEN if r in klassen.split()]
            assert len(gesetzt) <= 1, f"{name}: {klassen}"


def test_a_role_never_stands_without_its_context_class() -> None:
    """`hint-warn` allein hätte keine Größe und keine Abstände — die kommen
    aus der Kontextklasse."""
    for name, quelle in _templates().items():
        for klassen in _klassenlisten(quelle):
            teile = klassen.split()
            if any(r in teile for r in ROLLEN):
                assert any(k in teile for k in KONTEXTE), f"{name}: {klassen}"


def test_status_card_labels_are_no_longer_hints() -> None:
    """Die Beschriftung über einer Kartenzahl war nie ein Hinweis, sondern
    Teil der Karte — sie hieß nur so, weil sie dieselbe Schrift brauchte."""
    quellen = _templates()
    karten = [
        "_settings_verbindung_form.html",
        "_settings_retention_form.html",
        "_settings_backup_schedule_form.html",
        "_settings_storage_index_form.html",
        "housekeeping.html",
    ]
    treffer = 0
    for name in karten:
        quelle = quellen[name]
        for m in re.finditer(r'<div class="status-card[^"]*">(.*?)</div>', quelle, re.S):
            assert 'class="hint"' not in m.group(1), name
        treffer += quelle.count('class="status-card-label"')
    assert treffer == 16, f"16 Kartenbeschriftungen erwartet, {treffer} gefunden"


def test_the_roles_themselves_stay_a_pure_annotation() -> None:
    """Die Rollen sind Auszeichnung, keine Gestaltung.

    Wer `hint-warn` vergibt, entscheidet damit nur: dieser Text klappt nicht
    weg. Wie er aussieht, entscheidet die ausdrückliche Marke
    `hint-warn-strong` — sonst wäre jede künftige Auszeichnung nebenbei eine
    Gestaltungsänderung.
    """
    css = re.sub(r"/\*.*?\*/", "", APP_CSS.read_text(encoding="utf-8"), flags=re.S)
    assert ".hint,.hint-warn,.hint-status{" in css

    # Ohne die gemeinsame Regel und ohne die ausdrückliche Marke darf keine
    # Rolle mehr als Selektor auftauchen.
    rest = css.replace(".hint,.hint-warn,.hint-status{", ".hint{")
    rest = rest.replace(".hint-warn-strong", "")
    for rolle in ROLLEN:
        assert f".{rolle}" not in rest, f"{rolle} wird irgendwo gestaltet"


def test_only_the_irreversible_warnings_are_marked() -> None:
    """Drei der fünf Warnungen kündigen einen nicht umkehrbaren Verlust an und
    standen dafür in --ink-faint, der Farbe für Nebensächliches.

    Die beiden Diagnose-Werkzeuge bleiben unmarkiert: sie warnen vor dem, was
    sie aufzeichnen, nicht vor Datenverlust. Diese Grenze steht hier, weil sie
    beim nächsten Blick auf die Liste sonst willkürlich wirkt.
    """
    quellen = _templates()
    markiert = {
        name: quelle.count("hint-warn-strong")
        for name, quelle in quellen.items()
        if "hint-warn-strong" in quelle
    }
    assert markiert == {
        "_settings_backup_schedule_form.html": 1,  # Backups liegen nur lokal
        "_settings_purge_form.html": 1,  # entfernt endgültig
        "_settings_retention_form.html": 1,  # löscht nach Ablauf, endgültig
    }
    assert "hint-warn-strong" not in quellen["_settings_debug_tools.html"]

    # Die Marke steht nie allein — ohne `hint-warn` könnte der Text wegklappen.
    for name, quelle in quellen.items():
        for klassen in _klassenlisten(quelle):
            if "hint-warn-strong" in klassen.split():
                assert "hint-warn" in klassen.split(), f"{name}: {klassen}"


def test_the_marked_warnings_stay_a_warning_not_an_error() -> None:
    """--danger ist in dieser App die Farbe für Fehler (status-card-danger).
    Eine Warnung, die noch gar nichts kaputt gemacht hat, gehört auf
    --warning — sonst stumpft die stärkere Farbe ab."""
    css = APP_CSS.read_text(encoding="utf-8")
    regel = re.search(r"\.hint-warn-strong\{([^}]*)\}", css)
    assert regel is not None
    assert "var(--warning)" in regel.group(1)
    assert "var(--danger)" not in regel.group(1)
    assert "var(--ink-muted)" in regel.group(1), "der Kontrast war der halbe Punkt"


def test_the_card_label_keeps_the_look_it_inherited_from_hint() -> None:
    """Vorher stand `.status-card .hint` da und erbte Schriftgröße, Zeilenhöhe
    und Farbe von `.hint`. Ohne diese Angaben wäre der Ausbau der Klasse eine
    stille Gestaltungsänderung."""
    css = APP_CSS.read_text(encoding="utf-8")
    regel = re.search(r"\.status-card-label\{([^}]*)\}", css)
    assert regel is not None
    for angabe in ("display:block", "12.5px", "line-height:1.55", "var(--ink-faint)"):
        assert angabe in regel.group(1), angabe
    assert ".status-card .hint{" not in css
