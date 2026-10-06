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

from _paths import APP, page_text, template_text


ENTITY = page_text("entity_detail.html")
# Für Strukturaussagen nur das Template: page_text() hängt CSS und JS an, in
# denen dieselben Namen (compareMenuOpen …) erneut vorkommen.
TEMPLATE = template_text("entity_detail.html")
# energiedashboard.js liegt unter static/js/ (nicht static/js/pages/) und
# wird deshalb nicht von page_text() eingesammelt.
ENERGIE = (APP / "static/js/energiedashboard.js").read_text(encoding="utf-8")
EDITOR = page_text("chart_editor.html")
EDITOR_TEMPLATE = template_text("chart_editor.html")
DETAIL_JS = (APP / "static/js/pages/entity_detail.js").read_text(encoding="utf-8")
EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
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
    assert "t('Zurück zur laufenden Periode') : ''" in ENTITY


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
    assert ".toolbar-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}" in APP_CSS
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


def test_the_year_comparison_is_offered_only_where_it_says_something_new() -> None:
    """Bei „Jahr" standen zwei Zeilen mit demselben Wort „Vorjahr" untereinander,
    bei „Dekade" eine, die das Jahrzehnt um ein Jahr verschiebt und damit den
    Zeitraum überlappt, gegen den sie vergleicht. Beides ist gemessen in
    tests/test_query.py."""
    for name, js in (("entity_detail", DETAIL_JS), ("chart_editor", EDITOR_JS)):
        assert "const COMPARE_YEAR_RANGES = ['hour', 'day', 'week', 'month'];" in js, name
    for name, vorlage in (("entity_detail", TEMPLATE), ("chart_editor", EDITOR_TEMPLATE)):
        zeile = [z for z in vorlage.splitlines() if "setCompareMode('year')" in z]
        assert len(zeile) == 1, name
        assert 'x-show="compareYearAvailable"' in zeile[0], name
        # Die Vorperiode-Zeile bleibt IMMER sichtbar — sonst stünde bei „Jahr"
        # nur noch „Aus" im Menü.
        vorperiode = [z for z in vorlage.splitlines() if "setCompareMode('previous')" in z]
        assert len(vorperiode) == 1 and "x-show" not in vorperiode[0], name


def test_a_stale_year_mode_does_not_survive_a_range_switch() -> None:
    """Von „Monat, Vorjahresmonat" auf „Jahr" umschalten ließ den Modus
    stehen. Im Chart-Editor liefe er direkt in die nächste Abfrage (dort
    bleibt der Vergleich beim Wechsel an), auf der Entitätsseite käme er in
    der Vorbelegung von saveAsChartUrl wieder heraus."""
    for name, js in (("entity_detail", DETAIL_JS), ("chart_editor", EDITOR_JS)):
        # Bis zum Ende der Methode, nicht auf gut Glück n Zeichen weit: die
        # beiden setRange() sind unterschiedlich lang kommentiert.
        zweig = js.split("setRange(key) {")[1].split("\n        },")[0]
        assert "if (!compareYearAvailable(key)) this.compareMode = 'previous';" in zweig, name


def test_a_saved_chart_cannot_start_in_a_mode_its_range_does_not_offer() -> None:
    """Der Chart-Editor bekommt compare_mode vom Server. Ein Chart, das mit
    „Dekade" + Vorjahresvergleich gespeichert wurde, hätte sonst keine aktive
    Zeile im Menü und einen Knopf, dessen Wort nirgends mehr anwählbar ist."""
    assert "compareMode: compareYearAvailable(RANGE_KEY) ? COMPARE_MODE : 'previous'," in EDITOR_JS


def test_years_compare_is_not_a_separate_menu_option() -> None:
    """Ein früherer Anlauf gab "Jahre" als dritte, eigenständige Popover-Zeile
    neben "Aus"/"Vorjahr" — zwei Optionen, die beide irgendwie "Vorjahr"
    meinten, aber komplett verschieden aussahen (Schattenlinie vs. Balkenpaare
    mit eigener Legende), war die eigentliche Quelle der Unübersichtlichkeit.
    Seither gibt es nur noch "Aus"/"Vorjahr" im Menü; Laufsumme/Soll sind
    Erweiterungen VON "Vorjahr" (siehe test_previous_year_extension_row_*),
    kein dritter Menüpunkt."""
    assert "setCompareMode('years')" not in EDITOR_TEMPLATE
    assert 'x-model.number="compareYearA"' not in EDITOR_TEMPLATE
    assert 'x-model.number="compareYearB"' not in EDITOR_TEMPLATE
    assert "compareYearOptions" not in EDITOR_JS
    assert "'years'" not in EDITOR_JS
    vorperiode = [z for z in EDITOR_TEMPLATE.splitlines() if "setCompareMode('previous')" in z]
    assert len(vorperiode) == 1 and "x-show" not in vorperiode[0]


