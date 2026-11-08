// Energiedashboard: die Vergleichstabelle der Übersicht („Zeiträume im Vergleich“, energieVergleich), der Reiter
// „Verbraucher“ (energieVerbraucher) und der Reiter „Langzeit“ (energieLangzeit). Der Server rechnet alle Summen
// (POST energiedashboard/langzeit/table, /consumers, /records, /months); dieses Skript formatiert und zeichnet nur.
// Aufbau und Bedienung wie das Wetterdashboard (wetter.js). Kalenderangaben laufen über ServerTime in der Zeitzone des Servers.
// Die drei Komponenten teilen sich die Hilfsfunktionen in diesem Block und den Browser-Speicher (STORE_KEY).
(function () {
  const COLUMN_KEYS = ['heute', 'gestern', 'monat', 'vormonat', 'jahr'];
  const DEFAULT_COLUMNS = ['monat', 'vormonat', 'jahr'];
  const STORE_KEY = 'zeitarchiv.energie.langzeit';
  const LOCALE = window.ZA_FORMAT;
  const ZT = window.ServerTime;

  const {formats, fmtDate, rangeText, energy, columnName, delta: cellDelta, comparisonHead, energyComparison: renderTable, kenntagRule, kenntagLabels: KENNTAG_LABELS, energyKenntage, energyChanges, changesRange, kenntageRange} = window.ElementTableRender;
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

  // ---- Ansicht „Verbraucher“ (Roadmap 1.49) -----------------------------------------------------
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
  function consumerRank(data, year, limit = 0) {
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
    return {year, inWindow, prevYear, entries, total, totalPrev, measured, measuredPrev, limit};
  }

  // Änderung zum Vorjahr als Pfeil mit Prozent (unit 'pct') oder kWh (unit 'kwh'), darunter die kWh (withKwh) oder der
  // Grund für „–“.
  function rankDelta(entry, {withKwh = true, unit = 'pct'} = {}) {
    if (entry.delta == null) {
      return `<span class="wt-dl">–</span>${entry.note ? `<small>${escapeHtml(entry.note)}</small>` : ''}`;
    }
    const percent = entry.prev ? entry.delta / entry.prev * 100 : null;
    const byPct = unit === 'pct' && percent != null;
    const zero = byPct ? Math.round(Math.abs(percent)) === 0 : Math.round(Math.abs(entry.delta) * 10) === 0;
    const cls = zero ? '' : (entry.delta > 0 ? ' wt-dl-bad' : ' wt-dl-good');
    const sign = zero ? '±' : (entry.delta > 0 ? '+' : '−');
    const kwhAbs = `${sign}${kwhText(Math.abs(entry.delta))} kWh`;
    let head;
    if (unit === 'kwh') head = zero ? '± 0 kWh' : `${entry.delta > 0 ? '▲' : '▼'} ${kwhAbs}`;
    else {
      head = zero ? '± 0 %' : (entry.delta > 0 ? '▲' : '▼');
      if (!zero && percent != null) head += ` ${sign}${num(Math.abs(percent), 0)} %`;
    }
    const kwh = withKwh ? `<small>${escapeHtml(kwhAbs)}</small>` : '';
    return `<span class="wt-dl${cls}">${escapeHtml(head)}</span>${kwh}`;
  }

  // Die Änderung als kurzer Text für Tooltips („+120 kWh“).
  const deltaKwh = (delta) => `${delta > 0 ? '+' : delta < 0 ? '−' : '±'}${kwhText(Math.abs(delta))} kWh`;

  // Jahre im Verlauf der Rangliste (ältestes zuerst); weniger als zwei zeigen keinen Verlauf.
  function sparkYears(data, limit = 0) {
    const shown = limit ? (data.years || []).slice(0, limit) : (data.years || []);
    return shown.length < 2 ? [] : [...shown].reverse();
  }

  // Jahressummen als kleine Balken, ältestes Jahr links, das gewählte kräftig. Die Balken teilen sich die Spaltenbreite,
  // die Spalte wächst mit der Zahl der Jahre (--vr-years), der Anteilsbalken daneben wird dafür kürzer.
  function rankSpark(data, item, color, year, limit = 0) {
    const years = sparkYears(data, limit);
    if (!years.length) return '';
    const values = years.map((y) => yearValue(item, y));
    const top = Math.max(...values.map((v) => v || 0)) || 1;
    const tip = years.map((y, i) => `${y}: ${values[i] == null ? '–' : energy(values[i])}`).join(' · ');
    const bars = years.map((y, i) => {
      const v = values[i];
      if (v == null) return '<i class="wt-vr-sp-none"></i>';
      const pct = Math.max(6, v / top * 100);
      return `<i style="height:${pct.toFixed(1)}%;background:${color};opacity:${y === year ? 1 : 0.38}"></i>`;
    }).join('');
    return `<span class="wt-vr-sp" role="img" aria-label="${escapeHtml(tip)}" title="${escapeHtml(tip)}">${bars}</span>`;
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

  // Sortierwerte der Rangliste; kWh und Anteil sortieren gleich, die Änderung mit Vorzeichen (Anstieg oben bei 'desc').
  const RANK_SORT = {
    name: (e) => e.item.name,
    kwh: (e) => e.cur,
    chg: (e) => e.delta,
  };

  // sort: {col: 'name'|'kwh'|'chg', dir}. „Übrige (Grundlast)“ und die Bilanz bleiben immer unten.
  function rankHtml(data, rank, sort, selected) {
    const list = sortByValue(rank.entries.filter((e) => !e.item.rest), RANK_SORT[sort.col], sort.dir);
    const rest = rank.entries.filter((e) => e.item.rest);
    const rows = [...list, ...rest];
    const total = rank.total;
    const share = (value) => (total ? value / total * 100 : null);
    const top = Math.max(0, ...rows.map((e) => share(e.cur) || 0)) || 1;
    const head = (col, label, cls, aria) => {
      const on = sort.col === col;
      return `<span class="${cls} wt-vt-yh${on ? ' is-sorted' : ''}" role="columnheader" aria-sort="${ariaSort(on, sort.dir)}">`
        + `${sortHead(escapeHtml(label), on, sort.dir, `data-sort="${col}"`, aria)}</span>`;
    };
    let html = `<div class="wt-vr-row wt-vr-head">${head('name', t('Verbraucher'), 'wt-vr-name', t('Nach Name sortieren'))}<span></span>`
      + head('kwh', t('kWh'), 'wt-vr-num wt-vr-kwh', t('Nach Verbrauch sortieren'))
      + head('kwh', t('Anteil'), 'wt-vr-num wt-vr-pct', t('Nach Verbrauch sortieren'))
      + head('chg', t('Vorjahr'), 'wt-vr-num wt-vr-delta', t('Nach Änderung zum Vorjahr sortieren'))
      + `<span class="wt-vr-spark">${escapeHtml(t('Verlauf'))}</span></div>`;
    for (const entry of rows) {
      const item = entry.item;
      const part = share(entry.cur);
      const offset = rank.year - data.current.year;
      const name = item.entity_id
        ? `<a href="entities/${encodeURIComponent(item.entity_id)}?range=year&offset=${offset}">${escapeHtml(item.name)}</a>`
        : `<span>${escapeHtml(item.name)}</span>`;
      const warn = entry.flags.length
        ? `<span class="wt-vr-warn" data-tooltip-fixed="${escapeHtml(entry.flags.join('\n'))}" role="img" aria-label="${escapeHtml(t('Hinweis: {text}', {text: entry.flags.join('; ')}))}"></span>` : '';
      const from = item.late && item.first_ts != null && ZT.parts(item.first_ts).year >= rank.prevYear
        ? `<span class="wt-vr-from">${escapeHtml(t('ab {date}', {date: monthYear(item.first_ts)}))}</span>` : '';
      const hatch = item.rest ? ' wt-vr-hatch' : '';
      // Ein Klick auf die Zeile zeigt die Monate des Verbrauchers in der Matrix darunter.
      const label = escapeHtml(t('{name}: Monate unten zeigen', {name: item.name}));
      html += `<div class="wt-vr-row wt-vr-pick${item.rest ? ' wt-vr-rest' : ''}${selected === item.key ? ' is-sel' : ''}"`
        + ` data-key="${escapeHtml(item.key)}" tabindex="0" role="button" aria-label="${label}">`
        + `<span class="wt-vr-name"><i class="wt-vr-dot${hatch}" style="background:${item.color}"></i>${name}${warn}${from}</span>`
        + `<span class="wt-vr-bar"><i class="${hatch.trim()}" style="width:${part == null ? 0 : (part / top * 100).toFixed(1)}%;background:${item.color}"></i></span>`
        + `<span class="wt-vr-num wt-vr-kwh">${escapeHtml(kwhText(entry.cur))}</span>`
        + `<span class="wt-vr-num wt-vr-pct">${part == null ? '–' : escapeHtml(num(part, 1) + ' %')}</span>`
        + `<span class="wt-vr-num wt-vr-delta">${rankDelta(entry)}</span>`
        + `<span class="wt-vr-spark">${rankSpark(data, item, item.color, rank.year, rank.limit)}</span></div>`;
    }
    if (total != null) {
      const sum = {delta: rank.totalPrev != null ? total - rank.totalPrev : null, prev: rank.totalPrev, note: ''};
      html += `<div class="wt-vr-row wt-vr-sum"><span class="wt-vr-name">${escapeHtml(t('Verbrauch (Bilanz)'))}</span><span></span>`
        + `<span class="wt-vr-num wt-vr-kwh">${escapeHtml(kwhText(total))}</span><span class="wt-vr-num wt-vr-pct">100 %</span>`
        + `<span class="wt-vr-num wt-vr-delta">${rankDelta(sum)}</span><span class="wt-vr-spark"></span></div>`;
    }
    return html;
  }

  // „Gemessen: x % des Verbrauchs“: wie viel davon eigene Zähler erfassen, mit dem Vorjahr zum Vergleich.
  function coverHtml(rank) {
    if (!rank.total) return '';
    const cover = Math.min(100, rank.measured / rank.total * 100);
    let text = `${escapeHtml(t('Gemessen:'))} <b>${escapeHtml(num(cover, 0))} %</b> `
      + escapeHtml(t('des Verbrauchs ({measured} von {total} kWh)', {measured: kwhText(rank.measured), total: kwhText(rank.total)}));
    if (rank.totalPrev) text += ` · ${escapeHtml(t('Vorjahr {percent} %', {percent: num(Math.min(100, rank.measuredPrev / rank.totalPrev * 100), 0)}))}`;
    return `<span>${text}</span><span class="wt-vr-cover-bar" aria-hidden="true"><i style="width:${cover.toFixed(1)}%"></i></span>`;
  }

  // Anteile je Jahr: ein gestapelter Balken je Jahr (neuestes oben), Abschnitte in Setup-Reihenfolge, „Übrige“ zuletzt.
  // mode 'pct': jeder Balken volle Breite; 'kwh': alle Balken im Maßstab des größten Jahres.
  function sharesHtml(data, years, mode, pick) {
    const rows = consumerRows(data);
    const current = data.current.year;
    const rest = data.rest;
    const per = years.map((year) => {
      const items = rows.map((item) => ({item, value: yearValue(item, year)})).filter((e) => e.value != null && e.value > 0);
      const total = yearValue({year_sums: data.total ? data.total.year_sums : null}, year);
      const sum = items.reduce((a, e) => a + e.value, 0);
      const measured = items.filter((e) => !e.item.rest).reduce((a, e) => a + e.value, 0);
      return {year, items, total, base: Math.max(total || 0, sum), measured};
    }).filter((d) => d.base > 0);
    const peak = Math.max(0, ...per.map((d) => d.base)) || 1;
    let html = '';
    for (const d of per) {
      let sub = '';
      if (d.year === current) sub = t('bis {date}', {date: fmtDate(formats.dm, data.now)});
      else if (rest.first_ts != null && !coversYear(rest, d.year) && ZT.parts(rest.first_ts).year === d.year) {
        sub = t('ab {date}', {date: fmtDate(formats.dm, rest.first_ts)});
      }
      const width = mode === 'kwh' ? ` style="width:${(d.base / peak * 100).toFixed(1)}%"` : '';
      html += `<div class="wt-vs-row"><span class="wt-vs-year">${d.year}${d.year === current ? '*' : ''}${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</span>`
        + `<div class="wt-vs-trackwrap"><div class="wt-vs-track"${width}>`;
      for (const e of d.items) {
        const pct = e.value / d.base * 100;
        let label = '';
        if (mode === 'pct' && pct >= 7) label = `${num(pct, 0)} %`;
        if (mode === 'kwh' && pct >= 7 && e.value / peak * 100 >= 9) label = kwhText(e.value);
        const tip = `${e.item.name} ${d.year}: ${energy(e.value)} · ${num(pct, 1)} %`;
        const cls = `wt-vs-seg${e.item.rest ? ' wt-vr-hatch' : ''}${pick === e.item.key ? ' is-hl' : ''}`;
        html += `<span class="${cls}" data-key="${escapeHtml(e.item.key)}" style="width:${pct.toFixed(2)}%;background:${e.item.color}" data-tooltip-fixed="${escapeHtml(tip)}">`
          + (label ? (e.item.rest ? `<b>${escapeHtml(label)}</b>` : escapeHtml(label)) : '') + '</span>';
      }
      const covered = d.total ? ` · ${escapeHtml(t('{percent} % gemessen', {percent: num(Math.min(100, d.measured / d.total * 100), 0)}))}` : '';
      html += `</div></div><span class="wt-vs-total"><b>${escapeHtml(d.total != null ? energy(d.total) : '–')}</b>${covered}</span></div>`;
    }
    return html;
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

  // Spalten der Tabelle als Objekte für den gemeinsamen Kopf (comparisonHead): ein Zeitraum mit seinen Fenstern vom Server, ein
  // einzelnes Jahr mit „Jahr“ und der Jahreszahl; das erste, nicht ganz erfasste Jahr des Archivs trägt ein Sternchen.
  // Spalten ohne Daten vom Server entfallen.
  function tableColumns(data, columns) {
    const firstYear = partialFirstYear(data);
    const out = [];
    for (const id of columns) {
      if (isPeriod(id)) {
        const period = periodOf(data, id);
        if (period) out.push({id, key: id, cur: period.cur, vj: period.vj});
        continue;
      }
      const end = yearStart(id + 1);
      const col = {id, key: 'jahr', cur: {start: yearStart(id), end}, vj: {start: yearStart(id - 1), end: yearStart(id)}};
      if (id === firstYear) {
        col.sub = `${id}*`;
        col.tip = rangeText(data.rest.first_ts, end);
      }
      out.push(col);
    }
    return out;
  }

  // Tabelle „Zeiträume im Vergleich je Verbraucher“ im Aufbau der Vergleichstabelle (Wetter, Übersicht): je Spalte der Zeitraum
  // und, wenn vorjahr gilt, daneben sein Vorjahr. Jede Spalte ist die Rangliste ihres Zeitraums (periodRank) oder Jahres
  // (consumerRank). columns: Zeiträume (Schlüssel) und Jahre (Zahlen) in Anzeigereihenfolge; sort: {col, dir}; opts.main 'pct' oder
  // 'kwh' bestimmt die Hauptzahl, der andere Wert steht klein darunter; opts.vorjahr schaltet die Vorjahr-Spalten, opts.diff die
  // Änderung unter dem Wert.
  function sharesTableHtml(data, columns, {main, vorjahr, diff}, sort, selected) {
    const cols = tableColumns(data, columns);
    const ranks = {};
    for (const col of cols) ranks[col.id] = isPeriod(col.id) ? periodRank(data, col.id) : consumerRank(data, col.id);
    const entryOf = (id, key) => ranks[id].entries.find((e) => e.item.key === key) || null;
    const label = (id) => (isPeriod(id) ? columnName(id) : String(id));
    const years = columns.filter(Number.isInteger);
    const oldestYear = years.length ? Math.min(...years) : data.window.year;
    const pct = main === 'pct';
    const pctText = (value) => `${num(value, 1)} %`;
    const rows = consumerRows(data);
    const list = rows.filter((item) => !item.rest);
    // Ohne Wert in der sortierten Spalte ans Ende.
    sortByValue(list, (item) => (entryOf(sort.col, item.key) || {cur: null}).cur, sort.dir);
    const sorted = (id) => (id === sort.col ? ' wt-vt-sorted' : '');

    // Kopf wie in der Summen-Tabelle; der Titel ist der Sortierknopf der Spalte.
    for (const col of cols) {
      const on = col.id === sort.col;
      col.headClass = ` wt-vt-yh${on ? ' is-sorted' : ''}`;
      col.headAttrs = ` aria-sort="${ariaSort(on, sort.dir)}"`;
    }
    const head = comparisonHead({columns: cols, vorjahr, at: null, now: data.now}, {
      corner: {title: escapeHtml(t('Verbraucher')), sub: escapeHtml(pct ? t('Anteil, darunter kWh') : t('kWh, darunter Anteil'))},
      titleHtml: (col, title) => sortHead(title, col.id === sort.col, sort.dir, `data-col="${col.id}"`, t('Nach {year} sortieren', {year: label(col.id)})),
    });
    let html = `${head}<tbody>`;

    // Hauptwert und kleiner darunter der andere Wert; ohne Wert ein Strich (und optional der Grund).
    const valueHtml = (kwh, share, warn = '') => {
      if (kwh == null) return '<span class="wt-v">–</span>';
      const shareText = share == null ? '–' : pctText(share);
      return `<span class="wt-v">${warn}${escapeHtml(pct ? shareText : energy(kwh))}</span>`
        + `<span class="wt-vt-sub">${escapeHtml(pct ? energy(kwh) : shareText)}</span>`;
    };
    const naHtml = (cls, tip, note = '') => `<td class="${cls} wt-na"${tip ? ` data-tooltip-fixed="${escapeHtml(tip)}"` : ''}>`
      + `<span class="wt-v">–</span>${note ? `<small>${escapeHtml(note)}</small>` : ''}</td>`;

    for (const item of [...list, ...rows.filter((row) => row.rest)]) {
      const name = item.entity_id
        ? `<a href="entities/${encodeURIComponent(item.entity_id)}?range=year&offset=0">${escapeHtml(item.name)}</a>`
        : `<span>${escapeHtml(item.name)}</span>`;
      const from = item.late && item.first_ts != null && ZT.parts(item.first_ts).year >= oldestYear
        ? `<span class="wt-vr-from">${escapeHtml(t('ab {date}', {date: monthYear(item.first_ts)}))}</span>` : '';
      const hatch = item.rest ? ' wt-vr-hatch' : '';
      html += `<tr class="wt-vt-row${item.rest ? ' wt-vt-rest' : ''}${selected === item.key ? ' is-sel' : ''}" data-key="${escapeHtml(item.key)}"`
        + ` tabindex="0" aria-label="${escapeHtml(t('{name}: Monate unten zeigen', {name: item.name}))}">`
        + `<th scope="row"><span class="wt-vt-name"><i class="wt-vr-dot${hatch}" style="background:${item.color}"></i><span>${name}${from}</span></span></th>`;
      for (const col of cols) {
        const entry = entryOf(col.id, item.key);
        if (!entry) {
          const missing = t('{name} {year}: noch nicht erfasst', {name: item.name, year: label(col.id)});
          html += naHtml(`wt-sep${sorted(col.id)}`, missing);
          if (vorjahr) html += naHtml(`wt-vj${sorted(col.id)}`, '');
          continue;
        }
        const rank = ranks[col.id];
        const share = rank.total ? entry.cur / rank.total * 100 : null;
        const shareText = share == null ? '–' : pctText(share);
        const warn = entry.flags.length
          ? `<span class="wt-vr-warn" data-tooltip-fixed="${escapeHtml(entry.flags.join('\n'))}" role="img" aria-label="${escapeHtml(t('Hinweis: {text}', {text: entry.flags.join('; ')}))}"></span>` : '';
        let tip = `${item.name} ${label(col.id)}: ${energy(entry.cur)} · ${shareText}`;
        if (entry.delta != null) tip += ` · ${t('Vorjahr {value}', {value: deltaKwh(entry.delta)})}`;
        html += `<td class="wt-sep${sorted(col.id)}" data-tooltip-fixed="${escapeHtml(tip)}">${valueHtml(entry.cur, share, warn)}`
          + `${vorjahr && diff ? cellDelta(entry.cur, entry.prev, CONSUMPTION_ROW) : ''}</td>`;
        if (vorjahr) {
          html += entry.prev == null
            ? naHtml(`wt-vj${sorted(col.id)}`, '', entry.note)
            : `<td class="wt-vj${sorted(col.id)}">${valueHtml(entry.prev, rank.totalPrev ? entry.prev / rank.totalPrev * 100 : null)}</td>`;
        }
      }
      html += '</tr>';
    }

    // Verbrauch laut Bilanz mit seiner Änderung; ohne vergleichbares Vorjahr nennt „Übrige“ den Grund.
    html += `<tr class="wt-vt-sum"><th scope="row">${escapeHtml(t('Verbrauch (Bilanz)'))}</th>`;
    for (const col of cols) {
      const rank = ranks[col.id];
      if (rank.total == null) {
        html += naHtml(`wt-sep${sorted(col.id)}`, '');
        if (vorjahr) html += naHtml(`wt-vj${sorted(col.id)}`, '');
        continue;
      }
      const note = (entryOf(col.id, data.rest.key) || {}).note || '';
      html += `<td class="wt-sep${sorted(col.id)}" data-tooltip-fixed="${escapeHtml(`${t('Verbrauch (Bilanz)')} ${label(col.id)}: ${energy(rank.total)}`)}">`
        + `<span class="wt-v">${escapeHtml(pct ? '100 %' : energy(rank.total))}</span>`
        + `<span class="wt-vt-sub">${escapeHtml(pct ? energy(rank.total) : '100 %')}</span>`
        + `${vorjahr && diff ? cellDelta(rank.total, rank.totalPrev, CONSUMPTION_ROW) : ''}</td>`;
      if (vorjahr) {
        html += rank.totalPrev == null ? naHtml(`wt-vj${sorted(col.id)}`, '', note)
          : `<td class="wt-vj${sorted(col.id)}"><span class="wt-v">${escapeHtml(pct ? '100 %' : energy(rank.totalPrev))}</span>`
            + `<span class="wt-vt-sub">${escapeHtml(pct ? energy(rank.totalPrev) : '100 %')}</span></td>`;
      }
    }
    html += `</tr><tr class="wt-vt-cov"><th scope="row">${escapeHtml(t('Gemessen'))}</th>`;
    for (const col of cols) {
      const rank = ranks[col.id];
      if (!rank.total) {
        html += naHtml(`wt-sep${sorted(col.id)}`, '');
        if (vorjahr) html += naHtml(`wt-vj${sorted(col.id)}`, '');
        continue;
      }
      const tip = t('Eigene Zähler erfassen {measured} von {total} kWh', {measured: kwhText(rank.measured), total: kwhText(rank.total)});
      html += `<td class="wt-sep${sorted(col.id)}" data-tooltip-fixed="${escapeHtml(tip)}"><span class="wt-v">${escapeHtml(`${num(Math.min(100, rank.measured / rank.total * 100), 0)} %`)}</span>`
        + `<span class="wt-vt-sub">${escapeHtml(energy(rank.measured))}</span></td>`;
      if (vorjahr) {
        html += rank.totalPrev && rank.measuredPrev != null
          ? `<td class="wt-vj${sorted(col.id)}"><span class="wt-v">${escapeHtml(`${num(Math.min(100, rank.measuredPrev / rank.totalPrev * 100), 0)} %`)}</span>`
            + `<span class="wt-vt-sub">${escapeHtml(energy(rank.measuredPrev))}</span></td>`
          : naHtml(`wt-vj${sorted(col.id)}`, '');
      }
    }
    return `${html}</tr></tbody>`;
  }

  // Fußzeile der Tabelle: Sortierung, Änderung und das Teiljahr, soweit es in der Tabelle steht.
  function sharesTableFoot(data, columns, sort, diff) {
    const years = columns.filter(Number.isInteger);
    const parts = [
      sort.dir === 'desc' ? t('Sortiert nach {year}, größter zuerst.', {year: isPeriod(sort.col) ? columnName(sort.col) : sort.col})
        : t('Sortiert nach {year}, kleinster zuerst.', {year: isPeriod(sort.col) ? columnName(sort.col) : sort.col}),
      t('Ein Klick auf einen Spaltenkopf sortiert nach dieser Spalte, ein zweiter dreht die Reihenfolge. „Übrige (Grundlast)“ und die Summen stehen immer unten.'),
    ];
    if (diff) {
      parts.push(t('Die Änderung zum Vorjahr ist in kWh gerechnet; rot: mehr Verbrauch, grün: weniger, „–“ mit Grund, wenn das Vorjahr nicht ganz erfasst ist.'));
    }
    const firstYear = partialFirstYear(data);
    if (firstYear != null && years.includes(firstYear)) {
      parts.push(t('* Das Archiv beginnt erst am {date}, {year} ist kein ganzes Jahr.', {date: fmtDate(formats.dmy, data.rest.first_ts), year: firstYear}));
    }
    parts.push(t('Das Dreieck nennt Messlücken, Zählerrückgänge und einen verzerrten Vergleich. Ein Klick auf eine Zeile zeigt die Monate unten.'));
    return parts.join(' ');
  }

  // Jahre-Auswahl der Karten: „Letzte 5“ oder „Alle“, nur ab mehr als fünf Jahren im Archiv. Früher gab es auch „Letzte 10“
  // (gespeichert als 10); das gilt jetzt als „Alle“.
  const YEAR_STEP = 5;
  const storedYearLimit = (value) => (value === YEAR_STEP ? YEAR_STEP : 0);
  const yearChoices = (count) => [
    {value: YEAR_STEP, label: t('Letzte 5')},
    {value: 0, label: t('Alle ({n})', {n: count})},
  ];

  // Der Reiter „Langzeit“ und der Reiter „Verbraucher“ laden ihre Daten erst beim ersten Zeigen (Ereignis „energie-tab“).
  function onFirstShow(tabName, component, load) {
    window.addEventListener('energie-tab', (event) => {
      if (!component.$root.isConnected || event.detail !== tabName) return;
      if (!component.opened) {
        component.opened = true;
        load();
      } else {
        // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
        component.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
      }
    });
  }

  // ---- Übersicht: „Zeiträume im Vergleich“ -----------------------------------------------------
  // Eine Karte unter dem Energiefluss mit Spalten- und Vorjahres-Menü, wie die Vergleichstabelle des Wetterdashboards.
  // Sie folgt dem Tag des Energieflusses (Ereignis „energie-window“ mit dem Stichtag); heute oder später heißt „jetzt“.
  let lastTable = null;

  window.energieVergleich = function energieVergleich() {
    return {
      columnKeys: COLUMN_KEYS,
      columns: [...DEFAULT_COLUMNS],
      vorjahr: true,
      diff: true,
      mode: 'bis_jetzt',
      error: '',
      at: null,
      standText: '',
      _seq: 0,
      _started: false,

      init() {
        const saved = readStore();
        // Spalten ohne Daten gibt es nicht; frühere Stände kannten nur Monat, Vormonat und Jahr.
        if (Array.isArray(saved.columns)) {
          const known = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
          if (known.length) this.columns = known;
        }
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (typeof saved.diff === 'boolean') this.diff = saved.diff;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        window.addEventListener('energie-window', (event) => {
          if (!this.$root.isConnected) return;
          this.at = event.detail && event.detail.at ? event.detail.at : null;
          this._started = true;
          this.loadTable();
        });
        // Der Energiefluss meldet seinen Tag, sobald er geladen ist. Kam die Meldung schon vor diesem Aufbau oder bleibt sie
        // aus (Ladefehler), startet die Tabelle trotzdem, dann mit „jetzt“.
        if (window.energieWindowAt !== undefined) {
          this.at = window.energieWindowAt;
          this._started = true;
          this.loadTable();
        } else {
          setTimeout(() => {
            if (!this._started && this.$root.isConnected) {
              this._started = true;
              this.loadTable();
            }
          }, 2500);
        }
      },

      toggleColumn(key) {
        this.columns = this.columns.includes(key)
          ? this.columns.filter((k) => k !== key)
          : COLUMN_KEYS.filter((k) => k === key || this.columns.includes(k));
        this.saveAndLoad();
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

      // Differenzzeile unter den Werten ein- oder ausblenden; die Daten bleiben, nur die Tabelle wird neu gezeichnet.
      toggleDiff() {
        this.diff = !this.diff;
        writeStore({diff: this.diff});
        if (lastTable) this.paintTable(lastTable);
      },

      columnName,

      paintTable(data) {
        const table = this.$refs.table;
        const wrap = this.$refs.tableWrap;
        if (!table) return;
        table.innerHTML = renderTable(data, this.diff);
        // Scroll-Hinweis des App-Standards (.tbl-wrap.is-scrollable), nur wenn die Tabelle wirklich breiter ist.
        wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1);
      },

      saveAndLoad() {
        writeStore({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode});
        this.loadTable();
      },

      modeHint() {
        return this.mode === 'bis_jetzt'
          ? t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Abgeschlossene Zeiträume (Gestern, Letzter Monat) sind in beiden Fällen gleich.')
          : t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch meist höhere Summen.');
      },

      async loadTable() {
        const seq = ++this._seq;
        this.error = '';
        try {
          const res = await fetch('energiedashboard/langzeit/table', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode, at: this.at}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._seq) return;  // eine neuere Anfrage hat überholt
          lastTable = data;
          // „Stand“: der Tag, bis zu dem die Zeiträume zählen (ein früherer Tag oder heute).
          const day = data.at || data.today;
          this.standText = day ? t('Stand {date}', {date: fmtDate(formats.dmy, Date.parse(`${day}T12:00:00Z`) / 1000)}) : '';
          this.paintTable(data);
        } catch (e) {
          if (seq === this._seq) this.error = t('Die Tabelle konnte nicht geladen werden.');
        }
      },
    };
  };

  // ---- Reiter „Verbraucher“ ----------------------------------------------------------------------
  window.energieVerbraucher = function energieVerbraucher() {
    return {
      opened: false,
      yearLimit: 0,
      consumers: null,
      consumersError: '',
      _consumersSeq: 0,
      // Sortierung der Rangliste: Spalte ('name', 'kwh' für kWh und Anteil, 'chg') und Richtung.
      rankSortCol: 'kwh',
      rankSortDir: 'desc',
      // Gewähltes Jahr der Rangliste; null = das laufende Jahr.
      rankYearSel: null,
      sharesMode: 'pct',
      // Tabelle „Zeiträume im Vergleich je Verbraucher“: sortiert nach einer Spalte (null = „Dieses Jahr“); Spalten als Liste von
      // Zeiträumen (Schlüssel) und einzelnen Jahren (null = Monat, Vormonat, Jahr), Hauptwert 'pct'/'kwh'; Vorjahr-Spalten,
      // Vergleich ('bis_jetzt'/'komplett') und Änderung unter dem Wert wie in „Zeiträume im Vergleich“ der Übersicht.
      sharesSortCol: null,
      sharesSortDir: 'desc',
      tblCols: null,
      tblMain: 'pct',
      vorjahr: true,
      mode: 'bis_jetzt',
      diff: true,
      // Hervorgehobener Verbraucher (Schlüssel aus langzeit/consumers, „rest“ für Übrige) oder null.
      pick: null,
      // Verbraucher der Matrix „Monatswerte je Jahr“ (null = der erste).
      matrixSel: null,

      init() {
        const saved = readStore();
        this.yearLimit = storedYearLimit(saved.consYearLimit);
        // Früher ein Umschalter 'use'/'kwh' (Verbrauch) oder 'chg' (Änderung, jetzt nach Vorzeichen), heute {col, dir}.
        const rankSort = saved.rankSort;
        if (rankSort === 'chg') this.rankSortCol = 'chg';
        else if (rankSort && ['name', 'kwh', 'chg'].includes(rankSort.col) && ['asc', 'desc'].includes(rankSort.dir)) {
          this.rankSortCol = rankSort.col;
          this.rankSortDir = rankSort.dir;
        }
        if (saved.sharesMode === 'pct' || saved.sharesMode === 'kwh') this.sharesMode = saved.sharesMode;
        // Spalten ohne Daten fallen erst beim Zeichnen weg (sharesTableCols), wenn die Jahre des Archivs bekannt sind.
        // Gespeichert sind Jahre (Zahlen) und Zeiträume (Schlüssel); frühere Stände kannten nur Jahre.
        if (Array.isArray(saved.tblCols) && saved.tblCols.length && saved.tblCols.every(isColumn)) this.tblCols = saved.tblCols;
        if (saved.tblMain === 'pct' || saved.tblMain === 'kwh') this.tblMain = saved.tblMain;
        if (typeof saved.consVorjahr === 'boolean') this.vorjahr = saved.consVorjahr;
        if (typeof saved.consDiff === 'boolean') this.diff = saved.consDiff;
        else if (saved.tblChg === 'off') this.diff = false;  // frühere Änderungszeile „Aus“
        if (saved.consMode === 'bis_jetzt' || saved.consMode === 'komplett') this.mode = saved.consMode;
        const savedSortCol = saved.tblSort ? (saved.tblSort.col ?? saved.tblSort.year) : null;
        if (isColumn(savedSortCol) && ['asc', 'desc'].includes(saved.tblSort.dir)) {
          this.sharesSortCol = savedSortCol;
          this.sharesSortDir = saved.tblSort.dir;
        }
        // Geladen wird erst beim ersten Öffnen des Reiters.
        onFirstShow('cons', this, () => this.loadConsumers(true));
        // Nach einer Größenänderung neu zeichnen: welche Beschriftung in ihren Abschnitt passt, hängt von der Breite ab.
        // Die Tabelle braucht dabei nur den Scroll-Hinweis neu, der ebenfalls an der Breite hängt.
        window.addEventListener('resize', () => {
          if (!this.$root.isConnected || !this.consumers) return;
          this.drawShares();
          this.drawSharesTable();
        });
      },

      // Verbraucher über die Jahre. withMonths=false lädt nur das Fenster der Rangliste und behält die Monatswerte.
      async loadConsumers(withMonths) {
        const seq = ++this._consumersSeq;
        const months = withMonths || !this.consumers;
        this.consumersError = '';
        try {
          const res = await fetch('energiedashboard/langzeit/consumers', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({months, mode: this.mode}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._consumersSeq) return;  // eine neuere Anfrage hat überholt
          if (!months && this.consumers) {
            const old = this.consumers;
            const keep = (from, to) => {
              if (!from) return;
              for (const key of ['values', 'year_sums', 'flags', 'unchecked']) if (key in from) to[key] = from[key];
            };
            data.consumers.forEach((item) => keep(old.consumers.find((o) => o.key === item.key), item));
            keep(old.rest, data.rest);
            data.years = old.years;
            data.total = old.total;
          }
          this.consumers = data;
          this.$nextTick(() => this.drawConsumers());
        } catch (e) {
          if (seq === this._consumersSeq) this.consumersError = t('Die Verbraucher konnten nicht geladen werden.');
        }
      },

      drawConsumers() {
        this.drawRank();
        this.drawSharesTable();
        this.drawShares();
        this.drawConsumerMatrix();
      },

      // Anteile je Jahr in Prozent oder kWh; die Jahre wie in der Leiste „Jahre“ gewählt.
      setSharesMode(mode) {
        this.sharesMode = mode;
        writeStore({sharesMode: mode});
        this.drawShares();
      },

      sharesNote() {
        return this.sharesMode === 'pct'
          ? t('Anteil jedes Verbrauchers am Verbrauch des Jahres laut Bilanz. Das laufende Jahr (*) und das erste Jahr des Archivs sind nicht vollständig.')
          : t('Jahressummen in kWh, alle Balken im selben Maßstab. Das laufende Jahr (*) und das erste Jahr des Archivs sind nicht vollständig.');
      },

      sharesTableNote() {
        const parts = [
          this.tblMain === 'pct'
            ? t('Je Feld der Anteil am Verbrauch des Zeitraums laut Bilanz, darunter die kWh.')
            : t('Je Feld die kWh des Zeitraums, darunter der Anteil am Verbrauch laut Bilanz.'),
        ];
        if (this.vorjahr) parts.push(this.modeHint());
        parts.push(
          t('Jede Spalte ist die Rangliste ihres Zeitraums.'),
          t('„Spalten“ wählt Zeiträume und einzelne Jahre, „Vorjahr“ den Vergleich wie in „Zeiträume im Vergleich“ der Übersicht, Anteil | kWh den Hauptwert. Auswahl und Sortierung merkt sich der Browser.'),
        );
        // Sortierung, Änderung und Teiljahr der gezeigten Spalten.
        const data = this.consumers;
        if (data && data.years) {
          const cols = this.sharesTableCols();
          if (cols.length) parts.push(sharesTableFoot(data, cols, this.sharesSort(), this.vorjahr && this.diff));
        }
        return parts.join(' ');
      },

      modeHint() {
        return this.mode === 'bis_jetzt'
          ? t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Abgeschlossene Zeiträume (Gestern, Letzter Monat) sind in beiden Fällen gleich.')
          : t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch meist höhere Summen.');
      },

      // Spalten für das Menü „Spalten“: die Zeiträume wie in „Zeiträume im Vergleich“, dann die einzelnen Jahre davor, neueste
      // zuerst (das laufende Jahr ist „Dieses Jahr“). Zeiträume, die der Server nicht liefert, fehlen.
      tablePeriodOptions() {
        return ((this.consumers && this.consumers.periods) || []).map((period) => ({col: period.key, label: columnName(period.key)}));
      },

      tableYearOptions() {
        const data = this.consumers;
        if (!data || !data.years) return [];
        return data.years.filter((year) => year < data.window.year).map((year) => ({col: year, label: String(year)}));
      },

      sharesTableOptions() {
        return [...this.tablePeriodOptions(), ...this.tableYearOptions()];
      },

      // Das laufende Jahr als Zahl (frühere Stände) gilt als „Dieses Jahr“.
      canonCol(col) {
        return Number.isInteger(col) && this.consumers && col >= this.consumers.window.year ? 'jahr' : col;
      },

      // Eine Spalte an- oder abwählen; die letzte gewählte bleibt (das Kästchen ist dann gesperrt).
      toggleTableCol(col) {
        const current = this.sharesTableCols();
        if (current.includes(col) && current.length === 1) return;
        const next = current.includes(col) ? current.filter((c) => c !== col) : [...current, col];
        this.tblCols = this.sharesTableOptions().map((option) => option.col).filter((c) => next.includes(c));
        writeStore({tblCols: this.tblCols});
        this.drawSharesTable();
      },

      setTableMain(main) {
        this.tblMain = main;
        writeStore({tblMain: main});
        this.drawSharesTable();
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
        writeStore({consVorjahr: this.vorjahr, consMode: this.mode});
        if (this.mode !== before) this.loadConsumers(false);
        else this.drawSharesTable();
      },

      yoyLabel() {
        if (!this.vorjahr) return t('Vorjahr: Aus ▾');
        return (this.mode === 'komplett' ? t('Vorjahr: komplett') : t('Vorjahr: bis jetzt')) + ' ▾';
      },

      toggleDiff() {
        this.diff = !this.diff;
        writeStore({consDiff: this.diff});
        this.drawSharesTable();
      },

      drawShares() {
        const data = this.consumers;
        const host = this.$refs.shares;
        if (!data || !data.years || !host) return;
        const years = this.yearLimit ? data.years.slice(0, this.yearLimit) : data.years;
        host.innerHTML = sharesHtml(data, years, this.sharesMode, this.pick);
        host.classList.toggle('has-hl', !!this.pick);
        requestAnimationFrame(() => fitShareLabels(host));
        if (this.$refs.sharesLegend) this.$refs.sharesLegend.innerHTML = sharesLegendHtml(data, this.pick);
      },

      // Spalten der Tabelle (Zeiträume zuerst, dann die Jahre neueste zuerst): die im Menü „Spalten“ gewählten, soweit es sie
      // gibt; sonst Monat, Vormonat und Jahr wie in den Summen.
      sharesTableCols() {
        const options = this.sharesTableOptions().map((option) => option.col);
        const preset = DEFAULT_COLUMNS.filter((col) => options.includes(col));
        if (!this.tblCols) return preset;
        const wanted = this.tblCols.map((col) => this.canonCol(col));
        const chosen = options.filter((col) => wanted.includes(col));
        return chosen.length ? chosen : preset;
      },

      // Sortierung der Tabelle; eine Spalte, die nicht mehr gezeigt wird, fällt auf „Dieses Jahr“ zurück.
      sharesSort() {
        const cols = this.sharesTableCols();
        const col = this.canonCol(this.sharesSortCol);
        if (!cols.includes(col)) {
          return {col: cols.includes('jahr') ? 'jahr' : (cols.find(Number.isInteger) ?? cols[0]), dir: this.sharesSortCol == null ? this.sharesSortDir : 'desc'};
        }
        return {col, dir: this.sharesSortDir};
      },

      drawSharesTable() {
        const data = this.consumers;
        const table = this.$refs.sharesTable;
        if (!data || !data.years || !table) return;
        const cols = this.sharesTableCols();
        if (this.sharesSortCol != null && !cols.includes(this.canonCol(this.sharesSortCol))) {
          this.sharesSortCol = null;
          this.sharesSortDir = 'desc';
        }
        if (!cols.length) {
          table.innerHTML = '';
          return;
        }
        const sort = this.sharesSort();
        // Zeitraum und Vorjahr sind je eine Spalte gleicher Breite.
        table.style.setProperty('--n', cols.length * (this.vorjahr ? 2 : 1));
        table.innerHTML = sharesTableHtml(data, cols, {main: this.tblMain, vorjahr: this.vorjahr, diff: this.diff}, sort, this.matrixKey());
        // Scroll-Hinweis des App-Standards (.tbl-wrap.is-scrollable), nur wenn die Tabelle wirklich breiter ist.
        const wrap = this.$refs.sharesTableWrap;
        if (wrap) requestAnimationFrame(() => wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1));
      },

      // Klick auf einen Spaltenkopf sortiert nach dieser Spalte (ein zweiter dreht die Reihenfolge), auf eine Zeile wählt den
      // Verbraucher für die Matrix wie ein Klick auf die Balken; der Name öffnet die Entität.
      sharesTableClick(event) {
        if (event.target.closest('a')) return;
        const head = event.target.closest('button[data-col]');
        if (head) {
          const raw = head.dataset.col;
          const col = /^\d+$/.test(raw) ? Number(raw) : raw;
          const sort = this.sharesSort();
          if (col === sort.col) this.sharesSortDir = sort.dir === 'desc' ? 'asc' : 'desc';
          else this.sharesSortDir = 'desc';
          this.sharesSortCol = col;
          writeStore({tblSort: {col, dir: this.sharesSortDir}});
          this.drawSharesTable();
          return;
        }
        const row = event.target.closest('tr.wt-vt-row');
        if (row) this.pickConsumer(row.dataset.key);
      },

      sharesTableKey(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target.closest('a, button')) return;
        const row = event.target.closest('tr.wt-vt-row');
        if (!row) return;
        event.preventDefault();
        this.pickConsumer(row.dataset.key);
      },

      // Hervorhebung eines Verbrauchers in allen Jahren (Zeiger darüber vorübergehend, Klick bleibt).
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
        const key = target.dataset.key;
        this.pickConsumer(this.pick === key && target.tagName === 'BUTTON' ? null : key);
      },

      // Wählt einen Verbraucher für die Hervorhebung in „Anteile je Jahr“ und für die Matrix (null hebt nur die Hervorhebung auf).
      pickConsumer(key, scroll = false) {
        this.pick = key;
        if (key) this.matrixSel = key;
        this.drawRank();
        this.drawSharesTable();
        this.drawShares();
        this.drawConsumerMatrix();
        if (scroll && this.$refs.matrixCard) {
          const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          this.$refs.matrixCard.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'start'});
        }
      },

      rankClick(event) {
        if (event.target.closest('a')) return;  // der Name öffnet die Entität
        const head = event.target.closest('button[data-sort]');
        if (head) {
          this.sortRank(head.dataset.sort);
          return;
        }
        const row = event.target.closest('.wt-vr-pick');
        if (row) this.pickConsumer(row.dataset.key, true);
      },

      rankKey(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target.closest('a')) return;
        const row = event.target.closest('.wt-vr-pick');
        if (!row) return;
        event.preventDefault();
        this.pickConsumer(row.dataset.key, true);
      },

      // Verbraucher der Matrix: der gewählte, sonst der erste der Liste.
      matrixKey() {
        const data = this.consumers;
        if (!data) return null;
        const keys = consumerRows(data).map((item) => item.key);
        return keys.includes(this.matrixSel) ? this.matrixSel : keys[0];
      },

      consumerOptions() {
        return this.consumers ? consumerRows(this.consumers).map((item) => ({key: item.key, name: item.name})) : [];
      },

      consumerMatrixNote() {
        let text = t('Monatswerte des gewählten Verbrauchers in kWh, darunter die Abweichung vom Mittel dieses Monats über die abgeschlossenen Jahre. Rot: mehr Verbrauch, grün: weniger. Die letzte Zeile ist das Mittel.');
        const item = this.consumers && consumerRows(this.consumers).find((row) => row.key === this.matrixKey());
        if (item && item.unchecked) text += ' ' + t('{n} Entität(en) sind noch nicht auf Lücken und Zählersprünge geprüft.', {n: item.unchecked});
        return text;
      },

      // Dieselbe Matrix wie „Jahre × Monate“ der Summen (energyMatrix), mit dem Verbraucher als Zeile; mehr ist ungünstig.
      drawConsumerMatrix() {
        const data = this.consumers;
        const table = this.$refs.consumerMatrix;
        if (!data || !data.years || !table) return;
        const item = consumerRows(data).find((row) => row.key === this.matrixKey());
        if (!item || !item.values) return;
        const years = this.yearLimit ? data.years.slice(0, this.yearLimit) : data.years;
        const m = {years, current: data.current};
        const role = {values: item.values, flags: item.flags || {}, partial_start: item.partial_start, better: 'down'};
        const href = (year, month) => {
          if (!item.entity_id) return '';
          const back = (data.current.year - year) * 12 + (data.current.month - month);
          return `entities/${encodeURIComponent(item.entity_id)}?range=month&offset=${-back}`;
        };
        const colors = palette();
        table.innerHTML = energyMatrix(m, role, {colors, href});
        const scale = this.$refs.consumerMatrixScale;
        if (scale) scale.style.background = `linear-gradient(90deg, ${colors.bad}, ${colors.base}, ${colors.good})`;
      },

      // Alle Jahre vom laufenden Jahr zurück bis zum Beginn des Archivs, mit Beschriftung.
      pastYears() {
        const data = this.consumers;
        if (!data || !data.years) return [];
        const until = fmtDate(formats.dm, data.window.cur.end - 1);
        return data.years.filter((year) => year <= data.window.year).map((year) => ({
          year, label: year === data.window.year ? t('{year} bis {date}', {year, date: until}) : String(year),
        }));
      },

      // Jahre der Rangliste: so viele wie in der Leiste „Jahre“ gewählt.
      rankYears() {
        const past = this.pastYears();
        return this.yearLimit ? past.slice(0, this.yearLimit) : past;
      },

      rankYear() {
        const data = this.consumers;
        if (!data) return null;
        const shown = this.rankYears().map((option) => option.year);
        return this.rankYearSel != null && this.rankYearSel < data.window.year && shown.includes(this.rankYearSel) ? this.rankYearSel : data.window.year;
      },

      setRankYear(year) {
        this.rankYearSel = this.consumers && year >= this.consumers.window.year ? null : year;
        this.drawRank();
      },

      // Klick auf einen Spaltenkopf (mobil auf den Sortier-Umschalter): ein zweiter Klick dreht die Richtung. Der Name beginnt
      // aufsteigend, Zahlen absteigend (bei der Änderung: größter Anstieg zuerst).
      sortRank(col) {
        if (col === this.rankSortCol) this.rankSortDir = this.rankSortDir === 'desc' ? 'asc' : 'desc';
        else this.rankSortDir = col === 'name' ? 'asc' : 'desc';
        this.rankSortCol = col;
        writeStore({rankSort: {col, dir: this.rankSortDir}});
        this.drawRank();
      },

      rankSortArrow(col) {
        if (col !== this.rankSortCol) return '';
        return this.rankSortDir === 'desc' ? ' ▼' : ' ▲';
      },

      rankRange() {
        const data = this.consumers;
        if (!data) return '';
        const year = this.rankYear();
        if (year === data.window.year) return changesRange(data.window);
        if (data.rest.first_ts != null && !coversYear(data.rest, year)) {
          return t('{year} ab Beginn des Archivs am {date}, ohne Vorjahr.', {year, date: fmtDate(formats.dmy, data.rest.first_ts)});
        }
        if (!(data.years || []).includes(year - 1)) return t('Ganzes Jahr {year}, ohne Vorjahr im Archiv.', {year});
        return t('Ganzes Jahr {year} gegen das ganze Jahr {prev}.', {year, prev: year - 1});
      },

      rankNote() {
        return t('Verbraucher der obersten Ebene; Geräte hinter einem Zwischenzähler stecken im Zwischenzähler. Im laufenden Jahr zählt der 1. Januar bis heute, in den Jahren davor das ganze Jahr. Verbraucher, die später als die Bilanz erfasst werden, stecken vorher in „Übrige (Grundlast)“, das Dreieck dort nennt sie. Gelöschte Verbraucher fehlen auch in den Vorjahren, ihr Verbrauch steht dort in „Übrige“.');
      },

      drawRank() {
        const data = this.consumers;
        const host = this.$refs.rank;
        if (!data || !host) return;
        const rank = consumerRank(data, this.rankYear(), this.yearLimit);
        host.style.setProperty('--vr-years', String(sparkYears(data, rank.limit).length));
        host.innerHTML = rankHtml(data, rank, {col: this.rankSortCol, dir: this.rankSortDir}, this.matrixKey());
        if (this.$refs.rankCover) this.$refs.rankCover.innerHTML = coverHtml(rank);
      },

      columnName,

      // Anzahl der Jahre im Archiv (0 = noch nicht geladen); die Jahre-Auswahl erscheint erst ab mehr als fünf.
      yearCount() {
        return this.consumers && this.consumers.years ? this.consumers.years.length : 0;
      },

      yearOptions() {
        return yearChoices(this.yearCount());
      },

      showYearChoice() {
        return this.yearCount() > YEAR_STEP;
      },

      setYearLimit(value) {
        this.yearLimit = value;
        writeStore({consYearLimit: value});
        this.drawShares();
        this.drawConsumerMatrix();
        this.drawRank();
      },
    };
  };

  // ---- Reiter „Langzeit“ -------------------------------------------------------------------------
  // hidden: im Setup abgewählte Karten (Schlüssel wie energie_langzeit.CARD_BLOCKS); deren Daten werden gar nicht erst geladen.
  window.energieLangzeit = function energieLangzeit(hidden = []) {
    const shown = (key) => !hidden.includes(key);
    return {
      opened: false,
      months: null,
      monthsError: '',
      records: null,
      recordsError: '',
      monthlyRole: 'erzeugung',
      matrixRole: 'erzeugung',
      band: true,
      yearLimit: 0,
      ktMode: 'full',
      ktDialog: {title: '', sub: '', html: '', error: ''},
      _kenntageSeq: 0,

      init() {
        const saved = readStore();
        this.yearLimit = storedYearLimit(saved.yearLimit);
        if (saved.ktMode === 'full' || saved.ktMode === 'ytd') this.ktMode = saved.ktMode;
        // Geladen wird erst beim ersten Öffnen des Reiters: Die Übersicht ist die Startansicht.
        onFirstShow('long', this, () => {
          if (shown('monatsvergleich') || shown('matrix')) this.loadMonths();
          if (shown('rekorde') || shown('kenntage')) this.loadRecords();
        });
      },

      // Anzahl der Jahre im Archiv (0 = noch nicht geladen); die Jahre-Auswahl erscheint erst ab mehr als fünf.
      yearCount() {
        const m = this.months ? this.months.years.length : 0;
        const k = this.records && this.records.kenntage ? this.records.kenntage.years.length : 0;
        return Math.max(m, k);
      },

      yearOptions() {
        return yearChoices(this.yearCount());
      },

      showYearChoice() {
        return this.yearCount() > YEAR_STEP;
      },

      // Gilt für Kenntage, Monatswerte je Jahr und Monatsvergleich, damit alle dieselben Jahre und dieselbe Basis für Ø zeigen.
      setYearLimit(value) {
        this.yearLimit = value;
        writeStore({yearLimit: value});
        this.drawMonthly();
        this.drawMatrix();
        this.drawKenntage();
      },

      // Die Monatswerte mit den gewählten Jahren, für Diagramm und Matrix.
      view() {
        const years = this.yearLimit ? this.months.years.slice(0, this.yearLimit) : this.months.years;
        return {...this.months, years};
      },

      // Rollen mit Monatswerten (Erzeugung, Einspeisung, Netzbezug, Verbrauch), je nach Einrichtung.
      monthRoles() {
        return this.months ? this.months.roles : [];
      },

      roleOf(key) {
        const roles = this.monthRoles();
        return roles.find((role) => role.key === key) || roles[0] || null;
      },

      setMonthlyRole(key) {
        this.monthlyRole = key;
        this.drawMonthly();
      },

      setMatrixRole(key) {
        this.matrixRole = key;
        this.drawMatrix();
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
          const keys = this.monthRoles().map((role) => role.key);
          if (!keys.includes(this.monthlyRole)) this.monthlyRole = keys[0];
          if (!keys.includes(this.matrixRole)) this.matrixRole = keys[0];
          // Erst zeichnen, wenn Alpine die Karten sichtbar gemacht hat (x-show="months").
          this.$nextTick(() => { this.drawMonthly(); this.drawMatrix(); });
        } catch (e) {
          this.monthsError = t('Die Monatswerte konnten nicht geladen werden.');
        }
      },

      monthlyNote() {
        return t('Monatswerte je Jahr in kWh. Die gestrichelte Linie ist das Mittel der abgeschlossenen Jahre, die Fläche ihre Spanne. Der laufende Monat steht nur im Tooltip.');
      },

      drawMonthly() {
        if (!this.months) return;
        const m = this.view();
        const element = this.$refs.monthlyChart;
        const role = m && this.roleOf(this.monthlyRole);
        if (!role || !element) return;
        const chart = chartFor('monthly', element);
        chart.setOption(energyMonthly(m, role, {
          colors: palette(), band: this.band, narrow: element.clientWidth < 520, font: scaledFont,
        }), true);
        requestAnimationFrame(() => chart.resize());
      },

      matrixNote() {
        const role = this.roleOf(this.matrixRole);
        let text = t('Oben der Monatswert in kWh, darunter die Abweichung vom Mittel dieses Monats über alle abgeschlossenen Jahre im Archiv. Die letzte Zeile ist das Mittel. Grün ist günstig, rot ungünstig.');
        if (role && role.unchecked) {
          text += ' ' + t('{n} Entität(en) sind noch nicht auf Lücken und Zählersprünge geprüft.', {n: role.unchecked});
        }
        return text;
      },

      // Link auf den Monat der Entität (nur wenn die Rolle genau eine hat); sonst kein Link.
      matrixLink(role, year, month) {
        if (!role.entity_id) return '';
        const back = (this.months.current.year - year) * 12 + (this.months.current.month - month);
        return `entities/${encodeURIComponent(role.entity_id)}?range=month&offset=${-back}`;
      },

      drawMatrix() {
        if (!this.months) return;
        const m = this.view();
        const table = this.$refs.matrixTable;
        const role = m && this.roleOf(this.matrixRole);
        if (!role || !table) return;
        const colors = palette();
        table.innerHTML = energyMatrix(m, role, {colors, href: (year, month) => this.matrixLink(role, year, month)});
        const scale = this.$refs.matrixScale;
        if (scale) scale.style.background = `linear-gradient(90deg, ${colors.bad}, ${colors.base}, ${colors.good})`;
      },
    };
  };
})();
