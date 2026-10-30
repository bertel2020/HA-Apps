    // Jede Datumsformatierung in dieser Datei läuft über Intl mit dieser einen Konstante;
    // das Format kommt aus der Einstellung (window.ZA_FORMAT, siehe i18n.js).
    const LOCALE = window.ZA_FORMAT;
    const ENTITY_LABELS = Object.fromEntries(
      ENTITY_OPTIONS.map(opt => [opt.entity_id, opt.label])
    );
    const ENTITY_UNITS = Object.fromEntries(
      ENTITY_OPTIONS.map(opt => [opt.entity_id, opt.unit || ''])
    );
    // Reihenfolge bestimmt sowohl die Checkbox-Liste im Optionen-Menü als auch
    // die Reihenfolge der Werte im Legenden-Chip (siehe Template).
    const LEGEND_METRIC_OPTIONS = [
      {value: 'last', label: t('Aktuell')},
      {value: 'min', label: t('Min')},
      {value: 'max', label: t('Max')},
      // Symbole statt Text, wo eines eindeutig etabliert ist — dieselben
      // Zeichen, mit denen Durchschnitt/Summe schon überall sonst in der App
      // beschriftet sind (Ø in der Legende selbst, s. u.; Ø auch in der
      // Durchschnittslinie, siehe averageOf()-Verwendung). t("Aktuell")/"Min"/
      // "Max" bleiben Text — dafür gibt es kein vergleichbar etabliertes,
      // eindeutiges Zeichen in dieser App.
      {value: 'average', label: 'Ø'},
      {value: 'sum', label: 'Σ'},
    ];

    // Feste Farbfolge statt ECharts' Auto-Zuordnung — nur so lässt sich bei
    // aktivem Vergleich (siehe render()) die Vorperiode-Serie jeder Entität
    // eindeutig ihrer Hauptserie zuordnen. Gedeckte, erdige Töne passend zum
    // --accent-line/--accent-bar-Paar der Oberfläche, nicht die grellen
    // ECharts-Standardfarben.
    const PALETTE = Array.from({length: 8}, (_, i) =>
      getComputedStyle(document.documentElement).getPropertyValue(`--chart-${i + 1}`).trim()
    );
    const UI_FONT_SCALE = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
    // Blasse Vorperiode-/Vorjahr-Nebenserien blendeten bisher per
    // itemStyle/lineStyle.opacity ab, statt die Deckkraft in die Farbe selbst
    // einzurechnen — der Tooltip-Marker (sowohl ECharts' eigener bei
    // trigger:'axis' ohne Formatter als auch p.marker im eigenen HTML-
    // Formatter unten) übernimmt NUR itemStyle/lineStyle.color, ignoriert
    // opacity dabei komplett. Ergebnis: Haupt- und Vorperiode-Zeile zeigten
    // im Tooltip denselben satten Farbpunkt, obwohl der Balken/die Linie im
    // Chart selbst sichtbar blasser ist (gemessen). Eine echte rgba-Farbe
    // löst das an der Wurzel, für Canvas-Rendering UND Marker identisch —
    // alpha-Multiplikation ist in beiden Fällen dieselbe Rechnung.
    const withOpacity = (hex, opacity) => {
      const n = parseInt(hex.replace('#', ''), 16);
      return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${opacity})`;
    };

    // Zentral in static/js/number-format.js (window.NumberFormat) — dieselbe
    // Formatierung wie überall sonst in der Oberfläche, siehe Kommentar dort.
    const fmtNum = NumberFormat.fmt;

    // Ziel des Kalender-Sprungs (calendar-picker.js ruft diese globale
    // Funktion auf) — dieselbe Übersetzung "Datum → Perioden-Offset des
    // gewählten Zeitraums" wie jumpToDate() in entity_detail.js.
    function jumpToDate(dateStr) {
      const root = Alpine.$data(document.querySelector('.page'));
      const [y, m, d] = dateStr.split('-').map(Number);
      root._rangeAnchorMs = new Date(y, m - 1, d, 12).getTime();
      root.offset = PeriodNavigation.offsetForRange(root.range, root._rangeAnchorMs);
      root.load();
    }
    window.jumpToDate = jumpToDate;

    // Fester Zeitraum eines Charts (saved_charts.period_anchor): ein Tag als
    // JJJJ-MM-TT. Mittags statt Mitternacht, damit eine Zeitumstellung den
    // Tag nicht verschiebt — dieselbe Wahl wie jumpToDate().
    function anchorDateToMs(dateStr) {
      const [y, m, d] = dateStr.split('-').map(Number);
      return new Date(y, m - 1, d, 12).getTime();
    }
    function msToAnchorDate(ms) {
      const d = new Date(ms);
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }

    // Auf Modulebene statt lokal in formatPeriodLabel() (wie bis hierhin) —
    // formatPeriodForFilename() weiter unten braucht dieselben Formatierer.
    const fmtDay = d => d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric'});
    const fmtDayMonth = d => d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit'});
    const fmtMonthYear = d => d.toLocaleDateString(LOCALE, {month: 'long', year: 'numeric'});
    const fmtTime = d => d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});

    // Dieselbe Perioden-Beschriftungslogik wie auf der Entität-eigenen Chart-
    // Seite (entity_detail.html) — hier bewusst ohne Vorperiode/Offset-
    // Navigation, ein abgelegtes Chart zeigt beim Ansehen immer die aktuelle
    // Periode (offset bleibt fest bei 0, siehe load()).
    function formatPeriodLabel(range, continuous, windowStart, windowEnd, isCurrent) {
      if (windowStart == null || windowEnd == null) return '';
      const start = new Date(windowStart * 1000);
      const end = new Date(windowEnd * 1000 - 1000);
      switch (range) {
        case 'hour': return t('{range} Uhr',{range: `${fmtDay(start)} · ${fmtTime(start)}–${fmtTime(end)}`});
        case 'day': return continuous ? `${fmtDay(start)} – ${fmtDay(end)}` : (isCurrent ? t('Heute') : fmtDay(start));
        case 'week': return `${fmtDayMonth(start)}–${fmtDayMonth(end)} ${end.getFullYear()}`;
        case 'month': return continuous ? `${fmtDay(start)} – ${fmtDay(end)}` : (isCurrent ? t('{period} (bis heute)', {period: fmtMonthYear(start)}) : fmtMonthYear(start));
        case 'year': return continuous ? `${fmtMonthYear(start)} – ${fmtMonthYear(end)}` : (isCurrent ? t('{period} (bis heute)', {period: start.getFullYear()}) : `${start.getFullYear()}`);
        case 'decade': return `${start.getFullYear()}–${end.getFullYear()}`;
        default: return '';
      }
    }

    // Wie formatPeriodLabel(), aber fürs Dateinamensfeld beim CSV-/Bild-
    // Export (exportFilename-Getter weiter unten): "Heute"/"(bis heute)" löst
    // sich auf das tatsächliche Datum auf (windowEnd, exklusiv, daher -1s)
    // statt das relative Wort stehen zu lassen — ein heute heruntergeladener
    // Export bliebe sonst beim erneuten Ansehen (z. B. nächste Woche) nicht
    // mehr erkennbar, ab wann er galt. Trennzeichen ("-"/"_" statt "–"/"·")
    // bewusst dateinamentauglich statt der Lesetypografie aus
    // formatPeriodLabel(); die eigentliche Zeichen-Bereinigung (Leerzeichen,
    // verbotene Zeichen) übernimmt sanitizeFilename().
    function formatPeriodForFilename(range, continuous, windowStart, windowEnd, isCurrent) {
      if (windowStart == null || windowEnd == null) return '';
      const start = new Date(windowStart * 1000);
      const end = new Date(windowEnd * 1000 - 1000);
      switch (range) {
        case 'hour': return `${fmtDay(start)}_${fmtTime(start).replace(':', '-')}-${fmtTime(end).replace(':', '-')}`;
        case 'day': return continuous ? `${fmtDay(start)}-${fmtDay(end)}` : fmtDay(isCurrent ? end : start);
        case 'week': return `${fmtDayMonth(start)}-${fmtDayMonth(end)}_${end.getFullYear()}`;
        case 'month': return continuous ? `${fmtDay(start)}-${fmtDay(end)}` : (isCurrent ? `${fmtMonthYear(start)}_bis_${fmtDay(end)}` : fmtMonthYear(start));
        case 'year': return continuous ? `${fmtMonthYear(start)}-${fmtMonthYear(end)}` : (isCurrent ? `${start.getFullYear()}_bis_${fmtDay(end)}` : `${start.getFullYear()}`);
        case 'decade': return `${start.getFullYear()}-${end.getFullYear()}`;
        default: return '';
      }
    }

    // Entfernt Dateisystem-kritische Zeichen und macht Leerzeichen zu "_" —
    // gemeinsam für Chart-Namen und formatPeriodForFilename()-Ergebnis
    // genutzt (exportFilename-Getter weiter unten).
    function sanitizeFilename(s) {
      return String(s)
        .replace(/[\\/:*?"<>|]/g, '')
        .replace(/[(),]/g, '')
        .trim()
        .replace(/\s+/g, '_');
    }

    // "Als Bild speichern" (toolbox.feature.saveAsImage) — reines ECharts-
    // Bordmittel, dieselbe Konfiguration für alle drei Renderpfade (render()/
    // renderTimelineMulti()/renderDonut()), deshalb hier zentral statt
    // dreimal dupliziert. NUR auf dieser Seite (nicht auf der Dashboard-
    // Kachel, dashboard-tiles.js) — eine Kachel ist ohnehin nur eine
    // Vorschau, das Icon wäre dort in der kleinen Fläche nur Ballast, und das
    // Original mit vollem Bedienfeld steht ohnehin einen Klick entfernt.
    function toolboxOption(exportFilename, surface, inkFaint) {
      return {
        show: true, right: 6, top: 0,
        feature: {
          saveAsImage: {title: t('Als Bild speichern'), backgroundColor: surface, name: exportFilename},
        },
        iconStyle: {borderColor: inkFaint},
      };
    }

    function previousPeriodLabel(range) {
      return ({
        hour: t('Vorherige Stunde'), day: t('Vortag'), week: t('Vorwoche'),
        month: t('Vormonat'), year: t('Vorjahr'), decade: t('Vorherige Dekade'),
      })[range] || t('Vorperiode');
    }

    function previousYearPeriodLabel(range) {
      return ({
        hour: t('Vorjahresstunde'), day: t('Vorjahrestag'), week: t('Vorjahreswoche'),
        month: t('Vorjahresmonat'), year: t('Vorjahr'), decade: t('Vorjahresdekade'),
      })[range] || t('Vorjahreszeitraum');
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

    // Feld "Jahre" beim Vergleich "Langzeit" (Mehrjahresvergleich): Zahl der
    // Vorjahre zusätzlich zum angezeigten Jahr, 0 = alle Jahre mit Daten. Ein
    // einzelnes Vorjahr gibt es hier nicht — das ist "Vorjahr".
    const COMPARE_YEARS_OPTIONS = [
      {value: 2, label: '2'},
      {value: 5, label: '5'},
      {value: 10, label: '10'},
      {value: 0, label: t('Alle')},
    ];

    // Durchschnitt der GEZEICHNETEN Werte, oder null wenn es keine gibt.
    // Selbst gerechnet statt ECharts' eigenem Durchschnitts-Typ für markLine
    // — Begründung in entity_detail.js (dort steht dieselbe Funktion).
    function averageOf(values) {
      const zahlen = values.filter(Number.isFinite);
      if (!zahlen.length) return null;
      return zahlen.reduce((summe, v) => summe + v, 0) / zahlen.length;
    }

    // Gleitender Durchschnitt (Optionen-Menü, "Durchschnittslinie" →
    // "Gleitend") — zentriertes Fenster über die tatsächlich gezeichneten
    // Punkte (mainPoints), nicht über s.points, aus demselben Grund wie beim
    // flachen Durchschnitt: resamplePoints() fasst Zähler/Schalter per SUMME
    // zusammen, Rohpunkte lägen sonst auf einer anderen Skala.
    //
    // Fensterbreite als fester ANTEIL der gezeichneten Punkte (1/12,
    // zwischen 3 und 60 Punkten gekappt) statt einer festen Anzahl Tage —
    // dadurch funktioniert dieselbe Formel unverändert bei jeder Auflösung
    // (Auto/Medium/Coarse) und jedem Zeitraum, ohne RESOLUTION_SECONDS hier
    // ein zweites Mal auszuwerten. Die Note im Menü zeigt die sich daraus
    // ergebende ungefähre Zeitspanne an (siehe render()).
    function movingAverageWindow(pointCount) {
      return Math.min(60, Math.max(3, Math.round(pointCount / 12)));
    }
    function movingAverage(points, windowPoints) {
      return points.map((p, i) => {
        const start = Math.max(0, i - Math.floor(windowPoints / 2));
        const end = Math.min(points.length, i + Math.ceil(windowPoints / 2));
        const slice = points.slice(start, end).map(q => q.value).filter(Number.isFinite);
        return {ts: p.ts, value: slice.length ? slice.reduce((s, v) => s + v, 0) / slice.length : null};
      });
    }

    const RESOLUTION_SECONDS = {
      hour: {medium: 5 * 60, coarse: 15 * 60},
      // full: die komplette Periode als EIN Balken — z. B. "Tag" bei
      // Zeitraum "Tag", um mehrere Entitäten als jeweils einen einzigen
      // Gesamtwert der Periode miteinander zu vergleichen (Ranking, siehe
      // render()), statt als vielteilige Zeitreihe. Bei Woche/Monat/Jahr
      // bewusst großzügig gerundete, aber sichere Obergrenzen (nie kürzer
      // als die tatsächliche Fensterlänge) statt exakter Kalenderlängen —
      // resamplePoints() braucht nur "groß genug, dass alle Punkte in
      // Bucket 0 fallen", keine exakte Sekundenzahl.
      day: {medium: 30 * 60, coarse: 60 * 60, full: 24 * 60 * 60},
      week: {medium: 6 * 60 * 60, coarse: 24 * 60 * 60, full: 7 * 24 * 60 * 60},
      month: {medium: 24 * 60 * 60, coarse: 7 * 24 * 60 * 60, full: 31 * 24 * 60 * 60},
      year: {medium: 30 * 24 * 60 * 60, coarse: 90 * 24 * 60 * 60, full: 366 * 24 * 60 * 60},
      decade: {medium: 365 * 24 * 60 * 60, coarse: 2 * 365 * 24 * 60 * 60},
    };
    const RESOLUTION_LABELS = {
      hour: {medium: t('5 Minuten'), coarse: t('15 Minuten')},
      day: {medium: t('30 Minuten'), coarse: t('1 Stunde'), full: t('Tag')},
      week: {medium: t('6 Stunden'), coarse: t('1 Tag'), full: t('Woche')},
      month: {medium: t('1 Tag'), coarse: t('1 Woche'), full: t('Monat')},
      year: {medium: t('1 Monat'), coarse: t('3 Monate'), full: t('Jahr')},
      decade: {medium: t('1 Jahr'), coarse: t('2 Jahre')},
    };

    // Zeigt bei "Automatisch" die tatsächlich vom Server gelieferte Auflösung
    // an (Konzept "Auto-Auflösung anzeigen") — statt die Server-Logik hier zu
    // duplizieren (die je nach Zähler/Standard/Schalter UND pro Entität
    // unterschiedlich ausfällt, siehe FINE_LEVEL/BAR_RESOLUTION in query.py),
    // wird die Auflösung direkt aus dem Abstand der tatsächlich gelieferten
    // Punkte abgelesen — datengetrieben, also immer korrekt, unabhängig von
    // Aggregationstyp und ohne eigene Kopie der Backend-Logik. Median statt
    // Minimum/Durchschnitt, damit einzelne Lücken (z. B. eine Pause in den
    // Messwerten) das Ergebnis nicht verzerren.
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

    // Gesamt-Einschaltdauer aus rohen AN/AUS-Zeitstempeln — dieselbe Paar-
    // bildung (jeder Punkt gilt bis zum nächsten, letzter bis windowEnd) wie
    // renderTimelineMulti() beim Zeichnen der Zeitstrahl-Balken, hier nur für
    // die Summe statt fürs Rendering. Im Rohwerte-/Zeitstrahl-Modus sind die
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

    const DURATION_UNITS = [
      [365 * 24 * 3600, t('Jahr'), t('Jahre')],
      [30 * 24 * 3600, t('Monat'), t('Monate')],
      [7 * 24 * 3600, t('Woche'), t('Wochen')],
      [24 * 3600, t('Tag'), t('Tage')],
      [3600, t('Stunde'), t('Stunden')],
      [60, t('Minute'), t('Minuten')],
      [1, t('Sekunde'), t('Sekunden')],
    ];
    function fmtDurationLabel(seconds) {
      if (seconds == null) return '';
      for (const [unitSeconds, singular, plural] of DURATION_UNITS) {
        if (seconds >= unitSeconds * 0.9) {
          const n = Math.max(1, Math.round(seconds / unitSeconds));
          return `${n} ${t(n === 1 ? singular : plural)}`;
        }
      }
      return t('{n} Sekunden',{n: Math.round(seconds)});
    }

    // Tooltip-Zeitstempel richten sich nach der TATSÄCHLICHEN Bucket-Breite der
    // Daten (detectResolutionSeconds), nicht nach dem Zeitraum-Namen — die
    // Auflösung kann pro Zeitraum manuell überschrieben werden (Auflösung:
    // Automatisch/medium/coarse), und die Uhrzeit ist bei Tages-Buckets oder
    // gröber ohnehin immer Mitternacht, also reine Information ohne Wert.
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

    // ECharts stapelt auf einer Zeit-Achse nach INDEX in den Daten, nicht nach
    // Zeitstempel (nachgemessen mit ECharts 5.6: Serie B mit Beginn im April
    // landete auf Serie A vom Januar). Der Server liefert Buckets aber erst ab
    // dem ersten vorhandenen Wert — beginnen gestapelte Serien in
    // verschiedenen Perioden, rutscht die spätere nach links. Deshalb werden
    // alle Serien eines Stapels vor dem Zeichnen auf die Vereinigung ihrer
    // Zeitstempel gebracht; fehlende Stellen bekommen einen null-Wert (kein
    // Balken, keine erfundene "0"). Tooltips lassen null-Zeilen weg.
    // Gleiche Funktion in dashboard-tiles.js bzw. chart_editor.js.
    function padStackedSeries(seriesList) {
      const groups = new Map();
      seriesList.forEach(s => {
        if (!s.stack) return;
        if (!groups.has(s.stack)) groups.set(s.stack, []);
        groups.get(s.stack).push(s);
      });
      groups.forEach(list => {
        if (list.length < 2) return;
        const xs = new Set();
        list.forEach(s => s.data.forEach(p => xs.add(p[0])));
        const sorted = [...xs].sort((a, b) => a - b);
        list.forEach(s => {
          if (s.data.length === sorted.length) return;
          const byX = new Map(s.data.map(p => [p[0], p]));
          s.data = sorted.map(x => byX.get(x) || [x, null]);
        });
      });
    }

    // Ein rollierendes Fenster beginnt nicht an einer Tagesgrenze (z. B. 14:39).
    // Stunden- und Tagesbuckets, die daran hingen, lägen neben den Achsen-Ticks
    // (lokale Mitternacht). Deshalb auf die lokale Mitternacht abrunden, sofern
    // die Bucket-Breite den Tag teilt; "full" bleibt am Fensteranfang, damit alle
    // Punkte in Bucket 0 fallen. Gleiche Funktion in dashboard-tiles.js.
    function bucketAnchor(windowStart, seconds, preset) {
      if (preset === 'full' || seconds < 3600 || 86400 % seconds !== 0) return windowStart;
      const midnight = new Date(windowStart * 1000);
      midnight.setHours(0, 0, 0, 0);
      return midnight.getTime() / 1000;
    }

    // Beginnt ein rollierendes Fenster mitten im Tag, liegt der erste Bucket
    // (Mitternacht) vor dem Achsenanfang: der Balken ragt über die y-Achse und
    // verdeckt deren Beschriftung. Die Achse beginnt dann eine halbe
    // Bucket-Breite vor dem ersten Balken, sodass er ganz im Diagramm liegt.
    // Gleiche Funktion in dashboard-tiles.js.
    function axisMinForBars(seriesList, windowStartMs) {
      if (windowStartMs == null) return undefined;
      let min = windowStartMs;
      seriesList.filter(s => s.type === 'bar' && s.data && s.data.length > 1).forEach(s => {
        const xs = s.data.map(p => p[0]).filter(Number.isFinite).sort((a, b) => a - b);
        if (xs.length < 2 || xs[0] >= windowStartMs) return;
        let step = Infinity;
        for (let i = 1; i < xs.length; i++) if (xs[i] > xs[i - 1]) step = Math.min(step, xs[i] - xs[i - 1]);
        if (Number.isFinite(step)) min = Math.min(min, xs[0] - step / 2);
      });
      return min;
    }

    function resamplePoints(points, range, preset, aggregationType, windowStart) {
      const seconds = RESOLUTION_SECONDS[range] && RESOLUTION_SECONDS[range][preset];
      if (!seconds || !points.length || windowStart == null) return points;
      const anchor = bucketAnchor(windowStart, seconds, preset);
      const groups = new Map();
      points.forEach(point => {
        const bucket = Math.floor((point.ts - anchor) / seconds);
        const entry = groups.get(bucket) || {values: [], minima: [], maxima: []};
        if (Number.isFinite(point.value)) entry.values.push(point.value);
        if (Number.isFinite(point.min)) entry.minima.push(point.min);
        if (Number.isFinite(point.max)) entry.maxima.push(point.max);
        groups.set(bucket, entry);
      });
      return Array.from(groups.entries()).sort((a, b) => a[0] - b[0]).map(([bucket, entry]) => {
        const value = aggregationType === 'standard'
          ? entry.values.reduce((sum, v) => sum + v, 0) / entry.values.length
          : entry.values.reduce((sum, v) => sum + v, 0);
        return {
          ts: anchor + bucket * seconds,
          value,
          min: entry.minima.length ? Math.min(...entry.minima) : value,
          max: entry.maxima.length ? Math.max(...entry.maxima) : value,
        };
      }).filter(point => Number.isFinite(point.value));
    }

    // Gruppiert Punkte EINES Kalenderjahres nach tatsächlichem Kalendermonat
    // (0=Jan…11=Dez) statt nach fortlaufender Bucket-Breite wie
    // resamplePoints() — für renderYearsCompare() müssen aktuelles Jahr und
    // Vorjahr je Monat an DERSELBEN Kategorie-Position landen, damit Balken
    // nebeneinander stehen. resamplePoints()s Bucket-Sekunden sind dafür
    // ungeeignet: RESOLUTION_SECONDS.year.medium ist ein grob gerundetes
    // 30-Tage-Fenster (siehe Kommentar dort), das über ein volles Jahr
    // gegenüber echten Kalendermonatsgrenzen zunehmend abdriftet. Dieselbe
    // Aggregation wie dort (Durchschnitt bei "standard", sonst Summe).
    function monthlyBuckets(points, aggregationType) {
      const groups = Array.from({length: 12}, () => []);
      points.forEach(point => {
        if (!Number.isFinite(point.value)) return;
        groups[new Date(point.ts * 1000).getMonth()].push(point.value);
      });
      return groups.map(values => {
        if (!values.length) return null;
        const sum = values.reduce((total, v) => total + v, 0);
        return aggregationType === 'standard' ? sum / values.length : sum;
      });
    }

    // Laufsumme über monthlyBuckets()-Werte. Zwei verschiedene Sorten Lücke,
    // zwei verschiedene Antworten: eine Lücke MITTEN im Jahr (Sensor kurz
    // offline) hält die bisherige Summe und läuft mit den nächsten echten
    // Monaten weiter — ein einzelner fehlender Monat soll den Rest des
    // Jahres nicht auf 0 zurückwerfen. Monate NACH dem letzten echten Wert
    // (das laufende, noch nicht abgeschlossene Jahr) geben dagegen null
    // zurück: dieselbe Zusage wie überall sonst im Chart, nie in die Zukunft
    // fortzuschreiben (siehe windowEnd-Kommentar bei load()) — die Balken
    // brechen dort ohnehin ab, eine bis Dezember flach weiterlaufende Linie
    // daneben sähe wie ein Widerspruch auf demselben Chart aus.
    function cumulate(values) {
      let lastReal = -1;
      values.forEach((v, i) => { if (v != null) lastReal = i; });
      let sum = 0;
      return values.map((v, i) => {
        if (i > lastReal) return null;
        if (v != null) sum += v;
        return sum;
      });
    }

    // Bewusst außerhalb von x-data (siehe entity_detail.html: ECharts verlässt
    // sich auf `this` als echtes Objekt, nicht Alpines reaktiven Proxy).
    let chartInstance = null;
    // Schichten der verdichteten Spalten im Zeitstrahl, je Element über den Index im sechsten Wert
    // (ECharts führt in Werten keine Listen), siehe renderTimelineMulti().
    let timelineParts = [];

    // Ziehen zum Umsortieren der "Angezeigte Namen"-Liste. Anders als bei den
    // Dashboard-Kacheln (dashboard-tiles.js, setupDragAndDrop() — dort reines
    // DOM ohne konkurrierendes Framework) werden die Zeilen hier von Alpines
    // x-for gerendert. Während des Ziehens per insertBefore direkt im DOM zu
    // verschieben (die ursprüngliche Fassung) gerät mit Alpines eigener,
    // keyed x-for-Verwaltung derselben Elemente in Konflikt: die Statistik-
    // Kacheln (die NIE manuell angefasst werden) aktualisieren sich sofort
    // korrekt, aber die manuell verschobene Zeile selbst hinkt hinterher und
    // "springt" erst beim nächsten x-for-Durchlauf an ihre neue Position.
    // Deshalb hier stattdessen: WÄHREND des Ziehens direkt das reaktive Array
    // (selectedEntityIds) umsortieren und dem DOM die Neupositionierung
    // vollständig überlassen — Alpine bleibt die einzige Instanz, die diese
    // Knoten je bewegt.
    function setupEntityDrag(component) {
      const container = document.getElementById('entity-names-list');
      if (!container || container.dataset.dragBound) return;
      container.dataset.dragBound = '1';
      let draggedId = null;
      let draggedEl = null;
      // dragstart selbst kann nicht prüfen, ob der Griff angefasst wurde —
      // event.target ist dort laut Spec immer das draggable-Element (die
      // ganze Zeile), nicht der tatsächlich angeklickte Kindknoten. Deshalb
      // hier per mousedown merken, dort ist event.target noch der reale
      // Klick-Zielknoten wie bei jedem anderen Maus-Event.
      let handleGrabbed = false;
      container.addEventListener('mousedown', e => {
        handleGrabbed = !!e.target.closest('.drag-handle');
      });
      container.addEventListener('dragstart', e => {
        const row = e.target.closest('.entity-name-row');
        if (!row) return;
        // Nur vom Ziehgriff aus starten — sonst interpretiert der Browser
        // einen Drag-Start auf dem input als Textauswahl/Fokus statt als
        // natives Drag (siehe Kommentar bei .drag-handle oben).
        if (!handleGrabbed) { e.preventDefault(); return; }
        draggedEl = row;
        draggedId = row.dataset.entityId;
        row.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedId || '');
      });
      container.addEventListener('dragover', e => {
        if (!draggedId) return;
        e.preventDefault();
        const row = e.target.closest('.entity-name-row');
        if (!row || row.dataset.entityId === draggedId) return;
        const fromIdx = component.selectedEntityIds.indexOf(draggedId);
        const overIdx = component.selectedEntityIds.indexOf(row.dataset.entityId);
        if (fromIdx === -1 || overIdx === -1) return;
        const rect = row.getBoundingClientRect();
        const before = (e.clientY - rect.top) < rect.height / 2;
        let targetIdx = overIdx + (before ? 0 : 1);
        if (targetIdx > fromIdx) targetIdx -= 1;
        if (targetIdx === fromIdx) return;
        const [id] = component.selectedEntityIds.splice(fromIdx, 1);
        component.selectedEntityIds.splice(targetIdx, 0, id);
      });
      container.addEventListener('dragend', () => {
        if (draggedEl) draggedEl.classList.remove('dragging');
        if (draggedId) component.load();
        draggedEl = null;
        draggedId = null;
      });
    }

    // Darstellung der Zustände bei gewähltem chosen (null = keine Wahl: der
    // Standard je Zeitraum). Die Heatmap gibt es erst ab dem Monat, davor fällt
    // sie auf den Standard zurück.
    const stateModeFor = (range, chosen = null) => {
      if (chosen === 'heatmap') {
        return StateEntities.SERVER_SHARE_RANGES.includes(range) ? 'heatmap' : StateEntities.defaultMode(range);
      }
      return chosen || StateEntities.defaultMode(range);
    };

    function chartEditor() {
      return {
        chartId: CHART_ID,
        range: RANGE_KEY,
        continuous: CONTINUOUS,
        // Array statt Set — die Reihenfolge ist hier bedeutungsvoll (bestimmt
        // Legenden-/Statistik-Reihenfolge und Farbzuordnung) und muss per
        // Auf/Ab-Buttons in der "Angezeigte Namen"-Liste änderbar sein, was ein
        // Set nicht abbilden kann.
        selectedEntityIds: [...SELECTED_ENTITY_IDS],
        // Ausgeblendete Serien — eigene Liste statt eines Flags je Eintrag,
        // weil selectedEntityIds bewusst ein flaches Array von IDs ist (siehe
        // Kommentar oben). Die Entität bleibt ausgewählt und behält ihren
        // Platz in der Reihenfolge, sie wird nur nicht abgefragt und nicht
        // gezeichnet — dieselbe Bedeutung wie hidden bei Spalten und Zeilen
        // im Tabellen-Editor.
        hiddenEntityIds: [...HIDDEN_ENTITY_IDS],
        seriesColors: {...SERIES_COLORS},
        colorPickerFor: null,
        name: CHART_NAME,
        entityNames: {...ENTITY_NAMES},
        series: [],
        windowStart: null,
        windowEnd: null,
        // Natürliches, NIE an "jetzt" gedeckeltes Periodenende (vom Server,
        // query._window()) — bestimmt die X-Achsen-Ausdehnung, damit eine
        // laufende Woche/Monat/… immer bis zur vollen Kalendergrenze
        // angezeigt wird (z. B. bis Sonntag), auch ohne Daten in der Zukunft.
        // windowEnd (an "jetzt" gedeckelt) bleibt weiterhin maßgeblich dafür,
        // bis wohin der letzte bekannte Linienwert gehalten wird — dort wird
        // bewusst NICHT in die Zukunft fortgeschrieben.
        periodEnd: null,
        // Zeitraum-Navigation (‹ › und Kalender an der Zeitraumanzeige, nur
        // beim Anpassen) wie auf der Entitätsseite: 0 = laufende Periode,
        // -1 = eine davor usw. Ein Chart mit festem Zeitraum (PERIOD_ANCHOR)
        // startet in seiner Periode; beim Speichern wird die angezeigte
        // Periode übernommen (periodAnchorForSave).
        offset: PERIOD_ANCHOR ? PeriodNavigation.offsetForRange(RANGE_KEY, anchorDateToMs(PERIOD_ANCHOR)) : 0,
        // Zeitlicher Anker beim Wechsel des Zeitraums (siehe setRange()), wie
        // _rangeAnchorMs in entity_detail.js. Bei festem Zeitraum von Anfang an
        // gesetzt, damit ein Zeitraumwechsel vor dem ersten Laden ihn behält.
        _rangeAnchorMs: PERIOD_ANCHOR ? anchorDateToMs(PERIOD_ANCHOR) : null,
        isCurrent: true,
        // Explizit in load() gesetzt statt als Getter über this.series
        // berechnet — ein Getter, der this.series nur verschachtelt über
        // detectResolutionSeconds() liest, wurde von Alpines
        // Abhängigkeits-Tracking nach dem asynchronen Neuladen nicht
        // zuverlässig neu ausgewertet (zeigte den Badge z. B. erst nach
        // manuellem Umschalten von resolutionPreset, nie automatisch nach
        // load()). Eine direkt zugewiesene Property umgeht das vollständig.
        autoResolutionLabel: '',
        compare: COMPARE,
        // Ein gespeichertes Chart kann compare_mode="year" mit einem Zeitraum
        // tragen, der den Vorjahresvergleich nicht (mehr) anbietet — dann wäre
        // im Menü keine Zeile aktiv und der Knopf zeigte ein Wort, das nirgends
        // mehr auswählbar ist. "longterm" gibt es nur bei "Jahr"; passt der
        // Rest nicht (Rollierend, mehrere Entitäten), fällt load() auf
        // "Vorjahr" zurück.
        compareMode: COMPARE_MODE === 'longterm'
          ? (RANGE_KEY === 'year' ? 'longterm' : 'previous')
          : (compareYearAvailable(RANGE_KEY) ? COMPARE_MODE : 'previous'),
        // Laufsumme/Soll — Erweiterungen von "Vorjahr" bei range='year',
        // keine eigene Vergleichsoption (siehe render()). Wie der Vergleich
        // selbst mit dem Chart gespeichert (compare_settings, siehe save()).
        // compareSollEntityId braucht KEINE eigene Backend-Anbindung: die
        // Soll-Entität ist ganz normal Teil von selectedEntityIds (wird über
        // denselben entityPicker() ausgewählt, der sie bei Bedarf mit
        // toggleEntity() hinzufügt) und läuft durch dieselbe /api/query-multi-
        // Abfrage wie jede andere Entität — renderYearsCompare() zeichnet nur
        // GENAU DIESE eine als gepunktete Ziel-Linie statt als Balkenpaar.
        // '' bedeutet "keine Soll-Entität gewählt" — render() wechselt für
        // range='year' von der gewohnten Vorjahres-Schattenlinie auf die
        // Balkenpaare mit eigener Legende (siehe dort), sobald Soll ODER
        // Laufsumme aktiv ist (yearsCompareActive) — die kalendermonatsweise
        // Kumulierung (monthlyBuckets()/cumulate()) läuft ohnehin nur dort,
        // die Schattenlinie kennt keine Monats-Buckets.
        // Eine gespeicherte Soll-Entität gilt nur, solange sie noch zur
        // Auswahl des Charts gehört; sie steckt dort schon (wurde mit
        // gespeichert), deshalb sollAutoAdded false — Leeren entfernt sie nicht.
        compareSollEntityId: (COMPARE_SETTINGS.target && SELECTED_ENTITY_IDS.includes(COMPARE_SETTINGS.target)) ? COMPARE_SETTINGS.target : '',
        compareCumsum: !!COMPARE_SETTINGS.cumsum,
        // true, solange die aktuelle compareSollEntityId NUR deshalb in
        // selectedEntityIds steckt, weil setSollEntity() sie dort hinein
        // genommen hat (nicht, weil sie schon vorher regulär gewählt war)
        // — entscheidet, ob clearSollEntity() sie beim Leeren des Feldes
        // wieder entfernen darf (siehe dort).
        sollAutoAdded: false,
        // Mehrjahresvergleich (compareMode 'longterm', "Jahre" siehe
        // COMPARE_YEARS_OPTIONS) — wie Laufsumme/Soll mit dem Chart
        // gespeichert (compare_settings).
        compareYearsOptions: COMPARE_YEARS_OPTIONS,
        compareYears: COMPARE_SETTINGS.years ?? 5,
        multiYearAverage: COMPARE_SETTINGS.average ?? true,
        multiYearTableMode: 'values',
        multiYearMonths: [t('Jan'), t('Feb'), t('Mär'), t('Apr'), t('Mai'), t('Jun'), t('Jul'), t('Aug'), t('Sep'), t('Okt'), t('Nov'), t('Dez')],
        showPoints: SHOW_POINTS,
        showValues: SHOW_VALUES,
        averageLine: AVERAGE_LINE,
        areaFill: AREA_FILL,
        stacked: STACKED,
        normalize: NORMALIZE,
        averageStyle: AVERAGE_STYLE,
        horizontal: HORIZONTAL,
        raw: false,
        // Zeitstrahl (AN-Intervalle statt Linie/Balken) — wie auf der
        // Entität-eigenen Chart-Seite, hier nur sinnvoll/anwählbar, wenn ALLE
        // geladenen Serien Schalter sind (siehe allSwitch-Getter); bei
        // gemischten Charts bliebe unklar, was eine gemeinsame Zeitstrahl-
        // Achse für eine Standard-/Zähler-Serie bedeuten sollte. Aus
        // CHART_TYPE vorbelegt (persistiert über save(), siehe dort) statt
        // fest false — sonst würde jeder Seitenaufruf die zuletzt für dieses
        // Chart gespeicherte Wahl verwerfen.
        timeline: ['timeline', 'shares', 'changes', 'heatmap'].includes(CHART_TYPE),
        // Zustands-Charts: Zeitstrahl, Anteile oder Wechsel je Zustands-Entität
        // (die Balken-Ansichten laufen über dieselbe timeline-Weiche, damit die
        // Zeitverlauf-Optionen im Menü ausgeblendet bleiben). Gespeichert als
        // chart_type "timeline", "shares" oder "changes".
        // Ohne gewählte Ansicht ("auto") gilt je Zeitraum der Standard
        // (StateEntities.defaultMode); eine Wahl (stateChosen) bleibt, auch über
        // einen Zeitraumwechsel, und wird als chart_type gespeichert.
        // Die Heatmap gibt es erst ab dem Monat. heatmapWanted merkt den Wunsch
        // (gespeichert oder per Klick), auch wenn der Zeitraum gerade zu kurz
        // ist und stateMode deshalb auf den Standard zurückfällt — beim
        // Zurückwechseln kommt sie wieder (stateModeFor()).
        heatmapWanted: CHART_TYPE === 'heatmap',
        stateMode: stateModeFor(RANGE_KEY, ['timeline', 'shares', 'changes', 'heatmap'].includes(CHART_TYPE) ? CHART_TYPE : null),
        stateChosen: ['timeline', 'shares', 'changes', 'heatmap'].includes(CHART_TYPE),
        // Gezeichneter Zustand je Zustands-Entität der Heatmap: Rohtext, nicht
        // der Index (siehe saved_charts.heatmap_states). Fehlt er, gilt die
        // Vorbelegung (StateEntities.heatmapIndexFor()).
        heatmapStates: {...HEATMAP_STATES},
        // Ob die Darstellungsart schon feststeht (gespeichert oder per Klick
        // gewählt). Solange nicht, wird ein reines Schalter-Chart nach dem
        // Laden automatisch zum Zeitstrahl (Standard) — siehe load().
        typeChosen: CHART_TYPE !== 'auto',
        // Donut statt Zeitverlauf — ein Anteil je Serie (Summe/Durchschnitt/
        // Letzter Wert, siehe donutAggregation) statt einer Zeitachse.
        // Eigenes Feld statt eines dritten timeline-artigen Strings, weil
        // beide Darstellungsarten im Menü als ZWEI verschiedene Zeilen
        // auftreten (Darstellungsart oben, Zeitstrahl darunter, nur bei
        // Zeitverlauf sichtbar) — siehe setDisplayMode()/render() unten.
        donut: CHART_TYPE === 'donut',
        donutAggregation: DONUT_AGGREGATION,
        resolutionPreset: RESOLUTION_PRESET,
        dynamicYAxis: DYNAMIC_Y_AXIS,
        // Min/Max/Ø in der Legende statt einer separaten Kachel-Reihe (siehe
        // render(): die Legende wird komplett selbst gebaut statt ECharts'
        // eigene zu zeigen, damit Statistik und Serien-Umschalter ein
        // einziges Element sind). legendHiddenIds statt eines Set — direkte
        // .includes()-Aufrufe in Template-Ausdrücken sind bei Alpine
        // zuverlässig reaktiv, ein Set (oder ein Getter, der verschachtelt
        // darauf zugreift) war es an anderer Stelle in dieser Datei
        // nachweislich nicht (siehe autoResolutionLabel-Kommentar oben).
        chartStats: CHART_STATS,
        legendMetrics: [...LEGEND_METRICS],
        // Welche der beiden "Statistik in Legende"-Darstellungen gezeigt
        // wird — wie legendMetrics ein gespeichertes Chart-Feld (siehe
        // save()), damit sie beim erneuten Öffnen erhalten bleibt.
        legendStyle: LEGEND_STYLE,
        decimals: DECIMALS,
        legendHiddenIds: [],
        optionsMenuOpen: false,
        compareMenuOpen: false,
        // Ein bereits gespeichertes Chart öffnet sich im reinen Anzeige-Modus
        // (nur Chart + Zeitraum/Vergleich) — der "Anpassen"-Button schaltet
        // erst dann Entitäts-Auswahl und Speichern/Löschen sichtbar. Ein noch
        // ungespeichertes neues Chart hat nichts anzuzeigen, startet also
        // direkt im Bearbeiten-Modus. Der "Bearbeiten"-Button auf der
        // Charts-Übersicht verlinkt mit ?edit=1 hierher, um direkt im
        // Bearbeiten-Modus zu starten statt erst über "Anpassen" umschalten
        // zu müssen.
        editing: !CHART_ID || new URLSearchParams(location.search).has('edit'),
        // Zustands-Entitäten, die wegen gemischter Zahlen-Entitäten nicht
        // gezeichnet werden (siehe load()).
        droppedStateIds: [],
        loading: false,
        saving: false,
        savedMessage: '',
        _requestId: 0,

        get selectedCount() { return this.selectedEntityIds.length; },
        get legendMetricOptions() { return LEGEND_METRIC_OPTIONS; },
        labelFor(entityId) { return ENTITY_LABELS[entityId] || entityId; },
        unitFor(entityId) { return ENTITY_UNITS[entityId] || ''; },
        get visibleEntityIds() {
          return this.selectedEntityIds.filter(id => !this.hiddenEntityIds.includes(id));
        },
        // Farbe hängt an der Position in der GESAMTEN Liste, nicht am Index
        // der geladenen Serien: sonst rutschten beim Ausblenden einer Serie
        // alle folgenden eine Farbe weiter, und ein kurzes Ein-/Ausblenden
        // färbte das halbe Chart um.
        colorIndexFor(entityId) {
          const idx = this.selectedEntityIds.indexOf(entityId);
          return idx === -1 ? 0 : idx;
        },
        // Eigene Farbe je Serie ("palette:N" oder "#rrggbb", siehe
        // saved_charts.series_colors), sonst Palettenfarbe nach Position.
        // Gleiche Regel wie colorFor() in dashboard-tiles.js.
        colorFor(entityId) {
          const own = this.seriesColors[entityId];
          if (typeof own === 'string') {
            if (own.startsWith('palette:') && PALETTE[+own.slice(8)]) return PALETTE[+own.slice(8)];
            if (/^#[0-9a-f]{6}$/i.test(own)) return own;
          }
          return PALETTE[this.colorIndexFor(entityId) % PALETTE.length];
        },
        // null = zurück auf Auto. Neues Objekt statt Mutation, damit Alpine
        // die Änderung sicher bemerkt (wie bei heatmapStates).
        setSeriesColor(entityId, value) {
          const next = {...this.seriesColors};
          if (value) next[entityId] = value;
          else delete next[entityId];
          this.seriesColors = next;
          this.render();
        },
        toggleEntityHidden(entityId) {
          const idx = this.hiddenEntityIds.indexOf(entityId);
          if (idx === -1) this.hiddenEntityIds.push(entityId);
          else this.hiddenEntityIds.splice(idx, 1);
          this.load();
        },
        get hasData() { return this.series.some(s => s.points.length > 0 || (s.columns && s.columns.length > 0)); },
        // "Punkte" markiert einzelne Datenpunkte auf einer Linie — für
        // Balken-Serien (Zähler/Schalter, siehe chart_type-Kommentar bei
        // render()) ohne jede Wirkung, deshalb nur sichtbar, wenn mindestens
        // eine der aktuell geladenen Serien tatsächlich als Linie gerendert wird.
        get hasLineSeries() { return this.series.some(s => s.chart_type === 'line'); },
        // Gegenstück für "Werte anzeigen": eine Zahl je Balken bleibt lesbar,
        // dieselbe Beschriftung an jedem Punkt einer Linie (oft hunderte je
        // Serie) überdeckt dagegen die Kurve — deshalb nur bei mindestens
        // einer Balken-Serie angeboten. Im Zeitstrahl-/Rohwerte-Modus liefert
        // der Server ausschließlich chart_type 'line', die Zeile verschwindet
        // dort also automatisch (früher: :disabled="timeline").
        get hasBarSeries() { return this.series.some(s => s.chart_type === 'bar'); },
        // Auflösung "Voll" (Tag/Woche/Monat/Jahr, je nach Zeitraum — siehe
        // RESOLUTION_LABELS[range].full) fasst den kompletten Zeitraum zu
        // GENAU EINEM Wert je Entität zusammen, jede Entität bekommt dabei
        // eine eigene Kategorie auf der Achse (Ranking-Vergleich, s.
        // render()). Eigener Getter statt der lokalen Konstante in render():
        // wird auch im Template gebraucht (Ausrichtung/Gestapelt-Sichtbarkeit).
        get singleBucket() { return this.resolutionPreset === 'full'; },
        // "Gestapelt" (Optionen-Menü) nur ab zwei Balken-Serien UND außerhalb
        // von Auflösung "Voll" sinnvoll/anwählbar — mit nur einer Balken-
        // Serie wäre die Fläche identisch zur normalen Balken-Darstellung,
        // und bei "Voll" hat jede Entität bereits ihre eigene Kategorie
        // (Ranking-Vergleich); eine Kombination aus "alle Entitäten in eine
        // Kategorie stapeln" UND "jede Entität ihre eigene Kategorie" wäre
        // ein Widerspruch, deshalb schließen sich beide aus — dieselbe
        // Konvention wie "Gestapelt" + "Vergleichen".
        get canStack() { return !this.singleBucket && this.series.filter(s => s.chart_type === 'bar').length >= 2; },
        // "Ausrichtung" (Vertikal/Horizontal, Optionen-Menü) — setHorizontal()
        // weiter unten, statt eines eigenen canGoHorizontal-Getters: die Zeile
        // ist jetzt immer sichtbar, "Horizontal" schaltet den dafür nötigen
        // Ranking-Vergleich (Auflösung "Voll") selbst mit ein.
        // Nur wenn ALLE geladenen Serien Schalter oder Zustände sind, macht ein
        // gemeinsamer Zeitstrahl (eine Zeile je Entität) Sinn — siehe
        // timeline-Kommentar oben.
        get allSwitch() { return this.series.length > 0 && this.series.every(s => ['switch', 'state'].includes(s.aggregation_type)); },
        // Zustands-Entitäten (ROADMAP 1.29) gibt es nur als Zeitstrahl oder
        // Donut — eine Linie aus Zustandsindizes hätte keine Aussage.
        get hasState() { return this.series.some(s => s.aggregation_type === 'state'); },
        // Vor dem ersten Laden ist noch nicht bekannt, ob Zustände im Chart
        // sind — die Heatmap wird dann trotzdem mit angefragt (der Server
        // liefert Zellen nur für Zustände, sonst nichts).
        get heatmapAvailable() { return StateEntities.SERVER_SHARE_RANGES.includes(this.range); },
        get hasStateOrUnknown() { return this.hasState || !this.series.length; },
        // Balken-Ansicht der Zustände gewünscht — solange noch keine Serien
        // geladen sind, gilt der gespeicherte Wunsch (load() prüft ihn danach).
        get wantsStateBars() { return this.stateMode !== 'timeline' && (this.hasState || !this.series.length); },
        // Zustände stehen nie neben anderen Serien (load() wirft sie sonst
        // heraus): die Legende trägt dann immer Dauer und Anteil, wie auf der
        // Entitätsseite — der Schalter "Statistik in Legende" entfällt,
        // statsShown ersetzt chartStats in der Legende.
        get statsShown() { return this.chartStats || this.hasState; },
        get resolutionOptions() {
          const labels = RESOLUTION_LABELS[this.range] || RESOLUTION_LABELS.day;
          const options = [
            {value: 'auto', label: t('Automatisch')},
            {value: 'medium', label: labels.medium},
            {value: 'coarse', label: labels.coarse},
          ];
          // Optionale dritte, noch gröbere Stufe je Zeitraum (aktuell nur
          // "Tag" bei Zeitraum "Tag", siehe RESOLUTION_SECONDS/-LABELS oben)
          // — nur angeboten, wenn für den aktuellen Zeitraum definiert.
          if (labels.full) options.push({value: 'full', label: labels.full});
          return options;
        },
        // Ermittelt, welche Auflösung "Automatisch" gerade tatsächlich bedeutet
        // (siehe detectResolutionSeconds oben) — aus der ersten Serie mit genug
        // Rohpunkten, da die Server-Auflösung je nach Aggregationstyp zwischen
        // Entitäten derselben Serie ohnehin variieren kann. Von load() nach
        // jedem Neuladen in this.autoResolutionLabel geschrieben (siehe
        // Kommentar dort zum Grund, warum kein Getter).
        computeAutoResolutionLabel() {
          for (const s of this.series) {
            const seconds = detectResolutionSeconds(s.points);
            if (seconds != null) return fmtDurationLabel(seconds);
          }
          return '';
        },
        // Nachkommastellen-Override (Optionen-Menü, "Nachkommastellen") — bei
        // "Auto" behält jede Serie ihre eigene entities.decimals-Einstellung
        // (s.decimals, vom Server je Entität geliefert), sonst gilt dieser
        // Wert für ALLE Serien einheitlich statt individuell.
        effectiveDecimals(s) {
          return this.decimals === 'auto' ? s.decimals : parseInt(this.decimals, 10);
        },
        // Gemeinsame Kennzahlen-Berechnung für seriesStats — als eigene
        // Funktion statt inline, damit dieselbe Rechnung sowohl auf die
        // Hauptperiode (s.points) als auch auf die Vergleichsperiode
        // (s.compare_points) angewendet werden kann, siehe seriesStats.compare
        // unten. windowEnd ist Parameter statt fest this.windowEnd, weil die
        // Vergleichsperiode ihr eigenes Fensterende hat (s.compare_window_end)
        // — relevant für switchOnDuration() im Rohwerte-Modus.
        seriesStatsFor(s, points, windowEnd) {
          const values = points.map(p => p.value).filter(Number.isFinite);
          const minima = points.map(p => Number.isFinite(p.min) ? p.min : p.value).filter(Number.isFinite);
          const maxima = points.map(p => Number.isFinite(p.max) ? p.max : p.value).filter(Number.isFinite);
          const isDuration = s.aggregation_type === 'switch' && s.display_mode === 'time';
          const unit = s.unit ? ` ${s.unit}` : '';
          const formatted = value => isDuration ? NumberFormat.fmtDuration(value) : `${fmtNum(value, this.effectiveDecimals(s))}${unit}`;
          // Summe bei Zählern sinnvoll — Bucket-Werte sind dort bereits
          // Deltas je Zeitfenster (siehe query.py), deren Summe den
          // Gesamtverbrauch im Zeitraum ergibt — UND bei Schaltern, deren
          // Bucket-Werte bereits Einschaltsekunden sind (z. B. Summe =
          // gesamte Anwesenheitsdauer im Zeitraum). Bei Zählern nicht im
          // Rohwerte-Modus (Einzelmesswerte statt Deltas); bei Schaltern
          // dagegen AUCH im Rohwerte-/Zeitstrahl-Modus möglich — dort kommt
          // die Summe nicht aus den (nur 0/1-wertigen) Punktwerten, sondern
          // aus switchOnDuration() oben.
          const isSwitch = s.aggregation_type === 'switch';
          const hasSum = (s.aggregation_type === 'counter' && !this.raw) || isSwitch;
          const sumValue = this.raw && isSwitch ? switchOnDuration(points, windowEnd) : values.reduce((sum, v) => sum + v, 0);
          // Summe bei Schaltern ist immer eine Dauer in Sekunden — anders
          // als "last"/"min"/"max"/"average" (die bei Rohwert-Anzeigemodus
          // echte 0/1-Zustände sind) unabhängig vom Anzeigemodus
          // (display_mode) immer als h/m/s formatiert statt über
          // fmtNum()s generische 4-signifikante-Stellen-Rundung, die bei
          // größeren Sekundenwerten sichtbar ungenau wird.
          const sumFormatted = isSwitch ? NumberFormat.fmtDuration(sumValue) : formatted(sumValue);
          return {
            last: values.length ? formatted(values[values.length - 1]) : '—',
            min: minima.length ? formatted(Math.min(...minima)) : '—',
            max: maxima.length ? formatted(Math.max(...maxima)) : '—',
            average: values.length ? formatted(values.reduce((sum, v) => sum + v, 0) / values.length) : '—',
            sum: hasSum ? (values.length ? sumFormatted : '—') : null,
          };
        },
        // Tabellen-Legende bei Zuständen: eine Zeile je Entität und Zustand.
        get stateTableRows() {
          return this.seriesStats.filter(st => st.isState).flatMap(st => st.stateRows.map(row => ({
            key: `${st.entityId}:${row.idx}`,
            name: st.name,
            label: row.label,
            raw: row.raw,
            swatch: row.swatch,
            duration: NumberFormat.fmtDuration(row.seconds),
            share: StateEntities.fmtShare(row.share),
            entries: row.entries.toLocaleString(window.ZA_FORMAT),
          })));
        },
        // Dauer und Anteil je Zustand einer Zustands-Serie im Zeitraum — für die
        // Legende je Zeile und den Donut. Rohwechsel (Zeitstrahl, bis zur
        // Woche) und Server-Anteile (ab dem Monat) ergeben dieselbe Form.
        stateRowsFor(s) {
          // Ohne Rohwechsel (Rohabfrage über der Grenze) aus den Spalten vom Server.
          const buckets = s.chart_type === 'shares'
            ? StateEntities.bucketsFromShares(s.points)
            : (!s.points.length && s.columns
              ? StateEntities.bucketsFromShares(s.columns.map(([ts, states]) => ({ts, states})))
              : StateEntities.bucketsFromSegments(StateEntities.segments(s.points, this.windowEnd), () => 0, () => Infinity));
          return StateEntities.totals(buckets, s.states).map(row => ({
            ...row,
            label: StateEntities.label(s.states, row.idx),
            raw: StateEntities.raw(s.states, row.idx),
            swatch: StateEntities.swatchStyle(row.idx),
            text: `${NumberFormat.fmtDuration(row.seconds)} · ${StateEntities.fmtShare(row.share)}`,
          }));
        },
        // Summe der Vergleichsperiode „bis zum gleichen Tag“ (bzw. zur gleichen Uhrzeit), solange
        // die aktuelle Periode läuft. Nur wenn sie sich von der Gesamtsumme daneben unterscheidet —
        // beim Vorjahr ist das Fenster ohnehin schon gekappt, und zwei gleiche Zahlen wären Lärm.
        compareCutFor(s) {
          if (!s.compare_cut_points) return null;
          const full = this.seriesStatsFor(s, s.compare_points, s.compare_window_end).sum;
          const cut = this.seriesStatsFor(s, s.compare_cut_points, s.compare_cut_end).sum;
          if (cut === null || cut === full) return null;
          const end = new Date(s.compare_cut_end * 1000);
          const where = ['hour', 'day'].includes(this.range) ? fmtTime(end) : fmtDayMonth(end);
          return {label: t('bis {cut}', {cut: where}), sum: cut};
        },
        get seriesStats() {
          return this.series.map((s, i) => {
            // Vergleichs-Nebenserie (Vorjahr/Vorperiode, siehe render()) taucht
            // sonst in keiner Legende auf — dieselbe Rechnung wie die Hauptserie,
            // nur auf s.compare_points angewendet, damit Chip- und Tabellen-
            // Legende sie als gedämpfte Unterzeile je Entität zeigen können.
            const compare = (this.compare && s.compare_points && s.compare_points.length)
              ? {
                  seriesLabel: this.compareMode === 'year' ? t('Vorjahr') : t('Vorperiode'),
                  period: formatPeriodLabel(this.range, this.continuous, s.compare_window_start, s.compare_window_end, false),
                  ...this.seriesStatsFor(s, s.compare_points, s.compare_window_end),
                  cut: this.compareCutFor(s),
                }
              : null;
            return {
              entityId: s.entity_id,
              name: this.entityNames[s.entity_id] || s.friendly_name,
              isState: s.aggregation_type === 'state',
              stateRows: s.aggregation_type === 'state' ? this.stateRowsFor(s) : [],
              color: this.colorFor(s.entity_id),
              ...this.seriesStatsFor(s, s.points, this.windowEnd),
              // Min/Max/Ø über Zustandsindizes sind keine Aussage.
              ...(s.aggregation_type === 'state' ? {last: '—', min: '—', max: '—', average: '—', sum: null} : {}),
              compare,
            };
          });
        },
        // Eigene, nicht umschaltbare Legende für den Balken-Renderpfad bei
        // "Vorjahr" + range='year' + gewählter Soll-Entität (chart_editor.html,
        // eigener Block neben der normalen .chart-legend) — anders als
        // seriesStats oben (ein Eintrag je Entität) liefert dieser Getter für
        // jede "echte" Entität ZWEI (aktuelles Jahr + Vorjahr), weil
        // renderYearsCompare() daraus echte, unabhängig benannte Serien
        // macht statt einer gedämpften Vergleichs-Nebenserie. Die Jahreszahl
        // selbst kommt nicht vom Server (anders als beim früheren, inzwischen
        // wieder entfernten "Jahre"-Modus mit frei wählbaren Kalenderjahren)
        // — bei "Vorjahr" ist es immer "das Kalenderjahr von windowStart" und
        // exakt ein Jahr davor. toggleLegendItem() (Serien-Ausblenden) ist
        // hier bewusst nicht angebunden — "welches Jahr einer Entität"
        // bräuchte eine eigene Ausblend-ID-Konvention, für den ersten Wurf
        // dieser Funktion nicht eingebaut (siehe CHANGELOG_INTERNAL.md).
        get yearsSeriesStats() {
          const out = [];
          const currentYear = this.windowStart ? new Date(this.windowStart * 1000).getFullYear() : new Date().getFullYear();
          const previousYear = currentYear - 1;
          this.series.forEach(s => {
            if (!s.compare_points) return;
            const color = this.colorFor(s.entity_id);
            const name = this.entityNames[s.entity_id] || s.friendly_name;
            // Die Soll-Entität bekommt EINEN Eintrag ohne Jahreszahl (siehe
            // renderYearsCompare(): sie ist eine einzelne Ziel-Linie, kein
            // Jahresvergleich mit sich selbst) — das aktuelle Jahr, dasselbe,
            // das dort auch gezeichnet wird.
            if (s.entity_id === this.compareSollEntityId) {
              out.push({key: s.entity_id, color, name, ...this.seriesStatsFor(s, s.points, this.windowEnd)});
              return;
            }
            out.push({
              key: `${s.entity_id}-${previousYear}`,
              color,
              name: `${name} (${previousYear})`,
              ...this.seriesStatsFor(s, s.compare_points, s.compare_window_end),
            });
            out.push({
              key: `${s.entity_id}-${currentYear}`,
              color,
              name: `${name} (${currentYear})`,
              ...this.seriesStatsFor(s, s.points, this.windowEnd),
            });
          });
          return out;
        },
        get periodLabel() {
          return formatPeriodLabel(this.range, this.continuous, this.windowStart, this.windowEnd, this.isCurrent);
        },
        // Gemeinsamer Dateiname für CSV- und Bild-Export (exportCsv()/
        // toolbox.feature.saveAsImage in render()/renderTimelineMulti()/
        // renderDonut()) — Chart-Name + aufgelöster Zeitraum, siehe
        // formatPeriodForFilename()/sanitizeFilename().
        get exportFilename() {
          const namePart = sanitizeFilename(this.name || 'chart') || 'chart';
          const periodPart = sanitizeFilename(
            formatPeriodForFilename(this.range, this.continuous, this.windowStart, this.windowEnd, this.isCurrent)
          );
          return periodPart ? `${namePart}_${periodPart}` : namePart;
        },
        get comparePreviousLabel() { return previousPeriodLabel(this.range); },
        get compareYearLabel() { return previousYearPeriodLabel(this.range); },
        // Blendet die zweite Menüzeile aus, wo sie nichts Eigenes aussagt
        // (siehe COMPARE_YEAR_RANGES).
        get compareYearAvailable() { return compareYearAvailable(this.range); },
        // Zeigt im Button selbst, WELCHER Vergleich aktiv ist (statt nur
        // "Vergleichen" + separatem Auswahl-Segment daneben) — passt sich wie
        // die beiden Label-Getter oben automatisch an den Zeitraum an.
        get compareButtonLabel() {
          if (!this.compare) return t('Vergleichen');
          if (this.compareMode === 'longterm') return t('Langzeit');
          return this.compareMode === 'year' ? this.compareYearLabel : this.comparePreviousLabel;
        },
        // EIN Ort für "zeichnet render() gerade über renderYearsCompare()"
        // (siehe dort) statt derselben Bedingung an mehreren Stellen in
        // chart_editor.html — sonst driftet eine Kopie irgendwann auseinander
        // (z. B. eine vergessene compareCumsum-Prüfung). Soll ODER Laufsumme
        // reicht schon aus: beide brauchen die Kalendermonats-Buckets aus
        // renderYearsCompare() (monthlyBuckets()/cumulate()), die gewohnte
        // Schattenlinie kennt keine Monats-Buckets. Laufsumme allein zeichnet
        // dann Balkenpaare ohne Ziel-Linie, aber MIT der kumulierten Achse.
        get yearsCompareActive() {
          return this.compare && this.compareMode === 'previous' && this.range === 'year'
            && (this.compareCumsum || !!this.compareSollEntityId);
        },
        // Wann der Menüeintrag "Langzeit" erscheint: Zeitraum "Jahr",
        // kalendarisch, genau eine Entität (eine Soll-Entität zählt nicht mit,
        // setLongterm() entfernt sie). Dieselben Bedingungen prüft
        // api_query_multi() auf dem Server.
        get longtermAvailable() {
          return this.range === 'year' && !this.continuous && this.orderableEntityIds.length === 1;
        },
        // "Langzeit" gewählt und möglich — dann fragt load() die Vorjahre mit ab.
        get multiYearRequested() {
          return this.compare && this.compareMode === 'longterm' && this.longtermAvailable;
        },
        // ... und die Antwort ist da: render() zeichnet über renderMultiYear().
        get multiYearActive() {
          return this.multiYearRequested && this.series.length === 1 && Array.isArray(this.series[0].years);
        },
        // Gemeinsame Rechengrundlage für Chart, Legende und Tabelle des
        // Mehrjahresvergleichs: je Jahr zwölf Kalendermonatswerte (dieselben
        // monthlyBuckets() wie renderYearsCompare()), der Jahreswert, und der
        // Ø je Monat über die Vorjahre — das angezeigte Jahr zählt nicht mit,
        // sonst mittelte es sich selbst. Läuft das Jahr noch, kommen die
        // Vergleichswerte "bis zum gleichen Tag" aus den cut_points jedes
        // Vorjahres (siehe query_previous_years()) statt aus ganzen Monaten.
        get multiYear() {
          if (!this.multiYearActive) return null;
          const s = this.series[0];
          const agg = s.aggregation_type;
          const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
          const yearValue = monthly => {
            const values = monthly.filter(v => v != null);
            if (!values.length) return null;
            return agg === 'standard' ? mean(values) : values.reduce((a, b) => a + b, 0);
          };
          const monthlyMean = rows => Array.from({length: 12}, (_, m) => mean(rows.map(r => r[m]).filter(v => v != null)));
          const currentYear = this.windowStart ? new Date(this.windowStart * 1000).getFullYear() : new Date().getFullYear();
          const running = this.isCurrent && this.windowEnd != null;
          const cutMonth = running ? new Date(this.windowEnd * 1000 - 1).getMonth() : null;
          const cutLabel = running ? fmtDayMonth(new Date(this.windowEnd * 1000)) : null;
          const current = {year: currentYear, points: s.points, monthly: monthlyBuckets(s.points, agg), isCurrent: true};
          const previous = s.years.map(y => ({
            year: y.year,
            points: y.points,
            monthly: monthlyBuckets(y.points, agg),
            cutMonthly: y.cut_points ? monthlyBuckets(y.cut_points, agg) : null,
            isCurrent: false,
          }));
          previous.forEach(y => { y.value = yearValue(y.monthly); });
          current.value = yearValue(current.monthly);
          const average = previous.length ? monthlyMean(previous.map(y => y.monthly)) : null;
          const cutAverage = running && previous.length ? monthlyMean(previous.filter(y => y.cutMonthly).map(y => y.cutMonthly)) : null;
          return {
            entity: s,
            agg,
            isDuration: agg === 'switch',
            years: [current, ...previous],
            previous,
            average,
            averageValue: average ? yearValue(average) : null,
            averageFrom: previous.length ? previous[previous.length - 1].year : null,
            averageTo: previous.length ? previous[0].year : null,
            cutMonth,
            cutLabel,
            // Vergleichsbasis des angezeigten Jahres: je Monat der Ø, im
            // angeschnittenen laufenden Monat der Ø "bis zum gleichen Tag".
            currentReference: average
              ? average.map((v, m) => (m === cutMonth && cutAverage ? cutAverage[m] : v))
              : null,
            // Je Monat gemittelt und dann aufaddiert (bzw. gemittelt), nicht
            // je Jahr: ein angeschnittenes erstes Archivjahr (Daten erst ab
            // Dezember) hätte "bis heute" sonst als 0 mitgezählt und den Ø
            // nach unten gezogen — gemessen mit einer PV-Anlage ab 21.12.2013.
            currentAverageToDate: running ? (cutAverage ? yearValue(cutAverage) : null) : (average ? yearValue(average) : null),
          };
        },
        // Abweichung zum Ø als Text: Zähler/Schalter in Prozent, Messwerte als
        // Differenz in ihrer Einheit — 2 °C über dem Ø sind keine "+18 %".
        multiYearDelta(value, reference) {
          const my = this.multiYear;
          if (!my || value == null || reference == null) return '';
          if (my.agg === 'standard') {
            const diff = value - reference;
            const unit = my.entity.unit ? ` ${my.entity.unit}` : '';
            return `${diff >= 0 ? '+' : '−'}${fmtNum(Math.abs(diff), this.effectiveDecimals(my.entity))}${unit}`;
          }
          if (!reference) return '';
          const pct = (value / reference - 1) * 100;
          return `${pct >= 0 ? '+' : '−'}${fmtNum(Math.abs(pct), 0)} %`;
        },
        multiYearFormat(value) {
          const my = this.multiYear;
          if (!my || value == null) return '—';
          return my.isDuration ? NumberFormat.fmtDuration(value) : fmtNum(value, this.effectiveDecimals(my.entity));
        },
        // Ältere Jahre blasser, das angezeigte Jahr in voller Farbe — eine
        // Farbe je Entität wie überall im Editor, nach Alter abgestuft, damit
        // auch "Alle" mit vielen Linien lesbar bleibt.
        multiYearColor(index, total) {
          const color = this.colorFor(this.series[0].entity_id);
          if (index === 0) return color;
          const steps = Math.max(total - 1, 1);
          return withOpacity(color, total === 2 ? 0.55 : 0.75 - 0.5 * (index - 1) / Math.max(steps - 1, 1));
        },
        // Legende (Chips wie Tabelle, je nach "Legenden-Stil"): ein Eintrag je
        // Jahr, neuestes zuerst, plus der Ø — dieselben Kennzahlen wie jede
        // andere Legende über seriesStatsFor().
        get multiYearSeriesStats() {
          const my = this.multiYear;
          if (!my) return [];
          const s = my.entity;
          const out = my.years.map((y, i) => {
            const entry = {
              key: `${s.entity_id}-${y.year}`,
              color: this.multiYearColor(i, my.years.length),
              name: String(y.year),
              ...this.seriesStatsFor(s, y.points, null),
              delta: null,
            };
            if (y.isCurrent && my.currentAverageToDate != null) {
              const delta = this.multiYearDelta(y.value, my.currentAverageToDate);
              if (delta) {
                entry.delta = my.cutLabel
                  ? t('bis {cut}: {delta} zum Ø', {cut: my.cutLabel, delta})
                  : t('{delta} zum Ø', {delta});
              }
            }
            return entry;
          });
          if (my.average && this.multiYearAverage) {
            out.push({
              key: `${s.entity_id}-average`,
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted').trim(),
              name: t('Ø {from}–{to}', {from: my.averageFrom, to: my.averageTo}),
              ...this.seriesStatsFor(s, my.average.filter(v => v != null).map(v => ({value: v})), null),
              delta: null,
              isAverage: true,
            });
          }
          return out;
        },
        // Tabelle Jahr × Monat unter dem Chart. Zellen dezent nach Abweichung
        // vom Monats-Ø eingefärbt — zwei neutrale Akzentfarben statt grün/rot,
        // weil "mehr" je nach Entität gut (PV-Ertrag) oder schlecht
        // (Stromverbrauch) ist.
        get multiYearTable() {
          const my = this.multiYear;
          if (!my) return null;
          const showDelta = this.multiYearTableMode === 'delta';
          const shade = (value, reference) => {
            if (value == null || reference == null) return '';
            const ratio = my.agg === 'standard'
              ? (value - reference) / Math.max(Math.abs(reference), 5)
              : (reference ? value / reference - 1 : 0);
            const pct = Math.round(Math.min(Math.abs(ratio) / 0.25, 1) * 30);
            if (!pct) return '';
            return `background:color-mix(in srgb, var(${ratio >= 0 ? '--accent-bar' : '--accent-line'}) ${pct}%, transparent);`;
          };
          const rows = my.years.map(y => {
            const reference = y.isCurrent ? my.currentReference : my.average;
            const cells = y.monthly.map((value, m) => {
              const ref = reference ? reference[m] : null;
              const partial = y.isCurrent && m === my.cutMonth;
              if (value == null) return {text: '–', style: '', cls: 'empty', title: ''};
              return {
                text: (showDelta ? (this.multiYearDelta(value, ref) || '–') : this.multiYearFormat(value)) + (partial ? '*' : ''),
                style: shade(value, ref),
                cls: partial ? 'partial' : '',
                title: ref != null ? t('Ø {value}', {value: this.multiYearFormat(ref)}) : '',
              };
            });
            const totalReference = y.isCurrent ? my.currentAverageToDate : my.averageValue;
            return {
              year: y.year,
              isCurrent: y.isCurrent,
              cells,
              total: this.multiYearFormat(y.value) + (y.isCurrent && my.cutMonth != null ? '*' : ''),
              delta: my.average ? this.multiYearDelta(y.value, totalReference) : '',
            };
          });
          return {
            rows,
            average: my.average ? {
              label: t('Ø {from}–{to}', {from: my.averageFrom, to: my.averageTo}),
              cells: my.average.map(v => this.multiYearFormat(v)),
              total: this.multiYearFormat(my.averageValue),
            } : null,
            totalLabel: my.agg === 'standard' ? t('Ø Jahr') : t('Summe'),
            cutNote: my.cutMonth != null
              ? t('* {year} bis {cut}; „Δ zum Ø“ vergleicht mit dem Ø bis zum gleichen Tag.', {year: my.years[0].year, cut: my.cutLabel})
              : '',
          };
        },

        toggleEntity(entityId, checked) {
          const idx = this.selectedEntityIds.indexOf(entityId);
          if (checked) { if (idx === -1) this.selectedEntityIds.push(entityId); }
          else if (idx !== -1) this.selectedEntityIds.splice(idx, 1);
          // Eine abgewählte Entität bleibt sonst als tote Referenz im
          // Soll-Feld stehen — ohne Daten zu render, aber weiter mit Namen
          // im Knopf sichtbar.
          if (!checked && entityId === this.compareSollEntityId) {
            this.compareSollEntityId = '';
            this.sollAutoAdded = false;
          }
          this.load();
        },
        // Callback des Soll-Entität-Felds (entityPicker() in chart_editor.html)
        // — sowohl für eine neue Wahl als auch fürs Leeren (id === '', Klick
        // auf die ✕). Anders als schlichtes toggleEntity(id, true) beim Wählen
        // + Stehenlassen beim Leeren: eine per Soll NEU hinzugefügte Entität
        // (sollAutoAdded) wird beim Leeren wieder ENTFERNT statt als
        // gewöhnliche Balkenpaar-Serie (inkl. eigener Kumuliert-Achse) im
        // Chart hängen zu bleiben — gemessen mit Außentemperatur als Soll:
        // nach dem Leeren tauchte sie plötzlich als eigenes Balkenpaar samt
        // "°C"/"°C kumuliert"-Achsen auf, obwohl sie nur als Ziel-Linie
        // gedacht war. War die Entität dagegen schon VOR der Soll-Wahl reguär
        // ausgewählt, bleibt sie beim Leeren unangetastet — sie gehört dann
        // nicht dem Soll-Feld, sondern der normalen Auswahl.
        setSollEntity(id) {
          if (id) {
            this.compareSollEntityId = id;
            if (!this.selectedEntityIds.includes(id)) {
              this.sollAutoAdded = true;
              this.toggleEntity(id, true);
            } else {
              this.sollAutoAdded = false;
              this.render();
            }
            return;
          }
          const previous = this.compareSollEntityId;
          const autoAdded = this.sollAutoAdded;
          this.compareSollEntityId = '';
          this.sollAutoAdded = false;
          if (autoAdded && previous) this.toggleEntity(previous, false);
          else this.render();
        },
        setCompareYears(value) {
          this.compareYears = value;
          this.load();
        },
        // Menüeintrag "Langzeit". Laufsumme und Soll-Linie gehören zu "Vorjahr"
        // und werden zurückgesetzt statt unsichtbar weiterzuwirken. Eine nur
        // fürs Soll-Feld hinzugefügte Entität fliegt dabei wieder raus, wie
        // beim Leeren des Felds (setSollEntity('')), aber ohne dessen eigenes
        // load()/render() — setCompareMode() lädt ohnehin genau einmal.
        setLongterm() {
          this.compareCumsum = false;
          if (this.compareSollEntityId) {
            const previous = this.compareSollEntityId;
            if (this.sollAutoAdded) {
              const idx = this.selectedEntityIds.indexOf(previous);
              if (idx !== -1) this.selectedEntityIds.splice(idx, 1);
            }
            this.compareSollEntityId = '';
            this.sollAutoAdded = false;
          }
          this.setCompareMode('longterm');
        },
        // Für "Angezeigte Namen & Reihenfolge" (Template): die Soll-Entität
        // ist dort keine Zeile zum Umbenennen/Verschieben/Ausblenden — sie
        // hat keine eigene Legendenzeile/Reihenfolge, nur die eine gepunktete
        // Ziel-Linie (siehe renderYearsCompare()), da gibt es nichts zu
        // sortieren. Bleibt trotzdem ganz normal Teil von selectedEntityIds
        // (Abfrage/Farbzuordnung, siehe compareSollEntityId-Kommentar).
        get orderableEntityIds() {
          return this.compareSollEntityId
            ? this.selectedEntityIds.filter(id => id !== this.compareSollEntityId)
            : this.selectedEntityIds;
        },
        // Verschiebt eine Entität in der Anzeige-/Abfragereihenfolge (bestimmt
        // Legenden-, Statistik- und Farbreihenfolge sowie die gespeicherte
        // Reihenfolge) um eine Position nach oben (-1) oder unten (+1) —
        // innerhalb von orderableEntityIds (s. o.) ermittelt, NICHT per
        // rohem Index+Delta in selectedEntityIds wie früher: sonst würde ein
        // "Runter" ausgerechnet an der (unsichtbaren) Soll-Entität vorbei-
        // bzw. mit ihr vertauschen, sobald sie irgendwo dazwischen im rohen
        // Array steht, statt mit der visuell nächsten Zeile zu tauschen.
        moveEntity(entityId, delta) {
          const order = this.orderableEntityIds;
          const orderIdx = order.indexOf(entityId);
          const targetOrderIdx = orderIdx + delta;
          if (orderIdx === -1 || targetOrderIdx < 0 || targetOrderIdx >= order.length) return;
          const neighborId = order[targetOrderIdx];
          const idxA = this.selectedEntityIds.indexOf(entityId);
          const idxB = this.selectedEntityIds.indexOf(neighborId);
          [this.selectedEntityIds[idxA], this.selectedEntityIds[idxB]] = [this.selectedEntityIds[idxB], this.selectedEntityIds[idxA]];
          this.load();
        },
        get canGoForward() { return this.offset < 0; },
        // Was beim Speichern als fester Zeitraum mitgeht: in der laufenden
        // Periode nichts (das Chart bleibt "immer aktuell"), sonst ein Tag in
        // der angezeigten Periode — deren Mitte, wie PeriodNavigation sie als
        // Anker nimmt. Ein Datum statt des Versatzes, weil "ein Jahr zurück"
        // nächstes Jahr ein anderes Jahr wäre.
        get periodAnchorForSave() {
          if (this.offset === 0 || this.windowStart == null || this.windowEnd == null) return null;
          return msToAnchorDate(PeriodNavigation.anchorForWindow(this.windowStart, this.windowEnd, false));
        },
        goBack() { this._rangeAnchorMs = null; this.offset -= 1; this.load(); },
        goForward() {
          if (this.canGoForward) { this._rangeAnchorMs = null; this.offset += 1; this.load(); }
        },
        goToNow() { this._rangeAnchorMs = null; this.offset = 0; this.load(); },
        setRange(key) {
          // Erneuter Klick auf den aktiven Zeitraum: zurück zur laufenden
          // Periode, dieselbe Geste wie auf der Entitätsseite. Steht die
          // Ansicht schon dort, passiert nichts.
          if (key === this.range) {
            if (this.offset !== 0) this.goToNow();
            return;
          }
          // Den angezeigten Zeitpunkt beim Wechsel des Zeitraums behalten
          // (aus 2025 wird bei "Monat" der Dezember 2025), statt auf die
          // laufende Periode zurückzuspringen — wie in entity_detail.js.
          const anchorMs = this._rangeAnchorMs ?? PeriodNavigation.anchorForWindow(
            this.windowStart, this.windowEnd, this.isCurrent
          );
          this._rangeAnchorMs = anchorMs;
          this.offset = PeriodNavigation.offsetForRange(key, anchorMs);
          this.range = key;
          this.stateMode = stateModeFor(key, this.stateChosen ? (this.heatmapWanted ? 'heatmap' : this.stateMode) : null);
          // Anders als auf der Entitätsseite bleibt der Vergleich beim
          // Zeitraumwechsel eingeschaltet — ein stehengebliebener
          // "Vorjahres…"-Modus liefe hier also direkt in die nächste Abfrage.
          if (!compareYearAvailable(key)) this.compareMode = 'previous';
          // Rohwerte gibt es (wie auf der Entität-eigenen Chart-Seite) nur für
          // die kleineren Zeiträume — ohne diesen Reset bliebe raw bei einem
          // Wechsel z. B. zu "Jahr" unsichtbar aktiv (Chip ausgegraut/disabled,
          // aber der Zustand dahinter noch true) und load() würde weiter raw=true
          // an einen Zeitraum senden, für den die Umschaltfläche gar nicht mehr
          // erreichbar ist. Zeitstrahl ist die eine Ausnahme: der erzwingt raw
          // IMMER, unabhängig vom Zeitraum (siehe toggleTimeline()) — sonst
          // würde z. B. "Monat" hier still auf Bucket-Werte zurückfallen, die
          // renderTimelineMulti() fälschlich als durchgehende AN-Intervalle
          // statt als echte Rohübergänge zeichnet.
          if (!this.timeline && !['hour', 'day', 'week'].includes(key)) this.raw = false;
          // "Tag"-Auflösung (RESOLUTION_LABELS.full) gibt es aktuell nur bei
          // Zeitraum "Tag" — ohne diesen Reset bliebe resolutionPreset beim
          // Wechsel z. B. zu "Woche" auf einem Wert stehen, den die dortige
          // Auswahlliste gar nicht mehr anbietet.
          if (this.resolutionPreset === 'full' && !(RESOLUTION_LABELS[key] || {}).full) this.resolutionPreset = 'auto';
          this.load();
        },
        toggleContinuous() { this.continuous = !this.continuous; this.load(); },
        // "Tag"-Auflösung (ein Balken je Entität, s. render()/singleBucket)
        // und Periodenvergleich (zeitversetzte Zweitserie) schließen sich
        // aus derselben Begründung wie raw+compare aus (siehe toggleRaw()) —
        // ein Vergleich zweier Einzelwerte auf einer Kategorie-Achse ergäbe
        // keinen sinnvoll darstellbaren zweiten Zeitpunkt. Vergleich hat
        // Vorrang (siehe setCompareMode()): die "Voll"-Option im Auflösung-
        // Dropdown und der "Horizontal"-Knopf (setHorizontal() setzt
        // resolutionPreset ebenfalls auf 'full') sind disabled, solange
        // compare true ist — 'full' ist von dort aus also gar nicht mehr
        // erreichbar, kein this.compare = false hier mehr nötig.
        onResolutionChange() {
          if (this.resolutionPreset === 'full') {
            this.dynamicYAxis = false;
            // continuous ist (anders als compare/dynamicYAxis) ein
            // Server-Query-Parameter (verschiebt windowStart/windowEnd) —
            // ein bloßes render() würde den Chart weiter mit dem zuvor
            // geladenen rollierenden Fenster zeigen, deshalb load() statt
            // render(), wenn sich der Wert dadurch tatsächlich ändert.
            if (this.continuous) { this.continuous = false; this.load(); return; }
          }
          this.render();
        },
        // "Ausrichtung" (Vertikal/Horizontal, Optionen-Menü) — kehrt die
        // frühere Abhängigkeit um: nicht mehr "Auflösung: Voll" schaltet
        // diese Zeile erst frei, sondern "Horizontal" schaltet selbst in den
        // dafür nötigen Ranking-Vergleich (Auflösung "Voll", s. singleBucket).
        // Zurück auf "Vertikal" setzt die Auflösung auf "Automatisch" —
        // bewusst nicht auf den vorherigen Wert: der müsste sonst extra
        // gemerkt werden, nur um ihn beim nächsten Wechsel wieder zu
        // verwerfen. onResolutionChange() übernimmt dieselben Folgeschritte
        // wie bei manueller Auswahl im Auflösung-Dropdown (Dynamische Y-Achse
        // abschalten, rollierendes Fenster ggf. neu laden).
        setHorizontal(value) {
          if (value === this.horizontal) return;
          this.horizontal = value;
          this.resolutionPreset = value ? 'full' : 'auto';
          this.onResolutionChange();
        },
        // Vergleich hat Vorrang vor den Optionen, mit denen er sich
        // ausschließt (Rohwerte, Zeitstrahl, Gestapelt, Donut) — umgekehrt zur
        // früheren Richtung, in der AKTIVIEREN einer dieser Optionen den
        // Vergleich stillschweigend abschaltete. Ihre Bedienelemente sind
        // jetzt disabled, solange compare true ist (siehe chart_editor.html);
        // das Zurücksetzen hier ist zusätzliche Absicherung für einen älteren
        // gespeicherten Chart, der noch beide Zustände gleichzeitig trägt.
        setCompareMode(mode) {
          this.compare = true;
          this.compareMode = mode;
          // Popover bleibt reine Auswahl (Aus/Vorjahr/Jahre) und schließt bei
          // jeder Wahl sofort, wie die übrigen Menüs auch — Jahr A/B stehen
          // als eigene Zeile unter der Toolbar (siehe chart_editor.html),
          // nicht mehr als weitere Popover-Zeilen, und sind dadurch dauerhaft
          // sichtbar statt an ein offengehaltenes Menü gebunden.
          this.compareMenuOpen = false;
          this.raw = false;
          this.stacked = false;
          this.timeline = false;
          this.donut = false;
          // Ein Vergleich (Vorjahr, Langzeit, …) bezieht sich immer auf die
          // laufende Periode — wer vorher z. B. auf 2024 geblättert hatte,
          // landet wieder bei "jetzt", statt 2024 mit 2023 zu vergleichen.
          this.offset = 0;
          this._rangeAnchorMs = null;
          this.load();
        },
        disableCompare() {
          this.compare = false;
          this.compareMenuOpen = false;
          this.load();
        },
        // Serien-Umschalter der eigenen Legende (siehe render(): ECharts'
        // eigene Legende ist stummgeschaltet, legend.selected wird stattdessen
        // aus legendHiddenIds gebaut) — legendToggleSelect wirkt sofort auf
        // den bestehenden Chart, ohne auf den nächsten render() zu warten;
        // legendHiddenIds hält den Zustand für genau diesen nächsten render()
        // fest (setOption(..., true) baut die Optionen sonst komplett neu auf
        // und würde die Auswahl sofort wieder verwerfen).
        toggleLegendItem(entityId, seriesName) {
          const idx = this.legendHiddenIds.indexOf(entityId);
          if (idx === -1) this.legendHiddenIds.push(entityId);
          else this.legendHiddenIds.splice(idx, 1);
          if (chartInstance) chartInstance.dispatchAction({type: 'legendToggleSelect', name: seriesName});
        },
        // Hover auf eine Legendenzeile hebt bei aktivem Donut den zugehörigen
        // Slice hervor — dasselbe highlight/showTip- bzw. downplay/hideTip-
        // Aktionspaar wie #storage-pie (statistik.js) und .edash-share-donut
        // (energiedashboard.js), dort mit derselben Begründung: Tabelle/
        // Legende übernimmt die Funktion einer echten Legende, Hover soll sie
        // spürbar mit dem Ring verbinden. Nur bei Donut aktiv (sonst kein
        // "seriesIndex: 0"-Kreisdiagramm zum Hervorheben) und nur wenn schon
        // gerendert wurde (chartInstance existiert erst nach dem ersten load()).
        highlightDonutSlice(name) {
          if (!this.donut || !chartInstance) return;
          chartInstance.dispatchAction({type: 'highlight', seriesIndex: 0, name});
          chartInstance.dispatchAction({type: 'showTip', seriesIndex: 0, name});
        },
        unhighlightDonutSlice(name) {
          if (!this.donut || !chartInstance) return;
          chartInstance.dispatchAction({type: 'downplay', seriesIndex: 0, name});
          chartInstance.dispatchAction({type: 'hideTip'});
        },
        // Raw-Modus (Rohwerte) und Periodenvergleich schließen sich gegenseitig
        // aus — dieselbe Begründung wie auf der Entität-eigenen Chart-Seite:
        // ein Vergleich zweier Rohwert-Serien ergibt kaum lesbaren Sinn.
        // Vergleich hat dabei Vorrang (siehe setCompareMode()): das Rohwerte-
        // Kontrollelement ist disabled, solange compare true ist, statt dass
        // ein Klick hier den Vergleich stillschweigend abschaltet.
        toggleRaw() {
          this.raw = !this.raw;
          this.load();
        },
        // Gestapelt + Periodenvergleich zusammen wären eine ungeklärte
        // Kombination (die Vorperiode-Nebenserie müsste dann entweder
        // mitgestapelt — acht statt vier Segmente in einem Balken — oder
        // gesondert behandelt werden) und wurde nie entworfen. Vergleich hat
        // Vorrang (siehe setCompareMode()) — das Gestapelt-Kontrollelement
        // ist disabled, solange compare true ist. Reines render() statt
        // load(): Stapelung ändert nur, WIE die bereits geladenen Daten
        // gezeichnet werden, keinen Server-Query-Parameter.
        toggleStacked() {
          this.stacked = !this.stacked;
          this.render();
        },
        // Zeitstrahl erzwingt Rohwerte (er zeichnet AN/AUS-Übergänge, keine
        // Bucket-Summen) und schließt Vergleich aus — dieselbe Logik wie
        // setChartType('timeline') auf der Entität-eigenen Chart-Seite.
        // Vergleich hat Vorrang (siehe setCompareMode()) — das Zeitstrahl-
        // Kontrollelement ist disabled, solange compare true ist. Beim
        // Ausschalten wird raw wieder zurückgesetzt: anders als ein manuell
        // gesetztes "Rohwerte" darf es hier nicht stehen bleiben, sonst
        // liefert der Server laut query_raw_series() weiterhin chart_type
        // "line" für JEDE Entität (Balken ergäben bei vielen Rohpunkten nur
        // eine Fläche) — sichtbar als plötzliche Linie statt der eigentlich
        // erwarteten automatischen Balken für Zähler/Schalter.
        toggleTimeline() {
          this.typeChosen = true;
          this.timeline = !this.timeline;
          if (this.timeline) { this.raw = true; }
          else { this.raw = false; }
          this.load();
        },
        // "Diagrammtyp" (Optionen-Menü, oberste Zeile unter "Darstellung") —
        // Zeitverlauf (Linie/Balken je Serie), Zeitstrahl (nur wenn alle
        // Serien Schalter sind, dieselbe Auswahl wie auf der Entitätsseite)
        // oder Donut (renderDonut()). Donut braucht keine neue Serverabfrage,
        // solange keine Rohwerte geladen sind (wie toggleStacked()); Zeitstrahl
        // und der Rückweg aus ihm schon, denn er erzwingt Rohwerte (siehe
        // toggleTimeline() für die Begründung). Zeitstrahl und Donut sind
        // disabled, solange compare true ist (Vergleich hat Vorrang, siehe
        // setCompareMode()) — eine Vorperiode-Nebenserie ergibt dort keinen
        // sinnvoll darstellbaren zweiten Wert.
        setDisplayMode(mode) {
          const wasTimeline = this.timeline && !this.donut;
          const current = this.donut ? 'donut' : (this.hasState ? this.stateMode : (wasTimeline ? 'timeline' : 'timeseries'));
          // Auch ein Klick auf die schon gezeigte Ansicht ist eine Wahl: sie soll
          // den Zeitraumwechsel überstehen und wird mitgespeichert.
          if (this.hasState && ['timeline', 'shares', 'changes', 'heatmap'].includes(mode)) this.stateChosen = true;
          this.heatmapWanted = mode === 'heatmap';
          if (mode === current) return;
          this.typeChosen = true;
          // Zustände: Zeitstrahl braucht die Rohwechsel, Anteile und Wechsel
          // kommen ab dem Monat fertig vom Server — beides neu laden.
          if (this.hasState && ['timeline', 'shares', 'changes', 'heatmap'].includes(mode)) {
            this.donut = false;
            this.stateMode = mode;
            this.timeline = true;
            this.raw = mode === 'timeline';
            this.load();
            return;
          }
          this.donut = mode === 'donut';
          if (mode === 'donut') {
            // render() prüft this.timeline VOR this.donut — ein noch aktiver
            // Zeitstrahl bliebe sonst trotz gewähltem Donut sichtbar.
            this.timeline = false;
            // renderDonut() summiert/mittelt/liest den letzten Wert der
            // bereits geladenen Punkte direkt — bei Rohwerten (raw=true)
            // wären das bei Zählern kumulierte Zählerstände statt Bucket-
            // Deltas, ihre Summe damit bedeutungslos. load() statt render(),
            // falls raw noch aktiv war: this.series enthält dann noch die
            // alten Rohpunkte, nicht die passenden Bucket-Werte.
            if (this.raw) { this.raw = false; this.load(); return; }
          } else if (mode === 'timeline') {
            this.timeline = true;
            this.raw = true;
            this.load();
            return;
          } else if (this.hasState) {
            // Zurück zum Zeitverlauf: bei Zuständen ist das der Zeitstrahl
            // (Rohübergänge). Ein bloßes render() fiele auf Linie/Balken über
            // Zustandsindizes zurück und bliebe leer — load() setzt
            // timeline/raw selbst (siehe dort) und holt die Rohdaten neu.
            this.load();
            return;
          } else if (wasTimeline) {
            this.timeline = false;
            this.raw = false;
            this.load();
            return;
          }
          this.render();
        },

        async load() {
          // Ausgeblendete Serien werden gar nicht erst geholt — sie sollen
          // weder gezeichnet noch in der Statistik gezählt werden, und eine
          // Abfrage für Daten, die niemand sieht, wäre reine Last.
          if (this.visibleEntityIds.length === 0) {
            this.series = [];
            this.autoResolutionLabel = '';
            this.$nextTick(() => this.render());
            return;
          }
          // Zweite, robustere Absicherung neben setRange()/toggleTimeline():
          // Zeitstrahl braucht IMMER echte Rohübergänge, nie Bucket-Werte,
          // unabhängig davon, über welchen Code-Pfad load() gerade ausgelöst
          // wurde — Bucket-Werte würde renderTimelineMulti() sonst fälschlich
          // als durchgehende AN-Intervalle statt echter Übergänge zeichnen.
          if (this.timeline) this.raw = !this.wantsStateBars;
          // "Langzeit" gibt es nur bei Zeitraum "Jahr", kalendarisch und mit
          // genau einer Entität. Ändert sich eins davon (Zeitraum, Rollierend,
          // Entitätenauswahl — alles endet hier in load()), fällt der Vergleich
          // auf "Vorjahr" zurück, statt einen nicht mehr möglichen Modus
          // weiterzutragen. Ein Ort statt je einer Prüfung in jedem Umschalter.
          if (this.compare && this.compareMode === 'longterm' && !this.longtermAvailable) this.compareMode = 'previous';
          const requestId = ++this._requestId;
          this.loading = true;
          const params = new URLSearchParams({
            entity_ids: this.visibleEntityIds.join(','),
            range: this.range, offset: String(this.offset), continuous: String(this.continuous),
            compare: String(this.compare), compare_mode: this.compareMode,
            raw: String(this.raw),
          });
          if (this.multiYearRequested) params.set('previous_years', String(this.compareYears));
          // Heatmap: zusätzlich die Zellen (Wochentag x Stunde); Legende und
          // Kennzahlen kommen weiter aus den Anteilen derselben Abfrage.
          const heatmapParams = new URLSearchParams(params);
          heatmapParams.set('heatmap', 'true');
          heatmapParams.set('compare', 'false');
          const wantHeatmap = this.stateMode === 'heatmap' && this.hasStateOrUnknown;
          // Zeitstrahl der Zustände in Jahr/Dekade: zusätzlich Spalten vom Server, so breit
          // wie der Chart. Sie ersetzen die Rohabschnitte, wenn deren Abfrage über der
          // Grenze abbricht (sonst bleibt die Verdichtung im Browser exakt).
          const wantColumns = this.stateMode === 'timeline' && this.raw && this.hasStateOrUnknown
            && StateEntities.COLUMN_RANGES.includes(this.range);
          const columnsParams = new URLSearchParams(params);
          columnsParams.set('columns', 'true');
          columnsParams.set('compare', 'false');
          const chartEl = document.getElementById('chart');
          columnsParams.set('px', String(Math.max(60, Math.round((chartEl ? chartEl.clientWidth : 600) - 30))));
          const [res, heatmapRes, columnsRes] = await Promise.all([
            fetch(`${BASE}/api/query-multi?${params}`),
            wantHeatmap ? fetch(`${BASE}/api/query-multi?${heatmapParams}`) : Promise.resolve(null),
            wantColumns ? fetch(`${BASE}/api/query-multi?${columnsParams}`) : Promise.resolve(null),
          ]);
          const columnsData = columnsRes && columnsRes.ok ? await columnsRes.json() : null;
          // Die Rohabfrage bricht bei zu vielen Wechseln mit einem Fehler ab: dann gilt die
          // Spaltenantwort (Fenster, Zustandsliste und Spalten je Zustands-Entität).
          const data = res.ok ? await res.json() : (columnsData || {});
          const heatmapData = heatmapRes ? await heatmapRes.json() : null;
          if (requestId !== this._requestId) return;
          if (heatmapData) {
            const cellsById = new Map((heatmapData.series || []).map(x => [x.entity_id, x.cells]));
            for (const entry of data.series || []) if (cellsById.get(entry.entity_id)) entry.cells = cellsById.get(entry.entity_id);
          }
          if (columnsData && data !== columnsData) {
            const colsById = new Map((columnsData.series || []).filter(x => x.columns).map(x => [x.entity_id, x]));
            for (const entry of data.series || []) {
              const cols = colsById.get(entry.entity_id);
              if (cols) { entry.columns = cols.columns; entry.dt = cols.dt; }
            }
          }
          // Zustände lassen sich nur unter sich darstellen, auch nicht mit
          // Schaltern (ROADMAP 1.29): sobald etwas anderes im Chart ist, fallen
          // die Zustände heraus und ein Hinweis nennt sie, statt Indizes als
          // Linie zu zeichnen.
          const loaded = data.series || [];
          const foreign = loaded.some(s => s.aggregation_type && s.aggregation_type !== 'state');
          this.droppedStateIds = foreign ? loaded.filter(s => s.aggregation_type === 'state').map(s => s.entity_id) : [];
          this.series = loaded.filter(s => !this.droppedStateIds.includes(s.entity_id));
          // Entitäten-Auswahl kann sich geändert haben, seit "Zeitstrahl"
          // aktiviert wurde (z. B. eine Nicht-Schalter-Entität ergänzt) —
          // dann fällt die Ansicht automatisch auf Linie/Balken zurück statt
          // einen inzwischen unpassenden Zeitstrahl weiterzuzeigen.
          if (this.timeline && !this.allSwitch) this.timeline = false;
          // Anteile/Wechsel gibt es nur für Zustände: war das Chart anders
          // gespeichert (andere Entitäten), zurück zum Zeitstrahl — mit Rohwerten.
          if (!this.hasState && this.stateMode !== 'timeline') {
            this.stateMode = 'timeline';
            if (this.timeline && !this.raw) {
              this.raw = true;
              this.loading = false;
              return this.load();
            }
          }
          // Mit Zustands-Entitäten gibt es außer dem Donut nur den Zeitstrahl.
          if (this.hasState && !this.timeline && !this.donut) {
            this.timeline = true;
            this.raw = this.stateMode === 'timeline';
            this.loading = false;
            return this.load();
          }
          // Standard für reine Schalter-Charts: Zeitstrahl. Die Serien kamen
          // ohne Rohwerte, deshalb einmal neu laden.
          if (!this.typeChosen && !this.timeline && !this.donut && this.allSwitch) {
            this.typeChosen = true;
            this.timeline = true;
            this.raw = true;
            this.loading = false;
            return this.load();
          }
          this.autoResolutionLabel = this.computeAutoResolutionLabel();
          this.windowStart = data.window_start ?? null;
          this.windowEnd = data.window_end ?? null;
          this.periodEnd = data.period_end ?? null;
          this.isCurrent = data.is_current ?? true;
          this.loading = false;
          this.$nextTick(() => this.render());
        },

        render() {
          if (!chartInstance) chartInstance = echarts.init(document.getElementById('chart'));
          if (!this.hasData) { chartInstance.clear(); return; }
          // Nur die Zustands-Balken brauchen mehr Höhe (ein Diagramm je Entität).
          if (!(this.timeline && this.hasState && this.stateMode !== 'timeline')) document.getElementById('chart').style.height = '';
          // Vorjahresvergleich bei range='year' MIT Soll-Entität ODER
          // Laufsumme bekommt einen eigenen Renderpfad wie Donut/Zeitstrahl
          // (siehe timeline/donut-Zweige unten) statt eines weiteren Zweigs
          // in der allgemeinen Zeitachsen-Logik hier — Stacking, Prozent-
          // Normierung, gleitender Durchschnitt und singleBucket setzen alle
          // GENAU EINE Serie je Entität voraus, dieser Pfad macht daraus zwei
          // gleichwertige (aktuelles Jahr + Vorjahr als Balkenpaare statt der
          // gewohnten Schattenlinie), gebucketet auf echte Kalendermonate
          // (monthlyBuckets()) statt der Bucket-Sekunden-Logik unten — genau
          // das braucht auch die Laufsumme (cumulate()), unabhängig von Soll.
          // OHNE beides bleibt "Vorjahr" für JEDEN Zeitraum — auch 'year' —
          // die gewohnte Schattenlinie unten in render().
          // Mehrjahresvergleich ("Langzeit") vor allem anderen. yearsCompareActive
          // verlangt compareMode 'previous' und kann daneben gar nicht wahr
          // sein; die Reihenfolge sichert es zusätzlich ab.
          if (this.multiYearActive) {
            this.renderMultiYear();
            return;
          }
          if (this.yearsCompareActive) {
            this.renderYearsCompare();
            return;
          }
          // Aus den TATSÄCHLICH ANGEZEIGTEN (resamplePoints()-) Punkten
          // ermittelt, nicht aus den rohen Server-Punkten wie
          // computeAutoResolutionLabel(): bei manuell gesetzter Auflösung
          // (medium/coarse) resampelt resamplePoints() clientseitig gröber,
          // die rohen Punkte wären dann feiner als das, was im Tooltip
          // tatsächlich zu sehen ist (z. B. "00:00" statt "Mo, 04.08.2026"
          // bei Tages-Buckets aus stündlichen Rohpunkten). Erst innerhalb der
          // Schleife unten gesetzt (dort liegen die resampelten mainPoints
          // vor), hier nur deklariert.
          let tooltipBucketSeconds = null;
          // Gemeinsamer Werte-Formatter für Tooltip UND "Werte anzeigen"-Labels
          // (data[1]=Wert, data[3]=Einheit, data[4]=Nachkommastellen, data[5]=
          // Dauer-Anzeige) — dieselben Indizes wie beim Aufbau von mainData/
          // compareData unten. noUnit für die Labels über den Balken/Punkten —
          // dort reicht die reine Zahl, die Einheit steht schon an der Y-Achse.
          // data[6] (nur bei 100%-Normierung gesetzt, s. mainData unten) wird
          // hier NICHT gelesen — der Original-Absolutwert erscheint nur im
          // Tooltip, dort eigens angehängt (siehe row() weiter unten).
          const formatPointValue = (data, noUnit) => {
            const [, value, , unit, decimals, isDuration] = data;
            return isDuration ? NumberFormat.fmtDuration(value) : (unit && !noUnit ? `${fmtNum(value, decimals)} ${unit}` : fmtNum(value, decimals));
          };
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

          if (this.timeline && this.hasState && this.stateMode === 'heatmap') {
            this.renderStateHeatmapPanels();
            return;
          }
          if (this.timeline && this.hasState && this.stateMode !== 'timeline') {
            this.renderStateBarPanels();
            return;
          }
          if (this.timeline) {
            this.renderTimelineMulti(fmt);
            return;
          }
          if (this.donut) {
            this.renderDonut();
            return;
          }

          // Auflösung "Voll" (Tag/Woche/Monat/Jahr — RESOLUTION_LABELS[range].
          // full) bucketet den kompletten Zeitraum zu genau EINEM Wert je
          // Entität — ein Ranking-Vergleich, kein Zeitverlauf. Jede Entität
          // bekommt dafür eine EIGENE Kategorie auf der Achse (ihr
          // Anzeigename), statt auf der sonst üblichen Zeit-Achse einen
          // einzelnen, trotz barMaxWidth-Deckelung winzigen Balken exakt auf
          // windowStart zu zeigen. Eine Kategorie-Achse mit einer Kategorie
          // je Entität liest sich außerdem als Ranking natürlicher als
          // mehrere in eine Kategorie gruppierte Balken.
          //
          // "Gestapelt" schließt diese Auflösung aus (canStack-Getter) —
          // sonst gäbe es zwei widersprüchliche Antworten auf "wie viele
          // Kategorien": eine gestapelte Kombination will ALLE Entitäten in
          // EINER Kategorie, der Ranking-Vergleich hier will das Gegenteil.
          const singleBucket = this.singleBucket;
          const horizontalActive = singleBucket && this.horizontal;
          // Anzeigename je Entität, in Auswahlreihenfolge — dieselbe Quelle
          // wie die Legende (this.entityNames[entity_id] || friendly_name),
          // damit Kategorie-Beschriftung und Legende nie auseinanderlaufen.
          const entityCategories = this.series.map(s => this.entityNames[s.entity_id] || s.friendly_name);

          // Eine Y-Achse je unterschiedlicher Einheit (wie die Wachstums-Chart auf
          // der Statistik-Seite mit zwei Achsen, hier verallgemeinert auf N) —
          // wechselseitig links/rechts, weitere Achsen pro Seite nach außen versetzt.
          // Schalter-Entitäten im Anzeigemodus "Zeit" (Dauer statt Rohsekunden)
          // bekommen dafür einen eigenen synthetischen Achsen-Schlüssel statt
          // ihrer echten (meist leeren) unit — sonst würden sie sich fälschlich
          // eine Achse mit unitlosen Standard-Entitäten teilen und in Rohsekunden
          // statt als Dauer beschriftet.
          const isDurationSeries = s => s.aggregation_type === 'switch' && s.display_mode === 'time';
          const axisKey = s => isDurationSeries(s) ? ' duration' : s.unit;
          const units = [...new Set(this.series.map(axisKey))];
          // Balken-Serien stapeln je Einheit statt in einem globalen "total"-
          // Topf — sonst würden z. B. kWh- und Dauer-Serien auf derselben
          // Achse zusammenaddiert, sobald ein Chart mehrere Einheiten
          // kombiniert (siehe yAxis unten, eine Achse je axisKey).
          // stackedActive/normalizeActive gelten deshalb nicht global,
          // sondern je Achse.
          const stackedActive = this.stacked && this.canStack;
          const normalizeActive = stackedActive && this.normalize;
          // Nur Achsen, auf denen AUSSCHLIESSLICH Balken-Serien liegen, dürfen
          // auf Prozent umgestellt werden — eine mitgezeichnete Linien-Serie
          // derselben Einheit stünde sonst auf derselben 0–100%-Skala, die für
          // die gestapelten Balken gemeint ist, und zeigte ihre eigenen (nicht
          // normierten) Werte dadurch verzerrt.
          const barOnlyAxis = new Set(
            units.filter(u => this.series.every(s => axisKey(s) !== u || s.chart_type === 'bar'))
          );
          // Achsen mit MINDESTENS einer Balken-Serie (nicht nur reine
          // Balken-Achsen wie barOnlyAxis oben) dürfen "Dynamische Y-Achse"
          // nie übernehmen — sonst startet die Achse nicht bei 0 und
          // Balkenhöhen wirken optisch verzerrt (ein Balken, der nur "ein
          // bisschen kürzer" aussieht, kann bei einer bei 60 statt 0
          // beginnenden Achse tatsächlich nur ein Zehntel des Werts sein).
          // Dieselbe Regel gilt bereits für Dashboard-Kacheln
          // (dashboard-tiles.js, axisHasBar) und Einzel-Entitäts-Charts
          // (entity_detail.js, chartType !== 'bar') — hier bisher gefehlt.
          const axisHasBar = new Set(
            units.filter(u => this.series.some(s => axisKey(s) === u && s.chart_type === 'bar'))
          );
          // Resampelte Punkte je Serie einmal vorab berechnen (statt weiter
          // unten im Haupt-Loop) — die Prozent-Normierung braucht die Werte
          // ALLER Serien einer Achse zum selben Bucket-Zeitpunkt, bevor die
          // erste Serie fertig gebaut werden kann.
          const preparedPoints = this.series.map(s => resamplePoints(
            s.points, this.range, this.resolutionPreset, s.aggregation_type, this.windowStart
          ));
          // ts -> Summe je Achse, nur für tatsächlich normierte reine
          // Balken-Achsen.
          const axisTotals = new Map();
          if (normalizeActive) {
            this.series.forEach((s, i) => {
              const u = axisKey(s);
              if (!barOnlyAxis.has(u)) return;
              if (!axisTotals.has(u)) axisTotals.set(u, new Map());
              const totals = axisTotals.get(u);
              preparedPoints[i].forEach(p => totals.set(p.ts, (totals.get(p.ts) || 0) + p.value));
            });
          }
          // Eine Achse kann mehrere Entitäten mit gleicher Einheit aber
          // unterschiedlicher Nachkommastellen-Einstellung bündeln — dann gilt
          // die "strengste" (kleinste) Rundung aller beteiligten Entitäten, damit
          // kein Wert unpassend abgeschnitten wirkt.
          const unitDecimals = new Map();
          this.series.forEach(s => {
            const d = this.effectiveDecimals(s);
            if (d == null) return;
            const current = unitDecimals.get(axisKey(s));
            if (current == null || d < current) unitDecimals.set(axisKey(s), d);
          });
          // singleBucket erzwingt eine bei 0 startende Y-Achse, unabhängig
          // vom gespeicherten Schalter-Zustand — bei einem bereits vor
          // diesem Fix gespeicherten Chart könnte sonst weiterhin
          // dynamic_y_axis:true UND resolution_preset:"full" gemeinsam
          // vorliegen (siehe onResolutionChange()/Optionen-Menü, die das nur
          // clientseitig ab jetzt verhindern).
          const dynamicYAxis = this.dynamicYAxis && !singleBucket;
          const yAxis = units.map((u, i) => {
            const decimals = unitDecimals.get(u);
            const isDuration = u === ' duration';
            // Prozent-Achse (100%-Normierung, Optionen-Menü "Anteile (%)") —
            // immer fest 0–100, unabhängig von "Dynamische Y-Achse": eine
            // Anteils-Achse, die nicht bei 0 beginnt oder über 100 hinausgeht,
            // würde die Prozentwerte selbst verzerrt darstellen.
            const isPercentAxis = normalizeActive && barOnlyAxis.has(u);
            const axisDynamic = dynamicYAxis && !axisHasBar.has(u);
            return {
              type: 'value',
              name: isPercentAxis ? t('Anteil') : (isDuration ? t('Dauer') : (u || undefined)),
              nameLocation: 'end',
              position: i % 2 === 0 ? 'left' : 'right',
              offset: Math.floor(i / 2) * 55,
              min: isPercentAxis ? 0 : (axisDynamic ? undefined : value => Math.min(0, value.min)),
              max: isPercentAxis ? 100 : (axisDynamic ? undefined : value => Math.max(0, value.max)),
              // ECharts erzwingt bei einer value-Achse standardmäßig (scale:
              // false) IMMER die Einbindung der Null, auch wenn min/max
              // undefined sind — ohne scale:true hätte "Dynamische Y-Achse"
              // also keine sichtbare Wirkung gegenüber der festen Variante.
              scale: isPercentAxis ? false : axisDynamic,
              axisLabel: {formatter: v => isPercentAxis ? `${fmtNum(v, 0)} %` : (isDuration ? NumberFormat.fmtDuration(v) : (u ? `${fmtNum(v, decimals)} ${u}` : fmtNum(v, decimals)))},
            };
          });
          // "Ausrichtung: Horizontal" tauscht Kategorie- und Werte-Achse —
          // dieselben Werte-Achsen-Konfigurationen wie yAxis oben (inklusive
          // Prozent-Achse, Dauer, Dynamische Y-Achse), nur mit xAxis-
          // typischen Positionen (oben/unten statt links/rechts). Bewusst
          // ein reines position-Remapping statt einer zweiten, eigenen
          // Achsen-Berechnung — sonst müssten Prozent-/Dauer-/Dynamische-
          // Y-Achse-Logik an zwei Stellen synchron gehalten werden.
          const xAxisForHorizontal = horizontalActive
            ? yAxis.map((axis, i) => ({...axis, position: i % 2 === 0 ? 'bottom' : 'top'}))
            : null;
          // Kategorie-Achse für singleBucket — Entitätsnamen statt Zeit-
          // Ticks. rotate/width nur relevant, wenn sie tatsächlich als
          // x-Achse dient (vertikale Balken): horizontal liest sich die
          // volle Beschriftung ohnehin unrotiert von links nach rechts
          // (s. yAxis unten), rotate:0 dort ist deshalb kein Sonderfall,
          // sondern derselbe Ausdruck wie für die x-Achsen-Rolle.
          const categoryAxisObj = singleBucket ? {
            type: 'category',
            data: entityCategories,
            axisTick: {show: false},
            // Ohne dies versucht ECharts (Default: onZero:true), die Achsen-
            // Linie an der Y-Position von y=0 auszurichten — bei aktiver
            // "Dynamischer Y-Achse" (Y-Achse startet dann NICHT bei 0,
            // sondern knapp unter dem kleinsten Wert) landet diese
            // Ausrichtung mitten in den Balken statt am unteren Rand, sie
            // wirken dadurch "versenkt". false verankert die Achse immer
            // am unteren/linken Diagrammrand, unabhängig vom Werte-Minimum.
            axisLine: {onZero: false},
            axisLabel: {
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              width: horizontalActive ? 190 : 90,
              overflow: 'truncate',
              rotate: horizontalActive ? 0 : 28,
            },
          } : null;
          // Feste Farbe je Entität (statt ECharts' Auto-Zuordnung) — nur so lässt
          // sich bei aktivem Vergleich die Vorperiode-Serie einer Entität optisch
          // eindeutig ihrer Hauptserie zuordnen (dieselbe Farbe, nur blasser +
          // gestrichelt), auch wenn mehrere Entitäten gleichzeitig überlagert sind.
          // Balken statt Linie für Zähler/Schalter — dieselbe Regel wie auf der
          // Entität-eigenen Chart-Seite (entity_detail.html, DEFAULT_CHART_TYPE):
          // Zähler zeigen Bucket-Summen, ein Schalter eine Einschaltdauer je
          // Bucket — beides liest sich als Balken natürlicher als als Linie.
          const echartsSeries = [];
          // Für legend.selected unten — nur die Hauptserie ist über die
          // eigene Legende (siehe HTML-Template) umschaltbar, nicht die
          // Vergleichs-Nebenserie (die folgt dem "Vergleichen"-Menü als
          // Ganzes, siehe setCompareMode()/disableCompare()).
          const legendSelected = {};
          this.series.forEach((s, i) => {
            const color = this.colorFor(s.entity_id);
            // chart_type kommt vom Server (query.py: "bar" für Zähler/Schalter,
            // sonst "line", im Raw-Modus immer "line") statt hier redundant aus
            // aggregation_type neu abgeleitet zu werden — sonst würde z. B. ein
            // Zähler im Raw-Modus fälschlich weiter als Balken gerendert, obwohl
            // der Server für Rohwerte (ungebuckelte Einzelmesswerte) immer Linie
            // liefert.
            const chartType = s.chart_type;
            const seriesDecimals = this.effectiveDecimals(s);
            const displayName = this.entityNames[s.entity_id] || s.friendly_name;
            legendSelected[displayName] = !this.legendHiddenIds.includes(s.entity_id);
            const mainPoints = preparedPoints[i];
            if (tooltipBucketSeconds == null) {
              tooltipBucketSeconds = detectResolutionSeconds(mainPoints);
            }
            // 100%-Normierung (Optionen-Menü, "Anteile (%)") nur auf reinen
            // Balken-Achsen (barOnlyAxis, s. o.) und nur für Balken-Serien —
            // eine überlagerte Linie derselben Einheit bliebe unverändert in
            // Absolutwerten (axisNormalized bleibt dann false für sie, weil
            // ihre Achse nicht in barOnlyAxis steht).
            const axisNormalized = normalizeActive && chartType === 'bar' && barOnlyAxis.has(axisKey(s));
            // Bei singleBucket (Kategorie-Achse, s. o.) ist die X-Position die
            // EIGENE Kategorie dieser Entität (ihre Position in this.series,
            // s. entityCategories) statt eines Zeitstempels — der Halte-Punkt
            // bis windowEnd (nächster Block) ergibt bei einer einzelnen
            // Kategorie je Entität ohnehin keinen Sinn und entfällt deshalb
            // hier.
            const mainData = mainPoints.map(p => {
              if (!axisNormalized) return [singleBucket ? i : p.ts * 1000, p.value, p.ts * 1000, s.unit, seriesDecimals, isDurationSeries(s)];
              // Felder 7–9: Originalwert/-einheit/-Nachkommastellen, nur für
              // den Tooltip (formatPointValue() liest sie nicht, siehe dort)
              // — sonst verliert der Tooltip genau die Zahl, die die
              // Normierung aus dem Balken selbst entfernt.
              const total = axisTotals.get(axisKey(s)).get(p.ts) || 0;
              const pct = total ? (p.value / total) * 100 : 0;
              return [singleBucket ? i : p.ts * 1000, pct, p.ts * 1000, '%', 0, false, p.value, s.unit, seriesDecimals];
            });
            if (!singleBucket && chartType === 'line' && mainData.length && this.windowEnd != null
                && mainData[mainData.length - 1][0] < this.windowEnd * 1000) {
              const last = mainData[mainData.length - 1];
              mainData.push([this.windowEnd * 1000, last[1], last[2], last[3], last[4], last[5]]);
            }
            // Gestapelt nur für Balken-Serien und je Achse ein eigener Stapel-
            // Schlüssel (nicht ein globales "total") — sonst würden Serien
            // unterschiedlicher Einheit (z. B. kWh und Dauer) fälschlich in
            // denselben Balken aufsummiert, sobald ein Chart mehrere Achsen
            // kombiniert.
            const isStackedBar = chartType === 'bar' && stackedActive;
            const unitIndex = units.indexOf(axisKey(s));
            // Gleitender Durchschnitt (Optionen-Menü, "Durchschnittslinie" →
            // "Gleitend") nur für Linien-Serien — bei einer Balken-Serie
            // (Bucket-SUMME) ergäbe ein "gleitender Durchschnitt der Summen"
            // keine klar lesbare Aussage, deshalb dort schlicht keine
            // Zusatzlinie statt einer verwirrenden.
            const showRollingAverage = this.averageLine && !isStackedBar && this.averageStyle === 'rolling'
              && chartType === 'line' && mainPoints.length >= 3;
            const main = {
              name: displayName,
              type: chartType,
              // Horizontal tauschen Kategorie- und Werte-Achse die Plätze
              // (s. xAxisForHorizontal oben) — die Serie muss dann ihre
              // Werte-Achse über xAxisIndex ansprechen und die (einzige)
              // Kategorie-Achse über yAxisIndex:0, statt umgekehrt.
              xAxisIndex: horizontalActive ? unitIndex : undefined,
              yAxisIndex: horizontalActive ? 0 : unitIndex,
              data: mainData,
              // mainData bleibt IMMER [Kategorie-Index, Wert, ...] — ohne
              // encode würde ECharts das Tupel positionell als [x, y]
              // lesen, was bei getauschten Achsen (horizontalActive) die
              // Kategorie als Werte-Achsen-Position UND den Wert als
              // Kategorie-Index missverstehen würde (Balken verschwinden,
              // Werte-Achse skaliert auf die Kategorie-Indizes 0/1/2 statt
              // auf die echten Werte — so gefunden). encode sagt ECharts
              // stattdessen explizit, welche Tupel-Position zu welcher
              // Achse gehört, unabhängig von deren Reihenfolge im Array —
              // Tooltip/Label-Formatter lesen ohnehin per festem Index
              // (data[1]=Wert), bleiben also unverändert korrekt.
              encode: horizontalActive ? {x: 1, y: 0} : undefined,
              stack: isStackedBar ? 'bar-' + axisKey(s) : undefined,
              // Bei aktiver Trendlinie tritt die rohe (verrauschte) Kurve
              // zurück, bleibt aber sichtbar — die Trendlinie ist die
              // Ergänzung, nicht der Ersatz.
              lineStyle: {width: 1.5, color, opacity: showRollingAverage ? 0.45 : 1},
              itemStyle: {color},
              // "Werte anzeigen" (Optionen-Menü) — Zahl direkt über jedem Balken/
              // Punkt, zusätzlich zum Tooltip. Nur die Hauptserie, nicht die
              // Vergleichs-Nebenserie (siehe cmp weiter unten) — sonst überlagern
              // sich bei aktivem Vergleich zwei Beschriftungen je Zeitpunkt.
              // Gestapelt liegt das Label INNERHALB des Segments (weiß) statt
              // darüber — "darüber" wäre bei gestapelten Segmenten meist ein
              // fremdes Segment oder Whitespace, nicht das eigene.
              label: {
                show: this.showValues,
                position: isStackedBar ? 'inside' : (horizontalActive ? 'right' : 'top'),
                fontSize: Math.round(10.5 * UI_FONT_SCALE * 10) / 10,
                color: isStackedBar ? '#fff' : getComputedStyle(document.body).getPropertyValue('--ink-muted'),
                formatter: params => formatPointValue(params.data, true),
              },
              // Deckelt die Balkenbreite auf einer Zeit-Achse — ohne diese
              // fehlt ECharts bei sehr wenigen Punkten (z. B. "Jahr" einer
              // gerade erst begonnenen Entität, ein einziger Balken) der
              // Bezugspunkt für eine sinnvolle Auto-Breite, wodurch der Balken
              // einen Großteil der (bewusst bis zum Fensterende reichenden)
              // Achse einnehmen kann. Bei vielen Balken liegt die Auto-Breite
              // ohnehin längst unter dem Limit. Gilt jetzt auch für
              // singleBucket: seit jede Entität ihre eigene Kategorie hat
              // (statt mehrerer Balken in einer gemeinsamen Kategorie), ist
              // das genau der Normalfall eines Kategorie-Achsen-Balkens, kein
              // Sonderfall mehr.
              barMaxWidth: 48,
              // Ein Balken auf einer Zeit-Achse (kein boundaryGap, s. o.)
              // sitzt mit seiner Mitte GENAU auf dem Bucket-Zeitstempel —
              // beim ersten/letzten Bucket liegt die Hälfte der Balkenbreite
              // dadurch zwangsläufig knapp jenseits von min/max und würde
              // ohne dies hart am Diagrammrand abgeschnitten wirken. Bei
              // singleBucket (echte Kategorie-Achse mit eigener Bandbreite je
              // Kategorie, kein boundaryGap-Sonderfall) besteht dieses
              // Problem nicht.
              clip: singleBucket ? undefined : chartType !== 'bar',
            };
            if (chartType === 'line') {
              main.smooth = true;
              // symbol nie explizit auf undefined setzen (nur weglassen oder auf
              // 'none') — siehe entity_detail.html, ECharts' interne Options-
              // Normalisierung stürzt sonst ab.
              if (!this.showPoints) main.symbol = 'none';
              // Dezente Füllfläche unter der Linie (Optionen-Menü, "Fläche") —
              // dieselbe Opacity wie auf der Dashboard-Kachel (dashboard-
              // tiles.js), damit ein angeheftetes Chart nicht anders aussieht
              // als auf seiner eigenen Seite.
              if (this.areaFill) main.areaStyle = {color, opacity: 0.08};
            }
            // Durchschnittslinie (Optionen-Menü, "Darstellung") — je Serie eine,
            // in deren eigener Farbe und auf deren eigener y-Achse
            // (yAxisIndex oben), sonst läge die Linie einer °C-Serie auf der
            // kWh-Skala.
            //
            // Gerechnet wird über mainPoints, also über das, was TATSÄCHLICH
            // gezeichnet wird — nicht über s.points wie die Legende. Bei
            // gewählter Auflösung fasst resamplePoints() Zähler und Schalter
            // per SUMME zusammen (Standard-Entitäten per Mittelwert); der
            // Durchschnitt der Rohpunkte läge dort um den Faktor der
            // Bucketbreite unter den gezeichneten Balken, die Linie klebte
            // sichtbar an der Nulllinie. Legende und Linie können dadurch bei
            // Zählern mit gewählter Auflösung verschiedene Zahlen nennen — sie
            // beantworten dann auch verschiedene Fragen.
            // Entschieden: im gestapelten Modus keine Durchschnittslinie —
            // eine Serie beginnt darin nicht mehr bei 0, ihr Durchschnitt
            // läge mitten in einem fremden Segment und würde als
            // Segmentgrenze missverstanden statt als Mittelwert erkannt
            // (siehe :disabled an der Menü-Zeile in chart_editor.html).
            const durchschnitt = this.averageLine && !isStackedBar && this.averageStyle === 'flat'
              ? averageOf(mainPoints.map(p => p.value))
              : null;
            if (durchschnitt !== null) {
              main.markLine = {
                silent: true,
                symbol: 'none',
                lineStyle: {color, type: 'dashed', width: 1},
                label: {
                  position: 'insideEndTop',
                  fontSize: Math.round(10.5 * UI_FONT_SCALE * 10) / 10,
                  color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
                  formatter: () => `Ø ${fmtNum(durchschnitt, seriesDecimals)}${s.unit ? ' ' + s.unit : ''}`,
                },
                // Horizontal ist die Werte-Achse die x-Achse (s. o.) — der
                // Durchschnitt liegt dann bei einem x-, nicht y-Wert.
                data: [horizontalActive ? {xAxis: durchschnitt} : {yAxis: durchschnitt}],
              };
            }
            echartsSeries.push(main);
            if (showRollingAverage) {
              const windowPoints = movingAverageWindow(mainPoints.length);
              const rollingData = movingAverage(mainPoints, windowPoints)
                .filter(p => p.value !== null)
                .map(p => [p.ts * 1000, p.value]);
              // Eigene, zusätzliche Serie statt eines markLine/Umbaus der
              // Hauptserie — dieselbe Technik wie die Vorperiode-Nebenserie
              // (cmp weiter unten): eine sichtbar mit der Hauptserie
              // verbundene, aber eigenständige Linie. Nicht über die Legende
              // einzeln umschaltbar (wie cmp auch nicht) — sie gehört
              // sichtbar zur Hauptserie, kein eigener Umschalt-Anspruch.
              echartsSeries.push({
                name: t('{name} (Ø gleitend)',{name: displayName}),
                type: 'line',
                yAxisIndex: units.indexOf(axisKey(s)),
                data: rollingData,
                smooth: true,
                symbol: 'none',
                lineStyle: {width: 2.5, color},
                z: 3,
              });
            }
            // stackedActive schließt Vergleich schon am Knopf aus
            // (toggleStacked()/:disabled in chart_editor.html) — hier
            // zusätzlich robust dagegen, falls compare aus einem älteren
            // Zustand (z. B. prefill) noch true wäre: eine Vorperiode-
            // Nebenserie ließe sich in einem gestapelten Balken nicht sinnvoll
            // einordnen (mitstapeln würde acht statt vier Segmente ergeben).
            if (this.compare && !stackedActive && s.compare_points && s.compare_points.length) {
              // Vorperiode um die exakte Fensterdifferenz verschieben, nicht per
              // Array-Index mappen — siehe derselbe Kommentar/Grund in
              // entity_detail.html (unterschiedlich lange Punktreihen).
              const shiftMs = (s.compare_window_start != null && this.windowStart != null)
                ? (this.windowStart - s.compare_window_start) * 1000
                : 0;
              const compareOffset = this.compareMode === 'year' ? 0 : -1;
              const compareLabel = formatPeriodLabel(
                this.range, this.continuous, s.compare_window_start, s.compare_window_end, false
              );
              const seriesLabel = this.compareMode === 'year' ? t('Vorjahr') : t('Vorperiode');
              const comparePoints = resamplePoints(
                s.compare_points, this.range, this.resolutionPreset,
                s.aggregation_type, s.compare_window_start
              );
              const compareData = comparePoints.map(p => [p.ts * 1000 + shiftMs, p.value, p.ts * 1000, s.unit, seriesDecimals, isDurationSeries(s)]);
              if (chartType === 'line' && compareData.length && this.windowEnd != null
                  && compareData[compareData.length - 1][0] < this.windowEnd * 1000) {
                const last = compareData[compareData.length - 1];
                compareData.push([this.windowEnd * 1000, last[1], last[2], last[3], last[4], last[5]]);
              }
              const cmp = {
                name: `${displayName} (${seriesLabel}${compareLabel ? ', ' + compareLabel : ''})`,
                type: chartType,
                yAxisIndex: units.indexOf(axisKey(s)),
                data: compareData,
                lineStyle: {width: 1.5, color: withOpacity(color, 0.5), type: 'dashed'},
                itemStyle: {color: withOpacity(color, 0.5)},
                barMaxWidth: 48,
                clip: chartType !== 'bar',
              };
              if (chartType === 'line') {
                cmp.smooth = true;
                if (!this.showPoints) cmp.symbol = 'none';
                // Niedrigere Deckkraft als die Hauptserie (main, s. o.) — die
                // Vergleichs-Nebenserie ist ohnehin schon gestrichelt/blasser
                // (withOpacity(color, 0.5) oben), zwei überlagerte Flächen in
                // gleicher Stärke würden sich sonst gegenseitig zumatschen.
                if (this.areaFill) cmp.areaStyle = {color, opacity: 0.05};
              }
              echartsSeries.push(cmp);
            }
          });
          padStackedSeries(echartsSeries);
          chartInstance.setOption({
            toolbox: toolboxOption(
              this.exportFilename,
              getComputedStyle(document.body).getPropertyValue('--surface'),
              getComputedStyle(document.body).getPropertyValue('--ink-faint')
            ),
            textStyle: {
              fontFamily: getComputedStyle(document.body).getPropertyValue('--font-mono'),
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * UI_FONT_SCALE * 10) / 10,
            },
            // bottom nur noch für die X-Achsen-Beschriftung reserviert — die
            // Legende lebt jetzt als eigenes HTML-Element unterhalb der Karte
            // (siehe legend weiter unten), nicht mehr im Chart selbst. top um
            // 8px erhöht (20→28), damit das toolbox-Icon (oben rechts, s. o.)
            // nicht mit der obersten Y-Achsen-Beschriftung kollidiert.
            // left 24 statt 10: containLabel misst die breiteste Beschriftung etwas zu knapp, bei langen
            // Werten (z. B. „25,000 ct/kWh“) fehlte links eine Ziffer (siehe entity_detail.js).
            grid: {left: 24, right: 10, top: 28, bottom: 40, containLabel: true},
            // min/max explizit auf das Abfragefenster fixiert, statt ECharts
            // per Default auf den tatsächlichen Datenbereich auto-fitten zu
            // lassen — sonst hört die Achse (und damit sichtbar der Chart) beim
            // letzten tatsächlichen Wert auf, z. B. bei einer Entität, die seit
            // Stunden nichts mehr gemeldet hat, statt konsistent bis zum
            // Fensterende (bei "Heute" also bis zur aktuellen Uhrzeit) zu reichen.
            xAxis: singleBucket ? (horizontalActive ? xAxisForHorizontal : categoryAxisObj) : {
              type: 'time',
              min: axisMinForBars(echartsSeries, this.windowStart != null ? this.windowStart * 1000 : null),
              // periodEnd statt windowEnd: eine laufende Periode (z. B. Woche)
              // zeigt so bis zur vollen Kalendergrenze (Sonntag), auch für die
              // noch datenlose Zukunft — windowEnd (an "jetzt" gedeckelt) bleibt
              // nur für den Linien-Haltepunkt maßgeblich (siehe periodEnd oben).
              // Eine Sekunde zurück, da periodEnd/windowEnd EXKLUSIV sind — sonst
              // reicht die Achse sichtbar bis zum Beginn der nächsten Periode (ein
              // Achsen-Tick "01.09." für einen Monat, der am 31.08. endet).
              max: (this.periodEnd ?? this.windowEnd) != null ? (this.periodEnd ?? this.windowEnd) * 1000 - 1000 : undefined,
              // ECharts polstert eine Zeit-Achse mit Balken-Serie sonst zusätzlich
              // über min/max hinaus (sichtbar z. B. bei "Jahr"/"Dekade" mit nur
              // einem Bucket) — boundaryGap:[0,0] unterbindet dieses Auto-Polster,
              // min/max bleiben dadurch die tatsächlichen Achsengrenzen.
              boundaryGap: [0, 0],
              // Woche/Monat bucketen tagesweise (siehe fmt() oben, "dd.mm."-
              // Beschriftung) — mit fest erzwungenem Tages-Interval statt
              // ECharts' automatischer "nice tick"-Berechnung, die trotz
              // explizitem max (s. o.) gelegentlich einen zusätzlichen Tick
              // GITTERNETZ (nicht nur Label) jenseits der Periodengrenze
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
                // 31.08. endet). periodEnd/windowEnd (EXKLUSIV, Beginn der
                // Folgeperiode) ist die Grenze, ab der die Beschriftung
                // unterdrückt wird.
                formatter: v => {
                  const rawEnd = this.periodEnd ?? this.windowEnd;
                  return (rawEnd != null && v >= rawEnd * 1000) ? '' : fmt(v / 1000);
                },
                hideOverlap: true,
              },
            },
            yAxis: horizontalActive ? {...categoryAxisObj, inverse: true} : yAxis,
            // ECharts' eigene Legende ist unsichtbar (show:false) — die
            // sichtbare Legende ist das eigene HTML-Element unterhalb der
            // Karte (siehe Template, .chart-legend), das gleichzeitig Serien-
            // Umschalter UND Min/Max/Ø-Anzeige ist. legendToggleSelect-
            // Actions (toggleLegendItem()) wirken trotzdem auf diese
            // unsichtbare Legendeninstanz, deshalb braucht es sie weiterhin
            // (nur eben nicht gerendert) — data explizit, sonst kennt sie die
            // Vergleichs-Nebenserien nicht, deren Namen nicht in selected
            // vorkommen.
            legend: {show: false, data: echartsSeries.map(s => s.name), selected: legendSelected},
            tooltip: {
              trigger: 'axis',
              // Eigener Formatter statt valueFormatter, aus demselben Grund wie in
              // entity_detail.html: bei aktivem Vergleich hat jede Zeile ihre eigene
              // tatsächliche Zeit im dritten Datenfeld, nicht die (bei der
              // Vorperiode verschobene) Achsenposition. Ohne Vergleich (der
              // Normalfall) teilen sich alle Serien denselben Zeitpunkt — dann
              // eine gemeinsame Kopfzeile statt das Datum bei jeder Serie zu
              // wiederholen; nur wenn die Zeiten tatsächlich auseinanderfallen
              // (Vergleich aktiv), bekommt jede Zeile ihr eigenes Datum, aber
              // weiterhin einheitlich ÜBER dem Wert (wie überall sonst in der
              // App, siehe dashboard-tiles.js/entity_detail.html).
              formatter: allParams => {
                // Auffüll-Punkte gestapelter Serien (padStackedSeries) haben keinen Wert.
                const params = allParams.filter(p => p.data[1] != null);
                if (!params.length) return '';
                const times = params.map(p => fmtTooltipTimestamp(p.data[2], tooltipBucketSeconds));
                const sameTime = times.every(t => t === times[0]);
                // 100%-Normierung: zeigt IMMER beides — Anteil und
                // Original-Absolutwert in Klammern (data[6]/[7]/[8], siehe
                // mainData oben) — sonst verliert der Tooltip genau die Zahl,
                // die die Normierung aus dem Balken selbst entfernt.
                const row = p => {
                  const [, , , , , , absValue, absUnit, absDecimals] = p.data;
                  const extra = absValue != null
                    ? ` <span style="color:var(--ink-faint);">(${fmtNum(absValue, absDecimals)}${absUnit ? ' ' + absUnit : ''})</span>`
                    : '';
                  return `<div style="display:flex;justify-content:space-between;gap:18px;margin-bottom:4px;">`
                       + `<span>${p.marker}${p.seriesName}</span>`
                       + `<strong style="margin-left:8px;">${formatPointValue(p.data)}${extra}</strong></div>`;
                };
                if (sameTime) {
                  return `<div style="font-size:calc(11px * var(--font-scale, 1));color:var(--ink-faint);margin-bottom:4px;">${times[0]}</div>`
                       + params.map(row).join('');
                }
                return params.map((p, i) =>
                  `<div style="font-size:calc(11px * var(--font-scale, 1));color:var(--ink-faint);margin-bottom:2px;">${times[i]}</div>${row(p)}`
                ).join('');
              },
              appendToBody: true,
            },
            series: echartsSeries,
          }, true);
          chartInstance.resize();
        },

        // Mehrspuriger Zeitstrahl — eine Kategorie-Zeile je Entität, sonst
        // dieselbe Balken-Technik wie renderTimeline() auf der Entität-
        // eigenen Chart-Seite (entity_detail.html), hier nur auf N Zeilen
        // statt einer verallgemeinert. Nutzt bewusst die rohen, nicht
        // resampelten Punkte jeder Serie — Zeitstrahl zeigt AN/AUS-
        // Übergänge, kein gebuckeltes Resampling ergäbe hier Sinn.
        renderTimelineMulti(fmt) {
          const categories = this.series.map(s => this.entityNames[s.entity_id] || s.friendly_name);
          const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
          const data = [];
          const pattern = StateEntities.unavailablePattern();
          // Zustände: ein Band je Abschnitt, Farbe nach Zustand — jede Zeile mit ihrer
          // eigenen Farbfolge ab --chart-1 (ROADMAP 1.29), also dieselben Farben wie auf
          // der Entitätsseite. Fünftes/sechstes Element: Zustandsindex und Index der
          // Schichten einer verdichteten Spalte (-1 = ein einzelner Abschnitt). Wo mehr
          // Abschnitte liegen als der Chart Bildpunkte breit ist, zeichnet der Zeitstrahl
          // je Bildpunkt eine Spalte mit den Anteilen (StateEntities.stateTimelineItems).
          const stateRows = this.series
            .map((s, catIndex) => ({catIndex, points: s.points, columns: s.columns, dt: s.dt, aggregation_type: s.aggregation_type}))
            .filter(row => row.aggregation_type === 'state');
          const width = Math.max(60, Math.round(chartInstance.getWidth() - 30));
          const stateTimeline = StateEntities.stateTimelineItems(
            stateRows, this.windowStart, this.periodEnd, this.windowEnd, width, pattern
          );
          timelineParts = stateTimeline.parts;
          data.push(...stateTimeline.items);
          this.series.forEach((s, catIndex) => {
            if (s.aggregation_type === 'state') return;
            const color = this.colorFor(s.entity_id);
            const points = s.points;
            for (let i = 0; i < points.length; i++) {
              const start = points[i].ts;
              const end = i + 1 < points.length ? points[i + 1].ts : (this.windowEnd ?? start);
              if (points[i].value >= 0.5 && end > start) {
                data.push([catIndex, start * 1000, end * 1000, color]);
              }
            }
          });
          const option = {
            toolbox: toolboxOption(
              this.exportFilename,
              getComputedStyle(document.body).getPropertyValue('--surface'),
              getComputedStyle(document.body).getPropertyValue('--ink-faint')
            ),
            textStyle: {
              fontFamily: getComputedStyle(document.body).getPropertyValue('--font-mono'),
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * uiFontScale * 10) / 10,
            },
            // top um 8px erhöht (16→24), Begründung wie in render().
            grid: {left: 24, right: 20, top: 24, bottom: 40, containLabel: true},
            xAxis: {
              type: 'time',
              min: this.windowStart != null ? this.windowStart * 1000 : undefined,
              max: this.periodEnd != null ? this.periodEnd * 1000 - 1000 : undefined,
              boundaryGap: [0, 0],
              // Siehe Kommentar bei der Linien-/Balken-Chart-Achse in render()
              // oben (dort auch die Begründung für den erzwungenen Tages-
              // Interval bei Woche/Monat).
              interval: ['week', 'month'].includes(this.range) ? 24 * 60 * 60 * 1000 : undefined,
              axisLabel: {
                formatter: v => (this.periodEnd != null && v >= this.periodEnd * 1000) ? '' : fmt(v / 1000),
                hideOverlap: true,
              },
            },
            // Ohne Beschriftung — der Name steht bereits in der Legende
            // darunter und im Tooltip beim Hovern (siehe categories-Lookup
            // dort); ein zusätzliches Label je Zeile wäre nur Redundanz und
            // kostet bei langen Namen unnötig Platz.
            yAxis: {
              type: 'category', data: categories.map(() => ''), inverse: true,
              axisLine: {show: false}, axisTick: {show: false}, splitLine: {show: false},
            },
            tooltip: {
              trigger: 'item',
              formatter: p => {
                const [catIndex, startMs, endMs, , idx, partsIndex] = p.value;
                const pad = n => String(n).padStart(2, '0');
                const fmtTime = ms => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
                const span = `${fmtTime(startMs)}–${fmtTime(endMs)} · <strong>${NumberFormat.fmtDuration((endMs - startMs) / 1000)}</strong>`;
                const s = this.series[catIndex];
                if (!s || s.aggregation_type !== 'state') return `${categories[catIndex]}<br>${span}`;
                if (partsIndex >= 0) {
                  const stamp = ms => new Date(ms).toLocaleString(LOCALE, {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
                  return StateEntities.condensedTooltip(timelineParts[partsIndex], startMs, endMs, s.states, stamp, escapeHtml(categories[catIndex]));
                }
                // Angezeigt wird die Übersetzung, der Rohwert steht daneben.
                const name = StateEntities.label(s.states, idx);
                const raw = StateEntities.raw(s.states, idx);
                const rawPart = raw && raw !== name ? ` <span style="opacity:.6">${escapeHtml(raw)}</span>` : '';
                return `${categories[catIndex]}<br><strong>${escapeHtml(name)}</strong>${rawPart}<br>${span}`;
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
                  // Zustände mindestens 1 px breit, wie auf der Entitätsseite:
                  // ein Zustand von Sekunden bleibt als Strich sichtbar.
                  {x: start[0], y: start[1] - height / 2, width: Math.max(end[0] - start[0], api.value(4) != null ? 1 : 0), height},
                  {x: params.coordSys.x, y: params.coordSys.y, width: params.coordSys.width, height: params.coordSys.height}
                );
                // Farbe als eigenes Datenfeld statt itemStyle je Datenpunkt —
                // api.style() im renderItem einer Custom-Serie liest nur die
                // serienweite itemStyle-Option, nicht das itemStyle jedes
                // einzelnen Datenpunkts, deshalb hier stattdessen ein
                // zusätzlicher Wert je Intervall (siehe data-Aufbau oben).
                // Verdichtete Spalte: die Anteile der Zustände als Schichten.
                if (rectShape && api.value(5) >= 0) {
                  return StateEntities.condensedShape(timelineParts[api.value(5)], rectShape, idx => StateEntities.fill(idx, pattern));
                }
                return rectShape && {type: 'rect', shape: rectShape, style: {fill: api.value(3)}};
              },
              encode: {x: [1, 2], y: 0},
              data,
            }],
          };
          chartInstance.setOption(option, true);
          chartInstance.resize();
        },

        // "Jahre" (Vergleichen-Menü, nur bei range='year') — zwei frei
        // wählbare Kalenderjahre als gleichwertige Balkenserien je Entität,
        // Monat für Monat nebeneinander (entry.years, siehe api_query_multi()
        // in api_routes.py). Eigener Renderpfad statt eines Zweigs im
        // normalen render() (Begründung dort): eine feste 12-Kategorien-
        // Achse statt einer Zeitachse, keine Stacking-/Prozent-/gleitender-
        // Durchschnitt-Fälle — für "Jahre" ohnehin nicht erreichbar.
        renderYearsCompare() {
          const MONTHS = [t('Jan'), t('Feb'), t('Mär'), t('Apr'), t('Mai'), t('Jun'), t('Jul'), t('Aug'), t('Sep'), t('Okt'), t('Nov'), t('Dez')];
          const isDurationSeries = s => s.aggregation_type === 'switch' && s.display_mode === 'time';
          const axisUnit = s => isDurationSeries(s) ? ' duration' : s.unit;
          const hasCompare = s => !!s.compare_points;
          const isSoll = s => !!this.compareSollEntityId && s.entity_id === this.compareSollEntityId;
          // Anders als beim früheren, frei wählbaren "Jahre"-Modus kommt die
          // Jahreszahl nicht mehr vom Server (kein compare_years mehr) —
          // "Vorjahr" meint immer "das Kalenderjahr von windowStart" gegen
          // exakt ein Jahr davor (year_over_year=false, offset-1, siehe
          // api_query_multi()).
          const currentYear = this.windowStart ? new Date(this.windowStart * 1000).getFullYear() : new Date().getFullYear();
          const previousYear = currentYear - 1;
          const units = [...new Set(this.series.filter(hasCompare).map(axisUnit))];
          const unitDecimals = new Map();
          this.series.forEach(s => {
            const d = this.effectiveDecimals(s);
            if (d == null) return;
            const current = unitDecimals.get(axisUnit(s));
            if (current == null || d < current) unitDecimals.set(axisUnit(s), d);
          });
          // Laufsumme bekommt eine EIGENE Y-Achse je Einheit ("kWh kumuliert"
          // statt "kWh") statt sich die Skala mit den Monatsbalken zu teilen
          // — eine Jahressumme liegt um ein Vielfaches über dem größten
          // Monatswert, beide auf einer Achse hätten die Balken auf einen
          // schmalen Streifen am unteren Rand gequetscht (siehe Mockup,
          // "Laufsumme, zweite Achse"). Nur für echte Entitäten, nicht die
          // Soll-Linie (siehe unten) — ein Ziel kumuliert sich nicht selbst.
          const cumUnits = this.compareCumsum
            ? [...new Set(this.series.filter(s => hasCompare(s) && !isSoll(s)).map(axisUnit))]
            : [];
          // Nur Einheiten, die WIRKLICH als Balken gezeichnet werden (also
          // nicht ausschließlich von der Soll-Linie genutzt werden) brauchen
          // eine sichtbare Achse — Soll teilt sich zwar per unitIndex (s. u.)
          // die Achse einer bereits vorhandenen Balken-Einheit, hat aber bei
          // einer eigenen, sonst ungenutzten Einheit sonst KEINE Balken, an
          // denen sich die Achse ablesen ließe.
          const barUnits = new Set(this.series.filter(s => hasCompare(s) && !isSoll(s)).map(axisUnit));
          // Eine Y-Achse je Einheit, dieselbe Zuordnung wie im normalen
          // Zeitverlauf (render(), units/axisKey dort) — nur ohne dessen
          // Stacking-/Normierungs-/Dynamische-Y-Achse-Verzweigung. Kumulierte
          // Achsen hängen HINTEN an.
          const axisUnits = [...units, ...cumUnits];
          // Laufsumme UND eine reine Soll-Achse bleiben unsichtbar (show:
          // false) — beides sind Ableitungen, keine eigenständig ablesbaren
          // Größen (eine "kumulierte °C"-Achse ergäbe ohnehin keinen Sinn,
          // eine reine Soll-Achse zeigte nur die Ziel-Linie einer einzelnen
          // Entität). ECharts benutzt eine ausgeblendete Achse trotzdem
          // weiter zum Skalieren der Serien, die auf sie zeigen (yAxisIndex),
          // reserviert dafür aber keinen Rand — deshalb links/rechts/offset
          // NUR über die sichtbaren Achsen hochzählen, sonst bliebe für eine
          // unsichtbare Achse unnötig Platz frei (gemessen: zwei "kWh"-
          // Achsenbeschriftungen liefen bei EINER Einheit + Laufsumme, aber
          // ohne Soll, ineinander, weil beide denselben Offset 0 bekamen,
          // statt dass die zweite (unsichtbare) einfach entfällt).
          let visiblePos = 0;
          const yAxis = axisUnits.map((u, i) => {
            const isCum = i >= units.length;
            const hidden = isCum || !barUnits.has(u);
            const pos = hidden ? 0 : visiblePos++;
            const decimals = unitDecimals.get(u);
            const isDuration = u === ' duration';
            return {
              type: 'value',
              // show:false ALLEIN reichte nicht — containLabel (grid, s. u.)
              // reservierte trotzdem Rand für die ausgeblendete Achse (Name +
              // Beschriftungsbreite fließen in dessen Berechnung ein,
              // unabhängig von show), sichtbar als unbegründeter Leerraum
              // links vom sichtbaren "kWh" links neben den Balken (gemessen).
              // axisLine/axisTick/axisLabel/splitLine/name einzeln
              // abzuschalten nimmt der Achse jeden Platzanspruch, show:false
              // bleibt zusätzlich als Absicherung stehen.
              show: !hidden,
              name: hidden ? '' : (isDuration ? t('Dauer') : u || '') + (isCum ? t(' kumuliert') : '') || undefined,
              nameLocation: 'end',
              position: pos % 2 === 0 ? 'left' : 'right',
              offset: Math.floor(pos / 2) * 55,
              min: value => Math.min(0, value.min),
              max: value => Math.max(0, value.max),
              axisLine: {show: !hidden},
              axisTick: {show: !hidden},
              splitLine: {show: !hidden},
              axisLabel: {
                show: !hidden,
                formatter: v => isDuration ? NumberFormat.fmtDuration(v) : (u ? `${fmtNum(v, decimals)} ${u}` : fmtNum(v, decimals)),
              },
            };
          });
          const visibleAxisCount = visiblePos;
          const cumAxisIndex = u => { const i = cumUnits.indexOf(u); return i === -1 ? -1 : units.length + i; };
          // Eine Farbe je Entität (nicht je Jahr) — bleibt so auch bei
          // mehreren gleichzeitig gewählten Entitäten eindeutig zuordenbar,
          // statt mit einem zweiten Farbschema um dieselben Paletten-Farben
          // zu konkurrieren. Das aktuelle Jahr bekommt volle Deckkraft, das
          // Vorjahr ist gedimmt — dieselbe Idee wie die bestehende
          // Vorjahres-Schattenserie (render()), nur beide diesmal mit
          // eigenem Namen und eigener Legende.
          const echartsSeries = [];
          this.series.forEach(s => {
            if (!hasCompare(s)) return;
            const color = this.colorFor(s.entity_id);
            const displayName = this.entityNames[s.entity_id] || s.friendly_name;
            const decimals = unitDecimals.get(axisUnit(s));
            const unitIndex = units.indexOf(axisUnit(s));
            const valueFormatter = v => v == null
              ? '—'
              : (isDurationSeries(s) ? NumberFormat.fmtDuration(v) : `${fmtNum(v, decimals)}${s.unit ? ' ' + s.unit : ''}`);
            // Die Soll-Entität ist kein Jahresvergleich mit sich selbst,
            // sondern eine einzelne Ziel-Linie fürs aktuelle Jahr — teilt
            // sich aber die Achse ihrer EIGENEN Einheit wie jede andere
            // Entität auch (unitIndex oben), statt fest auf einer fremden zu
            // landen: eine Soll-Entität mit abweichender Einheit (z. B. °C
            // gegen eine m³-Entität) hätte auf einer geteilten Achse ihre
            // rohen Werte in die falsche Skala gemischt und die eigentliche
            // Entität auf einen schmalen Streifen gequetscht — gemessen mit
            // Außentemperatur als Soll neben dem Wasserzähler. Gepunktet
            // statt gestrichelt hält sie optisch von der Laufsumme
            // (gestrichelt, siehe unten) auseinander.
            if (isSoll(s)) {
              echartsSeries.push({
                name: displayName,
                type: 'line',
                yAxisIndex: unitIndex,
                data: monthlyBuckets(s.points, s.aggregation_type),
                lineStyle: {type: 'dotted', width: 2, color},
                itemStyle: {color},
                symbol: 'none',
                z: 3,
                tooltip: {valueFormatter},
              });
              return;
            }
            // Aktuelles Jahr ZUERST (siehe Reihenfolge unten) — ECharts
            // ordnet gruppierte Balken einer Kategorie in Serien-Reihenfolge
            // an, das zuerst gepushte Jahr steht also links. Ohne Radius
            // (anders als ein früherer Stand hier): die übrigen Balken
            // dieser Seite (render() oben, chartType 'bar') sind ebenfalls
            // eckig, dieselbe Optik bleibt auch im Jahresvergleich erhalten.
            [
              {year: currentYear, points: s.points, opacity: 1},
              {year: previousYear, points: s.compare_points, opacity: 0.55},
            ].forEach(({year, points, opacity}) => {
              const monthly = monthlyBuckets(points, s.aggregation_type);
              // withOpacity() statt itemStyle.opacity — der Tooltip-Marker
              // (ECharts' eigener, s. o.) ignoriert itemStyle/lineStyle.opacity
              // und zeigte Vorjahr/aktuelles Jahr dadurch mit demselben
              // satten Farbpunkt an, obwohl der Balken im Chart sichtbar
              // blasser ist (gemessen).
              echartsSeries.push({
                name: `${displayName} (${year})`,
                type: 'bar',
                yAxisIndex: unitIndex,
                barGap: '20%',
                data: monthly,
                itemStyle: {color: withOpacity(color, opacity)},
                tooltip: {valueFormatter},
              });
              if (this.compareCumsum) {
                echartsSeries.push({
                  name: t('{name} ({year}) kumuliert',{name: displayName, year}),
                  type: 'line',
                  yAxisIndex: cumAxisIndex(axisUnit(s)),
                  data: cumulate(monthly),
                  lineStyle: {type: 'dashed', width: 2, color: withOpacity(color, opacity)},
                  itemStyle: {color: withOpacity(color, opacity)},
                  symbol: 'none',
                  z: 4,
                  tooltip: {valueFormatter},
                });
              }
            });
          });
          const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
          chartInstance.setOption({
            toolbox: toolboxOption(
              this.exportFilename,
              getComputedStyle(document.body).getPropertyValue('--surface'),
              getComputedStyle(document.body).getPropertyValue('--ink-faint')
            ),
            textStyle: {
              fontFamily: getComputedStyle(document.body).getPropertyValue('--font-mono'),
              color: getComputedStyle(document.body).getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * uiFontScale * 10) / 10,
            },
            // top/left/right deutlich großzügiger als die 24/10/20 der
            // übrigen Renderpfade: eine zweite, rechte Achse mit langem
            // Namen ("… kumuliert") saß dort mit dem Toolbox-Download-Icon
            // (top:0, siehe toolboxOption()) buchstäblich übereinander und
            // wurde am Kartenrand abgeschnitten — gemessen mit aktiver
            // Laufsumme bei zwei Einheiten gleichzeitig.
            grid: {
              left: visibleAxisCount > 1 ? 34 : 24,
              right: visibleAxisCount > 1 ? 40 : 24,
              top: 36, bottom: 40, containLabel: true,
            },
            xAxis: {type: 'category', data: MONTHS},
            yAxis,
            tooltip: {trigger: 'axis'},
            series: echartsSeries,
          }, true);
          chartInstance.resize();
        },

        // Mehrjahresvergleich: eine Linie je Jahr über Jan–Dez (immer Linien,
        // auch für Zähler — zehn und mehr Balken je Monat wären nicht mehr
        // lesbar), das angezeigte Jahr kräftig, ältere blasser, dazu der Ø
        // der Vorjahre gestrichelt. Eigener Renderpfad wie renderYearsCompare(),
        // der bestehende Vorjahresvergleich bleibt davon unberührt.
        renderMultiYear() {
          const my = this.multiYear;
          const MONTHS = [t('Jan'), t('Feb'), t('Mär'), t('Apr'), t('Mai'), t('Jun'), t('Jul'), t('Aug'), t('Sep'), t('Okt'), t('Nov'), t('Dez')];
          const s = my.entity;
          const unit = s.unit ? ` ${s.unit}` : '';
          const format = v => v == null ? '—' : (my.isDuration ? NumberFormat.fmtDuration(v) : `${fmtNum(v, this.effectiveDecimals(s))}${unit}`);
          const style = getComputedStyle(document.body);
          const averageColor = style.getPropertyValue('--ink-muted').trim();
          const total = my.years.length;
          // Älteste Jahre zuerst, damit das angezeigte Jahr obenauf liegt.
          const echartsSeries = my.years.map((y, i) => {
            const color = this.multiYearColor(i, total);
            return {
              name: String(y.year),
              type: 'line',
              data: y.monthly.map((v, m) => (y.isCurrent && m === my.cutMonth && v != null
                ? {value: v, symbol: 'emptyCircle', symbolSize: 8}
                : v)),
              lineStyle: {color, width: y.isCurrent ? 3 : 1.5},
              itemStyle: {color},
              symbol: y.isCurrent ? 'circle' : 'none',
              symbolSize: 5,
              z: y.isCurrent ? 4 : 2,
              emphasis: {focus: 'series'},
            };
          }).reverse();
          if (my.average && this.multiYearAverage) {
            echartsSeries.push({
              name: t('Ø {from}–{to}', {from: my.averageFrom, to: my.averageTo}),
              type: 'line',
              data: my.average,
              lineStyle: {color: averageColor, type: 'dashed', width: 2},
              itemStyle: {color: averageColor},
              symbol: 'emptyCircle',
              symbolSize: 6,
              z: 5,
            });
          }
          const uiFontScale = parseFloat(style.getPropertyValue('--font-scale')) || 1;
          chartInstance.setOption({
            toolbox: toolboxOption(this.exportFilename, style.getPropertyValue('--surface'), style.getPropertyValue('--ink-faint')),
            textStyle: {
              fontFamily: style.getPropertyValue('--font-mono'),
              color: style.getPropertyValue('--ink-muted'),
              fontSize: Math.round(12 * uiFontScale * 10) / 10,
            },
            grid: {left: 24, right: 24, top: 36, bottom: 40, containLabel: true},
            xAxis: {type: 'category', data: MONTHS},
            yAxis: {
              type: 'value',
              name: my.isDuration ? t('Dauer') : (s.unit || undefined),
              nameLocation: 'end',
              scale: my.agg === 'standard',
              min: my.agg === 'standard' ? undefined : 0,
              axisLabel: {formatter: v => my.isDuration ? NumberFormat.fmtDuration(v) : fmtNum(v, this.effectiveDecimals(s))},
            },
            tooltip: {
              trigger: 'axis',
              formatter: params => {
                const m = params[0].dataIndex;
                const rows = params
                  .filter(p => p.value != null && p.value !== '-')
                  // Neuestes Jahr oben, der Ø (letzte Serie) ganz unten.
                  .sort((a, b) => (a.seriesIndex === total) - (b.seriesIndex === total) || b.seriesIndex - a.seriesIndex)
                  .map(p => {
                    const value = typeof p.value === 'object' ? p.value.value : p.value;
                    const partial = p.seriesName === String(my.years[0].year) && m === my.cutMonth
                      ? ` (${t('bis {cut}', {cut: my.cutLabel})})` : '';
                    return `${p.marker}${p.seriesName}: <strong>${format(value)}</strong>${partial}`;
                  });
                const reference = my.currentReference ? my.currentReference[m] : null;
                const delta = this.multiYearDelta(my.years[0].monthly[m], reference);
                const deltaRow = delta ? `<br>${t('{year} zum Ø: {delta}', {year: my.years[0].year, delta})}` : '';
                return `<strong>${MONTHS[m]}</strong><br>${rows.join('<br>')}${deltaRow}`;
              },
            },
            series: echartsSeries,
          }, true);
          chartInstance.resize();
        },

        // Donut statt Zeitverlauf ("Darstellungsart") — ein Anteil je Serie
        // aus GENAU EINEM aggregierten Wert (donutAggregation: Summe/
        // Durchschnitt/Letzter Wert), keine Zeitachse. Nutzt this.series
        // direkt (dieselben rohen, ungebuckelten Punkte wie seriesStats()
        // für die Legende) statt der resampelten preparedPoints aus render()
        // — eine Bucket-Verfeinerung ergibt für einen einzigen Werte je
        // Serie keinen Sinn. Dieselbe Radius-/Emphasis-/Tooltip-Konfiguration
        // wie #storage-pie (statistik.js) und .edash-share-donut
        // (energiedashboard.js), damit ein Donut in der ganzen App gleich
        // aussieht — inklusive label:show:false: die Namen stehen bereits in
        // der Legende darunter (chart-legend/chart-legend-table, unverändert
        // wiederverwendet), eine zweite Beschriftung im Ring selbst wäre
        // Redundanz.
        // Ausgelagert aus renderDonut(), damit exportCsv() im Donut-Modus
        // GENAU denselben Wert exportiert, der im Ring steckt — seriesStats()
        // (Legende) wäre hier keine verlässliche Quelle: deren sum-Feld ist
        // bei Nicht-Zähler-/Nicht-Schalter-Entitäten bewusst null (siehe dort),
        // während der Donut trotzdem einen Summenwert zeichnet.
        donutValueFor(s) {
          const values = s.points.map(p => p.value).filter(Number.isFinite);
          if (!values.length) return 0;
          if (this.donutAggregation === 'average') return values.reduce((sum, v) => sum + v, 0) / values.length;
          if (this.donutAggregation === 'last') return values[values.length - 1];
          return values.reduce((sum, v) => sum + v, 0);
        },
        // Zustände: ein Donut je Entität nebeneinander, Anteile je Zustand
        // (Mockup "Chart-Editor mit mehreren Zustands-Entitäten"). Schalter
        // daneben fallen hier heraus — ihr Donut ist ein Anteil am Ganzen
        // über Entitäten, das lässt sich nicht in dieselbe Reihe stellen.
        // Anteile oder Wechsel je Zustands-Entität (state-entities.js barPanels()).
        renderStateBarPanels() {
          if (!chartInstance) chartInstance = echarts.init(document.getElementById('chart'));
          const css = getComputedStyle(document.body);
          const rows = this.series
            .filter(s => s.aggregation_type === 'state' && !this.legendHiddenIds.includes(s.entity_id))
            .map(s => ({
              name: this.entityNames[s.entity_id] || s.friendly_name,
              states: s.states,
              buckets: StateEntities.bucketsForSeries(s, this.range, this.windowEnd),
            }));
          // Ab zwei Entitäten wächst die Höhe mit: sonst wären die Diagramme
          // zu flach für ihre Achsenbeschriftung.
          document.getElementById('chart').style.height = rows.length > 1 ? `${Math.max(420, rows.length * 190)}px` : '';
          chartInstance.resize();
          chartInstance.setOption({
            ...StateEntities.barPanels(rows, {
              mode: this.stateMode, range: this.range, windowStart: this.windowStart, periodEnd: this.periodEnd,
            }),
            toolbox: toolboxOption(this.exportFilename, css.getPropertyValue('--surface'), css.getPropertyValue('--ink-faint')),
          }, true);
          chartInstance.resize();
        },
        // Heatmap: eine je Zustands-Entität (state-entities.js heatmapPanels()).
        renderStateHeatmapPanels() {
          if (!chartInstance) chartInstance = echarts.init(document.getElementById('chart'));
          const css = getComputedStyle(document.body);
          const rows = this.heatmapRows;
          // Ab zwei Entitäten wächst die Höhe mit, wie bei Anteilen/Wechsel.
          document.getElementById('chart').style.height = rows.length > 1 ? `${Math.max(420, rows.length * 260)}px` : '';
          chartInstance.resize();
          chartInstance.setOption({
            ...StateEntities.heatmapPanels(rows),
            toolbox: toolboxOption(this.exportFilename, css.getPropertyValue('--surface'), css.getPropertyValue('--ink-faint')),
          }, true);
          chartInstance.resize();
        },
        // Sichtbare Zustands-Entitäten mit Zellen und gezeichnetem Zustand.
        get heatmapRows() {
          return this.series
            .filter(s => s.aggregation_type === 'state' && s.cells && !this.legendHiddenIds.includes(s.entity_id))
            .map(s => ({
              name: this.entityNames[s.entity_id] || s.friendly_name,
              states: s.states,
              cells: s.cells,
              idx: StateEntities.heatmapIndexFor(s.cells, s.states, this.heatmapStates[s.entity_id]),
            }))
            .filter(row => row.idx !== null);
        },
        // Ob ein Zustand in der Legende der gezeichnete ist (nur in der Heatmap
        // gibt es überhaupt einen; "nicht verfügbar" ist keiner).
        heatmapChipActive(entityId, idx) {
          const s = this.series.find(x => x.entity_id === entityId);
          if (!s || !s.cells) return false;
          return StateEntities.heatmapIndexFor(s.cells, s.states, this.heatmapStates[entityId]) === idx;
        },
        // Klick auf einen Zustand in der Legende wählt ihn für diese Entität.
        pickHeatmapState(entityId, idx) {
          if (this.stateMode !== 'heatmap' || idx === StateEntities.UNAVAILABLE) return;
          const s = this.series.find(x => x.entity_id === entityId);
          if (!s) return;
          this.heatmapStates = {...this.heatmapStates, [entityId]: StateEntities.raw(s.states, idx)};
          this.render();
        },
        renderStateDonuts() {
          const css = getComputedStyle(document.body);
          const surface = css.getPropertyValue('--surface');
          const inkFaint = css.getPropertyValue('--ink-faint');
          const pattern = StateEntities.unavailablePattern();
          const rows = this.series.filter(s => s.aggregation_type === 'state' && !this.legendHiddenIds.includes(s.entity_id));
          const width = 100 / Math.max(rows.length, 1);
          chartInstance.setOption({
            toolbox: toolboxOption(this.exportFilename, surface, inkFaint),
            tooltip: {trigger: 'item', appendToBody: true},
            series: rows.map((s, k) => ({
              type: 'pie',
              radius: ['38%', '62%'],
              center: [`${width * k + width / 2}%`, rows.length > 1 ? '45%' : '50%'],
              avoidLabelOverlap: true,
              label: {show: false},
              labelLine: {show: false},
              itemStyle: {borderColor: surface, borderWidth: 2},
              tooltip: {
                formatter: p => {
                  const raw = StateEntities.raw(s.states, p.data.idx);
                  const rawPart = raw && raw !== p.name ? ` <span style="opacity:.6">${escapeHtml(raw)}</span>` : '';
                  const name = escapeHtml(this.entityNames[s.entity_id] || s.friendly_name);
                  return `${name}<br>${p.marker} <strong>${escapeHtml(p.name)}</strong>${rawPart}<br>${NumberFormat.fmtDuration(p.value)} (${fmtNum(p.percent, 1)} %)`;
                },
              },
              data: this.stateRowsFor(s).map(row => ({
                name: row.label, value: row.seconds, idx: row.idx,
                itemStyle: {color: StateEntities.fill(row.idx, pattern)},
              })),
            })),
            // Name unter dem Donut nur, wenn mehrere nebeneinander stehen.
            // title statt graphic: textAlign 'center' zentriert den Text auf
            // left, bei graphic setzt left die linke Kante.
            title: rows.length > 1 ? rows.map((s, k) => ({
              text: this.entityNames[s.entity_id] || s.friendly_name,
              left: `${width * k + width / 2}%`,
              top: '86%',
              textAlign: 'center',
              textStyle: {
                color: css.getPropertyValue('--ink-muted').trim(),
                fontFamily: css.getPropertyValue('--font-sans').trim() || 'sans-serif',
                fontSize: 12,
                fontWeight: 'normal',
              },
            })) : [],
          }, true);
          chartInstance.resize();
        },
        renderDonut() {
          if (!chartInstance) chartInstance = echarts.init(document.getElementById('chart'));
          if (this.hasState) { this.renderStateDonuts(); return; }
          const surface = getComputedStyle(document.body).getPropertyValue('--surface');
          const inkFaint = getComputedStyle(document.body).getPropertyValue('--ink-faint');
          const data = this.series.map(s => ({
            name: this.entityNames[s.entity_id] || s.friendly_name,
            value: Math.max(0, this.donutValueFor(s)),
            itemStyle: {color: this.colorFor(s.entity_id)},
          }));
          // Serien-Umschalter der Legende (toggleLegendItem()/legendHiddenIds)
          // wirkt bei type:'pie' auf einzelne DATENPUNKTE statt auf ganze
          // Serien — ECharts behandelt jeden data-Eintrag wie einen eigenen
          // Legendeneintrag (per name), legendToggleSelect trifft darüber
          // trotzdem genau den richtigen Slice. Ohne dieses (unsichtbare,
          // show:false — wie im Zeitverlauf-Zweig oben) legend-Objekt hätte
          // die Aktion nichts zum Umschalten: die Legendenzeile blendete sich
          // dann selbst ab, ohne den Ring zu ändern.
          const legendSelected = {};
          this.series.forEach(s => {
            legendSelected[this.entityNames[s.entity_id] || s.friendly_name] = !this.legendHiddenIds.includes(s.entity_id);
          });
          chartInstance.setOption({
            toolbox: toolboxOption(this.exportFilename, surface, inkFaint),
            legend: {show: false, data: data.map(d => d.name), selected: legendSelected},
            tooltip: {
              trigger: 'item',
              formatter: p => {
                const s = this.series[p.dataIndex];
                const unit = s.unit ? ` ${s.unit}` : '';
                return `${p.marker} ${p.name}: <strong>${fmtNum(p.value, this.effectiveDecimals(s))}${unit}</strong> (${fmtNum(p.percent, 1)} %)`;
              },
              appendToBody: true,
            },
            series: [{
              type: 'pie',
              radius: ['52%', '85%'],
              center: ['50%', '50%'],
              avoidLabelOverlap: true,
              label: {show: false},
              labelLine: {show: false},
              itemStyle: {borderColor: surface, borderWidth: 2},
              emphasis: {scaleSize: 6, itemStyle: {shadowBlur: 12, shadowColor: 'rgba(0,0,0,0.25)'}},
              data,
            }],
          }, true);
          chartInstance.resize();
        },

        // "CSV" (Titelzeile) — dieselbe Blob-Download-Technik wie
        // exportCsv() in table_editor.js (Semikolon-Trennzeichen, BOM,
        // Anführungszeichen je Zelle), hier zwei Formen statt eines festen
        // Zeilen/Spalten-Rasters: im Donut GENAU der Wert, der im Ring
        // steckt (donutValueFor(), eine Zeile je Entität) — Zeitstempel gäbe
        // es dort nicht, jede Serie ist ja schon auf einen Wert reduziert.
        // Sonst (Zeitverlauf UND Zeitstrahl) ein Zeitstempel-Raster über die
        // Vereinigung aller Serien-Zeitstempel, eine Spalte je Entität.
        exportCsv() {
          const csvEscape = s => `"${String(s).replace(/"/g, '""')}"`;
          const lines = [];
          if (this.donut && this.hasState) {
            // Zustände: eine Zeile je Entität und Zustand, mit Text statt Index.
            lines.push([t('Entität'), t('Zustand'), t('Dauer'), t('Anteil')].map(csvEscape).join(';'));
            this.series.filter(s => s.aggregation_type === 'state').forEach(s => {
              const name = this.entityNames[s.entity_id] || s.friendly_name;
              this.stateRowsFor(s).forEach(row => {
                lines.push([name, row.label, NumberFormat.fmtDuration(row.seconds), StateEntities.fmtShare(row.share)].map(csvEscape).join(';'));
              });
            });
          } else if (this.donut) {
            lines.push([t('Entität'), t('Wert')].map(csvEscape).join(';'));
            this.series.forEach(s => {
              const name = this.entityNames[s.entity_id] || s.friendly_name;
              const value = fmtNum(this.donutValueFor(s), this.effectiveDecimals(s)) + (s.unit ? ` ${s.unit}` : '');
              lines.push([name, value].map(csvEscape).join(';'));
            });
          } else {
            const tsSet = new Set();
            this.series.forEach(s => s.points.forEach(p => tsSet.add(p.ts)));
            const timestamps = [...tsSet].sort((a, b) => a - b);
            const names = this.series.map(s => this.entityNames[s.entity_id] || s.friendly_name);
            lines.push([t('Zeit'), ...names].map(csvEscape).join(';'));
            timestamps.forEach(ts => {
              const row = [new Date(ts * 1000).toLocaleString(LOCALE)];
              this.series.forEach(s => {
                const p = s.points.find(pt => pt.ts === ts);
                if (s.aggregation_type === 'state') {
                  row.push(p && Number.isFinite(p.value) ? StateEntities.label(s.states, Math.round(p.value)) : '');
                  return;
                }
                row.push(p && Number.isFinite(p.value) ? fmtNum(p.value, this.effectiveDecimals(s)) : '');
              });
              lines.push(row.map(csvEscape).join(';'));
            });
          }
          const blob = new Blob(['﻿' + lines.join('\r\n')], {type: 'text/csv;charset=utf-8;'});
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${this.exportFilename}.csv`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        },

        async save() {
          if (!this.name.trim()) { appAlert(t('Bitte einen Namen für das Chart angeben.')); return; }
          if (this.selectedEntityIds.length === 0) { appAlert(t('Bitte mindestens eine Entität auswählen.')); return; }
          this.saving = true;
          this.savedMessage = '';
          // Nur Namen tatsächlich ausgewählter Entitäten mitschicken — eine
          // abgewählte Entität soll ihren früher gesetzten Anzeigenamen nicht
          // stumm für den Fall aufheben, dass sie später wieder angehakt wird.
          const entityNames = {};
          this.selectedEntityIds.forEach(id => {
            if (this.entityNames[id] && this.entityNames[id].trim()) entityNames[id] = this.entityNames[id].trim();
          });
          const body = {
            name: this.name.trim(),
            entity_ids: this.selectedEntityIds,
            range_key: this.range,
            continuous: this.continuous,
            period_anchor: this.periodAnchorForSave,
            // Vergleich samt Langzeit-/Vorjahr-Erweiterungen, siehe
            // compare_settings_or_400() in chart_period.py.
            compare_settings: {
              compare: this.compare, mode: this.compareMode,
              years: this.compareYears, average: this.multiYearAverage,
              cumsum: this.compareCumsum, target: this.compareSollEntityId || null,
            },
            entity_names: entityNames,
            // Nur ausgeblendete IDs mitschicken, die überhaupt noch ausgewählt
            // sind — sonst hielte eine abgewählte Entität ihren Ausblend-Zustand
            // stumm fest, bis sie irgendwann wieder angehakt wird.
            hidden_entity_ids: this.hiddenEntityIds.filter(id => this.selectedEntityIds.includes(id)),
            resolution_preset: this.resolutionPreset,
            dynamic_y_axis: this.dynamicYAxis,
            chart_stats: this.chartStats,
            legend_metrics: this.legendMetrics,
            legend_style: this.legendStyle,
            chart_type: this.donut ? 'donut' : (this.timeline ? (this.hasState ? (this.heatmapWanted ? 'heatmap' : (this.stateChosen ? this.stateMode : 'auto')) : 'timeline') : (this.allSwitch ? 'bar' : 'auto')),
            heatmap_states: this.heatmapStates,
            series_colors: this.seriesColors,
            decimals: this.decimals,
            show_values: this.showValues,
            average_line: this.averageLine,
            area_fill: this.areaFill,
            show_points: this.showPoints,
            stacked: this.stacked,
            normalize: this.normalize,
            average_style: this.averageStyle,
            horizontal: this.horizontal,
            donut_aggregation: this.donutAggregation,
          };
          try {
            const url = CHART_ID ? `${BASE}/charts/${CHART_ID}` : `${BASE}/charts`;
            const res = await fetch(url, {
              method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
            });
            if (!res.ok) {
              const err = await res.json().catch(() => ({}));
              appAlert(err.detail || t('Speichern fehlgeschlagen.'));
              return;
            }
            const data = await res.json();
            if (!CHART_ID) {
              window.location.href = `${BASE}/charts/${data.id}`;
              return;
            }
            this.savedMessage = t('✓ Gespeichert');
            this.editing = false;
            setTimeout(() => { this.savedMessage = ''; }, 2000);
          } finally {
            this.saving = false;
          }
        },

        async deleteChart() {
          if (!await appConfirm(t('Dieses Chart wirklich löschen?'), {danger: true})) return;
          // Wie deleteTable() in table_editor.js: bei einem Fehler auf der
          // Seite bleiben, statt zur Liste zu springen, in der das Chart steht.
          let response;
          try {
            response = await fetch(`${BASE}/charts/${CHART_ID}/delete`, {method: 'POST'});
          } catch (e) {
            response = null;
          }
          if (!response || !response.ok) {
            appAlert(t('Löschen fehlgeschlagen.'));
            return;
          }
          window.location.href = `${BASE}/charts`;
        },

        // Verwirft unsaved Änderungen. Ohne CHART_ID gibt es keinen
        // gespeicherten Stand, zu dem zurückgekehrt werden könnte — dann
        // direkt zur Chart-Liste. Mit CHART_ID: neu laden statt jedes
        // reaktive Feld (Entitäten, Reihenfolge, Auflösung, Darstellung,
        // Vergleich, Legende, …) einzeln zurückzusetzen, garantiert exakt
        // den zuletzt gespeicherten Stand.
        cancelEdit() {
          if (!CHART_ID) { window.location.href = `${BASE}/charts`; return; }
          window.location.reload();
        },

        init() {
          // Ein vor diesem Fix gespeichertes Chart könnte noch
          // resolution_preset:"full" UND continuous:true gemeinsam tragen
          // (siehe onResolutionChange()/Optionen-Menü, die das ab jetzt nur
          // clientseitig verhindern) — hier einmalig vor dem ersten load()
          // bereinigen, statt das bei jedem Rendern erneut zu maskieren.
          if (this.resolutionPreset === 'full' && this.continuous) this.continuous = false;
          this.load();
          window.addEventListener('resize', () => chartInstance && chartInstance.resize());
          new ResizeObserver(() => chartInstance && chartInstance.resize()).observe(document.getElementById('chart'));
          this.$nextTick(() => setupEntityDrag(this));
        },
      };
    }
