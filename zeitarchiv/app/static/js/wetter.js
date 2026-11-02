// Wetterdashboard: Kacheln und Vergleichstabelle (Heute, Gestern, Monat, Vormonat, Jahr — jeweils
// mit Vorjahr). Der Server rechnet alle Kennzahlen (POST wetterdashboard/table); dieses Skript
// formatiert und zeichnet nur. Kalenderangaben laufen über ServerTime in der Zeitzone des Servers.
(function () {
  const COLUMN_KEYS = ['heute', 'gestern', 'monat', 'vormonat', 'jahr'];
  const STORE_KEY = 'zeitarchiv.wetter.table';
  const LOCALE = window.ZA_FORMAT;
  const ZT = window.ServerTime;

  const formats = {
    dm: new Intl.DateTimeFormat(LOCALE, ZT.withZone({day: '2-digit', month: '2-digit'})),
    dmy: new Intl.DateTimeFormat(LOCALE, ZT.withZone({day: '2-digit', month: '2-digit', year: 'numeric'})),
    month: new Intl.DateTimeFormat(LOCALE, ZT.withZone({month: 'long'})),
    hm: new Intl.DateTimeFormat(LOCALE, ZT.withZone({hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})),
  };
  const fmtDate = (format, ts) => format.format(new Date(ts * 1000));
  const num = (value, decimals) => NumberFormat.fmt(value, decimals);

  // Auswahl-Namen (relativ) — im Tabellenkopf steht stattdessen der konkrete Zeitraum.
  function columnName(key) {
    return {
      heute: t('Heute'), gestern: t('Gestern'), monat: t('Dieser Monat'),
      vormonat: t('Letzter Monat'), jahr: t('Dieses Jahr'),
    }[key];
  }

  function columnTitle(col, data) {
    // Mit Stichtag stehen „Heute“ und „Gestern“ für andere Tage: dann das Datum selbst.
    if (data && data.at && (col.key === 'heute' || col.key === 'gestern')) return fmtDate(formats.dmy, col.cur.start);
    if (col.key === 'heute') return t('Heute');
    if (col.key === 'gestern') return t('Gestern');
    if (col.key === 'jahr') return String(ZT.parts(col.cur.start).year);
    return fmtDate(formats.month, col.cur.start);
  }

  function sameDay(a, b) {
    const pa = ZT.parts(a), pb = ZT.parts(b);
    return pa.year === pb.year && pa.month === pb.month && pa.day === pb.day;
  }

  // "01.11. – 02.11.2026"; ein einzelner Tag ohne Bereich. Ende ist exklusiv, daher end - 1 s.
  function rangeText(start, end) {
    if (end <= start) return '';
    const last = end - 1;
    if (sameDay(start, last)) return fmtDate(formats.dmy, start);
    return `${fmtDate(formats.dm, start)} – ${fmtDate(formats.dmy, last)}`;
  }

  function delta(cur, previous, unit, decimals, withPercent) {
    if (cur == null || previous == null) return '';
    const diff = cur - previous;
    const zero = Math.round(Math.abs(diff) * Math.pow(10, decimals)) === 0;
    let text = (zero ? '± ' : diff > 0 ? '▲ +' : '▼ −') + num(Math.abs(diff), decimals);
    if (unit) text += ' ' + unit;
    if (withPercent && previous !== 0 && !zero) {
      const percent = diff / previous * 100;
      text += ' · ' + (percent > 0 ? '+' : '−') + num(Math.abs(percent), 0) + ' %';
    }
    return `<span class="wt-dl">${escapeHtml(text)}</span>`;
  }

  // Kennzahl-Zeilen je Art der Rolle. when: 'point' = Zeitpunkt (Stunde bzw. Datum),
  // 'day' = ganzer Tag (in Tagesspalten ohne Zusatz).
  function metricsFor(row, data) {
    if (row.kind === 'kenntage') {
      return [
        {label: t('Frosttage'), field: 'frost', decimals: 0, unit: '', delta: true},
        {label: t('Eistage'), field: 'eis', decimals: 0, unit: '', delta: true},
        {label: t('Sommertage'), field: 'sommer', decimals: 0, unit: '', delta: true},
        {label: t('Heiße Tage'), field: 'heiss', decimals: 0, unit: '', delta: true},
      ];
    }
    if (row.kind === 'standard') {
      // Strahlung fällt nachts auf 0: ein Minimum sagt dort nichts, Max und Ø genügen.
      const minimum = row.key === 'radiation' ? [] : [{label: t('Min'), field: 'min', when: 'point', delta: true}];
      return [
        {label: t('Max'), field: 'max', when: 'point', delta: true},
        ...minimum,
        {label: 'Ø', field: 'avg', delta: true},
      ];
    }
    if (row.kind === 'counter') {
      return [
        {label: t('Summe'), field: 'sum', decimals: 1, delta: true, percent: true},
        {label: t('Regentage (ab {n} mm)', {n: num(data.rain_day_mm, 1)}), field: 'days', decimals: 0, unit: ''},
        {label: t('Nassester Tag'), field: 'top', decimals: 1, when: 'day'},
      ];
    }
    return [
      {label: t('Regenminuten'), field: 'minutes', decimals: 0, unit: 'min', delta: true, percent: true},
      {label: t('Regentage (ab {n} min)', {n: num(data.rain_day_minutes, 0)}), field: 'days', decimals: 0, unit: ''},
      {label: t('Regenreichster Tag'), field: 'top', decimals: 0, unit: 'min', when: 'day'},
    ];
  }

  function whenText(metric, summary, col) {
    if (!metric.when || !summary) return '';
    const ts = summary[metric.field + '_ts'];
    if (ts == null) return '';
    if (col.range === 'day') {
      // Die Werte stammen aus Stundenzeilen: „0–1 Uhr“ ist ehrlicher als eine Minutenangabe wie „00:00“.
      if (metric.when !== 'point') return '';
      const hour = ZT.parts(ts).hour;
      return t('{from}–{to} Uhr', {from: hour, to: hour + 1});
    }
    return fmtDate(formats.dm, ts);
  }

  function cellHtml(summary, fromTs, metric, row, col, vjCell, deltaHtml) {
    const cls = (vjCell ? 'wt-vj' : 'wt-sep');
    if (!summary || summary[metric.field] == null) {
      const title = fromTs ? ` title="${escapeHtml(t('Daten ab {date}', {date: fmtDate(formats.dmy, fromTs)}))}"` : '';
      return `<td class="wt-na ${cls}"${title}><span class="wt-v">–</span></td>`;
    }
    const decimals = metric.decimals != null ? metric.decimals : (row.decimals != null ? row.decimals : 1);
    const unit = metric.unit != null ? metric.unit : row.unit;
    const when = whenText(metric, summary, col);
    return `<td class="${cls}"><span class="wt-v">${escapeHtml(num(summary[metric.field], decimals))}${unit ? '&nbsp;' + escapeHtml(unit) : ''}</span>`
      + (when ? `<span class="wt-d">${escapeHtml(when)}</span>` : '')
      + (deltaHtml || '') + '</td>';
  }

  function renderTable(data) {
    const cols = data.columns;
    if (!cols.length) return `<tbody><tr><td>${escapeHtml(t('Keine Spalte ausgewählt.'))}</td></tr></tbody>`;
    const span = data.vorjahr ? 2 : 1;
    let html = '<thead><tr class="wt-g"><th></th>';
    for (const col of cols) html += `<th class="wt-sep" colspan="${span}">${escapeHtml(columnTitle(col, data))}</th>`;
    html += '</tr><tr><th></th>';
    for (const col of cols) {
      let sub = rangeText(col.cur.start, col.cur.end);
      if (col.key === 'heute' && !data.at) sub = t('bis {time}', {time: fmtDate(formats.hm, data.now)});
      html += `<th class="wt-sep"><span class="wt-sb">${escapeHtml(sub)}</span></th>`;
      if (data.vorjahr) {
        html += `<th class="wt-vj"><span class="wt-pill">${escapeHtml(t('Vorjahr'))}</span><span class="wt-sb">${escapeHtml(rangeText(col.vj.start, col.vj.end))}</span></th>`;
      }
    }
    html += '</tr></thead><tbody>';
    for (const row of data.rows) {
      html += `<tr class="wt-grp"><th>${escapeHtml(row.label)}</th>`
        + cols.map(() => '<td></td>' + (data.vorjahr ? '<td></td>' : '')).join('') + '</tr>';
      for (const metric of metricsFor(row, data)) {
        html += `<tr><th scope="row">${escapeHtml(metric.label)}</th>`;
        for (const col of cols) {
          const cell = row.cells[col.key] || {};
          const decimals = metric.decimals != null ? metric.decimals : (row.decimals != null ? row.decimals : 1);
          const unit = metric.unit != null ? metric.unit : (row.unit === '°C' ? 'K' : row.unit);
          const deltaHtml = data.vorjahr && metric.delta && cell.cur && cell.vj
            ? delta(cell.cur[metric.field], cell.vj[metric.field], unit, decimals, !!metric.percent) : '';
          html += cellHtml(cell.cur, cell.cur_from, metric, row, col, false, deltaHtml);
          if (data.vorjahr) html += cellHtml(cell.vj, cell.vj_from, metric, row, col, true, '');
        }
        html += '</tr>';
      }
    }
    return html + '</tbody>';
  }

  // ---- Diagramme (Phase 2) ------------------------------------------------------------------
  // ECharts-Instanzen liegen außerhalb des Alpine-Objekts: ECharts verlässt sich intern auf
  // Objekt-Identität, ein Alpine-Proxy bricht das lautlos (wie bei den anderen Seiten).
  const charts = {};

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function scaledFont(size) {
    return Math.round(size * (parseFloat(cssVar('--font-scale')) || 1) * 10) / 10;
  }

  // Bei jedem Zeichnen frisch gelesen: ein Wechsel von Farbschema oder Schriftgröße gilt sofort.
  function palette() {
    return {
      cur: cssVar('--chart-1'), prev: cssVar('--chart-2'), cold: cssVar('--chart-3'),
      text: cssVar('--ink-muted'), faint: cssVar('--ink-faint'), grid: cssVar('--border'),
      ink: cssVar('--ink'), surface: cssVar('--surface'),
    };
  }

  function chartFor(key, element) {
    if (charts[key] && charts[key].getDom() !== element) {
      charts[key].dispose();
      delete charts[key];
    }
    if (!charts[key]) charts[key] = echarts.init(element);
    return charts[key];
  }

  window.addEventListener('resize', () => Object.values(charts).forEach((chart) => chart.resize()));

  const monthFormat = (style) => new Intl.DateTimeFormat(LOCALE, {month: style, timeZone: 'UTC'});
  const monthName = (month, style) => monthFormat(style).format(new Date(Date.UTC(2000, month - 1, 15)));
  const MONTH_LENGTHS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const MONTH_STARTS = MONTH_LENGTHS.map((_len, index) => MONTH_LENGTHS.slice(0, index).reduce((a, b) => a + b, 0));

  // Platz 0..364 im Nicht-Schaltjahr -> {month 1..12, day}
  function slotDate(slot) {
    let month = 11;
    while (MONTH_STARTS[month] > slot) month--;
    return {month: month + 1, day: slot - MONTH_STARTS[month] + 1};
  }

  // Datum eines Platzes; in UTC formatiert, damit die Zeitzone des Browsers kein Datum verschiebt.
  const utcDayMonth = new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', timeZone: 'UTC'});
  // Spalten der Jahr×Tag-Matrix: ein Schaltjahr, damit der 29.02. eine eigene Spalte hat.
  const MONTH_LENGTHS_LEAP = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const MONTH_STARTS_LEAP = MONTH_LENGTHS_LEAP.map((_len, index) => MONTH_LENGTHS_LEAP.slice(0, index).reduce((a, b) => a + b, 0));
  function leapSlotDate(slot) {
    let month = 11;
    while (MONTH_STARTS_LEAP[month] > slot) month--;
    return {month: month + 1, day: slot - MONTH_STARTS_LEAP[month] + 1};
  }

  const slotLabel = (slot) => {
    const {month, day} = slotDate(slot);
    return utcDayMonth.format(new Date(Date.UTC(2025, month - 1, day)));
  };

  function axisStyle(colors) {
    return {
      axisLine: {lineStyle: {color: colors.grid}}, axisTick: {show: false},
      axisLabel: {color: colors.text, fontSize: scaledFont(11)},
      splitLine: {lineStyle: {color: colors.grid}},
    };
  }

  const gridBox = {left: 8, right: 16, top: 34, bottom: 8, containLabel: true};

  // Auf schmalen Bildschirmen bricht die Legende in zwei Zeilen um und braucht mehr Platz über dem Diagramm.
  const gridFor = (element, extra) => ({...gridBox, top: element.clientWidth < 520 ? 74 : 34, ...extra});

  function legendBox(colors, data) {
    return {data, top: 0, textStyle: {color: colors.text, fontSize: scaledFont(12)}, itemWidth: 18, itemHeight: 8};
  }

  function tooltipBox(colors, formatter) {
    return {
      trigger: 'axis', confine: true, formatter,
      backgroundColor: colors.surface, borderColor: colors.grid,
      textStyle: {color: colors.ink, fontSize: scaledFont(12)},
    };
  }

  function nullable(values, scale) {
    return values.map((value) => (value == null ? null : value * scale));
  }

  function readStore() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {};
    } catch (e) {
      return {};
    }
  }

  function writeStore(value) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({...readStore(), ...value}));
    } catch (e) {
      // Privatmodus o. Ä.: die Auswahl gilt dann nur bis zum Neuladen.
    }
  }

  window.wetterDashboard = function wetterDashboard(precipModes) {
    return {
      tab: 'overview',
      stichtag: '',
      todayIso: '',
      precipModes: precipModes || [],
      precipMode: (precipModes && precipModes[0]) || 'mm',
      precip: null,
      precipError: '',
      monthly: null,
      monthlyMetric: 'temperature',
      band: true,
      longterm: null,
      longError: '',
      kenntageYear: '',
      matrixMetric: 'avg',
      heat: null,
      heatOn: [],
      heatRoom: '20',
      heatLimit: '15',
      heatSeason: '',
      heatError: '',
      matrix: null,
      matrixError: '',

      columnKeys: COLUMN_KEYS,
      columns: [...COLUMN_KEYS],
      vorjahr: true,
      mode: 'bis_jetzt',
      tiles: [],
      tilesLoaded: false,
      error: '',
      _seq: 0,

      init() {
        const saved = readStore();
        if (Array.isArray(saved.columns)) this.columns = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        const requested = new URLSearchParams(window.location.search).get('at');
        if (requested && /^\d{4}-\d{2}-\d{2}$/.test(requested)) this.stichtag = requested;
        this.loadTiles();
        this.loadTable();
        this.loadMonthly();
      },

      columnName,

      toggleColumn(key) {
        this.columns = this.columns.includes(key)
          ? this.columns.filter((k) => k !== key)
          : COLUMN_KEYS.filter((k) => k === key || this.columns.includes(k));
        this.saveAndLoad();
      },

      // Stichtag wählen (leer = heute). Er steht in der Adresse (?at=…), damit sich die Ansicht teilen lässt.
      setStichtag(value) {
        this.stichtag = value && value < this.todayIso ? value : '';
        const url = new URL(window.location.href);
        if (this.stichtag) url.searchParams.set('at', this.stichtag);
        else url.searchParams.delete('at');
        window.history.replaceState(null, '', url);
        this.loadTable();
      },

      setMode(mode) {
        this.mode = mode;
        this.saveAndLoad();
      },

      saveAndLoad() {
        writeStore({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode});
        this.loadTable();
      },

      modeHint() {
        const base = this.mode === 'bis_jetzt'
          ? t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Abgeschlossene Zeiträume (Gestern, Letzter Monat) sind in beiden Fällen gleich.')
          : t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch mehr Spannweite und mehr Niederschlag.');
        if (!this.stichtag) return base;
        const date = fmtDate(formats.dmy, Date.parse(this.stichtag + 'T12:00:00Z') / 1000);
        return `${base} ${t('Die Tabelle zeigt die Zeiträume zum {date}; die Kacheln zeigen weiter die aktuellen Werte.', {date})}`;
      },

      tileValue(tile) {
        const unit = (text) => (text ? `<small>${escapeHtml(text)}</small>` : '');
        const decimals = tile.decimals != null ? tile.decimals : 1;
        if (tile.kind === 'standard') {
          return tile.last == null ? '–' : escapeHtml(num(tile.last, decimals)) + unit(tile.unit);
        }
        if (tile.kind === 'counter') {
          return tile.today ? escapeHtml(num(tile.today.sum, 1)) + unit(tile.unit) : '–';
        }
        return tile.today ? escapeHtml(num(tile.today.minutes, 0)) + unit('min') : '–';
      },

      tileSub(tile) {
        const decimals = tile.decimals != null ? tile.decimals : 1;
        if (tile.kind === 'standard') {
          if (!tile.today) return '';
          return tile.key === 'radiation'
            ? t('heute max. {max}', {max: num(tile.today.max, decimals)})
            : t('heute {min} / {max}', {min: num(tile.today.min, decimals), max: num(tile.today.max, decimals)});
        }
        if (!tile.yesterday) return '';
        if (tile.kind === 'counter') {
          return t('heute · gestern {value} {unit}', {value: num(tile.yesterday.sum, 1), unit: tile.unit || ''});
        }
        return t('heute · gestern {value} min', {value: num(tile.yesterday.minutes, 0)});
      },

      // ---- Reiter und Diagramme (Phase 2) ----------------------------------------------------

      showTab(tab) {
        this.tab = tab;
        if (tab === 'precip' && !this.precip) this.loadPrecip();
        if (tab === 'long' && !this.longterm) this.loadLongterm();
        if (tab === 'long' && !this.matrix) this.loadMatrix();
        if (tab === 'heat' && !this.heat) this.loadHeat();
        // Ein Diagramm in einem eben erst sichtbaren Reiter hat noch keine Größe.
        this.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
      },

      // Pfeiltasten, Pos1 und Ende wechseln wie bei jeder Reiterleiste zwischen den Reitern.
      tabKey(event) {
        const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
        if (!keys.includes(event.key)) return;
        const tabs = [...event.currentTarget.parentElement.querySelectorAll('[role="tab"]')];
        const current = tabs.indexOf(event.currentTarget);
        const target = {
          ArrowLeft: (current + tabs.length - 1) % tabs.length, ArrowRight: (current + 1) % tabs.length,
          Home: 0, End: tabs.length - 1,
        }[event.key];
        event.preventDefault();
        tabs[target].click();
        tabs[target].focus();
      },

      async fetchJson(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      },

      // Monatsvergleich
      async loadMonthly() {
        try {
          this.monthly = await this.fetchJson(
            `wetterdashboard/monatsvergleich?metric=${this.monthlyMetric}&mode=${this.precipModes[0] || 'mm'}`,
          );
          this.$nextTick(() => this.drawMonthly());
        } catch (e) {
          this.monthly = null;
        }
      },

      setMonthlyMetric(metric) {
        this.monthlyMetric = metric;
        this.loadMonthly();
      },

      headline() {
        const h = this.monthly && this.monthly.headline;
        if (!h) return '';
        const diff = h.current - h.reference;
        const values = {
          month: monthName(h.month, 'long'), value: num(h.current, 1), unit: this.monthly.unit,
          delta: num(Math.abs(diff), 1), from: h.first_year, to: h.last_year, n: h.n_years,
          range: h.last_day === 1 ? t('nur der 1.') : `1.–${h.last_day}.`,
        };
        if (Math.round(Math.abs(diff) * 10) === 0) {
          return t('{month} bisher Ø {value} {unit}, so warm wie der Ø {from}–{to} ({range}, aus {n} Jahren)', values);
        }
        return diff < 0
          ? t('{month} bisher Ø {value} {unit}, {delta} K kälter als der Ø {from}–{to} ({range}, aus {n} Jahren)', values)
          : t('{month} bisher Ø {value} {unit}, {delta} K wärmer als der Ø {from}–{to} ({range}, aus {n} Jahren)', values);
      },

      monthlyNote() {
        const m = this.monthly;
        if (!m) return '';
        const used = m.counts.filter((count) => count > 0);
        if (!used.length) return t('Noch kein abgeschlossenes Vorjahr mit genug Werten für einen Durchschnitt.');
        const n = Math.min(...used);
        return n === Math.max(...used)
          ? t('Ø aus {n} Jahren, ohne das laufende Jahr. Der laufende Monat zeigt nur den bisherigen Stand.', {n})
          : t('Ø aus 1 bis {n} Jahren je Monat, ohne das laufende Jahr. Der laufende Monat zeigt nur den bisherigen Stand.', {n: Math.max(...used)});
      },

      drawMonthly() {
        const m = this.monthly;
        const element = this.$refs.monthlyChart;
        if (!m || !element) return;
        const colors = palette();
        const decimals = m.metric === 'temperature' ? 1 : (m.unit === 'min' ? 0 : 1);
        const labels = Array.from({length: 12}, (_x, i) => monthName(i + 1, 'short'));
        const yearKeys = Object.keys(m.years).map(Number).sort((a, b) => a - b);
        const current = String(m.current_year);
        const lastPast = yearKeys.filter((year) => year < m.current_year).pop();
        const hasAvg = m.counts.some((count) => count > 0);
        const series = [];
        const legend = [];
        if (this.band && hasAvg) {
          series.push({
            name: 'low', type: 'line', stack: 'band', data: m.low, symbol: 'none', silent: true,
            lineStyle: {opacity: 0}, tooltip: {show: false},
          }, {
            name: 'band', type: 'line', stack: 'band', symbol: 'none', silent: true, lineStyle: {opacity: 0},
            data: m.high.map((high, i) => (high == null || m.low[i] == null ? null : high - m.low[i])),
            areaStyle: {color: colors.prev, opacity: 0.16},
          });
        }
        for (const year of yearKeys) {
          if (year >= m.current_year) continue;
          const strong = year === lastPast;
          series.push({
            name: String(year), type: 'line', data: m.years[year], symbol: 'none', connectNulls: false,
            lineStyle: {color: colors.prev, width: strong ? 2 : 1.2, opacity: strong ? 1 : 0.45},
            itemStyle: {color: colors.prev},
          });
          if (strong) legend.push(String(year));
        }
        if (hasAvg) {
          series.push({
            name: 'Ø', type: 'line', data: m.avg, symbol: 'none',
            lineStyle: {color: colors.text, width: 2, type: 'dashed'}, itemStyle: {color: colors.text},
          });
          legend.push('Ø');
        }
        if (m.years[current]) {
          series.push({
            name: current, type: 'line', data: m.years[current], symbol: 'circle', symbolSize: 7, z: 5,
            lineStyle: {color: colors.cur, width: 3}, itemStyle: {color: colors.cur},
          });
          legend.unshift(current);
        }
        const unit = m.unit;
        const chart = chartFor('monthly', element);
        chart.setOption({
          animation: false, grid: gridFor(element), legend: legendBox(colors, legend),
          xAxis: {type: 'category', data: labels, boundaryGap: false, ...axisStyle(colors)},
          yAxis: {type: 'value', scale: m.metric === 'temperature', name: unit, nameTextStyle: {color: colors.text}, ...axisStyle(colors)},
          tooltip: tooltipBox(colors, (params) => {
            const i = params[0].dataIndex;
            const cell = (value) => (value == null ? '–' : `${num(value, decimals)} ${unit}`);
            const running = i === m.current_month - 1;
            let html = `<b>${escapeHtml(monthName(i + 1, 'long'))}</b>`;
            const now = m.years[current] ? m.years[current][i] : null;
            html += `<br>${current}: ${cell(now)}${running && now != null ? ' ' + escapeHtml(t('(bisher)')) : ''}`;
            if (lastPast != null) html += `<br>${lastPast}: ${cell(m.years[lastPast][i])}`;
            if (m.avg[i] != null) {
              html += `<br>Ø (${m.counts[i]}): ${cell(m.avg[i])}`;
              if (now != null && !running) html += `<br>${escapeHtml(t('Abweichung'))}: ${num(now - m.avg[i], decimals)} ${unit === '°C' ? 'K' : unit}`;
            }
            return html;
          }),
          series,
        }, true);
        // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
        requestAnimationFrame(() => chart.resize());
      },

      // Niederschlag
      async loadPrecip() {
        this.precipError = '';
        try {
          this.precip = await this.fetchJson(`wetterdashboard/niederschlag?mode=${this.precipMode}`);
          this.$nextTick(() => this.drawPrecip());
        } catch (e) {
          this.precipError = t('Der Niederschlag konnte nicht geladen werden.');
        }
      },

      setPrecipMode(mode) {
        this.precipMode = mode;
        this.precip = null;
        this.loadPrecip();
      },

      // Stunden statt Minuten, wenn der Niederschlag aus einem Regensensor stammt (Plan: „in Stunden“).
      precipScale() {
        return this.precip && this.precip.mode === 'dur' ? 1 / 60 : 1;
      },

      precipUnit() {
        return this.precip && this.precip.mode === 'dur' ? 'h' : (this.precip ? this.precip.unit : '');
      },

      precipAmount(value) {
        return value == null ? '–' : `${num(value * this.precipScale(), 1)} ${this.precipUnit()}`;
      },

      precipDateText() {
        const p = this.precip;
        return p ? utcDayMonth.format(new Date(Date.UTC(2025, slotDate(p.today_slot).month - 1, slotDate(p.today_slot).day))) : '';
      },

      precipHeadline() {
        const p = this.precip;
        if (!p || p.total == null) return p ? t('Seit Jahresbeginn liegen noch keine Werte vor.') : '';
        const values = {value: this.precipAmount(p.total), date: this.precipDateText()};
        if (p.avg_to_date == null || p.avg_to_date === 0) {
          return p.previous_to_date == null
            ? t('{value} bis {date}', values)
            : t('{value} bis {date}, Vorjahr {previous}', {...values, previous: this.precipAmount(p.previous_to_date)});
        }
        const percent = (p.total - p.avg_to_date) / p.avg_to_date * 100;
        const full = {
          ...values, percent: num(Math.abs(percent), 0), avg: this.precipAmount(p.avg_to_date), n: p.avg_years.length,
        };
        if (Math.round(Math.abs(percent)) === 0) return t('{value} bis {date}, so viel wie im Ø ({avg}, aus {n} Jahren)', full);
        return percent < 0
          ? t('{value} bis {date}, {percent} % unter dem Ø ({avg}, aus {n} Jahren)', full)
          : t('{value} bis {date}, {percent} % über dem Ø ({avg}, aus {n} Jahren)', full);
      },

      precipKpis() {
        const p = this.precip;
        if (!p) return [];
        const kpis = [];
        const asDate = (iso) => utcDayMonth.format(new Date(iso + 'T12:00:00Z'));
        if (p.mode === 'dur') {
          kpis.push({label: t('Regenminuten'), value: escapeHtml(num(p.total, 0)) + '<small>min</small>', sub: ''});
        }
        kpis.push({
          label: p.mode === 'dur' ? t('Regentage (ab {n} min)', {n: num(p.threshold, 0)}) : t('Regentage (ab {n} mm)', {n: num(p.threshold, 1)}),
          value: escapeHtml(String(p.wet_days)), sub: t('seit Jahresbeginn'),
        });
        if (p.wettest) {
          kpis.push({
            label: p.mode === 'dur' ? t('Regenreichster Tag') : t('Nassester Tag'),
            value: escapeHtml(num(p.wettest.value * this.precipScale(), 1)) + `<small>${escapeHtml(this.precipUnit())}</small>`,
            sub: asDate(p.wettest.date),
          });
        }
        kpis.push({
          label: t('Längste Trockenphase'),
          value: escapeHtml(String(p.longest_dry_days)) + `<small>${escapeHtml(t('Tage'))}</small>`,
          sub: p.longest_dry_end ? t('bis {date}', {date: asDate(p.longest_dry_end)}) : '',
        });
        if (p.current_dry_days > 0) {
          kpis.push({label: t('Aktuell trocken'), value: escapeHtml(String(p.current_dry_days)) + `<small>${escapeHtml(t('Tage'))}</small>`, sub: ''});
        }
        return kpis;
      },

      drawPrecip() {
        const p = this.precip;
        const element = this.$refs.precipChart;
        if (!p || !element) return;
        const colors = palette();
        const scale = this.precipScale();
        const unit = this.precipUnit();
        const labels = Array.from({length: 365}, (_x, slot) => slotLabel(slot));
        const cumulative = nullable(p.cumulative, scale);
        const previous = p.previous ? nullable(p.previous, scale) : null;
        const avg = nullable(p.avg, scale);
        const low = nullable(p.low, scale);
        const high = nullable(p.high, scale);
        const hasAvg = p.avg_years.length >= 2;
        const dailyMax = Math.max(0, ...p.daily.filter((v) => v != null)) * scale;
        const series = [];
        const legend = [String(p.year)];
        if (hasAvg) {
          series.push({
            name: 'low', type: 'line', stack: 'band', data: low, symbol: 'none', silent: true,
            lineStyle: {opacity: 0}, tooltip: {show: false},
          }, {
            name: 'band', type: 'line', stack: 'band', symbol: 'none', silent: true, lineStyle: {opacity: 0},
            data: high.map((h, i) => (h == null || low[i] == null ? null : h - low[i])),
            areaStyle: {color: colors.prev, opacity: 0.16},
          }, {
            name: 'Ø', type: 'line', data: avg, symbol: 'none',
            lineStyle: {color: colors.text, width: 2, type: 'dashed'}, itemStyle: {color: colors.text},
          });
          legend.push('Ø');
        }
        if (previous) {
          series.push({
            name: String(p.previous_year), type: 'line', data: previous, symbol: 'none',
            lineStyle: {color: colors.prev, width: 2}, itemStyle: {color: colors.prev},
          });
          legend.push(String(p.previous_year));
        }
        series.push({
          name: String(p.year), type: 'line', data: cumulative, symbol: 'none', z: 5,
          lineStyle: {color: colors.cur, width: 3}, itemStyle: {color: colors.cur},
        }, {
          name: t('Tageswert'), type: 'bar', yAxisIndex: 1, data: nullable(p.daily, scale), barMaxWidth: 4,
          itemStyle: {color: colors.cur, opacity: 0.45},
        });
        const chart = chartFor('precip', element);
        chart.setOption({
          animation: false, grid: gridFor(element, {right: 8}), legend: legendBox(colors, legend),
          xAxis: {
            type: 'category', data: labels, boundaryGap: false, ...axisStyle(colors),
            axisLabel: {
              color: colors.text, fontSize: scaledFont(11),
              interval: (index) => MONTH_STARTS.includes(index),
              formatter: (_value, index) => monthName(slotDate(index).month, element.clientWidth < 520 ? 'narrow' : 'short'),
            },
            splitLine: {show: false},
          },
          yAxis: [
            {type: 'value', min: 0, name: unit, nameTextStyle: {color: colors.text}, ...axisStyle(colors)},
            {type: 'value', min: 0, max: Math.max(dailyMax * 4, 1), show: false, splitLine: {show: false}},
          ],
          tooltip: tooltipBox(colors, (params) => {
            const i = params[0].dataIndex;
            const cell = (value) => (value == null ? '–' : `${num(value, 1)} ${unit}`);
            let html = `<b>${escapeHtml(labels[i])}</b>`;
            html += `<br>${escapeHtml(t('Tageswert'))}: ${cell(p.daily[i] == null ? null : p.daily[i] * scale)}`;
            html += `<br>${p.year}: ${cell(cumulative[i])}`;
            if (previous) html += `<br>${p.previous_year}: ${cell(previous[i])}`;
            if (hasAvg && avg[i] != null) html += `<br>Ø (${p.avg_years.length}): ${cell(avg[i])}`;
            return html;
          }),
          series,
        }, true);
        // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
        requestAnimationFrame(() => chart.resize());
      },

      // Langzeit
      async loadLongterm() {
        this.longError = '';
        try {
          this.longterm = await this.fetchJson('wetterdashboard/langzeit');
          const years = this.kenntageYears();
          this.kenntageYear = years.includes(String(this.longterm.current_year))
            ? String(this.longterm.current_year) : (years[years.length - 1] || '');
          this.$nextTick(() => this.drawKenntage());
        } catch (e) {
          this.longError = t('Die Langzeitwerte konnten nicht geladen werden.');
        }
      },

      kenntageYears() {
        return this.longterm ? Object.keys(this.longterm.kenntage).sort() : [];
      },

      kenntageNote() {
        const year = this.longterm && this.longterm.kenntage[this.kenntageYear];
        if (!year) return t('Noch keine Tage mit ausreichend Werten (mindestens 18 Stunden je Tag).');
        const days = year.days.reduce((a, b) => a + b, 0);
        return t('Frosttag: Tiefstwert unter 0 °C, Eistag: Höchstwert unter 0 °C, Sommertag: Höchstwert ab 25 °C, heißer Tag: ab 30 °C. Gezählt sind {days} abgeschlossene Tage mit mindestens 18 Stunden Werten; der laufende Tag zählt nicht.', {days});
      },

      drawKenntage() {
        const element = this.$refs.kenntageChart;
        const year = this.longterm && this.longterm.kenntage[this.kenntageYear];
        if (!element) return;
        if (!year) {
          if (charts.kenntage) charts.kenntage.clear();
          return;
        }
        const colors = palette();
        const labels = Array.from({length: 12}, (_x, i) => monthName(i + 1, 'short'));
        const bar = (name, data, color, width, opacity) => ({
          name, type: 'bar', data, barGap: '-100%', barWidth: width, itemStyle: {color, opacity},
        });
        const negative = (values) => values.map((value) => (value ? -value : 0));
        const chart = chartFor('kenntage', element);
        chart.setOption({
          animation: false, grid: gridFor(element),
          legend: legendBox(colors, [t('Sommertage'), t('Heiße Tage'), t('Frosttage'), t('Eistage')]),
          xAxis: {type: 'category', data: labels, ...axisStyle(colors), splitLine: {show: false}, axisLabel: {color: colors.text, fontSize: scaledFont(11), margin: 12}},
          yAxis: {
            type: 'value', name: t('Tage'), nameTextStyle: {color: colors.text}, ...axisStyle(colors),
            axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (value) => String(Math.abs(value))},
          },
          tooltip: tooltipBox(colors, (params) => {
            const i = params[0].dataIndex;
            return `<b>${escapeHtml(monthName(i + 1, 'long'))} ${escapeHtml(this.kenntageYear)}</b>`
              + `<br>${escapeHtml(t('Sommertage'))}: ${year.sommer[i]} (${escapeHtml(t('heiß'))}: ${year.heiss[i]})`
              + `<br>${escapeHtml(t('Frosttage'))}: ${year.frost[i]} (${escapeHtml(t('Eis'))}: ${year.eis[i]})`
              + `<br>${escapeHtml(t('{days} Tage ausgewertet', {days: year.days[i]}))}`;
          }),
          series: [
            bar(t('Sommertage'), year.sommer, colors.prev, '62%', 0.45),
            bar(t('Heiße Tage'), year.heiss, colors.prev, '34%', 1),
            bar(t('Frosttage'), negative(year.frost), colors.cold, '62%', 0.45),
            bar(t('Eistage'), negative(year.eis), colors.cold, '34%', 1),
          ],
        }, true);
        // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
        requestAnimationFrame(() => chart.resize());
      },

      // Jahr×Tag-Matrix
      async loadMatrix() {
        this.matrixError = '';
        try {
          this.matrix = await this.fetchJson(
            `wetterdashboard/matrix?metric=${this.matrixMetric}&mode=${this.precipModes[0] || 'mm'}`,
          );
          this.$nextTick(() => this.drawMatrix());
        } catch (e) {
          this.matrixError = t('Die Jahresübersicht konnte nicht geladen werden.');
        }
      },

      setMatrixMetric(metric) {
        this.matrixMetric = metric;
        this.matrix = null;
        this.loadMatrix();
      },

      // Eine Zeile je Jahr: 20 Pixel plus Platz für Achse und Farbskala.
      matrixHeight() {
        const years = this.matrix ? this.matrix.years.length : 3;
        return `height: ${Math.max(160, years * 20 + 90)}px`;
      },

      matrixNote() {
        const m = this.matrix;
        if (!m) return '';
        if (!m.years.length) return t('Noch keine Tageswerte.');
        return t('Neuestes Jahr oben, eine Spalte je Kalendertag. Graue Felder sind Tage ohne ausreichende Werte (mindestens 18 Stunden), leer ist es vor dem Archivbeginn und ab heute. Ein Klick öffnet den Tag in der Entität.');
      },

      drawMatrix() {
        const m = this.matrix;
        const element = this.$refs.matrixChart;
        if (!m || !element) return;
        if (!m.years.length || m.min == null) {
          if (charts.matrix) charts.matrix.clear();
          return;
        }
        const colors = palette();
        const isPrecip = m.metric === 'precip';
        const decimals = isPrecip && m.unit === 'min' ? 0 : 1;
        const unit = m.unit;
        const inRange = isPrecip
          ? [colors.surface, colors.cold]
          : [colors.cold, colors.surface, colors.prev];
        const hidden = {itemStyle: {borderWidth: 0}};
        const chart = chartFor('matrix', element);
        chart.setOption({
          animation: false,
          grid: {left: 8, right: 16, top: 6, bottom: 52, containLabel: true},
          xAxis: {
            type: 'category', data: Array.from({length: 366}, (_x, i) => i), splitArea: {show: false},
            axisLine: {lineStyle: {color: colors.grid}}, axisTick: {show: false},
            axisLabel: {
              color: colors.text, fontSize: scaledFont(11),
              interval: (index) => MONTH_STARTS_LEAP.includes(index),
              formatter: (_value, index) => monthName(leapSlotDate(index).month, element.clientWidth < 520 ? 'narrow' : 'short'),
            },
          },
          yAxis: {
            type: 'category', data: m.years.map(String), inverse: true,
            axisLine: {show: false}, axisTick: {show: false}, axisLabel: {color: colors.text, fontSize: scaledFont(11)},
          },
          visualMap: {
            type: 'continuous', min: m.min, max: m.max, seriesIndex: 1, calculable: false, orient: 'horizontal',
            left: 'center', bottom: 4, itemWidth: 12, itemHeight: 140, inRange: {color: inRange},
            textStyle: {color: colors.text, fontSize: scaledFont(11)},
            text: [`${num(m.max, decimals)} ${unit}`, `${num(m.min, decimals)} ${unit}`],
          },
          tooltip: {
            confine: true, backgroundColor: colors.surface, borderColor: colors.grid,
            textStyle: {color: colors.ink, fontSize: scaledFont(12)},
            formatter: (param) => {
              const [slot, row, value] = param.data;
              const when = this.matrixDate(m.years[row], slot);
              const label = new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'}).format(when);
              return param.seriesIndex === 0
                ? `<b>${escapeHtml(label)}</b><br>${escapeHtml(t('Zu wenige Werte'))}`
                : `<b>${escapeHtml(label)}</b><br>${escapeHtml(num(value, decimals))} ${escapeHtml(unit === '°C' ? '°C' : unit)}`;
            },
          },
          series: [
            {
              type: 'heatmap', data: m.missing.map(([slot, row]) => [slot, row, 0]), silent: false, ...hidden,
              itemStyle: {color: colors.grid, borderWidth: 0},
            },
            {type: 'heatmap', data: m.cells, ...hidden},
          ],
        }, true);
        chart.off('click');
        chart.on('click', (param) => {
          if (param.seriesIndex !== 1) return;
          const [slot, row] = param.data;
          window.location.href = this.matrixLink(m, m.years[row], slot);
        });
        requestAnimationFrame(() => chart.resize());
      },

      // Kalendertag einer Spalte als UTC-Datum (Spalte 59 ist der 29.02. eines Schaltjahres).
      matrixDate(year, slot) {
        const leapDay = new Date(Date.UTC(2024, 0, 1 + slot));
        return new Date(Date.UTC(year, leapDay.getUTCMonth(), leapDay.getUTCDate()));
      },

      matrixLink(m, year, slot) {
        const day = this.matrixDate(year, slot);
        const [ty, tm, td] = m.today.split('-').map(Number);
        const offset = Math.round((day.getTime() - Date.UTC(ty, tm - 1, td)) / 86400000);
        return `entities/${encodeURIComponent(m.entity_id)}?range=day&offset=${offset}`;
      },

      // ---- Heizen (Phase 3) ------------------------------------------------------------------

      async loadHeat() {
        this.heatError = '';
        try {
          const data = await this.fetchJson('wetterdashboard/heizen');
          const saved = readStore().heat || {};
          this.heatOn = data.counters.map((_c, i) => (Array.isArray(saved.on) && saved.on.length === data.counters.length ? !!saved.on[i] : true));
          this.heatRoom = String(saved.room || data.room).replace('.', ',');
          this.heatLimit = String(saved.limit || data.limit).replace('.', ',');
          this.heat = data;
          const periods = this.heatPeriods();
          this.heatSeason = String(periods.length > 1 && WetterHeating.currentPeriod(data.today) === periods[0] ? periods[1] : (periods[0] || ''));
          this.$nextTick(() => this.drawHeat());
        } catch (e) {
          this.heatError = t('Die Heizwerte konnten nicht geladen werden.');
        }
      },

      heatOpts() {
        const read = (text, fallback) => {
          const value = NumberFormat.parse(String(text));
          return Number.isFinite(value) ? value : fallback;
        };
        return {room: read(this.heatRoom, 20), limit: read(this.heatLimit, 15), on: this.heatOn};
      },

      heatColor(index) {
        const colors = palette();
        return [colors.cur, colors.prev, colors.cold, cssVar('--chart-4'), cssVar('--chart-5'), cssVar('--chart-6')][index % 6];
      },

      heatLabel: (period) => WetterHeating.label(Number(period)),

      heatPeriods() {
        return this.heat ? WetterHeating.periods(this.heat.days) : [];
      },

      heatTag() {
        const o = this.heatOpts();
        return `G${num(o.room, 0)}/${num(o.limit, 0)}`;
      },

      toggleHeatCounter(index) {
        this.heatOn = this.heatOn.map((value, i) => (i === index ? !value : value));
        this.drawHeat();
      },

      // Wärmste und kälteste Periode für Kopfsatz und Tabelle: running = die laufende Heizperiode.
      heatPeriodInfo() {
        const periods = this.heatPeriods();
        const current = WetterHeating.currentPeriod(this.heat.today);
        const month = Number(this.heat.today.slice(5, 7));
        const running = WetterHeating.MONTHS.includes(month);
        const complete = periods.filter((p) => !running || p !== current);
        return {periods, current, running, complete};
      },

      heatHeadline() {
        if (!this.heat) return '';
        const o = this.heatOpts();
        const info = this.heatPeriodInfo();
        const q = (period) => {
          const [from, to] = WetterHeating.periodRange(period);
          return WetterHeating.stat(this.heat.days, o, from, to).q;
        };
        let text = '';
        if (info.complete.length) {
          const latest = info.complete[0];
          const a = q(latest);
          const b = info.complete[1] != null ? q(info.complete[1]) : null;
          if (a != null && b != null) {
            const change = (a / b - 1) * 100;
            const values = {period: WetterHeating.label(latest), value: num(a, 2), percent: num(Math.abs(change), 0), other: WetterHeating.label(info.complete[1]), before: num(b, 2)};
            text = Math.round(Math.abs(change)) === 0
              ? t('Heizperiode {period}: {value} kWh je Gradtag, genauso viel wie {other} ({before})', values)
              : change < 0
                ? t('Heizperiode {period}: {value} kWh je Gradtag, {percent} % weniger als {other} ({before})', values)
                : t('Heizperiode {period}: {value} kWh je Gradtag, {percent} % mehr als {other} ({before})', values);
          } else if (a != null) {
            text = t('Heizperiode {period}: {value} kWh je Gradtag', {period: WetterHeating.label(latest), value: num(a, 2)});
          }
        }
        if (info.running) {
          const cut = WetterHeating.cutRange(info.current, this.heat.today);
          const now = WetterHeating.stat(this.heat.days, o, cut.from, cut.to);
          const earlier = info.complete[0] != null ? WetterHeating.cutRange(info.complete[0], this.heat.today) : null;
          const before = earlier ? WetterHeating.stat(this.heat.days, o, earlier.from, earlier.to) : null;
          if (now.q != null && before && before.q != null) {
            const change = (now.q / before.q - 1) * 100;
            const part = t('{period} bisher {value} kWh je Gradtag ({percent} % {direction} als im gleichen Ausschnitt von {other})', {
              period: WetterHeating.label(info.current), value: num(now.q, 2), percent: num(Math.abs(change), 0),
              direction: change < 0 ? t('weniger') : t('mehr'), other: WetterHeating.label(info.complete[0]),
            });
            text = text ? `${text}. ${part}` : part;
          }
        }
        return text || t('Noch nicht genug Heiztage für einen Vergleich.');
      },

      heatReadout() {
        if (!this.heat) return '';
        const o = this.heatOpts();
        const parts = [];
        for (const period of this.heatPeriods().slice(0, 4)) {
          const line = WetterHeating.fit(this.heat.days, o, period);
          if (line) parts.push(t('{period}: {value} kWh je Kelvin ({n} Tage, R² {r2})', {period: WetterHeating.label(period), value: num(-line.slope, 2), n: line.n, r2: num(line.r2, 2)}));
        }
        parts.push(t('Gerade nur über Tage unter der Heizgrenze'));
        return parts.join(' · ');
      },

      drawHeat() {
        if (!this.heat) return;
        writeStore({heat: {on: this.heatOn, room: this.heatRoom, limit: this.heatLimit}});
        this.drawHeatScatter();
        this.drawHeatTable();
        this.drawHeatShare();
      },

      drawHeatScatter() {
        const element = this.$refs.heatChart;
        if (!element) return;
        const o = this.heatOpts();
        const colors = palette();
        const periods = this.heatPeriods().slice(0, 4);
        const series = [];
        const legend = [];
        periods.forEach((period, index) => {
          const color = this.heatColor(index);
          const name = WetterHeating.label(period);
          legend.push(name);
          const points = this.heat.days
            .filter((day) => WetterHeating.periodOf(day[0]) === period)
            .map((day) => [day[1], Math.round(WetterHeating.dayKwh(day, o.on) * 100) / 100, day[0]]);
          const line = WetterHeating.fit(this.heat.days, o, period);
          series.push({
            name, type: 'scatter', data: points, symbolSize: index === 0 ? 7 : 5,
            itemStyle: {color, opacity: index === 0 ? 0.9 : 0.5},
            markLine: index === 0 ? {
              silent: true, symbol: 'none', lineStyle: {color: colors.faint, type: 'dashed'},
              label: {formatter: () => `${t('Heizgrenze')} ${num(o.limit, 0)} °C`, color: colors.text, fontSize: scaledFont(11)},
              data: [{xAxis: o.limit}],
            } : undefined,
          });
          if (line) {
            const from = Math.min(...points.filter((p) => p[0] < o.limit).map((p) => p[0]));
            series.push({
              name: `${name} `, type: 'line', symbol: 'none', silent: true, z: 4,
              data: [[from, line.intercept + line.slope * from], [o.limit, line.intercept + line.slope * o.limit]],
              lineStyle: {color, width: 2.2}, tooltip: {show: false},
            });
          }
        });
        const chart = chartFor('heat', element);
        chart.setOption({
          animation: false, grid: gridFor(element, {right: 20, bottom: 24}), legend: legendBox(colors, legend),
          xAxis: {
            type: 'value', name: t('Tages-Ø Außentemperatur (°C)'), nameLocation: 'middle', nameGap: 26,
            nameTextStyle: {color: colors.text}, ...axisStyle(colors), min: (value) => Math.floor(value.min / 5) * 5,
          },
          // Einheit an den Teilstrichen statt als Achsenname: der Name landete in diesem Diagramm mitten in der Legende.
          yAxis: {
            type: 'value', min: 0, ...axisStyle(colors),
            axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (value) => `${value} kWh`},
          },
          tooltip: {
            trigger: 'item', confine: true, backgroundColor: colors.surface, borderColor: colors.grid,
            textStyle: {color: colors.ink, fontSize: scaledFont(12)},
            formatter: (param) => (param.data && param.data[2]
              ? `<b>${escapeHtml(new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'}).format(new Date(param.data[2] + 'T12:00:00Z')))}</b>`
                + `<br>${escapeHtml(num(param.data[0], 1))} °C<br>${escapeHtml(num(param.data[1], 1))} kWh`
              : ''),
          },
          series,
        }, true);
        requestAnimationFrame(() => chart.resize());
      },

      drawHeatTable() {
        const table = this.$refs.heatTable;
        if (!table || !this.heat) return;
        const o = this.heatOpts();
        const days = this.heat.days;
        const info = this.heatPeriodInfo();
        const shown = info.periods.slice(0, 3);
        if (!shown.length) {
          table.innerHTML = `<tbody><tr><td>${escapeHtml(t('Noch keine Heiztage.'))}</td></tr></tbody>`;
          return;
        }
        const tag = this.heatTag();
        const cells = (st) => (st && st.n
          ? `<td class="wt-sep">${escapeHtml(num(st.gt, 0))}</td><td>${escapeHtml(num(st.kwh, 0))}</td><td><b>${st.q == null ? '–' : escapeHtml(num(st.q, 2))}</b></td>`
          : '<td class="wt-na wt-sep">–</td><td class="wt-na">–</td><td class="wt-na">–</td>');
        const change = (a, b) => (a && b && a.q && b.q
          ? `<td class="wt-sep wt-dl">${a.q / b.q - 1 >= 0 ? '+' : '−'}${escapeHtml(num(Math.abs(a.q / b.q - 1) * 100, 0))} %</td>`
          : '<td class="wt-na wt-sep">–</td>');
        // Spalte Δ: die zwei letzten abgeschlossenen Heizperioden; in der Zeile „bis gestern“ die laufende gegen die letzte.
        const [newer, older] = [info.complete[0], info.complete[1]];
        let html = '<thead><tr class="wt-g"><th></th>'
          + shown.map((p) => `<th colspan="3" class="wt-sep">${escapeHtml(WetterHeating.label(p))}${info.running && p === info.current ? ' · ' + escapeHtml(t('läuft')) : ''}</th>`).join('')
          + '<th class="wt-sep">Δ</th></tr><tr><th></th>'
          + shown.map(() => `<th class="wt-sep"><span class="wt-sb">${escapeHtml(tag)}</span></th><th><span class="wt-sb">kWh</span></th><th><span class="wt-sb">kWh/Gt</span></th>`).join('')
          + `<th class="wt-sep wt-sb">${newer != null && older != null ? escapeHtml(t('kWh/Gt {a} gegenüber {b}', {a: WetterHeating.label(newer), b: WetterHeating.label(older)})) : ''}</th></tr></thead><tbody>`;
        for (const month of WetterHeating.MONTHS) {
          const stats = shown.map((p) => WetterHeating.stat(days, o, ...WetterHeating.monthRange(p, month)));
          const byPeriod = (p) => stats[shown.indexOf(p)];
          html += `<tr><th scope="row">${escapeHtml(monthName(month, 'short'))}</th>${stats.map(cells).join('')}${change(byPeriod(newer), byPeriod(older))}</tr>`;
        }
        const totals = shown.map((p) => WetterHeating.stat(days, o, ...WetterHeating.periodRange(p)));
        const totalOf = (p) => totals[shown.indexOf(p)];
        html += `<tr class="wt-sum"><th scope="row">${escapeHtml(t('Heizperiode'))}</th>${totals.map(cells).join('')}${change(totalOf(newer), totalOf(older))}</tr>`;
        if (info.running) {
          const cutOf = (p) => WetterHeating.cutRange(p, this.heat.today);
          const cuts = shown.map((p) => WetterHeating.stat(days, o, cutOf(p).from, cutOf(p).to));
          const label = cutOf(info.current).label;
          const first = info.current === shown[0] ? cuts[0] : null;
          const previous = info.complete[0] != null && shown.includes(info.complete[0]) ? cuts[shown.indexOf(info.complete[0])] : null;
          if (label) {
            html += `<tr class="wt-cut"><th scope="row">${escapeHtml(t('1.10.–{day}.{month}.', {day: label.day, month: label.month}))}</th>${cuts.map(cells).join('')}${change(first, previous)}</tr>`;
          }
        }
        table.innerHTML = html + '</tbody>';
      },

      heatShareTotals() {
        const period = Number(this.heatSeason);
        return WetterHeating.shares(this.heat.days, this.heat.counters, period);
      },

      heatShareText() {
        if (!this.heat) return '';
        const totals = this.heatShareTotals();
        const sums = this.heat.counters.map((_c, i) => totals.reduce((a, row) => a + row[i], 0));
        const all = sums.reduce((a, v, i) => a + (this.heatOn[i] ? v : 0), 0);
        if (!all) return t('Keine Werte in dieser Heizperiode.');
        return this.heat.counters
          .map((counter, i) => (this.heatOn[i] ? `${counter.name} ${num(sums[i], 0)} kWh (${num(sums[i] / all * 100, 0)} %)` : ''))
          .filter(Boolean).join(' · ');
      },

      drawHeatShare() {
        const element = this.$refs.heatShareChart;
        if (!element || !this.heat || this.heat.counters.length < 2 || !this.heatSeason) return;
        const colors = palette();
        const totals = this.heatShareTotals();
        const labels = WetterHeating.MONTHS.map((month) => monthName(month, 'short'));
        const series = this.heat.counters.map((counter, i) => ({
          name: counter.name, type: 'bar', stack: 'kwh', barMaxWidth: 48,
          data: totals.map((row) => (this.heatOn[i] ? Math.round(row[i] * 10) / 10 : 0)),
          itemStyle: {color: this.heatColor(i)},
        }));
        const chart = chartFor('heatShare', element);
        chart.setOption({
          animation: false, grid: gridFor(element), legend: legendBox(colors, this.heat.counters.map((c) => c.name)),
          xAxis: {type: 'category', data: labels, ...axisStyle(colors), splitLine: {show: false}},
          yAxis: {type: 'value', name: 'kWh', nameTextStyle: {color: colors.text}, ...axisStyle(colors)},
          tooltip: tooltipBox(colors, (params) => {
            const total = params.reduce((a, p) => a + (p.value || 0), 0);
            return `<b>${escapeHtml(params[0].axisValue)}</b>` + params
              .filter((p) => p.value)
              .map((p) => `<br>${escapeHtml(p.seriesName)}: ${escapeHtml(num(p.value, 1))} kWh (${escapeHtml(num(total ? p.value / total * 100 : 0, 0))} %)`).join('')
              + `<br>${escapeHtml(t('Summe'))}: ${escapeHtml(num(total, 1))} kWh`;
          }),
          series,
        }, true);
        requestAnimationFrame(() => chart.resize());
      },

      recordLabel(key) {
        return {
          warmest_day: t('Wärmster Tag (Ø)'), coldest_day: t('Kältester Tag (Ø)'), highest: t('Höchster Wert'),
          lowest: t('Tiefster Wert'), strongest_gust: t('Stärkste Böe'), wettest_day: t('Nassester Tag'),
          rainiest_day: t('Regenreichster Tag'),
        }[key] || key;
      },

      recordValue(record) {
        return escapeHtml(num(record.value, 1)) + (record.unit ? `<small>${escapeHtml(record.unit)}</small>` : '');
      },

      recordDate(record) {
        return new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'})
          .format(new Date(record.date + 'T12:00:00Z'));
      },

      recordLink(record) {
        return `entities/${encodeURIComponent(record.entity_id)}?range=day&offset=${record.offset_days}`;
      },

      recordsNote() {
        const l = this.longterm;
        if (!l || !l.records_since) return '';
        const since = new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'})
          .format(new Date(l.records_since + 'T12:00:00Z'));
        return t('Seit Archivbeginn ({date}, {days} Tage). Ein einzelner Messfehler kann einen Rekord setzen; über „Tag öffnen“ lässt sich der Tag prüfen.', {date: since, days: l.records_days});
      },

      async loadTiles() {
        try {
          const res = await fetch('wetterdashboard/tiles');
          if (res.ok) this.tiles = (await res.json()).tiles;
        } catch (e) {
          // Kacheln sind Beiwerk; die Tabelle meldet Verbindungsfehler selbst.
        }
        this.tilesLoaded = true;
      },

      async loadTable() {
        const seq = ++this._seq;
        this.error = '';
        try {
          const res = await fetch('wetterdashboard/table', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode, at: this.stichtag || null}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._seq) return;  // eine neuere Anfrage hat überholt
          this.todayIso = data.today;
          if (this.stichtag && !data.at) this.stichtag = '';  // heute oder später gilt als „jetzt“
          this.$refs.table.innerHTML = renderTable(data);
          // Scroll-Hinweis des App-Standards (.tbl-wrap.is-scrollable), nur wenn die Tabelle wirklich breiter ist.
          const wrap = this.$refs.tableWrap;
          wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1);
        } catch (e) {
          if (seq === this._seq) this.error = t('Die Tabelle konnte nicht geladen werden.');
        }
      },
    };
  };
})();
