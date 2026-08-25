// Dashboard-Kacheln auf der Übersichtsseite (Konzept "Offene Punkte",
// erweitert um Vergleichstabellen) — rendert je Kachel entweder ein
// kompaktes ECharts-Mini-Chart (Charts, über /api/query-multi, dasselbe wie
// bei der vollen Chart-Seite chart_editor.html, nur ohne Legende/Zeitraum-
// Toolbar/Tooltip-Feinschliff) oder eine reduzierte Mini-Tabelle (Vergleichs-
// tabellen, über static/js/table-compute.js — derselbe Rechenkern wie
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

  const instances = new Map();  // chartId -> echarts instance
  let observer = null;

  function fmtAxis(range, ts) {
    const d = new Date(ts * 1000);
    if (range === 'hour' || range === 'day') return d.toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit'});
    if (range === 'week' || range === 'month') return d.toLocaleDateString('de-DE', {day: '2-digit', month: '2-digit'});
    if (range === 'decade') return d.toLocaleDateString('de-DE', {year: 'numeric'});
    return d.toLocaleDateString('de-DE', {month: 'short', year: 'numeric'});
  }

  async function renderTile(el) {
    // item-id sitzt auf der äußeren .dtile (auch Drag&Drop-Handle, siehe
    // setupDragAndDrop), die restlichen Daten auf dem inneren Link selbst.
    const chartId = el.closest('.dtile').dataset.itemId;
    const entityIds = JSON.parse(el.dataset.entityIds || '[]');
    const entityNames = JSON.parse(el.dataset.entityNames || '{}');
    const range = el.dataset.range || 'day';
    const continuous = el.dataset.continuous === 'true';
    const chartEl = el.querySelector('.dtile-chart');
    if (!chartEl || !entityIds.length) return;

    const params = new URLSearchParams({entity_ids: entityIds.join(','), range, offset: '0', continuous: String(continuous)});
    let data;
    try {
      const res = await fetch(`api/query-multi?${params}`);
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
    chartEl.innerHTML = '';
    let chart = instances.get(chartId);
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

    // Eine Y-Achse je unterschiedlicher Einheit (genau wie auf der vollen
    // Chart-Seite, chart_editor.html) — ohne das teilen sich z. B. Watt- und
    // kWh-Werte dieselbe Achse, wodurch die kWh-Balken bei einer viel
    // größeren Watt-Spanne rechnerisch bei ~0 verschwinden, statt sichtbar zu
    // sein. Kompakt gehalten: kein Achsenname, nur die Zahl.
    const units = [...new Set(series.map(s => s.unit))];
    const yAxis = units.map((u, i) => ({
      type: 'value',
      position: i % 2 === 0 ? 'left' : 'right',
      offset: Math.floor(i / 2) * 46,
      axisLabel: {fontSize: scaledFont(10), color: inkFaint, formatter: v => fmtCompactNumber(v)},
      axisLine: {show: false},
      axisTick: {show: false},
      splitLine: {lineStyle: {color: borderColor, type: 'dashed'}},
    }));

    const echartsSeries = series.map((s, i) => {
      const color = PALETTE[i % PALETTE.length];
      const displayName = entityNames[s.entity_id] || s.friendly_name;
      const cfg = {
        // Angepasster Anzeigename (chart_editor.html, "Angezeigte Namen") hat
        // Vorrang vor dem Entität-eigenen friendly_name — dieselbe Regel wie
        // beim Rendern der vollen Chart-Seite (dort this.entityNames[entity_id]).
        name: displayName,
        type: s.chart_type,
        yAxisIndex: units.indexOf(s.unit),
        data: s.points.map(p => [p.ts * 1000, p.value, s.unit]),
        lineStyle: {width: 2, color},
        itemStyle: {color},
        barMaxWidth: 28,
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

    chart.setOption({
      textStyle: {fontFamily: style.getPropertyValue('--font-mono')},
      color: PALETTE,
      grid: {left: 6, right: 6, top: 10, bottom: 20, containLabel: true},
      xAxis: {
        type: 'time',
        min: data.window_start != null ? data.window_start * 1000 : undefined,
        max: data.window_end != null ? data.window_end * 1000 : undefined,
        boundaryGap: [0, 0],
        axisLabel: {formatter: v => fmtAxis(range, v / 1000), fontSize: scaledFont(10), color: inkFaint, hideOverlap: true},
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
          const d = new Date(params[0].axisValue);
          const header = d.toLocaleString('de-DE', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
          const rows = params.map(p => {
            const unit = p.data[2] || '';
            return `<div style="display:flex;justify-content:space-between;gap:14px;">`
                 + `<span>${p.marker}${p.seriesName}</span>`
                 + `<strong style="margin-left:8px;">${fmtCompactNumber(p.data[1])}${unit ? ' ' + unit : ''}</strong></div>`;
          }).join('');
          return `<div style="margin-bottom:4px;color:${inkFaint};">${header}</div>${rows}`;
        },
      },
      series: echartsSeries,
    });
  }

  // Kompakte Zahl-Formatierung für Achsenbeschriftung/Tooltip der Kacheln —
  // dieselbe 4-signifikante-Stellen-Regel wie fmtValue() auf der Entität-
  // eigenen Chart-Seite (entity_detail.html) bei "Automatisch", nur ohne
  // Zugriff auf eine pro-Entität konfigurierte feste Nachkommastellenzahl
  // (die Kachel kennt nur den Aggregations-Query, keine Entitäts-Einstellungen).
  function fmtCompactNumber(v) {
    if (v == null || Number.isNaN(v)) return '';
    if (v === 0) return '0';
    return Number(v.toPrecision(4)).toLocaleString('de-DE');
  }

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
    let values;
    try {
      values = await TableCompute.computeValues('.', columns, rows);
    } catch (e) {
      previewEl.innerHTML = '<div class="dtile-loading">Fehler beim Laden</div>';
      return;
    }

    const styleClasses = TableCompute.styleClasses(style);
    let html = `<table class="dt compact dtile-mini-table ${styleClasses}"><tr><th>&nbsp;</th>`;
    visibleCols.forEach(c => { html += `<th>${escapeHtml(c.label)}</th>`; });
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
        html += `<td class="tbl-num">${escapeHtml(TableCompute.cellText(cell))}</td>`;
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

  function setup() {
    const chartTiles = document.querySelectorAll('.dtile-body[data-entity-ids]');
    const tableTiles = document.querySelectorAll('.dtile-body[data-columns]');
    if (!chartTiles.length && !tableTiles.length) return;
    if (observer) observer.disconnect();
    observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const el = entry.target;
          if (el.dataset.columns != null) renderTableTile(el); else renderTile(el);
          observer.unobserve(el);
        }
      });
    }, {rootMargin: '150px'});
    chartTiles.forEach(el => observer.observe(el));
    tableTiles.forEach(el => observer.observe(el));
    window.addEventListener('resize', () => instances.forEach(c => c.resize()));
    setupSizePickers();
    setupDragAndDrop();
  }

  function setupSizePickers() {
    document.querySelectorAll('.dtile-menu').forEach(control => {
      const tile = control.closest('.dtile[data-item-id]');
      const cells = Array.from(control.querySelectorAll('.dtile-size-cell'));
      const preview = control.querySelector('.dtile-size-preview');
      const current = control.querySelector('.dtile-size-picker-head strong');
      const trigger = control.querySelector('.dtile-menu-btn');
      if (!tile || !cells.length || !preview || !current || !trigger) return;

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
          try {
            const response = await fetch('dashboard/size', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
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
    const pins = Array.from(grid.querySelectorAll('.dtile[data-item-id]')).map(el => ({
      item_type: el.dataset.itemType, item_id: parseInt(el.dataset.itemId, 10),
    }));
    try {
      await fetch('dashboard/reorder', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pins}),
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