def test_previous_year_extension_row_gates_on_previous_and_year_range() -> None:
    """Laufsumme/Soll Entität stehen rechtsbündig in derselben Zeile wie die
    Zeitraumanzeige (.chart-title-row), nicht im Popover (siehe
    test_compare_popover_stays_pure_selection) — sichtbar bei "Vorjahr" +
    range='year', unabhängig davon, ob schon eine Soll-Entität gewählt ist
    (sonst gäbe es keine Möglichkeit, überhaupt eine zu wählen). Die Zeile
    trägt zusätzlich chart-title-row-end (bottom statt center ausgerichtet,
    siehe chart_editor.css) — der zweistöckige Feld-Stack (Beschriftung über
    Bedienelement) sonst mittig zwischen den eigenen Zeilen hängt statt an
    einer von beiden auszurichten."""
    assert EDITOR_TEMPLATE.count('class="toolbar-field"') == 2
    title_row = EDITOR_TEMPLATE.split('<div class="chart-title-row chart-title-row-end">', 1)[1]
    period_and_fields = title_row.split("</div>", 1)[0]
    assert 'class="period-label"' in period_and_fields
    assert 'x-if="compare && compareMode === \'previous\' && range === \'year\'"' in period_and_fields


def test_previous_year_extension_row_offers_running_total_and_target_entity() -> None:
    """Laufsumme und Soll standen im Mockup (jahresvergleich-mockup.html) als
    zwei der drei „NEU"-Felder. Laufsumme ist reine Render-Einstellung
    (render(), keine neue Abfrage); Soll ist keine Sonderfunktion, sondern
    nur eine bequemere Auswahl für eine ganz normale Entität (siehe
    compareSollEntityId in chart_editor.js). Die Auswahl-Logik selbst steckt
    in setSollEntity() (chart_editor.js), nicht als Inline-Callback im
    Template — siehe test_clearing_soll_removes_only_auto_added_entity."""
    zeile = EDITOR_TEMPLATE.split(
        'x-if="compare && compareMode === \'previous\' && range === \'year\'"', 1
    )[1].split('<span class="period-label"', 1)[0]
    assert "compareCumsum = false; render()" in zeile
    assert "compareCumsum = true; render()" in zeile
    assert 'entityPicker(ENTITY_OPTIONS, compareSollEntityId, (id) => setSollEntity(id))' in zeile


def test_clearing_soll_removes_only_auto_added_entity() -> None:
    """Frueher blieb eine per Soll gewaehlte Entität nach dem Leeren des
    Feldes (Klick auf ✕) als gewöhnliche Balkenpaar-Serie samt eigener
    Kumuliert-Achse im Chart stehen (gemessen mit Außentemperatur als Soll:
    nach dem Leeren erschienen plötzlich "°C"/"°C kumuliert"-Achsen) — eine
    Entität, die NUR wegen der Soll-Wahl in selectedEntityIds gelandet ist
    (sollAutoAdded), wird beim Leeren jetzt wieder entfernt. War sie vorher
    schon regulär ausgewählt, bleibt sie unangetastet."""
    fn = EDITOR_JS.split("setSollEntity(id) {", 1)[1].split("\n        },", 1)[0]
    assert "this.sollAutoAdded = true;" in fn
    assert "if (autoAdded && previous) this.toggleEntity(previous, false);" in fn


def test_soll_entity_is_excluded_from_the_names_and_order_list() -> None:
    """"Angezeigte Namen & Reihenfolge" bestimmt Legenden-/Statistik-/Farb-
    Reihenfolge und ist per Ziehen/Pfeilen umsortierbar — Soll hat keine
    eigene Legendenzeile/Reihenfolge (nur die eine gepunktete Ziel-Linie,
    siehe renderYearsCompare()), da gibt es nichts zu sortieren. Die Liste
    iteriert deshalb orderableEntityIds (selectedEntityIds ohne die aktuelle
    Soll-Entität), nicht selectedEntityIds direkt; der Farbpunkt nutzt
    colorIndexFor(id) statt des lokalen (gefilterten) idx, sonst driftete er
    von der tatsächlichen, auf selectedEntityIds basierenden Chart-Farbe ab,
    sobald Soll irgendwo dazwischen im rohen Array steht."""
    assert 'x-for="(id, idx) in orderableEntityIds"' in EDITOR_TEMPLATE
    assert "colorIndexFor(id) % 8 + 1" in EDITOR_TEMPLATE
    assert 'x-show="editing && orderableEntityIds.length > 0"' in EDITOR_TEMPLATE
    getter = EDITOR_JS.split("get orderableEntityIds() {", 1)[1].split("\n        },", 1)[0]
    assert "this.selectedEntityIds.filter(id => id !== this.compareSollEntityId)" in getter


