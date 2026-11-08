// Energiedashboard, Reiter „Langzeit“ (Roadmap 1.45): Zeiträume im Vergleich mit dem Vorjahr. Der Server
// rechnet alle Summen (POST energiedashboard/langzeit/table); dieses Skript formatiert und zeichnet nur.
// Aufbau und Bedienung wie die Vergleichstabelle des Wetterdashboards (wetter.js). Kalenderangaben laufen
// über ServerTime in der Zeitzone des Servers.
(function () {
  const COLUMN_KEYS = ['monat', 'vormonat', 'jahr'];
  const STORE_KEY = 'zeitarchiv.energie.langzeit';
  const LOCALE = window.ZA_FORMAT;
  const ZT = window.ServerTime;

  const {formats, fmtDate, rangeText, energy, columnName, energyComparison: renderTable, kenntagRule, kenntagLabels: KENNTAG_LABELS, energyKenntage, energyChanges, changesRange, kenntageRange} = window.ElementTableRender;
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

  // Rangliste eines Jahres: im Jahr des Stichtags das Fenster bis zum Stichtag, sonst ganze Jahre. Eine Änderung gibt es nur,
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

  // Änderung zum Vorjahr als Pfeil mit Prozent, darunter die kWh (withKwh) oder der Grund für „–“.
  function rankDelta(entry, withKwh = true) {
    if (entry.delta == null) {
      return `<span class="wt-dl">–</span>${entry.note ? `<small>${escapeHtml(entry.note)}</small>` : ''}`;
    }
    const percent = entry.prev ? entry.delta / entry.prev * 100 : null;
    const zero = percent != null ? Math.round(Math.abs(percent)) === 0 : Math.round(Math.abs(entry.delta) * 10) === 0;
    const cls = zero ? '' : (entry.delta > 0 ? ' wt-dl-bad' : ' wt-dl-good');
    let head = zero ? '± 0 %' : (entry.delta > 0 ? '▲' : '▼');
    if (!zero && percent != null) head += ` ${entry.delta > 0 ? '+' : '−'}${num(Math.abs(percent), 0)} %`;
    const sign = zero ? '±' : (entry.delta > 0 ? '+' : '−');
    const kwh = withKwh ? `<small>${escapeHtml(`${sign}${kwhText(Math.abs(entry.delta))} kWh`)}</small>` : '';
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

  function rankHtml(data, rank, sort, selected) {
    const list = rank.entries.filter((e) => !e.item.rest);
    const rest = rank.entries.filter((e) => e.item.rest);
    if (sort === 'chg') list.sort((a, b) => (b.delta == null ? -1 : Math.abs(b.delta)) - (a.delta == null ? -1 : Math.abs(a.delta)));
    else list.sort((a, b) => b.cur - a.cur);
    const rows = [...list, ...rest];
    const total = rank.total;
    const share = (value) => (total ? value / total * 100 : null);
    const top = Math.max(0, ...rows.map((e) => share(e.cur) || 0)) || 1;
    let html = `<div class="wt-vr-row wt-vr-head" aria-hidden="true"><span class="wt-vr-name">${escapeHtml(t('Verbraucher'))}</span><span></span>`
      + `<span class="wt-vr-num wt-vr-kwh">kWh</span><span class="wt-vr-num wt-vr-pct">${escapeHtml(t('Anteil'))}</span>`
      + `<span class="wt-vr-num wt-vr-delta">${escapeHtml(t('Vorjahr'))}</span><span class="wt-vr-spark">${escapeHtml(t('Verlauf'))}</span></div>`;
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

  // Erstes Jahr des Archivs, wenn es nicht am 1. Januar beginnt (dann kein ganzes Jahr), sonst null.
  function partialFirstYear(data) {
    const rest = data.rest;
    if (rest.first_ts == null) return null;
    const year = ZT.parts(rest.first_ts).year;
    return coversYear(rest, year) ? null : year;
  }

  // Tabelle Verbraucher × Jahre, ältestes Jahr links. Jede Spalte ist die Rangliste ihres Jahres
  // (consumerRank): das Jahr des Stichtags bis zum Stichtag gegen dasselbe Stück des Vorjahres, die Jahre davor ganz.
  // years: neueste zuerst; sort: {year, dir}; mode 'pct' oder 'kwh' bestimmt die Hauptzahl, der andere Wert steht klein darunter.
  function sharesTableHtml(data, years, mode, sort, selected, limit = 0) {
    const cols = [...years].reverse();
    const ranks = {};
    for (const year of cols) ranks[year] = consumerRank(data, year, limit);
    const entryOf = (year, key) => ranks[year].entries.find((e) => e.item.key === key) || null;
    const hasPrev = (year) => (data.years || []).includes(year - 1);
    const firstYear = partialFirstYear(data);
    const until = fmtDate(formats.dm, data.window.cur.end - 1);
    const pct = mode === 'pct';
    const pctText = (value) => `${num(value, 1)} %`;
    const rows = consumerRows(data);
    const list = rows.filter((item) => !item.rest);
    // Ohne Wert im sortierten Jahr ans Ende; sonst bleibt bei Gleichstand die Setup-Reihenfolge (stabile Sortierung).
    list.sort((a, b) => {
      const ea = entryOf(sort.year, a.key);
      const eb = entryOf(sort.year, b.key);
      if (!ea || !eb) return (eb ? 1 : 0) - (ea ? 1 : 0);
      return sort.dir === 'asc' ? ea.cur - eb.cur : eb.cur - ea.cur;
    });
    const sorted = (year) => (year === sort.year ? ' wt-vt-sorted' : '');

    let html = `<thead><tr><th scope="col">${escapeHtml(t('Verbraucher'))}<small>${escapeHtml(pct ? t('Anteil, darunter kWh') : t('kWh, darunter Anteil'))}</small></th>`;
    for (const year of cols) {
      const on = year === sort.year;
      const arrow = on ? (sort.dir === 'desc' ? '▼' : '▲') : '↕';
      let sub = '';
      if (year === data.window.year) sub = t('bis {date}', {date: until});
      else if (year === firstYear) sub = t('ab {date}', {date: fmtDate(formats.dm, data.rest.first_ts)});
      html += `<th scope="col" class="wt-vt-yh${on ? ' is-sorted' : ''}" aria-sort="${on ? (sort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}">`
        + `<button type="button" data-year="${year}" aria-label="${escapeHtml(t('Nach {year} sortieren', {year}))}">`
        + `<span><span class="wt-vt-yl">${year}${year === firstYear ? '*' : ''}</span><span class="wt-vt-arrow${on ? '' : ' is-off'}" aria-hidden="true">${arrow}</span></span>`
        + `${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</button></th>`;
    }
    html += '</tr></thead><tbody>';

    for (const item of [...list, ...rows.filter((row) => row.rest)]) {
      const name = item.entity_id
        ? `<a href="entities/${encodeURIComponent(item.entity_id)}?range=year&offset=0">${escapeHtml(item.name)}</a>`
        : `<span>${escapeHtml(item.name)}</span>`;
      const from = item.late && item.first_ts != null && ZT.parts(item.first_ts).year >= cols[0]
        ? `<span class="wt-vr-from">${escapeHtml(t('ab {date}', {date: monthYear(item.first_ts)}))}</span>` : '';
      const hatch = item.rest ? ' wt-vr-hatch' : '';
      html += `<tr class="wt-vt-row${item.rest ? ' wt-vt-rest' : ''}${selected === item.key ? ' is-sel' : ''}" data-key="${escapeHtml(item.key)}"`
        + ` tabindex="0" aria-label="${escapeHtml(t('{name}: Monate unten zeigen', {name: item.name}))}">`
        + `<th scope="row"><span class="wt-vt-name"><i class="wt-vr-dot${hatch}" style="background:${item.color}"></i><span>${name}${from}</span></span></th>`;
      for (const year of cols) {
        const entry = entryOf(year, item.key);
        if (!entry) {
          const missing = t('{name} {year}: noch nicht erfasst', {name: item.name, year});
          html += `<td class="wt-vt-na${sorted(year)}" data-tooltip-fixed="${escapeHtml(missing)}"><span class="wt-vt-main">–</span></td>`;
          continue;
        }
        const total = ranks[year].total;
        const share = total ? entry.cur / total * 100 : null;
        const shareText = share == null ? '–' : pctText(share);
        const warn = entry.flags.length
          ? `<span class="wt-vr-warn" data-tooltip-fixed="${escapeHtml(entry.flags.join('\n'))}" role="img" aria-label="${escapeHtml(t('Hinweis: {text}', {text: entry.flags.join('; ')}))}"></span>` : '';
        let tip = `${item.name} ${year === data.window.year ? t('{year} bis {date}', {year, date: until}) : year}: ${energy(entry.cur)} · ${shareText}`;
        if (entry.delta != null) tip += ` · ${t('Vorjahr {value}', {value: deltaKwh(entry.delta)})}`;
        html += `<td class="wt-vt-c${sorted(year)}" data-tooltip-fixed="${escapeHtml(tip)}">`
          + `<span class="wt-vt-main">${warn}${escapeHtml(pct ? shareText : kwhText(entry.cur))}</span>`
          + `<span class="wt-vt-sub">${escapeHtml(pct ? `${kwhText(entry.cur)} kWh` : shareText)}</span>`
          + `${hasPrev(year) ? rankDelta(entry, false) : ''}</td>`;
      }
      html += '</tr>';
    }

    // Verbrauch laut Bilanz mit seiner Änderung; ohne vergleichbares Vorjahr nennt „Übrige“ den Grund.
    html += `<tr class="wt-vt-sum"><th scope="row">${escapeHtml(t('Verbrauch (Bilanz)'))}</th>`;
    for (const year of cols) {
      const rank = ranks[year];
      if (rank.total == null) {
        html += `<td class="${sorted(year).trim()}"><span class="wt-vt-main">–</span></td>`;
        continue;
      }
      const rest = entryOf(year, data.rest.key);
      const sum = {delta: rank.totalPrev != null ? rank.total - rank.totalPrev : null, prev: rank.totalPrev, note: rest ? rest.note : ''};
      const label = year === data.window.year ? t('{year} bis {date}', {year, date: until}) : year;
      html += `<td class="${sorted(year).trim()}" data-tooltip-fixed="${escapeHtml(`${t('Verbrauch (Bilanz)')} ${label}: ${energy(rank.total)}`)}">`
        + `<span class="wt-vt-main">${escapeHtml(pct ? '100 %' : kwhText(rank.total))}</span>`
        + `<span class="wt-vt-sub">${escapeHtml(pct ? `${kwhText(rank.total)} kWh` : '100 %')}</span>`
        + `${hasPrev(year) ? rankDelta(sum, false) : ''}</td>`;
    }
    html += `</tr><tr class="wt-vt-cov"><th scope="row">${escapeHtml(t('Gemessen'))}</th>`;
    for (const year of cols) {
      const rank = ranks[year];
      if (!rank.total) {
        html += `<td class="${sorted(year).trim()}"><span class="wt-vt-main">–</span></td>`;
        continue;
      }
      const tip = t('Eigene Zähler erfassen {measured} von {total} kWh', {measured: kwhText(rank.measured), total: kwhText(rank.total)});
      html += `<td class="${sorted(year).trim()}" data-tooltip-fixed="${escapeHtml(tip)}">`
        + `<span class="wt-vt-main">${escapeHtml(`${num(Math.min(100, rank.measured / rank.total * 100), 0)} %`)}</span>`
        + `<span class="wt-vt-sub">${escapeHtml(`${kwhText(rank.measured)} kWh`)}</span></td>`;
    }
    return `${html}</tr></tbody>`;
  }

  // Fußzeile der Tabelle: Sortierung, Änderung und die Teiljahre, soweit sie in der Tabelle stehen.
  function sharesTableFoot(data, years, sort) {
    const parts = [
      sort.dir === 'desc' ? t('Sortiert nach {year}, größter zuerst.', {year: sort.year}) : t('Sortiert nach {year}, kleinster zuerst.', {year: sort.year}),
      t('Ein Klick auf einen Jahreskopf sortiert nach diesem Jahr, ein zweiter dreht die Reihenfolge. „Übrige (Grundlast)“ und die Summen stehen immer unten.'),
      t('Die Änderung zum Vorjahr ist in kWh gerechnet; rot: mehr Verbrauch, grün: weniger, „–“ mit Grund, wenn das Vorjahr nicht ganz erfasst ist.'),
    ];
    if (years.includes(data.window.year)) {
      parts.push(t('{year} zählt bis {date} und vergleicht mit demselben Stück des Vorjahres.', {year: data.window.year, date: fmtDate(formats.dm, data.window.cur.end - 1)}));
    }
    const firstYear = partialFirstYear(data);
    if (firstYear != null && years.includes(firstYear)) {
      parts.push(t('* Das Archiv beginnt erst am {date}, {year} ist kein ganzes Jahr.', {date: fmtDate(formats.dmy, data.rest.first_ts), year: firstYear}));
    }
    parts.push(t('Das Dreieck nennt Messlücken, Zählerrückgänge und einen verzerrten Vergleich. Ein Klick auf eine Zeile zeigt die Monate unten.'));
    return parts.join(' ');
  }

  let lastTable = null;

  // hidden: im Setup abgewählte Bereiche (Schlüssel wie energie_langzeit.CARD_BLOCKS); deren Daten werden gar nicht erst geladen.
  // hasConsumers: es gibt Verbraucher der obersten Ebene, dann steht der Umschalter „Summen | Verbraucher“ über dem Reiter.
  window.energieLangzeit = function energieLangzeit(hidden = [], hasConsumers = false) {
    const shown = (key) => !hidden.includes(key);
    return {
      columnKeys: COLUMN_KEYS,
      columns: [...COLUMN_KEYS],
      vorjahr: true,
      diff: true,
      mode: 'bis_jetzt',
      error: '',
      opened: false,
      _seq: 0,
      _changesSeq: 0,
      _kenntageSeq: 0,
      months: null,
      monthsError: '',
      changes: null,
      records: null,
      recordsError: '',
      monthlyRole: 'erzeugung',
      matrixRole: 'erzeugung',
      band: true,
      yearLimit: 10,
      ktMode: 'full',
      ktDialog: {title: '', sub: '', html: '', error: ''},
      lzView: 'sum',
      sumLoaded: false,
      consumers: null,
      consumersError: '',
      _consumersSeq: 0,
      rankSort: 'kwh',
      // Gewähltes Jahr der Rangliste; null = das Jahr des Stichtags (folgt dann der Stichtag-Leiste).
      rankYearSel: null,
      sharesMode: 'pct',
      // Tabelle „Verbraucher × Jahre“: sortiert nach einem Jahr (null = dem neuesten).
      sharesSortYear: null,
      sharesSortDir: 'desc',
      // Hervorgehobener Verbraucher (Schlüssel aus langzeit/consumers, „rest“ für Übrige) oder null.
      pick: null,
      // Verbraucher der Matrix „Jahre × Monate je Verbraucher“ (null = der erste).
      matrixSel: null,

      init() {
        const saved = readStore();
        if (hasConsumers && (saved.view === 'sum' || saved.view === 'verb')) this.lzView = saved.view;
        if (saved.rankSort === 'kwh' || saved.rankSort === 'chg') this.rankSort = saved.rankSort;
        if (saved.sharesMode === 'pct' || saved.sharesMode === 'kwh') this.sharesMode = saved.sharesMode;
        if (Array.isArray(saved.columns)) this.columns = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (typeof saved.diff === 'boolean') this.diff = saved.diff;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        if ([0, 5, 10].includes(saved.yearLimit)) this.yearLimit = saved.yearLimit;
        if (saved.ktMode === 'full' || saved.ktMode === 'ytd') this.ktMode = saved.ktMode;
        // Geladen wird erst beim ersten Öffnen des Reiters: Die Übersicht ist die Startansicht und
        // soll nicht auf eine verborgene Tabelle warten.
        window.addEventListener('energie-tab', (event) => {
          if (!this.$root.isConnected) return;
          if (event.detail === 'long' && !this.opened) {
            this.opened = true;
            this.loadView();
          } else if (event.detail === 'long' && this.months) {
            // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
            this.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
          }
        });
        // Stichtag-Leiste des App-Standards: bei jeder Änderung Tabelle und Rangliste neu laden (was schon geladen ist).
        // Nach einer Größenänderung neu zeichnen: welche Beschriftung in ihren Abschnitt passt, hängt von der Breite ab.
        // Die Tabelle braucht dabei nur den Scroll-Hinweis neu, der ebenfalls an der Breite hängt.
        window.addEventListener('resize', () => {
          if (!this.$root.isConnected || !this.consumers) return;
          this.drawShares();
          this.drawSharesTable();
        });
        window.addEventListener('dashboard-stichtag', () => {
          if (!this.$root.isConnected || !this.opened) return;
          if (this.sumLoaded) {
            this.loadTable();
            if (shown('veraenderungen')) this.loadChanges();
          }
          if (this.consumers) {
            // Stand die Rangliste auf dem Jahr des bisherigen Stichtags, folgt sie dem neuen.
            if (this.rankYearSel != null && this.rankYearSel >= this.consumers.window.year) this.rankYearSel = null;
            this.loadConsumers(false);
          }
        });
      },

      // Lädt die Daten der gewählten Ansicht, jede nur beim ersten Zeigen.
      loadView() {
        if (this.lzView === 'verb') {
          if (!this.consumers) this.loadConsumers(true);
          return;
        }
        if (this.sumLoaded) return;
        this.sumLoaded = true;
        this.loadTable();
        if (shown('monatsvergleich') || shown('matrix')) this.loadMonths();
        if (shown('rekorde') || shown('kenntage')) this.loadRecords();
        if (shown('veraenderungen')) this.loadChanges();
      },

      setView(view) {
        if (!hasConsumers) return;
        this.lzView = view;
        writeStore({view});
        if (this.opened) this.loadView();
        // Diagramme, die verborgen angelegt wurden, kennen ihre Breite erst jetzt.
        if (view === 'sum') this.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
      },

      // Verbraucher über die Jahre. withMonths=false lädt nur das Fenster der Rangliste (Stichtag) und behält die Monatswerte.
      async loadConsumers(withMonths) {
        const seq = ++this._consumersSeq;
        const months = withMonths || !this.consumers;
        this.consumersError = '';
        try {
          const res = await fetch('energiedashboard/langzeit/consumers', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({at: this.stichtag(), months}),
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
        this.drawSharesTable();
      },

      sharesNote() {
        return this.sharesMode === 'pct'
          ? t('Anteil jedes Verbrauchers am Verbrauch des Jahres laut Bilanz. Das laufende Jahr (*) und das erste Jahr des Archivs sind nicht vollständig.')
          : t('Jahressummen in kWh, alle Balken im selben Maßstab. Das laufende Jahr (*) und das erste Jahr des Archivs sind nicht vollständig.');
      },

      sharesTableNote() {
        const text = this.sharesMode === 'pct'
          ? t('Je Feld der Anteil am Verbrauch des Jahres laut Bilanz, darunter die kWh und die Änderung zum Vorjahr.')
          : t('Je Feld die kWh des Jahres, darunter der Anteil am Verbrauch laut Bilanz und die Änderung zum Vorjahr.');
        return `${text} ${t('Jede Spalte ist die Rangliste ihres Jahres: Es gilt der Stichtag oben, das Jahr des Stichtags zählt bis zu diesem Tag.')}`;
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

      // Jahre der Tabelle: wie in der Rangliste bis zum Jahr des Stichtags, so viele wie in der Leiste „Jahre“ gewählt.
      sharesTableYears() {
        return this.rankYears().map((option) => option.year);
      },

      // Sortierung der Tabelle; ein Jahr, das nicht mehr gezeigt wird, fällt auf das neueste zurück.
      sharesSort() {
        const years = this.sharesTableYears();
        if (!years.includes(this.sharesSortYear)) return {year: years[0], dir: this.sharesSortYear == null ? this.sharesSortDir : 'desc'};
        return {year: this.sharesSortYear, dir: this.sharesSortDir};
      },

      drawSharesTable() {
        const data = this.consumers;
        const table = this.$refs.sharesTable;
        if (!data || !data.years || !table) return;
        const years = this.sharesTableYears();
        if (this.sharesSortYear != null && !years.includes(this.sharesSortYear)) {
          this.sharesSortYear = null;
          this.sharesSortDir = 'desc';
        }
        if (!years.length) {
          table.innerHTML = '';
          if (this.$refs.sharesTableFoot) this.$refs.sharesTableFoot.textContent = '';
          return;
        }
        const sort = this.sharesSort();
        table.innerHTML = sharesTableHtml(data, years, this.sharesMode, sort, this.matrixKey(), this.yearLimit);
        if (this.$refs.sharesTableFoot) this.$refs.sharesTableFoot.textContent = sharesTableFoot(data, years, sort);
        // Scroll-Hinweis des App-Standards (.tbl-wrap.is-scrollable), nur wenn die Tabelle wirklich breiter ist.
        const wrap = this.$refs.sharesTableWrap;
        if (wrap) requestAnimationFrame(() => wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1));
      },

      // Klick auf einen Jahreskopf sortiert nach diesem Jahr (ein zweiter dreht die Reihenfolge), auf eine Zeile wählt den
      // Verbraucher für die Matrix wie ein Klick auf die Balken; der Name öffnet die Entität.
      sharesTableClick(event) {
        if (event.target.closest('a')) return;
        const head = event.target.closest('button[data-year]');
        if (head) {
          const year = Number(head.dataset.year);
          const sort = this.sharesSort();
          if (year === sort.year) this.sharesSortDir = sort.dir === 'desc' ? 'asc' : 'desc';
          else this.sharesSortDir = 'desc';
          this.sharesSortYear = year;
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

      // Jahre der Rangliste: vom Jahr des Stichtags zurück bis zum Beginn des Archivs.
      rankYears() {
        const data = this.consumers;
        if (!data || !data.years) return [];
        const until = fmtDate(formats.dm, data.window.cur.end - 1);
        const past = data.years.filter((year) => year <= data.window.year);
        return (this.yearLimit ? past.slice(0, this.yearLimit) : past).map((year) => ({
          year, label: year === data.window.year ? t('{year} bis {date}', {year, date: until}) : String(year),
        }));
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

      setRankSort(sort) {
        this.rankSort = sort;
        writeStore({rankSort: sort});
        this.drawRank();
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
        return t('Verbraucher der obersten Ebene; Geräte hinter einem Zwischenzähler stecken im Zwischenzähler. Im Jahr des Stichtags zählt der 1. Januar bis zum Stichtag, in den Jahren davor das ganze Jahr. Verbraucher, die später als die Bilanz erfasst werden, stecken vorher in „Übrige (Grundlast)“, das Dreieck dort nennt sie. Gelöschte Verbraucher fehlen auch in den Vorjahren, ihr Verbrauch steht dort in „Übrige“.');
      },

      drawRank() {
        const data = this.consumers;
        const host = this.$refs.rank;
        if (!data || !host) return;
        const rank = consumerRank(data, this.rankYear(), this.yearLimit);
        host.style.setProperty('--vr-years', String(sparkYears(data, rank.limit).length));
        host.innerHTML = rankHtml(data, rank, this.rankSort, this.matrixKey());
        if (this.$refs.rankCover) this.$refs.rankCover.innerHTML = coverHtml(rank);
      },

      columnName,

      // Anzahl der Jahre für Monatsvergleich, Jahre × Monate und Kenntage (0 = alle); die Jahre stehen neueste zuerst.
      yearCount() {
        const m = this.months ? this.months.years.length : 0;
        const k = this.records && this.records.kenntage ? this.records.kenntage.years.length : 0;
        const c = this.consumers && this.consumers.years ? this.consumers.years.length : 0;
        return Math.max(m, k, c);
      },

      yearOptions() {
        return [
          {value: 5, label: t('Letzte 5')},
          {value: 10, label: t('Letzte 10')},
          {value: 0, label: t('Alle ({n})', {n: this.yearCount()})},
        ];
      },

      setYearLimit(value) {
        this.yearLimit = value;
        writeStore({yearLimit: value});
        this.drawMonthly();
        this.drawMatrix();
        this.drawKenntage();
        this.drawShares();
        this.drawSharesTable();
        this.drawConsumerMatrix();
        this.drawRank();
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

      // Differenzzeile unter den Werten ein- oder ausblenden; die Daten bleiben, nur die Tabelle wird neu gezeichnet.
      toggleDiff() {
        this.diff = !this.diff;
        writeStore({diff: this.diff});
        if (lastTable) this.paintTable(lastTable);
      },

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
        this.loadChanges();
      },

      modeHint() {
        const base = this.mode === 'bis_jetzt'
          ? t('Laufende Zeiträume werden mit demselben Stück des Vorjahres verglichen. Der letzte Monat ist in beiden Fällen gleich.')
          : t('Laufende Zeiträume stehen neben dem kompletten Vorjahreszeitraum. Das Vorjahr hat dadurch meist höhere Summen.');
        const stichtag = this.stichtag();
        if (!stichtag) return base;
        const date = fmtDate(formats.dmy, Date.parse(stichtag + 'T12:00:00Z') / 1000);
        return `${base} ${t('Die Tabelle zeigt die Zeiträume zum {date}.', {date})}`;
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

      // Rekorde und Kenntage je Jahr: einmal je Seitenaufruf, sie hängen nicht am Stichtag.
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

      // Verbraucher mit der größten Änderung; Stichtag und Vorjahr-Modus wie die Vergleichstabelle.
      async loadChanges() {
        const seq = ++this._changesSeq;
        try {
          const res = await fetch('energiedashboard/langzeit/changes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mode: this.mode, at: this.stichtag()}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._changesSeq) return;  // eine neuere Anfrage hat überholt
          this.changes = data;
          this.$nextTick(() => this.drawChanges());
        } catch (e) {
          if (seq === this._changesSeq) this.changes = null;
        }
      },

      changesNote() {
        return t('Verbraucher mit der größten Änderung gegenüber dem Vorjahr, im Zeitraum Dieses Jahr (Stichtag und Vorjahr wie in der Vergleichstabelle). Rot: mehr Verbrauch, grün: weniger. Geräte hinter einem Zwischenzähler stehen im Zwischenzähler. „Übrige (Grundlast)“ ist der Rest bis zum Verbrauch laut Bilanz.');
      },

      changesRange() {
        return this.changes ? changesRange(this.changes) : '';
      },

      drawChanges() {
        const c = this.changes;
        const box = this.$refs.changes;
        if (!c || !box) return;
        box.innerHTML = energyChanges(c, {links: true});
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

      async loadTable() {
        const seq = ++this._seq;
        this.error = '';
        try {
          const res = await fetch('energiedashboard/langzeit/table', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({columns: this.columns, vorjahr: this.vorjahr, mode: this.mode, at: this.stichtag()}),
          });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          if (seq !== this._seq) return;  // eine neuere Anfrage hat überholt
          lastTable = data;
          this.paintTable(data);
        } catch (e) {
          if (seq === this._seq) this.error = t('Die Tabelle konnte nicht geladen werden.');
        }
      },
    };
  };
})();
