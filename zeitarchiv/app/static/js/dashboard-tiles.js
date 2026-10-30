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
  // Chart-Darstellung (Optionen lesen, Daten laden, ECharts zeichnen) liegt in
  // chart-tile-render.js; hier bleibt die Kachel-Hülle: Legende, Instanzen,
  // Größenwahl, Drag&Drop, Auto-Refresh.
  const {
    LOCALE,
    PALETTE,
    draw,
    fetchChartData,
    fmtCompactNumber,
    hasData,
    prepareSeries,
    readSpec,
    scaledFont,
    stateRows,
    switchOnDuration,
    tileMultiYear,
    viewTypeFor,
    withOpacity,
  } = window.ChartTileRender;


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
    const spec = readSpec(el.dataset);
    const {entityIds, entityNames, colorFor, timeline, effectiveDecimals, compare} = spec;
    const chartEl = el.querySelector('.dtile-chart');
    if (!chartEl || !entityIds.length) return;

    const base = el.closest('#dashboard-grid')?.dataset.appRoot || '';
    let data;
    try {
      data = await fetchChartData(spec, {base, stichtag: stichtag(), widthPx: chartEl.clientWidth});
    } catch (e) {
      chartEl.innerHTML = '<div class="dtile-loading">' + t('Fehler beim Laden') + '</div>';
      return;
    }
    const series = prepareSeries(data);
    if (!hasData(series)) {
      chartEl.innerHTML = '<div class="dtile-loading">' + t('Keine Daten') + '</div>';
      return;
    }
    const viewType = viewTypeFor(spec, series);
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
    draw(chart, {spec, data, series, viewType, multiYear, hiddenSet: legendHidden.get(chartId), style});
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
    const {visibleCols, allRows, visibleRowIndexes, visibleRows} = TableTileRender.selectVisible(
      JSON.parse(el.dataset.columns || '[]'), JSON.parse(el.dataset.rows || '[]'));
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

    previewEl.innerHTML = TableTileRender.buildHtml({
      visibleCols, allRows, visibleRowIndexes, visibleRows, style, values, allValues,
      windowStarts, windowEnds, isCurrent, elapsedSeconds, rowSums, sumOverlapping,
    });
    TableTileRender.afterInsert(previewEl);
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
