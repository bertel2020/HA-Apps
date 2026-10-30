// Dashboard-Kacheln auf der Übersichtsseite (Konzept "Offene Punkte",
// erweitert um Vergleichstabellen) — rendert je Kachel entweder ein
// kompaktes ECharts-Mini-Chart (Charts, über /api/query-multi, dasselbe wie
// bei der vollen Chart-Seite chart_editor.html, nur ohne Zeitraum-Toolbar/
// Tooltip-Feinschliff — eine einfache Legende ist ab 2×2 Kachelgröße optional
// zuschaltbar, siehe Kachelmenü/renderLegend()) oder eine reduzierte Mini-
// Tabelle (Vergleichstabellen, über static/js/table-compute.js — derselbe Rechenkern wie
// table_editor.html). Bis zu 18 Kacheln gleichzeitig auf der meistbesuchten
// Seite der App — ein IntersectionObserver rendert erst, sobald eine Kachel
// tatsächlich sichtbar wird, statt alle sofort beim Laden zu initialisieren
// (sonst wäre ausgerechnet die Startseite die langsamste Seite).
//
// Läuft sowohl beim initialen Laden von "/" als auch nach jedem Pin/Unpin,
// wenn htmx das #dashboard-grid-Fragment neu einsetzt — deshalb über
// htmx:afterSettle neu verdrahtet statt nur einmal bei DOMContentLoaded.
//
// Reihenfolge ist per Drag&Drop änderbar (setupDragAndDrop() unten) — native
// HTML5-Drag&Drop-API statt einer zusätzlichen Bibliothek, konsistent mit
// dem Rest der App (kein Build-Schritt, minimale Abhängigkeiten).
(() => {
  // Dieselbe feste Farbpalette wie chart_editor.html — Kacheln und die volle
  // Chart-Seite sollen dieselbe Entität farblich konsistent zeigen.
  const PALETTE = Array.from({length: 8}, (_, i) =>
    getComputedStyle(document.documentElement).getPropertyValue(`--chart-${i + 1}`).trim()
  );
  const UI_FONT_SCALE = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--font-scale')
  ) || 1;
  const scaledFont = size => Math.round(size * UI_FONT_SCALE * 10) / 10;
  // Wortgleich mit withOpacity() in chart_editor.js (Begründung dort: der
  // Tooltip-Marker ignoriert itemStyle.opacity).
  const withOpacity = (hex, opacity) => {
    const n = parseInt(hex.replace('#', ''), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${opacity})`;
  };
  const MONTH_LABELS = () => [t('Jan'), t('Feb'), t('Mär'), t('Apr'), t('Mai'), t('Jun'), t('Jul'), t('Aug'), t('Sep'), t('Okt'), t('Nov'), t('Dez')];
  // Kalendermonats-Buckets und Laufsumme — wortgleich mit monthlyBuckets()/
  // cumulate() in chart_editor.js (Begründungen dort).
  function monthlyBuckets(points, aggregationType) {
    const groups = Array.from({length: 12}, () => []);
    (points || []).forEach(point => {
      if (!Number.isFinite(point.value)) return;
      groups[new Date(point.ts * 1000).getMonth()].push(point.value);
    });
    return groups.map(values => {
      if (!values.length) return null;
      const sum = values.reduce((total, v) => total + v, 0);
      return aggregationType === 'standard' ? sum / values.length : sum;
    });
  }
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

  // Vergleich, der mit dem Chart gespeichert ist (saved_charts.compare_settings,
  // data-compare-settings) — dieselben Regeln wie chart_editor.js: kein
  // Vergleich bei Zeitstrahl, Donut, Auflösung "Voll" oder gestapelten Balken
  // (dort ist der Knopf gesperrt), "Langzeit" nur bei Jahr/kalendarisch/genau
  // einer Entität, sonst wie im Editor "Vorjahr" (load()); "Vorjahr" mit
  // Laufsumme oder Soll zeichnet Monats-Balkenpaare (yearsCompareActive).
  const COMPARE_YEAR_RANGES = ['hour', 'day', 'week', 'month'];
  function tileCompare(el, {range, continuous, entityIds, chartType, resolutionPreset, stacked}) {
    let settings = {};
    try { settings = JSON.parse(el.dataset.compareSettings || '{}') || {}; } catch (e) { settings = {}; }
    if (!settings.compare || ['timeline', 'donut', 'shares', 'changes', 'heatmap'].includes(chartType) || resolutionPreset === 'full'
        || (stacked && entityIds.length >= 2)) return null;
    let mode = settings.mode || 'previous';
    const longterm = mode === 'longterm' && range === 'year' && !continuous && entityIds.length === 1;
    if (mode === 'longterm' && !longterm) mode = 'previous';
    if (mode === 'year' && !COMPARE_YEAR_RANGES.includes(range)) mode = 'previous';
    const target = settings.target && entityIds.includes(settings.target) ? settings.target : '';
    const cumsum = !!settings.cumsum;
    return {
      mode, longterm, target, cumsum,
      years: settings.years ?? 5,
      average: settings.average ?? true,
      yearsCompare: mode === 'previous' && range === 'year' && (cumsum || !!target),
    };
  }

  // Durchschnitt der gezeichneten Werte, oder null wenn es keine gibt.
  // Wortgleich mit averageOf() in entity_detail.js/chart_editor.js — selbst
  // gerechnet statt über ECharts' eigenen Durchschnitts-Typ für markLine,
  // damit der Wert nicht davon abhängt, welche Punkte die Serie gerade führt.
  const averageOf = values => {
    const zahlen = values.filter(Number.isFinite);
    return zahlen.length ? zahlen.reduce((summe, v) => summe + v, 0) / zahlen.length : null;
  };
  // Gleitender Durchschnitt — wortgleich mit movingAverageWindow()/
  // movingAverage() in chart_editor.js (siehe dortiger Kommentar zur
  // Fensterbreite als Anteil statt fester Tageszahl).
  const movingAverageWindow = pointCount => Math.min(60, Math.max(3, Math.round(pointCount / 12)));
  const movingAverage = (points, windowPoints) => points.map((p, i) => {
    const start = Math.max(0, i - Math.floor(windowPoints / 2));
    const end = Math.min(points.length, i + Math.ceil(windowPoints / 2));
    const slice = points.slice(start, end).map(q => q.value).filter(Number.isFinite);
    return {ts: p.ts, value: slice.length ? slice.reduce((s, v) => s + v, 0) / slice.length : null};
  });
  const RESOLUTION_SECONDS = {
    hour: {medium: 5 * 60, coarse: 15 * 60},
    day: {medium: 30 * 60, coarse: 60 * 60},
    week: {medium: 6 * 60 * 60, coarse: 24 * 60 * 60},
    month: {medium: 24 * 60 * 60, coarse: 7 * 24 * 60 * 60},
    year: {medium: 30 * 24 * 60 * 60, coarse: 90 * 24 * 60 * 60},
    decade: {medium: 365 * 24 * 60 * 60, coarse: 2 * 365 * 24 * 60 * 60},
  };

  // ECharts stapelt auf einer Zeit-Achse nach INDEX in den Daten, nicht nach
  // Zeitstempel (nachgemessen mit ECharts 5.6: Serie B mit Beginn im April
  // landete auf Serie A vom Januar). Der Server liefert Buckets aber erst ab
  // dem ersten vorhandenen Wert — beginnen gestapelte Serien in
  // verschiedenen Perioden, rutscht die spätere nach links. Deshalb werden
  // alle Serien eines Stapels vor dem Zeichnen auf die Vereinigung ihrer
  // Zeitstempel gebracht; fehlende Stellen bekommen einen null-Wert (kein
  // Balken, keine erfundene "0"). Tooltips lassen null-Zeilen weg.
  // Gleiche Funktion in chart_editor.js.
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
  // Punkte in Bucket 0 fallen. Gleiche Funktion in chart_editor.js.
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
  // Gleiche Funktion in chart_editor.js.
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
      const values = groups.get(bucket) || [];
      if (Number.isFinite(point.value)) values.push(point.value);
      groups.set(bucket, values);
    });
    return Array.from(groups.entries()).sort((a, b) => a[0] - b[0]).map(([bucket, values]) => ({
      ts: anchor + bucket * seconds,
      value: aggregationType === 'standard'
        ? values.reduce((sum, value) => sum + value, 0) / values.length
        : values.reduce((sum, value) => sum + value, 0),
    })).filter(point => Number.isFinite(point.value));
  }

  const instances = new Map();  // chartId -> echarts instance
  // Einmal für die ganze Seite statt in setup(): das läuft nach jedem
  // htmx-Austausch des Dashboard-Gitters erneut, und jeder Lauf legte einen
  // weiteren Listener an — nach n Austauschen n Resize-Durchläufe je Ereignis.
  // Die Sammlung wird beim Austausch geleert und neu befüllt, der eine
  // Listener sieht so immer die aktuellen Instanzen.
  window.addEventListener('resize', () => instances.forEach(c => c.resize()));
  // chartId -> {items, legendMetrics, showStats} — die zuletzt geladenen und
  // berechneten Legenden-Kennzahlen einer Chart-Kachel, unabhängig vom
  // aktuellen Sichtbarkeitszustand der Legende zwischengespeichert. Ein
  // Größenwechsel (Kachelmenü) kann die Legende so ohne erneuten API-Aufruf
  // ein-/ausblenden, siehe setupSizePickers().
  const legendCache = new Map();
  // chartId -> Set<seriesName> — welche Serien über die Kachel-Legende
  // ausgeblendet wurden (Klick auf einen Chip, siehe setupLegendToggles()).
  // Bleibt über erneutes Rendern hinweg erhalten (Größenwechsel, Resize),
  // damit eine einmal ausgeblendete Serie nicht bei jedem Neuladen wieder
  // auftaucht — dieselbe Idee wie legendHiddenIds in chart_editor.html.
  const legendHidden = new Map();
  let observer = null;
  // Bereits einmal gerenderte Kacheln (siehe observer-Callback in setup())
  // — Grundlage für den periodischen Auto-Refresh unten, damit der nicht
  // Kacheln anfasst, die noch nie geladen wurden (Platzhalter außerhalb des
  // Sichtbereichs beim ersten Laden der Seite).
  const renderedTiles = new Set();
  // Stichtag der Zeitraum-Navigation (dashboard-period-nav.js, nur wenn am
  // Dashboard eingeschaltet), null = heute. Der Server rechnet ihn je Kachel
  // in deren Periode um (chart_period.stichtag_window()).
  const stichtag = () => window.DashboardStichtag || null;
  let refreshTimer = null;
  // 60s — schneller als sinnvoll wäre unnötige Serverlast für Daten, die sich
  // i. d. R. erst nach mehreren Minuten sichtbar ändern; deutlich langsamer
  // ließe "manuell aktualisieren" wirken, obwohl es das gar nicht mehr
  // bräuchte. Nur während der Tab sichtbar ist (siehe setupAutoRefresh).
  const DASHBOARD_REFRESH_INTERVAL_MS = 60000;

  // Jede Datumsformatierung in dieser Datei läuft über Intl mit dieser einen Konstante;
  // das Format kommt aus der Einstellung (window.ZA_FORMAT, siehe i18n.js).
  const LOCALE = window.ZA_FORMAT;

  function fmtAxis(range, ts) {
    const d = new Date(ts * 1000);
    if (range === 'hour' || range === 'day') return d.toLocaleTimeString(LOCALE, window.ServerTime.withZone({hour: '2-digit', minute: '2-digit'}));
    if (range === 'week' || range === 'month') return d.toLocaleDateString(LOCALE, window.ServerTime.withZone({day: '2-digit', month: '2-digit'}));
    if (range === 'decade') return d.toLocaleDateString(LOCALE, window.ServerTime.withZone({year: 'numeric'}));
    return d.toLocaleDateString(LOCALE, window.ServerTime.withZone({month: 'short', year: 'numeric'}));
  }

  // Median-Abstand aufeinanderfolgender Zeitstempel — dieselbe Funktion wie in
  // chart_editor.html/entity_detail.html, hier für den Tooltip-Zeitstempel unten.
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
  // bildung wie renderTileTimeline() beim Zeichnen der Zeitstrahl-Balken,
  // hier nur für die Legenden-Summe statt fürs Rendering. Identisch zu
  // switchOnDuration() in chart_editor.html/entity_detail.html.
  function switchOnDuration(points, windowEnd) {
    let total = 0;
    for (let i = 0; i < points.length; i++) {
      const start = points[i].ts;
      const end = i + 1 < points.length ? points[i + 1].ts : (windowEnd ?? start);
      if (points[i].value >= 0.5 && end > start) total += end - start;
    }
    return total;
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
      return d.toLocaleString(LOCALE, window.ServerTime.withZone({day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}));
    }
    if (bucketSeconds < 86400 * 25) {
      const weekday = d.toLocaleDateString(LOCALE, window.ServerTime.withZone({weekday: 'short'})).replace(/\.$/, '');
      const date = d.toLocaleDateString(LOCALE, window.ServerTime.withZone({day: '2-digit', month: '2-digit', year: 'numeric'}));
      return `${weekday}, ${date}`;
    }
    if (bucketSeconds < 86400 * 200) {
      return d.toLocaleDateString(LOCALE, window.ServerTime.withZone({month: 'long', year: 'numeric'}));
    }
    return d.toLocaleDateString(LOCALE, window.ServerTime.withZone({year: 'numeric'}));
  }

  // "Ø"/"Σ" statt Text — dieselben Symbole wie chart_editor.js/entity_detail.js
  // (LEGEND_METRIC_OPTIONS dort), nur "Ø" stand hier schon vorher so; t("Summe")
  // war bis jetzt der einzige verbliebene Text-Ausreißer.
  const LEGEND_METRIC_LABELS = {last: t('Aktuell'), min: t('Min'), max: t('Max'), average: 'Ø', sum: 'Σ'};

  // Chip-Variante (Standard) — dieselben Chips/Kennzahlen wie chart_editor.html
  // (.chart-legend-item aus app.css), hier zusätzlich klickbar (siehe
  // toggleTileLegendItem()), anders als die "static" (nicht umschaltbare)
  // Variante auf entity_detail.html.
  // Punkt wie bisher, gestrichelt für die Ø-Linie der Langzeit-Kachel (wie
  // auf der Chart-Seite).
  function legendSwatch(item) {
    return item.dash
      ? `<span class="dash" style="border-color:${item.color};"></span>`
      : `<span class="dot" style="background:${item.color};"></span>`;
  }

  // Zustands-Entitäten (ROADMAP 1.29): statt Min/Max über Indizes die
  // Zustände der Zeile — Farbe, Name und (mit Statistik) Dauer · Anteil.
  function stateLegendValues(item, showStats) {
    return `<span class="values state-legend-values">${item.states.map(row =>
      `<span title="${escapeHtml(row.raw)}"><span class="dot state-dot" style="${row.swatch}"></span> ${escapeHtml(row.label)}`
      + (showStats ? ` <strong>${escapeHtml(row.text)}</strong>` : '') + '</span>').join('')}</span>`;
  }

  function renderLegendChips(items, legendMetrics, showStats, hiddenSet) {
    return items.map(item => {
      // "+N Jahre" (Langzeit-Kachel): nur ein Hinweis, kein Serien-Umschalter.
      if (item.more) return `<span class="chart-legend-item is-more">${escapeHtml(item.more)}</span>`;
      if (item.states) {
        return `<span class="chart-legend-item static"><span class="name">${escapeHtml(item.name)}</span>`
             + `${stateLegendValues(item, showStats)}</span>`;
      }
      let values = '';
      if (showStats) {
        const parts = [];
        for (const key of ['last', 'min', 'max', 'average', 'sum']) {
          if (!legendMetrics.includes(key)) continue;
          if (key === 'sum' && item.sum === null) continue;
          parts.push(`<span>${LEGEND_METRIC_LABELS[key]} <strong>${escapeHtml(item[key])}</strong></span>`);
        }
        if (parts.length) values = `<span class="values">${parts.join('')}</span>`;
      }
      const inactive = hiddenSet.has(item.name) ? ' inactive' : '';
      return `<span class="chart-legend-item${inactive}" data-series="${escapeHtml(item.name)}" role="button" tabindex="0">`
           + legendSwatch(item)
           + `<span class="name">${escapeHtml(item.name)}</span>${values}</span>`;
    }).join('');
  }

  // Tabellen-Variante ("Legenden-Stil": Tabelle, Optionen-Menü der Chart-Seite)
  // — dieselbe Spalten-Darstellung wie .chart-legend-table in chart_editor.html
  // (table.dt compact). Zeilen sind wie die Chips klickbare Serien-Umschalter
  // (dieselbe .chart-legend-table-row-Klasse/CSS wie dort, siehe app.css).
  function renderLegendTable(items, legendMetrics, showStats, hiddenSet) {
    const cols = showStats ? ['last', 'min', 'max', 'average', 'sum'].filter(k => legendMetrics.includes(k)) : [];
    const headerCells = cols.map(key => `<th>${LEGEND_METRIC_LABELS[key]}</th>`).join('');
    const rows = items.filter(item => !item.more).map(item => {
      const cells = cols.map(key => `<td>${escapeHtml(key === 'sum' && item.sum === null ? '—' : item[key])}</td>`).join('');
      const inactive = hiddenSet.has(item.name) ? ' inactive' : '';
      if (item.states) {
        return `<tr class="chart-legend-table-row static"><td class="legend-name-col"><span class="name">${escapeHtml(item.name)}</span>`
             + `${stateLegendValues(item, showStats)}</td>${cells}</tr>`;
      }
      return `<tr class="chart-legend-table-row${inactive}" data-series="${escapeHtml(item.name)}" role="button" tabindex="0">`
           + `<td class="legend-name-col"><span class="legend-name-cell">`
           + legendSwatch(item)
           + `<span class="name">${escapeHtml(item.name)}</span></span></td>${cells}</tr>`;
    }).join('');
    return `<div class="tbl-wrap"><table class="dt compact chart-legend-table">`
         + `<thead><tr><th class="legend-name-col">Name</th>${headerCells}</tr></thead>`
         + `<tbody>${rows}</tbody></table></div>`;
  }

  // el = die .dtile-body (Chart-Kachel-Link), legend = {items, legendMetrics,
  // showStats, style} — siehe Aufbau in renderTile(). Zeigt/versteckt UND
  // befüllt die .dtile-legend-Zeile — Kachelmenü-Toggle UND Größenwechsel
  // (setupSizePickers) rufen das mit demselben, aus legendCache
  // wiederverwendeten Objekt auf, ohne neu zu laden.
  //
  // Aussehen UND Inhalte 1:1 von der Chart-Seite übernommen, inklusive des
  // dort gewählten Legenden-Stils (Chips/Tabelle) — beide Varianten sind
  // klickbare Serien-Umschalter (siehe setupLegendToggles()).
  function renderLegend(el, legend, visible) {
    const legendEl = el.querySelector('.dtile-legend');
    if (!legendEl) return;
    legendEl.classList.toggle('is-visible', visible);
    if (!visible || !legend) { legendEl.innerHTML = ''; return; }
    const {items, legendMetrics, showStats, style} = legend;
    const chartId = el.closest('.dtile')?.dataset.itemId;
    const hiddenSet = legendHidden.get(chartId) || new Set();
    legendEl.innerHTML = style === 'table'
      ? renderLegendTable(items, legendMetrics, showStats, hiddenSet)
      : renderLegendChips(items, legendMetrics, showStats, hiddenSet);
  }

  // Klick/Enter/Leertaste auf einen Legenden-Chip ODER eine Tabellenzeile
  // blendet die zugehörige Serie im Chart ein/aus, statt (wie der Rest der
  // Kachel) zur Chart-Seite zu navigieren — dieselbe Aktion wie
  // toggleLegendItem() in chart_editor.html, hier über dispatchAction auf die
  // (unsichtbare, siehe legend:{show:false} in renderTile()) ECharts-eigene
  // Legendenauswahl. preventDefault/stopPropagation laufen für JEDEN Klick
  // innerhalb der Legende, auch daneben (leerer Bereich) — sonst würde ein
  // Klick dort zur Chart-Seite navigieren, weil die Legende immer innerhalb
  // der Kachel-<a> liegt. Eine Delegation pro Kachel statt pro Chip/Zeile:
  // .dtile-legend wird bei jedem Neurendern nur per innerHTML ersetzt, das
  // Element selbst (und damit sein Listener) bleibt erhalten — einmaliges
  // Verdrahten in setupLegendToggles() reicht.
  function toggleTileLegendItem(legendEl) {
    return (e) => {
      e.preventDefault();
      e.stopPropagation();
      const item = e.target.closest('.chart-legend-item, .chart-legend-table-row');
      if (!item || !legendEl.contains(item)) return;
      const tile = legendEl.closest('.dtile');
      const chartId = tile?.dataset.itemId;
      const seriesName = item.dataset.series;
      const chart = instances.get(chartId);
      if (!chartId || !seriesName || !chart) return;
      let hiddenSet = legendHidden.get(chartId);
      if (!hiddenSet) { hiddenSet = new Set(); legendHidden.set(chartId, hiddenSet); }
      if (hiddenSet.has(seriesName)) hiddenSet.delete(seriesName); else hiddenSet.add(seriesName);
      item.classList.toggle('inactive', hiddenSet.has(seriesName));
      chart.dispatchAction({type: 'legendToggleSelect', name: seriesName});
    };
  }

  // Hover auf eine Legendenzeile hebt bei einer Donut-Kachel den zugehörigen
  // Slice hervor — dieselbe highlight/showTip- bzw. downplay/hideTip-Aktion
  // wie in chart_editor.js (highlightDonutSlice()/unhighlightDonutSlice(),
  // dort mit derselben Begründung: #storage-pie/.edash-share-donut machen es
  // genauso). Nur bei chartType 'donut' aktiv — bei Linie/Balken hätte
  // seriesIndex:0 kombiniert mit einem Datenpunkt-Namen keine sinnvolle
  // Entsprechung (dort ist jede Serie, nicht jeder Datenpunkt, ein
  // Legendeneintrag).
  function hoverTileLegendItem(legendEl, entering) {
    return (e) => {
      const item = e.target.closest('.chart-legend-item, .chart-legend-table-row');
      if (!item || !legendEl.contains(item)) return;
      const tile = legendEl.closest('.dtile');
      if (tile?.querySelector('.dtile-body')?.dataset.chartType !== 'donut') return;
      const chartId = tile?.dataset.itemId;
      const seriesName = item.dataset.series;
      const chart = instances.get(chartId);
      if (!chartId || !seriesName || !chart) return;
      if (entering) {
        chart.dispatchAction({type: 'highlight', seriesIndex: 0, name: seriesName});
        chart.dispatchAction({type: 'showTip', seriesIndex: 0, name: seriesName});
      } else {
        chart.dispatchAction({type: 'downplay', seriesIndex: 0, name: seriesName});
        chart.dispatchAction({type: 'hideTip'});
      }
    };
  }

  function setupLegendToggles() {
    document.querySelectorAll('.dtile-legend').forEach(legendEl => {
      if (legendEl.dataset.toggleBound) return;
      legendEl.dataset.toggleBound = 'true';
      const handler = toggleTileLegendItem(legendEl);
      legendEl.addEventListener('click', handler);
      legendEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') handler(e);
      });
      legendEl.addEventListener('mouseover', hoverTileLegendItem(legendEl, true));
      legendEl.addEventListener('mouseout', hoverTileLegendItem(legendEl, false));
    });
  }

  async function renderTile(el) {
    // item-id sitzt auf der äußeren .dtile (auch Drag&Drop-Handle, siehe
    // setupDragAndDrop), die restlichen Daten auf dem inneren Link selbst.
    const chartId = el.closest('.dtile').dataset.itemId;
    // Volle Auswahl UND die ausgeblendeten getrennt: abgefragt und gezeichnet
    // werden nur die sichtbaren, die Farbe hängt aber an der Position in der
    // vollen Auswahl (colorIndexFor unten) — sonst wäre dieselbe Serie auf der
    // Chart-Seite und auf der Kachel unterschiedlich eingefärbt, sobald eine
    // Serie ausgeblendet ist. Gleiche Regel wie colorIndexFor() in
    // pages/chart_editor.js.
    const allEntityIds = JSON.parse(el.dataset.entityIds || '[]');
    const hiddenEntityIds = JSON.parse(el.dataset.hiddenEntityIds || '[]');
    const entityIds = allEntityIds.filter(id => !hiddenEntityIds.includes(id));
    const colorIndexFor = (entityId) => {
      const idx = allEntityIds.indexOf(entityId);
      return idx === -1 ? 0 : idx;
    };
    // Eigene Farbe je Serie (saved_charts.series_colors, "palette:N" oder
    // "#rrggbb"), sonst Palettenfarbe nach Position. Gleiche Regel wie
    // colorFor() in pages/chart_editor.js.
    let seriesColors = {};
    try { seriesColors = JSON.parse(el.dataset.seriesColors || '{}') || {}; } catch (e) { seriesColors = {}; }
    const colorFor = (entityId) => {
      const own = seriesColors[entityId];
      if (typeof own === 'string') {
        if (own.startsWith('palette:') && PALETTE[+own.slice(8)]) return PALETTE[+own.slice(8)];
        if (/^#[0-9a-f]{6}$/i.test(own)) return own;
      }
      return PALETTE[colorIndexFor(entityId) % PALETTE.length];
    };
    const entityNames = JSON.parse(el.dataset.entityNames || '{}');
    const range = el.dataset.range || 'day';
    const continuous = el.dataset.continuous === 'true';
    const resolutionPreset = el.dataset.resolutionPreset || 'auto';
    // Siehe chart_editor.html (render(), dynamicYAxis-Konstante): bei
    // Auflösung t("Tag") (singleBucket weiter unten) erzwungen aus, unabhängig
    // vom gespeicherten Chart-Zustand — eine nicht bei 0 startende Y-Achse
    // würde den Summen-Vergleich der wenigen Balken verzerren.
    const dynamicYAxis = el.dataset.dynamicYAxis === 'true' && resolutionPreset !== 'full';
    const animation = el.dataset.animation !== 'false';
    // Wie chart_editor.html: Zeitstrahl braucht immer echte Rohübergänge,
    // nie Bucket-Werte (siehe dortiger Kommentar bei setRange()/load() —
    // Bucket-Werte würden als fälschlich durchgehende AN-Intervalle statt
    // echter Übergänge gezeichnet).
    const chartType = el.dataset.chartType || 'auto';
    const timeline = chartType === 'timeline';
    // "Darstellungsart" Donut (Optionen-Menü der Chart-Seite) — Kachel-
    // Parität zu chart_editor.js renderDonut(), siehe donut-Zweig weiter
    // unten und renderTileDonut().
    const donut = chartType === 'donut';
    const donutAggregation = el.dataset.donutAggregation || 'sum';
    // Nachkommastellen-Override (Optionen-Menü, "Darstellung") — bei "Auto"
    // behält jede Serie ihre eigene entities.decimals-Einstellung (s.decimals,
    // vom Server je Entität geliefert), sonst gilt dieser Wert für ALLE Serien
    // dieser Kachel einheitlich, genau wie in chart_editor.html (effectiveDecimals()).
    const decimalsOverride = el.dataset.decimals || 'auto';
    const effectiveDecimals = s => decimalsOverride === 'auto' ? s.decimals : parseInt(decimalsOverride, 10);
    // "Werte anzeigen" (Optionen-Menü) — bislang nicht an die Dashboard-Kachel
    // durchgereicht, siehe showValues-Verwendung im echartsSeries-Aufbau unten.
    const showValues = el.dataset.showValues === 'true';
    // "Durchschnittslinie" (Optionen-Menü der Chart-Seite) — die Kachel
    // zeichnet sie mit, damit ein angeheftetes Chart nicht anders aussieht als
    // dasselbe Chart auf seiner eigenen Seite.
    const averageLine = el.dataset.averageLine === 'true';
    // "Flach"/"Gleitend" (verschachtelt unter "Durchschnittslinie" im
    // Chart-Editor) — dieselbe Kachel-Parität wie averageLine selbst.
    const averageStyle = el.dataset.averageStyle || 'flat';
    // "Fläche" (Optionen-Menü der Chart-Seite, chart_editor.js) — dieselbe
    // dezente Füllfläche wie auf der eigenen Chart-Seite, damit ein
    // angeheftetes Chart nicht anders aussieht als dasselbe Chart dort.
    const areaFill = el.dataset.areaFill !== 'false';
    const showPoints = el.dataset.showPoints === 'true';
    // "Gestapelt"/"Anteile (%)" (Optionen-Menü der Chart-Seite) — dieselbe
    // stacked-Kachel-Vorschau wie auf der eigenen Chart-Seite, siehe
    // stackedActive/normalizeActive weiter unten (chart_editor.js-Parität).
    const stacked = el.dataset.stacked === 'true';
    const normalize = el.dataset.normalize === 'true';
    // "Ausrichtung" (Optionen-Menü, nur bei Auflösung "Voll") — siehe
    // horizontalActive weiter unten (chart_editor.js-Parität).
    const horizontal = el.dataset.horizontal === 'true';
    // Gezeichneter Zustand je Zustands-Entität der Heatmap (saved_charts.heatmap_states).
    let heatmapStates = {};
    try { heatmapStates = JSON.parse(el.dataset.heatmapStates || '{}') || {}; } catch (e) { heatmapStates = {}; }
    const chartEl = el.querySelector('.dtile-chart');
    if (!chartEl || !entityIds.length) return;

    const base = el.closest('#dashboard-grid')?.dataset.appRoot || '';
    // Fester Zeitraum des Charts (saved_charts.period_anchor, JJJJ-MM-TT):
    // dieselbe Umrechnung in einen Versatz wie im Chart-Editor, damit Kachel
    // und Chart-Seite dieselbe Periode zeigen. Ohne Anker die laufende.
    let offset = 0;
    if (el.dataset.periodAnchor) {
      const [y, m, d] = el.dataset.periodAnchor.split('-').map(Number);
      offset = PeriodNavigation.offsetForRange(range, new Date(y, m - 1, d, 12).getTime());
    }
    const params = new URLSearchParams({
      entity_ids: entityIds.join(','), range, offset: String(offset), continuous: String(continuous),
      raw: String(timeline),
    });
    // Ein fester Zeitraum bleibt fest, der Stichtag gilt nur für die übrigen.
    if (!el.dataset.periodAnchor && stichtag()) params.set('at', stichtag());
    const compare = tileCompare(el, {range, continuous, entityIds, chartType, resolutionPreset, stacked});
    if (compare) {
      params.set('compare', 'true');
      params.set('compare_mode', compare.mode);
      if (compare.longterm) params.set('previous_years', String(compare.years));
    }
    let data;
    try {
      // Heatmap: zusätzlich die Zellen (Wochentag x Stunde), wie im Chart-Editor.
      // Zu kurze Zeiträume fallen unten auf den Standard zurück.
      const wantHeatmap = chartType === 'heatmap' && StateEntities.SERVER_SHARE_RANGES.includes(range);
      const heatmapParams = new URLSearchParams(params);
      heatmapParams.set('heatmap', 'true');
      heatmapParams.delete('compare');
      // Zeitstrahl in Jahr/Dekade: zusätzlich Spalten vom Server, so breit wie die Kachel,
      // wie im Chart-Editor. Sie ersetzen die Rohabschnitte, wenn deren Abfrage abbricht.
      const wantColumns = timeline && StateEntities.COLUMN_RANGES.includes(range);
      const columnsParams = new URLSearchParams(params);
      columnsParams.set('columns', 'true');
      columnsParams.delete('compare');
      columnsParams.set('px', String(Math.max(60, Math.round(chartEl.clientWidth - 10))));
      const [res, heatmapRes, columnsRes] = await Promise.all([
        fetch(`${base}/api/query-multi?${params}`),
        wantHeatmap ? fetch(`${base}/api/query-multi?${heatmapParams}`) : Promise.resolve(null),
        wantColumns ? fetch(`${base}/api/query-multi?${columnsParams}`) : Promise.resolve(null),
      ]);
      const columnsData = columnsRes && columnsRes.ok ? await columnsRes.json() : null;
      data = res.ok ? await res.json() : (columnsData || {});
      const heatmapData = heatmapRes ? await heatmapRes.json() : null;
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
    } catch (e) {
      chartEl.innerHTML = '<div class="dtile-loading">' + t('Fehler beim Laden') + '</div>';
      return;
    }
    // Zustände lassen sich nur unter sich darstellen (wie im Chart-Editor):
    // steht etwas anderes im Chart, fallen sie heraus.
    const loaded = data.series || [];
    const foreign = loaded.some(s => s.aggregation_type && s.aggregation_type !== 'state');
    const series = foreign ? loaded.filter(s => s.aggregation_type !== 'state') : loaded;
    if (!series.some(s => (s.points && s.points.length) || (s.columns && s.columns.length))) {
      chartEl.innerHTML = '<div class="dtile-loading">' + t('Keine Daten') + '</div>';
      return;
    }
    // Nur Zustände und keine Ansicht gewählt: je Zeitraum der Standard, wie im
    // Chart-Editor (Zeitstrahl bis zur Woche, danach Anteile).
    const heatmapTooShort = chartType === 'heatmap' && !StateEntities.SERVER_SHARE_RANGES.includes(range);
    const viewType = (chartType === 'auto' || heatmapTooShort) && series.every(s => s.aggregation_type === 'state')
      ? StateEntities.defaultMode(range) : chartType;
    // Dieselbe Farbzuordnung wie echartsSeries weiter unten (PALETTE nach
    // Serienindex) UND dieselbe Kennzahlen-Berechnung wie seriesStats() in
    // chart_editor.html (Min/Max/Ø/Summe/Aktuell) — hier separat gebaut,
    // damit die Legende VOR echarts.init() im DOM steht: der Chart-Container
    // bekommt sonst beim ersten Rendern die volle (noch legendenlose)
    // Kachelhöhe gemessen und die Legende schiebt ihn erst beim nächsten
    // Resize-Event auf seine endgültige Höhe.
    const statsItem = (s, points, name, color) => {
      if (s.aggregation_type === 'state') {
        return {name, color, states: stateRows(s, data.window_end), last: '—', min: '—', max: '—', average: '—', sum: null};
      }
      const values = (points || []).map(p => p.value).filter(Number.isFinite);
      const minima = (points || []).map(p => Number.isFinite(p.min) ? p.min : p.value).filter(Number.isFinite);
      const maxima = (points || []).map(p => Number.isFinite(p.max) ? p.max : p.value).filter(Number.isFinite);
      const isDuration = s.aggregation_type === 'switch' && s.display_mode === 'time';
      const unit = s.unit ? ` ${s.unit}` : '';
      const formatted = value => isDuration ? NumberFormat.fmtDuration(value) : `${fmtCompactNumber(value, effectiveDecimals(s))}${unit}`;
      // Summe bei Zählern UND bei Schaltern sinnvoll (Bucket-Werte sind dort
      // bereits Einschaltsekunden) — siehe derselbe Kommentar in
      // chart_editor.html (seriesStats()). Im Zeitstrahl-/Rohwerte-Modus
      // (raw=true, s. o.) kommt die Summe stattdessen aus switchOnDuration()
      // unten, da die Punktwerte dann nur noch 0/1-Zustände sind.
      const isSwitch = s.aggregation_type === 'switch';
      const hasSum = s.aggregation_type === 'counter' || isSwitch;
      const sumValue = timeline && isSwitch ? switchOnDuration(points, data.window_end) : values.reduce((sum, v) => sum + v, 0);
      // Summe bei Schaltern ist immer eine Dauer in Sekunden — unabhängig
      // vom Anzeigemodus immer als h/m/s formatiert statt über
      // fmtCompactNumber()s generische 4-signifikante-Stellen-Rundung, die
      // bei größeren Sekundenwerten sichtbar ungenau wird (siehe
      // chart_editor.html/entity_detail.html, derselbe Fix).
      const sumFormatted = isSwitch ? NumberFormat.fmtDuration(sumValue) : formatted(sumValue);
      return {
        name,
        color,
        last: values.length ? formatted(values[values.length - 1]) : '—',
        min: minima.length ? formatted(Math.min(...minima)) : '—',
        max: maxima.length ? formatted(Math.max(...maxima)) : '—',
        average: values.length ? formatted(values.reduce((sum, v) => sum + v, 0) / values.length) : '—',
        sum: hasSum ? (values.length ? sumFormatted : '—') : null,
      };
    };
    const tileEl = el.closest('.dtile');
    // Langzeit-Kachel: Größenwechsel über 1×1 hinweg zeichnet neu (siehe
    // setupSizePickers()), weil 1×1 nur das angezeigte Jahr und den Ø zeigt.
    const multiYear = compare?.longterm && series.length === 1 && Array.isArray(series[0].years)
      ? tileMultiYear(series[0], data, compare, {
        small: tileEl?.dataset.gridCols === '1' && tileEl?.dataset.gridRows === '1',
        color: colorFor(series[0].entity_id),
        inkMuted: getComputedStyle(document.body).getPropertyValue('--ink-muted').trim(),
      })
      : null;
    el.dataset.longtermActive = multiYear ? 'true' : 'false';
    let legendItems;
    if (multiYear) {
      // Entschieden: nur das angezeigte Jahr und der Ø, die übrigen Jahre als
      // "+N Jahre" (die Tabellen-Legende zeigt nur die beiden Zeilen).
      const s = series[0];
      legendItems = [statsItem(s, s.points, multiYear.series[0].name, multiYear.series[0].color)];
      if (multiYear.averageSeries) {
        legendItems.push({
          ...statsItem(s, multiYear.averageSeries.monthly.filter(v => v != null).map(v => ({value: v})),
            multiYear.averageSeries.name, multiYear.averageSeries.color),
          dash: true,
        });
      }
      if (multiYear.hiddenYears > 0) legendItems.push({more: t('+{n} Jahre', {n: multiYear.hiddenYears})});
    } else if (compare?.yearsCompare) {
      // Wie die Balkenpaare: je Entität angezeigtes Jahr und Vorjahr, die
      // Soll-Entität als eigene Zeile — Namen wie die Serien, damit das
      // Umschalten per Legende greift.
      const currentYear = data.window_start ? new Date(data.window_start * 1000).getFullYear() : new Date().getFullYear();
      legendItems = [];
      series.forEach(s => {
        if (!s.compare_points) return;
        const color = colorFor(s.entity_id);
        const displayName = entityNames[s.entity_id] || s.friendly_name;
        if (s.entity_id === compare.target) { legendItems.push(statsItem(s, s.points, displayName, color)); return; }
        legendItems.push(statsItem(s, s.points, `${displayName} (${currentYear})`, color));
        legendItems.push(statsItem(s, s.compare_points, `${displayName} (${currentYear - 1})`, withOpacity(color, 0.55)));
      });
    } else {
      legendItems = series.map(s => statsItem(
        s, s.points, entityNames[s.entity_id] || s.friendly_name, colorFor(s.entity_id)
      ));
    }
    const legendMetrics = JSON.parse(el.dataset.legendMetrics || '["sum"]');
    const showStats = el.dataset.chartStats === 'true';
    const legendStyle = el.dataset.legendStyle || 'chips';
    const legend = {items: legendItems, legendMetrics, showStats, style: legendStyle};
    legendCache.set(chartId, legend);
    const legendVisible = el.dataset.showLegend === 'true'
      && parseInt(tileEl?.dataset.gridCols || '1', 10) >= 2
      && parseInt(tileEl?.dataset.gridRows || '1', 10) >= 2;
    renderLegend(el, legend, legendVisible);

    let chart = instances.get(chartId);
    // Beim Neuzeichnen (Auto-Refresh, Stichtag der Zeitraum-Navigation) wird
    // die Instanz weiterverwendet — dann darf chartEl NICHT geleert werden:
    // das entfernte ihr Canvas, und setOption() zeichnete danach ins Leere
    // (Kachel blieb leer, Legende darunter voll). Geleert und neu gebunden
    // wird nur ohne intakte Instanz, z. B. nach einem "Keine Daten"-
    // Platzhalter, der das Canvas ersetzt hat.
    if (chart && chart.getDom() === chartEl && !chartEl.querySelector('canvas, svg')) {
      chart.dispose();
      chart = null;
    }
    // Ein htmx-Swap von #dashboard-grid (Pin/Unpin/Reorder) ersetzt chartEl
    // durch einen KOMPLETT NEUEN DOM-Knoten, während instances noch die alte
    // ECharts-Instanz unter derselben chartId hält — die zeigt dann auf ein
    // bereits entferntes Canvas. Ohne dispose() hier bliebe diese alte
    // Instanz (inkl. ihres via appendToBody an document.body gehängten
    // Tooltip-Elements, siehe tooltip weiter unten) unsichtbar für immer
    // bestehen, statt neu an den aktuellen Container gebunden zu werden —
    // sichtbar als sich über die Zeit ansammelnde verwaiste Tooltips.
    if (chart && chart.getDom() !== chartEl) {
      chart.dispose();
      chart = null;
    }
    if (!chart) {
      chartEl.innerHTML = '';
      chart = echarts.init(chartEl);
      instances.set(chartId, chart);
    }
    // Canvas kennt keine CSS-Variablen — anders als im übrigen CSS dieser
    // Seite müssen Farben hier erst über getComputedStyle in konkrete Werte
    // aufgelöst werden, sonst zeichnet ECharts den Literal-String
    // "var(--x)" gar nicht erst. Bewusst aus dem CSS gelesen statt fest
    // verdrahtet, damit die Kachel im Dark Mode mitzieht wie der Rest der App.
    const style = getComputedStyle(document.body);
    const borderColor = style.getPropertyValue('--border').trim();
    const inkFaint = style.getPropertyValue('--ink-faint').trim();
    const inkMuted = style.getPropertyValue('--ink-muted').trim();
    const surface = style.getPropertyValue('--surface').trim();

    // Zeitstrahl statt Linie/Balken — eigener, kompakter Renderpfad (analog
    // renderTimelineMulti() in chart_editor.html), springt hier komplett aus
    // der Linie-/Balken-Aufbereitung unten heraus, die bei AN/AUS-Intervallen
    // ohnehin keinen Sinn ergäbe.
    if (multiYear) {
      renderTileMultiYear(chart, multiYear, series[0], {
        effectiveDecimals, animation, style, borderColor, inkFaint, inkMuted, surface,
        hiddenSet: legendHidden.get(chartId),
      });
      return;
    }
    if (compare?.yearsCompare) {
      renderTileYearsCompare(chart, series, compare, data, {
        entityNames, colorFor, effectiveDecimals, animation, style, borderColor, inkFaint, inkMuted, surface,
        hiddenSet: legendHidden.get(chartId),
      });
      return;
    }
    if (viewType === 'heatmap' && series.some(s => s.aggregation_type === 'state')) {
      renderTileStateHeatmap(chart, series, entityNames, animation, heatmapStates);
      return;
    }
    if (['shares', 'changes'].includes(viewType) && series.some(s => s.aggregation_type === 'state')) {
      renderTileStateBars(chart, series, entityNames, data, range, viewType, animation);
      return;
    }
    if (timeline || viewType === 'timeline') {
      renderTileTimeline(chart, series, entityNames, data, range, animation, style, borderColor, inkFaint, surface, colorFor);
      return;
    }
    if (donut && series.some(s => s.aggregation_type === 'state')) {
      renderTileStateDonuts(chart, series, entityNames, data.window_end, style, surface);
      return;
    }
    if (donut) {
      renderTileDonut(chart, series, entityNames, colorFor, donutAggregation, effectiveDecimals, style, surface, chartId);
      return;
    }

    // Eine Y-Achse je unterschiedlicher Einheit (genau wie auf der vollen
    // Chart-Seite, chart_editor.html) — ohne das teilen sich z. B. Watt- und
    // kWh-Werte dieselbe Achse, wodurch die kWh-Balken bei einer viel
    // größeren Watt-Spanne rechnerisch bei ~0 verschwinden, statt sichtbar zu
    // sein. Kompakt gehalten: kein Achsenname, nur die Zahl.
    // Schalter-Entitäten im Anzeigemodus "Zeit" (Dauer statt Rohsekunden)
    // bekommen wie in chart_editor.html einen eigenen synthetischen Achsen-
    // Schlüssel statt ihrer echten (meist leeren) unit — sonst teilten sie
    // sich fälschlich eine Achse mit unitlosen Standard-Entitäten und
    // erschienen dort in rohen Sekunden statt als Dauer (Achse UND Tooltip
    // kannten den Anzeigemodus bisher gar nicht, nur die HTML-Legende oben).
    const isDurationSeries = s => s.aggregation_type === 'switch' && s.display_mode === 'time';
    const axisKey = s => isDurationSeries(s) ? ' duration' : s.unit;
    const units = [...new Set(series.map(axisKey))];
    // Auflösung "Voll" (Tag/Woche/Monat/Jahr) fasst den kompletten Zeitraum
    // zu genau EINEM Wert je Entität zusammen — ein Ranking-Vergleich, kein
    // Zeitverlauf. Jede Entität bekommt dafür eine eigene Kategorie auf der
    // Achse (ihr Anzeigename), siehe chart_editor.js (gleicher Name/
    // Kommentar dort für die ausführliche Begründung).
    const singleBucket = resolutionPreset === 'full';
    const horizontalActive = singleBucket && horizontal;
    const entityCategories = series.map(s => entityNames[s.entity_id] || s.friendly_name);
    // Wie chart_editor.js: "Gestapelt" nur ab zwei Balken-Serien UND
    // außerhalb von Auflösung "Voll" (dort hat jede Entität schon ihre
    // eigene Kategorie — eine gestapelte Kombination aller Entitäten in
    // EINER Kategorie wäre ein Widerspruch dazu), je Achse ein eigener
    // Stapel-Schlüssel statt eines globalen "total" (eine Kachel kann
    // mehrere Einheiten kombinieren, z. B. kWh und Dauer), und 100%-
    // Normierung nur auf Achsen, auf denen AUSSCHLIESSLICH Balken liegen —
    // eine mitgezeichnete Linie derselben Einheit stünde sonst auf einer
    // 0–100%-Skala, die für die gestapelten Balken gemeint ist.
    const stackedActive = stacked && !singleBucket && series.filter(s => s.chart_type === 'bar').length >= 2;
    const normalizeActive = stackedActive && normalize;
    const barOnlyAxis = new Set(
      units.filter(u => series.every(s => axisKey(s) !== u || s.chart_type === 'bar'))
    );
    // Strengste (kleinste) Nachkommastellen-Einstellung aller Entitäten einer
    // gemeinsamen Achse — dieselbe Regel wie in chart_editor.html.
    const unitDecimals = new Map();
    series.forEach(s => {
      const d = effectiveDecimals(s);
      if (d == null) return;
      const current = unitDecimals.get(axisKey(s));
      if (current == null || d < current) unitDecimals.set(axisKey(s), d);
    });
    const yAxis = units.map((u, i) => {
      const decimals = unitDecimals.get(u);
      const isDuration = u === ' duration';
      // Balken zeichnen immer von der Null-Basislinie zum Wert — liegt eine
      // auto-skalierte Achse (scale:true, min/max undefined) nicht bei 0,
      // ragt der Balken optisch über den unteren Achsenrand hinaus statt
      // sauber auf der x-Achse zu stehen. "Dynamische Y-Achse" gilt deshalb
      // nur für Achsen, auf denen ausschließlich Linien-Serien liegen —
      // je Achse geprüft, da eine Kachel mehrere Einheiten/Achsen mischen kann.
      const axisHasBar = series.some(s => axisKey(s) === u && s.chart_type === 'bar');
      const axisDynamic = dynamicYAxis && !axisHasBar;
      // Prozent-Achse (100%-Normierung) — immer fest 0–100, unabhängig von
      // "Dynamische Y-Achse", siehe chart_editor.js (gleiche Begründung).
      const isPercentAxis = normalizeActive && barOnlyAxis.has(u);
      return {
        type: 'value',
        position: i % 2 === 0 ? 'left' : 'right',
        offset: Math.floor(i / 2) * 46,
        min: isPercentAxis ? 0 : (axisDynamic ? undefined : value => Math.min(0, value.min)),
        max: isPercentAxis ? 100 : (axisDynamic ? undefined : value => Math.max(0, value.max)),
        // Siehe chart_editor.html: ohne scale:true erzwingt ECharts bei einer
        // value-Achse per Default immer die Einbindung der Null, auch bei
        // undefined min/max — "Dynamische Y-Achse" hätte sonst keine
        // sichtbare Wirkung.
        scale: isPercentAxis ? false : axisDynamic,
        axisLabel: {
          fontSize: scaledFont(10), color: inkFaint,
          formatter: v => isPercentAxis ? `${fmtCompactNumber(v, 0)} %` : (isDuration ? NumberFormat.fmtDuration(v) : fmtCompactNumber(v, decimals)),
        },
        axisLine: {show: false},
        axisTick: {show: false},
        splitLine: {lineStyle: {color: borderColor, type: 'dashed'}},
      };
    });
    // "Ausrichtung: Horizontal" tauscht Kategorie- und Werte-Achse — dasselbe
    // Position-Remapping wie chart_editor.js (siehe dortiger Kommentar).
    const xAxisForHorizontal = horizontalActive
      ? yAxis.map((axis, i) => ({...axis, position: i % 2 === 0 ? 'bottom' : 'top'}))
      : null;
    // Kategorie-Achse für singleBucket — Entitätsnamen statt Zeit-Ticks.
    const categoryAxisObj = singleBucket ? {
      type: 'category',
      data: entityCategories,
      axisTick: {show: false},
      axisLine: {show: false, onZero: false},
      axisLabel: {
        fontSize: scaledFont(10), color: inkFaint,
        width: horizontalActive ? 120 : 60,
        overflow: 'truncate',
        rotate: horizontalActive ? 0 : 28,
      },
    } : null;

    // Erste Serie mit genug angezeigten (resamplePoints()-) Punkten bestimmt
    // die Tooltip-Zeitstempel-Form (fmtTooltipTimestamp) — dieselbe Logik wie
    // chart_editor.html. Aus den resampelten, nicht den rohen Server-Punkten:
    // bei manuell gesetzter Auflösung (Kachel-Auflösung ungleich "auto")
    // resamplePoints() clientseitig gröber, die rohen Punkte wären dann
    // feiner als das, was im Tooltip tatsächlich zu sehen ist. Erst innerhalb
    // der Schleife unten gesetzt (dort liegen die resampelten displayPoints
    // vor), hier nur deklariert.
    let tooltipBucketSeconds = null;

    // Erster Durchgang: je Serie die anzuzeigenden Punkte berechnen (wie
    // bisher) — VOR dem eigentlichen Kachel-Aufbau, damit die 100%-
    // Normierung unten die Werte ALLER Serien einer Achse zum selben Bucket
    // kennt, bevor die erste Serie fertig gebaut wird.
    const prepared = series.map((s, i) => {
      // Die Punkte, über die der Durchschnitt geht: das GEZEICHNETE, aber
      // ohne den Halte-Punkt, den der Linien-Zweig unten bis window_end
      // anhängt — der ist eine Wiederholung des letzten Werts und würde
      // ihn doppelt zählen.
      if (singleBucket) {
        // resamplePoints() kennt nur "medium"/"coarse" (RESOLUTION_SECONDS),
        // für "full" gibt sie unverändert alle Rohpunkte zurück — ohne diesen
        // eigenen Zweig würde jeder einzelne Bucket-Punkt als eigener
        // Tooltip-Eintrag auf der eigenen Kategorie (x=i) landen, statt zu
        // einem einzigen Balkenwert für den ganzen Zeitraum zusammengefasst
        // zu werden (sichtbar als lange Dopplung im Tooltip). Dieselbe Summe-
        // vs.-Durchschnitt-Regel wie in den Legenden-Kennzahlen oben.
        const rawValues = (s.points || []).map(p => p.value).filter(Number.isFinite);
        if (!rawValues.length) return {lineData: [], averageValues: [], rawPoints: []};
        const isSumType = s.aggregation_type === 'counter' || s.aggregation_type === 'switch';
        const aggregate = isSumType
          ? rawValues.reduce((sum, v) => sum + v, 0)
          : rawValues.reduce((sum, v) => sum + v, 0) / rawValues.length;
        return {
          // x-Position ist die EIGENE Kategorie dieser Entität (ihre Position
          // in series, s. entityCategories), nicht mehr 0 für alle — seit
          // jede Entität ihre eigene Kategorie bekommt (s. o.).
          lineData: [[i, aggregate, s.unit, effectiveDecimals(s), isDurationSeries(s)]],
          averageValues: [aggregate],
          rawPoints: [{ts: data.window_start, value: aggregate}],
        };
      }
      const displayPoints = resamplePoints(
        s.points, range, resolutionPreset, s.aggregation_type, data.window_start
      );
      if (tooltipBucketSeconds == null) {
        tooltipBucketSeconds = detectResolutionSeconds(displayPoints);
      }
      const lineData = displayPoints.map(p => [p.ts * 1000, p.value, s.unit, effectiveDecimals(s), isDurationSeries(s)]);
      const averageValues = displayPoints.map(p => p.value);
      if (s.chart_type === 'line' && lineData.length && data.window_end != null
          && lineData[lineData.length - 1][0] < data.window_end * 1000) {
        const last = lineData[lineData.length - 1];
        lineData.push([data.window_end * 1000, last[1], last[2], last[3], last[4]]);
      }
      // rawPoints (ohne Halte-Punkt) für den gleitenden Durchschnitt — dieselbe
      // Begründung wie bei averageValues: der Halte-Punkt wiederholt nur den
      // letzten Wert und würde das Fenster am Rand verfälschen.
      return {lineData, averageValues, rawPoints: displayPoints};
    });
    // x (ms-Zeitstempel bzw. 0 bei singleBucket) -> Summe je Achse, nur für
    // tatsächlich normierte reine Balken-Achsen (barOnlyAxis, s. o.).
    const axisTotals = new Map();
    if (normalizeActive) {
      series.forEach((s, i) => {
        const u = axisKey(s);
        if (!barOnlyAxis.has(u)) return;
        if (!axisTotals.has(u)) axisTotals.set(u, new Map());
        const totals = axisTotals.get(u);
        prepared[i].lineData.forEach(p => totals.set(p[0], (totals.get(p[0]) || 0) + p[1]));
      });
    }
    // Letzter Balken-Index je Achse — nur der bekommt beim Stapeln abgerundete
    // obere Ecken (das oberste, sichtbare Ende des Stapels), statt wie im
    // gruppierten Fall JEDES Segment einzeln abzurunden.
    const lastBarIndexForAxis = new Map();
    series.forEach((s, i) => { if (s.chart_type === 'bar') lastBarIndexForAxis.set(axisKey(s), i); });

    // Gesammelt statt direkt in echartsSeries gepusht — series.map() liefert
    // genau ein Element je Durchlauf, die Trendlinie ist aber eine
    // ZUSÄTZLICHE, eigenständige Serie (siehe showRollingAverage unten).
    const rollingSeries = [];
    // Linien- und Balken-Serien, deren Tooltip-Zeile auch ohne eigenen Punkt am
    // Cursor erscheint (tooltip-hold.js, gleiche Regel wie chart_editor.js).
    const holdSeries = [];
    const echartsSeries = series.map((s, i) => {
      const color = colorFor(s.entity_id);
      const displayName = entityNames[s.entity_id] || s.friendly_name;
      const isStackedBar = s.chart_type === 'bar' && stackedActive;
      // 100%-Normierung nur auf reinen Balken-Achsen und nur für Balken-
      // Serien — eine überlagerte Linie derselben Einheit bleibt unverändert
      // in Absolutwerten (barOnlyAxis enthält ihre Achse dann nicht).
      const axisNormalized = normalizeActive && s.chart_type === 'bar' && barOnlyAxis.has(axisKey(s));
      let lineData = prepared[i].lineData;
      if (axisNormalized) {
        const totals = axisTotals.get(axisKey(s));
        // Felder 6/7 (Originaleinheit/-Nachkommastellen) stehen schon an
        // Position 2/3 des Ausgangs-Tupels — direkt übernommen statt erneut
        // nachgeschlagen. Feld 5: der Original-Absolutwert, nur für den
        // Tooltip (siehe formatter unten) — sonst verliert der Tooltip genau
        // die Zahl, die die Normierung aus dem Balken selbst entfernt.
        lineData = lineData.map(p => {
          const total = totals.get(p[0]) || 0;
          const pct = total ? (p[1] / total) * 100 : 0;
          return [p[0], pct, '%', 0, false, p[1], p[2], p[3]];
        });
      }
      // Gleitender Durchschnitt nur für Linien-Serien — dieselbe Begründung
      // wie chart_editor.js (eine Balken-Bucket-SUMME gleitend zu mitteln
      // ergäbe keine klar lesbare Aussage).
      const showRollingAverage = averageLine && !isStackedBar && averageStyle === 'rolling'
        && s.chart_type === 'line' && prepared[i].rawPoints.length >= 3;
      const cfg = {
        // Angepasster Anzeigename (chart_editor.html, "Angezeigte Namen") hat
        // Vorrang vor dem Entität-eigenen friendly_name — dieselbe Regel wie
        // beim Rendern der vollen Chart-Seite (dort this.entityNames[entity_id]).
        name: displayName,
        type: s.chart_type,
        // Horizontal tauschen Kategorie- und Werte-Achse die Plätze (s.
        // xAxisForHorizontal oben) — dieselbe Umkehr wie chart_editor.js.
        xAxisIndex: horizontalActive ? units.indexOf(axisKey(s)) : undefined,
        yAxisIndex: horizontalActive ? 0 : units.indexOf(axisKey(s)),
        data: lineData,
        // ECharts liest data-Tupel bei Balken/Linien sonst POSITIONAL als
        // [x, y] — unabhängig davon, welche Achse gerade Kategorie- bzw.
        // Werte-Achse ist. lineData[0] ist immer der Kategorie-Index, [1] der
        // Wert; horizontal tauschen xAxis/yAxis ihre Rollen (s. o.), also muss
        // ECharts das explizit gesagt werden — sonst interpretiert es den
        // Kategorie-Index als x-Wert und den Messwert als Kategorie-Index
        // (Balken verschwinden, Werte-Achse skaliert auf ~Kategorien-Anzahl).
        encode: horizontalActive ? {x: 1, y: 0} : undefined,
        stack: isStackedBar ? 'bar-' + axisKey(s) : undefined,
        // Bei aktiver Trendlinie tritt die rohe Kurve zurück, bleibt aber
        // sichtbar — dieselbe Ergänzung-statt-Ersatz-Logik wie im Editor.
        lineStyle: {width: 2, color, opacity: showRollingAverage ? 0.45 : 1},
        itemStyle: {color},
        // "Werte anzeigen" (Optionen-Menü) — Zahl direkt über jedem Balken/
        // Punkt, dieselbe Konvention wie entity_detail.html (ohne Einheit,
        // die steht schon an der Y-Achse). Gestapelt liegt das Label
        // INNERHALB des Segments (weiß) statt darüber — siehe chart_editor.js.
        label: {
          show: showValues,
          position: isStackedBar ? 'inside' : (horizontalActive ? 'right' : 'top'),
          fontSize: scaledFont(10),
          color: isStackedBar ? '#fff' : inkMuted,
          formatter: params => params.value[4]
            ? NumberFormat.fmtDuration(params.value[1])
            : fmtCompactNumber(params.value[1], params.value[3]),
        },
        // Gilt jetzt auch für singleBucket: seit jede Entität ihre eigene
        // Kategorie hat, ist das der Normalfall eines Kategorie-Achsen-
        // Balkens, kein Sonderfall mehr (siehe chart_editor.js).
        barMaxWidth: 28,
        // Ein Balken auf einer Zeit-Achse (kein boundaryGap, s. u.) sitzt mit
        // seiner Mitte GENAU auf dem Bucket-Zeitstempel — beim ersten/letzten
        // Bucket liegt die Hälfte der Balkenbreite dadurch zwangsläufig knapp
        // jenseits von min/max und würde ohne dies hart am Kachelrand
        // abgeschnitten wirken. Bei singleBucket (echte Kategorie-Achse mit
        // eigener Bandbreite je Kategorie) entfällt dieses Problem.
        clip: singleBucket ? undefined : s.chart_type !== 'bar',
      };
      if (s.chart_type === 'line') {
        cfg.smooth = true;
        if (!showPoints) cfg.symbol = 'none';
        // Dezente Füllfläche unter der Linie — macht eine einzelne Kurve auf
        // den ersten Blick lesbarer, stört bei mehreren überlagerten Serien
        // dank der niedrigen Deckkraft nicht. Abschaltbar (Optionen-Menü,
        // "Fläche"), Default an — siehe chart_editor.js.
        if (areaFill) cfg.areaStyle = {color, opacity: 0.08};
      } else if (!stackedActive) {
        cfg.itemStyle.borderRadius = [3, 3, 0, 0];
      } else if (lastBarIndexForAxis.get(axisKey(s)) === i) {
        // Nur das oberste Segment jedes Stapels abrunden — jedes einzelne
        // Segment abzurunden sähe wie mehrere getrennte Balken aus, nicht
        // wie ein durchgehender Stapel.
        cfg.itemStyle.borderRadius = [3, 3, 0, 0];
      }
      // Durchschnittslinie je Serie, in deren Farbe und auf deren y-Achse.
      // Ohne Einheit im Text: die Kachel beschriftet auch ihre Werte
      // (showValues) nur mit der Zahl, und der Platz ist hier knapper als auf
      // der Chart-Seite. Entschieden: im gestapelten Modus weggelassen —
      // dieselbe Begründung wie in chart_editor.js (eine Serie beginnt darin
      // nicht mehr bei 0).
      const durchschnitt = averageLine && !isStackedBar && averageStyle === 'flat'
        ? averageOf(prepared[i].averageValues) : null;
      if (durchschnitt !== null) {
        cfg.markLine = {
          silent: true,
          symbol: 'none',
          lineStyle: {color, type: 'dashed', width: 1},
          label: {
            position: 'insideEndTop',
            fontSize: scaledFont(10),
            color: inkMuted,
            formatter: () => `Ø ${fmtCompactNumber(durchschnitt, effectiveDecimals(s))}`,
          },
          data: [horizontalActive ? {xAxis: durchschnitt} : {yAxis: durchschnitt}],
        };
      }
      if (showRollingAverage) {
        const windowPoints = movingAverageWindow(prepared[i].rawPoints.length);
        const rollingData = movingAverage(prepared[i].rawPoints, windowPoints)
          .filter(p => p.value !== null)
          .map(p => [p.ts * 1000, p.value]);
        // Eigene, zusätzliche Serie statt eines markLine/Umbaus der
        // Hauptserie — dieselbe Technik wie chart_editor.js. Nicht über die
        // Legende einzeln umschaltbar, sie gehört sichtbar zur Hauptserie.
        rollingSeries.push({
          name: `${displayName} (Ø gleitend)`,
          type: 'line',
          yAxisIndex: units.indexOf(axisKey(s)),
          data: rollingData,
          smooth: true,
          symbol: 'none',
          lineStyle: {width: 2.5, color},
          z: 3,
        });
      }
      if (!singleBucket && !horizontalActive) {
        holdSeries.push({seriesIndex: i, name: displayName, color, data: lineData, bucket: s.chart_type === 'bar'});
      }
      return cfg;
    });
    echartsSeries.push(...rollingSeries);
    // Vorjahr/Vorperiode als blassere, gestrichelte Nebenserie je Entität —
    // dieselbe Technik wie render() in chart_editor.js: um die exakte
    // Fensterdifferenz verschoben, nicht über den Array-Index zugeordnet.
    if (compare && !singleBucket) {
      series.forEach(s => {
        if (!s.compare_points || !s.compare_points.length) return;
        const color = colorFor(s.entity_id);
        const displayName = entityNames[s.entity_id] || s.friendly_name;
        const shiftMs = (s.compare_window_start != null && data.window_start != null)
          ? (data.window_start - s.compare_window_start) * 1000
          : 0;
        const comparePoints = resamplePoints(
          s.compare_points, range, resolutionPreset, s.aggregation_type, s.compare_window_start
        );
        const compareData = comparePoints.map(p => [p.ts * 1000 + shiftMs, p.value, s.unit, effectiveDecimals(s), isDurationSeries(s)]);
        if (s.chart_type === 'line' && compareData.length && data.window_end != null
            && compareData[compareData.length - 1][0] < data.window_end * 1000) {
          const last = compareData[compareData.length - 1];
          compareData.push([data.window_end * 1000, last[1], last[2], last[3], last[4]]);
        }
        const cmp = {
          name: `${displayName} (${compare.mode === 'year' ? t('Vorjahr') : t('Vorperiode')})`,
          type: s.chart_type,
          yAxisIndex: units.indexOf(axisKey(s)),
          data: compareData,
          lineStyle: {width: 1.5, color: withOpacity(color, 0.5), type: 'dashed'},
          itemStyle: {color: withOpacity(color, 0.5)},
          barMaxWidth: 28,
          clip: s.chart_type !== 'bar',
        };
        if (s.chart_type === 'line') {
          cmp.smooth = true;
          if (!showPoints) cmp.symbol = 'none';
          if (areaFill) cmp.areaStyle = {color, opacity: 0.05};
        } else {
          cmp.itemStyle.borderRadius = [3, 3, 0, 0];
        }
        echartsSeries.push(cmp);
      });
    }
    padStackedSeries(echartsSeries);

    // ECharts' eigene Legende bleibt unsichtbar (show:false, wie in
    // chart_editor.html) — die sichtbare Legende ist das eigene HTML-Element
    // (renderLegend()), legend.selected/data existieren hier nur, damit
    // legendToggleSelect (setupLegendToggles()) überhaupt etwas zum
    // Umschalten hat.
    const hiddenSet = legendHidden.get(chartId);
    const legendSelected = {};
    echartsSeries.forEach(s => { legendSelected[s.name] = !hiddenSet || !hiddenSet.has(s.name); });

    chart.setOption({
      animation,
      textStyle: {fontFamily: style.getPropertyValue('--font-mono')},
      color: PALETTE,
      // "Werte anzeigen": Die Zahl steht über (horizontal: rechts neben) dem
      // Balken, und die Y-Achse endet exakt beim Maximalwert. Ohne Rand würde
      // das Label des höchsten Balkens am Kachelrand abgeschnitten.
      grid: {
        left: 6,
        right: showValues && horizontalActive ? 36 : 6,
        top: showValues && !horizontalActive && !stackedActive ? 10 + scaledFont(10) + 6 : 10,
        bottom: 20,
        containLabel: true,
      },
      xAxis: singleBucket ? (horizontalActive ? xAxisForHorizontal : categoryAxisObj) : {
        type: 'time',
        min: axisMinForBars(echartsSeries, data.window_start != null ? data.window_start * 1000 : null),
        // period_end statt window_end: eine laufende Periode (z. B. Woche)
        // zeigt so bis zur vollen Kalendergrenze (Sonntag), auch für die noch
        // datenlose Zukunft — window_end (an "jetzt" gedeckelt) bleibt nur für
        // den Linien-Haltepunkt oben (lineData.push(...)) maßgeblich. Eine
        // Sekunde zurück, da period_end (wie window_end) EXKLUSIV ist — sonst
        // reicht die Achse sichtbar bis zum Beginn der nächsten Periode (ein
        // Achsen-Tick "01.09." für einen Monat, der am 31.08. endet).
        max: (data.period_end ?? data.window_end) != null ? (data.period_end ?? data.window_end) * 1000 - 1000 : undefined,
        boundaryGap: [0, 0],
        // Woche/Monat bucketen tagesweise (siehe fmtAxis()) — mit fest
        // erzwungenem Tages-Interval statt ECharts' automatischer "nice
        // tick"-Berechnung, die trotz explizitem max (s. o.) gelegentlich
        // einen zusätzlichen Tick (Gitternetz, nicht nur Label) jenseits der
        // Periodengrenze erzeugt. Nicht für Jahr/Dekade erzwungen: Monate/
        // Jahre sind unterschiedlich lang, ein fester Millisekunden-Interval
        // würde dort falsch ausgerichtete Ticks erzeugen.
        interval: ['week', 'month'].includes(range) ? 24 * 60 * 60 * 1000 : undefined,
        axisLabel: {
          // ECharts' eigene "nice tick"-Berechnung polstert eine Zeit-Achse
          // intern minimal über min/max hinaus (auch mit explizit gesetztem
          // max) — ohne diese Sperre erschiene trotz max oben vereinzelt noch
          // ein Tick auf der falschen Seite der Periodengrenze (z. B. "01.09."
          // für einen Monat, der am 31.08. endet). rawPeriodEndMs ist die
          // EXKLUSIVE Grenze (Beginn der Folgeperiode) — ab dort wird die
          // Beschriftung unterdrückt statt einfach nur die Achse zu kürzen.
          formatter: v => {
            const rawPeriodEndMs = (data.period_end ?? data.window_end) != null ? (data.period_end ?? data.window_end) * 1000 : null;
            if (rawPeriodEndMs != null && v >= rawPeriodEndMs) return '';
            return fmtAxis(range, v / 1000);
          },
          fontSize: scaledFont(10), color: inkFaint, hideOverlap: true,
        },
        axisLine: {lineStyle: {color: borderColor}},
        axisTick: {show: false},
        splitLine: {show: false},
      },
      yAxis: horizontalActive ? {...categoryAxisObj, inverse: true} : yAxis,
      tooltip: {
        trigger: 'axis',
        backgroundColor: surface,
        borderColor,
        textStyle: {color: inkMuted, fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        formatter: (allParams) => {
          // Auffüll-Punkte gestapelter Serien (padStackedSeries) haben keinen Wert.
          // Danach jede sichtbare Linien- und Balken-Serie mit ihrem letzten
          // bekannten Wert (tooltip-hold.js), auch ohne eigenen Punkt am Cursor.
          const legendState = chart.getOption().legend?.[0]?.selected || {};
          const params = TooltipHold.complete(
            allParams.filter(p => p.data[1] != null),
            holdSeries,
            name => legendState[name] !== false
          );
          if (!params.length) return '';
          // Bei singleBucket ist axisValue jetzt der Entitätsname (eigene
          // Kategorie je Entität, s. o.), keine Zeitangabe mehr — die
          // Kopfzeile zeigt stattdessen weiterhin den Periodenbeginn direkt
          // aus window_start, wie vor der Kategorie-je-Entität-Umstellung.
          const header = singleBucket
            ? (data.window_start != null ? new Date(data.window_start * 1000).toLocaleDateString(LOCALE, window.ServerTime.withZone({day: '2-digit', month: '2-digit', year: 'numeric'})) : '')
            : fmtTooltipTimestamp(params[0].axisValue, tooltipBucketSeconds);
          const rows = params.map(p => {
            const unit = p.data[2] || '';
            const decimals = p.data[3];
            const value = p.data[4]
              ? NumberFormat.fmtDuration(p.data[1])
              : `${fmtCompactNumber(p.data[1], decimals)}${unit ? ' ' + unit : ''}`;
            // 100%-Normierung: zeigt IMMER beides — Anteil und Original-
            // Absolutwert in Klammern (Felder 5–7, siehe axisNormalized oben)
            // — sonst verliert der Tooltip genau die Zahl, die die
            // Normierung aus dem Balken selbst entfernt.
            const absValue = p.data[5];
            const extra = absValue != null
              ? ` <span style="color:${inkFaint};">(${fmtCompactNumber(absValue, p.data[7])}${p.data[6] ? ' ' + p.data[6] : ''})</span>`
              : '';
            return `<div style="display:flex;justify-content:space-between;gap:14px;">`
                 + `<span>${p.marker}${p.seriesName}</span>`
                 + `<span style="margin-left:8px;text-align:right;"><strong>${value}${extra}</strong>`
                 + `${TooltipHold.note(p, inkFaint, ms => fmtTooltipTimestamp(ms, tooltipBucketSeconds))}</span></div>`;
          }).join('');
          return header ? `<div style="margin-bottom:4px;color:${inkFaint};">${header}</div>${rows}` : rows;
        },
        // Kachel hat overflow:hidden (verhindert, dass z. B. die Legende das
        // Kachel-Layout sprengt) — ohne appendToBody würde der Tooltip am
        // Kachelrand abgeschnitten statt sichtbar über die Kachel
        // hinauszuragen (siehe dieselbe Begründung in chart_editor.html/
        // entity_detail.html).
        appendToBody: true,
      },
      legend: {show: false, data: echartsSeries.map(s => s.name), selected: legendSelected},
      series: echartsSeries,
      // notMerge: ohne dies mergt ECharts neue Optionen standardmäßig nur in
      // den bestehenden State statt ihn zu ersetzen — bei jeder erneuten
      // renderTile()-Ausführung derselben Kachel (Resize, Größenwechsel,
      // Legenden-Umschalter) könnten so Reste vorheriger Aufrufe bestehen
      // bleiben. chart_editor.html nutzt aus demselben Grund ebenfalls
      // setOption(..., true).
    }, true);
  }

  // Langzeit-Kachel: Rechengrundlage wie der multiYear-Getter in
  // chart_editor.js (Kalendermonate je Jahr, Ø je Monat über die Vorjahre),
  // ohne Tabelle und Jahreswerte — die gibt es nur auf der Chart-Seite.
  // Entschieden: eine 1×1-Kachel zeichnet nur das angezeigte Jahr und den Ø.
  function tileMultiYear(s, data, compare, {small, color, inkMuted}) {
    const agg = s.aggregation_type;
    const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
    const currentYear = data.window_start ? new Date(data.window_start * 1000).getFullYear() : new Date().getFullYear();
    const running = (data.is_current ?? true) && data.window_end != null;
    const cutMonth = running ? new Date(data.window_end * 1000 - 1).getMonth() : null;
    const years = [
      {year: currentYear, monthly: monthlyBuckets(s.points, agg), isCurrent: true},
      ...s.years.map(y => ({year: y.year, monthly: monthlyBuckets(y.points, agg), isCurrent: false})),
    ];
    const previous = years.slice(1);
    const total = years.length;
    // Farbe je Alter abgestuft — wortgleich mit multiYearColor() in chart_editor.js.
    const yearColor = index => {
      if (index === 0) return color;
      const steps = Math.max(total - 1, 1);
      return withOpacity(color, total === 2 ? 0.55 : 0.75 - 0.5 * (index - 1) / Math.max(steps - 1, 1));
    };
    const shown = small ? years.slice(0, 1) : years;
    const averageSeries = compare.average && previous.length ? {
      name: t('Ø {from}–{to}', {from: previous[previous.length - 1].year, to: previous[0].year}),
      monthly: Array.from({length: 12}, (_, m) => mean(previous.map(y => y.monthly[m]).filter(v => v != null))),
      color: inkMuted,
    } : null;
    return {
      cutMonth,
      series: shown.map((y, i) => ({...y, name: String(y.year), color: yearColor(i)})),
      averageSeries,
      // Jahre, die die Legende nicht einzeln nennt (nur bei mehr als 1×1 —
      // dort sind sie gezeichnet, aber nicht benannt).
      hiddenYears: small ? 0 : total - 1,
    };
  }

  function tileValueFormatter(s, decimals) {
    const isDuration = s.aggregation_type === 'switch' && s.display_mode === 'time';
    return v => v == null ? '—' : (isDuration ? NumberFormat.fmtDuration(v) : `${fmtCompactNumber(v, decimals)}${s.unit ? ' ' + s.unit : ''}`);
  }

  // Gemeinsamer Rahmen der beiden Monats-Renderpfade unten: feste
  // Jan–Dez-Achse statt Zeitachse, Tooltip mit den nicht leeren Zeilen.
  function tileMonthOption(echartsSeries, yAxis, {animation, style, borderColor, inkFaint, inkMuted, surface, hiddenSet, orderRows}) {
    const legendSelected = {};
    echartsSeries.forEach(x => { legendSelected[x.name] = !hiddenSet || !hiddenSet.has(x.name); });
    return {
      animation,
      textStyle: {fontFamily: style.getPropertyValue('--font-mono')},
      grid: {left: 6, right: 6, top: 10, bottom: 20, containLabel: true},
      xAxis: {
        type: 'category', data: MONTH_LABELS(),
        axisLabel: {fontSize: scaledFont(10), color: inkFaint, hideOverlap: true},
        axisLine: {lineStyle: {color: borderColor}}, axisTick: {show: false},
      },
      yAxis,
      tooltip: {
        trigger: 'axis',
        backgroundColor: surface,
        borderColor,
        textStyle: {color: inkMuted, fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        formatter: (allParams) => {
          const params = orderRows(allParams.filter(p => p.value != null));
          if (!params.length) return '';
          const rows = params.map(p => {
            const fmt = echartsSeries[p.seriesIndex]?.valueFormatter || (v => String(v));
            return `<div style="display:flex;justify-content:space-between;gap:14px;">`
                 + `<span>${p.marker}${p.seriesName}</span><strong style="margin-left:8px;">${fmt(p.value)}</strong></div>`;
          }).join('');
          return `<div style="margin-bottom:4px;color:${inkFaint};">${allParams[0]?.axisValue ?? ''}</div>${rows}`;
        },
        appendToBody: true,
      },
      legend: {show: false, data: echartsSeries.map(x => x.name), selected: legendSelected},
      series: echartsSeries.map(({valueFormatter, ...rest}) => rest),
    };
  }

  function renderTileMultiYear(chart, my, s, opts) {
    const format = tileValueFormatter(s, opts.effectiveDecimals(s));
    // Älteste zuerst, damit das angezeigte Jahr obenauf liegt (wie im Editor).
    const echartsSeries = my.series.map(y => ({
      name: y.name,
      type: 'line',
      data: y.monthly.map((v, m) => (y.isCurrent && m === my.cutMonth && v != null
        ? {value: v, symbol: 'emptyCircle', symbolSize: 7}
        : v)),
      lineStyle: {color: y.color, width: y.isCurrent ? 2.5 : 1.2},
      itemStyle: {color: y.color},
      symbol: y.isCurrent ? 'circle' : 'none',
      symbolSize: 4,
      z: y.isCurrent ? 4 : 2,
      valueFormatter: format,
    })).reverse();
    if (my.averageSeries) {
      echartsSeries.push({
        name: my.averageSeries.name,
        type: 'line',
        data: my.averageSeries.monthly,
        lineStyle: {color: my.averageSeries.color, type: 'dashed', width: 1.8},
        itemStyle: {color: my.averageSeries.color},
        symbol: 'none',
        z: 5,
        valueFormatter: format,
      });
    }
    const yAxis = {
      type: 'value', scale: true,
      axisLabel: {fontSize: scaledFont(10), color: opts.inkFaint, formatter: v => fmtCompactNumber(v, opts.effectiveDecimals(s))},
      axisLine: {show: false}, axisTick: {show: false},
      splitLine: {lineStyle: {color: opts.borderColor, type: 'dashed'}},
    };
    // Entschieden: alle gezeichneten Jahre, das neueste oben, der Ø zuletzt.
    const orderRows = params => {
      const average = params.filter(p => my.averageSeries && p.seriesName === my.averageSeries.name);
      return [...params.filter(p => !average.includes(p)).reverse(), ...average];
    };
    chart.setOption(tileMonthOption(echartsSeries, yAxis, {...opts, orderRows}), true);
  }

  // "Vorjahr" mit Laufsumme oder Soll: Monats-Balkenpaare wie
  // renderYearsCompare() in chart_editor.js — Laufsumme auf eigener,
  // unsichtbarer Achse je Einheit, Soll als gepunktete Linie. Ohne
  // Achsennamen: dafür ist in der Kachel kein Platz.
  function renderTileYearsCompare(chart, series, compare, data, opts) {
    const {entityNames, colorFor, effectiveDecimals} = opts;
    const isDurationSeries = s => s.aggregation_type === 'switch' && s.display_mode === 'time';
    const axisUnit = s => isDurationSeries(s) ? ' duration' : s.unit;
    const hasCompare = s => !!s.compare_points;
    const isSoll = s => !!compare.target && s.entity_id === compare.target;
    const currentYear = data.window_start ? new Date(data.window_start * 1000).getFullYear() : new Date().getFullYear();
    const units = [...new Set(series.filter(hasCompare).map(axisUnit))];
    const cumUnits = compare.cumsum ? [...new Set(series.filter(s => hasCompare(s) && !isSoll(s)).map(axisUnit))] : [];
    const barUnits = new Set(series.filter(s => hasCompare(s) && !isSoll(s)).map(axisUnit));
    const unitDecimals = new Map();
    series.forEach(s => {
      const d = effectiveDecimals(s);
      if (d == null) return;
      const current = unitDecimals.get(axisUnit(s));
      if (current == null || d < current) unitDecimals.set(axisUnit(s), d);
    });
    let visiblePos = 0;
    const yAxis = [...units, ...cumUnits].map((u, i) => {
      const hidden = i >= units.length || !barUnits.has(u);
      const pos = hidden ? 0 : visiblePos++;
      const decimals = unitDecimals.get(u);
      return {
        type: 'value',
        show: !hidden,
        position: pos % 2 === 0 ? 'left' : 'right',
        offset: Math.floor(pos / 2) * 46,
        min: value => Math.min(0, value.min),
        max: value => Math.max(0, value.max),
        axisLine: {show: false}, axisTick: {show: false},
        splitLine: {show: !hidden, lineStyle: {color: opts.borderColor, type: 'dashed'}},
        axisLabel: {
          show: !hidden, fontSize: scaledFont(10), color: opts.inkFaint,
          formatter: v => u === ' duration' ? NumberFormat.fmtDuration(v) : fmtCompactNumber(v, decimals),
        },
      };
    });
    const cumAxisIndex = u => units.length + cumUnits.indexOf(u);
    const echartsSeries = [];
    series.forEach(s => {
      if (!hasCompare(s)) return;
      const color = colorFor(s.entity_id);
      const displayName = entityNames[s.entity_id] || s.friendly_name;
      const valueFormatter = tileValueFormatter(s, unitDecimals.get(axisUnit(s)));
      if (isSoll(s)) {
        echartsSeries.push({
          name: displayName, type: 'line', yAxisIndex: units.indexOf(axisUnit(s)),
          data: monthlyBuckets(s.points, s.aggregation_type),
          lineStyle: {type: 'dotted', width: 2, color}, itemStyle: {color}, symbol: 'none', z: 3, valueFormatter,
        });
        return;
      }
      [
        {year: currentYear, points: s.points, opacity: 1},
        {year: currentYear - 1, points: s.compare_points, opacity: 0.55},
      ].forEach(({year, points, opacity}) => {
        const monthly = monthlyBuckets(points, s.aggregation_type);
        echartsSeries.push({
          name: `${displayName} (${year})`, type: 'bar', yAxisIndex: units.indexOf(axisUnit(s)), barGap: '20%',
          barMaxWidth: 14, data: monthly, itemStyle: {color: withOpacity(color, opacity), borderRadius: [2, 2, 0, 0]}, valueFormatter,
        });
        if (compare.cumsum) {
          echartsSeries.push({
            name: t('{name} ({year}) kumuliert', {name: displayName, year}), type: 'line',
            yAxisIndex: cumAxisIndex(axisUnit(s)), data: cumulate(monthly),
            lineStyle: {type: 'dashed', width: 2, color: withOpacity(color, opacity)},
            itemStyle: {color: withOpacity(color, opacity)}, symbol: 'none', z: 4, valueFormatter,
          });
        }
      });
    });
    chart.setOption(tileMonthOption(echartsSeries, yAxis, {...opts, orderRows: params => params}), true);
  }

  // Kompakter Zeitstrahl für eine Kachel — eine Kategorie-Zeile je Entität,
  // dieselbe Balken-Technik wie renderTimelineMulti() in chart_editor.html,
  // hier nur mit den kleineren Kachel-Schriftgrößen/Abständen. Ohne
  // Zeilen-Beschriftung (der Name steht schon in der Kachel-Legende, siehe
  // renderTile()) — bei mehreren Zeilen in einer kleinen Kachel wäre dafür
  // ohnehin kaum Platz.
  // Dauer und Anteil je Zustand einer Zustands-Serie — für Legende und
  // Donut. Rohwechsel (bis zur Woche, Zeitstrahl) und Server-Anteile (ab dem
  // Monat) ergeben dieselbe Form; dieselbe Rechnung wie stateRowsFor() in
  // pages/chart_editor.js.
  function stateRows(s, windowEnd) {
    // Ohne Rohwechsel (Rohabfrage über der Grenze) aus den Spalten vom Server.
    const buckets = s.chart_type === 'shares'
      ? StateEntities.bucketsFromShares(s.points || [])
      : (!(s.points || []).length && s.columns
        ? StateEntities.bucketsFromShares(s.columns.map(([ts, states]) => ({ts, states})))
        : StateEntities.bucketsFromSegments(StateEntities.segments(s.points || [], windowEnd), () => 0, () => Infinity));
    return StateEntities.totals(buckets, s.states).map(row => ({
      ...row,
      label: StateEntities.label(s.states, row.idx),
      raw: StateEntities.raw(s.states, row.idx),
      swatch: StateEntities.swatchStyle(row.idx),
      text: `${NumberFormat.fmtDuration(row.seconds)} · ${StateEntities.fmtShare(row.share)}`,
    }));
  }

  // Anteile oder Wechsel je Zustands-Entität, wie im Chart-Editor.
  function renderTileStateBars(chart, series, entityNames, data, range, mode, animation) {
    const rows = series.filter(s => s.aggregation_type === 'state').map(s => ({
      name: entityNames[s.entity_id] || s.friendly_name,
      states: s.states,
      buckets: StateEntities.bucketsForSeries(s, range, data.window_end),
    }));
    chart.setOption({
      ...StateEntities.barPanels(rows, {mode, range, windowStart: data.window_start, periodEnd: data.period_end ?? data.window_end}),
      animation,
    }, true);
  }

  // Heatmap je Zustands-Entität, wie im Chart-Editor, nur kompakt.
  function renderTileStateHeatmap(chart, series, entityNames, animation, heatmapStates) {
    const rows = series
      .filter(s => s.aggregation_type === 'state' && s.cells)
      .map(s => ({
        name: entityNames[s.entity_id] || s.friendly_name,
        states: s.states,
        cells: s.cells,
        idx: StateEntities.heatmapIndexFor(s.cells, s.states, heatmapStates[s.entity_id]),
      }))
      .filter(row => row.idx !== null);
    if (!rows.length) {
      chart.clear();
      return;
    }
    chart.setOption({...StateEntities.heatmapPanels(rows, {compact: true}), animation}, true);
  }

  function renderTileTimeline(chart, series, entityNames, data, range, animation, style, borderColor, inkFaint, surface, colorFor) {
    const categories = series.map(s => entityNames[s.entity_id] || s.friendly_name);
    const items = [];
    const pattern = StateEntities.unavailablePattern();
    // Zustände: ein Band je Abschnitt, Farbe nach Zustand (wie im Chart-Editor), bei
    // zu vielen Abschnitten für die Breite je Bildpunkt eine Spalte mit den Anteilen.
    const stateRows = series
      .map((s, catIndex) => ({catIndex, points: s.points, columns: s.columns, dt: s.dt, aggregation_type: s.aggregation_type}))
      .filter(row => row.aggregation_type === 'state');
    const stateTimeline = StateEntities.stateTimelineItems(
      stateRows, data.window_start, data.period_end ?? data.window_end, data.window_end,
      Math.max(60, Math.round(chart.getWidth() - 10)), pattern
    );
    const timelineParts = stateTimeline.parts;
    items.push(...stateTimeline.items);
    series.forEach((s, catIndex) => {
      if (s.aggregation_type === 'state') return;
      const color = colorFor(s.entity_id);
      const points = s.points || [];
      for (let i = 0; i < points.length; i++) {
        const start = points[i].ts;
        const end = i + 1 < points.length ? points[i + 1].ts : (data.window_end ?? start);
        if (points[i].value >= 0.5 && end > start) {
          items.push([catIndex, start * 1000, end * 1000, color]);
        }
      }
    });
    chart.setOption({
      animation,
      textStyle: {fontFamily: style.getPropertyValue('--font-mono')},
      grid: {left: 4, right: 6, top: 10, bottom: 20, containLabel: true},
      xAxis: {
        type: 'time',
        min: data.window_start != null ? data.window_start * 1000 : undefined,
        max: (data.period_end ?? data.window_end) != null ? (data.period_end ?? data.window_end) * 1000 - 1000 : undefined,
        boundaryGap: [0, 0],
        interval: ['week', 'month'].includes(range) ? 24 * 60 * 60 * 1000 : undefined,
        axisLabel: {
          formatter: v => {
            const rawPeriodEndMs = (data.period_end ?? data.window_end) != null ? (data.period_end ?? data.window_end) * 1000 : null;
            if (rawPeriodEndMs != null && v >= rawPeriodEndMs) return '';
            return fmtAxis(range, v / 1000);
          },
          fontSize: scaledFont(10), color: inkFaint, hideOverlap: true,
        },
        axisLine: {lineStyle: {color: borderColor}},
        axisTick: {show: false},
        splitLine: {show: false},
      },
      yAxis: {
        type: 'category', data: categories.map(() => ''), inverse: true,
        axisLine: {show: false}, axisTick: {show: false}, splitLine: {show: false},
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: surface,
        borderColor,
        textStyle: {color: style.getPropertyValue('--ink-muted').trim(), fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        formatter: p => {
          const [catIndex, startMs, endMs, , idx, partsIndex] = p.value;
          const pad = n => String(n).padStart(2, '0');
          const fmtTime = ms => { const t = window.ServerTime.parts(ms / 1000); return `${pad(t.hour)}:${pad(t.minute)}`; };
          const span = `${fmtTime(startMs)}–${fmtTime(endMs)} · <strong>${NumberFormat.fmtDuration((endMs - startMs) / 1000)}</strong>`;
          const s = series[catIndex];
          if (!s || s.aggregation_type !== 'state') return `${categories[catIndex]}<br>${span}`;
          if (partsIndex >= 0) {
            // Spalte über mehrere Tage: Datum dazu, in Serverzeit wie überall auf der Kachel.
            const stamp = ms => { const t = window.ServerTime.parts(ms / 1000); return `${pad(t.day)}.${pad(t.month)}. ${pad(t.hour)}:${pad(t.minute)}`; };
            return StateEntities.condensedTooltip(timelineParts[partsIndex], startMs, endMs, s.states, stamp, escapeHtml(categories[catIndex]));
          }
          return `${categories[catIndex]}<br><strong>${escapeHtml(StateEntities.label(s.states, idx))}</strong><br>${span}`;
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
            {x: start[0], y: start[1] - height / 2, width: Math.max(end[0] - start[0], api.value(4) != null ? 1 : 0), height},
            {x: params.coordSys.x, y: params.coordSys.y, width: params.coordSys.width, height: params.coordSys.height}
          );
          // Verdichtete Spalte: die Anteile der Zustände als Schichten.
          if (rectShape && api.value(5) >= 0) {
            return StateEntities.condensedShape(timelineParts[api.value(5)], rectShape, idx => StateEntities.fill(idx, pattern));
          }
          return rectShape && {type: 'rect', shape: rectShape, style: {fill: api.value(3)}};
        },
        encode: {x: [1, 2], y: 0},
        data: items,
      }],
    }, true);
  }

  // Zentral in static/js/number-format.js (window.NumberFormat) — dieselbe
  // Formatierung wie überall sonst in der Oberfläche, siehe Kommentar dort.
  const fmtCompactNumber = NumberFormat.fmt;

  // Donut statt Zeitverlauf ("Darstellungsart", Optionen-Menü der Chart-
  // Seite) — Kachel-Parität zu chart_editor.js renderDonut(): ein Anteil je
  // Serie aus GENAU EINEM aggregierten Wert (aggregation: Summe/Durchschnitt/
  // Letzter Wert der bereits geladenen Punkte), keine Zeitachse. Dieselbe
  // Radius-/Emphasis-Konfiguration wie #storage-pie (statistik.js) und
  // .edash-share-donut (energiedashboard.js) — label:show:false, weil die
  // Namen schon in der .dtile-legend-Zeile stehen (renderTile() baut sie VOR
  // diesem Zweig, siehe renderLegend()-Aufruf dort, unverändert wiederverwendet).
  // Zustände: ein Donut je Zustands-Entität nebeneinander, wie
  // renderStateDonuts() in pages/chart_editor.js.
  function renderTileStateDonuts(chart, series, entityNames, windowEnd, style, surface) {
    const rows = series.filter(s => s.aggregation_type === 'state');
    const width = 100 / Math.max(rows.length, 1);
    const pattern = StateEntities.unavailablePattern();
    chart.setOption({
      tooltip: {
        trigger: 'item',
        backgroundColor: surface,
        textStyle: {color: style.getPropertyValue('--ink-muted').trim(), fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        appendToBody: true,
      },
      series: rows.map((s, k) => ({
        type: 'pie',
        radius: rows.length > 1 ? ['36%', '60%'] : ['52%', '85%'],
        center: [`${width * k + width / 2}%`, '50%'],
        label: {show: false},
        labelLine: {show: false},
        itemStyle: {borderColor: surface, borderWidth: 2},
        tooltip: {
          formatter: p => `${escapeHtml(entityNames[s.entity_id] || s.friendly_name)}<br>${p.marker} <strong>${escapeHtml(p.name)}</strong><br>`
            + `${NumberFormat.fmtDuration(p.value)} (${fmtCompactNumber(p.percent, 1)} %)`,
        },
        data: stateRows(s, windowEnd).map(row => ({
          name: row.label, value: row.seconds, itemStyle: {color: StateEntities.fill(row.idx, pattern)},
        })),
      })),
    }, true);
  }

  function renderTileDonut(chart, series, entityNames, colorFor, aggregation, effectiveDecimals, style, surface, chartId) {
    const data = series.map(s => {
      const values = (s.points || []).map(p => p.value).filter(Number.isFinite);
      let value = 0;
      if (values.length) {
        if (aggregation === 'average') value = values.reduce((sum, v) => sum + v, 0) / values.length;
        else if (aggregation === 'last') value = values[values.length - 1];
        else value = values.reduce((sum, v) => sum + v, 0);
      }
      return {
        name: entityNames[s.entity_id] || s.friendly_name,
        value: Math.max(0, value),
        itemStyle: {color: colorFor(s.entity_id)},
      };
    });
    // Serien-Umschalter der Legende (toggleTileLegendItem()) wirkt bei
    // type:'pie' auf einzelne DATENPUNKTE statt auf ganze Serien — ECharts
    // behandelt jeden data-Eintrag wie einen eigenen Legendeneintrag (per
    // name), legendToggleSelect trifft darüber trotzdem genau den richtigen
    // Slice. Ohne dieses (unsichtbare, show:false — wie im Zeitverlauf-Zweig
    // oben) legend-Objekt hätte die Aktion nichts zum Umschalten.
    const hiddenSet = legendHidden.get(chartId);
    const legendSelected = {};
    data.forEach(d => { legendSelected[d.name] = !hiddenSet || !hiddenSet.has(d.name); });
    chart.setOption({
      legend: {show: false, data: data.map(d => d.name), selected: legendSelected},
      tooltip: {
        trigger: 'item',
        backgroundColor: surface,
        textStyle: {color: style.getPropertyValue('--ink-muted').trim(), fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        formatter: p => {
          const s = series[p.dataIndex];
          const unit = s.unit ? ` ${s.unit}` : '';
          return `${p.marker} ${p.name}: <strong>${fmtCompactNumber(p.value, effectiveDecimals(s))}${unit}</strong> (${fmtCompactNumber(p.percent, 1)} %)`;
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
  }

  // Kompakte Vorschau einer Vergleichstabelle-Kachel — dieselbe Rechenlogik
  // wie der volle Editor (static/js/table-compute.js). Zeigt IMMER alle
  // Zeilen/Spalten (kein "+N weitere" mehr) — .dtile-table-preview hat
  // ohnehin schon einen eigenen Scrollbalken (overflow:auto, siehe CSS),
  // ein zusätzliches Abschneiden mit Verweis auf ausgeblendete Zeilen war
  // deshalb nur eine zweite, redundante Begrenzung obendrauf. Style-Optionen
  // (Zebra/Rahmen/Dichte/Kopfzeile, siehe TableCompute.styleClasses) wirken
  // hier genauso wie in der vollen Ansicht.
  async function renderTableTile(el) {
    // Ausgeblendete Spalten/Zeilen (Tabelleneditor, col.hidden/row.hidden)
    // werden nicht gerendert, bleiben aber Teil der Berechnung — dieselbe
    // Regel wie in table_editor.html. computeValues() bekommt deshalb ALLE
    // Zeilen: sonst verschieben sich die Formel-Buchstaben, Formeln auf eine
    // ausgeblendete Zeile liefern "Fehler", und Summen-/Anteilszeilen
    // weichen vom Editor ab. Erst danach werden die sichtbaren Zeilen
    // herausgezogen.
    const visibleCols = JSON.parse(el.dataset.columns || '[]').filter(c => !c.hidden);
    const allRows = JSON.parse(el.dataset.rows || '[]');
    const visibleRowIndexes = allRows.map((r, i) => (r.hidden ? -1 : i)).filter(i => i >= 0);
    const visibleRows = visibleRowIndexes.map(i => allRows[i]);
    const style = JSON.parse(el.dataset.style || '{}');
    const previewEl = el.querySelector('.dtile-table-preview');
    if (!previewEl) return;

    if (!visibleCols.length || !visibleRows.length) {
      previewEl.innerHTML = '<div class="dtile-loading">' + t('Keine sichtbaren Zeilen/Spalten') + '</div>';
      return;
    }
    const base = el.closest('#dashboard-grid')?.dataset.appRoot || '';
    let values, allValues, windowStarts, windowEnds, isCurrent, elapsedSeconds, rowSums, sumOverlapping;
    try {
      let summable;
      ({values, windowStarts, windowEnds, isCurrent, elapsedSeconds, summable} = await TableCompute.computeValues(base, visibleCols, allRows, {at: stichtag()}));
      allValues = values;
      // Σ-Spalte: nur die sichtbaren Spalten kommen hier an, ausgeblendete
      // zählen also wie im Editor nie mit.
      rowSums = TableCompute.computeRowSums(visibleCols, allRows, allValues, summable);
      sumOverlapping = TableCompute.sumOverlap(visibleCols, windowStarts, windowEnds);
      values = values.map(colValues => visibleRowIndexes.map(i => colValues[i]));
    } catch (e) {
      previewEl.innerHTML = '<div class="dtile-loading">' + t('Fehler beim Laden') + '</div>';
      return;
    }

    // Abschnitts-Mitglieder (Entität-/Gruppen-Zeilen zwischen zwei Trennlinien
    // derselben Spalte) — Traversal-Gegenstück zu sectionMemberCells() in
    // table_editor.html, hier index- statt uid-basiert (dieselbe Datenform
    // wie computeValues()). Läuft wie dort über ALLE Zeilen des Abschnitts,
    // auch ausgeblendete (allRows/allValues, ri ist der Index der sichtbaren
    // Zeile). Die eigentliche Anteilsrechnung bleibt in
    // TableCompute.percentOfTotalCell(), damit beide Seiten denselben
    // Rechenkern nutzen.
    function sectionMemberCells(ci, ri) {
      const idx = visibleRowIndexes[ri];
      let start = 0;
      for (let j = idx - 1; j >= 0; j--) { if (allRows[j].row_type === 'separator') { start = j + 1; break; } }
      let end = allRows.length;
      for (let j = idx + 1; j < allRows.length; j++) { if (allRows[j].row_type === 'separator') { end = j; break; } }
      const cells = [];
      for (let j = start; j < end; j++) {
        if (allRows[j].row_type !== 'entity' && allRows[j].row_type !== 'group') continue;
        cells.push(allValues[ci] && allValues[ci][j]);
      }
      return cells;
    }
    // Spaltenweise statt zeilenweise — dieselbe Umstellung wie
    // columnHeatmapRange() in table_editor.html (siehe Kommentar dort):
    // eine Zeile enthält hier oft sehr unterschiedliche Größenordnungen
    // (Tag vs. Jahr), der sinnvolle Vergleich ist zwischen mehreren Zeilen
    // DERSELBEN Spalte, nicht innerhalb einer Zeile über alle Spalten. Nur
    // Entität-/Gruppen-Zeilen zählen als Vergleichspartner — eine Summen-/
    // Formelzeile ist fast immer das Maximum ihres Abschnitts und würde die
    // Skala sonst allein durch ihre Natur verzerren.
    function columnHeatmapRange(ci, ri) {
      const idx = visibleRowIndexes[ri];
      let start = 0;
      for (let j = idx - 1; j >= 0; j--) { if (allRows[j].row_type === 'separator') { start = j + 1; break; } }
      let end = allRows.length;
      for (let j = idx + 1; j < allRows.length; j++) { if (allRows[j].row_type === 'separator') { end = j; break; } }
      const vals = [];
      for (let j = start; j < end; j++) {
        if (allRows[j].row_type !== 'entity' && allRows[j].row_type !== 'group') continue;
        const cell = allValues[ci] && allValues[ci][j];
        if (cell && !cell.error && cell.value != null) vals.push(cell.value);
      }
      if (!vals.length) return null;
      return {min: Math.min(...vals), max: Math.max(...vals)};
    }
    // row.hide_if_empty: keine Zeile im Slice-Limit "verschwenden", die am
    // Ende doch nicht gerendert wird — deshalb hier VOR dem Bauen geprüft,
    // nicht erst beim Rendern selbst übersprungen.
    function isRowEmpty(row, ri) {
      if (!row.hide_if_empty) return false;
      return visibleCols.every((c, ci) => {
        const cell = values[ci] && values[ci][ri];
        return !cell || cell.value == null || cell.value === 0;
      });
    }

    // Manuelle Spaltenbreite (col.width/style.label_col_width, in
    // table_editor.html per Ziehgriff gesetzt). min-width direkt auf th/td
    // reicht bei HTML-Tabellen NICHT: Der Auto-Layout-Algorithmus darf diese
    // Werte bei zu wenig Platz trotzdem neu verteilen. Sobald mindestens eine
    // gespeicherte Breite existiert, bekommt die Kachel deshalb zusätzlich
    // ein echtes <colgroup> und eine feste Tabellen-Mindestbreite aus der
    // Summe aller Spalten. Nicht manuell gesetzte Spalten erhalten nur in
    // diesem gemischten Layout einen vernünftigen Fallback; Tabellen ganz
    // ohne gespeicherte Breiten bleiben beim bisherigen Auto-Layout.
    const colWidthAttr = (w) => w ? ` style="width:${w}px;min-width:${w}px;max-width:${w}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"` : '';
    const labelWidthAttr = colWidthAttr(style.label_col_width);

    const savedWidth = (w) => {
      const n = Number(w);
      return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
    };
    const savedLabelWidth = savedWidth(style.label_col_width);
    const savedValueWidths = visibleCols.map(c => savedWidth(c.width));
    // Nur gespeicherte WERT-Spaltenbreiten (oder "Spalten gleichmäßig")
    // erzwingen das feste <colgroup>-Layout unten — eine gespeicherte
    // Label-Spaltenbreite allein braucht das nicht: labelWidthAttr (s. u.)
    // setzt sie per Inline-Style direkt auf th/td, unabhängig vom
    // table-layout-Modus. Sonst zwang eine irgendwann mal per Ziehgriff
    // gesetzte Label-Breite ALLE Wertespalten unnötig auf die feste
    // 120px-Pauschalbreite (autoValueWidth) — sichtbar als deutlich zu
    // breite, nicht mehr auf die Kachel schrumpfende Tabelle.
    const hasSavedValueWidths = savedValueWidths.some(w => w != null);
    // "Spalten gleichmäßig" setzte bisher auf der Kachel zwingend width:100%
    // und war damit ein zweiter, unabhängiger Stauch-Pfad. Auch ohne alte
    // gespeicherte Einzelbreiten braucht diese Option deshalb das feste,
    // scrollbar breite Layout.
    const needsColumnLayout = hasSavedValueWidths || !!style.equal_value_cols;
    const longestRowLabel = visibleRows.reduce((n, row) => Math.max(n, String(row.label || '').length), 0);
    const autoLabelWidth = Math.max(120, Math.min(320, longestRowLabel * 8 + 24));
    // "Spalten gleichmäßig": alle Wertespalten gleich breit, bemessen an der
    // Spalte, deren Inhalt am meisten Platz braucht — nicht an einer festen
    // Pauschalbreite (die quetschte breitere Inhalte vorher mit ab). Dieselbe
    // Zeichen-basierte Heuristik wie autoLabelWidth oben, hier über den
    // tatsächlich formatierten Zellentext (Zahl + Einheit) jeder Spalte.
    const equalValueWidth = (() => {
      if (!style.equal_value_cols) return null;
      let longestCellText = 0;
      visibleCols.forEach((col, ci) => {
        longestCellText = Math.max(longestCellText, TableCompute.resolveLabel(col.label, windowStarts[ci]).length);
        visibleRows.forEach((row, ri) => {
          if (row.row_type === 'separator' || isRowEmpty(row, ri)) return;
          let cell = values[ci] && values[ci][ri];
          if (row.percent_of_total) cell = TableCompute.percentOfTotalCell(cell, sectionMemberCells(ci, ri));
          const decimals = row.percent_of_total ? '1' : col.decimals;
          const numberParts = TableCompute.cellNumberParts(cell, decimals, !!style.explicit_missing);
          const unit = style.show_units === false ? '' : TableCompute.cellUnit(cell);
          const text = `${numberParts.whole}${numberParts.separator}${numberParts.fraction}${unit ? ` ${unit}` : ''}`;
          longestCellText = Math.max(longestCellText, text.length);
        });
      });
      return Math.max(70, Math.min(260, longestCellText * 8 + 32));
    })();
    const autoValueWidth = equalValueWidth || 120;
    const showRowSum = !!style.show_row_sum;
    const sumLeft = showRowSum && style.row_sum_position !== 'right';
    const sumRight = showRowSum && !sumLeft;
    const sumLabel = (style.row_sum_label || '').trim() || 'Σ';
    // Bei "gleichmäßig" gilt equalValueWidth für JEDE Wertespalte, auch wenn
    // einzelne Spalten zusätzlich noch eine individuell gezogene Breite
    // gespeichert haben (table_editor.html) — sonst wären die Spalten trotz
    // aktivierter Option nicht wirklich alle gleich breit.
    const layoutWidths = needsColumnLayout
      ? [
          savedLabelWidth || autoLabelWidth,
          ...(sumLeft ? [autoValueWidth] : []),
          ...savedValueWidths.map(w => equalValueWidth || w || autoValueWidth),
          ...(sumRight ? [autoValueWidth] : []),
        ]
      : [];
    const savedTableWidth = layoutWidths.reduce((sum, width) => sum + width, 0);
    const tableWidthAttr = needsColumnLayout
      ? ` style="width:max(100%,${savedTableWidth}px);min-width:${savedTableWidth}px;table-layout:fixed;"`
      : '';
    const colgroup = needsColumnLayout
      ? `<colgroup>${layoutWidths.map(width => `<col style="width:${width}px">`).join('')}</colgroup>`
      : '';

    const sumHeadTh = () => {
      const overlapNote = t('Einige Spalten überschneiden sich zeitlich (z. B. Woche und Tag). Ihre Werte zählen in der Summe doppelt.');
      const tip = sumOverlapping ? ` data-tooltip-fixed="${escapeHtml(overlapNote)}"` : '';
      return `<th class="tbl-sum-col tbl-sum-head"${tip}>${escapeHtml(sumLabel)}${sumOverlapping ? '<span class="tbl-sum-warn" aria-hidden="true">!</span>' : ''}</th>`;
    };
    const sumTd = (row, ri) => {
      const cell = rowSums[visibleRowIndexes[ri]];
      const numberParts = TableCompute.cellNumberParts(cell, row.percent_of_total ? '1' : (visibleCols[0] ? (visibleCols.find(c => c.in_sum !== false) || visibleCols[0]).decimals : 'auto'), !!style.explicit_missing);
      const unit = style.show_units === false ? '' : TableCompute.cellUnit(cell);
      return `<td class="tbl-sum-col tbl-num${cell ? '' : ' tbl-sum-empty'}"><span class="tbl-cell"><span class="tbl-cell-number"><span class="tbl-number-whole">${escapeHtml(numberParts.whole)}</span><span class="tbl-number-separator">${escapeHtml(numberParts.separator)}</span><span class="tbl-number-fraction">${escapeHtml(numberParts.fraction)}</span></span>${unit ? `<span class="tbl-cell-unit">${escapeHtml(unit)}</span>` : ''}</span></td>`;
    };

    const styleClasses = TableCompute.styleClasses(style);
    let html = `<table class="dt compact dtile-mini-table ${styleClasses}"${tableWidthAttr}>${colgroup}<thead>`;
    // Mehrstufige Kopfzeile (col.group_label) — dieselbe Lauflängen-Logik wie
    // groupHeaderCells() in table_editor.html, hier direkt auf visibleCols
    // (die Kachel kürzt Spalten ohnehin schon aufs Sichtbare).
    if (visibleCols.some(c => (c.group_label || '').trim())) {
      html += `<tr class="tbl-header-row tbl-group-header-row"><th${labelWidthAttr}>&nbsp;</th>${sumLeft ? '<th></th>' : ''}`;
      let gi = 0;
      while (gi < visibleCols.length) {
        const label = (visibleCols[gi].group_label || '').trim();
        let span = 1;
        while (label && gi + span < visibleCols.length && (visibleCols[gi + span].group_label || '').trim() === label) span++;
        html += `<th colspan="${span}" class="tbl-group-header">${escapeHtml(label)}</th>`;
        gi += span;
      }
      html += `${sumRight ? '<th></th>' : ''}</tr>`;
    }
    html += `<tr class="tbl-header-row"><th${labelWidthAttr}>&nbsp;</th>${sumLeft ? sumHeadTh() : ''}`;
    // Beschriftungs-Platzhalter (z. B. "{jahr}") wurden beim Speichern bewusst
    // NICHT aufgelöst (siehe table_editor.html save()) — sonst würde eine so
    // beschriftete Spalte hier ewig denselben, beim Speichern eingefrorenen
    // Wert zeigen statt sich mit der Zeit automatisch zu aktualisieren.
    visibleCols.forEach((c, ci) => {
      const comparisonClass = TableCompute.isComparisonColumn(c) ? ' class="tbl-comparison-col"' : '';
      // Hinweis auf eine noch laufende (unvollständige) Woche/Monat/Jahr-
      // Spalte, dieselbe Kennzeichnung wie in table_editor.html —
      // data-tooltip-fixed (nicht das gewöhnliche data-tooltip), weil
      // .dtile-table-preview überläuft/scrollt und einen normalen
      // CSS-::after-Tooltip abschneiden würde (siehe fixed-tooltip.js).
      const periodNote = TableCompute.currentPeriodNote(c, isCurrent[ci], windowEnds[ci]);
      const periodTooltipAttr = periodNote ? ` data-tooltip-fixed="${escapeHtml(periodNote)}"` : '';
      const periodHint = periodNote ? '<span class="tbl-period-hint">i</span>' : '';
      html += `<th${comparisonClass}${colWidthAttr(c.width)}${periodTooltipAttr}>${escapeHtml(TableCompute.resolveLabel(c.label, windowStarts[ci]))}${periodHint}</th>`;
    });
    html += `${sumRight ? sumHeadTh() : ''}</tr></thead><tbody>`;
    let dataRowIndex = 0;
    visibleRows.forEach((row, ri) => {
      if (row.row_type === 'separator') {
        const span = visibleCols.length + 1 + (showRowSum ? 1 : 0);
        const sectionLabel = row.show_label && row.label ? escapeHtml(row.label) : '';
        const sepClasses = `tbl-separator-row${row.bold ? ' tbl-bold' : ''}`;
        html += `<tr class="${sepClasses}"><td colspan="${span}"><span class="tbl-separator-content"><span class="tbl-separator-line" aria-hidden="true"></span>${sectionLabel ? `<span class="tbl-separator-label">${sectionLabel}</span><span class="tbl-separator-line" aria-hidden="true"></span>` : ''}</span></td></tr>`;
        return;
      }
      if (isRowEmpty(row, ri)) return;
      const rowClasses = [];
      if (row.bold) rowClasses.push('tbl-bold');
      if (row.row_type === 'formula') rowClasses.push('tbl-formula-row');
      if (row.row_type === 'formula' && row.accent) rowClasses.push('tbl-row-accent');
      if (dataRowIndex % 2 === 1) rowClasses.push('tbl-zebra-alt');
      dataRowIndex += 1;
      html += `<tr${rowClasses.length ? ` class="${rowClasses.join(' ')}"` : ''}><td${labelWidthAttr}>${escapeHtml(row.label)}</td>${sumLeft ? sumTd(row, ri) : ''}`;
      visibleCols.forEach((col, ci) => {
        let cell = values[ci] && values[ci][ri];
        if (row.percent_of_total) cell = TableCompute.percentOfTotalCell(cell, sectionMemberCells(ci, ri));
        const cellClasses = ['tbl-num'];
        if (cell && cell.error) cellClasses.push('tbl-error');
        if (TableCompute.isComparisonColumn(col)) cellClasses.push('tbl-comparison-col');
        const decimals = row.percent_of_total ? '1' : col.decimals;
        const numberParts = TableCompute.cellNumberParts(cell, decimals, !!style.explicit_missing);
        const unit = style.show_units === false ? '' : TableCompute.cellUnit(cell);
        const comparisonIndex = TableCompute.comparisonIndexForBase(visibleCols, ci);
        const comparisonCell = comparisonIndex >= 0 ? (values[comparisonIndex] && values[comparisonIndex][ri]) : null;
        const deviation = style.show_deviation && !row.percent_of_total && comparisonIndex >= 0
          ? TableCompute.deviationText(cell, comparisonCell) : '';
        // Zusatzhinweis samt Vergleichswert, wenn die Prozentzahl auf
        // comparisonValue beruht (siehe deviationText()): die Zelle selbst
        // zeigt weiterhin den vollständigen Zeitraum, nur der Vergleich ist
        // auf "bisher vergangen" gekappt — ohne Erklärung UND Zahl wirkt das
        // wie ein Widerspruch zum vollen, daneben angezeigten Wert.
        const comparisonLabel = comparisonIndex >= 0
          ? TableCompute.resolveLabel(visibleCols[comparisonIndex].label, windowStarts[comparisonIndex]) : '';
        const comparisonValueStr = comparisonIndex >= 0
          ? TableCompute.comparisonValueText(comparisonCell, visibleCols[comparisonIndex].decimals) : '';
        const comparisonTimeStr = comparisonIndex >= 0
          ? TableCompute.comparisonElapsedTimeText(
              windowStarts[comparisonIndex], elapsedSeconds[comparisonIndex], visibleCols[comparisonIndex].range_key)
          : null;
        const deviationTitle = comparisonIndex < 0 ? '' : comparisonValueStr
          ? (comparisonTimeStr ? t('Gegenüber {label} bis {time}: {value}',{label: comparisonLabel, time: comparisonTimeStr, value: comparisonValueStr}) : t('Gegenüber {label}: {value}',{label: comparisonLabel, value: comparisonValueStr}))
          : t('Gegenüber {label}',{label: comparisonLabel});
        const widthCss = col.width ? `width:${col.width}px;max-width:${col.width}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;` : '';
        const heatmapCss = (col.heatmap && (row.row_type === 'entity' || row.row_type === 'group'))
          ? TableCompute.heatmapStyle(values[ci] && values[ci][ri], columnHeatmapRange(ci, ri)) : '';
        const cellStyleAttr = (widthCss || heatmapCss) ? ` style="${widthCss}${heatmapCss}"` : '';
        html += `<td class="${cellClasses.join(' ')}"${cellStyleAttr}><span class="tbl-cell"><span class="tbl-cell-number"><span class="tbl-number-whole">${escapeHtml(numberParts.whole)}</span><span class="tbl-number-separator">${escapeHtml(numberParts.separator)}</span><span class="tbl-number-fraction">${escapeHtml(numberParts.fraction)}</span></span>${unit ? `<span class="tbl-cell-unit">${escapeHtml(unit)}</span>` : ''}</span>${deviation ? `<span class="tbl-cell-deviation" data-tooltip-fixed="${escapeHtml(deviationTitle)}">${escapeHtml(deviation)}</span>` : ''}</td>`;
      });
      html += `${sumRight ? sumTd(row, ri) : ''}</tr>`;
    });
    html += '</tbody></table>';
    previewEl.innerHTML = html;
    // Höhe der Gruppen-Kopfzeile für "Header fixieren" bei zweistufigem
    // Kopf — dieselbe --tbl-group-header-h-Custom-Property wie in
    // table_editor.html (siehe dortiger Kommentar bei .tbl-style-sticky-
    // header tr.tbl-header-row th: sticky auf thead/tr funktioniert in
    // Safari nicht, deshalb pro th einzeln mit gemessenem top-Versatz).
    const tableEl = previewEl.querySelector('table');
    if (tableEl) {
      const groupRow = tableEl.querySelector('tr.tbl-group-header-row');
      const groupH = (groupRow && groupRow.offsetParent !== null) ? groupRow.getBoundingClientRect().height : 0;
      tableEl.style.setProperty('--tbl-group-header-h', groupH + 'px');
    }
  }

  // Baut dieselbe Linie+Füllfläche-Sparkline (SVG-Pfade, gerade Segmente) wie
  // die Statistik-Kacheln der Übersichtsseite (main.py _sparkline_paths(),
  // entities.html .sparkline/.area/.line) — hier als JS-Gegenstück, weil die
  // Werte-Kachel ihre Punkte client-seitig lädt statt sie beim Seitenaufbau
  // serverseitig fertig mitzubekommen.
  // padX 0 statt der stat-Kachel-üblichen 2 (siehe _sparkline_paths() in
  // main.py, für dieselbe Optik dort so übernommen): die Werte-Kachel legt
  // die Sparkline direkt unter Titel/Wert, ein seitlicher Versatz fiel dort
  // sichtbar als "mehr Rand als beim Text" auf. padY bleibt bei 2, um den
  // Linienstrich oben/unten nicht am Rand der Sparkline abzuschneiden — dort
  // störte der kleine Versatz optisch nicht.
  function sparklinePaths(values, width = 84, height = 28, padX = 0, padY = 2) {
    if (values.length < 2) return null;
    const lo = Math.min(...values), hi = Math.max(...values);
    const span = (hi - lo) || 1;
    const step = (width - 2 * padX) / (values.length - 1);
    const points = values.map((v, i) => [
      padX + i * step,
      padY + (height - 2 * padY) * (1 - (v - lo) / span),
    ]);
    const line = 'M' + points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L');
    const area = `${line} L${points[points.length - 1][0].toFixed(1)},${height} L${points[0][0].toFixed(1)},${height} Z`;
    return {line, area};
  }

  // Aktueller Wert + Alter + optionale Sparkline einer Werte-Kachel — ein
  // einziger Roh-Query-Fetch (letzte 24h) deckt alle drei ab. Bewusst NICHT
  // auf entities.last_value/last_ts (die Datenbankspalten) verlassen: die
  // werden nur über den echten Ingestion-Pfad gepflegt
  // (complete_ingest_event()) — Demo-/Importdaten, die diesen Pfad umgehen,
  // lassen last_value dauerhaft NULL, obwohl echte archivierte Werte
  // existieren (genau der gemeldete Bug: Kachel zeigt "–" trotz sichtbarer
  // Sparkline-Kurve). Der servergerenderte Anfangszustand (siehe
  // _dashboard_tiles_context() in main.py) bleibt als Platzhalter stehen,
  // falls dieser Fetch fehlschlägt oder keine Punkte liefert.
  // Zeitraum-Beschriftung einer Werte-Kachel — zweite Kopie von
  // _TILE_RANGE_LABELS in main.py: der Server beschriftet die erste Anzeige,
  // hier wird nach einer Änderung im Kachelmenü neu beschriftet, ohne die
  // Seite neu zu laden. Ein Test hält beide Kopien deckungsgleich.
  // [kalendarisch, rollierend]
  const TILE_RANGE_LABELS = {
    hour: [t('Std.'), t('60 Min.')], day: [t('Tag'), t('24 Std.')], week: [t('Woche'), t('7 Tage')],
    month: [t('Monat'), t('30 Tage')], year: [t('Jahr'), t('12 Monate')],
  };
  // Dasselbe Fenster ausgeschrieben, für den Tooltip: "Max 23,1" allein sagt
  // nicht, worüber.
  const TILE_RANGE_WINDOW = {
    hour: [t('in der laufenden Stunde'), t('in den letzten 60 Minuten')],
    day: [t('heute seit Mitternacht'), t('in den letzten 24 Stunden')],
    week: [t('in der laufenden Kalenderwoche'), t('in den letzten 7 Tagen')],
    month: [t('im laufenden Monat'), t('in den letzten 30 Tagen')],
    year: [t('im laufenden Jahr'), t('in den letzten 12 Monaten')],
  };
  // Rückfall, falls eine Antwort die Kürzel nicht mitbringt. Maßgeblich ist
  // ctx.metric_labels vom Server: dort hängt genau eines am Entitätstyp — bei
  // einem Zähler heißt die Summe "+", weil sie der Zuwachs des Zählerstands
  // ist (siehe _tile_metric_labels() in main.py).
  const TILE_METRIC_LABELS = {last: '', min: t('Min'), avg: 'Ø', max: t('Max'), sum: 'Σ'};

  function tileRangeLabel(el) {
    const pair = TILE_RANGE_LABELS[el.dataset.range] || TILE_RANGE_LABELS.day;
    return pair[el.dataset.continuous === 'true' ? 1 : 0];
  }

  // Erklärt eine Kennzahl im Klartext. Der heikle Fall ist der Zähler: dessen
  // Min/Ø/Max beziehen sich auf Bucket-Deltas, "Max" heißt dort "stärkster
  // Tag" und nicht t("größter Messwert"). Die Bucket-Größe kommt vom Server
  // (bucket_label), damit hier keine zweite Tabelle mit den Auflösungen des
  // Speichers entsteht, die still veralten könnte.
  function tileMetricTooltip(el, metric, serie, stichtagFenster = null) {
    const fenster = stichtagFenster || (TILE_RANGE_WINDOW[el.dataset.range] || TILE_RANGE_WINDOW.day)[
      el.dataset.continuous === 'true' ? 1 : 0
    ];
    const bucket = serie && serie.bucket_label;
    if (bucket) {
      const jeBucket = {
        min: t('schwächster {bucket}', {bucket: t(bucket)}), avg: t('Ø je {bucket}', {bucket: t(bucket)}),
        max: t('stärkster {bucket}', {bucket: t(bucket)}), sum: t('Zuwachs'),
      }[metric];
      return `${jeBucket} · ${fenster}`;
    }
    const messwert = {
      min: t('kleinster Messwert'), avg: t('Durchschnitt'),
      max: t('größter Messwert'), sum: t('Summe'),
    }[metric];
    return `${messwert} · ${fenster}`;
  }

  // ---- Bündelung -----------------------------------------------------
  // Statt eines Requests je Kachel: alle Kacheln, die im selben Durchgang
  // sichtbar werden, sammeln und nach (Zeitraum, rollierend, Auflösung,
  // Kennzahlen) gruppieren — je Gruppe ein Request an /api/entity-stats.
  // Das faule Nachladen bleibt erhalten: Kacheln unterhalb des Falzes bilden
  // beim Scrollen ihre eigene Gruppe.
  const pendingEntityTiles = new Set();
  let entityFlushScheduled = false;
  let entityFlushPromise = null;

  function renderEntityTile(el) {
    pendingEntityTiles.add(el);
    if (entityFlushScheduled) return entityFlushPromise;
    entityFlushScheduled = true;
    // setTimeout(0) statt eines Microtasks: der IntersectionObserver liefert
    // zwar alle gleichzeitig sichtbaren Kacheln in EINEM Callback, der
    // Auto-Refresh ruft aber je Kachel einzeln in einer forEach-Schleife.
    // Beide Fälle landen so im selben Sammelfenster.
    // Das zurückgegebene Promise löst sich erst nach dem eigentlichen Fetch
    // auf (flushEntityTiles() ist async) — mehrere Menü-Handler hängen ein
    // `await renderEntityTile(...)` dran, um nach dem Speichern sofort den
    // frischen Wert zu zeigen, statt bis zum nächsten Auto-Refresh zu warten.
    entityFlushPromise = new Promise(resolve => {
      setTimeout(() => resolve(flushEntityTiles()), 0);
    });
    return entityFlushPromise;
  }

  function tileNeedsAggregates(el) {
    // Aggregate braucht es nicht nur für die Kennzahlen-Zeile, sondern auch,
    // wenn der Hauptwert selbst eine Aggregation ist (Min/Ø/Max/Σ) — sonst
    // bleibt applyEntityTile() ohne serie.aggregates und rührt den Hauptwert
    // gar nicht an (Kachel bleibt leer, bis die Kennzahlen-Zeile aktiviert wird).
    return !!el.dataset.statsMetrics || (el.dataset.primaryMetric && el.dataset.primaryMetric !== 'last');
  }

  function tileGroupKey(el) {
    return [
      el.dataset.range || 'day',
      el.dataset.continuous === 'true' ? '1' : '0',
      el.dataset.sparklineResolution || 'raw',
      tileNeedsAggregates(el) ? '1' : '0',
    ].join('|');
  }

  async function flushEntityTiles() {
    entityFlushScheduled = false;
    const tiles = [...pendingEntityTiles].filter(el => el.isConnected);
    pendingEntityTiles.clear();
    if (!tiles.length) return;
    const groups = new Map();
    tiles.forEach(el => {
      const key = tileGroupKey(el);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(el);
    });
    await Promise.all([...groups.values()].map(loadEntityGroup));
  }

  async function loadEntityGroup(tiles) {
    const base = document.getElementById('dashboard-grid')?.dataset.appRoot || '';
    const erste = tiles[0];
    const params = new URLSearchParams({
      range: erste.dataset.range || 'day',
      continuous: erste.dataset.continuous === 'true' ? 'true' : 'false',
      resolution: erste.dataset.sparklineResolution || 'raw',
      stats: tileNeedsAggregates(erste) ? 'true' : 'false',
    });
    if (stichtag()) params.set('at', stichtag());
    // MAX_MULTI_QUERY_ENTITIES (25, siehe limits.py) — ein großes Dashboard
    // überschreitet das sonst und bekäme statt Daten eine 413.
    for (let i = 0; i < tiles.length; i += 25) {
      const stueck = tiles.slice(i, i + 25);
      // Dieselbe Entität kann mehrfach angeheftet sein (verschiedene
      // Dashboards teilen dieses Skript nicht, aber Größenvarianten auf einer
      // Seite schon) — die API liefert je Entität EINE Serie, deshalb hier
      // deduplizieren und unten wieder auf alle Kacheln verteilen.
      const ids = [...new Set(stueck.map(el => el.dataset.entityId))];
      let daten;
      try {
        const res = await fetch(
          `${base}/api/entity-stats?entity_ids=${encodeURIComponent(ids.join(','))}&${params}`
        );
        if (!res.ok) continue;
        daten = await res.json();
      } catch (e) {
        continue;  // Netzwerkfehler: der servergerenderte Platzhalter bleibt stehen
      }
      const nachId = new Map((daten.series || []).map(s => [s.entity_id, s]));
      // Mit Stichtag passt "heute seit Mitternacht" im Kennzahlen-Tooltip
      // nicht mehr — dann das tatsächliche Fenster.
      const fenster = stichtag() ? stichtagWindowText(daten.window_start, daten.window_end) : null;
      stueck.forEach(el => {
        const serie = nachId.get(el.dataset.entityId);
        if (serie) applyEntityTile(el, serie, fenster);
      });
    }
  }

  // ---- Darstellung ---------------------------------------------------
  // Bewusst NICHT auf entities.last_value/last_ts (die Datenbankspalten)
  // verlassen: die werden nur über den echten Ingestion-Pfad gepflegt
  // (complete_ingest_event()) — Demo-/Importdaten, die diesen Pfad umgehen,
  // lassen last_value dauerhaft NULL, obwohl echte archivierte Werte
  // existieren (genau der gemeldete Bug: Kachel zeigt "–" trotz sichtbarer
  // Sparkline-Kurve). Der servergerenderte Anfangszustand (siehe
  // _dashboard_tiles_context() in main.py) bleibt als Platzhalter stehen,
  // falls der Fetch fehlschlägt oder keine Daten liefert.
  // "21.10.2025" bzw. "01.10.2025 – 31.10.2025" für ein [Anfang, Ende)-Fenster
  // in Server-Sekunden.
  function stichtagWindowText(start, end) {
    if (start == null || end == null) return null;
    const tag = sec => {
      const p = window.ServerTime.parts(sec);
      return `${String(p.day).padStart(2, '0')}.${String(p.month).padStart(2, '0')}.${p.year}`;
    };
    const von = tag(start), bis = tag(end - 1);
    return von === bis ? von : `${von} – ${bis}`;
  }

  // ---- Zustands-Kachel (ROADMAP 1.29) --------------------------------
  // Aktueller Zustand mit Farbfeld, "seit", und darunter je nach Größe und
  // Darstellung (data-state-view): 1×1 der Streifen der letzten 24 h, ab zwei
  // Spalten die Anteile der letzten 7 Tage, oder der Donut über den Zeitraum
  // der Kachel mit dem aktuellen Zustand in der Mitte.
  function stateStripHtml(st) {
    const a = st.strip_start, b = st.strip_end;
    const span = Math.max(b - a, 1);
    const segs = StateEntities.segments(st.strip, b).filter(seg => seg.e > a);
    const parts = segs.map(seg => {
      const left = (Math.max(seg.s, a) - a) / span * 100;
      const width = (seg.e - Math.max(seg.s, a)) / span * 100;
      const label = escapeHtml(StateEntities.label(st.states, seg.idx));
      return `<i style="left:${left.toFixed(2)}%;width:${Math.max(width, 0.4).toFixed(2)}%;${StateEntities.swatchStyle(seg.idx)}" data-tooltip-fixed="${label}" data-tooltip-dot="${StateEntities.swatchStyle(seg.idx)}"></i>`;
    });
    return `<div class="dtile-state-strip">${parts.join('')}</div>`
      + `<div class="dtile-state-axis"><span>${t('−24 h')}</span><span>${t('jetzt')}</span></div>`;
  }

  function stateDaysHtml(st) {
    const order = st.states.map(x => x.idx);
    const rows = st.days.map(day => {
      const total = day.states.reduce((sum, [, seconds]) => sum + seconds, 0) || 1;
      const bars = [...day.states]
        .sort((x, y) => order.indexOf(x[0]) - order.indexOf(y[0]))
        .map(([idx, seconds]) => `<i style="width:${(seconds / total * 100).toFixed(2)}%;${StateEntities.swatchStyle(idx)}"`
          + ` data-tooltip-fixed="${escapeHtml(StateEntities.label(st.states, idx))}"`
          + ` data-tooltip-dot="${StateEntities.swatchStyle(idx)}" data-tooltip-value="${NumberFormat.fmtDuration(seconds)}"></i>`)
        .join('');
      const weekday = new Date(day.ts * 1000).toLocaleDateString(LOCALE, {weekday: 'short'});
      return `<div class="dtile-state-day"><span>${escapeHtml(weekday)}</span><div class="dtile-state-bar">${bars}</div></div>`;
    });
    return `<div class="dtile-state-days">${rows.join('')}</div>`;
  }

  function stateLegendHtml(st) {
    const total = st.durations.reduce((sum, [, seconds]) => sum + seconds, 0) || 1;
    return `<div class="dtile-state-legend">${st.durations
      .filter(([, seconds]) => seconds / total >= 0.01)
      .map(([idx, seconds]) => `<span><span class="state-dot" style="${StateEntities.swatchStyle(idx)}"></span>`
        + `${escapeHtml(StateEntities.label(st.states, idx))} ${StateEntities.fmtShare(seconds / total * 100)}</span>`)
      .join('')}</div>`;
  }

  function renderStateDonut(container, st) {
    container.innerHTML = '<div class="dtile-state-donut"><div class="dtile-state-donut-chart"></div>'
      + `<div class="dtile-state-donut-center"><b>${escapeHtml(StateEntities.label(st.states, st.idx))}</b>`
      + `<span>${t('jetzt')}</span></div></div>${stateLegendHtml(st)}`;
    const chartEl = container.querySelector('.dtile-state-donut-chart');
    const style = getComputedStyle(document.body);
    const pattern = StateEntities.unavailablePattern();
    const chart = echarts.init(chartEl);
    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'item', appendToBody: true,
        backgroundColor: style.getPropertyValue('--surface').trim(),
        textStyle: {color: style.getPropertyValue('--ink-muted').trim(), fontSize: scaledFont(12)},
        formatter: p => `${p.marker} <strong>${escapeHtml(p.name)}</strong><br>${NumberFormat.fmtDuration(p.value)} (${fmtCompactNumber(p.percent, 1)} %)`,
      },
      series: [{
        type: 'pie', radius: ['62%', '88%'], center: ['50%', '50%'],
        label: {show: false}, labelLine: {show: false},
        itemStyle: {borderColor: style.getPropertyValue('--surface').trim(), borderWidth: 2},
        data: st.durations.map(([idx, seconds]) => ({
          name: StateEntities.label(st.states, idx), value: seconds, itemStyle: {color: StateEntities.fill(idx, pattern)},
        })),
      }],
    });
  }

  function applyStateTile(el, serie) {
    const st = serie.state;
    const numberEl = el.querySelector('.dtile-entity-number');
    if (numberEl) {
      numberEl.textContent = StateEntities.label(st.states, st.idx);
      // Rohwert im Tooltip, wie überall bei Zuständen — data-tooltip-fixed
      // (App-Standard) statt nativem title, siehe fixed-tooltip.js.
      numberEl.setAttribute('data-tooltip-fixed', StateEntities.raw(st.states, st.idx));
      numberEl.setAttribute('data-tooltip-dot', StateEntities.swatchStyle(st.idx));
    }
    const swatchEl = el.querySelector('.dtile-state-swatch');
    if (swatchEl) swatchEl.setAttribute('style', StateEntities.swatchStyle(st.idx));
    const ageEl = el.querySelector('.dtile-entity-age');
    if (ageEl) {
      ageEl.textContent = st.since == null
        ? ''
        : t('seit {dauer}', {dauer: NumberFormat.fmtDuration(Date.now() / 1000 - st.since)});
    }
    const visualEl = el.querySelector('.dtile-state-visual');
    if (!visualEl) return;
    const instance = echarts.getInstanceByDom(visualEl.querySelector('.dtile-state-donut-chart') || visualEl);
    if (instance) instance.dispose();
    if (el.dataset.stateView === 'donut') { renderStateDonut(visualEl, st); return; }
    const wide = parseInt(el.closest('.dtile')?.dataset.gridCols || '1', 10) >= 2;
    visualEl.innerHTML = wide ? stateDaysHtml(st) : stateStripHtml(st);
  }

  function applyEntityTile(el, serie, stichtagFenster = null) {
    if (serie.state) { applyStateTile(el, serie); return; }
    const primary = el.dataset.primaryMetric || 'last';
    const dezimal = el.dataset.decimals === 'auto' ? null : parseInt(el.dataset.decimals, 10);
    const istSchalter = el.dataset.isSwitch === 'true';
    const wert = primary === 'last'
      ? serie.last
      : (serie.aggregates ? serie.aggregates[primary] : null);

    const numberEl = el.querySelector('.dtile-entity-number');
    if (numberEl && wert != null) {
      // "An"/"Aus" nur für den Momentanwert — eine Summe über Schalter ist
      // eine Einschaltdauer in Sekunden, kein Zustand.
      numberEl.textContent = istSchalter && primary === 'last'
        ? (wert ? 'An' : t('Aus'))
        : (istSchalter && primary === 'sum'
            ? NumberFormat.fmtDuration(wert)
            : NumberFormat.fmt(wert, dezimal));
    } else if (numberEl && serie.aggregates && primary !== 'last') {
      numberEl.textContent = '–';
    }

    const ageEl = el.querySelector('.dtile-entity-age');
    const secondsAgo = serie.last_ts == null ? null : Date.now() / 1000 - serie.last_ts;
    if (ageEl) {
      // Im Rückblick wäre "vor 1 Jahr" wahr, aber nutzlos — dann der Zeitpunkt.
      ageEl.textContent = serie.last_ts == null
        ? t('nie')
        : (stichtag()
            ? t('Stand {zeit}', {zeit: new Date(serie.last_ts * 1000).toLocaleString(LOCALE, window.ServerTime.withZone({
              day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
            }))})
            : t('vor {dauer}', {dauer: NumberFormat.fmtDuration(secondsAgo)}));
    }
    // Nur der Kartenrahmen zeigt "veraltet" an (siehe .dtile-entity.is-warn/
    // is-stale), der Wert bleibt immer schwarz. Die Schwellen kommen je Kachel
    // vom Server (data-stale-warn-after/-stale-after, Stufe stale_mode, siehe
    // staleness.py); leer heißt "nie markieren". Hängt weiterhin am letzten
    // ROHWERT, auch wenn die große Zahl eine Aggregation ist: "veraltet"
    // meint die Entität, nicht die Kennzahl.
    const tileEl = el.closest('.dtile');
    if (tileEl) {
      const warnAfter = parseFloat(el.dataset.staleWarnAfter);
      const staleAfter = parseFloat(el.dataset.staleStaleAfter);
      // Im Rückblick ist jeder Wert "alt" — markiert wird nur die Gegenwart.
      const mark = !stichtag() && secondsAgo != null && !Number.isNaN(warnAfter) && !Number.isNaN(staleAfter);
      tileEl.classList.toggle('is-warn', mark && secondsAgo > warnAfter && secondsAgo <= staleAfter);
      tileEl.classList.toggle('is-stale', mark && secondsAgo > staleAfter);
    }

    el.querySelectorAll('.dtile-entity-stat').forEach(statEl => {
      const metric = statEl.dataset.metric;
      const v = statEl.querySelector('.v');
      const zahl = serie.aggregates ? serie.aggregates[metric] : null;
      if (v) {
        v.textContent = zahl == null
          ? '–'
          : (istSchalter && metric === 'sum'
              ? NumberFormat.fmtDuration(zahl)
              : NumberFormat.fmt(zahl, dezimal));
      }
      // data-tooltip-fixed statt data-tooltip: .dtile-body hat
      // overflow:hidden, ein ::after-Tooltip würde am Kachelrand
      // abgeschnitten (siehe fixed-tooltip.js).
      statEl.setAttribute('data-tooltip-fixed', tileMetricTooltip(el, metric, serie, stichtagFenster));
    });

    const sparklineEl = el.querySelector('.dtile-entity-sparkline');
    if (sparklineEl) {
      // Das Ausdünnen ist serverseitig passiert (resolution), hier nur noch
      // zeichnen.
      const paths = sparklinePaths((serie.points || []).map(p => p.value));
      sparklineEl.innerHTML = paths
        ? `<svg class="sparkline" viewBox="0 0 84 28" preserveAspectRatio="none">`
          + `<path class="area" d="${paths.area}"/><path class="line" d="${paths.line}"/></svg>`
        : '';
    }
  }

  function setup() {
    const chartTiles = document.querySelectorAll('.dtile-body[data-entity-ids]');
    const tableTiles = document.querySelectorAll('.dtile-body[data-columns]');
    const entityTiles = document.querySelectorAll('.dtile-entity-body[data-entity-id]');
    if (!chartTiles.length && !tableTiles.length && !entityTiles.length) return;
    if (observer) observer.disconnect();
    observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const el = entry.target;
          renderedTiles.add(el);
          if (el.classList.contains('dtile-entity-body')) renderEntityTile(el);
          else if (el.dataset.columns != null) renderTableTile(el);
          else renderTile(el);
          observer.unobserve(el);
        }
      });
    }, {rootMargin: '150px'});
    chartTiles.forEach(el => observer.observe(el));
    tableTiles.forEach(el => observer.observe(el));
    entityTiles.forEach(el => observer.observe(el));
    setupSizePickers();
    setupDragAndDrop();
    setupLegendToggles();
    setupAutoRefresh();
  }

  // Abweichungs-Tooltip der Vergleichstabellen-Kacheln (data-tooltip-fixed,
  // siehe renderTableTile()) — Mechanik in static/js/fixed-tooltip.js
  // ausgelagert, weil table_editor.html (volle Tabellen-Bearbeitung, hat
  // denselben Tooltip) dieses Skript hier NICHT lädt. Dort auch die
  // Begründung fürs position:fixed-Vorgehen statt des generischen
  // [data-tooltip]::after-Systems.

  // Lädt bereits sichtbar gewesene Kacheln periodisch neu, solange der Tab
  // sichtbar ist — vorher blieb ein offen gelassenes Dashboard beliebig lange
  // auf dem Stand des letzten manuellen Reloads stehen (Konzept-Diskussion
  // "Seitenrefresh"). setInterval statt requestAnimationFrame-Loop: die Werte
  // ändern sich höchstens im Minutentakt, ein exakteres Timing bringt nichts.
  // Läuft auch im Hintergrund weiter (der Browser drosselt Intervalle
  // inaktiver Tabs ohnehin selbst), die visibilityState-Prüfung im Tick
  // spart zusätzlich die Server-Anfragen selbst, nicht nur deren Timing.
  function rerenderTiles() {
    renderedTiles.forEach(el => {
      if (!el.isConnected) { renderedTiles.delete(el); return; }
      if (el.classList.contains('dtile-entity-body')) renderEntityTile(el);
      else if (el.dataset.columns != null) renderTableTile(el);
      else renderTile(el);
    });
  }

  // Ein Rückblick (Stichtag in der Vergangenheit) ändert sich nicht mehr —
  // dort pausiert der Auto-Refresh, siehe Hinweis über den Kacheln.
  function setupAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
      if (document.visibilityState !== 'visible' || stichtag()) return;
      rerenderTiles();
    }, DASHBOARD_REFRESH_INTERVAL_MS);
  }

  // Neuer Stichtag: sichtbare Kacheln sofort neu laden. Noch nie gezeichnete
  // lesen ihn, sobald sie in den Sichtbereich kommen.
  window.addEventListener('dashboard-stichtag', rerenderTiles);

  function setupSizePickers() {
    const grid = document.getElementById('dashboard-grid');
    const base = grid?.dataset.appRoot || '';
    const dashboardId = parseInt(grid?.dataset.dashboardId || '1', 10);
    // Präziser Modus verdoppelt Gitter/Zeilenhöhe (siehe .dashboard-grid.is-
    // precise) — die "ab wann passt eine Legende rein"-Schwelle muss deshalb
    // mitwachsen, sonst würde sie in einer nur halb so großen Kachel (im
    // feineren Gitter dieselbe Zellenzahl, aber kleinere Zellen) fälschlich
    // als "passt" gelten. 3 statt einer exakten Verdopplung auf 4 (Konzept-
    // Wunsch: 3×3 im Präzisen Modus soll reichen).
    const legendThreshold = grid?.dataset.precise === 'true' ? 3 : 2;
    document.querySelectorAll('.dtile-menu').forEach(control => {
      const tile = control.closest('.dtile[data-item-id]');
      const cells = Array.from(control.querySelectorAll('.dtile-size-cell'));
      const preview = control.querySelector('.dtile-size-preview');
      const current = control.querySelector('.dtile-size-picker-head strong');
      const trigger = control.querySelector('.dtile-menu-btn');
      if (!tile || !cells.length || !preview || !current || !trigger) return;
      // Nur bei Chart-Kacheln vorhanden (siehe _dashboard_tile_menu.html) —
      // Vergleichstabellen haben keine Legende. Der ganze Wrapper (Divider +
      // Zeile) wird zusammen versteckt, siehe Kommentar im Template.
      const legendRow = control.querySelector('.dtile-legend-wrap');
      // Nur bei Werte-Kacheln vorhanden (siehe _dashboard_tile_menu.html).
      const sparklineCheckbox = control.querySelector('.dtile-sparkline-checkbox');
      const sparklineResolutionCells = Array.from(control.querySelectorAll('.dtile-sparkline-resolution-cell'));
      const showAgeCheckbox = control.querySelector('.dtile-show-age-checkbox');
      const staleModeCells = Array.from(control.querySelectorAll('.dtile-stale-mode-cell'));
      // Nur bei Zustands-Kacheln (ROADMAP 1.29): Streifen oder Donut.
      const stateViewCells = Array.from(control.querySelectorAll('.dtile-state-view-cell'));
      const showPeriodCheckbox = control.querySelector('.dtile-show-period-checkbox');
      const legendCheckbox = control.querySelector('.dtile-legend-checkbox');
      const dtileBody = tile.querySelector('.dtile-body');

      // Bedienelemente dürfen nie den Drag-Vorgang der äußeren Kachel starten.
      control.addEventListener('dragstart', e => e.preventDefault());
      const paintPreview = (cols, rows) => {
        cells.forEach(cell => {
          cell.classList.toggle(
            'is-preview',
            parseInt(cell.dataset.cols, 10) <= cols && parseInt(cell.dataset.rows, 10) <= rows
          );
        });
        preview.textContent = `${cols}×${rows}`;
      };
      const clearPreview = () => {
        cells.forEach(cell => cell.classList.remove('is-preview'));
        preview.textContent = `${tile.dataset.gridCols}×${tile.dataset.gridRows}`;
      };

      cells.forEach(cell => {
        cell.addEventListener('mouseenter', () => {
          paintPreview(parseInt(cell.dataset.cols, 10), parseInt(cell.dataset.rows, 10));
        });
        cell.addEventListener('focus', () => {
          paintPreview(parseInt(cell.dataset.cols, 10), parseInt(cell.dataset.rows, 10));
        });
        cell.addEventListener('click', async () => {
          const gridCols = parseInt(cell.dataset.cols, 10);
          const gridRows = parseInt(cell.dataset.rows, 10);
          const isEntityTile = tile.dataset.itemType === 'entity';
          try {
            const response = await fetch(`${base}/dashboard/${isEntityTile ? 'entity-size' : 'size'}`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(isEntityTile ? {
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10),
                grid_cols: gridCols, grid_rows: gridRows,
              } : {
                dashboard_id: dashboardId,
                item_type: tile.dataset.itemType,
                item_id: parseInt(tile.dataset.itemId, 10),
                grid_cols: gridCols,
                grid_rows: gridRows,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            tile.dataset.gridCols = String(gridCols);
            tile.dataset.gridRows = String(gridRows);
            tile.style.setProperty('--tile-cols', String(gridCols));
            tile.style.setProperty('--tile-rows', String(gridRows));
            current.textContent = `${gridCols}×${gridRows}`;
            cells.forEach(option => {
              option.classList.toggle(
                'is-selected',
                parseInt(option.dataset.cols, 10) <= gridCols && parseInt(option.dataset.rows, 10) <= gridRows
              );
            });
            clearPreview();

            // Legende (Kachelmenü-Toggle) nur ab 2×2 sichtbar/bedienbar — beim
            // Verkleinern ausblenden (Wert bleibt gespeichert, siehe
            // set_dashboard_pin_legend()), beim Vergrößern ggf. wieder
            // einblenden, ohne neu zu laden (legendCache).
            const fitsLegend = gridCols >= legendThreshold && gridRows >= legendThreshold;
            if (legendRow) legendRow.style.display = fitsLegend ? '' : 'none';
            if (dtileBody && dtileBody.dataset.showLegend !== undefined) {
              const legend = legendCache.get(tile.dataset.itemId);
              if (legend) renderLegend(dtileBody, legend, fitsLegend && dtileBody.dataset.showLegend === 'true');
            }

            // Größere Tabellen dürfen den zusätzlichen Platz sofort nutzen;
            // Charts brauchen nach der CSS-Grid-Änderung ein explizites Resize.
            const tableBody = tile.querySelector('.dtile-body[data-columns]');
            if (tableBody) await renderTableTile(tableBody);
            // Langzeit-Kachel: 1×1 zeichnet nur das angezeigte Jahr und den Ø.
            if (dtileBody?.dataset.longtermActive === 'true') await renderTile(dtileBody);
            // Zustands-Kachel: ab zwei Spalten Anteile je Tag statt Streifen.
            const stateBody = tile.querySelector('.dtile-entity-body[data-is-state="true"]');
            if (stateBody) await renderEntityTile(stateBody);
            const chart = instances.get(tile.dataset.itemId);
            requestAnimationFrame(() => chart && chart.resize());
          } catch (e) {
            trigger.title = t('Größe konnte nicht gespeichert werden');
          }
        });
      });
      control.querySelector('.dtile-size-grid').addEventListener('mouseleave', clearPreview);
      control.addEventListener('focusout', e => {
        if (!control.contains(e.relatedTarget)) clearPreview();
      });

      if (legendCheckbox && dtileBody) {
        legendCheckbox.addEventListener('change', async () => {
          const showLegend = legendCheckbox.checked;
          legendCheckbox.disabled = true;
          try {
            const response = await fetch(`${base}/dashboard/legend`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId,
                item_type: tile.dataset.itemType,
                item_id: parseInt(tile.dataset.itemId, 10),
                show_legend: showLegend,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            dtileBody.dataset.showLegend = String(showLegend);
            const legend = legendCache.get(tile.dataset.itemId);
            if (legend) renderLegend(dtileBody, legend, showLegend);
          } catch (e) {
            legendCheckbox.checked = !showLegend;
          } finally {
            legendCheckbox.disabled = false;
          }
        });
      }

      if (sparklineCheckbox) {
        sparklineCheckbox.addEventListener('change', async () => {
          const showSparkline = sparklineCheckbox.checked;
          sparklineCheckbox.disabled = true;
          try {
            const response = await fetch(`${base}/dashboard/sparkline`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), show_sparkline: showSparkline,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const entityBody = tile.querySelector('.dtile-entity-body');
            if (entityBody) entityBody.dataset.showSparkline = String(showSparkline);
            let sparklineEl = tile.querySelector('.dtile-entity-sparkline');
            if (showSparkline) {
              if (!sparklineEl) {
                sparklineEl = document.createElement('div');
                sparklineEl.className = 'dtile-entity-sparkline';
                entityBody?.appendChild(sparklineEl);
              }
              if (entityBody) await renderEntityTile(entityBody);
            } else if (sparklineEl) {
              sparklineEl.remove();
            }
          } catch (e) {
            sparklineCheckbox.checked = !showSparkline;
          } finally {
            sparklineCheckbox.disabled = false;
          }
        });
      }

      const sparklineResolutionHead = control.querySelector('.dtile-sparkline-resolution-row')
        ?.previousElementSibling?.querySelector('strong');
      const SPARKLINE_RESOLUTION_LABELS = {raw: t('Rohdaten'), '5min': t('5 Min'), '15min': t('15 Min'), '30min': t('30 Min'), '1h': t('1 Std')};
      sparklineResolutionCells.forEach(cell => {
        cell.addEventListener('click', async () => {
          const resolution = cell.dataset.resolution;
          try {
            const response = await fetch(`${base}/dashboard/sparkline-resolution`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), resolution,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            sparklineResolutionCells.forEach(option => option.classList.toggle('is-selected', option === cell));
            if (sparklineResolutionHead) sparklineResolutionHead.textContent = SPARKLINE_RESOLUTION_LABELS[resolution];
            const entityBody = tile.querySelector('.dtile-entity-body');
            if (entityBody) {
              entityBody.dataset.sparklineResolution = resolution;
              if (entityBody.dataset.showSparkline === 'true') await renderEntityTile(entityBody);
            }
          } catch (e) {
            trigger.title = t('Sparkline-Auflösung konnte nicht gespeichert werden');
          }
        });
      });

      stateViewCells.forEach(cell => {
        cell.addEventListener('click', async () => {
          const stateView = cell.dataset.stateView;
          try {
            const response = await fetch(`${base}/dashboard/entity-state-view`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), state_view: stateView,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            stateViewCells.forEach(option => option.classList.toggle('is-selected', option === cell));
            const entityBody = tile.querySelector('.dtile-entity-body');
            if (entityBody) {
              entityBody.dataset.stateView = stateView;
              await renderEntityTile(entityBody);
            }
          } catch (e) {
            trigger.title = t('Darstellung konnte nicht gespeichert werden');
          }
        });
      });

      staleModeCells.forEach(cell => {
        cell.addEventListener('click', async () => {
          const staleMode = cell.dataset.staleMode;
          try {
            const response = await fetch(`${base}/dashboard/entity-stale-mode`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), stale_mode: staleMode,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const result = await response.json();
            staleModeCells.forEach(option => option.classList.toggle('is-selected', option === cell));
            const entityBody = tile.querySelector('.dtile-entity-body');
            if (entityBody) {
              entityBody.dataset.staleWarnAfter = result.stale_warn_after;
              entityBody.dataset.staleStaleAfter = result.stale_stale_after;
              await renderEntityTile(entityBody);
            }
          } catch (e) {
            trigger.title = t('Veraltet-Schwelle konnte nicht gespeichert werden');
          }
        });
      });

      if (showAgeCheckbox) {
        showAgeCheckbox.addEventListener('change', async () => {
          const showAge = showAgeCheckbox.checked;
          showAgeCheckbox.disabled = true;
          try {
            const response = await fetch(`${base}/dashboard/entity-show-age`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), show_age: showAge,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const entityBody = tile.querySelector('.dtile-entity-body');
            if (entityBody) entityBody.dataset.showAge = String(showAge);
            let ageEl = tile.querySelector('.dtile-entity-age');
            if (showAge) {
              if (!ageEl) {
                ageEl = document.createElement('span');
                ageEl.className = 'dtile-entity-age';
                ageEl.title = t('Letzte Aktualisierung');
                entityBody?.querySelector('.dtile-entity-value')?.appendChild(ageEl);
              }
              if (entityBody) await renderEntityTile(entityBody);
            } else if (ageEl) {
              ageEl.remove();
            }
          } catch (e) {
            showAgeCheckbox.checked = !showAge;
          } finally {
            showAgeCheckbox.disabled = false;
          }
        });
      }

      // Nachkommastellen-Override (Werte-Kacheln) — dieselbe kleine
      // Zellen-Reihe wie die Kachelgröße oben, statt eines nativen <select>:
      // passt optisch besser in den schmalen Popover und fügt sich neben dem
      // Größen-Picker als "noch eine Reihe kleiner Kacheln" nahtlos ein.
      // Zeitraum, Laufend/Rollierend, Hauptwert und Kennzahlen-Zeile teilen
      // sich EINEN Endpunkt: der Server rechnet die Abhängigkeiten aus (der
      // Hauptwert fällt aus der Zeile, nicht anwendbare Kennzahlen fliegen
      // raus) und schickt den fertigen Anzeigezustand zurück. Der Browser
      // baut die Kachel daraus neu auf, statt dieselben Regeln ein zweites
      // Mal zu implementieren.
      const rangeCells = Array.from(control.querySelectorAll('.dtile-range-cell'));
      const continuousCells = Array.from(control.querySelectorAll('.dtile-continuous-cell'));
      const primaryCells = Array.from(control.querySelectorAll('.dtile-primary-cell'));
      const statsCells = Array.from(control.querySelectorAll('.dtile-stats-cell'));
      const statsRow = control.querySelector('.dtile-stats-row');
      const statsCheckbox = control.querySelector('.dtile-stats-checkbox');
      const resolutionRow = control.querySelector('.dtile-sparkline-resolution-row');

      if (rangeCells.length) {
        // Rohwerte gibt es nur bis t("Woche") (MAX_RAW_QUERY_POINTS) — darüber
        // kommen die Sparkline-Punkte aus den Buckets der Abfrage und die
        // Auflösungs-Reihe hat keine Wirkung mehr. Ausgrauen statt
        // verschwinden lassen, wie beim Legenden-Schalter der Chart-Kacheln.
        const rawFaehig = ['hour', 'day', 'week'];
        const zeigeAufloesung = () => {
          if (!resolutionRow) return;
          const aus = !rawFaehig.includes(tile.querySelector('.dtile-entity-body')?.dataset.range || 'day');
          resolutionRow.classList.toggle('is-disabled', aus);
          resolutionRow.querySelectorAll('button').forEach(b => { b.disabled = aus; });
          resolutionRow.title = aus
            ? t('Bei Monat und Jahr zeichnet die Sparkline die Buckets der Abfrage — eine feinere Auflösung gibt es dort nicht.')
            : '';
        };

        // Baut Kennzeichen, Kennzahlen-Zeile und Zeitraum-Text neu auf. Die
        // Werte selbst bleiben leer; sie kommen aus dem anschließenden
        // renderEntityTile(), das ohnehin neu laden muss (anderer Zeitraum =
        // andere Daten).
        const uebernehmen = (ctx) => {
          const body = tile.querySelector('.dtile-entity-body');
          if (!body) return;
          body.dataset.range = ctx.range_key;
          body.dataset.continuous = String(ctx.continuous);
          body.dataset.primaryMetric = ctx.primary_metric;
          body.dataset.statsMetrics = ctx.stats_metrics.join(',');

          const main = body.querySelector('.dtile-entity-main');
          let badge = body.querySelector('.dtile-entity-metric');
          if (ctx.primary_label) {
            if (!badge) {
              badge = document.createElement('span');
              badge.className = 'dtile-entity-metric';
              main?.prepend(badge);
            }
            badge.textContent = ctx.primary_label;
          } else if (badge) {
            badge.remove();
          }

          body.querySelector('.dtile-entity-stats')?.remove();
          if (ctx.stats_metrics.length) {
            const zeile = document.createElement('div');
            zeile.className = 'dtile-entity-stats';
            ctx.stats_metrics.forEach((metric, i) => {
              if (i) {
                const sep = document.createElement('span');
                sep.className = 'sep';
                sep.textContent = '·';
                zeile.appendChild(sep);
              }
              const stat = document.createElement('span');
              stat.className = 'dtile-entity-stat';
              stat.dataset.metric = metric;
              stat.innerHTML = `<span class="k"></span><span class="v">–</span>`;
              const kuerzel = ctx.metric_labels || TILE_METRIC_LABELS;
              stat.querySelector('.k').textContent = kuerzel[metric] || metric;
              zeile.appendChild(stat);
            });
            if (ctx.show_period) {
              const periode = document.createElement('span');
              periode.className = 'dtile-entity-period';
              periode.textContent = ctx.range_label;
              zeile.appendChild(periode);
            }
            body.querySelector('.dtile-entity-sparkline')
              ? body.insertBefore(zeile, body.querySelector('.dtile-entity-sparkline'))
              : body.appendChild(zeile);
          }

          // Zeitraum (in der Wert-Zeile nur dann, wenn es keine
          // Kennzahlen-Zeile gibt, die ihn trägt) und Alter sind unabhängig
          // voneinander — schließen sich nicht aus (siehe gleichlautender
          // Kommentar in _dashboard_tiles.html).
          const wertZeile = body.querySelector('.dtile-entity-value');
          wertZeile?.querySelector('.dtile-entity-period')?.remove();
          if (ctx.show_period_in_value_row) {
            const periode = document.createElement('span');
            periode.className = 'dtile-entity-period';
            periode.textContent = ctx.range_label;
            wertZeile?.appendChild(periode);
          }
          const alter = wertZeile?.querySelector('.dtile-entity-age');
          if (alter) alter.hidden = body.dataset.showAge !== 'true';

          // Popup-Zustand nachziehen: der Hauptwert sperrt seinen Eintrag in
          // der Kennzahlen-Zeile, deshalb reicht kein reines Umfärben.
          rangeCells.forEach(c => c.classList.toggle('is-selected', c.dataset.range === ctx.range_key));
          continuousCells.forEach(c => c.classList.toggle('is-selected', (c.dataset.continuous === 'true') === ctx.continuous));
          primaryCells.forEach(c => c.classList.toggle('is-selected', c.dataset.primary === ctx.primary_metric));
          statsCells.forEach(c => {
            const gesperrt = !ctx.available_metrics.includes(c.dataset.metric) || c.dataset.metric === ctx.primary_metric;
            c.classList.toggle('is-selected', ctx.stats_metrics.includes(c.dataset.metric));
            c.classList.toggle('is-off', gesperrt);
            c.disabled = gesperrt;
            c.title = gesperrt
              ? (c.dataset.metric === ctx.primary_metric ? t('Steht schon als Hauptwert') : t('Für diese Entität keine sinnvolle Kennzahl'))
              : '';
          });
          if (statsRow) statsRow.hidden = !statsCheckbox?.checked;
          zeigeAufloesung();
          renderEntityTile(body);
        };

        const senden = async (aenderung) => {
          try {
            const response = await fetch(`${base}/dashboard/entity-metrics`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), ...aenderung,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            uebernehmen(await response.json());
          } catch (e) {
            trigger.title = t('Kennzahlen konnten nicht gespeichert werden');
          }
        };

        // Eigener Endpunkt (nicht über senden()/entity-metrics, siehe
        // Kommentar dort), aber dieselbe uebernehmen()-Anwendung: show_period
        // wirkt auf dieselbe Stelle (Kennzahlen-Zeile ODER Wert-Bereich) wie
        // Zeitraum/Hauptwert/Kennzahlen-Zeile.
        if (showPeriodCheckbox) {
          showPeriodCheckbox.addEventListener('change', async () => {
            const showPeriod = showPeriodCheckbox.checked;
            showPeriodCheckbox.disabled = true;
            try {
              const response = await fetch(`${base}/dashboard/entity-show-period`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), show_period: showPeriod,
                }),
              });
              if (!response.ok) throw new Error(`HTTP ${response.status}`);
              uebernehmen(await response.json());
            } catch (e) {
              showPeriodCheckbox.checked = !showPeriod;
              trigger.title = t('Zeitraum-Anzeige konnte nicht gespeichert werden');
            } finally {
              showPeriodCheckbox.disabled = false;
            }
          });
        }

        rangeCells.forEach(c => c.addEventListener('click', () => senden({range_key: c.dataset.range})));
        continuousCells.forEach(c => c.addEventListener('click', () => senden({continuous: c.dataset.continuous === 'true'})));
        primaryCells.forEach(c => c.addEventListener('click', () => senden({primary_metric: c.dataset.primary})));
        statsCells.forEach(c => c.addEventListener('click', () => {
          const gewaehlt = statsCells.filter(x => x.classList.contains('is-selected')).map(x => x.dataset.metric);
          const neu = c.classList.contains('is-selected')
            ? gewaehlt.filter(m => m !== c.dataset.metric)
            : [...gewaehlt, c.dataset.metric];
          senden({stats_metrics: neu});
        }));
        if (statsCheckbox) {
          statsCheckbox.addEventListener('change', () => {
            if (statsRow) statsRow.hidden = !statsCheckbox.checked;
            if (statsCheckbox.checked) {
              // Beim Einschalten eine sinnvolle Vorbelegung statt einer leeren
              // Zeile: die verfügbaren Kennzahlen ohne den Hauptwert.
              const body = tile.querySelector('.dtile-entity-body');
              const primary = body?.dataset.primaryMetric || 'last';
              const moeglich = statsCells
                .filter(x => !x.classList.contains('is-off') || x.dataset.metric !== primary)
                .filter(x => x.dataset.metric !== primary && !x.disabled)
                .map(x => x.dataset.metric);
              senden({stats_metrics: moeglich});
            } else {
              senden({stats_metrics: []});
            }
          });
        }
        zeigeAufloesung();
      }

      const decimalsCells = Array.from(control.querySelectorAll('.dtile-decimals-cell'));
      const decimalsHead = control.querySelector('.dtile-decimals-picker-head strong');
      const DECIMALS_HEAD_LABELS = {auto: t('Auto'), '0': '0', '1': '1', '2': '2', '3': '3'};
      decimalsCells.forEach(cell => {
        cell.addEventListener('click', async () => {
          const decimals = cell.dataset.decimals;
          try {
            const response = await fetch(`${base}/dashboard/entity-decimals`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), decimals}),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            decimalsCells.forEach(option => option.classList.toggle('is-selected', option === cell));
            if (decimalsHead) decimalsHead.textContent = DECIMALS_HEAD_LABELS[decimals] || decimals;
            const entityBody = tile.querySelector('.dtile-entity-body');
            if (entityBody) {
              entityBody.dataset.decimals = decimals;
              await renderEntityTile(entityBody);
            }
          } catch (e) {
            trigger.title = t('Nachkommastellen konnten nicht gespeichert werden');
          }
        });
      });

      // Eigener Kachel-Titel (Werte-Kacheln) — speichert beim Verlassen des
      // Felds oder Enter, nicht bei jedem Tastendruck (sonst ein Request pro
      // Zeichen). Leeres Feld setzt auf den entity-eigenen friendly_name
      // zurück (siehe set_dashboard_entity_pin_title() in index.py).
      const titleInput = control.querySelector('.dtile-title-input');
      if (titleInput) {
        const titleEl = tile.querySelector('.dtile-title');
        const saveTitle = async () => {
          const title = titleInput.value.trim();
          try {
            const response = await fetch(`${base}/dashboard/entity-title`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({dashboard_id: dashboardId, pin_id: parseInt(tile.dataset.itemId, 10), title}),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            if (titleEl) titleEl.textContent = title || titleInput.placeholder;
          } catch (e) {
            trigger.title = t('Titel konnte nicht gespeichert werden');
          }
        };
        titleInput.addEventListener('blur', saveTitle);
        titleInput.addEventListener('keydown', e => {
          if (e.key === 'Enter') { e.preventDefault(); titleInput.blur(); }
        });
      }
    });
  }

  // Umsortieren per natives HTML5-Drag&Drop, keine zusätzliche Bibliothek
  // (Alpine/htmx/ECharts sind hier bereits die einzigen Abhängigkeiten). Der
  // gezogene Knoten wird während dragover live im DOM verschoben, statt nur
  // eine Ziel-Markierung anzuzeigen — dieselbe Kachel (inkl. schon
  // gerenderter ECharts-Instanz) bleibt dabei erhalten, es wird nichts neu
  // angelegt. Persistiert wird erst bei dragend, ein einzelner Request mit
  // der kompletten neuen Reihenfolge statt eines Requests je Zwischenschritt.
  //
  // Zwei getrennte Drag-Arten seit den Sektionen (_dashboard_tiles.html):
  // eine einzelne Kachel wandert zwischen den Mini-Rastern der Sektionen
  // ([data-section-grid]), ein ganzer Sektionskopf (.dsection) nimmt beim
  // Ziehen seine ganze Gruppe (.dgroup, Kopf + eigenes Mini-Raster) auf
  // einmal mit — sie stecken schon im selben Container, es muss dafür
  // nichts extra "eingesammelt" werden. Beide Drag-Arten laufen unabhängig
  // nebeneinander (draggedTile/draggedGroup), die jeweils andere Ebene
  // ignoriert dragover-Events, die nicht zu ihrer eigenen Art gehören.
  function setupDragAndDrop() {
    const shell = document.getElementById('dashboard-grid');
    if (!shell) return;
    let draggedTile = null;
    let draggedGroup = null;

    shell.querySelectorAll('.dtile[data-item-id]').forEach(tile => {
      tile.addEventListener('dragstart', (e) => {
        draggedTile = tile;
        tile.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        // setData ist in Firefox Voraussetzung dafür, dass dragover/drop
        // überhaupt feuern — der eigentliche Inhalt wird nicht ausgewertet,
        // die neue Reihenfolge liest persistOrder() direkt aus dem DOM.
        e.dataTransfer.setData('text/plain', `${tile.dataset.itemType}:${tile.dataset.itemId}`);
      });
      tile.addEventListener('dragend', () => {
        tile.classList.remove('dragging');
        if (draggedTile) persistOrder(shell);
        draggedTile = null;
      });
    });
    // Je Sektions-Raster statt einmal fürs ganze Dashboard, damit eine Kachel
    // gezielt in EIN bestimmtes Mini-Raster fällt statt (wie vor den
    // Sektionen) irgendwo auf der Seite. Am Raster selbst (statt nur je
    // Kachel) abgefangen, sonst bleibt ein Drop auf die Lücke zwischen zwei
    // Kacheln oder auf die "+"-Kachel ohne Wirkung.
    shell.querySelectorAll('[data-section-grid]').forEach(grid => {
      grid.addEventListener('dragover', (e) => {
        if (!draggedTile) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const addTile = grid.querySelector('.dtile-add');
        const after = Array.from(grid.querySelectorAll('.dtile[data-item-id]:not(.dragging)')).find(el => {
          const r = el.getBoundingClientRect();
          return e.clientY < r.top + r.height / 2
            || (e.clientY < r.bottom && (e.clientX - r.left) < r.width / 2);
        });
        if (after) grid.insertBefore(draggedTile, after);
        else if (addTile) grid.insertBefore(draggedTile, addTile);
        else grid.appendChild(draggedTile);
      });
    });

    shell.querySelectorAll('.dsection[data-item-id]').forEach(header => {
      header.addEventListener('dragstart', (e) => {
        draggedGroup = header.closest('.dgroup');
        draggedGroup.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', `section:${header.dataset.itemId}`);
      });
      header.addEventListener('dragend', () => {
        if (draggedGroup) {
          draggedGroup.classList.remove('dragging');
          // Verschiebt man ausgerechnet die Gruppe, die gerade die "+"-Kachel
          // trägt, muss diese der neuen letzten Gruppe folgen — sonst bliebe
          // sie mitten auf der Seite hängen, weil dieser Reorder-Request
          // (anders als Pin/Unpin) kein frisches Fragment vom Server
          // zurückbekommt, das sie neu platzieren würde.
          ensureAddTileAnchored(shell);
          persistOrder(shell);
        }
        draggedGroup = null;
      });
    });
    // Reihenfolge der GRUPPEN selbst — auf der Hülle statt je Sektionskopf,
    // damit auch ein Drop zwischen zwei Gruppen (nicht exakt auf einen
    // anderen Kopf) greift. Bricht während einer einzelnen Kachel nichts:
    // ohne aktiven Gruppen-Drag ist der Handler ein No-op, das darunter
    // liegende [data-section-grid]-dragover bleibt dafür zuständig.
    shell.addEventListener('dragover', (e) => {
      if (!draggedGroup) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const after = Array.from(shell.querySelectorAll('.dgroup:not(.dragging)')).find(g => {
        const r = g.getBoundingClientRect();
        return e.clientY < r.top + r.height / 2;
      });
      if (after) shell.insertBefore(draggedGroup, after);
      else shell.appendChild(draggedGroup);
    });
  }

  // Die "+"-Kachel hängt serverseitig immer an der zuletzt stehenden Gruppe
  // (main.py _dashboard_tiles_context()). Ein Gruppen-Reorder ändert das
  // clientseitig, ohne dass ein frisches Fragment vom Server kommt (siehe
  // persistOrder() unten) — diese Funktion zieht die "+"-Kachel deshalb nach
  // jedem Gruppen-Drag selbst in die jetzt tatsächlich letzte Gruppe um.
  function ensureAddTileAnchored(shell) {
    const addTile = shell.querySelector('.dtile-add');
    if (!addTile) return;
    const grids = shell.querySelectorAll('[data-section-grid]');
    const lastGrid = grids[grids.length - 1];
    if (lastGrid && addTile.parentElement !== lastGrid) lastGrid.appendChild(addTile);
  }

  async function persistOrder(shell) {
    // Eine Kachel kann in oder aus einer eingeklappten Sektion gezogen worden
    // sein — deren "N Kacheln ausgeblendet"-Zähler muss das mitbekommen.
    updateSectionHiddenCounts(shell);
    const base = shell.dataset.appRoot || '';
    const dashboardId = parseInt(shell.dataset.dashboardId || '1', 10);
    // Flach über alle Gruppen hinweg in ihrer aktuellen DOM-Reihenfolge —
    // ein Sektionskopf (falls vorhanden) kommt vor den Kacheln seiner
    // eigenen Gruppe, exakt wie /dashboard/reorder es für dashboard_pins
    // erwartet (reorder_dashboard_pins() kennt item_type='section' bereits
    // generisch mit, keine Sonderbehandlung nötig).
    const pins = [];
    shell.querySelectorAll('.dgroup').forEach(group => {
      const header = group.querySelector('.dsection[data-item-id]');
      if (header) {
        pins.push({item_type: 'section', item_id: parseInt(header.dataset.itemId, 10), item_entity_id: null});
      }
      group.querySelectorAll('.dtile[data-item-id]').forEach(el => {
        pins.push({
          item_type: el.dataset.itemType, item_id: parseInt(el.dataset.itemId, 10),
          item_entity_id: el.dataset.itemEntityId || null,
        });
      });
    });
    try {
      await fetch(`${base}/dashboard/reorder`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dashboard_id: dashboardId, pins}),
      });
    } catch (e) {
      // Reihenfolge bleibt clientseitig wie gezogen bestehen — ein erneutes
      // Laden der Seite würde bei einem Netzwerkfehler zwar auf den
      // zuletzt gespeicherten Stand zurückfallen, das ist aber kein Zustand,
      // der hier aktiv aufgelöst werden muss (kein Datenverlust, nur eine
      // im schlimmsten Fall nicht übernommene Umsortierung).
    }
  }

  // Sektionen ein-/ausklappen — rein clientseitig, nur lokal im Browser
  // gemerkt (kein Server-Feld, kein Sync über Geräte), dieselbe Konvention
  // wie Sortierung/Favoriten auf der Dashboard-Liste (card-browser.js). Per
  // Event-Delegation auf document statt in setup() gebunden: setup() bricht
  // auf einem Dashboard ganz ohne Chart-/Tabellen-/Werte-Kacheln früh ab
  // (nichts zu beobachten), Sektionen mit nur der "+"-Kachel müssten sich
  // aber trotzdem ein-/ausklappen lassen.
  function sectionCollapseKey(sectionId) {
    return `zeitarchiv.dashboard.section.${sectionId}.collapsed`;
  }

  function updateSectionHiddenCounts(root) {
    root.querySelectorAll('.dgroup[data-section-id]').forEach(group => {
      const countEl = group.querySelector('.dsection-hidden-count');
      if (!countEl) return;
      const n = group.querySelectorAll('[data-section-grid] .dtile:not(.dtile-add)').length;
      if (group.classList.contains('collapsed') && n > 0) {
        countEl.hidden = false;
        countEl.textContent = (n === 1 ? t('(1 Kachel ausgeblendet)') : t('({n} Kacheln ausgeblendet)',{n}));
      } else {
        countEl.hidden = true;
      }
    });
  }

  function applySectionCollapseState(root) {
    root.querySelectorAll('.dgroup[data-section-id]').forEach(group => {
      let collapsed = false;
      try {
        collapsed = localStorage.getItem(sectionCollapseKey(group.dataset.sectionId)) === '1';
      } catch (e) { /* ohne gemerkten Zustand bleibt die Sektion ausgeklappt */ }
      group.classList.toggle('collapsed', collapsed);
    });
    updateSectionHiddenCounts(root);
  }

  document.addEventListener('click', (e) => {
    const toggle = e.target.closest('.dsection-toggle');
    if (!toggle) return;
    const group = toggle.closest('.dgroup');
    if (!group) return;
    const collapsed = !group.classList.contains('collapsed');
    group.classList.toggle('collapsed', collapsed);
    try {
      localStorage.setItem(sectionCollapseKey(group.dataset.sectionId), collapsed ? '1' : '0');
    } catch (err) { /* Zustand gilt dann nur für diesen Seitenaufruf */ }
    updateSectionHiddenCounts(document);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { setup(); applySectionCollapseState(document); });
  } else {
    setup();
    applySectionCollapseState(document);
  }
  // Jeder #dashboard-grid-weite Swap (Sektion umbenennen/entfernen, Chart/
  // Tabelle/Entität anpinnen …) baut ALLE Kachelmenüs aus ihrem x-data neu
  // auf — ein gerade offenes Bearbeiten-Popup einer ANDEREN Kachel (der
  // Nutzer tippt dort z.B. gerade an den Einstellungen, während irgendwo
  // sonst auf der Seite eine dieser Aktionen feuert) verschwindet dabei
  // kommentarlos, weil der Server dessen offenen Zustand nicht kennt (nur
  // die eigene Kachel des jeweiligen Endpunkts kennt `auto_open_pin_id`,
  // siehe _dashboard_tile_menu.html). Deshalb hier vor jedem solchen Swap
  // merken, welche Kachel-Menüs offen waren (Kachel-Identität überlebt den
  // Swap: data-item-type + data-item-id), und sie danach wieder öffnen.
  // data-item-id ist für Entitäts-Kacheln seit dem Mehrfach-Anheften-Feature
  // die eigene Pin-ID statt eines Platzhalters — kein Sonderfall mehr nötig,
  // derselbe generische Schlüssel wie bei Chart/Tabelle identifiziert jede
  // Kachel eindeutig, auch mehrere derselben Entität.
  function tileIdentity(dtileEl) {
    if (!dtileEl) return null;
    return {type: dtileEl.dataset.itemType, id: dtileEl.dataset.itemId};
  }

  function findTileByIdentity(identity) {
    if (!identity) return null;
    const grid = document.getElementById('dashboard-grid');
    if (!grid) return null;
    return grid.querySelector(`.dtile[data-item-type="${identity.type}"][data-item-id="${CSS.escape(identity.id || '')}"]`);
  }

  let openMenusBeforeSwap = [];

  document.body.addEventListener('htmx:beforeRequest', (e) => {
    if (e.detail?.target?.id !== 'dashboard-grid') return;
    openMenusBeforeSwap = [...document.querySelectorAll('#dashboard-grid .dtile-menu.is-open')]
      .map(menu => tileIdentity(menu.closest('.dtile')))
      .filter(Boolean);
  });

  // Nach Pin/Unpin ersetzt htmx #dashboard-grid komplett (outerHTML) — alte
  // ECharts-Instanzen zeigen dann auf längst entfernte DOM-Knoten, deshalb
  // hier verwerfen statt sie weiter zu behalten; neue Kacheln bekommen beim
  // erneuten setup() ihre eigene frische Instanz.
  document.body.addEventListener('htmx:afterSettle', (e) => {
    if (e.target && e.target.id === 'dashboard-grid') {
      instances.forEach(c => c.dispose());
      instances.clear();
      setup();
      applySectionCollapseState(document);
      // Die entfernte Kachel (falls die ausgelöste Aktion selbst ein
      // "Vom Dashboard entfernen" war) findet findTileByIdentity() nicht
      // mehr — dann bleibt ihr Menü zu Recht zu, statt sich neu zu öffnen.
      openMenusBeforeSwap.forEach(identity => {
        const menu = findTileByIdentity(identity)?.querySelector('.dtile-menu');
        if (menu && typeof Alpine !== 'undefined') Alpine.$data(menu).menuOpen = true;
      });
      openMenusBeforeSwap = [];
    }
  });
})();
