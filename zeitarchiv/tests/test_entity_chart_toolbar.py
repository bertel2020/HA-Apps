"""Die Werkzeugleiste des Entitäts-Charts.

Sie trägt zwei Sorten von Bedienelementen, und die Datei hält fest, dass die
Trennung sichtbar bleibt: links steht, WELCHER Ausschnitt gezeigt wird
(Zeitraum, Blättern, Markierungen), rechts, WIE er gezeigt wird (Vergleichen,
Optionen).

Der frühere „Jetzt"-Knopf ist entfallen. Er belegte dauerhaft Platz und war
die meiste Zeit deaktiviert — seine Funktion liegt jetzt als Zweitfunktion auf
der schon aktiven Zeitraum-Stufe, dieselbe Geste wie im Energiedashboard.
"""

from _paths import APP, page_text


ENTITY = page_text("entity_detail.html")
# Für Strukturaussagen nur das Template: page_text() hängt CSS und JS an, in
# denen dieselben Namen (compareMenuOpen …) erneut vorkommen.
TEMPLATE = (APP / "templates/entity_detail.html").read_text(encoding="utf-8")
# energiedashboard.js liegt unter static/js/ (nicht static/js/pages/) und
# wird deshalb nicht von page_text() eingesammelt.
ENERGIE = (APP / "static/js/energiedashboard.js").read_text(encoding="utf-8")


def test_clicking_the_active_range_again_jumps_back_to_now() -> None:
    """Die Geste, die den „Jetzt"-Knopf ersetzt.

    Ohne sie ist ein zweiter Klick auf die aktive Stufe folgenlos — und es gäbe
    gar keinen Weg mehr zurück in die laufende Periode außer sich Schritt für
    Schritt vorzublättern.
    """
    zweig = ENTITY.split("setRange(key) {")[1][:900]
    assert "if (key === this.range) {" in zweig
    assert "if (this.offset !== 0) this.goToNow();" in zweig


def test_the_old_now_button_is_gone() -> None:
    """Sonst stünden beide Wege nebeneinander, und der Knopf wäre wieder das,
    was er vorher war: ein meist deaktiviertes Feld in der Leiste."""
    assert 'goToNow()">Jetzt<' not in ENTITY
    assert ">Jetzt<" not in ENTITY


def test_the_second_function_is_discoverable() -> None:
    """Eine Zweitfunktion ohne Hinweis ist keine. Der title erscheint nur,
    solange es etwas zu tun gibt — an einer Ansicht, die ohnehin auf „jetzt"
    steht, wäre er eine Lüge."""
    assert "'Zurück zur laufenden Periode' : ''" in ENTITY


def test_the_energy_dashboard_has_the_same_gesture() -> None:
    """Die Vorlage. Bricht sie dort weg, ist die Begründung hier hinfällig —
    dann stünde in zwei Charts dieselbe Leiste mit zwei Bedienungen."""
    assert "if (key === this.range && this.offset === 0) return;" in ENERGIE


def test_view_options_sit_at_the_right_edge_as_one_group() -> None:
    """Vergleichen und Optionen bilden die zweite Gruppe. Der Abstand
    dazwischen macht aus zwei Gruppen zwei Gedanken.

    Beide MÜSSEN in einem gemeinsamen Behälter stehen. Ein margin-left:auto auf
    dem ersten von zweien wurde am laufenden Stand gemessen und war falsch: der
    erste verschluckt den ganzen freien Platz, der zweite bricht in die nächste
    Zeile — die Gruppe war auseinandergerissen statt zusammengerückt.
    """
    assert 'class="toolbar-right"' in ENTITY
    assert 'class="menu-wrap toolbar-right"' not in ENTITY
    assert ".toolbar-right{display:flex;align-items:center;gap:8px;}" in ENTITY
    assert ".toolbar-right{margin-left:auto;}" in ENTITY
    # Beide Menüs liegen im Behälter, bevor er wieder zugeht: die Tiefe muss
    # zwischen ihnen durchgehend über null bleiben.
    gruppe = TEMPLATE.split('<div class="toolbar-right">')[1]
    bis_optionen = gruppe.split('<div class="menu-wrap" @click.outside="optionsMenuOpen')[0]
    tiefe = 1 + bis_optionen.count("<div") - bis_optionen.count("</div>")
    assert 'compareMenuOpen = false' in bis_optionen
    assert tiefe == 1, f"Optionen steht nicht mehr im Behälter (Tiefe {tiefe})"


def test_the_right_alignment_stops_before_the_toolbar_wraps() -> None:
    """Unter 641 px bricht die Leiste ohnehin um; eine rechtsbündige Restzeile
    läse sich als Versehen, nicht als Gruppierung."""
    regel = ENTITY.split(".toolbar-right{margin-left:auto;}")[0]
    assert "@media (min-width:641px){" in regel.split("/*")[-1] or "min-width:641px" in regel[-400:]
