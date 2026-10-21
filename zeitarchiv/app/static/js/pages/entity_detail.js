    // Vom Kalender-Widget (static/js/calendar-picker.js) beim Klick auf einen
    // Tag mit Daten aufgerufen — anders als auf der Bereinigungs-Seite (ein
    // Tage-Block-Offset in einem versteckten Formularfeld) ist die Navigation
    // hier ein Alpine-Zustand (range/offset in entityChart()), deshalb über
    // Alpine.$data direkt an der Komponente gesetzt statt über ein DOM-Feld.
    // Rechnet den gewählten Tag in "wie viele Perioden der aktuell gewählten
    // Zeitraum-Auflösung liegt er hinter der jeweils laufenden Periode" um —
    // dieselbe Offset-Definition wie _window() in query.py (0 = aktuell,
    // -1 = eine Periode zurück, …), pro Zeitraum-Typ an dessen eigener
    // Kalendergrenze ausgerichtet (Kalenderwoche Mo–So bei "week", Kalendermonat
    // bei "month", …). 12 Uhr ist für Stunde/Tag ein stabiler Anker mitten im
    // gewählten Datum und vermeidet Randfälle an Sommerzeitwechseln.
    function jumpToDate(dateStr) {
      const root = Alpine.$data(document.querySelector('.page'));
      const [y, m, d] = dateStr.split('-').map(Number);
      const picked = new Date(y, m - 1, d, 12);
      root._rangeAnchorMs = picked.getTime();
      root.offset = PeriodNavigation.offsetForRange(root.range, root._rangeAnchorMs);
      root.load();
    }

    // Jede Datumsformatierung in dieser Datei läuft über Intl mit dieser einen Konstante;
    // das Format kommt aus der Einstellung (window.ZA_FORMAT, siehe i18n.js).
    const LOCALE = window.ZA_FORMAT;

    // Dieselbe Optionen-Menü-Auswahl (Dynamische Y-Achse/Statistik in
    // Legende) wie chart_editor.html, hier nur mit einem einzelnen
    // Legenden-Chip statt einer Serienliste.
    const LEGEND_METRIC_OPTIONS = [
      {value: 'last', label: t('Aktuell')},
      {value: 'min', label: t('Min')},
      {value: 'max', label: t('Max')},
      // Symbole statt Text, wo eines eindeutig etabliert ist — dieselben
      // Zeichen wie in der Legende selbst (Ø) und in chart_editor.js
      // (LEGEND_METRIC_OPTIONS, identische Begründung dort).
      {value: 'average', label: 'Ø'},
      {value: 'sum', label: 'Σ'},
    ];
    // Rohwerte kommen als JS-Floats mit Rauschen im letzten Bit (z. B. 0.0003999999998995918
    // statt 0.0004) — bei DECIMALS=null (Automatisch) auf 4 signifikante Stellen runden statt
    // fest auf 2 Nachkommastellen, sonst verschwinden kleine Zähler-Deltas (z. B. Wh-Bereich
    // bei einer kWh-Einheit) optisch komplett zu "0.00", obwohl echte Werte vorhanden sind.
    // Mit explizit gesetztem DECIMALS wird stattdessen immer auf genau diese Stellenzahl
    // gerundet, auch wenn das Nullen anhängt.
    // Zentral in static/js/number-format.js (window.NumberFormat) — dieselbe
    // Formatierung wie überall sonst in der Oberfläche, siehe Kommentar dort.
    function fmtValue(v) {
      if (DURATION_DISPLAY) return NumberFormat.fmtDuration(v);
      return NumberFormat.fmt(v, DECIMALS);
    }

    // Wandelt den gespeicherten decimals-String ("auto" oder eine Ziffer) in den
    // Parameter für NumberFormat.fmt() um — dieselbe Konvention wie
    // formatting.decimals_to_int() auf dem Server.
    function decimalsStringToInt(value) {
      if (value == null || value === 'auto') return null;
      const n = parseInt(value, 10);
      return Number.isNaN(n) ? null : n;
    }

    // Gesamt-Einschaltdauer aus rohen AN/AUS-Zeitstempeln — dieselbe Paar-
    // bildung (jeder Punkt gilt bis zum nächsten, letzter bis windowEnd) wie
    // renderTimeline() beim Zeichnen der Zeitstrahl-Balken, hier nur für die
    // Summe statt fürs Rendering. Im Rohwerte-/Zeitstrahl-Modus sind die
    // Punktwerte selbst nur 0/1-Zustände — deren Summe wäre bedeutungslos,
    // die tatsächliche Dauer ergibt sich erst aus den Intervalllängen.
    function switchOnDuration(points, windowEnd) {
      let total = 0;
      for (let i = 0; i < points.length; i++) {
        const start = points[i].ts;
        const end = i + 1 < points.length ? points[i + 1].ts : (windowEnd ?? start);
        if (points[i].value >= 0.5 && end > start) total += end - start;
      }
      return total;
    }

    // Eigenständige Funktion statt Alpine-Getter, damit sie sowohl für die
    // aktuelle Periode (Toolbar-Label) als auch für die Vorperiode (Legenden-
    // Beschriftung des Vergleichs-Serie in render()) genutzt werden kann.
    // skipRelative unterdrückt t("Heute")/t("Gestern") — für die Vorjahres-Vergleichslabel
    // wäre offset===-1 sonst als t("Gestern") missverständlich, obwohl der Zeitraum ein
    // ganzes Jahr zurückliegt und nur zufällig denselben rechnerischen offset teilt.
    function formatPeriodLabel(range, continuous, windowStart, windowEnd, offset, isCurrent, skipRelative = false) {
      if (windowStart == null || windowEnd == null) return '';
      const start = new Date(windowStart * 1000);
      // windowEnd ist exklusiv — für die Anzeige eine Sekunde zurück, sonst wirkt
      // z. B. ein Monat so, als würde er schon im Folgemonat enden.
      const end = new Date(windowEnd * 1000 - 1000);
      const fmtDay = d => d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric'});
      const fmtDayMonth = d => d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit'});
      const fmtMonthYear = d => d.toLocaleDateString(LOCALE, {month: 'long', year: 'numeric'});
      const fmtTime = d => d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
      switch (range) {
        case 'hour':
          return t('{range} Uhr',{range: `${fmtDay(start)} · ${fmtTime(start)}–${fmtTime(end)}`});
        case 'day':
          if (!continuous) {
            if (!skipRelative && offset === 0) return t('Heute');
            if (!skipRelative && offset === -1) return t('Gestern');
            return fmtDay(start);
          }
          return `${fmtDay(start)} – ${fmtDay(end)}`;
        case 'week':
          return `${fmtDayMonth(start)}–${fmtDayMonth(end)} ${end.getFullYear()}`;
        case 'month':
          if (continuous) return `${fmtDay(start)} – ${fmtDay(end)}`;
          return isCurrent ? t('{period} (bis heute)', {period: fmtMonthYear(start)}) : fmtMonthYear(start);
        case 'year':
          if (continuous) return `${fmtMonthYear(start)} – ${fmtMonthYear(end)}`;
          return isCurrent ? t('{period} (bis heute)', {period: start.getFullYear()}) : `${start.getFullYear()}`;
        case 'decade':
          return `${start.getFullYear()}–${end.getFullYear()}`;
        default:
          return '';
      }
    }

    // Name der t("Vorperiode") beim Vergleich compareMode="previous" — welche
    // Periode genau eine zurückliegt, hängt vom Zeitraum-Typ ab (t("Vortag") bei
    // t("Tag"), t("Vormonat") bei t("Monat"), …). Genutzt für den Umschalt-Button
    // (Seg neben t("Vorjahr")) und die Legenden-Beschriftung der Vergleichs-Serie
    // in render() — beide sollen dasselbe Wort zeigen. Eigenständige Funktion
    // aus demselben Grund wie formatPeriodLabel oben (zwei Aufrufstellen,
    // eine davon außerhalb des Alpine-Objekts).
    function previousPeriodLabel(range) {
      const labels = {
        hour: t('Vorherige Stunde'), day: t('Vortag'), week: t('Vorwoche'),
        month: t('Vormonat'), year: t('Vorjahr'), decade: t('Vorherige Dekade'),
      };
      return labels[range] || t('Vorperiode');
    }

    // Gegenstück zu previousPeriodLabel für compareMode="year" — dieselbe
    // Periode, aber genau ein Jahr zurück statt eine Periode zurück (bei
    // t("Jahr") fallen beide Modi auf dasselbe Wort t("Vorjahr")).
    function previousYearPeriodLabel(range) {
      const labels = {
        hour: t('Vorjahresstunde'), day: t('Vorjahrestag'), week: t('Vorjahreswoche'),
        month: t('Vorjahresmonat'), year: t('Vorjahr'), decade: t('Vorjahresdekade'),
      };
      return labels[range] || t('Vorjahreszeitraum');
    }

    // Zwei Zeiträume bekommen keine zweite Vergleichszeile, aus zwei
    // verschiedenen Gründen (beide in tests/test_query.py gemessen):
    //
    //   t("Jahr")   — die Vorperiode IST das Vorjahr. Für jedes abgeschlossene
    //              Jahr liefern beide Modi buchstäblich dasselbe Fenster; im
    //              laufenden Jahr unterscheiden sie sich nur darin, dass der
    //              Vorjahresvergleich am selben TAG des Vorjahres endet statt
    //              am Jahresende. Das ist ein echter Unterschied — aber beide
    //              Zeilen trugen dafür dasselbe Wort t("Vorjahr"), und zwei
    //              Fenster unter einer Beschriftung kann niemand
    //              auseinanderhalten. Soll der faire Jahresvergleich zurück,
    //              braucht er ein eigenes Wort, keine zweite t("Vorjahr")-Zeile.
    //   t("Dekade") — dort ist der Modus schlicht falsch: er schiebt das
    //              Jahrzehnt um EIN Jahr zurück, das Ergebnis überlappt also
    //              genau den Zeitraum, gegen den es verglichen wird.
    const COMPARE_YEAR_RANGES = ['hour', 'day', 'week', 'month'];

    function compareYearAvailable(range) {
      return COMPARE_YEAR_RANGES.includes(range);
    }

    // Durchschnitt der GEZEICHNETEN Werte, oder null wenn es keine gibt.
    //
    // Bewusst selbst gerechnet statt ECharts' eigenem Durchschnitts-Typ für
    // markLine: der rechnet über die Daten, die die Serie im Moment führt — bei aktivem
    // dataZoom mit filterMode 'filter' also über den sichtbaren Ausschnitt.
    // Die Linie änderte damit ihre Bedeutung, sobald jemand hineinzoomt, und
    // zwar abhängig von einer ganz anderen Option ("Dynamische Y-Achse", die
    // den filterMode bestimmt). Ein fester yAxis-Wert kann das nicht.
    function averageOf(values) {
      const zahlen = values.filter(Number.isFinite);
      if (!zahlen.length) return null;
      return zahlen.reduce((summe, v) => summe + v, 0) / zahlen.length;
    }

    // Median-Abstand aufeinanderfolgender Zeitstempel — dieselbe Funktion wie
    // in chart_editor.html, hier für den Tooltip-Zeitstempel-Formatter unten.
    function detectResolutionSeconds(points) {
      if (!points || points.length < 2) return null;
      const gaps = [];
      for (let i = 1; i < points.length; i++) {
        const gap = points[i].ts - points[i - 1].ts;
        if (gap > 0) gaps.push(gap);
      }
      if (!gaps.length) return null;
      gaps.sort((a, b) => a - b);
      return gaps[Math.floor(gaps.length / 2)];
    }

    // Tooltip-Zeitstempel richten sich nach der TATSÄCHLICHEN Bucket-Breite der
    // Daten, nicht nach dem Zeitraum-Namen — die Uhrzeit ist bei Tages-Buckets
    // oder gröber ohnehin immer Mitternacht, also reine Information ohne Wert.
    // Durchgehend über Intl (LOCALE) statt handgebauter Strings, damit sich
    // Datum/Uhrzeit/Wochentag mit einer künftigen Sprachumschaltung automatisch
    // anpassen. Der abgeschnittene Punkt hinter dem Wochentagskürzel
    // (".replace") gleicht nur einen ICU-Unterschied zwischen Engines aus
    // ("Mo" vs. "Mo.") — die Sprache/Reihenfolge selbst bleibt Intl überlassen.
    function fmtTooltipTimestamp(ms, bucketSeconds) {
      const d = new Date(ms);
      if (bucketSeconds == null || bucketSeconds < 86400) {
        return d.toLocaleString(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'});
      }
      if (bucketSeconds < 86400 * 25) {
        const weekday = d.toLocaleDateString(LOCALE, {weekday: 'short'}).replace(/\.$/, '');
        const date = d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric'});
        return `${weekday}, ${date}`;
      }
      if (bucketSeconds < 86400 * 200) {
        return d.toLocaleDateString(LOCALE, {month: 'long', year: 'numeric'});
      }
      return d.toLocaleDateString(LOCALE, {year: 'numeric'});
    }

    // Bewusst außerhalb des Alpine-x-data-Objekts: Alpine macht jede Eigenschaft
    // dort reaktiv, indem es sie in einen Proxy einwickelt. ECharts verlässt sich
    // intern (zrender-Eventsystem, WeakMap-Lookups per Objekt-Identität) darauf,
    // dass `this` innerhalb seiner Methoden das echte Objekt ist, nicht ein Proxy
    // — als `this.chart` reaktiv war, brach das lautlos (kein Fehler, aber
    // dispatchAction/Tooltip reagierten nie), plus ein zusätzlicher zrender-Crash
    // bei jedem Redraw. Ein normaler Closure-Variable reicht hier, weil es genau
    // eine Chart-Instanz pro Seite gibt.
    let chartInstance = null;

    // Ab wie vielen Punkten ein Zoom überhaupt etwas aufdecken kann: der Chart
    // ist am Desktop rund 900 px breit, bei 200 Punkten hat jeder davon noch
    // gut 4 px für sich — darunter verdeckt kein Punkt einen anderen, es gibt
    // nichts zu vergrößern.
    //
    // Bewusst eine Schwelle über die tatsächlich geladenen Punkte statt einer
    // Liste erlaubter Zeiträume: dieselbe Zeitraum-Stufe braucht je nach
    // Melderhythmus der Entität unterschiedliche Antworten. Ein Sensor mit zwei
    // Werten am Tag hat im Monat 60 Punkte und nichts zu zoomen, einer im
    // 10-Sekunden-Takt am Tag 8.640 und dringend etwas davon.
    const ZOOM_MIN_POINTS = 200;

    // Gemeinsam für Chart und Zeitstrahl. Die Voreinstellungen von ECharts sind
    // hier durchweg die falschen, deshalb steht jede Zeile bewusst so:
    //
    // - zoomOnMouseWheel:'ctrl' statt true — sonst kapert der Chart das
    //   Scrollrad, und wer nur an ihm vorbeiscrollen will, zoomt versehentlich.
    //   Strg+Rad ist außerdem das, was Browser selbst mit Zoom belegen. Ein
    //   Trackpad-Pinch erzeugt genau diese Kombination nativ, die Geste
    //   funktioniert dadurch ohne eine einzige Zeile Touch-Code.
    // - moveOnMouseWheel:false — dasselbe Argument: das Rad allein gehört der
    //   Seite, nicht dem Chart.
    // - moveOnMouseMove:'ctrl' statt true, und das ist eine Mobil-Entscheidung:
    //   zrender setzt eine Ein-Finger-Berührung in Mausereignisse um, ein
    //   schlichtes true würde also auf dem Telefon jeden senkrechten Wisch über
    //   dem Chart als Schwenk verstehen — und weil preventDefaultMouseMove per
    //   Voreinstellung true ist, bliebe die Seite dabei stehen statt zu
    //   scrollen. Mit 'ctrl' greift der Schwenk nur bei gedrückter Taste, die es
    //   auf einem Touchscreen nicht gibt: dort bleibt der Wisch der Seite, und
    //   gezoomt wird mit zwei Fingern (der Pinch läuft über einen eigenen
    //   Handler und ist von diesen Schaltern unberührt). Nebenbei ergibt sich
    //   dadurch am Rechner eine einzige Regel statt zweier: Strg gedrückt
    //   halten heißt "ich meine den Chart, nicht die Seite".
    // - filterMode entscheidet, ob der Zoom die y-Achse mitskaliert. 'filter'
    //   wirft Punkte außerhalb des Ausschnitts weg und lässt die Achse damit
    //   nachziehen; 'none' behält alles. Die Seite hat mit "y-Achse
    //   fest/dynamisch" schon einen Schalter dafür — ein Zoom, der die Achse
    //   eigenmächtig nachskaliert, würde gegen diese Option arbeiten statt sie
    //   zu bedienen. Deshalb wird sie hier durchgereicht, nicht neu entschieden.
    function zoomConfig(filterMode) {
      return {
        type: 'inside',
        zoomOnMouseWheel: 'ctrl',
        moveOnMouseWheel: false,
        moveOnMouseMove: 'ctrl',
        filterMode,
      };
    }

    // Aufziehen eines Bereichs mit gedrückter Umschalttaste.
//
// Über die `brush`-Komponente, NICHT über `toolbox.feature.dataZoom`. Der
// naheliegende Weg wäre eine toolbox mit `show: false` gewesen, damit die
// fremden ECharts-Icons draußen bleiben — der ist aber am laufenden Chart
// gemessen wirkungslos: ohne sichtbare toolbox legt ECharts deren View nicht
// an, und `takeGlobalCursor` mit `dataZoomSelect` läuft dann ins Leere (die
// brush-Komponente bleibt leer, `getModel().getComponent('brush')` null).
// Über `brush` direkt ist der Modus dagegen nachweislich scharf
// (`brushOption.brushType === 'lineX'`), und die Auswahl gehört uns: aus dem
// brushEnd-Ereignis wird selbst ein dataZoom, statt sich auf die Umsetzung
// der toolbox zu verlassen.
//
// brushType 'lineX' beschränkt das Rechteck auf die Zeitachse: senkrecht
// hineinzuzoomen ergäbe hier keinen Sinn — die y-Achse folgt der Einstellung
// "fest/dynamisch" und nicht einem gezogenen Rahmen.
//
// Die Knopfleiste muss an ZWEI Stellen abbestellt werden, und die zweite ist
// am laufenden Chart aufgefallen: `toolbox: []` hier sagt nur, welche
// brush-Knöpfe die Werkzeugleiste zeigen soll — die Werkzeugleiste selbst legt
// ECharts trotzdem an, und sie erschien mit vier fremden Icons genau dort, wo
// der Ausschnitts-Chip sitzt. Erst `toolbox: {show: false}` in der Option
// (siehe unten, neben brush) hält sie draußen.
function selectBrush() {
  return {
    xAxisIndex: 0,
    toolbox: [],
    throttleType: 'debounce',
    throttleDelay: 80,
    brushStyle: {
      color: getComputedStyle(document.body).getPropertyValue('--accent-line-soft'),
      borderColor: getComputedStyle(document.body).getPropertyValue('--accent-line'),
      borderWidth: 1,
    },
  };
}

// Der sichtbare Ausschnitt in echten Zeitstempeln (ms). ECharts drückt den
    // Zoom je nach Auslöser mal als Prozentbereich (start/end), mal als
    // Wertebereich (startValue/endValue) aus — deshalb beide Wege, statt sich
    // auf einen zu verlassen. null bedeutet "voller Zeitraum", also kein Zoom.
    function visibleWindow(zoom, fromMs, toMs) {
      if (zoom == null || fromMs == null || toMs == null) return null;
      let start = zoom.startValue;
      let end = zoom.endValue;
      if (start == null || end == null) {
        const span = toMs - fromMs;
        start = fromMs + span * (zoom.start ?? 0) / 100;
        end = fromMs + span * (zoom.end ?? 100) / 100;
      }
      // Ein Promille Toleranz: eine Radbewegung bis an den Anschlag landet
      // nicht immer exakt auf 0/100, und ein Chip, der bei voller Ansicht noch
      // "aktiv" aussieht, wäre schlicht falsch.
      const tolerance = (toMs - fromMs) / 1000;
      if (start <= fromMs + tolerance && end >= toMs - tolerance) return null;
      return {start, end};
    }

    // Zustands-Entitäten (Typ "state", ROADMAP 1.29): Farben, Abschnitte und
    // Buckets aus state-entities.js — Chart-Editor und Kacheln nutzen dieselben.
    const STATE_UNAVAILABLE = StateEntities.UNAVAILABLE;
    const stateColor = StateEntities.color;
    const stateSwatchStyle = StateEntities.swatchStyle;
    const unavailablePattern = StateEntities.unavailablePattern;
    const STATE_LOG_PAGE = 25;
    // Darstellung je Zeitraum, solange niemand umschaltet: bis zur Woche der
    // Zeitstrahl, ab dem Monat die Anteile (ROADMAP 1.29 — ein Jahr Zeitstrahl
    // wären zehntausende Rechtecke, und die Frage dort ist "wie lange", nicht
    // "wann genau").
    // Eine selbst gewählte Darstellung (chosen) bleibt beim Zeitraumwechsel
    // erhalten; nur der Zeitstrahl geht ab dem Monat nicht (siehe oben) und
    // fällt dort auf den Standard zurück — wer danach wieder in einen kurzen
    // Zeitraum wechselt, bekommt ihn zurück.
    const stateModeFor = (range, chosen = null) => {
      const longRange = StateEntities.SERVER_SHARE_RANGES.includes(range);
      if (chosen && !(chosen === 'timeline' && longRange)) return chosen;
      return longRange ? 'shares' : 'timeline';
    };
    // Klick auf einen Balken öffnet den Zeitraum darunter.
    const STATE_DRILL = {week: 'day', month: 'day', year: 'month', decade: 'year', day: 'hour'};

    function entityChart() {
      return {
        ranges: [
          {key: 'hour', label: t('Stunde')}, {key: 'day', label: t('Tag')}, {key: 'week', label: t('Woche')},
          {key: 'month', label: t('Monat')}, {key: 'year', label: t('Jahr')}, {key: 'decade', label: t('Dekade')},
        ],
        range: INITIAL_RANGE || 'day',
        // chart_type "auto" (globaler Default) löst hier zum entitätstyp-
        // abhängigen DEFAULT_CHART_TYPE auf — "line"/"bar" (eigene Wahl,
        // global oder pro Entität übersteuert) gilt dagegen direkt.
        chartType: CHART_OPTIONS.chart_type === 'auto' ? DEFAULT_CHART_TYPE : CHART_OPTIONS.chart_type,
        showPoints: CHART_OPTIONS.show_points,
        showValues: CHART_OPTIONS.show_values,
        dynamicYAxis: CHART_OPTIONS.dynamic_y_axis,
        chartStats: CHART_OPTIONS.chart_stats,
        averageLine: CHART_OPTIONS.average_line,
        legendMetrics: [...CHART_OPTIONS.legend_metrics],
        legendStyle: CHART_OPTIONS.legend_style,
        decimals: CHART_OPTIONS.decimals,
        compare: false,
        compareMode: 'previous',
        optionsMenuOpen: false,
        compareMenuOpen: false,
        raw: CHART_OPTIONS.raw,
        offset: INITIAL_OFFSET,
        continuous: CHART_OPTIONS.continuous,
        points: [],
        // Sichtbarer Ausschnitt innerhalb des geladenen Zeitraums ({start, end}
        // in ms) oder null für "ganzer Zeitraum". Bewusst NICHT in der URL und
        // nicht in den Chart-Optionen der Entität gespiegelt: "Rollierend",
        // "Rohwerte" und "Diagrammtyp" sind Aussagen über die Entität und
        // gehören dorthin, ein Ausschnitt ist eine Aussage über die letzten
        // dreißig Sekunden.
        zoomRange: null,
        // Zur Löschung markierte Bereiche: die Werte selbst sind aus points
        // herausgefiltert, hier kommen die betroffenen Zeitabschnitte zurück.
        // markedTotal ist die WAHRE Zahl markierter Werte im Fenster, auch
        // wenn die Blockliste gekappt wurde (MAX_MARKED_RANGES) — der Chip
        // nennt sie, das Chart zeichnet nur, was es lesbar zeichnen kann.
        showMarked: INITIAL_MARKED || CHART_OPTIONS.show_marked,
        markedRanges: [],
        markedTotal: 0,
        markedMenuOpen: false,
        comparePoints: [],
        compareWindowStart: null,
        compareWindowEnd: null,
        aggregationType: null,
        isState: IS_STATE,
        // Zuordnung Index → Text aus /api/query ({idx, text, label}), nur bei
        // Zustands-Entitäten gefüllt; "nicht verfügbar" steht zuletzt.
        states: [],
        // Zeitstrahl, Anteile oder Wechsel je Bucket. Nicht gespeichert: der
        // Zeitraum bestimmt den Ausgangspunkt (stateModeFor), umgeschaltet
        // wird für den Moment. stateModeChosen merkt die eigene Wahl, damit sie
        // einen Zeitraumwechsel überlebt.
        stateMode: stateModeFor(INITIAL_RANGE || 'day'),
        stateModeChosen: null,
        // Anteile je Bucket vom Server (chart_type "shares"), nur ab dem Monat.
        // points bleiben die Rohwechsel — Protokoll und längster Abschnitt
        // brauchen sie in jedem Zeitraum.
        sharePoints: [],
        // Per Klick auf die Legende ausgeblendete Zustände — bewusst nicht
        // gespeichert, wie das Ein-/Ausblenden von Serien im Chart-Editor.
        hiddenStates: [],
        stateLog: CHART_OPTIONS.state_log !== false,
        stateLogShown: STATE_LOG_PAGE,
        stateLogDays: {},
        // Zeile im Wechsel-Protokoll, deren Band gerade hervorgehoben ist.
        stateHot: null,
        windowStart: null,
        windowEnd: null,
        // Natürliches, NIE an "jetzt" gedeckeltes Periodenende (vom Server,
        // query._window()) — siehe derselbe Kommentar in chart_editor.html.
        periodEnd: null,
        isCurrent: true,
        loading: false,
        _requestId: 0,
        // Bleibt über mehrere reine Auflösungswechsel hinweg erhalten. Ohne
        // diesen separaten Anker würde z. B. der 22. eines laufenden Monats
        // beim Zwischenschritt t("Monat") zu offset=0 werden und beim Zurück-
        // wechseln auf t("Tag") fälschlich auf heute springen.
        _rangeAnchorMs: null,
        get total() { return this.points.reduce((sum, p) => sum + (p.value || 0), 0); },
        get legendMetricOptions() { return LEGEND_METRIC_OPTIONS; },
        // Min/Max/Ø/Summe für den Legenden-Chip (siehe Template) — dieselbe
        // Berechnung wie seriesStats() in chart_editor.html, hier nur für die
        // eine Serie dieser Seite statt für eine Liste. Summe bei Zählern
        // (Bucket-Werte sind dort bereits Deltas je Zeitfenster) UND bei
        // Schaltern sinnvoll — z. B. Summe = gesamte Anwesenheitsdauer im
        // Zeitraum. Bei Zählern nicht im Rohwerte-Modus (Einzelmesswerte
        // statt Deltas); bei Schaltern dagegen AUCH im Rohwerte-/Zeitstrahl-
        // Modus möglich — dort kommt die Summe nicht aus den Punktwerten
        // (die sind nur 0/1), sondern aus switchOnDuration() weiter oben.
        get entityStat() {
          const values = this.points.map(p => p.value).filter(Number.isFinite);
          const minima = this.points.map(p => Number.isFinite(p.min) ? p.min : p.value).filter(Number.isFinite);
          const maxima = this.points.map(p => Number.isFinite(p.max) ? p.max : p.value).filter(Number.isFinite);
          const isSwitch = this.aggregationType === 'switch';
          const hasSum = (this.aggregationType === 'counter' && !this.raw) || isSwitch;
          const formatted = value => fmtValue(value) + (UNIT ? ' ' + UNIT : '');
          const sumValue = this.raw && isSwitch ? switchOnDuration(this.points, this.windowEnd) : this.total;
          // Summe bei Schaltern ist immer eine Dauer in Sekunden (Bucket-
          // Summe der on_seconds bzw. switchOnDuration()) — unabhängig vom
          // Anzeigemodus (DURATION_DISPLAY/fmtValue greift nur, wenn die
          // Entität selbst auf "Zeit" steht) immer als h/m/s formatiert statt
          // über die generische 4-signifikante-Stellen-Rundung von
          // NumberFormat.fmt(), die bei größeren Sekundenwerten sichtbar
          // ungenau wird (z. B. 53624 → "53.620").
          const sumFormatted = isSwitch ? NumberFormat.fmtDuration(sumValue) : formatted(sumValue);
          return {
            last: values.length ? formatted(values[values.length - 1]) : '—',
            min: minima.length ? formatted(Math.min(...minima)) : '—',
            max: maxima.length ? formatted(Math.max(...maxima)) : '—',
            average: values.length ? formatted(values.reduce((sum, v) => sum + v, 0) / values.length) : '—',
            sum: hasSum ? (values.length ? sumFormatted : '—') : null,
          };
        },
        get canGoForward() { return this.offset < 0; },
        // "Als Chart speichern" (Optionen-Menü) — übergibt die aktuell
        // betrachtete Entität + Zeitraum-/Vergleichs-Einstellungen an
        // /charts/new, das sie als Startwerte in den (Mehrentitäten-)Chart-
        // Editor vorausfüllt statt dort blank zu starten. Auflösung gibt es
        // auf dieser Seite nicht (immer volle Auflösung) — /charts/new fällt
        // dafür serverseitig auf "Automatisch" zurück.
        get saveAsChartUrl() {
          const params = new URLSearchParams({
            entity_id: ENTITY_ID, name: DISPLAY_NAME,
            range: this.range, continuous: String(this.continuous),
            compare: String(this.compare), compare_mode: this.compareMode,
            // Darstellung mitnehmen: sonst fängt der Editor bei Null an und die
            // Wahl dieser Seite (Legende als Tabelle, Nachkommastellen …) ginge
            // beim Speichern verloren. Der Editor kennt "line" nicht: eine
            // Schalter-Entität ohne Zeitstrahl heißt dort "bar", alles andere
            // bestimmt "auto" je Serie selbst.
            chart_type: this.chartType === 'timeline' ? 'timeline' : (DEFAULT_CHART_TYPE === 'timeline' ? 'bar' : 'auto'),
            decimals: this.decimals,
            show_points: String(this.showPoints), show_values: String(this.showValues),
            dynamic_y_axis: String(this.dynamicYAxis), average_line: String(this.averageLine),
            chart_stats: String(this.chartStats), legend_metrics: this.legendMetrics.join(','),
            legend_style: this.legendStyle,
          });
          // Eine vergangene Periode (z. B. 2025) geht als fester Zeitraum mit:
          // ein Tag darin (Mitte des Fensters) statt des Versatzes, siehe
          // saved_charts.period_anchor. Die laufende Periode bleibt "immer
          // aktuell". Diese Seite selbst merkt sich die Periode nicht.
          if (this.offset !== 0 && this.windowStart != null && this.windowEnd != null) {
            const d = new Date(PeriodNavigation.anchorForWindow(this.windowStart, this.windowEnd, false));
            const pad = n => String(n).padStart(2, '0');
            params.set('anchor', `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
          }
          return `${BASE}/charts/new?${params}`;
        },
        get comparePreviousLabel() {
          return previousPeriodLabel(this.range);
        },
        get compareYearLabel() {
          return previousYearPeriodLabel(this.range);
        },
        // Blendet die zweite Menüzeile aus, wo sie nichts Eigenes aussagt
        // (siehe COMPARE_YEAR_RANGES).
        get compareYearAvailable() {
          return compareYearAvailable(this.range);
        },
        // Zeigt im Button selbst, WELCHER Vergleich aktiv ist (statt nur
        // t("Vergleichen") + separatem Auswahl-Segment daneben) — passt sich wie
        // die beiden Label-Getter oben automatisch an den Zeitraum an.
        get compareButtonLabel() {
          if (!this.compare) return t('Vergleichen');
          return this.compareMode === 'year' ? this.compareYearLabel : this.comparePreviousLabel;
        },
        get periodLabel() {
          return formatPeriodLabel(this.range, this.continuous, this.windowStart, this.windowEnd, this.offset, this.isCurrent);
        },
        // Einzige Stelle, an der die Schwelle ausgewertet wird — render() liest
        // sie hier ab, statt die Bedingung ein zweites Mal hinzuschreiben.
        // Der Zeitstrahl ist ausgenommen: dort geht es nicht um Komfort,
        // sondern um Sichtbarkeit (Begründung bei seiner dataZoom-Angabe in
        // renderTimeline()), und die Zahl der Segmente sagt darüber nichts.
        get zoomAvailable() {
          if (this.isState) return this.stateMode === 'timeline';
          return this.chartType === 'timeline' || this.points.length > ZOOM_MIN_POINTS;
        },
        get chartIsTimeline() {
          return this.isState ? this.stateMode === 'timeline' : this.chartType === 'timeline';
        },
        // Steht dauerhaft unter dem Chart, weil er zwei Dinge auf einmal sagt,
        // von denen das erste ein Datenzustand ist: WIE VIELE Punkte gerade
        // gezeichnet sind und ob sich daran etwas vergrößern lässt. Damit ist
        // er `hint-status` und kein erklärender Hinweis — er darf nicht hinter
        // den Info-Knopf (siehe "Hinweistexte: drei Rollen" in
        // docs/frontend.md). Ohne ihn bliebe der graue Ausschnitts-Chip in der
        // Werkzeugleiste unerklärt: dass er nicht anklickbar ist, hat einen
        // Grund, und der steht hier.
        get zoomHint() {
          const geste = t('mit Strg und Mausrad zoomen, mit Umschalt einen Bereich aufziehen, am Telefon mit zwei Fingern');
          if (this.isState && this.stateMode !== 'timeline') {
            return t('Ein Klick auf einen Balken öffnet diesen Zeitraum.');
          }
          if (this.isState) {
            return t('Kurze Zustände sind schmaler als ein Bildpunkt — {geste}.', {geste});
          }
          if (this.chartType === 'timeline') {
            return t('Kurze Schaltvorgänge sind schmaler als ein Bildpunkt — {geste}.', {geste});
          }
          const einzeln = this.points.length === 1;
          const anzahl = this.points.length.toLocaleString(LOCALE);
          const punkte = einzeln ? t('1 Datenpunkt') : t('{count} Datenpunkte', {count: anzahl});
          if (this.zoomAvailable) return t('{punkte} — {geste}.', {punkte, geste});
          // "alle einzeln sichtbar" passt nicht zu einem einzelnen Punkt, und
          // den gibt es wirklich: eine Stundenansicht einer selten meldenden
          // Entität hat oft genau einen.
          return einzeln
            ? t('{punkte} — Hineinzoomen ist hier nicht nötig.', {punkte})
            : t('{punkte}, alle einzeln sichtbar — Hineinzoomen ist hier nicht nötig.', {punkte});
        },
        // Ohne Zoom ein fester Text statt eines leeren Knopfes: der Chip bleibt
        // wie die Zeitraum-Knöpfe daneben immer im Layout stehen und wird nur
        // deaktiviert (siehe Kommentar zu .toolbars in entity_detail.css) —
        // dann braucht er auch ohne Ausschnitt eine Beschriftung.
        get zoomLabel() {
          if (!this.zoomRange) return t('Ausschnitt');
          const from = new Date(this.zoomRange.start);
          const to = new Date(this.zoomRange.end);
          const span = this.zoomRange.end - this.zoomRange.start;
          // Dieselbe Staffelung wie fmt() im Chart: ein Ausschnitt von zwei
          // Stunden wird über die Uhrzeit benannt, einer von zwei Monaten über
          // das Datum. Die Grenzen liegen bewusst über 24 Stunden bzw. einem
          // Jahr, damit ein knapp darunter liegender Ausschnitt nicht zwischen
          // zwei Formaten hin und her springt.
          if (span < 36 * 3600 * 1000) {
            const opts = {hour: '2-digit', minute: '2-digit'};
            return `${from.toLocaleTimeString(LOCALE, opts)} – ${to.toLocaleTimeString(LOCALE, opts)}`;
          }
          if (span < 400 * 86400 * 1000) {
            const opts = {day: '2-digit', month: '2-digit'};
            return `${from.toLocaleDateString(LOCALE, opts)} – ${to.toLocaleDateString(LOCALE, opts)}`;
          }
          const opts = {year: 'numeric'};
          return `${from.toLocaleDateString(LOCALE, opts)} – ${to.toLocaleDateString(LOCALE, opts)}`;
        },
        get markedLabel() {
          if (!this.markedTotal) return t('Keine Markierungen');
          return this.markedTotal === 1 ? t('1 markiert') : t('{count} markiert', {count: this.markedTotal});
        },
        // --- Zustands-Entitäten ---------------------------------------------
        // Abschnitte {s, e, idx, key} in Sekunden über den ganzen geladenen
        // Zeitraum. key ist der Beginn des Abschnitts und verbindet Band im
        // Zeitstrahl und Zeile im Protokoll. Der letzte Abschnitt reicht bis
        // zum Fensterende (bei der laufenden Periode: jetzt).
        get stateSegments() {
          if (!this.isState) return [];
          return StateEntities.segments(this.points, this.windowEnd);
        },
        // Buckets für Anteile/Wechsel: ab dem Monat vom Server (exakt aus den
        // Rollups, auch wo die Rohwerte gekappt wären), darunter aus den
        // Rohwechseln im Browser.
        get stateBuckets() {
          if (!this.isState) return [];
          if (StateEntities.SERVER_SHARE_RANGES.includes(this.range)) {
            return StateEntities.bucketsFromShares(this.sharePoints);
          }
          const [bucketStart, bucketNext] = StateEntities.RANGE_BUCKETS[this.range]();
          return StateEntities.bucketsFromSegments(this.stateSegments, bucketStart, bucketNext);
        },
        // Bezugsfenster für Legende, Kennzahlen und Protokoll: der gezoomte
        // Ausschnitt, sonst der ganze Zeitraum (ROADMAP 1.29: Zoom grenzt alles
        // unter dem Chart ein).
        get stateWindow() {
          if (this.zoomRange) return {a: this.zoomRange.start / 1000, b: this.zoomRange.end / 1000};
          return {a: this.windowStart ?? 0, b: this.windowEnd ?? 0};
        },
        get stateSummary() {
          // Ohne Zoom ab dem Monat aus den Server-Buckets: die Rohwechsel eines
          // Jahrzehnts können gekappt sein, die Rollups nicht. Der längste
          // Abschnitt kommt weiter aus den Rohwechseln, das Rollup kennt ihn nicht.
          if (this.sharePoints.length && !this.zoomRange) {
            const rows = StateEntities.totals(this.stateBuckets, this.states);
            let longest = null;
            for (const seg of this.stateSegments) {
              if (seg.idx !== STATE_UNAVAILABLE && (!longest || seg.e - seg.s > longest.seconds)) {
                longest = {idx: seg.idx, seconds: seg.e - seg.s};
              }
            }
            const na = rows.find(row => row.idx === STATE_UNAVAILABLE);
            return {
              rows,
              changes: rows.reduce((sum, row) => sum + row.entries, 0),
              longest,
              unavailable: na ? na.seconds : 0,
            };
          }
          const {a, b} = this.stateWindow;
          const total = Math.max(b - a, 1);
          const seconds = new Map();
          const entries = new Map();
          let changes = 0;
          let longest = null;
          for (const seg of this.stateSegments) {
            const s = Math.max(seg.s, a);
            const e = Math.min(seg.e, b);
            if (!(e > s)) continue;
            seconds.set(seg.idx, (seconds.get(seg.idx) || 0) + (e - s));
            if (seg.s >= a) {
              entries.set(seg.idx, (entries.get(seg.idx) || 0) + 1);
              changes += 1;
            }
            if (seg.idx !== STATE_UNAVAILABLE && (!longest || e - s > longest.seconds)) {
              longest = {idx: seg.idx, seconds: e - s};
            }
          }
          // Reihenfolge = Index, "nicht verfügbar" zuletzt (ROADMAP 1.29).
          const order = this.states.map(st => st.idx).filter(idx => seconds.has(idx));
          return {
            rows: order.map(idx => ({
              idx, seconds: seconds.get(idx), share: seconds.get(idx) / total * 100, entries: entries.get(idx) || 0,
            })),
            // Der erste Abschnitt im Fenster ist ein Zustand, kein Wechsel.
            changes: Math.max(changes - 1, 0),
            longest,
            unavailable: seconds.get(STATE_UNAVAILABLE) || 0,
          };
        },
        get stateLogRows() {
          const {a, b} = this.stateWindow;
          return this.stateSegments
            .filter(seg => !this.hiddenStates.includes(seg.idx) && seg.e > a && seg.s < b)
            .reverse();
        },
        // Protokoll nach Tagen gruppiert (ab einem Zeitraum über einen Tag), der
        // jüngste zuerst. Die beiden neuesten Tage sind offen, ältere zu, bis
        // man sie öffnet (stateLogDays hält nur die eigenen Umschaltungen).
        get stateLogGroups() {
          const rows = this.stateLogRows;
          if (['hour', 'day'].includes(this.range)) return {multiDay: false, groups: [{key: 'all', segs: rows, open: true}]};
          const byDay = new Map();
          rows.forEach(seg => {
            const key = new Date(seg.s * 1000).toDateString();
            if (!byDay.has(key)) byDay.set(key, []);
            byDay.get(key).push(seg);
          });
          const groups = [...byDay].map(([key, segs], order) => ({
            key, segs, open: key in this.stateLogDays ? this.stateLogDays[key] : order < 2,
          }));
          return {multiDay: true, groups};
        },
        // Zeilen, die tatsächlich ausgeklappt sind — nur sie zählen für
        // "Weitere laden", ein geschlossener Tag verbraucht kein Kontingent.
        get stateLogVisibleRows() {
          return this.stateLogGroups.groups.filter(g => g.open).flatMap(g => g.segs);
        },
        // Protokoll-Einträge: Tageskopf (mit Mini-Leiste der Zusammensetzung)
        // und darunter die Abschnitte als Zeitspanne mit Dauerbalken,
        // begrenzt auf stateLogShown Zeilen.
        get stateLogItems() {
          const {multiDay, groups} = this.stateLogGroups;
          const longest = this.stateLogRows.reduce((m, seg) => Math.max(m, seg.e - seg.s), 1);
          const mkRow = seg => ({
            type: 'row', id: `r${seg.key}`, seg,
            span: `${this.fmtStateTime(seg.s)} – ${this.stateRunning(seg) ? t('jetzt') : this.fmtStateTime(seg.e)}`,
            bar: `width:${Math.max((seg.e - seg.s) / longest * 100, 1.5)}%;${stateSwatchStyle(seg.idx)}`,
          });
          const items = [];
          let left = this.stateLogShown;
          groups.forEach(g => {
            if (multiDay) {
              const total = g.segs.reduce((sum, seg) => sum + (seg.e - seg.s), 0) || 1;
              const count = g.segs.length;
              items.push({
                type: 'day', id: `d${g.key}`, key: g.key, open: g.open,
                label: new Date(g.segs[0].s * 1000).toLocaleDateString(LOCALE, {weekday: 'short', day: '2-digit', month: '2-digit'}),
                countLabel: count === 1 ? t('1 Abschnitt') : t('{count} Abschnitte', {count}),
                strip: [...g.segs].reverse().map(seg => ({key: seg.key, style: `width:${(seg.e - seg.s) / total * 100}%;${stateSwatchStyle(seg.idx)}`})),
              });
            }
            if (!g.open) return;
            for (const seg of g.segs) {
              if (left <= 0) break;
              items.push(mkRow(seg));
              left -= 1;
            }
          });
          return items;
        },
        toggleLogDay(key, open) { this.stateLogDays = {...this.stateLogDays, [key]: !open}; },
        get stateLogTitle() {
          const count = this.stateLogRows.length.toLocaleString(LOCALE);
          return this.zoomRange
            ? t('Wechsel-Protokoll · {count} im Ausschnitt', {count})
            : t('Wechsel-Protokoll · {count}', {count});
        },
        get stateKpiText() {
          const sum = this.stateSummary;
          const parts = [sum.changes === 1 ? t('1 Wechsel') : t('{count} Wechsel', {count: sum.changes.toLocaleString(LOCALE)})];
          if (sum.longest) {
            parts.push(t('längster Abschnitt {dauer} ({zustand})', {
              dauer: NumberFormat.fmtDuration(sum.longest.seconds), zustand: this.stateLabel(sum.longest.idx),
            }));
          }
          parts.push(t('nicht verfügbar {dauer}', {dauer: NumberFormat.fmtDuration(sum.unavailable)}));
          return parts.join(' · ');
        },
        stateLabel(idx) {
          if (idx === STATE_UNAVAILABLE) return t('nicht verfügbar');
          const st = this.states.find(x => x.idx === idx);
          return st ? (st.label || st.text) : String(idx);
        },
        stateRaw(idx) {
          const st = this.states.find(x => x.idx === idx);
          return st ? st.text : '';
        },
        stateSwatch(idx) { return stateSwatchStyle(idx); },
        stateShare(row) { return StateEntities.fmtShare(row.share); },
        setStateMode(mode) {
          this.stateModeChosen = mode;
          if (mode === this.stateMode) return;
          this.stateMode = mode;
          this.zoomRange = null;
          this.render();
        },
        fmtStateDuration(seconds) { return NumberFormat.fmtDuration(seconds); },
        fmtStateTime(ts) {
          return new Date(ts * 1000).toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
        },
        stateRunning(seg) { return this.isCurrent && seg.e >= (this.windowEnd ?? 0); },
        toggleState(idx) {
          const i = this.hiddenStates.indexOf(idx);
          if (i >= 0) this.hiddenStates.splice(i, 1);
          else this.hiddenStates.push(idx);
          this.render();
        },
        // Hover über eine Protokollzeile: Band im Zeitstrahl einrahmen.
        highlightState(key) {
          this.stateHot = key;
          if (!chartInstance || !this.chartIsTimeline) return;
          const seg = key == null ? null : this.stateSegments.find(x => x.key === key);
          chartInstance.setOption({series: [{id: 'main'}, {id: 'hl', data: seg ? [[0, seg.s * 1000, seg.e * 1000]] : []}]});
        },
        // Klick auf ein Band: zur Zeile springen (nachladen, falls sie hinter
        // den bisher gezeigten Einträgen liegt).
        focusStateRow(key) {
          if (!this.stateLog) this.stateLog = true;
          const group = this.stateLogGroups.groups.find(g => g.segs.some(seg => seg.key === key));
          if (!group) return;
          if (!group.open) this.stateLogDays = {...this.stateLogDays, [group.key]: true};
          const pos = this.stateLogVisibleRows.findIndex(seg => seg.key === key);
          if (pos >= this.stateLogShown) this.stateLogShown = Math.ceil((pos + 1) / STATE_LOG_PAGE) * STATE_LOG_PAGE;
          this.stateHot = key;
          this.$nextTick(() => {
            const row = document.querySelector(`.state-log [data-key="${key}"]`);
            if (row) row.scrollIntoView({block: 'nearest', behavior: 'smooth'});
          });
        },
        toggleMarked() {
          this.showMarked = !this.showMarked;
          this.load();
          this.saveChartOptions();
        },
        resetZoom() {
          if (!chartInstance) return;
          chartInstance.dispatchAction({type: 'dataZoom', start: 0, end: 100});
          this.zoomRange = null;
        },

        setRange(key) {
          // Zweitfunktion der schon aktiven Stufe: zurück auf die laufende
          // Periode. Sie ersetzt den früheren "Jetzt"-Knopf, der dauerhaft
          // Platz in der Leiste belegte und die meiste Zeit deaktiviert war —
          // dieselbe Geste kennt das Energiedashboard schon (setRange() in
          // energiedashboard.js). Steht die Ansicht bereits auf "jetzt",
          // passiert wie bisher nichts.
          if (key === this.range) {
            if (this.offset !== 0) this.goToNow();
            return;
          }
          // Nicht auf offset=0 ("jetzt") zurückspringen: Aus dem tatsächlich
          // angezeigten Serverfenster einen zeitlichen Anker bilden und diesen
          // in den Offset der neuen Auflösung übersetzen. So bleibt beim
          // Zoomen Tag→Stunde das Datum, Woche→Tag die Woche usw. erhalten.
          const anchorMs = this._rangeAnchorMs ?? PeriodNavigation.anchorForWindow(
            this.windowStart, this.windowEnd, this.isCurrent
          );
          this._rangeAnchorMs = anchorMs;
          const nextOffset = PeriodNavigation.offsetForRange(key, anchorMs);
          this.range = key;
          this.compare = false;
          // Sonst bliebe ein "Vorjahres…"-Modus an einem Zeitraum stehen, der
          // ihn gar nicht mehr anbietet — sichtbar würde das erst indirekt,
          // etwa in der Vorbelegung von saveAsChartUrl.
          if (!compareYearAvailable(key)) this.compareMode = 'previous';
          // Der Zeitstrahl zeichnet AN/AUS-Übergänge und braucht Rohwerte in
          // jedem Zeitraum — sonst kämen gebucketete Daten, bis man auf
          // Balken und zurück schaltet.
          this.raw = this.chartType === 'timeline';
          if (this.isState) this.stateMode = stateModeFor(key, this.stateModeChosen);
          this.offset = nextOffset;
          this.continuous = false;
          this.load();
        },
        goBack() { this._rangeAnchorMs = null; this.offset -= 1; this.load(); },
        goForward() {
          if (this.canGoForward) { this._rangeAnchorMs = null; this.offset += 1; this.load(); }
        },
        goToNow() { this._rangeAnchorMs = null; this.offset = 0; this.load(); },
        // Optionen-Menü (Konzept "Pro Entität speicherbare Chart-Optionen") —
        // kein Speichern-Button, jede Änderung im Menü sendet sofort den
        // gesamten aktuellen Options-Stand. Die Anzeige hat sich schon
        // (optimistisch, über die reaktiven Alpine-Felder) geändert und wird
        // bei einem Fehler nicht zurückgedreht — beim nächsten Laden der
        // Seite greift wieder der zuletzt tatsächlich gespeicherte Stand.
        // Gesagt wird es aber: vorher blieb ein 4xx/5xx (fetch() wirft dabei
        // nicht) wie ein Netzwerkfehler völlig stumm, die Einstellung sah
        // gespeichert aus und war beim nächsten Besuch weg.
        async saveChartOptions() {
          let response = null;
          try {
            response = await fetch(`${BASE}/entities/${ENTITY_ID}/chart-options`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                continuous: this.continuous, raw: this.raw, chart_type: this.chartType,
                show_points: this.showPoints, show_values: this.showValues,
                dynamic_y_axis: this.dynamicYAxis, chart_stats: this.chartStats,
                show_marked: this.showMarked,
                average_line: this.averageLine,
                legend_metrics: this.legendMetrics, legend_style: this.legendStyle,
                decimals: this.decimals, state_log: this.stateLog,
              }),
            });
          } catch (e) {
            // Netzwerkfehler: wie ein Fehlerstatus, siehe unten.
          }
          if (!response || !response.ok) {
            appAlert(t('Chart-Optionen konnten nicht gespeichert werden'));
          }
        },
        // "Auf Standard zurücksetzen" (Optionen-Menü, Aktionen) — wirft die
        // individuelle Übersteuerung dieser Entität weg und lädt die Seite
        // neu, damit alle Felder wieder die (dann wieder live geltenden)
        // globalen Defaults zeigen, statt sie hier einzeln nachzubauen.
        async resetChartOptions() {
          let response = null;
          try {
            response = await fetch(`${BASE}/entities/${ENTITY_ID}/chart-options/reset`, {method: 'POST'});
          } catch (e) {
            // Netzwerkfehler: wie ein Fehlerstatus, siehe unten.
          }
          // Neu laden in jedem Fall — die Seite zeigt danach den tatsächlich
          // gespeicherten Stand. Ein Fehlschlag wird vorher gemeldet, sonst
          // sähe ein unverändertes Neuladen wie ein gelungenes Zurücksetzen aus.
          if (!response || !response.ok) {
            await appAlert(t('Chart-Optionen konnten nicht zurückgesetzt werden'));
          }
          location.reload();
        },
        // Vergleicht den aktuellen (reaktiven) Options-Stand gegen die
        // globalen Startwerte — chart_type dabei aufgelöst wie oben bei
        // chartType, da CHART_DEFAULTS.chart_type "auto" sein kann, this.
        // chartType aber nie.
        hasCustomOptions() {
          const defaultChartType = CHART_DEFAULTS.chart_type === 'auto' ? DEFAULT_CHART_TYPE : CHART_DEFAULTS.chart_type;
          return (
            this.continuous !== CHART_DEFAULTS.continuous ||
            this.raw !== (defaultChartType === 'timeline' && this.chartType === 'timeline' ? true : CHART_DEFAULTS.raw) ||
            this.chartType !== defaultChartType ||
            this.showPoints !== CHART_DEFAULTS.show_points ||
            this.showValues !== CHART_DEFAULTS.show_values ||
            this.dynamicYAxis !== CHART_DEFAULTS.dynamic_y_axis ||
            this.chartStats !== CHART_DEFAULTS.chart_stats ||
            this.showMarked !== CHART_DEFAULTS.show_marked ||
            this.averageLine !== CHART_DEFAULTS.average_line ||
            this.legendStyle !== CHART_DEFAULTS.legend_style ||
            this.decimals !== CHART_DEFAULTS.decimals ||
            this.stateLog !== (CHART_DEFAULTS.state_log !== false) ||
            JSON.stringify([...this.legendMetrics].sort()) !== JSON.stringify([...CHART_DEFAULTS.legend_metrics].sort())
          );
        },
        // Nachkommastellen-Wahl (Optionen-Menü, Darstellung) — aktualisiert
        // DECIMALS (von fmtValue() gelesen) sofort mit, ein Neuladen reicht
        // sonst nicht: der Chart selbst braucht dafür kein neues /api/query
        // (nur die Formatierung ändert sich), deshalb render() statt load().
        setDecimals(value) {
          this.decimals = value;
          DECIMALS = decimalsStringToInt(value);
          this.render();
          this.saveChartOptions();
        },
        toggleContinuous() { this._rangeAnchorMs = null; this.continuous = !this.continuous; this.load(); this.saveChartOptions(); },
        setCompareMode(mode) {
          this.compare = true;
          this.compareMode = mode;
          this.raw = false;
          this.compareMenuOpen = false;
          this.load();
        },
        disableCompare() {
          this.compare = false;
          this.compareMenuOpen = false;
          this.load();
        },
        // Raw-Modus (Konzept Abschnitt 06/10 "Hohe Dichte") zeigt Einzelmesswerte
        // statt Buckets — ein Periodenvergleich zweier Rohwert-Serien ergibt kaum
        // lesbaren Sinn, deshalb schließen sich beide Modi gegenseitig aus, und
        // Balken (eine Bucket-Summe pro Balken) weichen automatisch der Linie.
        toggleRaw() {
          this.raw = !this.raw;
          if (this.raw) { this.compare = false; this.chartType = 'line'; }
          this.load();
          this.saveChartOptions();
        },

        // Diagrammtyp-Wahl (Linie/Balken/Zeitstrahl) — eigene Methode statt
        // wie bisher direkt im Klick-Handler, weil "Zeitstrahl" zusätzlich
        // Rohwerte erzwingt (er zeichnet AN/AUS-Übergänge, keine Buckets) und
        // "Balken" Rohwerte ausschließt (Bucket-Summen ergeben nur gebuckelt
        // Sinn) — die drei Buttons bleiben dadurch IMMER klickbar (kein
        // :disabled="raw" mehr nötig, das Zeitstrahl↔Linie/Balken sonst
        // gegenseitig blockiert hätte), jeder Klick stellt raw/compare
        // selbst passend ein.
        setChartType(type) {
          this.chartType = type;
          if (type === 'timeline') {
            this.raw = true;
            this.compare = false;
          } else if (type === 'bar') {
            this.raw = false;
          }
          this.load();
          this.saveChartOptions();
        },

        async load() {
          // Schutz gegen Race-Bedingungen: wenn schnell zwischen Zeiträumen geklickt
          // wird, kann eine ältere Anfrage nach einer neueren zurückkommen — ohne
          // diese Prüfung würde dann kurzzeitig der falsche Zeitraum gerendert
          // (Achsen-Skalierung/Daten aus zwei verschiedenen Anfragen gemischt).
          const requestId = ++this._requestId;
          this.loading = true;
          const params = new URLSearchParams({
            entity_id: ENTITY_ID, range: this.range, offset: String(this.offset),
            continuous: String(this.continuous), compare: String(this.compare),
            // /api/query kennt nur "line"/"bar" (siehe api_query in main.py) — im
            // Zeitstrahl-Modus wird ohnehin immer raw=true gesendet, wofür der
            // Server chart_type unbeachtet lässt, daher hier "line" als gültiger
            // Platzhalter statt des ungültigen Werts "timeline".
            compare_mode: this.compareMode, raw: String(this.raw),
            chart_type: this.chartType === 'timeline' ? 'line' : this.chartType,
          });
          // Nur anfordern, wenn die Anzeige auch eingeschaltet ist: die
          // Markierungen kosten serverseitig einen eigenen Lauf über Hot Buffer
          // und Archiv, und im Normalfall (Entität ohne offene Markierungen)
          // wäre der Ertrag eine leere Liste.
          if (this.showMarked) params.set('marked', 'true');
          // Zustände ab dem Monat: zusätzlich die Anteile je Bucket, parallel
          // zu den Rohwechseln (raw bleibt bei Zuständen immer true).
          const shareParams = new URLSearchParams(params);
          shareParams.set('raw', 'false');
          shareParams.delete('marked');
          const wantShares = this.isState && StateEntities.SERVER_SHARE_RANGES.includes(this.range);
          const [res, shareRes] = await Promise.all([
            fetch(`${BASE}/api/query?${params}`),
            wantShares ? fetch(`${BASE}/api/query?${shareParams}`) : Promise.resolve(null),
          ]);
          const data = await res.json();
          const shareData = shareRes ? await shareRes.json() : null;
          if (requestId !== this._requestId) return; // eine neuere Anfrage ist schon unterwegs/angekommen
          this.sharePoints = shareData && shareData.chart_type === 'shares' ? shareData.points : [];
          this.points = data.points || [];
          this.comparePoints = data.compare_points || [];
          this.compareWindowStart = data.compare_window_start ?? null;
          this.compareWindowEnd = data.compare_window_end ?? null;
          this.aggregationType = data.aggregation_type;
          this.states = data.states || [];
          this.stateLogShown = STATE_LOG_PAGE;
          this.stateLogDays = {};
          this.windowStart = data.window_start;
          this.windowEnd = data.window_end;
          this.periodEnd = data.period_end ?? data.window_end;
          this.isCurrent = data.is_current;
          this.markedRanges = data.marked_ranges || [];
          this.markedTotal = data.marked_total || 0;
          this.loading = false;
          this.$nextTick(() => this.render());
        },

        render() {
          if (!this.points.length) return;
          if (!chartInstance) {
            chartInstance = echarts.init(document.getElementById('chart'));
            // Einmalig bei der Instanzerzeugung registriert, nicht bei jedem
            // Rendern — sonst stapeln sich mit jedem Neuzeichnen weitere
            // Handler auf derselben Instanz.
            chartInstance.on('click', (p) => {
              if (this.isState && p.seriesId === 'main') this.focusStateRow(p.value[4]);
              else if (this.isState && String(p.seriesId).startsWith('state-')) this.drillState(p.dataIndex);
            });
            chartInstance.on('mouseover', (p) => {
              if (this.isState && p.seriesId === 'main') this.stateHot = p.value[4];
            });
            chartInstance.on('mouseout', () => { if (this.isState) this.stateHot = null; });
            chartInstance.on('dataZoom', () => {
              const [zoom] = chartInstance.getOption().dataZoom || [];
              this.zoomRange = visibleWindow(
                zoom,
                this.windowStart != null ? this.windowStart * 1000 : null,
                this.periodEnd != null ? this.periodEnd * 1000 - 1000 : null,
              );
            });

            // Umschalt gedrückt = Bereich aufziehen. Die Taste statt eines
            // Modus-Knopfes, weil das schlichte Ziehen ohnehin unbelegt ist
            // (Schwenken liegt auf Strg+Ziehen, Zoomen auf Strg+Rad) und weil
            // es auf einem Touchscreen keine Umschalttaste gibt — dort bleibt
            // der Wisch der Seite, ohne dass es dafür eine Sonderregel
            // bräuchte. Dieselbe Regel wie beim Rest: Taste halten heißt "ich
            // meine den Chart".
            let scharf = false;
            const setzeAuswahl = (an) => {
              if (an === scharf) return;
              scharf = an;
              chartInstance.dispatchAction({
                type: 'takeGlobalCursor',
                key: 'brush',
                // brushType:false ist das dokumentierte Ausschalten — ein
                // fehlendes brushOption ließe den Modus stehen.
                brushOption: an ? {brushType: 'lineX', brushMode: 'single'} : {brushType: false},
              });
            };
            chartInstance.on('brushEnd', (params) => {
              const spanne = ((params.areas || [])[0] || {}).coordRange;
              // Die Auswahl sofort wieder aufheben: sonst bleibt das gezogene
              // Rechteck als graue Fläche über dem Chart liegen, obwohl es
              // seine Aufgabe erfüllt hat.
              chartInstance.dispatchAction({type: 'brush', areas: []});
              // Ein Klick ohne Ziehen liefert eine Spanne von null Breite —
              // daraus einen Zoom zu machen hieße, auf nichts zu zoomen.
              if (!spanne || !(spanne[1] > spanne[0])) return;
              chartInstance.dispatchAction({
                type: 'dataZoom', startValue: spanne[0], endValue: spanne[1],
              });
            });
            // Ein gerade laufendes Ziehen darf nicht mitten im Rechteck
            // abgeschaltet werden — wer die Taste vor der Maustaste loslässt,
            // hinterließe sonst einen halb gezogenen Rahmen. Der Zustand wird
            // deshalb gemerkt und erst beim Loslassen der Maus nachgezogen.
            let zieht = false;
            let taste = false;
            const nachziehen = () => { if (!zieht) setzeAuswahl(taste); };
            chartInstance.getZr().on('mousedown', () => { zieht = true; });
            chartInstance.getZr().on('mouseup', () => { zieht = false; nachziehen(); });
            document.addEventListener('keydown', (e) => {
              if (e.key !== 'Shift' || !this.zoomAvailable) return;
              taste = true;
              nachziehen();
            });
            document.addEventListener('keyup', (e) => {
              if (e.key !== 'Shift') return;
              taste = false;
              nachziehen();
            });
            // Fensterwechsel bei gedrückter Taste: das keyup kommt dann nie an,
            // und der Chart bliebe dauerhaft im Auswahlmodus.
            window.addEventListener('blur', () => { taste = false; zieht = false; nachziehen(); });
          }
          // Der Zoom ist in absoluten Zeitstempeln eines Fensters ausgedrückt,
          // das sich beim Neuzeichnen geändert haben kann. setOption(…, true)
          // weiter unten wirft die dataZoom-Komponente ohnehin weg (notMerge),
          // hier zieht nur der Chip nach.
          this.zoomRange = null;
          const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
          // Kurze, zum Zeitraum passende Beschriftung statt immer Datum+Uhrzeit —
          // sonst überlappen sich die Achsenbeschriftungen bei vielen Punkten.
          const fmt = ts => {
            const d = new Date(ts * 1000);
            if (this.range === 'hour' || this.range === 'day') {
              return d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
            }
            if (this.range === 'week' || this.range === 'month') {
              return d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit'});
            }
            if (this.range === 'decade') {
              return d.toLocaleDateString(LOCALE, {year: 'numeric'});
            }
            return d.toLocaleDateString(LOCALE, {month: 'short', year: 'numeric'});
          };
          const tooltipBucketSeconds = detectResolutionSeconds(this.points);

          if (this.isState && this.stateMode !== 'timeline') {
            this.renderStateBars(fmt);
            return;
          }
          if (this.chartType === 'timeline') {
            this.renderTimeline(fmt);
            return;
          }

          // Drittes Element je Datenpunkt: die TATSÄCHLICHE Zeit (unverschoben) —
          // bei der Hauptreihe identisch zum x-Wert, bei der Vorperiode weiter unten
          // nicht (die wird zeitlich verschoben, um sie optisch zu überlagern). Das
          // Tooltip liest weiter unten bewusst dieses Feld statt des x-Werts, sonst
          // würde die Vorperiode im Tooltip die heutige statt ihre echte Zeit zeigen.
          const mainData = this.points.map(p => [p.ts * 1000, p.value, p.ts * 1000]);
          if (this.chartType === 'line' && mainData.length && this.windowEnd != null
              && mainData[mainData.length - 1][0] < this.windowEnd * 1000) {
            const last = mainData[mainData.length - 1];
            mainData.push([this.windowEnd * 1000, last[1], last[2]]);
          }
          const series = [{
            name: DISPLAY_NAME,
            type: this.chartType,
            data: mainData,
            itemStyle: {color: getComputedStyle(document.body).getPropertyValue(this.chartType === 'bar' ? '--chart-bar' : '--chart-line')},
            lineStyle: {width: 1.5},
            smooth: this.chartType === 'line',
            // "Werte anzeigen" (Optionen-Menü) — Zahl direkt über jedem Balken/Punkt,
            // zusätzlich zum Tooltip. Ohne Einheit (die steht schon an der
            // Y-Achse) — hier reicht die reine Zahl.
            label: {
              show: this.showValues,
              position: 'top',
              fontSize: Math.round(10.5 * uiFontScale * 10) / 10,
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              formatter: params => fmtValue(params.value[1]),
            },
            // Ohne Deckelung errechnet ECharts die Balkenbreite auf einer
            // Zeit-Achse aus dem Abstand zu benachbarten Punkten — bei nur
            // einem einzigen Punkt (z. B. t("Jahr") einer Entität, die erst
            // diesen Monat zu senden begann) fehlt dieser Bezugspunkt völlig,
            // wodurch der Balken einen Großteil der (jetzt bewusst bis zum
            // Fensterende reichenden, siehe xAxis min/max oben) Achse
            // einnimmt, statt nur die tatsächliche Bucket-Breite. barMaxWidth
            // greift nur in diesem Sparse-Fall, bei vielen Balken ist die
            // Auto-Breite ohnehin längst kleiner als das Limit.
            barMaxWidth: 48,
            // Ein Balken auf einer Zeit-Achse (kein boundaryGap, s. o.) sitzt
            // mit seiner Mitte GENAU auf dem Bucket-Zeitstempel — beim
            // ersten/letzten Bucket liegt die Hälfte der Balkenbreite dadurch
            // zwangsläufig knapp jenseits von min/max und würde ohne dies
            // hart am Diagrammrand abgeschnitten wirken.
            clip: this.chartType !== 'bar',
          }];
          // symbol nie explizit auf undefined setzen (nur weglassen oder auf 'none') —
          // ECharts' interne Options-Normalisierung stürzt bei einem explizit
          // undefined gesetzten Schlüssel ab (siehe Kommentar bei der Legende unten).
          if (this.chartType === 'line' && !this.showPoints) {
            series[0].symbol = 'none';
          }
          if (this.compare && this.comparePoints.length) {
            // Die Vorperiode um die exakte Differenz der beiden Fenster-Startzeiten
            // verschieben, NICHT per Array-Index auf die Hauptreihe mappen — Index-
            // Mapping brach, sobald die aktuelle Periode weniger Punkte hatte als die
            // Vorperiode (z. B. eine gerade erst begonnene Stunde vs. eine volle
            // Vorstunde): die überzähligen Punkte fielen auf ihre ursprünglichen,
            // unverschobenen Zeitstempel zurück, wodurch die Linie mitten im Chart
            // rückwärts auf der Zeitachse sprang — sichtbar als eine einzelne lange,
            // quer durchs Diagramm laufende Gerade. Ein fester Zeit-Offset bleibt
            // dagegen für beliebig unterschiedlich lange Punktreihen korrekt.
            const shiftMs = (this.compareWindowStart != null && this.windowStart != null)
              ? (this.windowStart - this.compareWindowStart) * 1000
              : 0;
            // Label der Vorperiode aus ihrem eigenen, vom Server gelieferten Fenster
            // — NICHT aus der (evtl. noch unvollständigen) Dauer der aktuellen
            // Periode abgeleitet, sonst zeigt das Label bei einer gerade erst
            // begonnenen Stunde nur die paar verstrichenen Minuten der Vorstunde
            // statt der vollen Vorstunde. Bei compareMode="year" bleibt der offset
            // gegenüber der Hauptperiode gleich (nur das Fenster ist serverseitig
            // um ein Jahr verschoben) — offset-1 würde hier fälschlich t("Gestern")
            // statt des tatsächlichen Datums vor einem Jahr anzeigen, deshalb
            // skipRelative=true für diesen Modus.
            const compareOffset = this.compareMode === 'year' ? this.offset : this.offset - 1;
            const compareLabel = formatPeriodLabel(
              this.range, this.continuous, this.compareWindowStart, this.compareWindowEnd,
              compareOffset, false, this.compareMode === 'year'
            );
            const seriesLabel = this.compareMode === 'year' ? previousYearPeriodLabel(this.range) : previousPeriodLabel(this.range);
            const compareData = this.comparePoints.map(p => [p.ts * 1000 + shiftMs, p.value, p.ts * 1000]);
            if (this.chartType === 'line' && compareData.length && this.windowEnd != null
                && compareData[compareData.length - 1][0] < this.windowEnd * 1000) {
              const last = compareData[compareData.length - 1];
              compareData.push([this.windowEnd * 1000, last[1], last[2]]);
            }
            const compareSeries = {
              name: compareLabel ? `${seriesLabel} (${compareLabel})` : seriesLabel, type: this.chartType,
              data: compareData,
              itemStyle: {opacity: 0.45, color: getComputedStyle(document.body).getPropertyValue('--chart-bar')},
              lineStyle: {width: 1.5},
              barMaxWidth: 48,
              clip: this.chartType !== 'bar',
            };
            if (this.chartType === 'line' && !this.showPoints) {
              compareSeries.symbol = 'none';
            }
            if (this.chartType === 'line') compareSeries.smooth = true;
            series.push(compareSeries);
          }
          // Zur Löschung markierte Bereiche als senkrechte Bänder. markArea
          // und nicht eine zweite Serie: eine Serie bestimmte die
          // Achsenskalierung mit, verfälschte die Punkt-Schwelle des Zooms und
          // stünde in der Legende.
          //
          // Ein Band braucht nur die x-Achse und trifft damit keine Aussage
          // über den Wert — deshalb gilt es für JEDEN Entitätstyp. (Ein
          // Marker auf dem entfernten Wert hätte das nicht gekonnt: bei einem
          // Zähler sind Bucket-Werte Zuwächse und Rohwerte absolute Stände,
          // ein markierter Stand von 45.213 kWh in einem Chart mit
          // Tageszuwächsen um 12 kWh zerrisse die Achse.)
          if (this.showMarked && this.markedRanges.length) {
            const dangerSoft = getComputedStyle(document.body).getPropertyValue('--danger-soft');
            const danger = getComputedStyle(document.body).getPropertyValue('--danger');
            series[0].markArea = {
              // silent: das Band ist Beiwerk. Ohne dies fängt es die
              // Mauszeiger-Ereignisse ab und das Achsen-Tooltip der Kurve
              // bleibt ausgerechnet an den interessanten Stellen aus.
              silent: true,
              itemStyle: {color: dangerSoft, opacity: 0.85, borderColor: danger, borderWidth: 0},
              // Ein Block aus genau einer Markierung hat start == end und wäre
              // ohne Mindestbreite unsichtbar — ein Band von null Pixeln. Ein
              // Dreihundertstel des Fensters entspricht der Schwelle, mit der
              // der Server Blöcke zusammenfasst (siehe
              // marked_ranges_in_window()), also etwa drei Pixeln.
              data: this.markedRanges.map(b => {
                const mindest = (this.periodEnd - this.windowStart) / 300;
                const breite = Math.max(b.end - b.start, mindest);
                const mitte = (b.start + b.end) / 2;
                return [
                  {xAxis: (mitte - breite / 2) * 1000},
                  {xAxis: (mitte + breite / 2) * 1000},
                ];
              }),
            };
          }
          // Durchschnittslinie (Optionen-Menü, "Darstellung"). Sie sitzt auf der
          // Hauptserie, nicht zusätzlich auf der Vergleichsserie: die Legende
          // nennt genau einen Durchschnitt, nämlich den der gezeigten Periode
          // — zwei Linien für zwei Perioden wären eine zweite Aussage, die
          // dort niemand ablesen kann.
          //
          // Der Wert kommt aus denselben Punkten wie die Legende, Zahl und
          // Linie stimmen hier also immer überein.
          const durchschnitt = this.averageLine ? averageOf(this.points.map(p => p.value)) : null;
          if (durchschnitt !== null) {
            series[0].markLine = {
              // Wie beim Markierungs-Band: die Linie ist Beiwerk und darf das
              // Achsen-Tooltip der Kurve nicht abfangen.
              silent: true,
              symbol: 'none',
              lineStyle: {
                color: getComputedStyle(document.body).getPropertyValue('--accent-line'),
                type: 'dashed', width: 1,
              },
              // Rechts innen, weil oben links die Einheit der y-Achse steht.
              label: {
                position: 'insideEndTop',
                fontSize: Math.round(10.5 * uiFontScale * 10) / 10,
                color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
                formatter: () => `Ø ${fmtValue(durchschnitt)}`,
              },
              data: [{yAxis: durchschnitt}],
            };
          }
          const option = {
            textStyle: {
              fontFamily: getComputedStyle(document.body).getPropertyValue('--font-mono'),
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * uiFontScale * 10) / 10,
            },
            // containLabel: reserviert automatisch genug Platz für die Y-Achsen-
            // Beschriftung — ein fester left-Wert reichte bei längeren formatierten
            // Werten (z. B. "0.0347 kWh" bei kleinen Zähler-Deltas) nicht immer aus
            // und schnitt die führende Ziffer links ab, was wie eine falsche/
            // unplausible Skala aussah, obwohl nur die Beschriftung geclippt war.
            grid: {left: 10, right: 20, top: (UNIT || DURATION_DISPLAY) ? 36 : 20, bottom: this.compare ? 64 : 40, containLabel: true},
            // min/max explizit auf das Abfragefenster fixiert, statt ECharts
            // per Default auf den tatsächlichen Datenbereich auto-fitten zu
            // lassen — sonst hört die Achse (und damit sichtbar der Chart) beim
            // letzten tatsächlichen Wert auf, z. B. bei einer Entität, die seit
            // Stunden nichts mehr gemeldet hat, statt konsistent bis zum
            // Fensterende (bei t("Heute") also bis zur aktuellen Uhrzeit) zu reichen.
            xAxis: {
              type: 'time',
              min: this.windowStart != null ? this.windowStart * 1000 : undefined,
              // periodEnd statt windowEnd: eine laufende Periode (z. B. Woche)
              // zeigt so bis zur vollen Kalendergrenze (Sonntag), auch für die
              // noch datenlose Zukunft — windowEnd (an "jetzt" gedeckelt) bleibt
              // nur für den Linien-Haltepunkt maßgeblich (siehe periodEnd oben).
              // Eine Sekunde zurück, da periodEnd EXKLUSIV ist — sonst reicht die
              // Achse sichtbar bis zum Beginn der nächsten Periode (ein Achsen-Tick
              // "01.09." für einen Monat, der am 31.08. endet).
              max: this.periodEnd != null ? this.periodEnd * 1000 - 1000 : undefined,
              // ECharts polstert eine Zeit-Achse mit Balken-Serie sonst zusätzlich
              // über min/max hinaus (sichtbar z. B. bei t("Jahr")/t("Dekade") mit nur
              // einem Bucket: die Achse reichte weit über das eigentliche Fenster
              // hinaus) — boundaryGap:[0,0] unterbindet dieses Auto-Polster, min/max
              // bleiben dadurch die tatsächlichen Achsengrenzen.
              boundaryGap: [0, 0],
              // Woche/Monat bucketen tagesweise (siehe fmt() oben, "dd.mm."-
              // Beschriftung) — mit fest erzwungenem Tages-Interval statt
              // ECharts' automatischer "nice tick"-Berechnung, die trotz
              // explizitem max (s. o.) gelegentlich einen zusätzlichen Tick
              // (Gitternetz, nicht nur Label) jenseits der Periodengrenze
              // erzeugt — sichtbar als 8. statt 7. Einteilung bei t("Woche").
              // Nicht für Jahr/Dekade erzwungen: Monate/Jahre sind
              // unterschiedlich lang, ein fester Millisekunden-Interval
              // würde dort falsch ausgerichtete Ticks erzeugen.
              interval: ['week', 'month'].includes(this.range) ? 24 * 60 * 60 * 1000 : undefined,
              axisLabel: {
                // ECharts' "nice tick"-Berechnung polstert die Achse intern
                // minimal über max hinaus (trotz explizitem max oben) — ohne
                // diese Sperre erschiene sonst vereinzelt noch ein Tick jenseits
                // der Periodengrenze (z. B. "01.09." für einen Monat, der am
                // 31.08. endet). this.periodEnd (EXKLUSIV, Beginn der Folge-
                // periode) ist die Grenze, ab der die Beschriftung unterdrückt wird.
                formatter: v => (this.periodEnd != null && v >= this.periodEnd * 1000) ? '' : fmt(v / 1000),
                hideOverlap: true,
              },
            },
            yAxis: {
              type: 'value',
              name: DURATION_DISPLAY ? t('Dauer') : (UNIT || undefined),
              nameLocation: 'end',
              nameTextStyle: {align: 'left'},
              // "fest" bindet die Achse an 0, "dynamisch" lässt ECharts frei auf
              // den tatsächlichen Datenbereich skalieren (scale:true, sonst
              // erzwingt eine value-Achse per Default immer die Einbindung der
              // Null). Bei Balken NIE dynamisch, selbst wenn eingeschaltet: ein
              // Balken zeichnet immer von der Null-Basislinie zum Wert — liegt
              // die auto-skalierte Achse nicht bei 0 (z. B. min knapp unter dem
              // kleinsten Balkenwert), ragt der Balken optisch über den unteren
              // Achsenrand hinaus, statt sauber auf der x-Achse zu stehen.
              min: (this.dynamicYAxis && this.chartType !== 'bar') ? undefined : value => Math.min(0, value.min),
              max: (this.dynamicYAxis && this.chartType !== 'bar') ? undefined : value => Math.max(0, value.max),
              scale: this.dynamicYAxis && this.chartType !== 'bar',
              axisLabel: {formatter: v => UNIT ? `${fmtValue(v)} ${UNIT}` : fmtValue(v)},
            },
            tooltip: {
              trigger: 'axis',
              // Eigener Formatter statt valueFormatter: bei aktivem Vergleich teilen
              // sich beide Serien sonst einen gemeinsamen Zeit-Header (den x-Wert der
              // Achsenposition) — für die Vorperiode ist das aber der VERSCHOBENE,
              // nicht der tatsächliche Zeitpunkt (siehe Kommentar bei shiftMs oben).
              // Jede Zeile bekommt hier stattdessen ihre eigene echte Zeit aus dem
              // dritten Datenfeld — nur wenn sie tatsächlich auseinanderfällt
              // (aktiver Vergleich, der Normalfall ohne Vergleich hat ohnehin nur
              // eine Zeile): sonst eine gemeinsame Kopfzeile statt das Datum je
              // Zeile zu wiederholen (dieselbe Logik wie chart_editor.html). Immer
              // einheitlich ÜBER dem Wert. Keine Serien-Bezeichnung (Farbpunkt vom
              // Marker reicht zur Unterscheidung zwischen Haupt- und Vorperiode).
              formatter: params => {
                const times = params.map(p => fmtTooltipTimestamp(p.data[2], tooltipBucketSeconds));
                const sameTime = times.every(t => t === times[0]);
                const row = p => {
                  const val = UNIT ? `${fmtValue(p.value[1])} ${UNIT}` : fmtValue(p.value[1]);
                  return `<span style="display:flex;align-items:center;gap:5px;">${p.marker}<strong>${val}</strong></span>`;
                };
                if (sameTime) {
                  return `<div style="font-size:calc(11px * var(--font-scale, 1));color:var(--ink-faint);margin-bottom:4px;">${times[0]}</div>`
                       + params.map(p => `<div style="margin-bottom:2px;">${row(p)}</div>`).join('');
                }
                return params.map((p, i) =>
                  `<div style="display:flex;align-items:center;gap:5px;font-size:calc(11px * var(--font-scale, 1));color:var(--ink-faint);margin-bottom:2px;">${p.marker}${times[i]}</div>`
                  + `<strong style="display:block;margin-bottom:4px;">${UNIT ? `${fmtValue(p.value[1])} ${UNIT}` : fmtValue(p.value[1])}</strong>`
                ).join('');
              },
              // appendToBody: die Karte hat overflow:hidden (verhindert, dass die
              // Zeitraum-Buttons das Seitenlayout sprengen) — ohne appendToBody
              // würde das Tooltip an den Kartenrändern abgeschnitten statt sichtbar
              // über den Chart hinauszuragen.
              appendToBody: true,
            },
            series,
          };
          // legend nur setzen, wenn wirklich gebraucht — ein explizit auf undefined
          // gesetzter Komponenten-Key (statt ihn wegzulassen) lässt ECharts beim
          // internen Normalisieren mit einer TypeError abstürzen, wodurch nicht nur
          // die Legende fehlt, sondern serverseitig auch das komplette Tooltip nie
          // rendert (der Fehler reißt den ganzen Render-Zyklus ab).
          if (this.compare) option.legend = {bottom: 0};
          // Bedingt zugewiesen statt "dataZoom: … : undefined" — aus demselben
          // Grund wie legend direkt darüber: ein explizit auf undefined
          // gesetzter Komponenten-Key reißt beim internen Normalisieren den
          // ganzen Render-Zyklus ab.
          //
          // Der Vergleichsmodus bleibt ausdrücklich eingeschlossen: die zweite
          // Serie liegt auf derselben Achse, der Zoom erfasst also beide — ein
          // Ausschnitt, in dem Vorperiode und aktuelle Periode gemeinsam
          // vergrößert sind, ist eher nützlicher als die Gesamtansicht.
          if (this.zoomAvailable) {
            option.dataZoom = [zoomConfig(
              (this.dynamicYAxis && this.chartType !== 'bar') ? 'filter' : 'none'
            )];
            option.brush = selectBrush();
            option.toolbox = {show: false};
          }
          chartInstance.setOption(option, true);
          chartInstance.resize();
        },

        // Zeitstrahl: statt Bucket-Summen (Balken) oder einer geglätteten 0/1-Linie
        // ein durchgehendes Band mit genau den AN-Intervallen — aus den Rohpunkten
        // (this.points, hier per raw=true immer Einzelmesswerte) durch Paarung
        // aufeinanderfolgender Zeitstempel gebildet: Punkt i beginnt ein Intervall,
        // das beim nächsten Punkt endet (oder am Fensterende, falls i der letzte
        // Punkt ist) — dieselbe Paarungslogik, mit der rollup.py schon heute
        // on_seconds je Bucket berechnet, hier nur zum Zeichnen statt Aufsummieren.
        // AUS-Abschnitte werden bewusst NICHT gezeichnet (Lücke im Band) statt als
        // eigene, andersfarbige Segmente — reduziert auf die eigentliche Frage
        // "wann AN", ohne dass ein zusätzlicher Rahmen um den ganzen Zeitraum nötig
        // wäre, um "kein Wert" von "AUS" zu unterscheiden.
        renderTimeline(fmt) {
          const intervals = [];
          if (this.isState) {
            // Ein Band je Abschnitt, Farbe nach Zustand; ausgeblendete fehlen.
            // Viertes/fünftes Element: Zustandsindex und Schlüssel der Zeile im
            // Wechsel-Protokoll.
            for (const seg of this.stateSegments) {
              if (!this.hiddenStates.includes(seg.idx)) intervals.push([0, seg.s * 1000, seg.e * 1000, seg.idx, seg.key]);
            }
          } else {
            for (let i = 0; i < this.points.length; i++) {
              const start = this.points[i].ts;
              const end = i + 1 < this.points.length ? this.points[i + 1].ts : (this.windowEnd ?? start);
              if (this.points[i].value >= 0.5 && end > start) {
                intervals.push([0, start * 1000, end * 1000]);
              }
            }
          }
          const accent = getComputedStyle(document.body).getPropertyValue('--chart-bar');
          const naFill = this.isState ? unavailablePattern() : null;
          const fillFor = idx => (idx === STATE_UNAVAILABLE ? naFill : stateColor(idx));
          const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
          const option = {
            textStyle: {
              fontFamily: getComputedStyle(document.body).getPropertyValue('--font-mono'),
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * uiFontScale * 10) / 10,
            },
            grid: {left: 10, right: 20, top: 16, bottom: 40, containLabel: true},
            xAxis: {
              type: 'time',
              min: this.windowStart != null ? this.windowStart * 1000 : undefined,
              // Eine Sekunde zurück, siehe Kommentar bei der Linien-/Balken-
              // Chart-Achse oben (periodEnd ist exklusiv).
              max: this.periodEnd != null ? this.periodEnd * 1000 - 1000 : undefined,
              boundaryGap: [0, 0],
              // Siehe Kommentar bei der Linien-/Balken-Chart-Achse oben (dort
              // auch die Begründung für den erzwungenen Tages-Interval).
              interval: ['week', 'month'].includes(this.range) ? 24 * 60 * 60 * 1000 : undefined,
              // Siehe Kommentar bei der Linien-/Balken-Chart-Achse oben — ECharts
              // polstert die Achse trotz max intern minimal weiter, deshalb
              // zusätzlich die Beschriftung ab periodEnd unterdrücken.
              axisLabel: {
                formatter: v => (this.periodEnd != null && v >= this.periodEnd * 1000) ? '' : fmt(v / 1000),
                hideOverlap: true,
              },
            },
            // Eine einzelne Kategorie = eine Zeile — bewusst ohne Achsenlinie/
            // -beschriftung, das Band selbst macht klar, worum es geht (Legende/
            // Name steht bereits als Seitentitel über dem Chart).
            yAxis: {type: 'category', data: [''], axisLine: {show: false}, axisTick: {show: false}, splitLine: {show: false}},
            tooltip: {
              trigger: 'item',
              formatter: p => {
                const [, startMs, endMs] = p.value;
                const pad = n => String(n).padStart(2, '0');
                const fmtTime = ms => {
                  const d = new Date(ms);
                  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
                };
                const span = `${fmtTime(startMs)}–${fmtTime(endMs)} · <strong>${NumberFormat.fmtDuration((endMs - startMs) / 1000)}</strong>`;
                if (!this.isState) return span;
                const idx = p.value[3];
                const raw = this.stateRaw(idx);
                const name = escapeHtml(this.stateLabel(idx));
                const rawPart = raw && raw !== this.stateLabel(idx) ? ` <span style="opacity:.6">${escapeHtml(raw)}</span>` : '';
                return `<strong>${name}</strong>${rawPart}<br>${span}`;
              },
              appendToBody: true,
            },
            series: [{
              id: 'main',
              type: 'custom',
              renderItem: (params, api) => {
                const categoryIndex = api.value(0);
                const start = api.coord([api.value(1), categoryIndex]);
                const end = api.coord([api.value(2), categoryIndex]);
                const height = api.size([0, 1])[1] * 0.6;
                const rectShape = echarts.graphic.clipRectByRect(
                  // Mindestens 1 px: ein Zustand von Sekunden soll als Strich
                  // sichtbar bleiben statt ganz zu verschwinden.
                  {x: start[0], y: start[1] - height / 2, width: Math.max(end[0] - start[0], this.isState ? 1 : 0), height},
                  {x: params.coordSys.x, y: params.coordSys.y, width: params.coordSys.width, height: params.coordSys.height}
                );
                return rectShape && {type: 'rect', shape: rectShape, style: {fill: this.isState ? fillFor(api.value(3)) : accent}};
              },
              encode: {x: [1, 2], y: 0},
              data: intervals,
            }, {
              // Rahmen um das Band der Protokollzeile unter dem Mauszeiger
              // (highlightState()); bei Schaltern bleibt die Reihe leer.
              id: 'hl',
              type: 'custom',
              silent: true,
              z: 10,
              renderItem: (params, api) => {
                const start = api.coord([api.value(1), api.value(0)]);
                const end = api.coord([api.value(2), api.value(0)]);
                const height = api.size([0, 1])[1] * 0.6 + 8;
                const rectShape = echarts.graphic.clipRectByRect(
                  {x: start[0] - 2, y: start[1] - height / 2, width: Math.max(end[0] - start[0], 1) + 4, height},
                  {x: params.coordSys.x - 3, y: params.coordSys.y, width: params.coordSys.width + 6, height: params.coordSys.height}
                );
                const ink = getComputedStyle(document.body).getPropertyValue('--ink').trim();
                return rectShape && {type: 'rect', shape: {...rectShape, r: 3}, style: {fill: 'transparent', stroke: ink, lineWidth: 2}};
              },
              encode: {x: [1, 2], y: 0},
              data: [],
            }],
            // Der Zeitstrahl bekommt den Zoom IMMER, ohne die Punkt-Schwelle
            // der Linien-/Balken-Ansicht. Grund ist kein Komfort, sondern
            // Sichtbarkeit: ein Segment wird als Rechteck von seinem Anfang bis
            // zu seinem Ende gezeichnet, und bei Zeitraum t("Monat") auf rund
            // 900 px entspricht ein Pixel etwa 48 Minuten. Jedes kürzere
            // Schaltereignis ist damit schmaler als ein Pixel und praktisch
            // unsichtbar — Hineinzoomen ist die einzige Möglichkeit, überhaupt
            // hinzusehen. Die Zahl der Intervalle sagt darüber nichts aus: auch
            // drei Segmente können zu kurz zum Sehen sein.
            //
            // filterMode 'none': die y-Achse ist hier eine einzelne Kategorie
            // ("AN"), es gibt nichts nachzuskalieren.
            dataZoom: [zoomConfig('none')],
            brush: selectBrush(),
            toolbox: {show: false},
          };
          chartInstance.setOption(option, true);
          chartInstance.resize();
        },

        // Bucket-Anfänge der gezeigten Periode — auch die ohne Daten, damit die
        // Achse den ganzen Monat bzw. das ganze Jahr zeigt.
        get stateBucketStarts() {
          if (this.windowStart == null || this.periodEnd == null) return [];
          return StateEntities.bucketStarts(this.range, this.windowStart, this.periodEnd);
        },
        // Klick auf einen Balken: diesen Tag/Monat/dieses Jahr öffnen.
        drillState(dataIndex) {
          const next = STATE_DRILL[this.range];
          const ts = this.stateBucketStarts[dataIndex];
          if (!next || ts == null) return;
          // setRange() übersetzt den Anker in den Offset der neuen Stufe; eine
          // Sekunde nach dem Bucket-Anfang, damit er sicher darin liegt.
          this._rangeAnchorMs = (ts + 1) * 1000;
          this.setRange(next);
        },
        // Anteile (gestapelt auf 100 %) oder Wechsel (Beginne je Zustand) je
        // Bucket. Ausgeblendete Zustände fehlen, die Anteile verteilen sich dann
        // auf die sichtbaren (ROADMAP 1.29).
        renderStateBars(fmt) {
          const shares = this.stateMode === 'shares';
          const starts = this.stateBucketStarts;
          const [bucketStart] = StateEntities.RANGE_BUCKETS[this.range]();
          const byStart = new Map(this.stateBuckets.map(b => [bucketStart(b.ts + 1), b]));
          const visible = idx => !this.hiddenStates.includes(idx);
          const order = this.states.map(st => st.idx).filter(idx => visible(idx)
            && this.stateBuckets.some(b => b.states.has(idx)));
          const totalOf = bucket => {
            let sum = 0;
            if (bucket) for (const [idx, v] of bucket.states) if (visible(idx)) sum += v.seconds;
            return sum;
          };
          const pattern = unavailablePattern();
          const fmtBucket = ts => {
            const d = new Date(ts * 1000);
            if (this.range === 'hour') return d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
            if (this.range === 'day') return d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
            if (this.range === 'week') return d.toLocaleDateString(LOCALE, {weekday: 'short', day: '2-digit', month: '2-digit'});
            return fmt(ts);
          };
          const series = order.map(idx => ({
            id: `state-${idx}`,
            name: this.stateLabel(idx),
            type: 'bar',
            stack: 'states',
            barCategoryGap: '20%',
            itemStyle: {color: StateEntities.fill(idx, pattern)},
            emphasis: {focus: 'none'},
            data: starts.map(ts => {
              const bucket = byStart.get(ts);
              const v = bucket && bucket.states.get(idx);
              if (!v) return null;
              if (!shares) return v.entries || null;
              const total = totalOf(bucket);
              return total > 0 && v.seconds > 0 ? v.seconds / total * 100 : null;
            }),
          }));
          const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
          const css = getComputedStyle(document.body);
          chartInstance.setOption({
            textStyle: {
              fontFamily: css.getPropertyValue('--font-mono'),
              color: css.getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * uiFontScale * 10) / 10,
            },
            grid: {left: 10, right: 20, top: 24, bottom: 40, containLabel: true},
            xAxis: {
              type: 'category',
              data: starts.map(fmtBucket),
              axisTick: {alignWithLabel: true},
              axisLabel: {hideOverlap: true},
            },
            yAxis: shares
              ? {type: 'value', max: 100, axisLabel: {formatter: v => `${v} %`}, splitLine: {lineStyle: {color: css.getPropertyValue('--border')}}}
              : {type: 'value', minInterval: 1, splitLine: {lineStyle: {color: css.getPropertyValue('--border')}}},
            tooltip: {
              trigger: 'axis',
              axisPointer: {type: 'shadow'},
              appendToBody: true,
              formatter: items => {
                const ts = starts[items[0]?.dataIndex];
                const bucket = byStart.get(ts);
                if (!bucket) return escapeHtml(fmtBucket(ts));
                const lines = order.filter(idx => bucket.states.has(idx)).map(idx => {
                  const v = bucket.states.get(idx);
                  const total = totalOf(bucket);
                  const value = shares
                    ? `${NumberFormat.fmtDuration(v.seconds)} · ${StateEntities.fmtShare(total ? v.seconds / total * 100 : 0)}`
                    : (v.entries === 1 ? t('1 Beginn') : t('{count} Beginne', {count: v.entries.toLocaleString(LOCALE)}));
                  return `<span class="dot" style="display:inline-block;width:8px;height:8px;border-radius:2px;${stateSwatchStyle(idx)}"></span> ${escapeHtml(this.stateLabel(idx))} <strong>${value}</strong>`;
                });
                return `${escapeHtml(fmtBucket(ts))}<br>${lines.join('<br>')}`;
              },
            },
            series,
            toolbox: {show: false},
          }, true);
          chartInstance.resize();
        },

        init() {
          // Verteidigt gegen eine widersprüchliche Kombination aus den
          // globalen Defaults (z. B. Rohwerte + Diagrammtyp Balken gleichzeitig
          // gesetzt) — dieselbe Vorrangregel wie toggleRaw()/setChartType()
          // schon zur Laufzeit durchsetzen: Rohwerte schließt Balken aus.
          if (this.raw && this.chartType === 'bar') this.chartType = 'line';
          // Der Zeitstrahl (Standard für Schalter) braucht immer Rohwerte.
          if (this.chartType === 'timeline') this.raw = true;
          this.load();
          window.addEventListener('resize', () => chartInstance && chartInstance.resize());
          // ResizeObserver zusätzlich zu window-resize: die Kartenbreite kann sich
          // auch ohne Fenster-Resize ändern (z. B. wenn die Toolbar umbricht).
          new ResizeObserver(() => chartInstance && chartInstance.resize()).observe(document.getElementById('chart'));
        },
      };
    }