def test_move_entity_swaps_within_the_orderable_list_not_the_raw_array() -> None:
    """Ein simples idx+delta-Splice auf dem ROHEN selectedEntityIds (früherer
    Stand) würde eine Entität mit der dazwischenstehenden, unsichtbaren
    Soll-Entität vertauschen statt mit ihrer visuell nächsten Nachbarzeile —
    moveEntity() ermittelt den Tausch-Partner deshalb über orderableEntityIds
    und vertauscht dann per echtem Index in selectedEntityIds."""
    fn = EDITOR_JS.split("moveEntity(entityId, delta) {", 1)[1].split("\n        },", 1)[0]
    assert "const order = this.orderableEntityIds;" in fn
    assert "const neighborId = order[targetOrderIdx];" in fn


def test_compare_popover_stays_pure_selection() -> None:
    """Das Popover bietet für range='year' ausschließlich "Aus"/"Vorjahr" —
    keine Konfiguration (Jahr A/B gab es früher, siehe
    test_years_compare_is_not_a_separate_menu_option). Jede Wahl schließt es
    sofort, wie bei den übrigen Menüs auch."""
    popover = EDITOR_TEMPLATE.split('class="menu-popover menu-popover-narrow"', 1)[1].split("</div>", 1)[0]
    assert "compareYearA" not in popover.replace("compareYearAvailable", "")
    assert "compareYearB" not in popover
    assert "this.compareMenuOpen = false;" in EDITOR_JS.split("setCompareMode(mode) {", 1)[1].split("},", 1)[0]


def test_bar_render_path_activates_on_running_total_or_target_entity() -> None:
    """render() wechselt für "Vorjahr" bei range='year' auf die Balkenpaare
    mit eigener Legende (renderYearsCompare()), sobald Laufsumme ODER eine
    Soll-Entität aktiv ist — Laufsumme braucht die kalendermonatsweise
    Kumulierung aus renderYearsCompare() (monthlyBuckets()/cumulate()) auch
    ohne Soll (die gewohnte Schattenlinie kennt keine Monats-Buckets). Ohne
    beides bleibt "Vorjahr" für JEDEN Zeitraum, auch 'year', unverändert die
    gewohnte Schattenlinie."""
    assert "renderYearsCompare()" in EDITOR_JS
    assert "if (this.yearsCompareActive) {" in EDITOR_JS
    getter = EDITOR_JS.split("get yearsCompareActive() {", 1)[1].split("\n        },", 1)[0]
    assert "compareMode === 'previous'" in getter
    assert "this.range === 'year'" in getter
    assert "this.compareCumsum" in getter
    assert "!!this.compareSollEntityId" in getter


def test_previous_year_bars_use_the_fixed_calendar_pair() -> None:
    """Kein frei wählbares Jahr A/B mehr (siehe
    test_years_compare_is_not_a_separate_menu_option) — "Vorjahr" heißt für
    range='year' fest "aktuelles Jahr vs. Vorjahr", aus windowStart
    abgeleitet, exakt dieselbe Abfrage wie die gewohnte Schattenlinie
    (compare_points via offset-1/year_over_year=false in api_query_multi()),
    kein eigener compare_years-Parameter mehr."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    assert "s.compare_points" in fn
    assert "s.points" in fn
    assert "this.windowStart" in fn
    assert "compare_years" not in (APP / "api_routes.py").read_text(encoding="utf-8")


def test_soll_entity_needs_no_backend_change() -> None:
    """Soll ist im Mockup ausdrücklich "keine Sonderfunktion" — die
    Ziel-Entität läuft durch dieselbe /api/query-multi-Abfrage wie jede
    andere gewählte Entität (siehe compareSollEntityId-Kommentar); nur
    renderYearsCompare() behandelt GENAU DIESE eine anders (eine Linie statt
    zweier Balken). toggleEntity() setzt compareSollEntityId außerdem
    zurück, wenn genau diese Entität wieder abgewählt wird — sonst zeigt das
    Soll-Feld einen Namen, zu dem keine Daten mehr geladen werden."""
    zweig = EDITOR_JS.split("toggleEntity(entityId, checked) {", 1)[1].split("\n        },", 1)[0]
    assert "this.compareSollEntityId = '';" in zweig


def test_years_render_path_gives_target_entity_its_own_line() -> None:
    """Die Soll-Entität bekommt EINE gepunktete Ziel-Linie fürs aktuelle
    Jahr, nicht zwei Balken wie jede andere Entität — sonst würde ein Ziel
    wie ein Jahresvergleich mit sich selbst aussehen. Laufsumme (kumulierte
    Werte je Jahr, gestrichelt auf einer eigenen "… kumuliert"-Achse) bleibt
    dagegen auf die echten Entitäten beschränkt."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    assert "isSoll(s)" in fn
    assert "type: 'dotted'" in fn
    assert "cumulate(monthly)" in fn
    assert "kumuliert" in fn


