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

    // Zentraler Hook für eine spätere Sprachumschaltung (aktuell nur Deutsch) —
    // jede Datumsformatierung in dieser Datei läuft über Intl mit dieser einen
    // Konstante statt verstreuter 'de-DE'-Literale, damit ein künftiges
    // Sprach-Setting nur hier greifen muss.
    const LOCALE = 'de-DE';

    // Dieselbe Optionen-Menü-Auswahl (Dynamische Y-Achse/Statistik in
    // Legende) wie chart_editor.html, hier nur mit einem einzelnen
    // Legenden-Chip statt einer Serienliste.
    const LEGEND_METRIC_OPTIONS = [
      {value: 'last', label: 'Aktuell'},
      {value: 'min', label: 'Min'},
      {value: 'max', label: 'Max'},
      {value: 'average', label: 'Durchschnitt'},
      {value: 'sum', label: 'Summe'},
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
    // skipRelative unterdrückt "Heute"/"Gestern" — für die Vorjahres-Vergleichslabel
    // wäre offset===-1 sonst als "Gestern" missverständlich, obwohl der Zeitraum ein
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
          return `${fmtDay(start)} · ${fmtTime(start)}–${fmtTime(end)} Uhr`;
        case 'day':
          if (!continuous) {
            if (!skipRelative && offset === 0) return 'Heute';
            if (!skipRelative && offset === -1) return 'Gestern';
            return fmtDay(start);
          }
          return `${fmtDay(start)} – ${fmtDay(end)}`;
        case 'week':
          return `${fmtDayMonth(start)}–${fmtDayMonth(end)} ${end.getFullYear()}`;
        case 'month':
          if (continuous) return `${fmtDay(start)} – ${fmtDay(end)}`;
          return isCurrent ? `${fmtMonthYear(start)} (bis heute)` : fmtMonthYear(start);
        case 'year':
          if (continuous) return `${fmtMonthYear(start)} – ${fmtMonthYear(end)}`;
          return isCurrent ? `${start.getFullYear()} (bis heute)` : `${start.getFullYear()}`;
        case 'decade':
          return `${start.getFullYear()}–${end.getFullYear()}`;
        default:
          return '';
      }
    }

    // Name der "Vorperiode" beim Vergleich compareMode="previous" — welche
    // Periode genau eine zurückliegt, hängt vom Zeitraum-Typ ab ("Vortag" bei
    // "Tag", "Vormonat" bei "Monat", …). Genutzt für den Umschalt-Button
    // (Seg neben "Vorjahr") und die Legenden-Beschriftung der Vergleichs-Serie
    // in render() — beide sollen dasselbe Wort zeigen. Eigenständige Funktion
    // aus demselben Grund wie formatPeriodLabel oben (zwei Aufrufstellen,
    // eine davon außerhalb des Alpine-Objekts).
    function previousPeriodLabel(range) {
      const labels = {
        hour: 'Vorherige Stunde', day: 'Vortag', week: 'Vorwoche',
        month: 'Vormonat', year: 'Vorjahr', decade: 'Vorherige Dekade',
      };
      return labels[range] || 'Vorperiode';
    }

    // Gegenstück zu previousPeriodLabel für compareMode="year" — dieselbe
    // Periode, aber genau ein Jahr zurück statt eine Periode zurück (bei
    // "Jahr" fallen beide Modi auf dasselbe Wort "Vorjahr").
    function previousYearPeriodLabel(range) {
      const labels = {
        hour: 'Vorjahresstunde', day: 'Vorjahrestag', week: 'Vorjahreswoche',
        month: 'Vorjahresmonat', year: 'Vorjahr', decade: 'Vorjahresdekade',
      };
      return labels[range] || 'Vorjahreszeitraum';
    }

    // Zwei Zeiträume bekommen keine zweite Vergleichszeile, aus zwei
    // verschiedenen Gründen (beide in tests/test_query.py gemessen):
    //
    //   "Jahr"   — die Vorperiode IST das Vorjahr. Für jedes abgeschlossene
    //              Jahr liefern beide Modi buchstäblich dasselbe Fenster; im
    //              laufenden Jahr unterscheiden sie sich nur darin, dass der
    //              Vorjahresvergleich am selben TAG des Vorjahres endet statt
    //              am Jahresende. Das ist ein echter Unterschied — aber beide
    //              Zeilen trugen dafür dasselbe Wort "Vorjahr", und zwei
    //              Fenster unter einer Beschriftung kann niemand
    //              auseinanderhalten. Soll der faire Jahresvergleich zurück,
    //              braucht er ein eigenes Wort, keine zweite "Vorjahr"-Zeile.
    //   "Dekade" — dort ist der Modus schlicht falsch: er schiebt das
    //              Jahrzehnt um EIN Jahr zurück, das Ergebnis überlappt also
    //              genau den Zeitraum, gegen den es verglichen wird.
    const COMPARE_YEAR_RANGES = ['hour', 'day', 'week', 'month'];

    function compareYearAvailable(range) {
      return COMPARE_YEAR_RANGES.includes(range);
    }

    // Durchschnitt der GEZEICHNETEN Werte, oder null wenn es keine gibt.
    //
    // Bewusst selbst gerechnet statt ECharts' markLine {type:'average'}: das
    // rechnet über die Daten, die die Serie im Moment führt — bei aktivem
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

    function entityChart() {
      return {
        ranges: [
          {key: 'hour', label: 'Stunde'}, {key: 'day', label: 'Tag'}, {key: 'week', label: 'Woche'},
          {key: 'month', label: 'Monat'}, {key: 'year', label: 'Jahr'}, {key: 'decade', label: 'Dekade'},
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
        // beim Zwischenschritt "Monat" zu offset=0 werden und beim Zurück-
        // wechseln auf "Tag" fälschlich auf heute springen.
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
          });
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
        // "Vergleichen" + separatem Auswahl-Segment daneben) — passt sich wie
        // die beiden Label-Getter oben automatisch an den Zeitraum an.
        get compareButtonLabel() {
          if (!this.compare) return 'Vergleichen';
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
          return this.chartType === 'timeline' || this.points.length > ZOOM_MIN_POINTS;
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
          const geste = 'mit Strg und Mausrad zoomen, '
            + 'mit Umschalt einen Bereich aufziehen, am Telefon mit zwei Fingern';
          if (this.chartType === 'timeline') {
            return `Kurze Schaltvorgänge sind schmaler als ein Bildpunkt — ${geste}.`;
          }
          const einzeln = this.points.length === 1;
          const anzahl = this.points.length.toLocaleString(LOCALE);
          const punkte = einzeln ? '1 Datenpunkt' : `${anzahl} Datenpunkte`;
          if (this.zoomAvailable) return `${punkte} — ${geste}.`;
          // "alle einzeln sichtbar" passt nicht zu einem einzelnen Punkt, und
          // den gibt es wirklich: eine Stundenansicht einer selten meldenden
          // Entität hat oft genau einen.
          const sichtbar = einzeln ? '' : ', alle einzeln sichtbar';
          return `${punkte}${sichtbar} — Hineinzoomen ist hier nicht nötig.`;
        },
        // Ohne Zoom ein fester Text statt eines leeren Knopfes: der Chip bleibt
        // wie die Zeitraum-Knöpfe daneben immer im Layout stehen und wird nur
        // deaktiviert (siehe Kommentar zu .toolbars in entity_detail.css) —
        // dann braucht er auch ohne Ausschnitt eine Beschriftung.
        get zoomLabel() {
          if (!this.zoomRange) return 'Ausschnitt';
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
          if (!this.markedTotal) return 'Keine Markierungen';
          return this.markedTotal === 1 ? '1 markiert' : `${this.markedTotal} markiert`;
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
          this.raw = false;
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
        // gesamten aktuellen Options-Stand. Fire-and-forget wie
        // toggleFavorite() (favorite-toggle.js): ein Netzwerkfehler soll die
        // Bedienung nicht blockieren, die Anzeige selbst hat sich ja schon
        // (optimistisch, über die reaktiven Alpine-Felder) geändert — beim
        // nächsten Laden der Seite greift dann wieder der zuletzt tatsächlich
        // gespeicherte Stand.
        async saveChartOptions() {
          try {
            await fetch(`${BASE}/entities/${ENTITY_ID}/chart-options`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                continuous: this.continuous, raw: this.raw, chart_type: this.chartType,
                show_points: this.showPoints, show_values: this.showValues,
                dynamic_y_axis: this.dynamicYAxis, chart_stats: this.chartStats,
                show_marked: this.showMarked,
                average_line: this.averageLine,
                legend_metrics: this.legendMetrics, legend_style: this.legendStyle,
                decimals: this.decimals,
              }),
            });
          } catch (e) {
            // Netzwerkfehler: nächster Seitenaufruf lädt wieder den zuletzt
            // gespeicherten Stand, siehe Kommentar oben.
          }
        },
        // "Auf Standard zurücksetzen" (Optionen-Menü, Aktionen) — wirft die
        // individuelle Übersteuerung dieser Entität weg und lädt die Seite
        // neu, damit alle Felder wieder die (dann wieder live geltenden)
        // globalen Defaults zeigen, statt sie hier einzeln nachzubauen.
        async resetChartOptions() {
          try {
            await fetch(`${BASE}/entities/${ENTITY_ID}/chart-options/reset`, {method: 'POST'});
          } finally {
            location.reload();
          }
        },
        // Vergleicht den aktuellen (reaktiven) Options-Stand gegen die
        // globalen Startwerte — chart_type dabei aufgelöst wie oben bei
        // chartType, da CHART_DEFAULTS.chart_type "auto" sein kann, this.
        // chartType aber nie.
        hasCustomOptions() {
          const defaultChartType = CHART_DEFAULTS.chart_type === 'auto' ? DEFAULT_CHART_TYPE : CHART_DEFAULTS.chart_type;
          return (
            this.continuous !== CHART_DEFAULTS.continuous ||
            this.raw !== CHART_DEFAULTS.raw ||
            this.chartType !== defaultChartType ||
            this.showPoints !== CHART_DEFAULTS.show_points ||
            this.showValues !== CHART_DEFAULTS.show_values ||
            this.dynamicYAxis !== CHART_DEFAULTS.dynamic_y_axis ||
            this.chartStats !== CHART_DEFAULTS.chart_stats ||
            this.showMarked !== CHART_DEFAULTS.show_marked ||
            this.averageLine !== CHART_DEFAULTS.average_line ||
            this.legendStyle !== CHART_DEFAULTS.legend_style ||
            this.decimals !== CHART_DEFAULTS.decimals ||
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
          const res = await fetch(`${BASE}/api/query?${params}`);
          const data = await res.json();
          if (requestId !== this._requestId) return; // eine neuere Anfrage ist schon unterwegs/angekommen
          this.points = data.points || [];
          this.comparePoints = data.compare_points || [];
          this.compareWindowStart = data.compare_window_start ?? null;
          this.compareWindowEnd = data.compare_window_end ?? null;
          this.aggregationType = data.aggregation_type;
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
            // einem einzigen Punkt (z. B. "Jahr" einer Entität, die erst
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
            // um ein Jahr verschoben) — offset-1 würde hier fälschlich "Gestern"
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
            // Fensterende (bei "Heute" also bis zur aktuellen Uhrzeit) zu reichen.
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
              // über min/max hinaus (sichtbar z. B. bei "Jahr"/"Dekade" mit nur
              // einem Bucket: die Achse reichte weit über das eigentliche Fenster
              // hinaus) — boundaryGap:[0,0] unterbindet dieses Auto-Polster, min/max
              // bleiben dadurch die tatsächlichen Achsengrenzen.
              boundaryGap: [0, 0],
              // Woche/Monat bucketen tagesweise (siehe fmt() oben, "dd.mm."-
              // Beschriftung) — mit fest erzwungenem Tages-Interval statt
              // ECharts' automatischer "nice tick"-Berechnung, die trotz
              // explizitem max (s. o.) gelegentlich einen zusätzlichen Tick
              // (Gitternetz, nicht nur Label) jenseits der Periodengrenze
              // erzeugt — sichtbar als 8. statt 7. Einteilung bei "Woche".
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
              name: DURATION_DISPLAY ? 'Dauer' : (UNIT || undefined),
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
          for (let i = 0; i < this.points.length; i++) {
            const start = this.points[i].ts;
            const end = i + 1 < this.points.length ? this.points[i + 1].ts : (this.windowEnd ?? start);
            if (this.points[i].value >= 0.5 && end > start) {
              intervals.push([0, start * 1000, end * 1000]);
            }
          }
          const accent = getComputedStyle(document.body).getPropertyValue('--chart-bar');
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
                return `${fmtTime(startMs)}–${fmtTime(endMs)} · <strong>${NumberFormat.fmtDuration((endMs - startMs) / 1000)}</strong>`;
              },
              appendToBody: true,
            },
            series: [{
              type: 'custom',
              renderItem: (params, api) => {
                const categoryIndex = api.value(0);
                const start = api.coord([api.value(1), categoryIndex]);
                const end = api.coord([api.value(2), categoryIndex]);
                const height = api.size([0, 1])[1] * 0.6;
                const rectShape = echarts.graphic.clipRectByRect(
                  {x: start[0], y: start[1] - height / 2, width: end[0] - start[0], height},
                  {x: params.coordSys.x, y: params.coordSys.y, width: params.coordSys.width, height: params.coordSys.height}
                );
                return rectShape && {type: 'rect', shape: rectShape, style: {fill: accent}};
              },
              encode: {x: [1, 2], y: 0},
              data: intervals,
            }],
            // Der Zeitstrahl bekommt den Zoom IMMER, ohne die Punkt-Schwelle
            // der Linien-/Balken-Ansicht. Grund ist kein Komfort, sondern
            // Sichtbarkeit: ein Segment wird als Rechteck von seinem Anfang bis
            // zu seinem Ende gezeichnet, und bei Zeitraum "Monat" auf rund
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

        init() {
          // Verteidigt gegen eine widersprüchliche Kombination aus den
          // globalen Defaults (z. B. Rohwerte + Diagrammtyp Balken gleichzeitig
          // gesetzt) — dieselbe Vorrangregel wie toggleRaw()/setChartType()
          // schon zur Laufzeit durchsetzen: Rohwerte schließt Balken aus.
          if (this.raw && this.chartType === 'bar') this.chartType = 'line';
          this.load();
          window.addEventListener('resize', () => chartInstance && chartInstance.resize());
          // ResizeObserver zusätzlich zu window-resize: die Kartenbreite kann sich
          // auch ohne Fenster-Resize ändern (z. B. wenn die Toolbar umbricht).
          new ResizeObserver(() => chartInstance && chartInstance.resize()).observe(document.getElementById('chart'));
        },
      };
    }
