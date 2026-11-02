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

  function columnTitle(col) {
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
      return [
        {label: t('Max'), field: 'max', when: 'point', delta: true},
        {label: t('Min'), field: 'min', when: 'point', delta: true},
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
    if (col.range === 'day') return metric.when === 'point' ? fmtDate(formats.hm, ts) : '';
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
    for (const col of cols) html += `<th class="wt-sep" colspan="${span}">${escapeHtml(columnTitle(col))}</th>`;
    html += '</tr><tr><th></th>';
    for (const col of cols) {
      let sub = rangeText(col.cur.start, col.cur.end);
      if (col.key === 'heute') sub = `${fmtDate(formats.dmy, col.cur.start)} · ${t('bis {time}', {time: fmtDate(formats.hm, data.now)})}`;
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
      localStorage.setItem(STORE_KEY, JSON.stringify(value));
    } catch (e) {
      // Privatmodus o. Ä.: die Auswahl gilt dann nur bis zum Neuladen.
    }
  }

  window.wetterDashboard = function wetterDashboard(precipModes) {
    return {
      tab: 'overview',
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
      matrix: null,
      matrixError: '',

      columnKeys: COLUMN_KEYS,
      columns: [...COLUMN_KEYS],
      vorjahr: true,
      mode: 'bis_jetzt',
      tiles: [],
      error: '',
      _seq: 0,

      init() {
        const saved = readStore();
        if (Array.isArray(saved.columns)) this.columns = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
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

      setMode(mode) {
        this.mode = mode;
        this.saveAndLoad();
      },

      saveAndLoad() {
        writeStore({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode});
        this.loadTable();
      },

      modeHint() {
        return this.mode === 'bis_jetzt'
          ? t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Abgeschlossene Zeiträume (Gestern, Letzter Monat) sind in beiden Fällen gleich.')
          : t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch mehr Spannweite und mehr Niederschlag.');
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
          return tile.today
            ? t('heute {min} / {max}', {min: num(tile.today.min, decimals), max: num(tile.today.max, decimals)})
            : '';
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
        // Ein Diagramm in einem eben erst sichtbaren Reiter hat noch keine Größe.
        this.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
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
          animation: false, grid: gridBox, legend: legendBox(colors, legend),
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
          animation: false, grid: {...gridBox, right: 8}, legend: legendBox(colors, legend),
          xAxis: {
            type: 'category', data: labels, boundaryGap: false, ...axisStyle(colors),
            axisLabel: {
              color: colors.text, fontSize: scaledFont(11),
              interval: (index) => MONTH_STARTS.includes(index),
              formatter: (_value, index) => monthName(slotDate(index).month, 'short'),
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
          animation: false, grid: gridBox,
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
              formatter: (_value, index) => monthName(leapSlotDate(index).month, 'short'),
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
      },

      async loadTable() {
        const seq = ++this._seq;
        this.error = '';
        try {
          const res = await fetch('wetterdashboard/table', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._seq) return;  // eine neuere Anfrage hat überholt
          this.$refs.table.innerHTML = renderTable(data);
        } catch (e) {
          if (seq === this._seq) this.error = t('Die Tabelle konnte nicht geladen werden.');
        }
      },
    };
  };
})();
