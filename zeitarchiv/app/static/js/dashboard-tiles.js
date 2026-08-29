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
  const RESOLUTION_SECONDS = {
    hour: {medium: 5 * 60, coarse: 15 * 60},
    day: {medium: 30 * 60, coarse: 60 * 60},
    week: {medium: 6 * 60 * 60, coarse: 24 * 60 * 60},
    month: {medium: 24 * 60 * 60, coarse: 7 * 24 * 60 * 60},
    year: {medium: 30 * 24 * 60 * 60, coarse: 90 * 24 * 60 * 60},
    decade: {medium: 365 * 24 * 60 * 60, coarse: 2 * 365 * 24 * 60 * 60},
  };

  function resamplePoints(points, range, preset, aggregationType, windowStart) {
    const seconds = RESOLUTION_SECONDS[range] && RESOLUTION_SECONDS[range][preset];
    if (!seconds || !points.length || windowStart == null) return points;
    const groups = new Map();
    points.forEach(point => {
      const bucket = Math.floor((point.ts - windowStart) / seconds);
      const values = groups.get(bucket) || [];
      if (Number.isFinite(point.value)) values.push(point.value);
      groups.set(bucket, values);
    });
    return Array.from(groups.entries()).sort((a, b) => a[0] - b[0]).map(([bucket, values]) => ({
      ts: windowStart + bucket * seconds,
      value: aggregationType === 'standard'
        ? values.reduce((sum, value) => sum + value, 0) / values.length
        : values.reduce((sum, value) => sum + value, 0),
    })).filter(point => Number.isFinite(point.value));
  }

  const instances = new Map();  // chartId -> echarts instance
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

  // Zentraler Hook für eine spätere Sprachumschaltung (aktuell nur Deutsch) —
  // jede Datumsformatierung in dieser Datei läuft über Intl mit dieser einen
  // Konstante statt verstreuter 'de-DE'-Literale, damit ein künftiges
  // Sprach-Setting nur hier greifen muss.
  const LOCALE = 'de-DE';

  function fmtAxis(range, ts) {
    const d = new Date(ts * 1000);
    if (range === 'hour' || range === 'day') return d.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
    if (range === 'week' || range === 'month') return d.toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit'});
    if (range === 'decade') return d.toLocaleDateString(LOCALE, {year: 'numeric'});
    return d.toLocaleDateString(LOCALE, {month: 'short', year: 'numeric'});
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

  const LEGEND_METRIC_LABELS = {last: 'Aktuell', min: 'Min', max: 'Max', average: 'Ø', sum: 'Summe'};

  function escLegend(s) {
    return String(s).replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  // Chip-Variante (Standard) — dieselben Chips/Kennzahlen wie chart_editor.html
  // (.chart-legend-item aus app.css), hier zusätzlich klickbar (siehe
  // toggleTileLegendItem()), anders als die "static" (nicht umschaltbare)
  // Variante auf entity_detail.html.
  function renderLegendChips(items, legendMetrics, showStats, hiddenSet) {
    return items.map(item => {
      let values = '';
      if (showStats) {
        const parts = [];
        for (const key of ['last', 'min', 'max', 'average', 'sum']) {
          if (!legendMetrics.includes(key)) continue;
          if (key === 'sum' && item.sum === null) continue;
          parts.push(`<span>${LEGEND_METRIC_LABELS[key]} <strong>${escLegend(item[key])}</strong></span>`);
        }
        if (parts.length) values = `<span class="values">${parts.join('')}</span>`;
      }
      const inactive = hiddenSet.has(item.name) ? ' inactive' : '';
      return `<span class="chart-legend-item${inactive}" data-series="${escLegend(item.name)}" role="button" tabindex="0">`
           + `<span class="dot" style="background:${item.color};"></span>`
           + `<span class="name">${escLegend(item.name)}</span>${values}</span>`;
    }).join('');
  }

  // Tabellen-Variante ("Legenden-Stil": Tabelle, Optionen-Menü der Chart-Seite)
  // — dieselbe Spalten-Darstellung wie .chart-legend-table in chart_editor.html
  // (table.dt compact). Zeilen sind wie die Chips klickbare Serien-Umschalter
  // (dieselbe .chart-legend-table-row-Klasse/CSS wie dort, siehe app.css).
  function renderLegendTable(items, legendMetrics, showStats, hiddenSet) {
    const cols = showStats ? ['last', 'min', 'max', 'average', 'sum'].filter(k => legendMetrics.includes(k)) : [];
    const headerCells = cols.map(key => `<th>${LEGEND_METRIC_LABELS[key]}</th>`).join('');
    const rows = items.map(item => {
      const cells = cols.map(key => `<td>${escLegend(key === 'sum' && item.sum === null ? '—' : item[key])}</td>`).join('');
      const inactive = hiddenSet.has(item.name) ? ' inactive' : '';
      return `<tr class="chart-legend-table-row${inactive}" data-series="${escLegend(item.name)}" role="button" tabindex="0">`
           + `<td class="legend-name-col"><span class="legend-name-cell">`
           + `<span class="dot" style="background:${item.color};"></span>`
           + `<span class="name">${escLegend(item.name)}</span></span></td>${cells}</tr>`;
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

  function setupLegendToggles() {
    document.querySelectorAll('.dtile-legend').forEach(legendEl => {
      if (legendEl.dataset.toggleBound) return;
      legendEl.dataset.toggleBound = 'true';
      const handler = toggleTileLegendItem(legendEl);
      legendEl.addEventListener('click', handler);
      legendEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') handler(e);
      });
    });
  }

  async function renderTile(el) {
    // item-id sitzt auf der äußeren .dtile (auch Drag&Drop-Handle, siehe
    // setupDragAndDrop), die restlichen Daten auf dem inneren Link selbst.
    const chartId = el.closest('.dtile').dataset.itemId;
    const entityIds = JSON.parse(el.dataset.entityIds || '[]');
    const entityNames = JSON.parse(el.dataset.entityNames || '{}');
    const range = el.dataset.range || 'day';
    const continuous = el.dataset.continuous === 'true';
    const resolutionPreset = el.dataset.resolutionPreset || 'auto';
    // Siehe chart_editor.html (render(), dynamicYAxis-Konstante): bei
    // Auflösung "Tag" (singleBucket weiter unten) erzwungen aus, unabhängig
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
    // Nachkommastellen-Override (Optionen-Menü, "Darstellung") — bei "Auto"
    // behält jede Serie ihre eigene entities.decimals-Einstellung (s.decimals,
    // vom Server je Entität geliefert), sonst gilt dieser Wert für ALLE Serien
    // dieser Kachel einheitlich, genau wie in chart_editor.html (effectiveDecimals()).
    const decimalsOverride = el.dataset.decimals || 'auto';
    const effectiveDecimals = s => decimalsOverride === 'auto' ? s.decimals : parseInt(decimalsOverride, 10);
    // "Werte anzeigen" (Optionen-Menü) — bislang nicht an die Dashboard-Kachel
    // durchgereicht, siehe showValues-Verwendung im echartsSeries-Aufbau unten.
    const showValues = el.dataset.showValues === 'true';
    const chartEl = el.querySelector('.dtile-chart');
    if (!chartEl || !entityIds.length) return;

    const base = el.closest('#dashboard-grid')?.dataset.base || '.';
    const params = new URLSearchParams({
      entity_ids: entityIds.join(','), range, offset: '0', continuous: String(continuous),
      raw: String(timeline),
    });
    let data;
    try {
      const res = await fetch(`${base}/api/query-multi?${params}`);
      data = await res.json();
    } catch (e) {
      chartEl.innerHTML = '<div class="dtile-loading">Fehler beim Laden</div>';
      return;
    }
    const series = data.series || [];
    if (!series.some(s => s.points && s.points.length)) {
      chartEl.innerHTML = '<div class="dtile-loading">Keine Daten</div>';
      return;
    }
    // Dieselbe Farbzuordnung wie echartsSeries weiter unten (PALETTE nach
    // Serienindex) UND dieselbe Kennzahlen-Berechnung wie seriesStats() in
    // chart_editor.html (Min/Max/Ø/Summe/Aktuell) — hier separat gebaut,
    // damit die Legende VOR echarts.init() im DOM steht: der Chart-Container
    // bekommt sonst beim ersten Rendern die volle (noch legendenlose)
    // Kachelhöhe gemessen und die Legende schiebt ihn erst beim nächsten
    // Resize-Event auf seine endgültige Höhe.
    const legendItems = series.map((s, i) => {
      const values = (s.points || []).map(p => p.value).filter(Number.isFinite);
      const minima = (s.points || []).map(p => Number.isFinite(p.min) ? p.min : p.value).filter(Number.isFinite);
      const maxima = (s.points || []).map(p => Number.isFinite(p.max) ? p.max : p.value).filter(Number.isFinite);
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
      const sumValue = timeline && isSwitch ? switchOnDuration(s.points, data.window_end) : values.reduce((sum, v) => sum + v, 0);
      // Summe bei Schaltern ist immer eine Dauer in Sekunden — unabhängig
      // vom Anzeigemodus immer als h/m/s formatiert statt über
      // fmtCompactNumber()s generische 4-signifikante-Stellen-Rundung, die
      // bei größeren Sekundenwerten sichtbar ungenau wird (siehe
      // chart_editor.html/entity_detail.html, derselbe Fix).
      const sumFormatted = isSwitch ? NumberFormat.fmtDuration(sumValue) : formatted(sumValue);
      return {
        name: entityNames[s.entity_id] || s.friendly_name,
        color: PALETTE[i % PALETTE.length],
        last: values.length ? formatted(values[values.length - 1]) : '—',
        min: minima.length ? formatted(Math.min(...minima)) : '—',
        max: maxima.length ? formatted(Math.max(...maxima)) : '—',
        average: values.length ? formatted(values.reduce((sum, v) => sum + v, 0) / values.length) : '—',
        sum: hasSum ? (values.length ? sumFormatted : '—') : null,
      };
    });
    const legendMetrics = JSON.parse(el.dataset.legendMetrics || '["sum"]');
    const showStats = el.dataset.chartStats === 'true';
    const legendStyle = el.dataset.legendStyle || 'chips';
    const legend = {items: legendItems, legendMetrics, showStats, style: legendStyle};
    legendCache.set(chartId, legend);
    const tileEl = el.closest('.dtile');
    const legendVisible = el.dataset.showLegend === 'true'
      && parseInt(tileEl?.dataset.gridCols || '1', 10) >= 2
      && parseInt(tileEl?.dataset.gridRows || '1', 10) >= 2;
    renderLegend(el, legend, legendVisible);

    chartEl.innerHTML = '';
    let chart = instances.get(chartId);
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
    if (timeline) {
      renderTileTimeline(chart, series, entityNames, data, range, animation, style, borderColor, inkFaint, surface);
      return;
    }

    // Eine Y-Achse je unterschiedlicher Einheit (genau wie auf der vollen
    // Chart-Seite, chart_editor.html) — ohne das teilen sich z. B. Watt- und
    // kWh-Werte dieselbe Achse, wodurch die kWh-Balken bei einer viel
    // größeren Watt-Spanne rechnerisch bei ~0 verschwinden, statt sichtbar zu
    // sein. Kompakt gehalten: kein Achsenname, nur die Zahl.
    const units = [...new Set(series.map(s => s.unit))];
    // Strengste (kleinste) Nachkommastellen-Einstellung aller Entitäten einer
    // gemeinsamen Achse — dieselbe Regel wie in chart_editor.html.
    const unitDecimals = new Map();
    series.forEach(s => {
      const d = effectiveDecimals(s);
      if (d == null) return;
      const current = unitDecimals.get(s.unit);
      if (current == null || d < current) unitDecimals.set(s.unit, d);
    });
    const yAxis = units.map((u, i) => {
      const decimals = unitDecimals.get(u);
      return {
        type: 'value',
        position: i % 2 === 0 ? 'left' : 'right',
        offset: Math.floor(i / 2) * 46,
        min: dynamicYAxis ? undefined : value => Math.min(0, value.min),
        max: dynamicYAxis ? undefined : value => Math.max(0, value.max),
        // Siehe chart_editor.html: ohne scale:true erzwingt ECharts bei einer
        // value-Achse per Default immer die Einbindung der Null, auch bei
        // undefined min/max — "Dynamische Y-Achse" hätte sonst keine
        // sichtbare Wirkung.
        scale: dynamicYAxis,
        axisLabel: {fontSize: scaledFont(10), color: inkFaint, formatter: v => fmtCompactNumber(v, decimals)},
        axisLine: {show: false},
        axisTick: {show: false},
        splitLine: {lineStyle: {color: borderColor, type: 'dashed'}},
      };
    });

    // Erste Serie mit genug angezeigten (resamplePoints()-) Punkten bestimmt
    // die Tooltip-Zeitstempel-Form (fmtTooltipTimestamp) — dieselbe Logik wie
    // chart_editor.html. Aus den resampelten, nicht den rohen Server-Punkten:
    // bei manuell gesetzter Auflösung (Kachel-Auflösung ungleich "auto")
    // resamplePoints() clientseitig gröber, die rohen Punkte wären dann
    // feiner als das, was im Tooltip tatsächlich zu sehen ist. Erst innerhalb
    // der Schleife unten gesetzt (dort liegen die resampelten displayPoints
    // vor), hier nur deklariert.
    let tooltipBucketSeconds = null;

    // Siehe chart_editor.html (render(), gleicher Name/Kommentar): "Tag"-
    // Auflösung bucketet auf genau EINEN Wert je Entität — auf der sonst
    // üblichen Zeit-Achse säße dieser eine Balken winzig am linken Rand
    // (windowStart), der Rest der auf den ganzen Tag gespreizten Achse
    // bliebe leer. Eine Kategorie-Achse mit einer Kategorie lässt ECharts
    // die Balken mehrerer Entitäten stattdessen automatisch und gut
    // sichtbar nebeneinander anordnen.
    const singleBucket = resolutionPreset === 'full';
    const singleBucketLabel = data.window_start != null
      ? new Date(data.window_start * 1000).toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric'})
      : '';

    const echartsSeries = series.map((s, i) => {
      const color = PALETTE[i % PALETTE.length];
      const displayName = entityNames[s.entity_id] || s.friendly_name;
      let lineData;
      if (singleBucket) {
        // resamplePoints() kennt nur "medium"/"coarse" (RESOLUTION_SECONDS),
        // für "full" gibt sie unverändert alle Rohpunkte zurück — ohne diesen
        // eigenen Zweig würde jeder einzelne Bucket-Punkt als eigener
        // Tooltip-Eintrag auf derselben Kategorie (x=0) landen, statt zu
        // einem einzigen Balkenwert für den ganzen Zeitraum zusammengefasst
        // zu werden (sichtbar als lange Dopplung im Tooltip). Dieselbe Summe-
        // vs.-Durchschnitt-Regel wie in den Legenden-Kennzahlen oben.
        const rawValues = (s.points || []).map(p => p.value).filter(Number.isFinite);
        if (!rawValues.length) {
          lineData = [];
        } else {
          const isSumType = s.aggregation_type === 'counter' || s.aggregation_type === 'switch';
          const aggregate = isSumType
            ? rawValues.reduce((sum, v) => sum + v, 0)
            : rawValues.reduce((sum, v) => sum + v, 0) / rawValues.length;
          lineData = [[0, aggregate, s.unit, effectiveDecimals(s)]];
        }
      } else {
        const displayPoints = resamplePoints(
          s.points, range, resolutionPreset, s.aggregation_type, data.window_start
        );
        if (tooltipBucketSeconds == null) {
          tooltipBucketSeconds = detectResolutionSeconds(displayPoints);
        }
        lineData = displayPoints.map(p => [p.ts * 1000, p.value, s.unit, effectiveDecimals(s)]);
        if (s.chart_type === 'line' && lineData.length && data.window_end != null
            && lineData[lineData.length - 1][0] < data.window_end * 1000) {
          const last = lineData[lineData.length - 1];
          lineData.push([data.window_end * 1000, last[1], last[2], last[3]]);
        }
      }
      const cfg = {
        // Angepasster Anzeigename (chart_editor.html, "Angezeigte Namen") hat
        // Vorrang vor dem Entität-eigenen friendly_name — dieselbe Regel wie
        // beim Rendern der vollen Chart-Seite (dort this.entityNames[entity_id]).
        name: displayName,
        type: s.chart_type,
        yAxisIndex: units.indexOf(s.unit),
        data: lineData,
        lineStyle: {width: 2, color},
        itemStyle: {color},
        // "Werte anzeigen" (Optionen-Menü) — Zahl direkt über jedem Balken/
        // Punkt, dieselbe Konvention wie entity_detail.html (ohne Einheit,
        // die steht schon an der Y-Achse).
        label: {
          show: showValues,
          position: 'top',
          fontSize: scaledFont(10),
          color: inkMuted,
          formatter: params => fmtCompactNumber(params.value[1], params.value[3]),
        },
        barMaxWidth: singleBucket ? undefined : 28,
        // Ein Balken auf einer Zeit-Achse (kein boundaryGap, s. u.) sitzt mit
        // seiner Mitte GENAU auf dem Bucket-Zeitstempel — beim ersten/letzten
        // Bucket liegt die Hälfte der Balkenbreite dadurch zwangsläufig knapp
        // jenseits von min/max und würde ohne dies hart am Kachelrand
        // abgeschnitten wirken. Bei singleBucket (Kategorie-Achse) entfällt
        // dieses Problem.
        clip: singleBucket ? undefined : s.chart_type !== 'bar',
      };
      if (s.chart_type === 'line') {
        cfg.smooth = true;
        cfg.symbol = 'none';
        // Dezente Füllfläche unter der Linie — macht eine einzelne Kurve auf
        // den ersten Blick lesbarer, stört bei mehreren überlagerten Serien
        // dank der niedrigen Deckkraft nicht.
        cfg.areaStyle = {color, opacity: 0.08};
      } else {
        cfg.itemStyle.borderRadius = [3, 3, 0, 0];
      }
      return cfg;
    });

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
      grid: {left: 6, right: 6, top: 10, bottom: 20, containLabel: true},
      xAxis: singleBucket ? {
        // Genau eine Kategorie statt einer auf den ganzen Tag gespreizten
        // Zeit-Achse — siehe singleBucket oben.
        type: 'category',
        data: [singleBucketLabel],
        axisLabel: {fontSize: scaledFont(10), color: inkFaint},
        axisLine: {lineStyle: {color: borderColor}},
        axisTick: {show: false},
        splitLine: {show: false},
      } : {
        type: 'time',
        min: data.window_start != null ? data.window_start * 1000 : undefined,
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
      yAxis,
      tooltip: {
        trigger: 'axis',
        backgroundColor: surface,
        borderColor,
        textStyle: {color: inkMuted, fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        formatter: (params) => {
          if (!params.length) return '';
          // Bei singleBucket ist axisValue die Kategorie-Beschriftung (bereits
          // ein fertiges Datum), kein Millisekunden-Zeitstempel — fmtTooltip-
          // Timestamp() erwartet Millisekunden, deshalb hier direkt verwendet.
          const header = singleBucket ? singleBucketLabel : fmtTooltipTimestamp(params[0].axisValue, tooltipBucketSeconds);
          const rows = params.map(p => {
            const unit = p.data[2] || '';
            const decimals = p.data[3];
            return `<div style="display:flex;justify-content:space-between;gap:14px;">`
                 + `<span>${p.marker}${p.seriesName}</span>`
                 + `<strong style="margin-left:8px;">${fmtCompactNumber(p.data[1], decimals)}${unit ? ' ' + unit : ''}</strong></div>`;
          }).join('');
          return `<div style="margin-bottom:4px;color:${inkFaint};">${header}</div>${rows}`;
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

  // Kompakter Zeitstrahl für eine Kachel — eine Kategorie-Zeile je Entität,
  // dieselbe Balken-Technik wie renderTimelineMulti() in chart_editor.html,
  // hier nur mit den kleineren Kachel-Schriftgrößen/Abständen. Ohne
  // Zeilen-Beschriftung (der Name steht schon in der Kachel-Legende, siehe
  // renderTile()) — bei mehreren Zeilen in einer kleinen Kachel wäre dafür
  // ohnehin kaum Platz.
  function renderTileTimeline(chart, series, entityNames, data, range, animation, style, borderColor, inkFaint, surface) {
    const categories = series.map(s => entityNames[s.entity_id] || s.friendly_name);
    const items = [];
    series.forEach((s, catIndex) => {
      const color = PALETTE[catIndex % PALETTE.length];
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
          const [catIndex, startMs, endMs] = p.value;
          const pad = n => String(n).padStart(2, '0');
          const fmtTime = ms => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
          return `${categories[catIndex]}<br>${fmtTime(startMs)}–${fmtTime(endMs)} · <strong>${NumberFormat.fmtDuration((endMs - startMs) / 1000)}</strong>`;
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

  // Kompakte Vorschau einer Vergleichstabelle-Kachel — dieselbe Rechenlogik
  // wie der volle Editor (static/js/table-compute.js), nur reduziert
  // dargestellt: höchstens TABLE_TILE_MAX_ROWS/-COLS, ein "+N" statt der
  // übrigen (die Kachel ist zu klein für eine komplette Tabelle mit vielen
  // Zeilen/Spalten). Style-Optionen (Zebra/Rahmen/Dichte/Kopfzeile, siehe
  // TableCompute.styleClasses) wirken hier genauso wie in der vollen Ansicht.
  const TABLE_TILE_MAX_ROWS_PER_GRID_ROW = 5;
  const TABLE_TILE_MAX_COLS_PER_GRID_COL = 3;

  async function renderTableTile(el) {
    const columns = JSON.parse(el.dataset.columns || '[]');
    const rows = JSON.parse(el.dataset.rows || '[]');
    const style = JSON.parse(el.dataset.style || '{}');
    const previewEl = el.querySelector('.dtile-table-preview');
    if (!previewEl) return;

    const tile = el.closest('.dtile');
    const gridCols = Math.max(1, Math.min(3, parseInt(tile.dataset.gridCols || '1', 10)));
    const gridRows = Math.max(1, Math.min(3, parseInt(tile.dataset.gridRows || '1', 10)));
    const visibleCols = columns.slice(0, TABLE_TILE_MAX_COLS_PER_GRID_COL * gridCols);
    const visibleRows = rows.slice(0, TABLE_TILE_MAX_ROWS_PER_GRID_ROW * gridRows);
    const base = el.closest('#dashboard-grid')?.dataset.base || '.';
    let values, windowStarts;
    try {
      ({values, windowStarts} = await TableCompute.computeValues(base, columns, rows));
    } catch (e) {
      previewEl.innerHTML = '<div class="dtile-loading">Fehler beim Laden</div>';
      return;
    }

    const styleClasses = TableCompute.styleClasses(style);
    let html = `<table class="dt compact dtile-mini-table ${styleClasses}"><tr><th>&nbsp;</th>`;
    // Beschriftungs-Platzhalter (z. B. "{jahr}") wurden beim Speichern bewusst
    // NICHT aufgelöst (siehe table_editor.html save()) — sonst würde eine so
    // beschriftete Spalte hier ewig denselben, beim Speichern eingefrorenen
    // Wert zeigen statt sich mit der Zeit automatisch zu aktualisieren.
    visibleCols.forEach((c, ci) => { html += `<th>${escapeHtml(TableCompute.resolveLabel(c.label, windowStarts[ci]))}</th>`; });
    if (columns.length > visibleCols.length) html += '<th>…</th>';
    html += '</tr>';
    visibleRows.forEach((row, ri) => {
      if (row.row_type === 'separator') {
        const span = visibleCols.length + 1 + (columns.length > visibleCols.length ? 1 : 0);
        html += `<tr class="tbl-separator-row"><td colspan="${span}"><span class="tbl-separator-line" aria-hidden="true"></span></td></tr>`;
        return;
      }
      html += `<tr${row.bold ? ' class="tbl-bold"' : ''}><td>${escapeHtml(row.label)}</td>`;
      visibleCols.forEach((col, ci) => {
        const cell = values[ci] && values[ci][ri];
        html += `<td class="tbl-num">${escapeHtml(TableCompute.cellText(cell, col.decimals))}</td>`;
      });
      if (columns.length > visibleCols.length) html += '<td>…</td>';
      html += '</tr>';
    });
    if (rows.length > visibleRows.length) {
      html += `<tr><td colspan="${visibleCols.length + 2}" class="dtile-table-more">+${rows.length - visibleRows.length} weitere Zeile${rows.length - visibleRows.length !== 1 ? 'n' : ''}</td></tr>`;
    }
    html += '</table>';
    previewEl.innerHTML = html;
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
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
  async function renderEntityTile(el) {
    const base = document.getElementById('dashboard-grid')?.dataset.base || '.';
    const entityId = el.dataset.entityId;
    const sparklineEl = el.querySelector('.dtile-entity-sparkline');
    let data;
    try {
      const res = await fetch(`${base}/api/query?entity_id=${encodeURIComponent(entityId)}&range=day&raw=true`);
      data = await res.json();
    } catch (e) {
      return;
    }
    const points = (data.points || []).filter(p => Number.isFinite(p.value));
    if (!points.length) return;  // kein besseres Ergebnis als der Server-Platzhalter

    const last = points[points.length - 1];
    const numberEl = el.querySelector('.dtile-entity-number');
    const ageEl = el.querySelector('.dtile-entity-age');
    if (numberEl) {
      numberEl.textContent = el.dataset.isSwitch === 'true'
        ? (last.value ? 'An' : 'Aus')
        : NumberFormat.fmt(last.value, el.dataset.decimals === 'auto' ? null : parseInt(el.dataset.decimals, 10));
    }
    const secondsAgo = Date.now() / 1000 - last.ts;
    if (ageEl) ageEl.textContent = `vor ${NumberFormat.fmtDuration(secondsAgo)}`;
    // Nur der Kartenrahmen zeigt "veraltet" an (siehe .dtile-entity.is-warn/
    // is-stale in dashboard_detail.html/entities.html), der Wert bleibt immer
    // schwarz — dieselben zwei Schwellen (15 Min./1 Std.) wie beim
    // Server-Rendern (main.py _dashboard_tiles_context()), hier maßgeblich
    // seit last_value/last_ts als Datenquelle unzuverlässig sind.
    const tileEl = el.closest('.dtile');
    if (tileEl) {
      tileEl.classList.toggle('is-warn', secondsAgo > 900 && secondsAgo <= 3600);
      tileEl.classList.toggle('is-stale', secondsAgo > 3600);
    }

    if (sparklineEl) {
      const paths = sparklinePaths(points.map(p => p.value));
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
    window.addEventListener('resize', () => instances.forEach(c => c.resize()));
    setupSizePickers();
    setupDragAndDrop();
    setupLegendToggles();
  }

  function setupSizePickers() {
    const grid = document.getElementById('dashboard-grid');
    const base = grid?.dataset.base || '.';
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
      const showAgeCheckbox = control.querySelector('.dtile-show-age-checkbox');
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
                dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId,
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
            const chart = instances.get(tile.dataset.itemId);
            requestAnimationFrame(() => chart && chart.resize());
          } catch (e) {
            trigger.title = 'Größe konnte nicht gespeichert werden';
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
                dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId, show_sparkline: showSparkline,
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

      if (showAgeCheckbox) {
        showAgeCheckbox.addEventListener('change', async () => {
          const showAge = showAgeCheckbox.checked;
          showAgeCheckbox.disabled = true;
          try {
            const response = await fetch(`${base}/dashboard/entity-show-age`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId, show_age: showAge,
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
                ageEl.title = 'Letzter Wert';
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
      const decimalsCells = Array.from(control.querySelectorAll('.dtile-decimals-cell'));
      const decimalsHead = control.querySelector('.dtile-decimals-picker-head strong');
      const DECIMALS_HEAD_LABELS = {auto: 'Auto', '0': '0', '1': '1', '2': '2', '3': '3'};
      decimalsCells.forEach(cell => {
        cell.addEventListener('click', async () => {
          const decimals = cell.dataset.decimals;
          try {
            const response = await fetch(`${base}/dashboard/entity-decimals`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId, decimals}),
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
            trigger.title = 'Nachkommastellen konnten nicht gespeichert werden';
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
              body: JSON.stringify({dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId, title}),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            if (titleEl) titleEl.textContent = title || titleInput.placeholder;
          } catch (e) {
            trigger.title = 'Titel konnte nicht gespeichert werden';
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
  function setupDragAndDrop() {
    const grid = document.getElementById('dashboard-grid');
    if (!grid) return;
    let draggedEl = null;

    grid.querySelectorAll('.dtile[data-item-id]').forEach(tile => {
      tile.addEventListener('dragstart', (e) => {
        draggedEl = tile;
        tile.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        // setData ist in Firefox Voraussetzung dafür, dass dragover/drop
        // überhaupt feuern — der eigentliche Inhalt wird nicht ausgewertet,
        // die neue Reihenfolge liest persistOrder() direkt aus dem DOM.
        e.dataTransfer.setData('text/plain', `${tile.dataset.itemType}:${tile.dataset.itemId}`);
      });
      tile.addEventListener('dragend', () => {
        tile.classList.remove('dragging');
        if (draggedEl) persistOrder(grid);
        draggedEl = null;
      });
      tile.addEventListener('dragover', (e) => {
        if (!draggedEl || draggedEl === tile) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const rect = tile.getBoundingClientRect();
        const before = (e.clientX - rect.left) < rect.width / 2;
        tile.parentNode.insertBefore(draggedEl, before ? tile : tile.nextSibling);
      });
    });
    // Auf dem Raster selbst (statt nur je Kachel) abfangen, sonst bleibt ein
    // Drop auf die Lücke zwischen zwei Kacheln oder auf die "+"-Kachel ohne
    // Wirkung, weil dort kein "dragover"-preventDefault registriert ist —
    // ohne das bricht der Browser den Drop grundsätzlich ab.
    grid.addEventListener('dragover', (e) => e.preventDefault());
  }

  async function persistOrder(grid) {
    const base = grid.dataset.base || '.';
    const dashboardId = parseInt(grid.dataset.dashboardId || '1', 10);
    const pins = Array.from(grid.querySelectorAll('.dtile[data-item-id]')).map(el => ({
      item_type: el.dataset.itemType, item_id: parseInt(el.dataset.itemId, 10),
      item_entity_id: el.dataset.itemEntityId || null,
    }));
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
  // Nach Pin/Unpin ersetzt htmx #dashboard-grid komplett (outerHTML) — alte
  // ECharts-Instanzen zeigen dann auf längst entfernte DOM-Knoten, deshalb
  // hier verwerfen statt sie weiter zu behalten; neue Kacheln bekommen beim
  // erneuten setup() ihre eigene frische Instanz.
  document.body.addEventListener('htmx:afterSettle', (e) => {
    if (e.target && e.target.id === 'dashboard-grid') {
      instances.forEach(c => c.dispose());
      instances.clear();
      setup();
    }
  });
})();
