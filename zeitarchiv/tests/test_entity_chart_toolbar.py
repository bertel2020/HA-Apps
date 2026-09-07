"""Werkzeugleiste und Optionen-Menü des Entitäts-Charts.

Das Optionen-Menü steht im Chart-Editor in weiten Teilen genauso; wo eine
Zusage beide Seiten betrifft, prüft sie hier auch beide.

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
EDITOR = page_text("chart_editor.html")
EDITOR_TEMPLATE = (APP / "templates/chart_editor.html").read_text(encoding="utf-8")
APP_CSS = (APP / "static/css/app.css").read_text(encoding="utf-8")


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

    Beide Seiten tragen dieselbe Leiste, deshalb prüft die Zusage beide.
    """
    assert ".toolbar-right{display:flex;align-items:center;gap:8px;}" in APP_CSS
    assert ".toolbar-right{margin-left:auto;}" in APP_CSS
    for name, vorlage in (("entity_detail", TEMPLATE), ("chart_editor", EDITOR_TEMPLATE)):
        assert 'class="toolbar-right"' in vorlage, name
        assert 'class="menu-wrap toolbar-right"' not in vorlage, name
        # Beide Menüs liegen im Behälter, bevor er wieder zugeht: die Tiefe muss
        # zwischen ihnen durchgehend über null bleiben.
        gruppe = vorlage.split('<div class="toolbar-right">')[1]
        bis_optionen = gruppe.split('<div class="menu-wrap" @click.outside="optionsMenuOpen')[0]
        tiefe = 1 + bis_optionen.count("<div") - bis_optionen.count("</div>")
        assert "compareMenuOpen = false" in bis_optionen, name
        assert tiefe == 1, f"{name}: Optionen steht nicht mehr im Behälter (Tiefe {tiefe})"


def test_the_toolbar_group_lives_in_the_shared_stylesheet() -> None:
    """Seit der Chart-Editor dieselbe Gruppe trägt, gehört die Regel nicht mehr
    in eine der beiden Seiten-Dateien — sonst wäre sie doppelt gepflegt und
    liefe auseinander."""
    for seite in ("entity_detail", "chart_editor"):
        css = (APP / f"static/css/pages/{seite}.css").read_text(encoding="utf-8")
        assert ".toolbar-right" not in css, seite


def test_the_right_alignment_stops_before_the_toolbar_wraps() -> None:
    """Unter 641 px bricht die Leiste ohnehin um; eine rechtsbündige Restzeile
    läse sich als Versehen, nicht als Gruppierung."""
    davor = APP_CSS.split(".toolbar-right{margin-left:auto;}")[0]
    assert davor.rstrip().endswith("@media (min-width:641px){")


def test_the_legend_metrics_are_chips_on_both_pages() -> None:
    """`.filter-chip` ist der app-weite Standard für Chip-Mehrfachauswahl (so
    ausdrücklich im Kommentar in `_energiedashboard_setup.html`). Die
    Kennzahlen-Auswahl war als einzige eine Liste aus Kästchen mit Text daneben
    — ohne Grund, sie ist genau dasselbe Muster."""
    for name, quelle in (("entity_detail", ENTITY), ("chart_editor", EDITOR)):
        block = quelle.split("legend-metrics-row")[1][:700]
        assert 'class="filter-chip"' in block, name
        assert "legend-metric-check" not in quelle, name


def test_the_metric_chips_are_smaller_inside_the_menu() -> None:
    """Das Menü-Popover ist 290 px breit; in voller Chip-Größe passten zwei je
    Zeile. Die Verkleinerungswerte sind dieselben wie bei `.menu-row .seg
    button`, damit die Zeilen im selben Menü nicht unterschiedlich hoch
    aufragen."""
    assert ".legend-metrics-row .filter-chip span{padding:4px 9px;" in APP_CSS
    assert "font-size:calc(11.5px * var(--font-scale, 1));}" in APP_CSS.split(
        ".legend-metrics-row .filter-chip span{"
    )[1][:120]


def test_the_right_hand_menus_open_leftwards_but_only_where_they_must() -> None:
    """`.menu-popover` hängt sonst mit `left:0` am Anker und ist 290 px breit.
    Seit Vergleichen/Optionen rechtsbündig stehen, ragte das Optionen-Menü aus
    dem Fenster — gemessen bis 1163 px in einem 1000 px breiten Fenster, samt
    waagerechtem Rollbalken.

    Die Gegenrichtung ist unterhalb von 641 px genauso falsch: dort stehen die
    Knöpfe wieder links, und ein rechts verankertes Popover schnitt links ab
    (gemessen: linke Kante bei −45 px). Deshalb dieselbe Bedingung wie für die
    Rechtsbündigkeit selbst.
    """
    regel = ".toolbar-right .menu-popover{left:auto;right:0;}"
    assert regel in APP_CSS
    davor = APP_CSS.split(regel)[0]
    assert davor.rstrip().endswith("@media (min-width:641px){"), \
        "die Rechts-Verankerung muss an dieselbe Breite gebunden sein wie die Rechtsbündigkeit"
