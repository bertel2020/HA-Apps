// Energiedashboard: der Reiter „Vergleich“ (energieVergleich: „Zeiträume im Vergleich“ und „Aufteilung des Verbrauchs“) und
// der Reiter „Langzeit“ (energieLangzeit). Der Server rechnet alle Summen (POST energiedashboard/langzeit/table, /consumers,
// /records, GET /months); dieses Skript formatiert und zeichnet nur. Aufbau und Bedienung wie das Wetterdashboard (wetter.js):
// ein Stichtag (Alpine-Store dashStichtag), die Tabelle mit Spalten- und Vorjahr-Menü. Kalenderangaben laufen über ServerTime in
// der Zeitzone des Servers. Beide Komponenten teilen sich die Hilfsfunktionen in diesem Block und den Browser-Speicher (STORE_KEY).
(function () {
  const COLUMN_KEYS = ['heute', 'gestern', 'monat', 'vormonat', 'jahr'];
  const DEFAULT_COLUMNS = ['monat', 'vormonat', 'jahr'];
  const STORE_KEY = 'zeitarchiv.energie.langzeit';
  const LOCALE = window.ZA_FORMAT;
  const ZT = window.ServerTime;

  const {formats, fmtDate, rangeText, energy, columnName, delta: cellDelta, comparisonHead, cellHtml, kenntagRule, kenntagLabels: KENNTAG_LABELS, energyKenntage, kenntageRange} = window.ElementTableRender;
  const num = (value, decimals) => NumberFormat.fmt(value, decimals);

  function readStore() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
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

  // ---- Monatsvergleich und Matrix „Jahre × Monate“ ----------------------------------------------
  // ECharts-Instanzen liegen außerhalb des Alpine-Objekts: ECharts verlässt sich intern auf Objekt-Identität,
  // ein Alpine-Proxy bricht das lautlos (wie bei den anderen Seiten).
  const charts = {};
  const observers = {};
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const scaledFont = (size) => Math.round(size * (parseFloat(cssVar('--font-scale')) || 1) * 10) / 10;

  // Bei jedem Zeichnen frisch gelesen: ein Wechsel von Farbschema oder Schriftgröße gilt sofort.
  function palette() {
    return {
      cur: cssVar('--chart-1'), prev: cssVar('--chart-2'), text: cssVar('--ink-muted'), faint: cssVar('--ink-faint'),
      grid: cssVar('--border'), ink: cssVar('--ink'), surface: cssVar('--surface'),
      good: cssVar('--positive'), bad: cssVar('--danger'), base: cssVar('--surface-alt'),
    };
  }

  // Ein Diagramm folgt der Breite seines Kastens auch ohne Fenster-Ereignis (Reiter eben erst sichtbar).
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

  const {monthName, mean, present, monthKey, isPartialStart, monthStats, yearSum, energyMatrix} = window.ElementTableRender;
  const {energyMonthly} = window.ElementChartRender;

  function kenntagValues(key, values, average, kwp, capacity) {
    const kwh = (value) => num(value, 1);
    switch (key) {
      case 'fast_autark': return t('Netzbezug {grid} kWh bei {use} kWh Verbrauch', {grid: kwh(values.netzbezug), use: kwh(values.verbrauch)});
      case 'ueberschuss': return t('Einspeisung {feed} kWh, Verbrauch {use} kWh', {feed: kwh(values.einspeisung), use: kwh(values.verbrauch)});
      case 'sonnig': return t('Erzeugung {pv} kWh ({spec} kWh je kWp)', {pv: kwh(values.erzeugung), spec: kwh(values.erzeugung / kwp)});
      case 'truebe': return t('Erzeugung {pv} kWh (Tagesmittel {avg} kWh)', {pv: kwh(values.erzeugung), avg: kwh(average)});
      default: return t('Entladung {n} kWh ({percent} %)', {n: kwh(values.entladung), percent: num(values.entladung / capacity * 100, 0)});
    }
  }
  // ---- Vergleich: Verbraucher und Zeiträume ----------------------------------------------------
  // Der Server liefert Fenster, Monatswerte und Jahressummen je Verbraucher (POST energiedashboard/langzeit/consumers);
  // hier entstehen daraus nur Anteile, Änderungen und das HTML.

  // Farben nach der Reihenfolge im Setup; die achte Farbe bleibt „Übrige (Grundlast)“ vorbehalten (dort schraffiert).
  const consumerColor = (index) => (index < 0 ? 'var(--chart-8)' : `var(--chart-${index % 7 + 1})`);
  const yearStart = (year) => ZT.toEpoch(year, 1, 1, 0);
  const monthYear = (ts) => {
    const p = ZT.parts(ts);
    return `${String(p.month).padStart(2, '0')}/${p.year}`;
  };
  const kwhText = (value) => num(value, Math.abs(value) < 100 ? 1 : 0);
  // Ein Monat am Archivbeginn zählt wie in partial_start (Server) als ganz, wenn das Archiv am 1. beginnt.
  const coversYear = (item, year) => item.first_ts != null && item.first_ts < yearStart(year) + 86400;

  // Verbraucher und „Übrige“ in Setup-Reihenfolge, mit Farbe.
  function consumerRows(data) {
    return [
      ...data.consumers.map((item, i) => ({...item, color: consumerColor(i), rest: false})),
      {...data.rest, color: consumerColor(-1), rest: true},
    ];
  }

  const yearValue = (item, year) => {
    const value = item.year_sums ? item.year_sums[String(year)] : null;
    return value == null ? null : value;
  };

  // Befunde eines Verbrauchers in den Monaten des Zeitraums (Jahr bis einschließlich lastMonth), als Sätze für das Dreieck.
  function flagTexts(flags, year, lastMonth) {
    const out = [];
    for (let month = 1; month <= lastMonth; month++) {
      const flag = flags[monthKey(year, month)];
      if (!flag) continue;
      const parts = [];
      if (flag.gaps) parts.push(t('Messlücken: {n} (sonst etwa {typical})', {n: flag.gaps, typical: flag.gaps_typical || 0}));
      if (flag.counter_decreases) parts.push(t('Zählerrückgänge: {n}', {n: flag.counter_decreases}));
      out.push(`${monthName(month, 'short')} ${year}: ${parts.join(', ')}`);
    }
    return out;
  }

  // Rangliste eines Jahres: im laufenden Jahr das Fenster bis heute, sonst ganze Jahre. Eine Änderung gibt es nur,
  // wenn der Verbraucher das ganze Vorjahr erfasst war; sonst steht ein kurzer Grund statt des Pfeils.
  function consumerRank(data, year) {
    const inWindow = year === data.window.year;
    const prevYear = year - 1;
    const years = data.years || [];
    const lastMonth = inWindow ? ZT.parts(data.window.cur.end - 1).month : 12;
    const windowEnd = inWindow ? data.window.cur.end : yearStart(year + 1);
    const entries = [];
    for (const item of consumerRows(data)) {
      const cur = inWindow ? item.cur : yearValue(item, year);
      if (cur == null) continue;
      const prev = inWindow ? item.vj : yearValue(item, prevYear);
      const comparable = prev != null && coversYear(item, prevYear);
      let note = '';
      if (!comparable) {
        const started = item.first_ts != null ? ZT.parts(item.first_ts).year : null;
        if (!years.includes(prevYear)) note = t('kein Vorjahr');
        else if (started != null && started >= year) note = t('neu ab {date}', {date: monthYear(item.first_ts)});
        else if (started === prevYear) note = t('Vorjahr erst ab {date}', {date: monthYear(item.first_ts)});
      }
      const flags = flagTexts(item.flags || {}, year, lastMonth);
      if (item.rest) {
        // Später begonnene Verbraucher steckten vorher in „Übrige“: der Vergleich über diesen Zeitpunkt ist verzerrt.
        for (const c of data.consumers) {
          if (c.late && c.first_ts > yearStart(prevYear) && c.first_ts < windowEnd) {
            flags.push(t('{name} wird erst ab {date} gemessen und steckt vorher in „Übrige“. Der Vergleich mit dem Vorjahr ist dadurch verzerrt.', {name: c.name, date: monthYear(c.first_ts)}));
          }
        }
      }
      entries.push({item, cur, prev: comparable ? prev : null, delta: comparable ? cur - prev : null, note, flags});
    }
    const rest = data.rest;
    const total = inWindow ? data.window.total.cur : yearValue({year_sums: data.total ? data.total.year_sums : null}, year);
    const restComparable = coversYear(rest, prevYear);
    const totalPrev = !restComparable ? null
      : (inWindow ? data.window.total.vj : yearValue({year_sums: data.total ? data.total.year_sums : null}, prevYear));
    const measured = entries.filter((e) => !e.item.rest).reduce((sum, e) => sum + e.cur, 0);
    const measuredPrev = totalPrev == null ? null
      : (inWindow ? data.window.measured.vj : data.consumers.reduce((sum, c) => sum + (yearValue(c, prevYear) || 0), 0));
    return {year, inWindow, prevYear, entries, total, totalPrev, measured, measuredPrev};
  }

  // Gemeinsame Sortierung von Rangliste und Tabelle: stabil (Gleichstand behält die Setup-Reihenfolge), nach Zahl oder Text;
  // ohne Wert (null) in beiden Richtungen ans Ende.
  function sortByValue(list, value, dir) {
    const sign = dir === 'asc' ? 1 : -1;
    return list.sort((a, b) => {
      const va = value(a);
      const vb = value(b);
      if (va == null || vb == null) return (va == null) - (vb == null);
      return sign * (typeof va === 'string' ? va.localeCompare(vb, undefined, {sensitivity: 'base'}) : va - vb);
    });
  }

  const ariaSort = (on, dir) => (on ? (dir === 'desc' ? 'descending' : 'ascending') : 'none');

  // Kopf einer sortierbaren Spalte (Rangliste und Tabelle): Knopf mit Beschriftung (labelHtml, schon maskiert) und Pfeil.
  function sortHead(labelHtml, on, dir, attrs, aria, sub = '') {
    const arrow = on ? (dir === 'desc' ? '▼' : '▲') : '↕';
    return `<button type="button" ${attrs} aria-label="${escapeHtml(aria)}">`
      + `<span><span class="wt-vt-yl">${labelHtml}</span><span class="wt-vt-arrow${on ? '' : ' is-off'}" aria-hidden="true">${arrow}</span></span>`
      + `${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</button>`;
  }

  // Zeiträume der Tabelle (Schlüssel wie energie_langzeit.CONSUMER_PERIODS, dieselben wie in „Zeiträume im Vergleich“); dazu
  // optional einzelne Jahre (Zahlen). Das laufende Jahr ist die Spalte „Dieses Jahr“.
  const PERIOD_KEYS = ['heute', 'gestern', 'monat', 'vormonat', 'jahr'];
  const isPeriod = (col) => PERIOD_KEYS.includes(col);
  const isColumn = (col) => Number.isInteger(col) || isPeriod(col);
  const periodOf = (data, key) => (data.periods || []).find((period) => period.key === key) || null;
  // Änderung zum Vorjahr wie in „Zeiträume im Vergleich“: mehr Verbrauch ist ungünstig.
  const CONSUMPTION_ROW = {kind: 'energy', better: 'down'};

  // Rangliste eines Zeitraums im Format von consumerRank: Monat bis heute gegen dasselbe Stück des Vorjahres, der Vormonat
  // ganz. Der Server liefert je Verbraucher {cur, vj}; ohne Vorjahreswert steht wie bei den Jahren ein kurzer Grund.
  function periodRank(data, key) {
    const period = periodOf(data, key);
    const empty = {key, inWindow: false, entries: [], total: null, totalPrev: null, measured: 0, measuredPrev: null};
    if (!period) return empty;
    // Ein Tag Spielraum wie coversYear: Ein Archiv, das am Beginn des Vorjahresfensters anfängt, deckt es ganz ab.
    const covers = (item) => item.first_ts != null && item.first_ts < period.vj.start + 86400;
    const entries = [];
    for (const item of consumerRows(data)) {
      const values = item.periods ? item.periods[key] : null;
      if (!values || values.cur == null) continue;
      const comparable = values.vj != null && covers(item);
      let note = '';
      if (!comparable) {
        if (item.first_ts != null && !covers(item)) {
          note = item.first_ts >= period.cur.start
            ? t('neu ab {date}', {date: monthYear(item.first_ts)})
            : t('Vorjahr erst ab {date}', {date: monthYear(item.first_ts)});
        } else note = t('kein Vorjahr');
      }
      const flags = [];
      if (item.rest) {
        for (const c of data.consumers) {
          if (c.late && c.first_ts > period.vj.start && c.first_ts < period.cur.end) {
            flags.push(t('{name} wird erst ab {date} gemessen und steckt vorher in „Übrige“. Der Vergleich mit dem Vorjahr ist dadurch verzerrt.', {name: c.name, date: monthYear(c.first_ts)}));
          }
        }
      }
      entries.push({item, cur: values.cur, prev: comparable ? values.vj : null, delta: comparable ? values.cur - values.vj : null, note, flags});
    }
    const totalPrev = covers(data.rest) ? period.total.vj : null;
    return {
      key, inWindow: false, entries, total: period.total.cur, totalPrev,
      measured: entries.filter((e) => !e.item.rest).reduce((sum, e) => sum + e.cur, 0),
      measuredPrev: totalPrev == null ? null : period.measured.vj,
    };
  }

  // Erstes Jahr des Archivs, wenn es nicht am 1. Januar beginnt (dann kein ganzes Jahr), sonst null.
  function partialFirstYear(data) {
    const rest = data.rest;
    if (rest.first_ts == null) return null;
    const year = ZT.parts(rest.first_ts).year;
    return coversYear(rest, year) ? null : year;
  }

  // Die Änderung als kurzer Text für Tooltips („+120 kWh“).
  const deltaKwh = (delta) => `${delta > 0 ? '+' : delta < 0 ? '−' : '±'}${kwhText(Math.abs(delta))} kWh`;

  // „Aufteilung des Verbrauchs“: ein gestapelter Balken je Zeile (Jahr oder Spalte der Tabelle), Abschnitte in Setup-Reihenfolge,
  // „Übrige“ zuletzt. per: [{label, sub, items: [{item, value}], total, base, measured, vj}]; vj (optional) ist derselbe Aufbau für
  // das Vorjahr und steht als dünner Balken darunter. mode 'pct': jeder Balken volle Breite; 'kwh': alle im Maßstab des größten.
  function sharesRowsHtml(per, mode, pick) {
    const peak = Math.max(0, ...per.map((d) => Math.max(d.base, d.vj ? d.vj.base : 0))) || 1;
    const segments = (d, label, vjTag) => d.items.map((e) => {
      const pct = e.value / d.base * 100;
      let text = '';
      if (!vjTag) {
        if (mode === 'pct' && pct >= 7) text = `${num(pct, 0)} %`;
        if (mode === 'kwh' && pct >= 7 && e.value / peak * 100 >= 9) text = kwhText(e.value);
      }
      const tip = `${e.item.name} ${label}${vjTag ? ` (${t('Vorjahr')})` : ''}: ${energy(e.value)} · ${num(pct, 1)} %`;
      const cls = `wt-vs-seg${e.item.rest ? ' wt-vr-hatch' : ''}${pick === e.item.key ? ' is-hl' : ''}`;
      return `<span class="${cls}" data-key="${escapeHtml(e.item.key)}" style="width:${pct.toFixed(2)}%;background:${e.item.color}" data-tooltip-fixed="${escapeHtml(tip)}">`
        + (text ? (e.item.rest ? `<b>${escapeHtml(text)}</b>` : escapeHtml(text)) : '') + '</span>';
    }).join('');
    let html = '';
    for (const d of per) {
      const width = (base) => (mode === 'kwh' ? ` style="width:${(base / peak * 100).toFixed(1)}%"` : '');
      const covered = d.total ? ` · ${escapeHtml(t('{percent} % gemessen', {percent: num(Math.min(100, d.measured / d.total * 100), 0)}))}` : '';
      html += `<div class="wt-vs-row"><span class="wt-vs-year">${escapeHtml(d.label)}${d.sub ? `<small>${escapeHtml(d.sub)}</small>` : ''}</span>`
        + `<div class="wt-vs-trackwrap"><div class="wt-vs-track"${width(d.base)}>${segments(d, d.label, false)}</div>`
        + (d.vj ? `<div class="wt-vs-track wt-vs-track-vj"${width(d.vj.base)}>${segments(d.vj, d.label, true)}</div>` : '')
        + `</div><span class="wt-vs-total"><b>${escapeHtml(d.total != null ? energy(d.total) : '–')}</b>${covered}</span></div>`;
    }
    return html;
  }

  const shareItems = (entries, value) => entries.map((e) => ({item: e.item, value: value(e)})).filter((e) => e.value != null && e.value > 0);

  // Ansicht „Jahre“: je Jahr aus den Jahressummen (neuestes oben).
  function sharesYearsHtml(data, years, mode, pick) {
    const rows = consumerRows(data);
    const current = data.current.year;
    const rest = data.rest;
    const per = years.map((year) => {
      const items = shareItems(rows.map((item) => ({item, value: yearValue(item, year)})), (e) => e.value);
      const total = yearValue({year_sums: data.total ? data.total.year_sums : null}, year);
      const sum = items.reduce((a, e) => a + e.value, 0);
      const measured = items.filter((e) => !e.item.rest).reduce((a, e) => a + e.value, 0);
      let sub = '';
      if (year === current) sub = t('bis {date}', {date: fmtDate(formats.dm, data.now)});
      else if (rest.first_ts != null && !coversYear(rest, year) && ZT.parts(rest.first_ts).year === year) {
        sub = t('ab {date}', {date: fmtDate(formats.dm, rest.first_ts)});
      }
      return {label: `${year}${year === current ? '*' : ''}`, sub, items, total, base: Math.max(total || 0, sum), measured};
    }).filter((d) => d.base > 0);
    return sharesRowsHtml(per, mode, pick);
  }

  // Ansicht „Zeiträume“: dieselben Spalten wie die Tabelle, darunter dünn das Vorjahr.
  function sharesColsHtml(data, cols, mode, pick, vorjahr) {
    const per = cols.map((col) => {
      const rank = isPeriod(col.id) ? periodRank(data, col.id) : consumerRank(data, col.id);
      const items = shareItems(rank.entries, (e) => e.cur);
      const sum = items.reduce((a, e) => a + e.value, 0);
      let vj = null;
      if (vorjahr && rank.totalPrev != null) {
        const vitems = shareItems(rank.entries, (e) => e.prev);
        vj = {items: vitems, base: Math.max(rank.totalPrev, vitems.reduce((a, e) => a + e.value, 0))};
      }
      return {
        label: isPeriod(col.id) ? columnName(col.id) : `${col.id}${col.sub ? '*' : ''}`,
        sub: rangeText(col.cur.start, col.cur.end), items, total: rank.total, base: Math.max(rank.total || 0, sum),
        measured: rank.measured, vj,
      };
    }).filter((d) => d.base > 0);
    return sharesRowsHtml(per, mode, pick);
  }

  // Beschriftungen, die in ihrem Abschnitt keinen Platz haben (schmale Fenster), entfallen; der Tooltip nennt den Wert.
  function fitShareLabels(host) {
    host.querySelectorAll('.wt-vs-seg').forEach((seg) => {
      if (seg.scrollWidth > seg.clientWidth + 1) seg.textContent = '';
    });
  }

  function sharesLegendHtml(data, pick) {
    return consumerRows(data).map((item) => `<button type="button" data-key="${escapeHtml(item.key)}" aria-pressed="${pick === item.key}">`
      + `<i class="${item.rest ? 'wt-vr-hatch' : ''}" style="background:${item.color}"></i>${escapeHtml(item.name)}</button>`).join('');
  }

  // ---- Vergleich: die Tabelle „Zeiträume im Vergleich“ --------------------------------------------
  // Eine Tabelle für Bilanz und Verbraucher: oben die Gruppen der Summen-Tabelle (Energie, Quoten, Speicher, PV-Anlage) aus
  // POST langzeit/table, darunter die Gruppe „Verbraucher“ aus POST langzeit/consumers. Jede Spalte ist ein Zeitraum (mit
  // Fenstern vom Server) oder ein einzelnes Jahr, daneben ihr Vorjahr.

  // Spalten als Objekte für den gemeinsamen Kopf (comparisonHead): ein Zeitraum mit seinen Fenstern vom Server, ein einzelnes
  // Jahr mit „Jahr“ und der Jahreszahl; das erste, nicht ganz erfasste Jahr des Archivs trägt ein Sternchen. Spalten ohne Daten
  // vom Server entfallen.
  function compareColumns(ctx) {
    const {table, cons, columns} = ctx;
    const windowOf = (key) => (table ? table.columns.find((col) => col.key === key) : null) || (cons ? periodOf(cons, key) : null);
    const firstYear = cons ? partialFirstYear(cons) : monthsPartialYear(ctx.months);
    const out = [];
    for (const id of columns) {
      if (isPeriod(id)) {
        const win = windowOf(id);
        if (win) out.push({id, key: id, cur: win.cur, vj: win.vj});
        continue;
      }
      const end = yearStart(id + 1);
      const col = {id, key: 'jahr', cur: {start: yearStart(id), end}, vj: {start: yearStart(id - 1), end: yearStart(id)}};
      if (id === firstYear) {
        col.sub = `${id}*`;
        if (cons && cons.rest.first_ts != null) col.tip = rangeText(cons.rest.first_ts, end);
      }
      out.push(col);
    }
    return out;
  }

  // Erstes, nicht ganz erfasstes Jahr aus den Monatswerten (ohne Verbraucher): das Jahr, in dem die Messung mitten im Jahr beginnt.
  function monthsPartialYear(months) {
    const role = months && months.roles.find((r) => r.partial_start);
    return role ? role.partial_start[0] : null;
  }

  // Summe einer Rolle (Erzeugung, Einspeisung, Netzbezug, Verbrauch) über die vorhandenen Monate eines Jahres.
  function roleYearSum(months, key, year) {
    const role = months && months.roles.find((r) => r.key === key);
    const values = role && role.values[String(year)];
    if (!values) return null;
    const list = present(values);
    return list.length ? list.reduce((a, b) => a + b, 0) : null;
  }

  // Zeilen der Bilanz, die der Reiter „Langzeit“ als Monatswerte kennt: sie lassen sich in der Tabelle auswählen.
  const MONTH_ROLE_ROWS = ['erzeugung', 'einspeisung', 'netzbezug', 'verbrauch'];
  const NA_YEAR = () => t('Für einzelne Jahre nicht berechnet');

  // Hauptwert und kleiner darunter der andere Wert; ohne Wert ein Strich.
  function consumerValueHtml(kwh, share, pct, warn = '') {
    if (kwh == null) return '<span class="wt-v">–</span>';
    const shareText = share == null ? '–' : `${num(share, 1)} %`;
    return `<span class="wt-v">${warn}${escapeHtml(pct ? shareText : energy(kwh))}</span>`
      + `<span class="wt-vt-sub">${escapeHtml(pct ? energy(kwh) : shareText)}</span>`;
  }

  const naCell = (cls, tip, note = '') => `<td class="${cls} wt-na"${tip ? ` data-tooltip-fixed="${escapeHtml(tip)}"` : ''}>`
    + `<span class="wt-v">–</span>${note ? `<small>${escapeHtml(note)}</small>` : ''}</td>`;

  // ctx: {table, cons, months, columns, vorjahr, diff, main ('pct'|'kwh'), filter ('all'|'bilanz'|'cons'), sort {col, dir}, selected}
  function compareTableHtml(ctx) {
    const {table, cons, months, vorjahr, diff, filter, sort, selected} = ctx;
    const cols = compareColumns(ctx);
    if (!cols.length) return `<tbody><tr><td>${escapeHtml(t('Keine Spalte ausgewählt.'))}</td></tr></tbody>`;
    const base = table || cons;
    const sortable = !!cons && filter !== 'bilanz';
    const pct = ctx.main === 'pct';
    const label = (id) => (isPeriod(id) ? columnName(id) : String(id));
    const sorted = (id) => (sortable && id === sort.col ? ' wt-vt-sorted' : '');
    for (const col of cols) {
      const on = sortable && col.id === sort.col;
      col.headClass = ` wt-vt-yh${on ? ' is-sorted' : ''}`;
      col.headAttrs = sortable ? ` aria-sort="${ariaSort(on, sort.dir)}"` : '';
    }
    const head = comparisonHead({columns: cols, vorjahr, at: base.at, now: base.now}, {
      titleHtml: sortable ? (col, title) => sortHead(title, col.id === sort.col, sort.dir, `data-col="${col.id}"`, t('Nach {year} sortieren', {year: label(col.id)})) : null,
    });
    const span = 1 + cols.length * (vorjahr ? 2 : 1);
    let html = `${head}<tbody>`;

    // Summen-Zeilen der Bilanz.
    if (table && filter !== 'cons') {
      let group = null;
      for (const row of table.rows) {
        if (row.group !== group) {
          group = row.group;
          html += `<tr class="wt-grp"><th colspan="${span}">${escapeHtml(group)}</th></tr>`;
        }
        const key = `r:${row.key}`;
        const pickable = MONTH_ROLE_ROWS.includes(row.key);
        html += `<tr${pickable ? ` class="wt-vt-row${selected === key ? ' is-sel' : ''}" data-key="${key}" tabindex="0" aria-label="${escapeHtml(t('{name}: Monatswerte in Langzeit zeigen', {name: row.label}))}"` : ''}>`
          + `<th scope="row">${escapeHtml(row.label)}</th>`;
        for (const col of cols) {
          let cur;
          let vj;
          let curFrom;
          let vjFrom;
          if (isPeriod(col.id)) {
            const cell = row.cells[col.id] || {};
            ({cur, vj} = cell);
            curFrom = cell.cur_from;
            vjFrom = cell.vj_from;
          } else if (row.kind === 'energy' && pickable) {
            cur = roleYearSum(months, row.key, col.id);
            vj = roleYearSum(months, row.key, col.id - 1);
          } else {
            html += naCell(`wt-sep${sorted(col.id)}`, NA_YEAR());
            if (vorjahr) html += naCell('wt-vj', '');
            continue;
          }
          html += cellHtml(cur, curFrom, false, vorjahr && diff ? cellDelta(cur, vj, row) : '', row);
          if (vorjahr) html += cellHtml(vj, vjFrom, true, '', row);
        }
        html += '</tr>';
      }
    }

    // Gruppe „Verbraucher“: jede Spalte ist die Rangliste ihres Zeitraums (periodRank) oder Jahres (consumerRank).
    if (cons && filter !== 'bilanz') {
      const ranks = {};
      for (const col of cols) ranks[col.id] = isPeriod(col.id) ? periodRank(cons, col.id) : consumerRank(cons, col.id);
      const entryOf = (id, key) => ranks[id].entries.find((e) => e.item.key === key) || null;
      const years = ctx.columns.filter(Number.isInteger);
      const oldestYear = years.length ? Math.min(...years) : cons.window.year;
      const rows = consumerRows(cons);
      const list = rows.filter((item) => !item.rest);
      sortByValue(list, (item) => (entryOf(sort.col, item.key) || {cur: null}).cur, sort.dir);
      const seg = (value, text) => `<button type="button" data-main="${value}" aria-pressed="${pct === (value === 'pct')}">${escapeHtml(text)}</button>`;
      const segLabel = escapeHtml(t('Anteil oder kWh'));
      html += `<tr class="wt-grp"><th colspan="${span}"><span class="wt-grp-row"><span>${escapeHtml(t('Verbraucher'))}</span>`
        + `<span class="wt-grp-seg" role="group" aria-label="${segLabel}">${seg('kwh', t('kWh'))}${seg('pct', t('Anteil'))}</span></span></th></tr>`;

      for (const item of [...list, ...rows.filter((row) => row.rest)]) {
        const key = `c:${item.key}`;
        const name = item.entity_id
          ? `<a href="entities/${encodeURIComponent(item.entity_id)}?range=year&offset=0">${escapeHtml(item.name)}</a>`
          : `<span>${escapeHtml(item.name)}</span>`;
        const from = item.late && item.first_ts != null && ZT.parts(item.first_ts).year >= oldestYear
          ? `<span class="wt-vr-from">${escapeHtml(t('ab {date}', {date: monthYear(item.first_ts)}))}</span>` : '';
        const hatch = item.rest ? ' wt-vr-hatch' : '';
        html += `<tr class="wt-vt-row${item.rest ? ' wt-vt-rest' : ''}${selected === key ? ' is-sel' : ''}" data-key="${escapeHtml(key)}"`
          + ` tabindex="0" aria-label="${escapeHtml(t('{name}: Monatswerte in Langzeit zeigen', {name: item.name}))}">`
          + `<th scope="row"><span class="wt-vt-name"><i class="wt-vr-dot${hatch}" style="background:${item.color}"></i><span>${name}${from}</span></span></th>`;
        for (const col of cols) {
          const entry = entryOf(col.id, item.key);
          if (!entry) {
            html += naCell(`wt-sep${sorted(col.id)}`, t('{name} {year}: noch nicht erfasst', {name: item.name, year: label(col.id)}));
            if (vorjahr) html += naCell(`wt-vj${sorted(col.id)}`, '');
            continue;
          }
          const rank = ranks[col.id];
          const share = rank.total ? entry.cur / rank.total * 100 : null;
          const shareText = share == null ? '–' : `${num(share, 1)} %`;
          const warn = entry.flags.length
            ? `<span class="wt-vr-warn" data-tooltip-fixed="${escapeHtml(entry.flags.join('\n'))}" role="img" aria-label="${escapeHtml(t('Hinweis: {text}', {text: entry.flags.join('; ')}))}"></span>` : '';
          let tip = `${item.name} ${label(col.id)}: ${energy(entry.cur)} · ${shareText}`;
          if (entry.delta != null) tip += ` · ${t('Vorjahr {value}', {value: deltaKwh(entry.delta)})}`;
          html += `<td class="wt-sep${sorted(col.id)}" data-tooltip-fixed="${escapeHtml(tip)}">${consumerValueHtml(entry.cur, share, pct, warn)}`
            + `${vorjahr && diff ? cellDelta(entry.cur, entry.prev, CONSUMPTION_ROW) : ''}</td>`;
          if (vorjahr) {
            html += entry.prev == null
              ? naCell(`wt-vj${sorted(col.id)}`, '', entry.note)
              : `<td class="wt-vj${sorted(col.id)}">${consumerValueHtml(entry.prev, rank.totalPrev ? entry.prev / rank.totalPrev * 100 : null, pct)}</td>`;
          }
        }
        html += '</tr>';
      }

      // Wie viel des Verbrauchs eigene Zähler erfassen.
      html += `<tr class="wt-vt-cov"><th scope="row">${escapeHtml(t('Gemessen'))}</th>`;
      for (const col of cols) {
        const rank = ranks[col.id];
        if (!rank.total) {
          html += naCell(`wt-sep${sorted(col.id)}`, '');
          if (vorjahr) html += naCell(`wt-vj${sorted(col.id)}`, '');
          continue;
        }
        const tip = t('Eigene Zähler erfassen {measured} von {total} kWh', {measured: kwhText(rank.measured), total: kwhText(rank.total)});
        html += `<td class="wt-sep${sorted(col.id)}" data-tooltip-fixed="${escapeHtml(tip)}"><span class="wt-v">${escapeHtml(`${num(Math.min(100, rank.measured / rank.total * 100), 0)} %`)}</span>`
          + `<span class="wt-vt-sub">${escapeHtml(energy(rank.measured))}</span></td>`;
        if (vorjahr) {
          html += rank.totalPrev && rank.measuredPrev != null
            ? `<td class="wt-vj${sorted(col.id)}"><span class="wt-v">${escapeHtml(`${num(Math.min(100, rank.measuredPrev / rank.totalPrev * 100), 0)} %`)}</span>`
              + `<span class="wt-vt-sub">${escapeHtml(energy(rank.measuredPrev))}</span></td>`
            : naCell(`wt-vj${sorted(col.id)}`, '');
        }
      }
      html += '</tr>';
    }
    return `${html}</tbody>`;
  }

  // Jahre-Auswahl der Karten: „Letzte 5“ oder „Alle“, nur ab mehr als fünf Jahren im Archiv. Früher gab es auch „Letzte 10“
  // (gespeichert als 10); das gilt jetzt als „Alle“.
  const YEAR_STEP = 5;
  const storedYearLimit = (value) => (value === YEAR_STEP ? YEAR_STEP : 0);
  const yearChoices = (count) => [
    {value: YEAR_STEP, label: t('Letzte 5')},
    {value: 0, label: t('Alle ({n})', {n: count})},
  ];

  // Die Reiter „Vergleich“ und „Langzeit“ laden ihre Daten erst beim ersten Zeigen (Ereignis „energie-tab“).
  function onFirstShow(tabName, component, load) {
    window.addEventListener('energie-tab', (event) => {
      if (!component.$root.isConnected || event.detail !== tabName) return;
      if (!component.opened) {
        component.opened = true;
        load();
      } else {
        // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
        component.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
        if (component.onReshow) component.onReshow();
      }
    });
  }

  // Der Stichtag der Seite (Alpine-Store dashStichtag wie im Wetterdashboard); null = heute.
  const stichtag = () => window.DashboardStichtag || null;

  const MODE_HINT = {
    bis_jetzt: () => t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Abgeschlossene Zeiträume (Gestern, Letzter Monat) sind in beiden Fällen gleich.'),
    komplett: () => t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch meist höhere Summen.'),
  };

  // ---- Reiter „Vergleich“ ------------------------------------------------------------------------
  // opts.consumers: Verbraucher der obersten Ebene eingerichtet; opts.hidden: im Setup abgewählte Bereiche
  // (energie_langzeit.CARD_BLOCKS), „verbraucher“ blendet Gruppe und Aufteilung aus.
  window.energieVergleich = function energieVergleich(opts = {}) {
    const hidden = opts.hidden || [];
    const hasCons = !!opts.consumers && !hidden.includes('verbraucher');
    return {
      hasCons,
      opened: false,
      table: null,
      cons: null,
      months: null,
      error: '',
      consumersError: '',
      standText: '',
      // Gewählte Spalten: Zeiträume (Schlüssel) und einzelne Jahre (Zahlen); null = Monat, Vormonat, Jahr.
      tblCols: null,
      vorjahr: true,
      diff: true,
      mode: 'bis_jetzt',
      // Hauptwert der Verbraucher ('kwh' oder 'pct') und welche Gruppen die Tabelle zeigt.
      main: 'kwh',
      filter: 'all',
      sortCol: null,
      sortDir: 'desc',
      // Ausgewählte Zeile: „r:<Rolle>“ (Bilanz) oder „c:<Verbraucher>“; gilt auch im Reiter „Langzeit“.
      sel: null,
      shView: 'cols',
      sharesMode: 'pct',
      yearLimit: 0,
      _seq: 0,
      _consSeq: 0,

      init() {
        const saved = readStore();
        // Gespeichert sind Jahre (Zahlen) und Zeiträume (Schlüssel); frühere Stände kannten die Summen-Tabelle der Übersicht
        // (nur Zeiträume, „columns“) und die Verbraucher-Tabelle (Jahre, „tblCols“) getrennt.
        if (Array.isArray(saved.tblCols) && saved.tblCols.length && saved.tblCols.every(isColumn)) this.tblCols = saved.tblCols;
        else if (Array.isArray(saved.columns)) {
          const known = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
          if (known.length) this.tblCols = known;
        }
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (typeof saved.diff === 'boolean') this.diff = saved.diff;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        if (saved.tblMain === 'pct' || saved.tblMain === 'kwh') this.main = saved.tblMain;
        if (['all', 'bilanz', 'cons'].includes(saved.cmpFilter)) this.filter = saved.cmpFilter;
        if (typeof saved.sel === 'string') this.sel = saved.sel;
        if (saved.shView === 'cols' || saved.shView === 'years') this.shView = saved.shView;
        if (saved.sharesMode === 'pct' || saved.sharesMode === 'kwh') this.sharesMode = saved.sharesMode;
        this.yearLimit = storedYearLimit(saved.consYearLimit);
        const savedSortCol = saved.tblSort ? (saved.tblSort.col ?? saved.tblSort.year) : null;
        if (isColumn(savedSortCol) && ['asc', 'desc'].includes(saved.tblSort.dir)) {
          this.sortCol = savedSortCol;
          this.sortDir = saved.tblSort.dir;
        }
        // Der Energiefluss der Übersicht meldet seinen Tag: er wird zum gemeinsamen Stichtag (heute oder später = „jetzt“).
        const follow = (at) => {
          const store = window.Alpine && Alpine.store('dashStichtag');
          if (store && store.date !== (at || null)) store.set(at || null);
        };
        window.addEventListener('energie-window', (event) => {
          if (this.$root.isConnected) follow(event.detail && event.detail.at ? event.detail.at : null);
        });
        if (window.energieWindowAt !== undefined) follow(window.energieWindowAt);
        window.addEventListener('dashboard-stichtag', () => {
          if (this.$root.isConnected && this.opened) this.reloadAtStichtag();
        });
        // Geladen wird erst beim ersten Öffnen des Reiters: Die Übersicht ist die Startansicht.
        onFirstShow('cmp', this, () => this.load());
        // Nach einer Größenänderung neu zeichnen: welche Beschriftung in ihren Abschnitt passt, hängt von der Breite ab.
        window.addEventListener('resize', () => {
          if (this.$root.isConnected && this.opened) this.draw();
        });
      },

      load() {
        this.loadTable();
        if (this.hasCons) this.loadConsumers(true);
        this.loadMonths();
      },

      // Ein neuer Stichtag verschiebt alle Fenster; die Monatswerte aller Jahre bleiben.
      reloadAtStichtag() {
        this.loadTable();
        if (this.hasCons) this.loadConsumers(false);
      },

      async loadTable() {
        const seq = ++this._seq;
        this.error = '';
        try {
          // Alle fünf Zeiträume samt Vorjahr: das Spalten- und das Vorjahr-Menü zeichnen nur neu, laden also nicht.
          const res = await fetch('energiedashboard/langzeit/table', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({columns: COLUMN_KEYS, vorjahr: true, mode: this.mode, at: stichtag()}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._seq) return;  // eine neuere Anfrage hat überholt
          this.table = data;
          // „Stand“: der Tag, bis zu dem die Zeiträume zählen (ein früherer Tag oder heute).
          const day = data.at || data.today;
          this.standText = day ? t('Stand {date}', {date: fmtDate(formats.dmy, Date.parse(`${day}T12:00:00Z`) / 1000)}) : '';
          this.$nextTick(() => this.drawTable());
        } catch (e) {
          if (seq === this._seq) this.error = t('Die Tabelle konnte nicht geladen werden.');
        }
      },

      // Verbraucher: Fenster, Zeiträume und (withMonths) Monatswerte aller Jahre. Ohne withMonths bleiben die Monatswerte.
      async loadConsumers(withMonths) {
        const seq = ++this._consSeq;
        const months = withMonths || !this.cons;
        this.consumersError = '';
        try {
          const res = await fetch('energiedashboard/langzeit/consumers', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({months, mode: this.mode, at: stichtag(), periods: COLUMN_KEYS}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._consSeq) return;  // eine neuere Anfrage hat überholt
          if (!months && this.cons) {
            const old = this.cons;
            const keep = (from, to) => {
              if (!from) return;
              for (const key of ['values', 'year_sums', 'flags', 'unchecked']) if (key in from) to[key] = from[key];
            };
            data.consumers.forEach((item) => keep(old.consumers.find((o) => o.key === item.key), item));
            keep(old.rest, data.rest);
            data.years = old.years;
            data.total = old.total;
          }
          this.cons = data;
          this.$nextTick(() => this.draw());
        } catch (e) {
          if (seq === this._consSeq) this.consumersError = t('Die Verbraucher konnten nicht geladen werden.');
        }
      },

      // Monatswerte der Bilanz: Jahressummen für einzelne Jahre in der Tabelle und die Liste der Jahre ohne Verbraucher.
      async loadMonths() {
        try {
          const res = await fetch('energiedashboard/langzeit/months');
          if (!res.ok) throw new Error(String(res.status));
          this.months = await res.json();
          this.$nextTick(() => this.draw());
        } catch (e) {
          // Ohne Monatswerte fehlen nur die einzelnen Jahre; die Zeiträume stehen trotzdem da.
        }
      },

      draw() {
        this.drawTable();
        this.drawShares();
      },

      // ---- Spalten und Menüs ----

      // Das laufende Jahr (Jahresfenster des Stichtags); „Dieses Jahr“ steht für dieses Jahr, davor die einzelnen Jahre.
      windowYear() {
        const jahr = this.table && this.table.columns.find((col) => col.key === 'jahr');
        if (jahr) return ZT.parts(jahr.cur.start).year;
        return this.cons ? this.cons.window.year : ZT.parts(Date.now() / 1000).year;
      },

      tablePeriodOptions() {
        return COLUMN_KEYS.map((key) => ({col: key, label: columnName(key)}));
      },

      tableYearOptions() {
        const years = this.cons && this.cons.years ? this.cons.years : (this.months ? this.months.years : []);
        return years.filter((year) => year < this.windowYear()).map((year) => ({col: year, label: String(year)}));
      },

      columnOptions() {
        return [...this.tablePeriodOptions(), ...this.tableYearOptions()];
      },

      // Das laufende Jahr als Zahl (frühere Stände) gilt als „Dieses Jahr“.
      canonCol(col) {
        return Number.isInteger(col) && col >= this.windowYear() ? 'jahr' : col;
      },

      // Gewählte Spalten in Anzeigereihenfolge (Zeiträume zuerst, dann die Jahre, neueste zuerst); sonst Monat, Vormonat, Jahr.
      cols() {
        if (!this.tblCols) return [...DEFAULT_COLUMNS];
        const wanted = this.tblCols.map((col) => this.canonCol(col));
        const chosen = this.columnOptions().map((option) => option.col).filter((col) => wanted.includes(col));
        return chosen.length ? chosen : [...DEFAULT_COLUMNS];
      },

      // Eine Spalte an- oder abwählen; die letzte gewählte bleibt (das Kästchen ist dann gesperrt).
      toggleCol(col) {
        const current = this.cols();
        if (current.includes(col) && current.length === 1) return;
        const next = current.includes(col) ? current.filter((c) => c !== col) : [...current, col];
        this.tblCols = this.columnOptions().map((option) => option.col).filter((c) => next.includes(c));
        writeStore({tblCols: this.tblCols});
        this.draw();
      },

      // Vorjahresvergleich: 'off' oder der Modus ('bis_jetzt'/'komplett'); schließt das Popover. Ein anderer Modus verschiebt die
      // Fenster des Vorjahres, die Daten kommen dann neu vom Server.
      setYoy(choice) {
        const before = this.mode;
        if (choice === 'off') this.vorjahr = false;
        else {
          this.vorjahr = true;
          this.mode = choice;
        }
        document.querySelectorAll('.wt-yoy-popover').forEach((popover) => popover.classList.remove('open'));
        writeStore({vorjahr: this.vorjahr, mode: this.mode});
        if (this.mode !== before) this.reloadAtStichtag();
        else this.draw();
      },

      yoyLabel() {
        if (!this.vorjahr) return t('Vorjahr: Aus ▾');
        return (this.mode === 'komplett' ? t('Vorjahr: komplett') : t('Vorjahr: bis jetzt')) + ' ▾';
      },

      // Differenzzeile unter den Werten ein- oder ausblenden; die Daten bleiben, nur die Tabelle wird neu gezeichnet.
      toggleDiff() {
        this.diff = !this.diff;
        writeStore({diff: this.diff});
        this.draw();
      },

      columnName,

      modeHint() {
        return MODE_HINT[this.mode]();
      },

      setFilter(filter) {
        this.filter = filter;
        writeStore({cmpFilter: filter});
        this.drawTable();
      },

      setMain(main) {
        this.main = main;
        writeStore({tblMain: main});
        this.drawTable();
      },

      // Sortierung der Verbraucher; eine Spalte, die nicht mehr gezeigt wird, fällt auf „Dieses Jahr“ zurück.
      sortState() {
        const cols = this.cols();
        const col = this.canonCol(this.sortCol);
        if (!cols.includes(col)) {
          return {col: cols.includes('jahr') ? 'jahr' : (cols.find(Number.isInteger) ?? cols[0]), dir: this.sortCol == null ? this.sortDir : 'desc'};
        }
        return {col, dir: this.sortDir};
      },

      // ---- Tabelle ----

      note() {
        const parts = [];
        if (this.vorjahr) parts.push(this.modeHint());
        parts.push(
          t('Die Zeiträume richten sich nach dem Stichtag; heute heißt „jetzt“. Grün: günstig (mehr Erzeugung und Einspeisung, weniger Netzbezug und Verbrauch), rot: ungünstig. „Dieses Jahr“ vergleicht kalendarisch, die Kacheln der Übersicht vergleichen rollierend über 365 Tage. Der Verbrauch ist die Bilanz: Erzeugung + Netzbezug + Speicherentladung − Einspeisung − Speicherladung. Quoten ändern sich in Prozentpunkten (PP). Spezifischer Ertrag und Ertrag je Einstrahlung erscheinen, wenn bei den Erzeugern eine Anlagenleistung (kWp) steht; der Ertrag je Einstrahlung braucht außerdem die Strahlung aus dem Wetterdashboard.'),
          t('„Spalten“ wählt Zeiträume und einzelne Jahre. Einzelne Jahre zeigen Erzeugung, Einspeisung, Netzbezug, Verbrauch und die Verbraucher.'),
        );
        if (this.hasCons) {
          parts.push(
            t('Die Verbraucher sind die der obersten Ebene; Geräte hinter einem Zwischenzähler stecken im Zwischenzähler. Jede Spalte ist die Rangliste ihres Zeitraums, kWh | Anteil wählt den Hauptwert. „Übrige (Grundlast)“ ist der Rest bis zur Bilanz, nie negativ. Das Dreieck nennt Messlücken, Zählerrückgänge und einen verzerrten Vergleich, „–“ steht mit Grund, wenn der Verbraucher im Vorjahr noch nicht ganz erfasst war.'),
            t('Ein Klick auf einen Spaltenkopf sortiert die Verbraucher, ein zweiter dreht die Reihenfolge. Ein Klick auf eine Zeile wählt sie für „Aufteilung des Verbrauchs“ und für die Monatswerte im Reiter „Langzeit“.'),
          );
          const sort = this.sortState();
          parts.push(sort.dir === 'desc'
            ? t('Sortiert nach {year}, größter zuerst.', {year: isPeriod(sort.col) ? columnName(sort.col) : sort.col})
            : t('Sortiert nach {year}, kleinster zuerst.', {year: isPeriod(sort.col) ? columnName(sort.col) : sort.col}));
          const first = this.cons ? partialFirstYear(this.cons) : null;
          if (first != null && this.cols().includes(first)) {
            parts.push(t('* Das Archiv beginnt erst am {date}, {year} ist kein ganzes Jahr.', {date: fmtDate(formats.dmy, this.cons.rest.first_ts), year: first}));
          }
        }
        return parts.join(' ');
      },

      // Filter nur, wenn es Verbraucher gibt.
      showFilter() {
        return this.hasCons;
      },

      drawTable() {
        const table = this.$refs.table;
        const wrap = this.$refs.tableWrap;
        if (!table || !(this.table || this.cons)) return;
        const cols = this.cols();
        // Zeitraum und Vorjahr sind je eine Spalte gleicher Breite.
        table.style.setProperty('--n', cols.length * (this.vorjahr ? 2 : 1));
        table.innerHTML = compareTableHtml({
          table: this.table, cons: this.cons, months: this.months, columns: cols, vorjahr: this.vorjahr, diff: this.diff,
          main: this.main, filter: this.hasCons ? this.filter : 'bilanz', sort: this.sortState(), selected: this.sel,
        });
        // Scroll-Hinweis des App-Standards (.tbl-wrap.is-scrollable), nur wenn die Tabelle wirklich breiter ist.
        if (wrap) requestAnimationFrame(() => wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1));
      },

      // Klick auf einen Spaltenkopf sortiert nach dieser Spalte (ein zweiter dreht die Reihenfolge), auf „kWh | Anteil“ wählt den
      // Hauptwert, auf eine Zeile wählt sie aus; der Name öffnet die Entität.
      tableClick(event) {
        if (event.target.closest('a')) return;
        const head = event.target.closest('button[data-col]');
        if (head) {
          const raw = head.dataset.col;
          const col = /^\d+$/.test(raw) ? Number(raw) : raw;
          const sort = this.sortState();
          this.sortDir = col === sort.col ? (sort.dir === 'desc' ? 'asc' : 'desc') : 'desc';
          this.sortCol = col;
          writeStore({tblSort: {col, dir: this.sortDir}});
          this.drawTable();
          return;
        }
        const main = event.target.closest('button[data-main]');
        if (main) {
          this.setMain(main.dataset.main);
          return;
        }
        const row = event.target.closest('tr[data-key]');
        if (row) this.select(row.dataset.key);
      },

      tableKey(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target.closest('a, button')) return;
        const row = event.target.closest('tr[data-key]');
        if (!row) return;
        event.preventDefault();
        this.select(row.dataset.key);
      },

      // ---- Auswahl ----

      // Wählt eine Zeile (ein zweiter Klick hebt die Auswahl auf); sie hebt den Verbraucher in der Aufteilung hervor und ist im
      // Reiter „Langzeit“ vorgewählt.
      select(key) {
        this.sel = this.sel === key ? null : key;
        writeStore({sel: this.sel});
        this.draw();
      },

      selection() {
        const key = this.sel;
        if (!key) return null;
        if (key.startsWith('c:') && this.cons) {
          const item = consumerRows(this.cons).find((row) => row.key === key.slice(2));
          return item ? {name: item.name, color: item.color, hatch: item.rest} : null;
        }
        if (key.startsWith('r:') && this.table) {
          const row = this.table.rows.find((r) => r.key === key.slice(2));
          return row ? {name: row.label, color: 'var(--accent-line)', hatch: false} : null;
        }
        return null;
      },

      pickKey() {
        return this.sel && this.sel.startsWith('c:') ? this.sel.slice(2) : null;
      },

      goLongterm() {
        window.dispatchEvent(new CustomEvent('energie-goto', {detail: 'long'}));
      },

      // ---- Aufteilung des Verbrauchs ----

      setShView(view) {
        this.shView = view;
        writeStore({shView: view});
        this.drawShares();
      },

      setSharesMode(mode) {
        this.sharesMode = mode;
        writeStore({sharesMode: mode});
        this.drawShares();
      },

      sharesNote() {
        const base = this.shView === 'cols'
          ? t('Anteil jedes Verbrauchers am Verbrauch laut Bilanz, je Spalte der Tabelle; dünn darunter das Vorjahr.')
          : t('Anteil jedes Verbrauchers am Verbrauch des Jahres laut Bilanz, je Jahr im Archiv. Das laufende Jahr (*) und das erste Jahr des Archivs sind nicht vollständig.');
        return `${base} ${this.sharesMode === 'pct' ? t('Jeder Balken zeigt 100 %.') : t('Alle Balken im selben Maßstab in kWh.')} `
          + t('Rechts die Summe und wie viel davon eigene Zähler erfassen. Schraffiert: „Übrige (Grundlast)“, alles ohne eigenen Zähler. Ein Klick auf einen Verbraucher hebt ihn hervor und wählt ihn in der Tabelle aus.');
      },

      drawShares() {
        const data = this.cons;
        const host = this.$refs.shares;
        if (!data || !host) return;
        const pick = this.pickKey();
        if (this.shView === 'years') {
          if (!data.years) return;
          const years = this.yearLimit ? data.years.slice(0, this.yearLimit) : data.years;
          host.innerHTML = sharesYearsHtml(data, years, this.sharesMode, pick);
        } else {
          host.innerHTML = sharesColsHtml(data, compareColumns({table: this.table, cons: data, months: this.months, columns: this.cols()}), this.sharesMode, pick, this.vorjahr);
        }
        host.classList.toggle('has-hl', !!pick);
        requestAnimationFrame(() => fitShareLabels(host));
        if (this.$refs.sharesLegend) this.$refs.sharesLegend.innerHTML = sharesLegendHtml(data, pick);
      },

      // Hervorhebung eines Verbrauchers in allen Zeilen (Zeiger darüber vorübergehend, Klick wählt aus).
      highlight(key) {
        const host = this.$refs.shares;
        if (!host) return;
        host.classList.toggle('has-hl', !!key);
        host.querySelectorAll('.wt-vs-seg').forEach((seg) => seg.classList.toggle('is-hl', seg.dataset.key === key));
      },

      sharesHover(event) {
        const seg = event.target.closest('.wt-vs-seg');
        if (seg) this.highlight(seg.dataset.key);
      },

      sharesClick(event) {
        const target = event.target.closest('[data-key]');
        if (!target) return;
        this.select(`c:${target.dataset.key}`);
      },

      // Anzahl der Jahre im Archiv (0 = noch nicht geladen); die Jahre-Auswahl der Ansicht „Jahre“ erscheint ab mehr als fünf.
      yearCount() {
        return this.cons && this.cons.years ? this.cons.years.length : 0;
      },

      yearOptions() {
        return yearChoices(this.yearCount());
      },

      showYearChoice() {
        return this.shView === 'years' && this.yearCount() > YEAR_STEP;
      },

      setYearLimit(value) {
        this.yearLimit = value;
        writeStore({consYearLimit: value});
        this.drawShares();
      },
    };
  };

  // ---- Reiter „Langzeit“ -------------------------------------------------------------------------
  // hidden: im Setup abgewählte Karten (Schlüssel wie energie_langzeit.CARD_BLOCKS); deren Daten werden gar nicht erst geladen.
  // hasConsumers: Verbraucher der obersten Ebene eingerichtet (dann stehen sie neben der Bilanz in der Auswahl der Größe).
  window.energieLangzeit = function energieLangzeit(hidden = [], hasConsumers = false) {
    const shown = (key) => !hidden.includes(key);
    const withConsumers = hasConsumers && shown('verbraucher');
    const withMonths = shown('matrix') || shown('monatsvergleich');
    return {
      opened: false,
      months: null,
      cons: null,
      monthsError: '',
      records: null,
      recordsError: '',
      // Gewählte Größe: „r:<Rolle>“ (Bilanz) oder „c:<Verbraucher>“; die Auswahl im Reiter „Vergleich“ gilt auch hier.
      sizeSel: 'r:erzeugung',
      // Darstellung der Monatswerte: 'tab' (Jahre × Monate) oder 'line' (eine Linie je Jahr).
      lzView: shown('matrix') ? 'tab' : 'line',
      band: true,
      yearLimit: 0,
      ktMode: 'full',
      ktDialog: {title: '', sub: '', html: '', error: ''},
      _kenntageSeq: 0,

      init() {
        const saved = readStore();
        this.yearLimit = storedYearLimit(saved.yearLimit);
        if (saved.ktMode === 'full' || saved.ktMode === 'ytd') this.ktMode = saved.ktMode;
        if ((saved.lzView === 'tab' && shown('matrix')) || (saved.lzView === 'line' && shown('monatsvergleich'))) this.lzView = saved.lzView;
        if (typeof saved.sizeSel === 'string') this.sizeSel = saved.sizeSel;
        // Geladen wird erst beim ersten Öffnen des Reiters: Die Übersicht ist die Startansicht.
        onFirstShow('long', this, () => {
          this.takeSelection();
          if (withMonths) this.loadMonths();
          if (withMonths && withConsumers) this.loadConsumers();
          if (shown('rekorde') || shown('kenntage')) this.loadRecords();
        });
      },

      // Die Zeile, die im Reiter „Vergleich“ ausgewählt wurde, ist hier vorgewählt.
      takeSelection() {
        const fromTable = readStore().sel;
        if (typeof fromTable === 'string' && fromTable !== this.sizeSel) {
          this.sizeSel = fromTable;
          this.$nextTick(() => this.drawSize());
        }
      },

      onReshow() {
        this.takeSelection();
      },

      // Anzahl der Jahre im Archiv (0 = noch nicht geladen); die Jahre-Auswahl erscheint erst ab mehr als fünf.
      yearCount() {
        const m = this.months ? this.months.years.length : 0;
        const c = this.cons && this.cons.years ? this.cons.years.length : 0;
        const k = this.records && this.records.kenntage ? this.records.kenntage.years.length : 0;
        return Math.max(m, c, k);
      },

      yearOptions() {
        return yearChoices(this.yearCount());
      },

      showYearChoice() {
        return this.yearCount() > YEAR_STEP;
      },

      // Gilt für Kenntage und die Monatswerte, damit alle dieselben Jahre und dieselbe Basis für Ø zeigen.
      setYearLimit(value) {
        this.yearLimit = value;
        writeStore({yearLimit: value});
        this.drawSize();
        this.drawKenntage();
      },

      // ---- Monatswerte einer Größe: Tabelle „Jahre × Monate“ oder Linien ----

      // Größen der Auswahl: die Rollen der Bilanz (Erzeugung, Einspeisung, Netzbezug, Verbrauch) und die Verbraucher.
      balanceOptions() {
        return this.months ? this.months.roles.map((role) => ({key: `r:${role.key}`, label: role.label})) : [];
      },

      consumerOptions() {
        return this.cons ? consumerRows(this.cons).map((item) => ({key: `c:${item.key}`, label: item.name})) : [];
      },

      // Die gewählte Größe als Rolle für Matrix und Diagramm (values, flags, partial_start, better) samt Jahren; null ohne Daten.
      size() {
        const key = this.sizeSel || '';
        if (key.startsWith('c:') && this.cons && this.cons.years) {
          const item = consumerRows(this.cons).find((row) => row.key === key.slice(2));
          if (item && item.values) {
            const years = this.yearLimit ? this.cons.years.slice(0, this.yearLimit) : this.cons.years;
            const role = {label: item.name, values: item.values, flags: item.flags || {}, partial_start: item.partial_start, better: 'down', unchecked: item.unchecked || 0};
            const data = this.cons;
            const href = (year, month) => {
              if (!item.entity_id) return '';
              const back = (data.current.year - year) * 12 + (data.current.month - month);
              return `entities/${encodeURIComponent(item.entity_id)}?range=month&offset=${-back}`;
            };
            return {m: {years, current: this.cons.current}, role, href, consumer: true};
          }
        }
        if (this.months) {
          const roles = this.months.roles;
          const role = roles.find((r) => `r:${r.key}` === key) || roles[0] || null;
          if (role) {
            const years = this.yearLimit ? this.months.years.slice(0, this.yearLimit) : this.months.years;
            const href = (year, month) => this.matrixLink(role, year, month);
            return {m: {...this.months, years}, role, href, consumer: false};
          }
        }
        return null;
      },

      // Wählbar ist eine Größe nur, wenn es sie gibt; sonst die erste der Bilanz.
      selectedKey() {
        const keys = [...this.balanceOptions(), ...this.consumerOptions()].map((option) => option.key);
        return keys.includes(this.sizeSel) ? this.sizeSel : (keys[0] || this.sizeSel);
      },

      pickSize(key) {
        this.sizeSel = key;
        writeStore({sizeSel: key});
        this.drawSize();
      },

      sizeTitle() {
        const size = this.size();
        return t('{name} je Monat und Jahr', {name: size ? size.role.label : t('Monatswerte')});
      },

      setView(view) {
        this.lzView = view;
        writeStore({lzView: view});
        this.$nextTick(() => this.drawSize());
      },

      showView(view) {
        return view === 'tab' ? shown('matrix') : shown('monatsvergleich');
      },

      showSizeCard() {
        return !!(this.months || (this.cons && this.cons.years));
      },

      drawSize() {
        if (this.lzView === 'tab') this.drawMatrix();
        else this.drawMonthly();
      },

      // Rekorde und Kenntage je Jahr: einmal je Seitenaufruf, sie hängen nicht am Zeitraum der Tabelle.
      async loadRecords() {
        this.recordsError = '';
        try {
          const res = await fetch('energiedashboard/langzeit/records');
          if (!res.ok) throw new Error(String(res.status));
          this.records = await res.json();
          this.$nextTick(() => this.drawKenntage());
        } catch (e) {
          this.recordsError = t('Rekorde und Kenntage konnten nicht geladen werden.');
        }
      },

      recordsNote() {
        return t('Höchste Tageswerte seit Beginn des Archivs. Tage direkt nach einem fehlenden Tag zählen nicht mit, weil dort die Differenz mehrerer Tage steht und einen Schein-Rekord ergäbe. Bei Gleichstand gilt der frühere Tag.');
      },

      recordAccent(key) {
        return {erzeugung: 'var(--chart-7)', einspeisung: 'var(--chart-4)', verbrauch: 'var(--chart-2)', netzbezug: 'var(--chart-3)'}[key] || 'var(--chart-8)';
      },

      recordValue(record) {
        const text = energy(record.value);
        const [value, unit] = text.split('\u00a0');
        return `${escapeHtml(value)}<small>${escapeHtml(unit)}</small>`;
      },

      recordDate(record) {
        return fmtDate(formats.dmy, Date.parse(record.date + 'T12:00:00Z') / 1000);
      },

      recordLink(record) {
        return `entities/${encodeURIComponent(record.entity_id)}?range=day&offset=${record.offset}`;
      },

      kenntageNote() {
        return t('Tage je Kalenderjahr mit einer Eigenschaft, die Regel steht unter dem Titel. Die Schwellen stellst du im Setup ein. Es zählen nur Tage mit allen Werten, ohne den Tag nach einem fehlenden Tag.');
      },

      ktRange() {
        const kt = this.records && this.records.kenntage;
        return kt ? kenntageRange(kt, this.ktMode === 'ytd') : '';
      },

      setKtMode(mode) {
        this.ktMode = mode;
        writeStore({ktMode: mode});
        this.drawKenntage();
      },

      // Ein Feld je Kenntag, darin ein Balken je Jahr (Länge = Anteil an den ausgewerteten Tagen, Maßstab je Feld).
      drawKenntage() {
        const r = this.records;
        const host = this.$refs.kenntage;
        if (!r || !r.kenntage || !host) return;
        host.innerHTML = energyKenntage(r.kenntage, {
          ytd: this.ktMode === 'ytd', years: this.yearLimit, current: window.ServerTime.parts(Date.now() / 1000).year, interactive: true,
        });
      },

      kenntageClick(event) {
        const row = event.target.closest('.wt-kt-row');
        if (row) this.openKenntage(row.dataset.field, Number(row.dataset.year));
      },

      // Jahreskalender eines Kenntags: 12 Monate × 31 Tage, die Treffer sind gefüllt, ein Klick öffnet den Tag.
      async openKenntage(field, year) {
        const kt = this.records.kenntage;
        const dialog = this.$refs.kenntageDialog;
        this.ktDialog = {title: `${KENNTAG_LABELS()[field]} · ${year}`, sub: kenntagRule(field, kt.thresholds), html: '', error: ''};
        dialog.showModal();
        const seq = ++this._kenntageSeq;
        try {
          const res = await fetch(`energiedashboard/langzeit/kenntage?field=${encodeURIComponent(field)}&year=${year}`);
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._kenntageSeq) return;  // ein anderes Jahr wurde inzwischen geöffnet
          const byDate = new Map(data.days.map((d) => [d.date, d]));
          const since = this.records.since;
          const cutoff = this.ktMode === 'ytd' ? kt.today.slice(5) : null;
          const iso = (month, day) => `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
          const gapLabel = t('nicht ausgewertet');
          let hits = 0, days = 0;
          let html = `<thead><tr><th></th>${Array.from({length: 31}, (_x, i) => `<th>${i + 1}</th>`).join('')}</tr></thead><tbody>`;
          for (let month = 1; month <= 12; month++) {
            html += `<tr><th>${escapeHtml(monthName(month, 'short'))}</th>`;
            const last = new Date(year, month, 0).getDate();
            for (let day = 1; day <= 31; day++) {
              if (day > last) { html += '<td class="none"></td>'; continue; }
              const key = iso(month, day);
              const dateText = fmtDate(formats.dmy, Date.parse(key + 'T12:00:00Z') / 1000);
              const beyond = key < since || key > kt.today || (cutoff && key.slice(5) > cutoff);
              if (beyond) { html += `<td class="off" data-tooltip-fixed="${escapeHtml(dateText)}"></td>`; continue; }
              const entry = byDate.get(key);
              if (!entry) { html += `<td class="gap" data-tooltip-fixed="${escapeHtml(dateText + ' · ' + gapLabel)}"></td>`; continue; }
              days++;
              if (!entry.hit) { html += `<td data-tooltip-fixed="${escapeHtml(dateText)}"></td>`; continue; }
              hits++;
              const tip = `${dateText} · ${kenntagValues(field, entry.values, data.average, kt.kwp, kt.capacity)}`;
              html += `<td class="hit" data-tooltip-fixed="${escapeHtml(tip)}"><a href="energiedashboard?date=${key}" aria-label="${escapeHtml(tip)}"></a></td>`;
            }
            html += '</tr>';
          }
          this.ktDialog.html = html + '</tbody>';
          this.ktDialog.sub = `${kenntagRule(field, kt.thresholds)} · ${t('{hits} von {days} ausgewerteten Tagen', {hits: num(hits, 0), days: num(days, 0)})}`;
        } catch (e) {
          if (seq === this._kenntageSeq) this.ktDialog.error = t('Die Tage konnten nicht geladen werden.');
        }
      },

      async loadMonths() {
        this.monthsError = '';
        try {
          const res = await fetch('energiedashboard/langzeit/months');
          if (!res.ok) throw new Error(String(res.status));
          this.months = await res.json();
          // Erst zeichnen, wenn Alpine die Karte sichtbar gemacht hat (x-show).
          this.$nextTick(() => this.drawSize());
        } catch (e) {
          this.monthsError = t('Die Monatswerte konnten nicht geladen werden.');
        }
      },

      // Monatswerte je Verbraucher über alle Jahre (ohne die Zeiträume des Vergleichs).
      async loadConsumers() {
        try {
          const res = await fetch('energiedashboard/langzeit/consumers', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({months: true, periods: []}),
          });
          if (!res.ok) throw new Error(String(res.status));
          this.cons = await res.json();
          this.$nextTick(() => this.drawSize());
        } catch (e) {
          this.monthsError = t('Die Verbraucher konnten nicht geladen werden.');
        }
      },

      sizeNote() {
        const size = this.size();
        let text = this.lzView === 'tab'
          ? t('Oben der Monatswert in kWh, darunter die Abweichung vom Mittel dieses Monats über die abgeschlossenen Jahre im Archiv. Die letzte Zeile ist das Mittel. Grün ist günstig, rot ungünstig; bei Verbrauchern ist mehr Verbrauch rot.')
          : t('Monatswerte je Jahr in kWh. Die gestrichelte Linie ist das Mittel der abgeschlossenen Jahre, die Fläche ihre Spanne. Der laufende Monat steht nur im Tooltip.');
        text += ' ' + t('Ein Klick auf ein Feld öffnet den Monat der Entität (bei Rollen mit genau einer Entität). Das Dreieck oben rechts markiert Messlücken oder Zählersprünge, der Tooltip nennt sie. * laufendes Jahr, der laufende Monat zählt im Mittel nicht mit.');
        if (size && size.role.unchecked) {
          text += ' ' + t('{n} Entität(en) sind noch nicht auf Lücken und Zählersprünge geprüft.', {n: size.role.unchecked});
        }
        return text;
      },

      drawMonthly() {
        const size = this.size();
        const element = this.$refs.monthlyChart;
        if (!size || !element) return;
        const chart = chartFor('monthly', element);
        chart.setOption(energyMonthly(size.m, size.role, {
          colors: palette(), band: this.band, narrow: element.clientWidth < 520, font: scaledFont,
        }), true);
        requestAnimationFrame(() => chart.resize());
      },

      // Link auf den Monat der Entität (nur wenn die Rolle genau eine hat); sonst kein Link.
      matrixLink(role, year, month) {
        if (!role.entity_id) return '';
        const back = (this.months.current.year - year) * 12 + (this.months.current.month - month);
        return `entities/${encodeURIComponent(role.entity_id)}?range=month&offset=${-back}`;
      },

      drawMatrix() {
        const size = this.size();
        const table = this.$refs.matrixTable;
        if (!size || !table) return;
        const colors = palette();
        table.innerHTML = energyMatrix(size.m, size.role, {colors, href: size.href});
        const scale = this.$refs.matrixScale;
        if (scale) scale.style.background = `linear-gradient(90deg, ${colors.bad}, ${colors.base}, ${colors.good})`;
      },
    };
  };
})();
