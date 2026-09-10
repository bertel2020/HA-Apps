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
  // Bereits einmal gerenderte Kacheln (siehe observer-Callback in setup())
  // — Grundlage für den periodischen Auto-Refresh unten, damit der nicht
  // Kacheln anfasst, die noch nie geladen wurden (Platzhalter außerhalb des
  // Sichtbereichs beim ersten Laden der Seite).
  const renderedTiles = new Set();
  let refreshTimer = null;
  // 60s — schneller als sinnvoll wäre unnötige Serverlast für Daten, die sich
  // i. d. R. erst nach mehreren Minuten sichtbar ändern; deutlich langsamer
  // ließe "manuell aktualisieren" wirken, obwohl es das gar nicht mehr
  // bräuchte. Nur während der Tab sichtbar ist (siehe setupAutoRefresh).
  const DASHBOARD_REFRESH_INTERVAL_MS = 60000;

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

  // "Ø"/"Σ" statt Text — dieselben Symbole wie chart_editor.js/entity_detail.js
  // (LEGEND_METRIC_OPTIONS dort), nur "Ø" stand hier schon vorher so; "Summe"
  // war bis jetzt der einzige verbliebene Text-Ausreißer.
  const LEGEND_METRIC_LABELS = {last: 'Aktuell', min: 'Min', max: 'Max', average: 'Ø', sum: 'Σ'};

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
    // "Gestapelt"/"Anteile (%)" (Optionen-Menü der Chart-Seite) — dieselbe
    // stacked-Kachel-Vorschau wie auf der eigenen Chart-Seite, siehe
    // stackedActive/normalizeActive weiter unten (chart_editor.js-Parität).
    const stacked = el.dataset.stacked === 'true';
    const normalize = el.dataset.normalize === 'true';
    // "Ausrichtung" (Optionen-Menü, nur bei Auflösung "Voll") — siehe
    // horizontalActive weiter unten (chart_editor.js-Parität).
    const horizontal = el.dataset.horizontal === 'true';
    const chartEl = el.querySelector('.dtile-chart');
    if (!chartEl || !entityIds.length) return;

    const base = el.closest('#dashboard-grid')?.dataset.appRoot || '';
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
        color: PALETTE[colorIndexFor(s.entity_id) % PALETTE.length],
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
      renderTileTimeline(chart, series, entityNames, data, range, animation, style, borderColor, inkFaint, surface, colorIndexFor);
      return;
    }
    if (donut) {
      renderTileDonut(chart, series, entityNames, colorIndexFor, donutAggregation, effectiveDecimals, style, surface, chartId);
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
    const echartsSeries = series.map((s, i) => {
      const color = PALETTE[colorIndexFor(s.entity_id) % PALETTE.length];
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
        cfg.symbol = 'none';
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
      return cfg;
    });
    echartsSeries.push(...rollingSeries);

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
      xAxis: singleBucket ? (horizontalActive ? xAxisForHorizontal : categoryAxisObj) : {
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
      yAxis: horizontalActive ? {...categoryAxisObj, inverse: true} : yAxis,
      tooltip: {
        trigger: 'axis',
        backgroundColor: surface,
        borderColor,
        textStyle: {color: inkMuted, fontFamily: style.getPropertyValue('--font-mono'), fontSize: scaledFont(12)},
        formatter: (params) => {
          if (!params.length) return '';
          // Bei singleBucket ist axisValue jetzt der Entitätsname (eigene
          // Kategorie je Entität, s. o.), keine Zeitangabe mehr — die
          // Kopfzeile zeigt stattdessen weiterhin den Periodenbeginn direkt
          // aus window_start, wie vor der Kategorie-je-Entität-Umstellung.
          const header = singleBucket
            ? (data.window_start != null ? new Date(data.window_start * 1000).toLocaleDateString(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric'}) : '')
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
                 + `<strong style="margin-left:8px;">${value}${extra}</strong></div>`;
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

  // Kompakter Zeitstrahl für eine Kachel — eine Kategorie-Zeile je Entität,
  // dieselbe Balken-Technik wie renderTimelineMulti() in chart_editor.html,
  // hier nur mit den kleineren Kachel-Schriftgrößen/Abständen. Ohne
  // Zeilen-Beschriftung (der Name steht schon in der Kachel-Legende, siehe
  // renderTile()) — bei mehreren Zeilen in einer kleinen Kachel wäre dafür
  // ohnehin kaum Platz.
  function renderTileTimeline(chart, series, entityNames, data, range, animation, style, borderColor, inkFaint, surface, colorIndexFor) {
    const categories = series.map(s => entityNames[s.entity_id] || s.friendly_name);
    const items = [];
    series.forEach((s, catIndex) => {
      const color = PALETTE[colorIndexFor(s.entity_id) % PALETTE.length];
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

  // Donut statt Zeitverlauf ("Darstellungsart", Optionen-Menü der Chart-
  // Seite) — Kachel-Parität zu chart_editor.js renderDonut(): ein Anteil je
  // Serie aus GENAU EINEM aggregierten Wert (aggregation: Summe/Durchschnitt/
  // Letzter Wert der bereits geladenen Punkte), keine Zeitachse. Dieselbe
  // Radius-/Emphasis-Konfiguration wie #storage-pie (statistik.js) und
  // .edash-share-donut (energiedashboard.js) — label:show:false, weil die
  // Namen schon in der .dtile-legend-Zeile stehen (renderTile() baut sie VOR
  // diesem Zweig, siehe renderLegend()-Aufruf dort, unverändert wiederverwendet).
  function renderTileDonut(chart, series, entityNames, colorIndexFor, aggregation, effectiveDecimals, style, surface, chartId) {
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
        itemStyle: {color: PALETTE[colorIndexFor(s.entity_id) % PALETTE.length]},
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
    // fliegen hier raus — dieselbe Regel wie in table_editor.html: nicht
    // gerendert, aber weiterhin Teil der Berechnung (computeValues()
    // bekommt hier ohnehin nur den bereits gekürzten Ausschnitt, siehe
    // TableCompute.computeValues()-Kommentar dort zur Formel-Buchstaben-
    // Einschränkung auf die Kachel).
    const visibleCols = JSON.parse(el.dataset.columns || '[]').filter(c => !c.hidden);
    const visibleRows = JSON.parse(el.dataset.rows || '[]').filter(r => !r.hidden);
    const style = JSON.parse(el.dataset.style || '{}');
    const previewEl = el.querySelector('.dtile-table-preview');
    if (!previewEl) return;

    if (!visibleCols.length || !visibleRows.length) {
      previewEl.innerHTML = '<div class="dtile-loading">Keine sichtbaren Zeilen/Spalten</div>';
      return;
    }
    const base = el.closest('#dashboard-grid')?.dataset.appRoot || '';
    let values, windowStarts, elapsedSeconds;
    try {
      ({values, windowStarts, elapsedSeconds} = await TableCompute.computeValues(base, visibleCols, visibleRows));
    } catch (e) {
      previewEl.innerHTML = '<div class="dtile-loading">Fehler beim Laden</div>';
      return;
    }

    // Abschnitts-Mitglieder (Entität-/Gruppen-Zeilen zwischen zwei Trennlinien
    // derselben Spalte) — Traversal-Gegenstück zu sectionMemberCells() in
    // table_editor.html, hier index- statt uid-basiert (dieselbe Datenform
    // wie computeValues()). Die eigentliche Anteilsrechnung bleibt in
    // TableCompute.percentOfTotalCell(), damit beide Seiten denselben
    // Rechenkern nutzen.
    function sectionMemberCells(ci, ri) {
      let start = 0;
      for (let j = ri - 1; j >= 0; j--) { if (visibleRows[j].row_type === 'separator') { start = j + 1; break; } }
      let end = visibleRows.length;
      for (let j = ri + 1; j < visibleRows.length; j++) { if (visibleRows[j].row_type === 'separator') { end = j; break; } }
      const cells = [];
      for (let j = start; j < end; j++) {
        if (visibleRows[j].row_type !== 'entity' && visibleRows[j].row_type !== 'group') continue;
        cells.push(values[ci] && values[ci][j]);
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
      let start = 0;
      for (let j = ri - 1; j >= 0; j--) { if (visibleRows[j].row_type === 'separator') { start = j + 1; break; } }
      let end = visibleRows.length;
      for (let j = ri + 1; j < visibleRows.length; j++) { if (visibleRows[j].row_type === 'separator') { end = j; break; } }
      const vals = [];
      for (let j = start; j < end; j++) {
        if (visibleRows[j].row_type !== 'entity' && visibleRows[j].row_type !== 'group') continue;
        const cell = values[ci] && values[ci][j];
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
    // Bei "gleichmäßig" gilt equalValueWidth für JEDE Wertespalte, auch wenn
    // einzelne Spalten zusätzlich noch eine individuell gezogene Breite
    // gespeichert haben (table_editor.html) — sonst wären die Spalten trotz
    // aktivierter Option nicht wirklich alle gleich breit.
    const layoutWidths = needsColumnLayout
      ? [
          savedLabelWidth || autoLabelWidth,
          ...savedValueWidths.map(w => equalValueWidth || w || autoValueWidth),
        ]
      : [];
    const savedTableWidth = layoutWidths.reduce((sum, width) => sum + width, 0);
    const tableWidthAttr = needsColumnLayout
      ? ` style="width:max(100%,${savedTableWidth}px);min-width:${savedTableWidth}px;table-layout:fixed;"`
      : '';
    const colgroup = needsColumnLayout
      ? `<colgroup>${layoutWidths.map(width => `<col style="width:${width}px">`).join('')}</colgroup>`
      : '';

    const styleClasses = TableCompute.styleClasses(style);
    let html = `<table class="dt compact dtile-mini-table ${styleClasses}"${tableWidthAttr}>${colgroup}<thead>`;
    // Mehrstufige Kopfzeile (col.group_label) — dieselbe Lauflängen-Logik wie
    // groupHeaderCells() in table_editor.html, hier direkt auf visibleCols
    // (die Kachel kürzt Spalten ohnehin schon aufs Sichtbare).
    if (visibleCols.some(c => (c.group_label || '').trim())) {
      html += `<tr class="tbl-header-row tbl-group-header-row"><th${labelWidthAttr}>&nbsp;</th>`;
      let gi = 0;
      while (gi < visibleCols.length) {
        const label = (visibleCols[gi].group_label || '').trim();
        let span = 1;
        while (label && gi + span < visibleCols.length && (visibleCols[gi + span].group_label || '').trim() === label) span++;
        html += `<th colspan="${span}" class="tbl-group-header">${escapeHtml(label)}</th>`;
        gi += span;
      }
      html += '</tr>';
    }
    html += `<tr class="tbl-header-row"><th${labelWidthAttr}>&nbsp;</th>`;
    // Beschriftungs-Platzhalter (z. B. "{jahr}") wurden beim Speichern bewusst
    // NICHT aufgelöst (siehe table_editor.html save()) — sonst würde eine so
    // beschriftete Spalte hier ewig denselben, beim Speichern eingefrorenen
    // Wert zeigen statt sich mit der Zeit automatisch zu aktualisieren.
    visibleCols.forEach((c, ci) => {
      const comparisonClass = TableCompute.isComparisonColumn(c) ? ' class="tbl-comparison-col"' : '';
      html += `<th${comparisonClass}${colWidthAttr(c.width)}>${escapeHtml(TableCompute.resolveLabel(c.label, windowStarts[ci]))}</th>`;
    });
    html += '</tr></thead><tbody>';
    let dataRowIndex = 0;
    visibleRows.forEach((row, ri) => {
      if (row.row_type === 'separator') {
        const span = visibleCols.length + 1;
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
      html += `<tr${rowClasses.length ? ` class="${rowClasses.join(' ')}"` : ''}><td${labelWidthAttr}>${escapeHtml(row.label)}</td>`;
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
          ? TableCompute.comparisonElapsedTimeText(windowStarts[comparisonIndex], elapsedSeconds[comparisonIndex]) : null;
        const deviationTitle = comparisonIndex < 0 ? '' : comparisonValueStr
          ? `Gegenüber ${comparisonLabel}${comparisonTimeStr ? ` bis ${comparisonTimeStr}` : ''}: ${comparisonValueStr}`
          : `Gegenüber ${comparisonLabel}`;
        const widthCss = col.width ? `width:${col.width}px;max-width:${col.width}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;` : '';
        const heatmapCss = (col.heatmap && (row.row_type === 'entity' || row.row_type === 'group'))
          ? TableCompute.heatmapStyle(values[ci] && values[ci][ri], columnHeatmapRange(ci, ri)) : '';
        const cellStyleAttr = (widthCss || heatmapCss) ? ` style="${widthCss}${heatmapCss}"` : '';
        html += `<td class="${cellClasses.join(' ')}"${cellStyleAttr}><span class="tbl-cell"><span class="tbl-cell-number"><span class="tbl-number-whole">${escapeHtml(numberParts.whole)}</span><span class="tbl-number-separator">${escapeHtml(numberParts.separator)}</span><span class="tbl-number-fraction">${escapeHtml(numberParts.fraction)}</span></span>${unit ? `<span class="tbl-cell-unit">${escapeHtml(unit)}</span>` : ''}</span>${deviation ? `<span class="tbl-cell-deviation" data-tooltip-fixed="${escapeHtml(deviationTitle)}">${escapeHtml(deviation)}</span>` : ''}</td>`;
      });
      html += '</tr>';
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
  // Zeitraum-Beschriftung einer Werte-Kachel — zweite Kopie von
  // _TILE_RANGE_LABELS in main.py: der Server beschriftet die erste Anzeige,
  // hier wird nach einer Änderung im Kachelmenü neu beschriftet, ohne die
  // Seite neu zu laden. Ein Test hält beide Kopien deckungsgleich.
  // [kalendarisch, rollierend]
  const TILE_RANGE_LABELS = {
    hour: ['Std.', '60 Min.'], day: ['Tag', '24 Std.'], week: ['Woche', '7 Tage'],
    month: ['Monat', '30 Tage'], year: ['Jahr', '12 Monate'],
  };
  // Dasselbe Fenster ausgeschrieben, für den Tooltip: "Max 23,1" allein sagt
  // nicht, worüber.
  const TILE_RANGE_WINDOW = {
    hour: ['in der laufenden Stunde', 'in den letzten 60 Minuten'],
    day: ['heute seit Mitternacht', 'in den letzten 24 Stunden'],
    week: ['in der laufenden Kalenderwoche', 'in den letzten 7 Tagen'],
    month: ['im laufenden Monat', 'in den letzten 30 Tagen'],
    year: ['im laufenden Jahr', 'in den letzten 12 Monaten'],
  };
  // Rückfall, falls eine Antwort die Kürzel nicht mitbringt. Maßgeblich ist
  // ctx.metric_labels vom Server: dort hängt genau eines am Entitätstyp — bei
  // einem Zähler heißt die Summe "+", weil sie der Zuwachs des Zählerstands
  // ist (siehe _tile_metric_labels() in main.py).
  const TILE_METRIC_LABELS = {last: '', min: 'Min', avg: 'Ø', max: 'Max', sum: 'Σ'};

  function tileRangeLabel(el) {
    const pair = TILE_RANGE_LABELS[el.dataset.range] || TILE_RANGE_LABELS.day;
    return pair[el.dataset.continuous === 'true' ? 1 : 0];
  }

  // Erklärt eine Kennzahl im Klartext. Der heikle Fall ist der Zähler: dessen
  // Min/Ø/Max beziehen sich auf Bucket-Deltas, "Max" heißt dort "stärkster
  // Tag" und nicht "größter Messwert". Die Bucket-Größe kommt vom Server
  // (bucket_label), damit hier keine zweite Tabelle mit den Auflösungen des
  // Speichers entsteht, die still veralten könnte.
  function tileMetricTooltip(el, metric, serie) {
    const fenster = (TILE_RANGE_WINDOW[el.dataset.range] || TILE_RANGE_WINDOW.day)[
      el.dataset.continuous === 'true' ? 1 : 0
    ];
    const bucket = serie && serie.bucket_label;
    if (bucket) {
      const jeBucket = {
        min: `schwächster ${bucket}`, avg: `Ø je ${bucket}`,
        max: `stärkster ${bucket}`, sum: 'Zuwachs',
      }[metric];
      return `${jeBucket} · ${fenster}`;
    }
    const messwert = {
      min: 'kleinster Messwert', avg: 'Durchschnitt',
      max: 'größter Messwert', sum: 'Summe',
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

  function renderEntityTile(el) {
    pendingEntityTiles.add(el);
    if (entityFlushScheduled) return;
    entityFlushScheduled = true;
    // setTimeout(0) statt eines Microtasks: der IntersectionObserver liefert
    // zwar alle gleichzeitig sichtbaren Kacheln in EINEM Callback, der
    // Auto-Refresh ruft aber je Kachel einzeln in einer forEach-Schleife.
    // Beide Fälle landen so im selben Sammelfenster.
    setTimeout(flushEntityTiles, 0);
  }

  function tileGroupKey(el) {
    return [
      el.dataset.range || 'day',
      el.dataset.continuous === 'true' ? '1' : '0',
      el.dataset.sparklineResolution || 'raw',
      el.dataset.statsMetrics ? '1' : '0',
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
      stats: erste.dataset.statsMetrics ? 'true' : 'false',
    });
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
      stueck.forEach(el => {
        const serie = nachId.get(el.dataset.entityId);
        if (serie) applyEntityTile(el, serie);
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
  function applyEntityTile(el, serie) {
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
        ? (wert ? 'An' : 'Aus')
        : (istSchalter && primary === 'sum'
            ? NumberFormat.fmtDuration(wert)
            : NumberFormat.fmt(wert, dezimal));
    } else if (numberEl && serie.aggregates && primary !== 'last') {
      numberEl.textContent = '–';
    }

    const ageEl = el.querySelector('.dtile-entity-age');
    const secondsAgo = serie.last_ts == null ? null : Date.now() / 1000 - serie.last_ts;
    if (ageEl) {
      ageEl.textContent = secondsAgo == null ? 'nie' : `vor ${NumberFormat.fmtDuration(secondsAgo)}`;
    }
    // Nur der Kartenrahmen zeigt "veraltet" an (siehe .dtile-entity.is-warn/
    // is-stale), der Wert bleibt immer schwarz — dieselben zwei Schwellen
    // (15 Min./1 Std.) wie beim Server-Rendern. Hängt weiterhin am letzten
    // ROHWERT, auch wenn die große Zahl eine Aggregation ist: "veraltet"
    // meint die Entität, nicht die Kennzahl.
    const tileEl = el.closest('.dtile');
    if (tileEl && secondsAgo != null) {
      tileEl.classList.toggle('is-warn', secondsAgo > 900 && secondsAgo <= 3600);
      tileEl.classList.toggle('is-stale', secondsAgo > 3600);
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
      statEl.setAttribute('data-tooltip-fixed', tileMetricTooltip(el, metric, serie));
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
    window.addEventListener('resize', () => instances.forEach(c => c.resize()));
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
  function setupAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      renderedTiles.forEach(el => {
        if (!el.isConnected) { renderedTiles.delete(el); return; }
        if (el.classList.contains('dtile-entity-body')) renderEntityTile(el);
        else if (el.dataset.columns != null) renderTableTile(el);
        else renderTile(el);
      });
    }, DASHBOARD_REFRESH_INTERVAL_MS);
  }

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

      const sparklineResolutionHead = control.querySelector('.dtile-sparkline-resolution-row')
        ?.previousElementSibling?.querySelector('strong');
      const SPARKLINE_RESOLUTION_LABELS = {raw: 'Rohdaten', '5min': '5 Min', '15min': '15 Min', '30min': '30 Min', '1h': '1 Std'};
      sparklineResolutionCells.forEach(cell => {
        cell.addEventListener('click', async () => {
          const resolution = cell.dataset.resolution;
          try {
            const response = await fetch(`${base}/dashboard/sparkline-resolution`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId, resolution,
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
            trigger.title = 'Sparkline-Auflösung konnte nicht gespeichert werden';
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
                ageEl.title = 'Letzte Aktualisierung';
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
        // Rohwerte gibt es nur bis "Woche" (MAX_RAW_QUERY_POINTS) — darüber
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
            ? 'Bei Monat und Jahr zeichnet die Sparkline die Buckets der Abfrage — eine feinere Auflösung gibt es dort nicht.'
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
            const periode = document.createElement('span');
            periode.className = 'dtile-entity-period';
            periode.textContent = ctx.range_label;
            zeile.appendChild(periode);
            body.querySelector('.dtile-entity-sparkline')
              ? body.insertBefore(zeile, body.querySelector('.dtile-entity-sparkline'))
              : body.appendChild(zeile);
          }

          // Der Zeitraum steht genau einmal — in der Wert-Zeile nur dann,
          // wenn es keine Kennzahlen-Zeile gibt, die ihn trägt.
          const wertZeile = body.querySelector('.dtile-entity-value');
          wertZeile?.querySelector('.dtile-entity-period')?.remove();
          const alter = wertZeile?.querySelector('.dtile-entity-age');
          if (ctx.show_period_in_value_row) {
            if (alter) alter.hidden = true;
            const periode = document.createElement('span');
            periode.className = 'dtile-entity-period';
            periode.textContent = ctx.range_label;
            wertZeile?.appendChild(periode);
          } else if (alter) {
            alter.hidden = body.dataset.showAge !== 'true';
          }

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
              ? (c.dataset.metric === ctx.primary_metric ? 'Steht schon als Hauptwert' : 'Für diese Entität keine sinnvolle Kennzahl')
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
                dashboard_id: dashboardId, entity_id: tile.dataset.itemEntityId, ...aenderung,
              }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            uebernehmen(await response.json());
          } catch (e) {
            trigger.title = 'Kennzahlen konnten nicht gespeichert werden';
          }
        };

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
        countEl.textContent = `(${n} Kachel${n === 1 ? '' : 'n'} ausgeblendet)`;
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
    }
  });
})();