def test_target_entity_shares_the_axis_of_its_own_unit() -> None:
    """Ein früherer Anlauf ließ die Ziel-Linie fest auf der ersten Achse
    laufen ("keine eigene Y-Achse") — mit einer Soll-Entität ANDERER Einheit
    (gemessen: Außentemperatur als Soll neben dem Wasserzähler) landeten ihre
    rohen °C-Werte auf der m³-Achse, zogen deren Skala auf über 18 hoch und
    quetschten die Balken der eigentlichen Entität auf einen schmalen
    Streifen — die passende °C-Achse blieb leer. Seither teilt sich die
    Ziel-Linie die Achse ihrer EIGENEN Einheit (unitIndex), wie jede andere
    Entität auch."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    soll_branch = fn.split("if (isSoll(s)) {", 1)[1].split("return;", 1)[0]
    assert "yAxisIndex: unitIndex," in soll_branch


def test_current_year_bar_renders_left_of_previous_year() -> None:
    """ECharts ordnet gruppierte Balken einer Kategorie in der Reihenfolge,
    in der ihre Serien gepusht werden — das zuerst gepushte Jahr steht links.
    Gemeldet: das Vorjahr stand links, das aktuelle Jahr rechts ("Jahre
    verdreht"); jetzt zuerst das aktuelle Jahr."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    pair = fn.split("].forEach(({year, points, opacity}) => {", 1)[0].split(
        "[\n", 1
    )[1]
    assert pair.index("year: currentYear") < pair.index("year: previousYear")


def test_year_compare_bars_are_square_not_rounded() -> None:
    """Die übrigen Balken auf dieser Seite (render() oben, chartType 'bar')
    sind eckig (itemStyle ohne borderRadius) — ein früherer Stand rundete nur
    die Balken des Jahresvergleichs oben ab, was neben den eckigen Balken der
    gewohnten Schattenlinie inkonsistent wirkte."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    assert "borderRadius" not in fn


def test_cumulative_and_soll_only_axes_stay_hidden() -> None:
    """Laufsumme- ("… kumuliert") und eine Soll-exklusive Achse (eine Einheit,
    die AUSSCHLIESSLICH von der Soll-Ziel-Linie genutzt wird, keine Balken
    trägt) bleiben unsichtbar (show:false) statt als eigene Achsen-Spalte
    Platz zu beanspruchen — ECharts skaliert Serien auf eine ausgeblendete
    Achse trotzdem korrekt. barUnits (nur Einheiten mit echten Balken)
    entscheidet, welche Achse eine Soll-exklusive ist."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    assert "const barUnits = new Set(" in fn
    assert "const hidden = isCum || !barUnits.has(u);" in fn
    assert "show: !hidden," in fn


def test_hidden_axes_reserve_no_grid_space() -> None:
    """show:false allein reichte nicht — containLabel (grid) reservierte
    trotzdem Rand für die ausgeblendete Achse (Name + Beschriftungsbreite
    fließen in dessen Berechnung ein), sichtbar als unbegründeter Leerraum
    links von der tatsächlich sichtbaren Achse (gemessen: kWh-Balken + Soll +
    Laufsumme, nur EINE sichtbare Achse, trotzdem deutlicher Leerraum davor).
    axisLine/axisTick/axisLabel/splitLine einzeln abschalten UND kein Name
    nimmt der ausgeblendeten Achse jeden Platzanspruch."""
    fn = EDITOR_JS.split("renderYearsCompare() {", 1)[1].split("\n        },", 1)[0]
    assert "name: hidden ? '' :" in fn
    assert "axisLine: {show: !hidden}," in fn
    assert "axisTick: {show: !hidden}," in fn
    assert "splitLine: {show: !hidden}," in fn
    assert "axisLabel: {\n                show: !hidden," in fn


def test_running_total_stops_instead_of_projecting_into_the_future() -> None:
    """Dieselbe Zusage wie überall sonst im Chart: eine Linie wird nie über
    den letzten bekannten Wert hinaus in die Zukunft fortgeschrieben (siehe
    windowEnd-Kommentar). Ein einzelner fehlender Monat MITTEN im Jahr soll
    die Summe dagegen nicht auf 0 zurückwerfen, sondern nur überspringen."""
    fn = EDITOR_JS.split("function cumulate(values) {", 1)[1].split("\n    }", 1)[0]
    assert "if (i > lastReal) return null;" in fn
