"""Zoom im Entitäts-Chart (dataZoom).

Der Zoom ist eine Lupe INNERHALB des geladenen Zeitraums, kein zweiter Weg,
den Zeitraum zu wechseln: `entity_detail.js` nagelt die x-Achse hart auf das
Abfragefenster fest (min/max aus windowStart/periodEnd), der Zoom wählt nur
einen Ausschnitt darin. Deshalb gibt es dazu weder eine Serveranfrage noch
einen URL- oder Optionszustand.

Die Zusagen hier sind fast alle über die *Konfiguration*, nicht über die
Optik — und das mit Absicht: die Voreinstellungen von ECharts sind an jeder
einzelnen dieser Stellen die falschen, und wer sie versehentlich
zurückdreht, merkt es an keinem Test, der nur prüft, dass irgendein
dataZoom existiert.
"""

from _paths import APP, page_text

ENTITY = page_text("entity_detail.html")
EDITOR = page_text("chart_editor.html")
ENERGIE = page_text("energiedashboard.html")
STATISTIK = page_text("statistik.html")
DASHBOARD = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")


def test_the_wheel_alone_still_scrolls_the_page() -> None:
    """Die wichtigste Zusage des ganzen Features.

    `zoomOnMouseWheel: true` (die naheliegende Fassung) lässt den Chart das
    Scrollrad kapern: wer nur an ihm vorbeiscrollen will, zoomt stattdessen.
    Beide Schalter zusammen halten das Rad bei der Seite — Strg macht daraus
    eine bewusste Geste, und ein Trackpad-Pinch erzeugt genau diese
    Kombination von sich aus.
    """
    assert "zoomOnMouseWheel: 'ctrl'" in ENTITY
    assert "moveOnMouseWheel: false" in ENTITY
    assert "zoomOnMouseWheel: true" not in ENTITY


def test_dragging_pans_only_with_ctrl_so_phones_keep_their_scroll() -> None:
    """zrender setzt eine Ein-Finger-Berührung in Mausereignisse um.

    Mit `moveOnMouseMove: true` würde deshalb auf dem Telefon jeder senkrechte
    Wisch über dem Chart als Schwenk gelten, und preventDefaultMouseMove
    (Voreinstellung true) hielte die Seite dabei an. 'ctrl' gibt es auf einem
    Touchscreen nicht — dort bleibt der Wisch der Seite, gezoomt wird mit zwei
    Fingern über den Pinch-Handler, den diese Schalter nicht berühren.
    """
    assert "moveOnMouseMove: 'ctrl'" in ENTITY
    assert "moveOnMouseMove: true" not in ENTITY


def test_the_zoom_follows_the_y_axis_setting_instead_of_fighting_it() -> None:
    """filterMode entscheidet, ob der Zoom die y-Achse mitskaliert.

    Die Seite hat mit "y-Achse fest/dynamisch" schon einen Schalter dafür. Ein
    fest verdrahtetes 'filter' würde die Achse auch dann nachziehen, wenn der
    Nutzer sie ausdrücklich an die Null gebunden hat; ein fest verdrahtetes
    'none' nähme der Einstellung "dynamisch" ihre Wirkung im Ausschnitt.
    """
    assert "(this.dynamicYAxis && this.chartType !== 'bar') ? 'filter' : 'none'" in ENTITY


def test_zoom_appears_only_where_there_is_more_data_than_pixels() -> None:
    """Unterhalb der Schwelle verdeckt kein Punkt einen anderen.

    Die Schwelle liest die tatsächlich geladenen Punkte statt einer Liste
    erlaubter Zeiträume: derselbe Zeitraum braucht je nach Melderhythmus der
    Entität unterschiedliche Antworten.
    """
    assert "const ZOOM_MIN_POINTS = 200;" in ENTITY
    assert "this.points.length > ZOOM_MIN_POINTS" in ENTITY
    # Genau einmal ausgewertet: der Hinweis unter dem Chart und die
    # dataZoom-Angabe müssen dieselbe Antwort geben. Stünde die Bedingung
    # zweimal da, könnte eine Änderung eine der beiden Stellen vergessen — und
    # der Chart böte einen Zoom an, den der Text darunter verneint.
    assert ENTITY.count("ZOOM_MIN_POINTS") == 2, "Deklaration + genau eine Auswertung"
    assert "if (this.zoomAvailable) {" in ENTITY


