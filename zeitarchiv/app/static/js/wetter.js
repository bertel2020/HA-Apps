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
    weekday: new Intl.DateTimeFormat(LOCALE, ZT.withZone({weekday: 'long'})),
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
    if (col.key === 'jahr') return t('Jahr');
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
    // Die Einheit steht schon am Wert; nur „K“ bleibt, weil sie von „°C“ abweicht. Kürzer = schmalere Spalte.
    if (unit === 'K') text += ' K';
    if (withPercent && previous !== 0 && !zero) {
      const percent = diff / previous * 100;
      text += ' (' + (percent > 0 ? '+' : '−') + num(Math.abs(percent), 0) + ' %)';
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
    if (row.kind === 'sunshine') {
      // Stundengenau: Stunden, deren Mittel die Schwelle erreicht. Die Schwelle steht in der Beschriftung.
      return [
        {label: t('Sonnenstunden (ab {n} lx)', {n: num(data.sun_threshold_lx, 0)}), field: 'hours', decimals: 1, unit: 'h', delta: true, percent: true},
        {label: t('Sonnigster Tag'), field: 'top', decimals: 1, unit: 'h', when: 'day'},
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

  // Zusatz in Klammern („(ab 10 min)“) klein und gedämpft hinter dem Namen: hält die erste Spalte schmal.
  function metricLabelHtml(label) {
    const i = label.indexOf(' (');
    if (i < 0) return escapeHtml(label);
    return `${escapeHtml(label.slice(0, i))} <small class="wt-qual">${escapeHtml(label.slice(i + 1))}</small>`;
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
    // Tag von Max/Min in Zeitraum-Spalten als eigene kleine Zeile unter dem Wert; in Tagesspalten steht die Stunde
    // im Tooltip der Zelle (data-tooltip-fixed, fixed-tooltip.js).
    const when = whenText(metric, summary, col);
    const inline = col.range !== 'day' && when;
    const tip = when && !inline ? ` data-tooltip-fixed="${escapeHtml(when)}"` : '';
    return `<td class="${cls}"${tip}><span class="wt-v">${escapeHtml(num(summary[metric.field], decimals))}${unit ? '&nbsp;' + escapeHtml(unit) : ''}</span>`
      + (inline ? `<span class="wt-when">${escapeHtml(when)}</span>` : '') + (deltaHtml || '') + '</td>';
  }

  // Gruppen: „wind“ = Reiter „Wind & Sonne“, „klima“ = Zusatzsensoren (Schlüssel „temperature:N“) auf „Temperatur & Klima“,
  // alles andere steht in der Übersicht.
  const WIND_KEYS = ['wind', 'gust', 'radiation', 'illuminance'];
  const groupOf = (key) => (WIND_KEYS.includes(key) ? 'wind' : (key.includes(':') ? 'klima' : 'basis'));
  const inGroup = (key, group) => groupOf(key) === group;
  // Tabellen und Kachelreihen je Gruppe: x-ref-Namen wie in _wetter_view.html (tiles<Gruppe>, table[Wrap]<Gruppe>, Übersicht ohne Gruppenname).
  const GROUPS = ['basis', 'wind', 'klima'];
  const refName = (prefix, group) => prefix + (group === 'basis' && prefix !== 'tiles' ? '' : group.charAt(0).toUpperCase() + group.slice(1));

  function renderTable(data, group, showDiff) {
    const cols = data.columns;
    if (!cols.length) return `<tbody><tr><td>${escapeHtml(t('Keine Spalte ausgewählt.'))}</td></tr></tbody>`;
    const span = data.vorjahr ? 2 : 1;
    if (!data.rows.some((r) => inGroup(r.key, group))) {
      return `<tbody><tr><td class="wt-empty">${escapeHtml(t('Alle Blöcke dieser Gruppe sind in der Einrichtung ausgeblendet.'))}</td></tr></tbody>`;
    }
    // Zwei Zeilen: Titel mit Info-Tooltip (Zeitraum, „bis 15:47“), darunter Tag bzw. Jahr der Spalte und ihres Vorjahrs.
    // Mit Stichtag steht das Datum schon im Titel; die zweite Zeile nennt dann den Wochentag.
    const subText = (range, key) => {
      if (key === 'heute' || key === 'gestern') return fmtDate(formats.dmy, range.start);
      return String(ZT.parts(range.start).year);
    };
    let head = '<thead><tr><th></th>';
    let sub = '<tr class="wt-subhead"><th></th>';
    for (const col of cols) {
      let tip = rangeText(col.cur.start, col.cur.end);
      const dayCol = col.key === 'heute' || col.key === 'gestern';
      if (col.key === 'heute' && !data.at) tip = t('bis {time}', {time: fmtDate(formats.hm, data.now)});
      if (data.at && dayCol) tip = fmtDate(formats.weekday, col.cur.start);
      head += `<th class="wt-cur wt-sep" data-tooltip-fixed="${escapeHtml(tip)}">${escapeHtml(columnTitle(col, data))}<span class="wt-info" aria-hidden="true">ⓘ</span></th>`;
      const curSub = data.at && dayCol ? fmtDate(formats.weekday, col.cur.start) : subText(col.cur, col.key);
      sub += `<th class="wt-cur wt-sep">${escapeHtml(curSub)}</th>`;
      if (data.vorjahr) {
        head += `<th class="wt-vj" data-tooltip-fixed="${escapeHtml(rangeText(col.vj.start, col.vj.end))}">${escapeHtml(t('Vorjahr'))}</th>`;
        sub += `<th class="wt-vj">${escapeHtml(subText(col.vj, col.key))}</th>`;
      }
    }
    let html = head + '</tr>' + sub + '</tr>';
    html += '</thead><tbody>';

    for (const row of data.rows.filter((r) => inGroup(r.key, group))) {
      html += `<tr class="wt-grp"><th colspan="${1 + cols.length * span}">${escapeHtml(row.label)}</th></tr>`;
      for (const metric of metricsFor(row, data)) {
        html += `<tr><th scope="row">${metricLabelHtml(metric.label)}</th>`;
        for (const col of cols) {
          const cell = row.cells[col.key] || {};
          const decimals = metric.decimals != null ? metric.decimals : (row.decimals != null ? row.decimals : 1);
          const unit = metric.unit != null ? metric.unit : (row.unit === '°C' ? 'K' : row.unit);
          const deltaHtml = showDiff && data.vorjahr && metric.delta && cell.cur && cell.vj
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

  // Ein CSS-Farbausdruck (auch color-mix) als „rgb(…)“, weil ECharts nur klassische Farbwerte versteht.
  function resolveColor(expression) {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d');
      context.fillStyle = expression;
      context.fillRect(0, 0, 1, 1);
      const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
      return `rgb(${r}, ${g}, ${b})`;
    } catch (e) {
      return expression;
    }
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
      // Tagesklassen der Sonne, dieselben Töne wie die Pillen im Tagesbild.
      sun: resolveColor(`color-mix(in srgb, ${cssVar('--chart-7')} 70%, ${cssVar('--surface')})`),
      sunSoft: resolveColor(`color-mix(in srgb, ${cssVar('--chart-7')} 30%, ${cssVar('--surface')})`),
      overcast: resolveColor(`color-mix(in srgb, ${cssVar('--ink-faint')} 40%, ${cssVar('--surface')})`),
    };
  }

  // Ein Diagramm folgt der Breite seines Kastens auch ohne Fenster-Ereignis (Reiter eben erst sichtbar, Scrollleiste erscheint,
  // Karte ändert ihre Breite): sonst blieb es bei der Breite des Zeichnens stehen und ließ rechts Leerraum.
  const observers = {};

  function chartFor(key, element) {
    if (charts[key] && charts[key].getDom() !== element) {
      charts[key].dispose();
      delete charts[key];
      if (observers[key]) observers[key].disconnect();
    }
    if (!charts[key]) {
      charts[key] = echarts.init(element);
      if (typeof ResizeObserver === 'function') {
        observers[key] = new ResizeObserver(() => { if (charts[key] && element.clientWidth) charts[key].resize(); });
        observers[key].observe(element);
      }
    }
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

  // Zuletzt geladene Tabellendaten (außerhalb des Alpine-Objekts), damit das Umschalten der Differenz nicht neu lädt.
  let lastTable = null;

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

  // Symbole der Wetterlage: feste Zeichenfolgen ohne Nutzdaten, daher als HTML einsetzbar.
  const ICON_ATTR = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
  const LAGE_ICONS = {
    sun: `<svg viewBox="0 0 48 48" ${ICON_ATTR}><circle cx="24" cy="24" r="8.5" fill="currentColor" fill-opacity=".18"/><path d="M24 5v6M24 37v6M5 24h6M37 24h6M10.6 10.6l4.2 4.2M33.2 33.2l4.2 4.2M37.4 10.6l-4.2 4.2M14.8 33.2l-4.2 4.2"/></svg>`,
    part: `<svg viewBox="0 0 48 48" ${ICON_ATTR}><circle cx="19" cy="18" r="6.5" fill="currentColor" fill-opacity=".18"/><path d="M19 4.5v3M6.5 18h3M9.9 8.9l2.1 2.1M28.1 8.9L26 11M30.5 18h3"/><path d="M17 40h20a7 7 0 0 0 .8-13.9A9 9 0 0 0 20.6 28 6 6 0 0 0 17 40z" fill="var(--surface)" stroke="var(--ink-muted)"/></svg>`,
    cloud: `<svg viewBox="0 0 48 48" ${ICON_ATTR}><path d="M14 36h21a8 8 0 0 0 .9-15.9A11 11 0 0 0 15 22.5 7 7 0 0 0 14 36z" fill="currentColor" fill-opacity=".14"/></svg>`,
    rain: `<svg viewBox="0 0 48 48" ${ICON_ATTR}><path d="M14 30h21a8 8 0 0 0 .9-15.9A11 11 0 0 0 15 16.5 7 7 0 0 0 14 30z" fill="currentColor" fill-opacity=".14"/><path d="M17 35l-2 6M25 35l-2 6M33 35l-2 6"/></svg>`,
    moon: `<svg viewBox="0 0 48 48" ${ICON_ATTR}><path d="M38 28.5A15 15 0 0 1 19.5 10 15 15 0 1 0 38 28.5z" fill="currentColor" fill-opacity=".16"/><path d="M33 8v5M30.5 10.5h5"/></svg>`,
    dusk: `<svg viewBox="0 0 48 48" ${ICON_ATTR}><path d="M12 34a12 12 0 0 1 24 0z" fill="currentColor" fill-opacity=".18"/><path d="M5 34h38M24 12v4M10 18l3 3M38 18l-3 3M14 40h20"/></svg>`,
    wind: `<svg viewBox="0 0 24 24" ${ICON_ATTR}><path d="M3 9h10a2.5 2.5 0 1 0-2.4-3.2M3 14h14a2.8 2.8 0 1 1-2.6 3.8M3 19h6"/></svg>`,
    drop: `<svg viewBox="0 0 24 24" ${ICON_ATTR}><path d="M12 3.5c3.6 4.2 6 7.2 6 10.2a6 6 0 0 1-12 0c0-3 2.4-6 6-10.2z" fill="currentColor" fill-opacity=".16"/></svg>`,
    temp: `<svg viewBox="0 0 24 24" ${ICON_ATTR}><path d="M10 14.2V5a2 2 0 1 1 4 0v9.2a4 4 0 1 1-4 0z"/><path d="M12 9v7"/></svg>`,
    bright: `<svg viewBox="0 0 24 24" ${ICON_ATTR}><circle cx="12" cy="12" r="4" fill="currentColor" fill-opacity=".16"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M18.7 5.3l-1.8 1.8M7.1 16.9l-1.8 1.8"/></svg>`,
    clock: `<svg viewBox="0 0 24 24" ${ICON_ATTR}><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>`,
  };

  window.wetterDashboard = function wetterDashboard(precipModes) {
    return {
      tab: 'overview',
      precipModes: precipModes || [],
      precipMode: (precipModes && precipModes[0]) || 'mm',
      precip: null,
      precipError: '',
      tempCmp: null,
      tempDays: 30,
      tempError: '',
      monthly: null,
      monthlyMetric: 'temperature',
      band: true,
      longterm: null,
      longError: '',
      kenntageRange: 'all',
      hasConditions: false,
      conditions: null,
      _conditionsSeq: 0,
      hasSunhours: false,
      sunhours: null,
      sunhoursError: '',
      hasPv: false,
      pv: null,
      pvError: '',
      _sunhoursSeq: 0,
      hasDayimage: false,
      dayimage: null,
      dayimageError: '',
      hasDaylight: false,
      daylight: null,
      daylightError: '',
      _daylightSeq: 0,
      matrixMetric: 'avg',
      heat: null,
      heatOn: [],
      heatRoom: '20',
      heatLimit: '15',
      heatSeason: '',
      heatBySun: false,
      heatError: '',
      matrix: null,
      tempYears: null,
      matrixError: '',

      columnKeys: COLUMN_KEYS,
      columns: [...COLUMN_KEYS],
      vorjahr: true,
      diff: true,
      mode: 'bis_jetzt',
      tiles: [],
      tilesLoaded: false,
      tileCols: {basis: 4, wind: 3, klima: 3},
      tilesStand: '',
      tilesAt: null,
      error: '',
      _seq: 0,
      _tilesSeq: 0,

      init() {
        const saved = readStore();
        if (Array.isArray(saved.columns)) this.columns = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (typeof saved.diff === 'boolean') this.diff = saved.diff;
        if (saved.kenntageRange === '5' || saved.kenntageRange === 'all') this.kenntageRange = saved.kenntageRange;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        // Ausgeblendete Karten fehlen im Dokument: der erste vorhandene Reiter ist der Start, Diagramme ohne Karte laden nicht.
        const firstTab = this.$root.querySelector('[role="tab"]');
        if (firstTab) this.tab = firstTab.id.replace('wt-tab-', '');
        if (this.tab !== 'overview') this.$nextTick(() => this.showTab(this.tab));
        this.loadTiles();
        this.loadTable();
        if (this.$root.querySelector('[x-ref="monthlyChart"]')) this.loadMonthly();
        this.hasConditions = !!this.$root.querySelector('[data-wt-conditions]');
        if (this.hasConditions) this.startConditions();
        // Die Karte „Tageslicht“ gibt es nur mit zugeordneter Rolle; $refs sind in init() noch nicht angelegt.
        this.hasSunhours = !!this.$root.querySelector('[x-ref="sunChart"]');
        this.hasDayimage = !!this.$root.querySelector('[data-wt-dayimage]');
        if (this.hasDayimage) this.loadDayimage();
        this.hasDaylight = !!this.$root.querySelector('[x-ref="daylightChart"]');
        this.hasPv = !!this.$root.querySelector('[x-ref="pvChart"]');
        const observer = new ResizeObserver(() => this.layoutTiles());
        for (const group of GROUPS) {
          const ref = this.$refs[refName('tiles', group)];
          if (ref) observer.observe(ref);
        }
        // Stichtag-Leiste des App-Standards: bei jeder Änderung die Tabelle neu laden.
        window.addEventListener('dashboard-stichtag', () => {
          this.loadTiles();
          this.loadTable();
          // Die Karte liegt im Reiter „Wind & Sonne“: dort sofort neu laden, sonst beim nächsten Öffnen.
          if (this.hasDaylight) { this.daylight = null; if (this.tab === 'wind') this.loadDaylight(); }
          if (this.hasSunhours) { this.sunhours = null; if (this.tab === 'wind') this.loadSunhours(); }
          if (this.hasConditions) { this.conditions = null; this.loadConditions(); }
        });
      },

      columnName,

      toggleColumn(key) {
        this.columns = this.columns.includes(key)
          ? this.columns.filter((k) => k !== key)
          : COLUMN_KEYS.filter((k) => k === key || this.columns.includes(k));
        this.saveAndLoad();
      },

      // Gewählter Stichtag (JJJJ-MM-TT) oder null für heute — vom Store der Stichtag-Leiste.
      stichtag() {
        return Alpine.store('dashStichtag').date;
      },

      // Vorjahresvergleich: 'off' oder der Modus ('bis_jetzt'/'komplett'); schließt das Popover.
      setYoy(choice) {
        if (choice === 'off') this.vorjahr = false;
        else {
          this.vorjahr = true;
          this.mode = choice;
        }
        document.querySelectorAll('.wt-yoy-popover').forEach((popover) => popover.classList.remove('open'));
        this.saveAndLoad();
      },

      yoyLabel() {
        if (!this.vorjahr) return t('Vorjahr: Aus ▾');
        return (this.mode === 'komplett' ? t('Vorjahr: komplett') : t('Vorjahr: bis jetzt')) + ' ▾';
      },

      setMode(mode) {
        this.mode = mode;
        this.saveAndLoad();
      },

      // Differenzzeile unter den Werten ein- oder ausblenden; die Daten bleiben, nur die Tabelle wird neu gezeichnet.
      toggleDiff() {
        this.diff = !this.diff;
        writeStore({diff: this.diff});
        if (lastTable) this.paintTables(lastTable);
      },

      paintTables(data) {
        for (const group of GROUPS) {
          const table = this.$refs[refName('table', group)];
          const wrap = this.$refs[refName('tableWrap', group)];
          if (!table) continue;
          table.innerHTML = renderTable(data, group, this.diff);
          // Scroll-Hinweis des App-Standards (.tbl-wrap.is-scrollable), nur wenn die Tabelle wirklich breiter ist.
          wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1);
        }
      },

      saveAndLoad() {
        writeStore({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode});
        this.loadTable();
      },

      modeHint() {
        const base = this.mode === 'bis_jetzt'
          ? t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Abgeschlossene Zeiträume (Gestern, Letzter Monat) sind in beiden Fällen gleich.')
          : t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch mehr Spannweite und mehr Niederschlag.');
        const stichtag = this.stichtag();
        if (!stichtag) return base;
        const date = fmtDate(formats.dmy, Date.parse(stichtag + 'T12:00:00Z') / 1000);
        return `${base} ${t('Kacheln und Tabelle zeigen die Zeiträume zum {date}.', {date})}`;
      },

      // Titel der Karte: der gewählte Tag, in der Vergangenheit „Gestern“ oder „Rückblick“.
      cardTitle() {
        const stichtag = this.stichtag();
        if (!stichtag) return t('Heute');
        // "Gestern" nach dem Kalender des Servers, nicht des Browsers: Der Stichtag kommt vom Server (Mittag als Anker,
        // damit eine Zeitumstellung das Datum nicht verschiebt).
        const today = ZT.parts(Date.now() / 1000);
        const yesterday = ZT.parts(ZT.toEpoch(today.year, today.month, today.day - 1, 12));
        const iso = `${yesterday.year}-${String(yesterday.month).padStart(2, '0')}-${String(yesterday.day).padStart(2, '0')}`;
        return stichtag === iso ? t('Gestern') : t('Rückblick');
      },

      // Zeile unter dem Titel: Stand (heute) bzw. Tagesende (früherer Tag) und was ▼ ▲ bedeuten.
      standText() {
        if (this.tilesAt) return t('Tagesende · Tiefstwert ▼ und Höchstwert ▲ des Tages');
        return t('Stand {time}', {time: this.tilesStand}) + ' · ' + t('Tiefstwert ▼ und Höchstwert ▲ von heute');
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
        if (tile.kind === 'sunshine') {
          return tile.today ? escapeHtml(num(tile.today.hours, 1)) + unit('h') : '–';
        }
        return tile.today ? escapeHtml(num(tile.today.minutes, 0)) + unit('min') : '–';
      },

      tileSub(tile) {
        const decimals = tile.decimals != null ? tile.decimals : 1;
        if (tile.kind === 'standard') {
          if (!tile.today) return '';
          // Strahlung fällt nachts auf 0: nur der Höchstwert.
          return tile.key === 'radiation'
            ? `▲ ${num(tile.today.max, decimals)}`
            : `▼ ${num(tile.today.min, decimals)}  ▲ ${num(tile.today.max, decimals)}`;
        }
        if (!tile.yesterday) return '';
        if (tile.kind === 'counter') {
          const values = {value: num(tile.yesterday.sum, 1), unit: tile.unit || ''};
          return this.tilesAt ? t('Vortag {value} {unit}', values) : t('gestern {value} {unit}', values);
        }
        if (tile.kind === 'sunshine') {
          const hours = {value: num(tile.yesterday.hours, 1)};
          return this.tilesAt ? t('Vortag {value} h', hours) : t('gestern {value} h', hours);
        }
        const minutes = {value: num(tile.yesterday.minutes, 0)};
        return this.tilesAt ? t('Vortag {value} min', minutes) : t('gestern {value} min', minutes);
      },

      // Was die Zeile unter dem Wert bedeutet (Tooltip der Kachel).
      tileTitle(tile) {
        if (this.tilesAt) {
          return tile.kind === 'standard'
            ? t('Dieser Tag: Tiefstwert ▼ und Höchstwert ▲. Ein Klick öffnet die Entität.')
            : t('Dieser Tag, darunter der Vortag. Ein Klick öffnet die Entität.');
        }
        return tile.kind === 'standard'
          ? t('Heute: Tiefstwert ▼ und Höchstwert ▲. Ein Klick öffnet die Entität.')
          : t('Heute bisher, darunter der gestrige Tag. Ein Klick öffnet die Entität.');
      },

      // Spaltenzahl so wählen, dass keine einzelnen Kacheln allein in der letzten Reihe stehen.
      layoutTiles() {
        for (const group of GROUPS) {
          const element = this.$refs[refName('tiles', group)];
          const count = this.tilesFor(group).length;
          if (element && count && element.clientWidth) this.tileCols[group] = WetterLayout.tileColumns(count, element.clientWidth);
        }
      },

      // Kennzahlen-Raster (Niederschlag, Rekorde): höchstens vier Spalten, ohne einzelne Kachel in der letzten Reihe
      // (sechs Rekorde: 3+3). Schmale Bildschirme übernimmt das Stylesheet.
      kpiCols(count) {
        return WetterLayout.tileColumns(count, 640);
      },

      tilesFor(group) {
        return this.tiles.filter((tile) => inGroup(tile.key, group));
      },

      // ---- Reiter und Diagramme (Phase 2) ----------------------------------------------------

      showTab(tab) {
        this.tab = tab;
        if (tab === 'precip' && !this.precip) this.loadPrecip();
        if (tab === 'klima') { if (!this.tempCmp) this.loadTemps(); else this.$nextTick(() => this.drawTemps()); }
        if (tab === 'long' && !this.longterm) this.loadLongterm();
        if (tab === 'long' && !this.matrix) this.loadMatrix();
        if (tab === 'long' && !this.tempYears) this.loadTempYears();
        if (tab === 'heat' && !this.heat) this.loadHeat();
        if (tab === 'wind' && this.hasSunhours) { if (!this.sunhours) this.loadSunhours(); else this.$nextTick(() => this.drawSunhours()); }
        if (tab === 'wind' && this.hasPv) { if (!this.pv) this.loadPv(); else this.$nextTick(() => this.drawPv()); }
        if (tab === 'wind' && this.hasDaylight) { if (!this.daylight) this.loadDaylight(); else this.$nextTick(() => this.drawDaylight()); }
        // Ein Diagramm in einem eben erst sichtbaren Reiter hat noch keine Größe.
        this.$nextTick(() => {
          this.layoutTiles();
          Object.values(charts).forEach((chart) => chart.resize());
        });
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
        if (m.metric === 'sun' && !used.length) return t('Summe der Sonnenstunden je Monat (Stunden über der Sonnen-Schwelle). Die Helligkeit gibt es erst seit Kurzem im Archiv, ein Durchschnitt der Vorjahre erscheint nach dem ersten abgeschlossenen Vorjahr.');
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
        const decimals = m.metric === 'temperature' ? 1 : (m.unit === 'min' || m.unit === 'h' ? 0 : 1);
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

      // ---- Wetterlage: Satz aus Himmel, Wind, Regen und Temperatur (immer der aktuelle Stand) ---------

      // Lädt neu, solange die Seite offen ist; nach dem Austausch der Seite (Einrichtung gespeichert) hört das alte Objekt auf.
      startConditions() {
        this.loadConditions();
        const timer = setInterval(() => {
          if (!this.$root.isConnected) clearInterval(timer);
          else if (!document.hidden) this.loadConditions();
        }, 5 * 60 * 1000);
        document.addEventListener('visibilitychange', () => {
          if (!document.hidden && this.$root.isConnected) this.loadConditions();
        });
      },

      async loadConditions() {
        const stichtag = this.stichtag();
        const seq = ++this._conditionsSeq;
        try {
          const data = await this.fetchJson(`wetterdashboard/wetterlage${stichtag ? `?date=${encodeURIComponent(stichtag)}` : ''}`);
          if (seq === this._conditionsSeq) this.conditions = data.sky || data.wind || data.rain ? data : null;
        } catch (e) {
          // Die Zeile ist Beiwerk: ohne Antwort bleibt der letzte Stand stehen.
        }
      },

      skyName(kind) {
        return {
          night: t('Nacht'), dusk: t('Dämmerung'), day: t('Tag'), overcast: t('trüb'), cloudy: t('bewölkt'),
          fair: t('heiter'), sunny: t('sonnig'),
        }[kind] || kind;
      },

      beaufortName(stage) {
        return [
          t('Windstille'), t('leiser Zug'), t('leichte Brise'), t('schwache Brise'), t('mäßige Brise'), t('frische Brise'),
          t('starker Wind'), t('steifer Wind'), t('stürmischer Wind'), t('Sturm'), t('schwerer Sturm'),
          t('orkanartiger Sturm'), t('Orkan'),
        ][stage] || '';
      },

      conditionsRain() {
        const rain = this.conditions && this.conditions.rain;
        if (!rain) return null;
        const mm = rain.mm_today ? ` · ${num(rain.mm_today, 1)} ${rain.mm_unit || 'mm'}` : '';
        if (rain.raining) {
          if (rain.minutes == null) return {text: t('Regen') + mm, sub: t('seit mehr als {n} h', {n: 48})};
          const minutes = Math.round(rain.minutes);
          const since = minutes < 120 ? t('Regen seit {n} min', {n: minutes}) : t('Regen seit {h} h {m} min', {h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, '0')});
          return {text: since + mm, sub: t('Beginn {time}', {time: fmtDate(formats.hm, rain.since)})};
        }
        let sub = '';
        if (rain.raining === false) {
          if (rain.last == null) sub = t('in den letzten {n} h kein Regen', {n: 48});
          else {
            const sameDay = fmtDate(formats.dm, rain.last) === fmtDate(formats.dm, this.conditions.now);
            sub = t('zuletzt Regen um {time}', {time: (sameDay ? '' : fmtDate(formats.dm, rain.last) + ' ') + fmtDate(formats.hm, rain.last)});
          }
        }
        if (rain.raining == null && rain.mm_today) return {text: t('heute {value} {unit}', {value: num(rain.mm_today, 1), unit: rain.mm_unit || 'mm'}), sub};
        return {text: t('trocken'), sub};
      },

      conditionsTitle() {
        const c = this.conditions;
        if (c && c.mode === 'day') return t('Wetter am {date}', {date: utcDayMonth.format(new Date(`${c.date}T12:00:00Z`))});
        return t('Wetterlage');
      },

      // Rückblick auf einen Tag: Beschreibung der Teile (Himmel, Wind, Regen, Temperatur) als Zeile und als Kacheln.
      conditionsDayParts() {
        const c = this.conditions;
        const parts = {};
        if (c.sky && c.sky.class) {
          parts.sky = c.sky.sun_hours != null
            ? {value: t('{h} Sonnenstunden', {h: num(c.sky.sun_hours, 1)}), sub: t('von {n} hellen Stunden', {n: c.sky.day_hours})}
            : {value: t('{n} helle Stunden', {n: c.sky.day_hours}), sub: ''};
        }
        if (c.wind) {
          parts.wind = {
            value: this.beaufortName(c.wind.bft),
            sub: t('Maximum: Stärke {bft} · {kmh} km/h', {bft: c.wind.bft, kmh: num(c.wind.kmh, 0)}),
          };
        }
        if (c.rain) {
          const dry = !c.rain.mm && !c.rain.minutes;
          const mm = c.rain.mm ? `${num(c.rain.mm, 1)} ${c.rain.mm_unit || 'mm'}` : '';
          const minutes = c.rain.minutes ? t('{n} min', {n: num(c.rain.minutes, 0)}) : '';
          parts.rain = {value: dry ? t('trocken') : (mm || minutes), sub: dry ? '' : (mm && minutes ? minutes : '')};
        }
        if (c.temp) parts.temp = {value: this.conditionsTempText(), sub: ''};
        return parts;
      },

      // Große Zeile: die allgemeine Wetterlage in einem Wort (Regen vor Himmel, ohne Himmelsdaten „trocken“), bei
      // starkem Wind ergänzt; die Einzelheiten stehen in den Kacheln darunter und werden nicht wiederholt.
      conditionsLine() {
        const c = this.conditions;
        if (!c) return '';
        const day = c.mode === 'day';
        const rainy = day ? !!(c.rain && (c.rain.mm || c.rain.minutes)) : !!(c.rain && c.rain.raining);
        let main = '';
        if (rainy) main = day ? t('regnerisch') : t('Regen');
        else if (c.sky && c.sky.class) main = this.skyName(c.sky.class);
        else if (c.rain && (day || c.rain.raining === false)) main = t('trocken');
        main = main.charAt(0).toUpperCase() + main.slice(1);
        const windy = c.wind && c.wind.bft >= 8 ? t('stürmisch') : c.wind && c.wind.bft >= 6 ? t('windig') : '';
        const line = [main, windy].filter(Boolean).join(' · ');
        if (line) return line;
        const temp = this.conditionsTempText();
        return temp || '';
      },

      conditionsTempText() {
        const c = this.conditions;
        if (!c || !c.temp) return '';
        const dec = c.temp.decimals == null ? 1 : c.temp.decimals;
        if (c.mode === 'day') {
          return t('{min} bis {max} {unit}', {min: num(c.temp.min, dec), max: num(c.temp.max, dec), unit: c.temp.unit}).trim();
        }
        return `${num(c.temp.value, dec)} ${c.temp.unit}`.trim();
      },

      iconSvg: (name) => LAGE_ICONS[name] || '',

      // Symbol und Farbe über dem Wort: Regen vor Himmelsklasse, ohne Himmelsdaten das Regen- oder kein Symbol.
      conditionsHero() {
        const c = this.conditions;
        if (!c) return {icon: '', color: ''};
        const day = c.mode === 'day';
        const rainy = day ? !!(c.rain && (c.rain.mm || c.rain.minutes)) : !!(c.rain && c.rain.raining);
        if (rainy) return {icon: 'rain', color: 'var(--chart-3)'};
        const kind = c.sky && c.sky.class;
        const map = {
          night: ['moon', 'var(--chart-4)'], dusk: ['dusk', 'var(--chart-4)'], day: ['sun', 'var(--chart-7)'],
          sunny: ['sun', 'var(--chart-7)'], fair: ['part', 'var(--chart-7)'], cloudy: ['cloud', 'var(--ink-muted)'],
          overcast: ['cloud', 'var(--ink-muted)'],
        };
        const [icon, color] = map[kind] || ['', ''];
        return {icon, color};
      },

      // Ein bis zwei Sätze unter dem Wort, aus den Werten der Kacheln; fehlt ein Teil, fehlt sein Satz.
      conditionsSentence() {
        const c = this.conditions;
        if (!c) return '';
        if (c.mode === 'day') {
          const p = this.conditionsDayParts();
          return ['sky', 'temp', 'wind', 'rain'].filter((key) => p[key]).map((key) => (key === 'wind'
            ? t('Wind bis Stärke {bft}', {bft: c.wind.bft}) : p[key].value)).join(' · ');
        }
        const parts = [];
        const temp = c.temp ? this.conditionsTempText() : '';
        const wind = c.wind ? this.windPhrase(c.wind.bft) : '';
        const adj = c.temp && c.temp.unit === '°C' ? this.tempAdjective(c.temp.value) : '';
        if (temp && wind && adj) parts.push(t('{adj} bei {temp}, {wind}.', {adj, temp, wind}));
        else if (temp && adj) parts.push(t('{adj} bei {temp}.', {adj, temp}));
        else if (temp && wind) parts.push(t('{temp}, {wind}.', {temp, wind}));
        else if (temp) parts.push(`${temp}.`);
        else if (wind) parts.push(t('{wind}.', {wind}));
        const rain = c.rain;
        if (rain && rain.raining) {
          if (rain.minutes == null) parts.push(t('Es regnet seit mehr als {n} h.', {n: 48}));
          else {
            const minutes = Math.round(rain.minutes);
            parts.push(minutes < 120 ? t('Es regnet seit {n} min.', {n: minutes})
              : t('Es regnet seit {h} h {m} min.', {h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, '0')}));
          }
        } else if (rain && rain.raining === false) {
          parts.push(rain.last != null
            ? t('Aktuell trocken, zuletzt Regen um {time}.', {time: (fmtDate(formats.dm, rain.last) === fmtDate(formats.dm, c.now) ? '' : `${fmtDate(formats.dm, rain.last)} `) + fmtDate(formats.hm, rain.last)})
            : t('Aktuell trocken.'));
        }
        return parts.join(' ');
      },

      tempAdjective(value) {
        if (value == null) return '';
        if (value < 0) return t('Frostig');
        if (value < 6) return t('Kalt');
        if (value < 12) return t('Kühl');
        if (value < 18) return t('Mild');
        if (value < 25) return t('Warm');
        return t('Heiß');
      },

      windPhrase(bft) {
        if (bft <= 1) return t('kaum Wind');
        if (bft <= 3) return t('schwacher Wind');
        if (bft === 4) return t('mäßiger Wind');
        if (bft === 5) return t('frischer Wind');
        if (bft <= 7) return t('starker Wind');
        return t('stürmischer Wind');
      },

      // Kleine Angaben unter dem Satz: Tagesbeginn oder Nachtbeginn und Sonnenstunden bis jetzt.
      conditionsChips() {
        const c = this.conditions;
        if (!c || c.mode === 'day') return [];
        const chips = [];
        const light = c.light;
        if (light) {
          if (light.to != null && c.now >= light.to) chips.push({icon: 'clock', text: t('Nacht seit {time}', {time: fmtDate(formats.hm, light.to)})});
          else chips.push({icon: 'clock', text: t('Tag seit {time}', {time: fmtDate(formats.hm, light.from)})});
        }
        if (c.sky && c.sky.sun_hours) chips.push({icon: 'bright', text: t('{h} Sonnenstunden', {h: num(c.sky.sun_hours, 1)})});
        return chips;
      },

      conditionsTiles() {
        const c = this.conditions;
        if (!c) return [];
        if (c.mode === 'day') {
          const p = this.conditionsDayParts();
          const labels = {sky: t('Sonnenschein'), wind: t('Wind'), rain: t('Regen'), temp: t('Temperatur')};
          const icons = {sky: 'bright', wind: 'wind', rain: 'drop', temp: 'temp'};
          return ['sky', 'wind', 'rain', 'temp'].filter((key) => p[key]).map((key) => ({
            kind: key, icon: icons[key], label: labels[key], value: p[key].value, sub: p[key].sub, sub2: '',
          }));
        }
        const tiles = [];
        if (c.sky) {
          // Die Klasse steht in der Überschrift; die Kachel zeigt die Messwerte dahinter.
          const main = c.sky.value != null ? {n: c.sky.value, unit: c.sky.unit} : {n: c.sky.dawn, unit: 'lx'};
          const sub = c.sky.value != null && c.sky.dawn != null ? t('Dämmerung {n} lx', {n: num(c.sky.dawn, 0)}) : '';
          tiles.push({kind: 'sky', icon: 'bright', label: t('Helligkeit'), value: `${num(main.n, 0)} ${main.unit}`.trim(), sub, sub2: ''});
        }
        if (c.wind) {
          tiles.push({
            kind: 'wind', icon: 'wind', label: t('Wind'), value: this.beaufortName(c.wind.bft),
            sub: t('Stärke {bft} · {kmh} km/h', {bft: c.wind.bft, kmh: num(c.wind.kmh, 0)}),
            sub2: c.wind.gust_kmh ? t('Böen bis {kmh} km/h', {kmh: num(c.wind.gust_kmh, 0)}) : '',
          });
        }
        const rain = this.conditionsRain();
        if (rain) {
          const wet = c.rain && !c.rain.raining && c.rain.mm_today;
          tiles.push({
            kind: 'rain', icon: 'drop', label: t('Regen'), value: rain.text, sub: rain.sub,
            sub2: wet ? t('{value} {unit} heute', {value: num(c.rain.mm_today, 1), unit: c.rain.mm_unit || 'mm'}) : '',
          });
        }
        if (c.temp) {
          const dec = c.temp.decimals == null ? 1 : c.temp.decimals;
          const span = c.temp.min != null && c.temp.max != null
            ? t('heute {min} bis {max} {unit}', {min: num(c.temp.min, dec), max: num(c.temp.max, dec), unit: c.temp.unit}).trim() : '';
          tiles.push({kind: 'temp', icon: 'temp', label: t('Temperatur'), value: this.conditionsTempText(), sub: span, sub2: ''});
        }
        return tiles;
      },

      // ---- Tageslicht (Dämmerungssensor): Kurve mit Nacht-, Dämmerungs- und Tagband -----------------

      // ---- Sonnenstunden: Tagesverlauf der Süd-Helligkeit mit den Stunden über der Schwelle ----------

      async loadSunhours() {
        const stichtag = this.stichtag();
        const seq = ++this._sunhoursSeq;
        this.sunhoursError = '';
        try {
          const data = await this.fetchJson(`wetterdashboard/sonnenstunden${stichtag ? `?date=${encodeURIComponent(stichtag)}` : ''}`);
          if (seq !== this._sunhoursSeq) return;  // ein neuerer Tag hat überholt
          this.sunhours = data;
          this.$nextTick(() => this.drawSunhours());
        } catch (e) {
          if (seq === this._sunhoursSeq) this.sunhoursError = t('Die Sonnenstunden konnten nicht geladen werden.');
        }
      },

      sunhoursHead() {
        const data = this.sunhours;
        if (!data) return '';
        if (!data.hours.length) return escapeHtml(t('Für diesen Tag liegen keine Werte vor.'));
        let text = `<b>${escapeHtml(t('{h} Sonnenstunden', {h: num(data.sun_hours || 0, 1)}))}</b> ${escapeHtml(t('bei Schwelle {n} lx', {n: num(data.threshold, 0)}))}`;
        const peak = data.peak;
        if (peak && peak.value > 0) {
          text += ` · ${escapeHtml(t('Spitze um {time} Uhr: {value} lx', {time: fmtDate(formats.hm, peak.start), value: num(peak.value, 0)}))}`;
          if (peak.value >= data.saturation * 0.995) text += ` · ${escapeHtml(t('Süd mittags „ab {n} lx“ (Sensor sättigt)', {n: num(data.saturation, 0)}))}`;
        }
        return text;
      },

      drawSunhours() {
        const data = this.sunhours;
        const element = this.$refs.sunChart;
        if (!data || !element || !data.hours.length) return;
        const colors = palette();
        const byStart = new Map(data.hours.map((row) => [row[0], row]));
        const points = [];
        for (let stamp = data.start; stamp < Math.min(data.end, data.hours[data.hours.length - 1][0] + 3600); stamp += 3600) {
          const row = byStart.get(stamp);
          points.push([(stamp + 1800) * 1000, row ? row[1] : null]);
        }
        const peak = Math.max(data.threshold * 1.2, ...data.hours.map((row) => row[1]));
        const chart = chartFor('sunhours', element);
        chart.setOption({
          animation: false, grid: gridFor(element, {top: 16, right: 12}),
          xAxis: {
            type: 'value', min: data.start * 1000, max: data.end * 1000, interval: 3 * 3600 * 1000, ...axisStyle(colors),
            splitLine: {show: false}, axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (ms) => fmtDate(formats.hm, ms / 1000)},
          },
          yAxis: {
            type: 'value', min: 0, max: peak, name: 'klx', nameTextStyle: {color: colors.text}, ...axisStyle(colors),
            axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (value) => num(value / 1000, 0)},
          },
          tooltip: {
            trigger: 'axis', confine: true, backgroundColor: colors.surface, borderColor: colors.grid,
            textStyle: {color: colors.ink, fontSize: scaledFont(12)},
            formatter: (params) => {
              const point = params[0].value;
              if (point[1] == null) return '';
              const stamp = point[0] / 1000 - 1800;
              const sunny = point[1] >= data.threshold;
              return `<b>${escapeHtml(fmtDate(formats.hm, stamp))}–${escapeHtml(fmtDate(formats.hm, stamp + 3600))}</b><br>${escapeHtml(num(point[1], 0))} lx · ${escapeHtml(sunny ? t('Sonne') : t('keine Sonne'))}`;
            },
          },
          series: [{
            type: 'line', data: points, symbol: 'circle', symbolSize: 5, connectNulls: false,
            lineStyle: {color: colors.cur, width: 2.4}, itemStyle: {color: colors.cur},
            markArea: {
              silent: true,
              data: data.hours.filter((row) => row[1] >= data.threshold).map((row) => [
                {xAxis: row[0] * 1000, itemStyle: {color: cssVar('--chart-7'), opacity: 0.22}}, {xAxis: (row[0] + 3600) * 1000},
              ]),
            },
            markLine: {
              silent: true, symbol: 'none',
              lineStyle: {color: colors.faint, type: 'dashed', width: 1},
              label: {color: colors.text, fontSize: scaledFont(11), position: 'insideEndTop', formatter: t('Schwelle {n} lx', {n: num(data.threshold, 0)})},
              data: [{yAxis: data.threshold}],
            },
          }],
        }, true);
        requestAnimationFrame(() => chart.resize());
      },

      // ---- PV und Helligkeit (experimentell): Tagesertrag gegen die Erwartung aus der Süd-Helligkeit -------

      async loadPv() {
        this.pvError = '';
        try {
          this.pv = await this.fetchJson('wetterdashboard/pv');
          this.$nextTick(() => this.drawPv());
        } catch (e) {
          this.pvError = t('Der Vergleich von PV-Leistung und Helligkeit konnte nicht geladen werden.');
        }
      },

      pvHead() {
        const data = this.pv;
        if (!data || !data.days.length) return '';
        const last = data.days[data.days.length - 1];
        const day = new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', timeZone: 'UTC'}).format(new Date(`${last.date}T12:00:00Z`));
        const values = {day, percent: num(Math.abs(last.deviation), 0), actual: num(last.actual, 1), expected: num(last.expected, 1)};
        let text;
        if (!last.flagged) text = t('{day}: {actual} kWh, im Rahmen der Erwartung ({expected} kWh)', values);
        else if (last.deviation < 0) text = t('{day}: {actual} kWh, {percent} % unter der Erwartung ({expected} kWh)', values);
        else text = t('{day}: {actual} kWh, {percent} % über der Erwartung ({expected} kWh)', values);
        const flagged = data.days.filter((row) => row.flagged).length;
        return flagged ? `${text} · ${t('{n} von {total} Tagen weichen um mehr als {limit} % ab', {n: flagged, total: data.days.length, limit: data.limit})}` : text;
      },

      pvHourNote() {
        const hours = this.pv ? this.pv.hours : [];
        if (hours.length < 2) return '';
        const low = hours.reduce((a, b) => (b.ratio < a.ratio ? b : a));
        const high = hours.reduce((a, b) => (b.ratio > a.ratio ? b : a));
        return t('Um {low} Uhr liefert die Anlage {lowPct} % der Erwartung, um {high} Uhr {highPct} %.', {
          low: low.hour, lowPct: num(low.ratio * 100, 0), high: high.hour, highPct: num(high.ratio * 100, 0),
        });
      },

      drawPv() {
        const data = this.pv;
        if (!data) return;
        const colors = palette();
        const fmt = new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', timeZone: 'UTC'});
        const element = this.$refs.pvChart;
        if (element) {
          const labels = data.days.map((row) => fmt.format(new Date(`${row.date}T12:00:00Z`)));
          const chart = chartFor('pv', element);
          chart.setOption({
            animation: false, grid: gridFor(element, {top: 28, right: 12}), legend: legendBox(colors, [t('Ertrag'), t('Erwartung')]),
            xAxis: {type: 'category', data: labels, ...axisStyle(colors), splitLine: {show: false}},
            yAxis: {type: 'value', min: 0, ...axisStyle(colors), axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (value) => `${value} kWh`}},
            tooltip: tooltipBox(colors, (params) => {
              const row = data.days[params[0].dataIndex];
              return `<b>${escapeHtml(params[0].axisValue)}</b><br>${escapeHtml(t('Ertrag'))}: ${escapeHtml(num(row.actual, 1))} kWh`
                + `<br>${escapeHtml(t('Erwartung'))}: ${escapeHtml(num(row.expected, 1))} kWh<br>${escapeHtml(row.deviation >= 0 ? '+' : '−')}${escapeHtml(num(Math.abs(row.deviation), 0))} %`;
            }),
            series: [
              {
                name: t('Ertrag'), type: 'bar', barMaxWidth: 28,
                data: data.days.map((row) => ({value: row.actual, itemStyle: {color: row.flagged ? cssVar('--chart-4') : colors.cur}})),
              },
              {
                name: t('Erwartung'), type: 'line', symbol: 'diamond', symbolSize: 9, lineStyle: {width: 0},
                itemStyle: {color: colors.ink}, data: data.days.map((row) => row.expected), z: 5,
              },
            ],
          }, true);
          requestAnimationFrame(() => chart.resize());
        }
        const hourElement = this.$refs.pvHourChart;
        if (hourElement && data.hours.length) {
          const chart = chartFor('pvHour', hourElement);
          chart.setOption({
            animation: false, grid: gridFor(hourElement, {top: 12, right: 12}),
            xAxis: {type: 'category', data: data.hours.map((row) => `${row.hour}`), ...axisStyle(colors), splitLine: {show: false}},
            yAxis: {type: 'value', min: 0, ...axisStyle(colors), axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (value) => `${value} %`}},
            tooltip: tooltipBox(colors, (params) => {
              const row = data.hours[params[0].dataIndex];
              return `<b>${escapeHtml(t('{hour} Uhr', {hour: row.hour}))}</b><br>${escapeHtml(num(row.ratio * 100, 0))} % ${escapeHtml(t('der Erwartung'))} (${escapeHtml(t('{n} Tage', {n: row.days}))})`;
            }),
            series: [{
              type: 'bar', barMaxWidth: 24, itemStyle: {color: colors.cur},
              data: data.hours.map((row) => Math.round(row.ratio * 100)),
              markLine: {silent: true, symbol: 'none', lineStyle: {color: colors.faint, type: 'dashed'}, label: {show: false}, data: [{yAxis: 100}]},
            }],
          }, true);
          requestAnimationFrame(() => chart.resize());
        }
      },

      // ---- Tagesbild: ein Streifen mit der Klasse je Tag --------------------------------------------

      async loadDayimage() {
        this.dayimageError = '';
        try {
          this.dayimage = await this.fetchJson('wetterdashboard/tagesbild');
        } catch (e) {
          this.dayimage = null;
          this.dayimageError = t('Das Tagesbild konnte nicht geladen werden.');
        }
      },

      dayimageKindName(kind) {
        return {sunny: t('sonnig'), mixed: t('wechselhaft'), overcast: t('trüb')}[kind] || '';
      },

      dayimageCells() {
        const data = this.dayimage;
        if (!data) return [];
        const picked = this.stichtag();
        return data.days.map((day) => {
          const label = utcDayMonth.format(new Date(`${day.date}T12:00:00Z`));
          const has = !!day.class;
          const sub = has
            ? t('{sun} von {bright} h Sonne', {sun: num(day.sun_hours, 1), bright: num(day.bright_hours, 0)})
            : t('keine Klasse');
          const title = has
            ? t('{day}: {kind} · {sun} h Sonne von {bright} h hell', {
              day: day.today ? t('{day} (bisher)', {day: label}) : label, kind: this.dayimageKindName(day.class),
              sun: num(day.sun_hours, 1), bright: num(day.bright_hours, 1),
            })
            : t('{day}: keine Klasse (zu wenig helle Stunden oder keine Werte)', {day: label});
          return {
            ...day, picked: picked === day.date, title, sub, label: day.today ? t('{day} (bisher)', {day: label}) : label,
            kindName: has ? this.dayimageKindName(day.class) : '–', percent: has ? Math.round(day.share * 100) : 0,
          };
        });
      },

      dayimageHead() {
        const data = this.dayimage;
        if (!data) return '';
        const names = {sunny: t('sonnig'), mixed: t('wechselhaft'), overcast: t('trüb')};
        const parts = ['sunny', 'mixed', 'overcast'].filter((kind) => data.counts[kind]).map((kind) => `${data.counts[kind]} ${names[kind]}`);
        return parts.length ? t('Die letzten {n} Tage: {parts}', {n: data.days.length, parts: parts.join(' · ')}) : '';
      },

      pickDay(cell) {
        Alpine.store('dashStichtag').set(cell.today ? null : cell.date);
      },

      async loadDaylight() {
        const stichtag = this.stichtag();
        const seq = ++this._daylightSeq;
        this.daylightError = '';
        try {
          const data = await this.fetchJson(`wetterdashboard/tageslicht${stichtag ? `?date=${encodeURIComponent(stichtag)}` : ''}`);
          if (seq !== this._daylightSeq) return;  // ein neuerer Tag hat überholt
          this.daylight = data;
          this.$nextTick(() => this.drawDaylight());
        } catch (e) {
          if (seq === this._daylightSeq) this.daylightError = t('Das Tageslicht konnte nicht geladen werden.');
        }
      },

      daylightClock(stamp) {
        return stamp == null ? '–' : fmtDate(formats.hm, stamp);
      },

      daylightDuration(seconds, approx) {
        if (seconds == null) return '–';
        const minutes = Math.round(seconds / 60);
        const text = t('{h} h {m} min', {h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, '0')});
        return (approx ? '≈ ' : '') + text;
      },

      daylightMinutes(seconds, approx) {
        return seconds == null ? '–' : (approx ? '≈ ' : '') + t('{n} min', {n: num(seconds / 60, 0)});
      },

      daylightSpan(day) {
        if (day.from == null) return '–';
        if (day.ongoing) return t('ab {time}', {time: this.daylightClock(day.from)});
        return day.to == null ? this.daylightClock(day.from) : `${this.daylightClock(day.from)}–${this.daylightClock(day.to)}`;
      },

      daylightHead() {
        const day = this.daylight && this.daylight.day;
        if (!day) return '';
        if (!day.resolution) return t('Für diesen Tag liegen keine Werte vor.');
        if (this.daylightStartedBright()) {
          const start = this.daylightClock(day.points[0][0]);
          return day.to == null
            ? t('Hell seit Beginn der Aufzeichnung um {start} Uhr; der Tagesbeginn davor fehlt.', {start})
            : t('Hell bis {to} Uhr; die Aufzeichnung setzt erst um {start} Uhr ein, der Tagesbeginn fehlt.', {to: this.daylightClock(day.to), start});
        }
        if (day.from == null) return t('Der Sensor erreichte an diesem Tag {n} lx nicht.', {n: num(this.daylight.day_lx, 0)});
        const values = {span: this.daylightSpan(day), length: this.daylightDuration(day.length, day.approx)};
        return day.ongoing ? t('Hell {span}, bisher {length} Tageslicht', values) : t('Hell {span} Uhr, {length} Tageslicht', values);
      },

      // Die Aufzeichnung setzt schon über der Tag-Schwelle ein (Sensor erst seit Kurzem im Archiv): kein Anstieg
      // sichtbar, der Tagesbeginn ist unbekannt, nicht „nie erreicht“.
      daylightStartedBright() {
        const data = this.daylight;
        const day = data && data.day;
        return !!(day && day.resolution === 'fine' && day.from == null && day.points && day.points.length
          && day.points[0][1] >= data.day_lx);
      },

      daylightStats() {
        const data = this.daylight;
        if (!data || !data.day.resolution) return [];
        const day = data.day;
        const basis = day.approx ? t('Stundenraster, auf eine Stunde genau') : t('Minutenwerte');
        return [
          {
            kind: 'day', label: t('Tag ab / bis'),
            value: day.from == null ? (day.to != null && this.daylightStartedBright() ? `– / ${this.daylightClock(day.to)}` : '–') : (day.to == null ? this.daylightClock(day.from) : `${this.daylightClock(day.from)} / ${this.daylightClock(day.to)}`),
            sub: this.daylightStartedBright() ? t('Aufzeichnung ab {time}', {time: this.daylightClock(day.points[0][0])}) : basis,
          },
          {kind: 'length', label: t('Tageslänge'), value: this.daylightDuration(day.length, day.approx), sub: t('über {n} lx', {n: num(data.day_lx, 0)})},
          {
            kind: 'dawn', label: t('Dämmerung morgens'), value: this.daylightMinutes(day.morning, day.approx),
            sub: day.dawn != null ? t('ab {time} (über {n} lx)', {time: this.daylightClock(day.dawn), n: num(data.night_lx, 0)}) : '',
          },
          {
            kind: 'dusk', label: t('Dämmerung abends'), value: this.daylightMinutes(day.evening, day.approx),
            sub: day.dusk != null ? t('bis {time} (unter {n} lx)', {time: this.daylightClock(day.dusk), n: num(data.night_lx, 0)}) : '',
          },
        ];
      },

      daylightNote() {
        const day = this.daylight && this.daylight.day;
        if (!day || day.resolution !== 'hour') return '';
        return t('Für diesen Tag liegen nur Stundenwerte vor. Eine Stunde zählt als Tag, wenn ihr Mittel die Tag-Schwelle erreicht; die Zeiten sind auf eine Stunde genau.');
      },

      // Tage ohne Werte stehen nicht in der Tabelle, ein Satz nennt stattdessen, seit wann es Daten gibt.
      daylightHistoryNote() {
        const data = this.daylight;
        if (!data) return '';
        const empty = data.compare.filter((row) => !row.resolution).length;
        if (!empty) return '';
        const known = data.compare.filter((row) => row.resolution);
        const since = known.length ? utcDayMonth.format(new Date(`${known[known.length - 1].date}T12:00:00Z`)) : '';
        return since
          ? t('Für {n} frühere Tage liegen keine Werte vor: Der Sensor wird erst seit dem {date} archiviert.', {n: empty, date: since})
          : t('Für die letzten Tage liegen keine Werte vor.');
      },

      daylightRows() {
        const data = this.daylight;
        if (!data) return [];
        return data.compare.filter((row) => row.resolution).map((row) => ({
          date: row.date, current: row.date === data.day.date,
          day: utcDayMonth.format(new Date(`${row.date}T12:00:00Z`)),
          span: row.resolution ? this.daylightSpan(row) : '–',
          length: row.resolution ? this.daylightDuration(row.length, row.approx) : '–',
          morning: row.resolution ? this.daylightMinutes(row.morning, row.approx) : '–',
          evening: row.resolution ? this.daylightMinutes(row.evening, row.approx) : '–',
        }));
      },

      drawDaylight() {
        const data = this.daylight;
        const element = this.$refs.daylightChart;
        if (!data || !element) return;
        const day = data.day;
        const colors = palette();
        const kindName = (value) => (value >= data.day_lx ? t('Tag') : value >= data.night_lx ? t('Dämmerung') : t('Nacht'));
        const bandColor = {
          night: {color: colors.ink, opacity: 0.14},
          dusk: {color: cssVar('--chart-3'), opacity: 0.2},
          day: {color: cssVar('--chart-7'), opacity: 0.22},
        };
        const peak = Math.max(1000, ...day.points.map((point) => point[1]));
        const chart = chartFor('daylight', element);
        chart.setOption({
          animation: false, grid: gridFor(element, {top: 16, right: 12}),
          xAxis: {
            type: 'value', min: day.start * 1000, max: day.end * 1000, interval: 3 * 3600 * 1000, ...axisStyle(colors),
            splitLine: {show: false}, axisLabel: {color: colors.text, fontSize: scaledFont(11), formatter: (ms) => fmtDate(formats.hm, ms / 1000)},
          },
          yAxis: {type: 'value', min: 0, max: peak, name: 'lx', nameTextStyle: {color: colors.text}, ...axisStyle(colors)},
          tooltip: {
            trigger: 'axis', confine: true, backgroundColor: colors.surface, borderColor: colors.grid,
            textStyle: {color: colors.ink, fontSize: scaledFont(12)},
            formatter: (params) => {
              const point = params[0].value;
              let html = `<b>${escapeHtml(fmtDate(formats.hm, point[0] / 1000))}</b><br>${escapeHtml(num(point[1], 0))} lx · ${escapeHtml(kindName(point[1]))}`;
              const hour = (day.hours || []).find((row) => row[0] * 1000 + 1800000 === point[0]);
              if (hour) html += `<br>${escapeHtml(t('Max {n} lx', {n: num(hour[2], 0)}))}`;
              return html;
            },
          },
          series: [{
            type: 'line', data: day.points.map((point) => [point[0] * 1000, point[1]]), symbol: day.resolution === 'hour' ? 'circle' : 'none',
            symbolSize: 5, lineStyle: {color: colors.cur, width: 2.4}, itemStyle: {color: colors.cur},
            markArea: {
              silent: true,
              data: day.bands.map(([from, to, kind]) => [
                {xAxis: from * 1000, itemStyle: bandColor[kind]}, {xAxis: to * 1000},
              ]),
            },
            markLine: {
              silent: true, symbol: 'none',
              lineStyle: {color: colors.faint, type: 'dashed', width: 1},
              label: {color: colors.text, fontSize: scaledFont(11), position: 'insideEndTop'},
              data: [
                {yAxis: data.night_lx, label: {formatter: t('Nacht unter {n} lx', {n: num(data.night_lx, 0)})}},
                {yAxis: data.day_lx, label: {formatter: t('Tag ab {n} lx', {n: num(data.day_lx, 0)})}},
              ],
            },
          }],
        }, true);
        requestAnimationFrame(() => chart.resize());
      },

      // Temperaturen im Vergleich (Reiter „Temperatur & Klima“)
      async loadTemps() {
        this.tempError = '';
        try {
          this.tempCmp = await this.fetchJson(`wetterdashboard/temperaturen?days=${this.tempDays}`);
          this.$nextTick(() => this.drawTemps());
        } catch (e) {
          this.tempError = t('Die Temperaturen konnten nicht geladen werden.');
        }
      },

      setTempDays(days) {
        this.tempDays = days;
        this.loadTemps();
      },

      drawTemps() {
        const data = this.tempCmp;
        if (!data) return;
        if (data.series.length) this.drawComparison('temps', this.$refs.tempChart, data, data.series, '°C', 1);
        if (data.humidity && data.humidity.length) this.drawComparison('humid', this.$refs.humidChart, data, data.humidity, '%', 0);
      },

      // Linien je Sensor über die letzten Tage; die Einheit steht an der Achse (Temperatur und Feuchte nie gemischt).
      drawComparison(name, element, data, series, fallbackUnit, decimals) {
        if (!element) return;
        const colors = palette();
        const labels = data.dates.map((iso) => utcDayMonth.format(new Date(`${iso}T00:00:00Z`)));
        const unit = (series[0] && series[0].unit) || fallbackUnit;
        const names = series.map((row) => row.label);
        const chart = chartFor(name, element);
        chart.setOption({
          animation: false, grid: gridFor(element, {right: 8}), legend: legendBox(colors, names),
          xAxis: {type: 'category', data: labels, boundaryGap: false, ...axisStyle(colors), splitLine: {show: false}},
          yAxis: {type: 'value', scale: true, name: unit, nameTextStyle: {color: colors.text}, ...axisStyle(colors)},
          tooltip: tooltipBox(colors, (params) => {
            let html = `<b>${escapeHtml(labels[params[0].dataIndex])}</b>`;
            for (const param of params) {
              const value = param.value == null ? '–' : `${num(param.value, decimals)} ${unit}`;
              html += `<br>${param.marker}${escapeHtml(param.seriesName)}: ${escapeHtml(value)}`;
            }
            return html;
          }),
          series: series.map((row, index) => ({
            name: row.label, type: 'line', data: row.values, symbol: 'none', connectNulls: false,
            z: index === 0 ? 5 : 2,
            lineStyle: {color: this.heatColor(index), width: index === 0 ? 3 : 2},
            itemStyle: {color: this.heatColor(index)},
          })),
        }, true);
        chart.resize();
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
          this.$nextTick(() => this.drawKenntage());
        } catch (e) {
          this.longError = t('Die Langzeitwerte konnten nicht geladen werden.');
        }
      },

      // Jahre der Karte „Kenntage je Jahr“: alle oder die letzten fünf.
      kenntageYears() {
        const years = this.longterm ? Object.keys(this.longterm.kenntage).sort() : [];
        return this.kenntageRange === '5' ? years.slice(-5) : years;
      },

      setKenntageRange(range) {
        this.kenntageRange = range;
        writeStore({kenntageRange: range});
        this.drawKenntage();
      },

      kenntageNote() {
        const all = this.longterm ? this.longterm.kenntage : {};
        const days = Object.values(all).reduce((sum, year) => sum + year.days.reduce((a, b) => a + b, 0), 0);
        if (!days) return t('Noch keine Tage mit ausreichend Werten (mindestens 18 Stunden je Tag).');
        return t('Frosttag: Tiefstwert unter 0 °C, Eistag: Höchstwert unter 0 °C, Sommertag: Höchstwert ab 25 °C, heißer Tag: ab 30 °C. Gezählt sind {days} abgeschlossene Tage mit mindestens 18 Stunden Werten; der laufende Tag zählt nicht. Sonnentage und trübe Tage richten sich nach dem Sonnenanteil des Tages wie im Tagesbild.', {days});
      },

      // Fußnote zum laufenden Jahr (mit „*“ an der Jahreszahl markiert).
      kenntageFoot() {
        const years = this.kenntageYears();
        if (!this.longterm || !years.includes(String(this.longterm.current_year))) return '';
        const today = this.longterm.today.split('-');
        return t('* laufendes Jahr, bis {date}', {date: `${today[2]}.${today[1]}.`});
      },

      // Frost, Eis, Sommer, heiß: je ein kleines Diagramm mit eigener Achse (Eis- und heiße Tage gingen neben den Frosttagen unter).
      kenntageKinds() {
        const colors = palette();
        return [
          {key: 'frost', name: t('Frosttage'), color: colors.cold},
          {key: 'eis', name: t('Eistage'), color: cssVar('--chart-4')},
          {key: 'sommer', name: t('Sommertage'), color: cssVar('--chart-7')},
          {key: 'heiss', name: t('Heiße Tage'), color: cssVar('--chart-5')},
          {key: 'sunny', name: t('Sonnentage'), color: cssVar('--chart-7'), sun: true},
          {key: 'overcast', name: t('Trübe Tage'), color: cssVar('--ink-faint'), sun: true},
        ];
      },

      // Zähltabelle einer Art: Temperatur-Kenntage oder die der Sonne (dort fehlen Jahre vor dem Archivbeginn der Helligkeit).
      kenntageSource(key) {
        const sun = key === 'sunny' || key === 'overcast';
        const source = this.longterm ? (sun ? this.longterm.sun_kenntage : this.longterm.kenntage) : null;
        return source || {};
      },

      // Ø je Jahr über die abgeschlossenen Jahre der Auswahl (das laufende Jahr zählt nicht).
      kenntageAverage(key) {
        if (!this.longterm) return null;
        const current = String(this.longterm.current_year);
        const source = this.kenntageSource(key);
        const past = this.kenntageYears().filter((year) => year !== current && source[year]);
        if (!past.length) return null;
        const sum = (list) => list.reduce((a, b) => a + b, 0);
        return sum(past.map((year) => sum(source[year][key]))) / past.length;
      },

      kenntageAvg(key) {
        const average = this.kenntageAverage(key);
        return average == null ? '' : t('Ø {n} je Jahr', {n: num(average, 0)});
      },

      drawKenntage() {
        const years = this.kenntageYears();
        const colors = palette();
        const current = this.longterm ? String(this.longterm.current_year) : '';
        const sum = (list) => list.reduce((a, b) => a + b, 0);
        for (const kind of this.kenntageKinds()) {
          const data = this.kenntageSource(kind.key);
          const empty = {[kind.key]: Array(12).fill(0), days: Array(12).fill(0)};
          const element = this.$refs[`kenntageChart${kind.key.charAt(0).toUpperCase()}${kind.key.slice(1)}`];
          if (!element) continue;
          if (!years.length) {
            if (charts[`kenntage-${kind.key}`]) charts[`kenntage-${kind.key}`].clear();
            continue;
          }
          const labels = years.map((year) => (year === current ? `${year}*` : year));
          const average = this.kenntageAverage(kind.key);
          const chart = chartFor(`kenntage-${kind.key}`, element);
          // Schmale Diagramme (Telefon) kürzen die Jahreszahl auf die letzten Ziffern; wird bei jedem Zeichnen und Größenwechsel neu ausgewertet.
          const narrow = () => chart.getWidth() < 300;
          chart.setOption({
            animation: false, grid: {left: 4, right: 8, top: 14, bottom: 4, containLabel: true},
            xAxis: {
              type: 'category', data: labels, ...axisStyle(colors), splitLine: {show: false},
              axisLabel: {color: colors.text, fontSize: scaledFont(10), rotate: 0, hideOverlap: true, formatter: (value) => (narrow() ? value.replace(/^\d\d/, '') : value)},
            },
            yAxis: {type: 'value', min: 0, max: (value) => Math.max(value.max, 3), minInterval: 1, ...axisStyle(colors), axisLine: {show: false}, splitNumber: 3, axisLabel: {color: colors.text, fontSize: scaledFont(10)}},
            // Je Balken: Summe des Jahres, darunter die Verteilung auf die Monate.
            tooltip: {...tooltipBox(colors, (param) => {
              const year = years[param.dataIndex];
              const counts = data[year] || empty;
              const months = counts[kind.key].map((count, i) => (count ? `${monthName(i + 1, 'short')} ${count}` : '')).filter(Boolean);
              const lines = [];
              for (let i = 0; i < months.length; i += 4) lines.push(escapeHtml(months.slice(i, i + 4).join(' · ')));
              return `<b>${escapeHtml(kind.name)} ${escapeHtml(year)}</b><br>${escapeHtml(t('{n} Tage', {n: sum(counts[kind.key])}))}`
                + (lines.length ? `<br>${lines.join('<br>')}` : '')
                + `<br>${escapeHtml(t('{days} Tage ausgewertet', {days: sum(counts.days)}))}`;
            }), trigger: 'item'},
            series: [{
              name: kind.name, type: 'bar', barMaxWidth: 28,
              itemStyle: {color: kind.color, borderRadius: [3, 3, 0, 0]},
              data: years.map((year) => ({value: sum((data[year] || empty)[kind.key]), itemStyle: year === current ? {opacity: 0.55} : {}})),
              markLine: average == null ? undefined : {
                silent: true, symbol: 'none', label: {show: false},
                lineStyle: {color: colors.text, type: 'dashed', width: 1}, data: [{yAxis: Math.round(average * 10) / 10}],
              },
            }],
          }, true);
          // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
          requestAnimationFrame(() => chart.resize());
        }
      },

      // Mitteltemperatur je Monat und Jahr (Tabelle, Farbe = Abweichung vom Mittel dieses Monats über die abgeschlossenen Jahre)
      async loadTempYears() {
        try {
          this.tempYears = await this.fetchJson('wetterdashboard/monatsvergleich?metric=temperature');
          this.$nextTick(() => this.drawTempYears());
        } catch (e) {
          this.tempYears = null;
        }
      },

      tempYearsNote() {
        return t('Oben die Mitteltemperatur, darunter die Abweichung vom Mittel dieses Monats über alle abgeschlossenen Jahre im Archiv (in Kelvin). Die letzte Zeile ist das Mittel.');
      },

      drawTempYears() {
        const data = this.tempYears;
        const table = this.$refs.tempYearsTable;
        if (!data || !table) return;
        const colors = palette();
        const current = String(data.current_year);
        const years = Object.keys(data.years).sort().slice(-10);
        const mean = (values) => values.reduce((a, b) => a + b, 0) / values.length;
        const present = (list) => list.filter((value) => value != null);
        // Jahres-Ø nur aus vorhandenen Monaten und erst ab sechs davon; als Vergleich zählen nur komplette, abgeschlossene Jahre.
        const yearMean = (year) => { const values = present(data.years[year]); return values.length >= 6 ? mean(values) : null; };
        const reference = (() => {
          const full = Object.keys(data.years).filter((y) => y < current && present(data.years[y]).length === 12);
          return full.length >= 2 ? mean(full.map(yearMean)) : null;
        })();
        const unit = data.unit || '°C';
        const sign = (value) => (Math.round(Math.abs(value) * 10) === 0 ? '±' : (value > 0 ? '+' : '−')) + num(Math.abs(value), 1);
        // Mittelwerte ohne „-0,0“: ein Wert, der auf null rundet, heißt 0,0.
        const plain = (value) => num(Math.round(Math.abs(value) * 10) === 0 ? 0 : value, 1);
        const cold = colors.cold, hot = colors.prev, base = cssVar('--surface-alt');
        const cell = (value, ref, title, strong) => {
          if (value == null) return `<td class="wt-tm-na">–</td>`;
          const tip = title ? ` data-tooltip-fixed="${escapeHtml(title)}"` : '';
          // Ohne Vergleichswert (weniger als zwei abgeschlossene Jahre für diesen Monat) bleibt die Zelle ungefärbt und einzeilig.
          if (ref == null) return `<td class="wt-tm-plain${strong ? ' wt-tm-year' : ''}"${tip}>${escapeHtml(plain(value))}</td>`;
          const diff = value - ref;
          const share = Math.round(Math.min(1, Math.abs(diff) / 3.2) * 80);
          const style = ` style="background:color-mix(in srgb, ${diff < 0 ? cold : hot} ${share}%, ${base})"`;
          return `<td${style}${tip}${strong ? ' class="wt-tm-year"' : ''}><span class="wt-tm-v">${escapeHtml(plain(value))}</span><span class="wt-tm-d">${escapeHtml(sign(diff))}</span></td>`;
        };
        let html = `<thead><tr><th></th>${Array.from({length: 12}, (_x, i) => `<th>${escapeHtml(monthName(i + 1, 'short'))}</th>`).join('')}`
          + `<th class="wt-tm-year">${escapeHtml(t('Jahr Ø'))}</th></tr></thead><tbody>`;
        for (const year of years.slice().reverse()) {
          const running = year === current;
          html += `<tr><th>${escapeHtml(year)}${running ? '*' : ''}</th>`;
          data.years[year].forEach((value, i) => {
            const ref = data.counts[i] >= 2 ? data.avg[i] : null;
            const title = value == null ? '' : (ref == null
              ? t('{month} {year}: {value} {unit}', {month: monthName(i + 1, 'short'), year, value: num(value, 1), unit})
              : t('{month} {year}: {value} {unit} ({delta} K zum Mittel)', {month: monthName(i + 1, 'short'), year, value: num(value, 1), unit, delta: sign(value - ref)}));
            html += cell(value, ref, title, false);
          });
          const ya = yearMean(year);
          const complete = !running && present(data.years[year]).length === 12;
          html += cell(ya, complete ? reference : null, ya == null ? '' : t('Jahresmittel {year}: {value} {unit}', {year, value: num(ya, 1), unit}), true);
          html += '</tr>';
        }
        html += `<tr class="wt-tm-avg"><th>Ø</th>${data.avg.map((value, i) => `<td>${value == null || data.counts[i] < 2 ? '–' : escapeHtml(plain(value))}</td>`).join('')}`
          + `<td class="wt-tm-year">${reference == null ? '–' : escapeHtml(plain(reference))}</td></tr></tbody>`;
        table.innerHTML = html;
        const scale = this.$refs.tempYearsScale;
        if (scale) scale.style.background = `linear-gradient(90deg, ${cold}, ${base}, ${hot})`;
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
        if (m.metric === 'sun') return t('Neuestes Jahr oben, eine Spalte je Kalendertag, gefärbt nach der Tagesklasse (sonnig, wechselhaft, trüb) wie im Tagesbild. Graue Felder sind Tage ohne Klasse (zu wenig helle Stunden oder keine Werte), leer ist es vor dem Archivbeginn und ab heute. Ein Klick öffnet den Tag in der Entität.');
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
        const isSun = m.metric === 'sun';
        const inRange = isPrecip
          ? [colors.surface, colors.cold]
          : [colors.cold, colors.surface, colors.prev];
        const sunPieces = [
          {value: 2, label: t('sonnig'), color: colors.sun},
          {value: 1, label: t('wechselhaft'), color: colors.sunSoft},
          {value: 0, label: t('trüb'), color: colors.overcast},
        ];
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
          visualMap: isSun ? {
            type: 'piecewise', pieces: sunPieces, seriesIndex: 1, orient: 'horizontal', left: 'center', bottom: 4,
            itemWidth: 14, itemHeight: 14, textStyle: {color: colors.text, fontSize: scaledFont(11)},
          } : {
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
              if (param.seriesIndex === 0) return `<b>${escapeHtml(label)}</b><br>${escapeHtml(isSun ? t('Keine Tagesklasse') : t('Zu wenige Werte'))}`;
              if (isSun) {
                const hours = m.hours ? m.hours[when.toISOString().slice(0, 10)] : null;
                return `<b>${escapeHtml(label)}</b><br>${escapeHtml(sunPieces.find((piece) => piece.value === value).label)}`
                  + (hours == null ? '' : `<br>${escapeHtml(num(hours, 1))} ${escapeHtml(t('Sonnenstunden'))}`);
              }
              return `<b>${escapeHtml(label)}</b><br>${escapeHtml(num(value, decimals))} ${escapeHtml(unit === '°C' ? '°C' : unit)}`;
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
          this.heatBySun = !!saved.bySun;
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
        writeStore({heat: {on: this.heatOn, room: this.heatRoom, limit: this.heatLimit, bySun: this.heatBySun}});
        this.drawHeatScatter();
        this.drawHeatSun();
        this.drawHeatTable();
        this.drawHeatShare();
      },

      heatHasSun() {
        return !!(this.heat && this.heat.sun && Object.keys(this.heat.sun).length);
      },

      heatSunNames() {
        return {sunny: t('sonnig'), mixed: t('wechselhaft'), overcast: t('trüb')};
      },

      // Punkte nach Tagesklasse statt nach Heizperiode: Farbe und Form je Klasse, damit es auch ohne Farbe lesbar bleibt.
      heatSunSeries(o, colors) {
        const names = this.heatSunNames();
        const style = {
          sunny: {color: colors.sun, symbol: 'circle'},
          mixed: {color: cssVar('--chart-4'), symbol: 'diamond'},
          overcast: {color: colors.cold, symbol: 'rect'},
        };
        const groups = {sunny: [], mixed: [], overcast: [], none: []};
        for (const day of this.heat.days) {
          const kind = this.heat.sun[day[0]] || 'none';
          groups[kind].push([day[1], Math.round(WetterHeating.dayKwh(day, o.on) * 100) / 100, day[0]]);
        }
        const series = WetterHeating.SUN_CLASSES.map((kind) => ({
          name: names[kind], type: 'scatter', data: groups[kind], symbolSize: 8, symbol: style[kind].symbol,
          itemStyle: {color: style[kind].color, opacity: 0.9},
        }));
        series.push({
          name: t('ohne Klasse'), type: 'scatter', data: groups.none, symbolSize: 4, itemStyle: {color: colors.faint, opacity: 0.4},
        });
        return {series, legend: [...WetterHeating.SUN_CLASSES.map((kind) => names[kind]), t('ohne Klasse')]};
      },

      heatSunSentence() {
        if (!this.heatHasSun()) return '';
        const stats = WetterHeating.classStats(this.heat.days, this.heatOpts(), this.heat.sun);
        const base = stats.overcast;
        const sunny = stats.sunny;
        const min = WetterHeating.MIN_CLASS_DAYS;
        const result = WetterHeating.compare(sunny, base);
        const days = {a: sunny.n, b: base.n};
        if (result.status === 'few') return t('Zu wenig Tage für einen Vergleich (mindestens {min} sonnige und trübe Heiztage, jetzt {a} und {b}).', {...days, min});
        if (result.status === 'unclear') return t('Kein klarer Unterschied zwischen sonnigen und trüben Tagen ({a} gegen {b} Tage).', days);
        const values = {...days, percent: num(Math.abs(result.percent), 0)};
        return result.percent < 0
          ? t('An sonnigen Tagen braucht das Haus {percent} % weniger je Gradtag als an trüben ({a} gegen {b} Tage).', values)
          : t('An sonnigen Tagen braucht das Haus {percent} % mehr je Gradtag als an trüben ({a} gegen {b} Tage).', values);
      },

      drawHeatSun() {
        const table = this.$refs.heatSunTable;
        if (!table || !this.heatHasSun()) return;
        const stats = WetterHeating.classStats(this.heat.days, this.heatOpts(), this.heat.sun);
        const names = this.heatSunNames();
        const tag = this.heatTag();
        const cell = (value, digits) => (value == null ? '<td class="wt-na">–</td>' : `<td>${escapeHtml(num(value, digits))}</td>`);
        let html = `<thead><tr><th></th><th>${escapeHtml(t('Tage'))}</th><th>${escapeHtml(t('Ø Außentemperatur'))}</th>`
          + `<th>${escapeHtml(t('Ø kWh je Tag'))}</th><th>kWh/Gt <span class="wt-sb">${escapeHtml(tag)}</span></th><th>${escapeHtml(t('gegen trüb'))}</th></tr></thead><tbody>`;
        for (const kind of WetterHeating.SUN_CLASSES) {
          const group = stats[kind];
          let diff = '<td class="wt-na">–</td>';
          if (kind !== 'overcast') {
            const result = WetterHeating.compare(group, stats.overcast);
            if (result.status === 'clear') diff = `<td><b>${result.percent < 0 ? '−' : '+'}${escapeHtml(num(Math.abs(result.percent), 0))} %</b></td>`;
            else if (result.status === 'unclear') diff = `<td class="wt-na">${escapeHtml(t('kein klarer Unterschied'))}</td>`;
            else diff = `<td class="wt-na">${escapeHtml(t('zu wenig Tage'))}</td>`;
          }
          html += `<tr><th scope="row">${escapeHtml(names[kind])}</th><td>${group.n}</td>${cell(group.temp, 1)}${cell(group.kwh, 1)}`
            + `<td>${group.q == null ? '–' : `<b>${escapeHtml(num(group.q, 2))}</b>`}</td>${diff}</tr>`;
        }
        table.innerHTML = html + '</tbody>';
      },

      drawHeatScatter() {
        const element = this.$refs.heatChart;
        if (!element) return;
        const o = this.heatOpts();
        const colors = palette();
        const periods = this.heatPeriods().slice(0, 4);
        const sunMode = this.heatBySun && this.heatHasSun();
        const series = [];
        let legend = [];
        if (sunMode) {
          const built = this.heatSunSeries(o, colors);
          series.push(...built.series);
          legend = built.legend;
          series[0].markLine = {
            silent: true, symbol: 'none', lineStyle: {color: colors.faint, type: 'dashed'},
            label: {formatter: () => `${t('Heizgrenze')} ${num(o.limit, 0)} °C`, color: colors.text, fontSize: scaledFont(11)},
            data: [{xAxis: o.limit}],
          };
        }
        (sunMode ? [] : periods).forEach((period, index) => {
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
        // Die Spalte Δ gibt es nur, wenn zwei abgeschlossene Heizperioden zum Vergleichen da sind.
        const delta = newer != null && older != null && shown.includes(newer) && shown.includes(older);
        let html = '<thead><tr class="wt-g"><th></th>'
          + shown.map((p) => `<th colspan="3" class="wt-sep">${escapeHtml(WetterHeating.label(p))}${info.running && p === info.current ? ' · ' + escapeHtml(t('läuft')) : ''}</th>`).join('')
          + (delta ? '<th class="wt-sep">Δ</th>' : '') + '</tr><tr><th></th>'
          + shown.map(() => `<th class="wt-sep"><span class="wt-sb">${escapeHtml(tag)}</span></th><th><span class="wt-sb">kWh</span></th><th><span class="wt-sb">kWh/Gt</span></th>`).join('')
          + (delta ? `<th class="wt-sep wt-sb">${escapeHtml(t('kWh/Gt {a} gegenüber {b}', {a: WetterHeating.label(newer), b: WetterHeating.label(older)}))}</th>` : '') + '</tr></thead><tbody>';
        const deltaCell = (a, b) => (delta ? change(a, b) : '');
        // Monate, in denen keine der gezeigten Heizperioden Werte hat (noch nicht erreicht), bleiben weg.
        for (const month of WetterHeating.MONTHS) {
          const stats = shown.map((p) => WetterHeating.stat(days, o, ...WetterHeating.monthRange(p, month)));
          if (!stats.some((st) => st && st.n)) continue;
          const byPeriod = (p) => stats[shown.indexOf(p)];
          html += `<tr><th scope="row">${escapeHtml(monthName(month, 'short'))}</th>${stats.map(cells).join('')}${deltaCell(byPeriod(newer), byPeriod(older))}</tr>`;
        }
        const totals = shown.map((p) => WetterHeating.stat(days, o, ...WetterHeating.periodRange(p)));
        const totalOf = (p) => totals[shown.indexOf(p)];
        html += `<tr class="wt-sum"><th scope="row">${escapeHtml(t('Heizperiode'))}</th>${totals.map(cells).join('')}${deltaCell(totalOf(newer), totalOf(older))}</tr>`;
        // Die Zeile „1.10. bis heute“ ist ohne zweite Heizperiode nur eine Wiederholung der Summe.
        if (info.running && shown.length > 1) {
          const cutOf = (p) => WetterHeating.cutRange(p, this.heat.today);
          const cuts = shown.map((p) => WetterHeating.stat(days, o, cutOf(p).from, cutOf(p).to));
          const label = cutOf(info.current).label;
          const first = info.current === shown[0] ? cuts[0] : null;
          const previous = info.complete[0] != null && shown.includes(info.complete[0]) ? cuts[shown.indexOf(info.complete[0])] : null;
          if (label) {
            html += `<tr class="wt-cut"><th scope="row">${escapeHtml(t('1.10.–{day}.{month}.', {day: label.day, month: label.month}))}</th>${cuts.map(cells).join('')}${deltaCell(first, previous)}</tr>`;
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
          rainiest_day: t('Regenreichster Tag'), sunniest_day: t('Sonnigster Tag'), sunniest_month: t('Sonnigster Monat'),
          longest_sunny_streak: t('Längste sonnige Folge'), longest_overcast_streak: t('Längste trübe Folge'),
        }[key] || key;
      },

      recordValue(record) {
        if (record.key.endsWith('_streak')) return escapeHtml(num(record.value, 0)) + `<small>${escapeHtml(t('Tage'))}</small>`;
        return escapeHtml(num(record.value, 1)) + (record.unit ? `<small>${escapeHtml(record.unit)}</small>` : '');
      },

      recordDate(record) {
        const day = (iso) => new Intl.DateTimeFormat(LOCALE, {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'})
          .format(new Date(iso + 'T12:00:00Z'));
        if (record.key === 'sunniest_month') {
          return new Intl.DateTimeFormat(LOCALE, {month: 'long', year: 'numeric', timeZone: 'UTC'}).format(new Date(record.date + 'T12:00:00Z'));
        }
        if (record.end) return `${day(record.date)} – ${day(record.end)}`;
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
          const stichtag = this.stichtag();
          const seq = ++this._tilesSeq;
          const res = await fetch(`wetterdashboard/tiles${stichtag ? `?date=${encodeURIComponent(stichtag)}` : ''}`);
          if (res.ok) {
            const data = await res.json();
            if (seq !== this._tilesSeq) return;  // ein neuerer Tag hat überholt
            this.tiles = data.tiles;
            this.tilesAt = data.at;
            this.tilesStand = new Intl.DateTimeFormat(LOCALE, {hour: '2-digit', minute: '2-digit', timeZone: data.tz}).format(new Date());
          }
        } catch (e) {
          // Kacheln sind Beiwerk; die Tabelle meldet Verbindungsfehler selbst.
        }
        this.tilesLoaded = true;
        this.$nextTick(() => this.layoutTiles());
      },

      async loadTable() {
        const seq = ++this._seq;
        this.error = '';
        try {
          const res = await fetch('wetterdashboard/table', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode, at: this.stichtag()}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._seq) return;  // eine neuere Anfrage hat überholt
          lastTable = data;
          this.paintTables(data);
        } catch (e) {
          if (seq === this._seq) this.error = t('Die Tabelle konnte nicht geladen werden.');
        }
      },
    };
  };
})();
