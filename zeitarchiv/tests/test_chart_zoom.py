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


def test_the_hint_under_the_chart_says_whether_zooming_is_possible() -> None:
    """Ohne ihn gäbe es keinen Hinweis darauf, dass es den Zoom überhaupt gibt:
    der Ausschnitts-Chip erscheint erst, wenn schon gezoomt ist.

    Der Hinweis nennt deshalb beide Fälle: wo sich zoomen lässt, wie es geht;
    wo nicht, warum es nicht nötig ist. `hint-status` und nicht hinter dem
    Info-Knopf, weil die Punktzahl ein Datenzustand ist und keine Erklärung
    (siehe "Hinweistexte: drei Rollen" in docs/frontend.md).
    """
    assert 'class="hint hint-status zoom-hint" x-show="points.length" x-text="zoomHint"' in ENTITY
    assert "get zoomHint()" in ENTITY
    assert "alle einzeln sichtbar" in ENTITY
    assert "mit Strg und Mausrad zoomen" in ENTITY
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


def test_a_region_can_be_dragged_open_with_shift() -> None:
    """Die Geste war noch frei: schlichtes Ziehen ist unbelegt, Schwenken liegt
    auf Strg+Ziehen, Zoomen auf Strg+Rad. Umschalt fügt sich in dieselbe Regel
    ein — Taste halten heißt „ich meine den Chart" — und es gibt sie auf einem
    Touchscreen nicht, dort bleibt der Wisch also der Seite, ohne dass es dafür
    eine Sonderregel bräuchte."""
    assert "key: 'brush'," in ENTITY
    assert "brushType: 'lineX', brushMode: 'single'" in ENTITY
    # Ausschalten braucht brushType:false — ein fehlendes brushOption ließe den
    # Modus stehen.
    assert "{brushType: false}" in ENTITY
    assert "e.key !== 'Shift'" in ENTITY


def test_the_selection_goes_through_brush_not_through_the_toolbox() -> None:
    """Am laufenden Chart gemessen: `toolbox.feature.dataZoom` mit
    `show: false` ist wirkungslos — ohne sichtbare Werkzeugleiste legt ECharts
    deren View nicht an, `takeGlobalCursor` mit `dataZoomSelect` läuft ins
    Leere. Über die brush-Komponente ist der Modus dagegen nachweislich scharf.
    """
    # Der Name darf im ERKLÄRENDEN Kommentar stehen — nur benutzt werden darf
    # er nicht, sonst wäre der gemessene Fehlweg wieder eingebaut.
    assert "key: 'dataZoomSelect'" not in ENTITY
    assert "function selectBrush()" in ENTITY
    assert "brushType: 'lineX'" in ENTITY


def test_the_foreign_toolbox_icons_are_switched_off_twice() -> None:
    """Die zweite Stelle ist am laufenden Chart aufgefallen und war nicht
    naheliegend: `toolbox: []` in der brush-Konfiguration sagt nur, welche
    brush-Knöpfe die Werkzeugleiste zeigt. Die Werkzeugleiste selbst legt
    ECharts trotzdem an — sie erschien mit vier fremden Icons genau dort, wo
    der Ausschnitts-Chip sitzt."""
    assert "toolbox: []," in ENTITY
    assert "option.toolbox = {show: false};" in ENTITY
    assert "toolbox: {show: false}," in ENTITY


def test_a_click_without_dragging_does_not_zoom_to_nothing() -> None:
    """Und die gezogene Fläche verschwindet sofort wieder — sonst bliebe das
    Rechteck als graue Fläche über dem Chart liegen, obwohl es seine Aufgabe
    erfüllt hat."""
    handler = ENTITY.split("chartInstance.on('brushEnd'")[1][:900]
    assert "type: 'brush', areas: []" in handler
    assert "if (!spanne || !(spanne[1] > spanne[0])) return;" in handler
    assert "type: 'dataZoom', startValue: spanne[0], endValue: spanne[1]" in handler


def test_releasing_shift_mid_drag_does_not_cut_the_rectangle() -> None:
    """Wer die Taste vor der Maustaste loslässt, hinterließe sonst einen halb
    gezogenen Rahmen. Und ein Fensterwechsel bei gedrückter Taste bekommt nie
    ein keyup — der Chart bliebe dauerhaft im Auswahlmodus."""
    assert "const nachziehen = () => { if (!zieht) setzeAuswahl(taste); };" in ENTITY
    assert "getZr().on('mouseup'" in ENTITY
    assert "window.addEventListener('blur'" in ENTITY


def test_the_hint_names_the_new_gesture() -> None:
    """Ohne Hinweis ist eine Tastenkombination unauffindbar."""
    assert "mit Umschalt einen Bereich aufziehen" in ENTITY


def test_the_reset_chip_sits_in_the_chart_not_in_the_toolbar() -> None:
    """Gemessen, nicht gefühlt: in der Werkzeugleiste brauchte sie mit dem Chip
    1003 von 952 verfügbaren Pixeln und brach um, ohne ihn 843. Er war dabei
    das Element mit dem seltensten Anlass — sichtbar immer, gemeint nur während
    eines Zooms.

    Im Chart überdeckt er keine Daten: ECharts beginnt erst bei grid.top
    (36 px) zu zeichnen, und dieser Streifen ist rechts leer.
    """
    template = (APP / "templates/entity_detail.html").read_text(encoding="utf-8")
    leiste = template.split('<div class="toolbar">')[1].split('<div class="card">')[0]
    assert "chip-zoom" not in leiste, "der Chip gehört nicht mehr in die Werkzeugleiste"
    assert 'class="chip chip-zoom active"' in template
    assert ".chart-wrap{position:relative;}" in ENTITY
    assert "position:absolute;top:2px;right:6px" in ENTITY


def test_the_chip_only_exists_while_something_is_zoomed() -> None:
    """Kein deaktivierter Zustand mehr: er wäre unerreichbar, weil der Chip
    ohne Ausschnitt gar nicht erscheint. Und ohne Nachbarn braucht die
    wechselnde Beschriftung keine feste Mindestbreite mehr — die gab es nur,
    damit sie die Leiste nicht bei jeder Radbewegung neu umbrechen lässt."""
    chip = ENTITY.split('class="chip chip-zoom active"')[1][:300]
    assert 'x-show="zoomRange"' in chip
    assert "x-cloak" in chip, "sonst blitzt er vor Alpines Initialisierung auf"
    assert ":disabled" not in chip
    assert "min-width:152px" not in ENTITY


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