def test_the_hint_under_the_chart_answers_why_the_chip_is_grey() -> None:
    """Ein deaktivierter Knopf ohne Begründung ist eine Sackgasse.

    Der Hinweis nennt deshalb beide Fälle: wo sich zoomen lässt, wie es geht;
    wo nicht, warum es nicht nötig ist. `hint-status` und nicht hinter dem
    Info-Knopf, weil die Punktzahl ein Datenzustand ist und keine Erklärung
    (siehe "Hinweistexte: drei Rollen" in docs/frontend.md).
    """
    assert 'class="hint hint-status zoom-hint" x-show="points.length" x-text="zoomHint"' in ENTITY
    assert "get zoomHint()" in ENTITY
    assert "alle einzeln sichtbar" in ENTITY
    assert "mit Strg und Mausrad einen Ausschnitt vergrößern" in ENTITY
    # Unter der Karte, nicht darin: er sagt etwas über das Bedienen der
    # Ansicht, während in der Karte die Legende steht, die die Werte selbst
    # beschreibt.
    # Über die Verschachtelungstiefe statt über die Textreihenfolge geprüft:
    # eine Stellungsangabe ("kommt nach diesem Schnipsel") bricht bei jeder
    # Umformatierung, die Tiefe nicht.
    _, ab_karte = ENTITY.split('<div class="card">', 1)
    bis_hinweis = ab_karte.split('class="hint hint-status zoom-hint"', 1)[0]
    tiefe = 1 + bis_hinweis.count("<div") - bis_hinweis.count("</div>")
    assert tiefe == 0, f"Hinweis steht in der Karte (Tiefe {tiefe}), nicht darunter"
    # "1 Datenpunkt, alle einzeln sichtbar" wäre falsches Deutsch, und den
    # einzelnen Punkt gibt es wirklich (Stundenansicht einer selten meldenden
    # Entität).
    assert "einzeln ? '' : ', alle einzeln sichtbar'" in ENTITY
    # Der Zeitstrahl darf nicht mit einer Punktzahl argumentieren: dort sind es
    # oft eine Handvoll Segmente, und "3 Datenpunkte — zoomen möglich" würde
    # der Regel widersprechen, die der Text daneben aufstellt.
    zeitstrahl_satz = ENTITY.split("if (this.chartType === 'timeline') {\n            return `")[1][:120]
    assert "Datenpunkt" not in zeitstrahl_satz


def test_the_timeline_gets_the_zoom_regardless_of_how_few_segments_it_has() -> None:
    """Beim Zeitstrahl geht es nicht um Komfort, sondern um Sichtbarkeit.

    Ein Segment wird von seinem Anfang bis zu seinem Ende als Rechteck
    gezeichnet. Bei Zeitraum "Monat" auf rund 900 px entspricht ein Pixel etwa
    48 Minuten — jedes kürzere Schaltereignis ist schmaler als ein Pixel und
    praktisch unsichtbar. Drei Segmente können also genauso zoombedürftig sein
    wie dreitausend, die Punktzahl sagt darüber nichts.
    """
    # Auf die Methodendefinition aufgeteilt, nicht auf den Aufruf weiter oben
    # (`this.renderTimeline(fmt);`) — sonst prüft der Test den Linien-/Balken-
    # Zweig und ginge stillschweigend durch.
    zeitstrahl = ENTITY.split("\n        renderTimeline(fmt) {")[1]
    assert "dataZoom: [zoomConfig('none')]" in zeitstrahl
    assert "ZOOM_MIN_POINTS" not in zeitstrahl


def test_the_zoom_component_is_assigned_conditionally_never_set_to_undefined() -> None:
    """Ein explizit auf undefined gesetzter Komponenten-Key reißt beim internen
    Normalisieren den ganzen Render-Zyklus ab — nicht nur die Komponente fehlt
    dann, auch das Tooltip rendert nie mehr. Dieselbe Falle ist bei `legend`
    direkt daneben schon einmal zugeschnappt."""
    assert "dataZoom: zoomEnabled ? " not in ENTITY
    assert ": undefined,\n" not in ENTITY.split("option.dataZoom = [")[1][:200]


def test_the_zoom_is_not_persisted_anywhere() -> None:
    """Ein Ausschnitt ist eine Aussage über die letzten dreißig Sekunden.

    "Rollierend", "Rohwerte" und "Diagrammtyp" beschreiben dagegen die
    Entität und werden deshalb gespeichert. Landete zoomRange in
    saveChartOptions() oder in der URL, müsste es htmx-Swaps und die
    Zurück-Links überleben — viel Mechanik für einen flüchtigen Blick.
    """
    optionen = ENTITY.split("saveChartOptions(")[1][:1200]
    assert "zoomRange" not in optionen
    assert "zoom" not in ENTITY.split("_syncUrl")[-1][:600].lower()


def test_the_reset_chip_never_leaves_the_layout() -> None:
    """Die Toolbar hält jeden Control an einem festen Platz und schaltet ihn nur
    aktiv/inaktiv (siehe der Kommentar zu .toolbars in entity_detail.css) —
    sonst rutschen die Nachbarn bei jedem Zustandswechsel woandershin. Ein per
    x-show erscheinender Chip wäre genau der Fall, gegen den diese Regel
    geschrieben wurde. Die feste Mindestbreite gehört dazu: die Beschriftung
    wechselt zwischen "Ausschnitt" und einer Zeitspanne."""
    chip = ENTITY.split('class="chip chip-zoom"')[1][:400]
    assert ':disabled="!zoomRange"' in chip
    assert "x-show" not in chip
    assert "min-width:152px" in ENTITY


def test_no_other_chart_in_the_app_gets_a_zoom() -> None:
    """Die Abgrenzung ist Teil des Vorschlags, nicht ein noch nicht erledigter
    Rest: eine Kachel ist ein Blickfang und kein Werkzeug (ein Mini-Chart, den
    man beim Vorbeiscrollen verstellt, wäre ein Defekt), Sankey und Donut haben
    keine Zeitachse, die Tag-mal-Stunde-Heatmap ist kategorial, und der
    Monatsverlauf im Bericht hat zwölf Balken."""
    for name, quelle in (
        ("dashboard-tiles.js", DASHBOARD),
        ("chart_editor", EDITOR),
        ("energiedashboard", ENERGIE),
        ("statistik", STATISTIK),
    ):
        assert "dataZoom" not in quelle, name
