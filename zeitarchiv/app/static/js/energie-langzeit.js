// Energiedashboard, Reiter „Langzeit“ (Roadmap 1.45): Zeiträume im Vergleich mit dem Vorjahr. Der Server
// rechnet alle Summen (POST energiedashboard/langzeit/table); dieses Skript formatiert und zeichnet nur.
// Aufbau und Bedienung wie die Vergleichstabelle des Wetterdashboards (wetter.js). Kalenderangaben laufen
// über ServerTime in der Zeitzone des Servers.
(function () {
  const COLUMN_KEYS = ['monat', 'vormonat', 'jahr'];
  const STORE_KEY = 'zeitarchiv.energie.langzeit';
  const LOCALE = window.ZA_FORMAT;
  const ZT = window.ServerTime;

  const formats = {
    dm: new Intl.DateTimeFormat(LOCALE, ZT.withZone({day: '2-digit', month: '2-digit'})),
    dmy: new Intl.DateTimeFormat(LOCALE, ZT.withZone({day: '2-digit', month: '2-digit', year: 'numeric'})),
    month: new Intl.DateTimeFormat(LOCALE, ZT.withZone({month: 'long'})),
  };
  const fmtDate = (format, ts) => format.format(new Date(ts * 1000));
  const num = (value, decimals) => NumberFormat.fmt(value, decimals);

  // Auswahl-Namen (relativ) — im Tabellenkopf steht stattdessen der konkrete Zeitraum.
  function columnName(key) {
    return {monat: t('Dieser Monat'), vormonat: t('Letzter Monat'), jahr: t('Dieses Jahr')}[key];
  }

  function columnTitle(col) {
    return col.key === 'jahr' ? t('Jahr') : fmtDate(formats.month, col.cur.start);
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

  // Energie in kWh; ab 10.000 kWh als MWh. Kleine Werte mit einer Nachkommastelle, damit ein Monatsanfang
  // nicht als „2 kWh“ gegen „3 kWh“ erscheint.
  function energy(value) {
    if (Math.abs(value) >= 10000) return num(value / 1000, 1) + ' MWh';
    return num(value, Math.abs(value) < 100 ? 1 : 0) + ' kWh';
  }

  // Wert je Art der Zeile: kWh (energy), Prozent (pct, Differenz in Prozentpunkten) oder kWh je kWp (spec).
  function formatValue(row, value) {
    if (row.kind === 'pct') return num(value, 1) + '\u00a0%';
    if (row.kind === 'spec') return num(value, 1) + '\u00a0' + t('kWh/kWp');
    return energy(value);
  }

  // Differenz zum Vorjahr unter dem Wert; die Farbe sagt, ob die Änderung günstig ist (mehr Erzeugung
  // und Einspeisung, weniger Bezug und Verbrauch), nicht, ob die Zahl steigt oder fällt. Zeilen ohne Wertung
  // (Speicher) bleiben ungefärbt.
  function delta(cur, previous, row) {
    if (cur == null || previous == null) return '';
    const diff = cur - previous;
    const zero = Math.round(Math.abs(diff) * 10) === 0;
    let text = (zero ? '± ' : diff > 0 ? '▲ +' : '▼ −');
    // Die Einheit steht schon im Wert darüber; nur bei Prozentpunkten und MWh bleibt sie, damit die Zeile eindeutig ist.
    if (row.kind === 'pct') text += num(Math.abs(diff), 1) + ' ' + t('PP');
    else if (row.kind === 'spec') text += num(Math.abs(diff), 1);
    else text += Math.abs(diff) >= 10000 ? energy(Math.abs(diff)) : num(Math.abs(diff), Math.abs(diff) < 100 ? 1 : 0);
    // Prozentwerte vergleichen sich in Prozentpunkten, dort steht kein zweites Prozent daneben.
    if (row.kind !== 'pct' && previous !== 0 && !zero) {
      const percent = diff / previous * 100;
      text += ' (' + (percent > 0 ? '+' : '−') + num(Math.abs(percent), 0) + ' %)';
    }
    const good = !zero && row.better && (diff > 0) === (row.better === 'up');
    const cls = zero || !row.better ? '' : (good ? ' wt-dl-good' : ' wt-dl-bad');
    return `<span class="wt-dl${cls}">${escapeHtml(text)}</span>`;
  }

  function cellHtml(value, fromTs, vjCell, deltaHtml, row) {
    const cls = vjCell ? 'wt-vj' : 'wt-sep';
    if (value == null) {
      const title = fromTs ? ` title="${escapeHtml(t('Daten ab {date}', {date: fmtDate(formats.dmy, fromTs)}))}"` : '';
      return `<td class="wt-na ${cls}"${title}><span class="wt-v">–</span></td>`;
    }
    return `<td class="${cls}"><span class="wt-v">${formatValue(row, value)}</span>${deltaHtml || ''}</td>`;
  }

  function renderTable(data, showDiff) {
    const cols = data.columns;
    if (!cols.length) return `<tbody><tr><td>${escapeHtml(t('Keine Spalte ausgewählt.'))}</td></tr></tbody>`;
    const span = data.vorjahr ? 2 : 1;
    const year = (range) => String(ZT.parts(range.start).year);
    // Zwei Zeilen: Titel mit Info-Tooltip (Zeitraum), darunter das Jahr der Spalte und ihres Vorjahrs.
    let head = '<thead><tr><th></th>';
    let sub = '<tr class="wt-subhead"><th></th>';
    for (const col of cols) {
      head += `<th class="wt-cur wt-sep" data-tooltip-fixed="${escapeHtml(rangeText(col.cur.start, col.cur.end))}">${escapeHtml(columnTitle(col))}<span class="wt-info" aria-hidden="true">ⓘ</span></th>`;
      sub += `<th class="wt-cur wt-sep">${escapeHtml(year(col.cur))}</th>`;
      if (data.vorjahr) {
        head += `<th class="wt-vj" data-tooltip-fixed="${escapeHtml(rangeText(col.vj.start, col.vj.end))}">${escapeHtml(t('Vorjahr'))}</th>`;
        sub += `<th class="wt-vj">${escapeHtml(year(col.vj))}</th>`;
      }
    }
    let html = head + '</tr>' + sub + '</tr></thead><tbody>';
    let group = null;
    for (const row of data.rows) {
      if (row.group !== group) {
        group = row.group;
        html += `<tr class="wt-grp"><th colspan="${1 + cols.length * span}">${escapeHtml(group)}</th></tr>`;
      }
      html += `<tr><th scope="row">${escapeHtml(row.label)}</th>`;
      for (const col of cols) {
        const cell = row.cells[col.key] || {};
        const deltaHtml = showDiff && data.vorjahr ? delta(cell.cur, cell.vj, row) : '';
        html += cellHtml(cell.cur, cell.cur_from, false, deltaHtml, row);
        if (data.vorjahr) html += cellHtml(cell.vj, cell.vj_from, true, '', row);
      }
      html += '</tr>';
    }
    return html + '</tbody>';
  }

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

  const monthFormat = (style) => new Intl.DateTimeFormat(LOCALE, {month: style, timeZone: 'UTC'});
  const monthName = (month, style) => monthFormat(style).format(new Date(Date.UTC(2000, month - 1, 15)));
  const mean = (values) => values.reduce((a, b) => a + b, 0) / values.length;
  const present = (list) => list.filter((value) => value != null);
  const monthKey = (year, month) => `${year}-${String(month).padStart(2, '0')}`;

  // Mittel, Spanne und Anzahl je Monat über alle abgeschlossenen Jahre (mindestens zwei, sonst kein Vergleich).
  // Der Monat, in dem das Archiv mitten im Monat beginnt, ist nur zum Teil erfasst: er geht in kein Mittel ein.
  const isPartialStart = (role, year, index) => !!role.partial_start && role.partial_start[0] === year && role.partial_start[1] === index + 1;

  function monthStats(role, months) {
    const past = months.years.filter((year) => year < months.current.year);
    const avg = [], low = [], high = [], counts = [];
    for (let i = 0; i < 12; i++) {
      const values = present(past.filter((year) => !isPartialStart(role, year, i)).map((year) => role.values[year][i]));
      counts.push(values.length);
      const enough = values.length >= 2;
      avg.push(enough ? mean(values) : null);
      low.push(enough ? Math.min(...values) : null);
      high.push(enough ? Math.max(...values) : null);
    }
    return {past, avg, low, high, counts};
  }

  const yearSum = (list) => (list.every((value) => value != null) ? list.reduce((a, b) => a + b, 0) : null);

  // Kenntage je Jahr: Beschriftung, Regel und die Tooltip-Zeile je Kenntag. Die Schwellen kommen aus dem Setup.
  const KENNTAG_GROUPS = () => ({eigenversorgung: t('Eigenversorgung'), erzeugung: t('Erzeugung'), speicher: t('Speicher')});
  const KENNTAG_LABELS = () => ({
    fast_autark: t('Fast autark'), ueberschuss: t('Überschusstage'), sonnig: t('Sonnentage'), truebe: t('Trübe Tage'),
    speicher_stark: t('Speicher stark genutzt'), speicher_schwach: t('Speicher kaum genutzt'),
  });

  function kenntagRule(key, th) {
    switch (key) {
      case 'fast_autark': return t('Autarkie ab {value} %', {value: num(th.autark, 0)});
      case 'ueberschuss': return t('mehr Einspeisung als Verbrauch');
      case 'sonnig': return t('ab {value} kWh je kWp', {value: num(th.sonnig, 1)});
      case 'truebe': return t('unter {value} % des Tagesmittels', {value: num(th.truebe, 0)});
      case 'speicher_stark': return t('ab {value} % der Kapazität', {value: num(th.speicher_stark, 0)});
      default: return t('unter {value} % der Kapazität', {value: num(th.speicher_schwach, 0)});
    }
  }

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

  let lastTable = null;

  // hidden: im Setup abgewählte Bereiche (Schlüssel wie energie_langzeit.CARD_BLOCKS); deren Daten werden gar nicht erst geladen.
  window.energieLangzeit = function energieLangzeit(hidden = []) {
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

      init() {
        const saved = readStore();
        if (Array.isArray(saved.columns)) this.columns = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (typeof saved.diff === 'boolean') this.diff = saved.diff;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        if ([0, 5, 10].includes(saved.yearLimit)) this.yearLimit = saved.yearLimit;
        if (saved.ktMode === 'full' || saved.ktMode === 'ytd') this.ktMode = saved.ktMode;
        // Geladen wird erst beim ersten Öffnen des Reiters: Die Übersicht ist die Startansicht und
        // soll nicht auf eine verborgene Tabelle warten.
        window.addEventListener('energie-tab', (event) => {
          if (event.detail === 'long' && !this.opened) {
            this.opened = true;
            this.loadTable();
            if (shown('monatsvergleich') || shown('matrix')) this.loadMonths();
            if (shown('rekorde') || shown('kenntage')) this.loadRecords();
            if (shown('veraenderungen')) this.loadChanges();
          } else if (event.detail === 'long' && this.months) {
            // Ein eben erst sichtbar gewordener Reiter hat beim Anlegen noch keine Breite gehabt.
            this.$nextTick(() => Object.values(charts).forEach((chart) => chart.resize()));
          }
        });
        // Stichtag-Leiste des App-Standards: bei jeder Änderung die Tabelle neu laden.
        window.addEventListener('dashboard-stichtag', () => { if (this.opened) { this.loadTable(); if (shown('veraenderungen')) this.loadChanges(); } });
      },

      columnName,

      // Anzahl der Jahre für Monatsvergleich, Jahre × Monate und Kenntage (0 = alle); die Jahre stehen neueste zuerst.
      yearCount() {
        const m = this.months ? this.months.years.length : 0;
        const k = this.records && this.records.kenntage ? this.records.kenntage.years.length : 0;
        return Math.max(m, k);
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
        const now = new Date();
        const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
        const iso = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;
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
        if (!kt) return '';
        if (this.ktMode !== 'ytd') return t('Ganze Kalenderjahre. Das laufende Jahr (*) zählt bis heute.');
        const date = fmtDate(formats.dmy, Date.parse(kt.today + 'T12:00:00Z') / 1000);
        return t('Alle Jahre nur bis {date} gezählt, damit das laufende Jahr (*) vergleichbar ist.', {date});
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
        const kt = r.kenntage;
        const ytd = this.ktMode === 'ytd';
        const years = this.yearLimit ? kt.years.slice(0, this.yearLimit) : kt.years;
        const current = new Date().getFullYear();
        const groups = KENNTAG_GROUPS();
        const labels = KENNTAG_LABELS();
        let html = '';
        let lastGroup = null;
        for (const key of kt.available) {
          const group = kt.groups[key];
          if (group !== lastGroup) html += `<div class="wt-kt-group">${escapeHtml(groups[group])}</div>`;
          lastGroup = group;
          const rows = years.map((entry) => {
            const f = entry.fields[key];
            const days = ytd ? f.days_ytd : f.days;
            const hit = ytd ? f.hit_ytd : f.hit;
            return {year: entry.year, days, hit, pct: days ? hit / days * 100 : 0};
          }).filter((row) => row.days > 0);
          const top = Math.max(0, ...rows.map((row) => row.pct));
          html += `<div class="wt-kt-col"><h3 class="wt-kt-title">${escapeHtml(labels[key])}</h3>`
            + `<p class="wt-kt-rule">${escapeHtml(kenntagRule(key, kt.thresholds))}</p>`;
          for (const row of rows) {
            const pct = row.hit > 0 && row.pct < 1 ? '< 1 %' : `${num(row.pct, 0)} %`;
            const tip = `${t('Ausgewertete Tage')}: ${num(row.days, 0)}`;
            html += `<button type="button" class="wt-kt-row" data-field="${key}" data-year="${row.year}" data-tooltip-fixed="${escapeHtml(tip)}">`
              + `<span class="wt-kt-year">${row.year}${row.year === current ? '*' : ''}</span>`
              + `<span class="wt-kt-bar"><i style="width:${top > 0 ? (row.pct / top * 100).toFixed(1) : 0}%"></i></span>`
              + `<span class="wt-kt-val"><b>${escapeHtml(num(row.hit, 0))}</b> ${escapeHtml(t('Tage'))} · ${escapeHtml(pct)}</span></button>`;
          }
          html += '</div>';
        }
        host.innerHTML = html;
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
        try {
          const res = await fetch(`energiedashboard/langzeit/kenntage?field=${encodeURIComponent(field)}&year=${year}`);
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
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
          this.ktDialog.error = t('Die Tage konnten nicht geladen werden.');
        }
      },

      // Verbraucher mit der größten Änderung; Stichtag und Vorjahr-Modus wie die Vergleichstabelle.
      async loadChanges() {
        try {
          const res = await fetch('energiedashboard/langzeit/changes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mode: this.mode, at: this.stichtag()}),
          });
          if (!res.ok) throw new Error(String(res.status));
          this.changes = await res.json();
          this.$nextTick(() => this.drawChanges());
        } catch (e) {
          this.changes = null;
        }
      },

      changesNote() {
        return t('Verbraucher mit der größten Änderung gegenüber dem Vorjahr, im Zeitraum Dieses Jahr (Stichtag und Vorjahr wie in der Vergleichstabelle). Rot: mehr Verbrauch, grün: weniger. Geräte hinter einem Zwischenzähler stehen im Zwischenzähler. „Übrige (Grundlast)“ ist der Rest bis zum Verbrauch laut Bilanz.');
      },

      changesRange() {
        const c = this.changes;
        if (!c) return '';
        return `${rangeText(c.cur.start, c.cur.end)} ${t('gegen')} ${rangeText(c.vj.start, c.vj.end)}`;
      },

      drawChanges() {
        const c = this.changes;
        const box = this.$refs.changes;
        if (!c || !box) return;
        const peak = Math.max(...c.items.map((item) => Math.abs(item.diff)), 0.0001);
        box.innerHTML = c.items.map((item) => {
          const more = item.diff > 0;
          const width = Math.max(1, Math.round(Math.abs(item.diff) / peak * 48));
          const side = more ? `left:50%;width:${width}%` : `right:50%;width:${width}%`;
          const color = more ? 'var(--danger)' : 'var(--positive)';
          const percent = item.percent == null ? '' : ` (${item.percent > 0 ? '+' : '−'}${num(Math.abs(item.percent), 0)} %)`;
          const text = (more ? '▲ +' : '▼ −') + energy(Math.abs(item.diff)) + percent;
          const name = item.entity_id
            ? `<a href="entities/${encodeURIComponent(item.entity_id)}">${escapeHtml(item.name)}</a>` : escapeHtml(item.name);
          return `<div class="wt-chg-row"><span class="wt-chg-name">${name}</span>`
            + `<div class="wt-chg-bar"><i style="${side};background:${color}"></i></div>`
            + `<span class="wt-chg-val" style="color:${color}">${escapeHtml(text)}</span></div>`;
        }).join('');
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
        const colors = palette();
        const labels = Array.from({length: 12}, (_x, i) => monthName(i + 1, 'short'));
        const {past, avg, low, high, counts} = monthStats(role, m);
        const current = m.current.year;
        const lastPast = past[0];
        const hasAvg = counts.some((count) => count >= 2);
        const series = [];
        const legend = [];
        if (this.band && hasAvg) {
          series.push({
            name: 'low', type: 'line', stack: 'band', data: low, symbol: 'none', silent: true,
            lineStyle: {opacity: 0}, tooltip: {show: false},
          }, {
            name: 'band', type: 'line', stack: 'band', symbol: 'none', silent: true, lineStyle: {opacity: 0},
            data: high.map((top, i) => (top == null || low[i] == null ? null : top - low[i])),
            areaStyle: {color: colors.prev, opacity: 0.16},
          });
        }
        for (const year of past.slice().reverse()) {
          const strong = year === lastPast;
          const data = role.values[year].map((value, i) => (isPartialStart(role, year, i) ? null : value));
          series.push({
            name: String(year), type: 'line', data, symbol: 'none', connectNulls: false,
            lineStyle: {color: colors.prev, width: strong ? 2 : 1.2, opacity: strong ? 1 : 0.45},
            itemStyle: {color: colors.prev},
          });
          if (strong) legend.push(String(year));
        }
        if (hasAvg) {
          series.push({
            name: 'Ø', type: 'line', data: avg, symbol: 'none',
            lineStyle: {color: colors.text, width: 2, type: 'dashed'}, itemStyle: {color: colors.text},
          });
          legend.push('Ø');
        }
        if (role.values[current]) {
          // Der laufende Monat ist erst zum Teil vergangen und würde die Linie einbrechen lassen.
          const data = role.values[current].map((value, i) => (i + 1 >= m.current.month ? null : value));
          series.push({
            name: String(current), type: 'line', data, symbol: 'circle', symbolSize: 7, z: 5,
            lineStyle: {color: colors.cur, width: 3}, itemStyle: {color: colors.cur},
          });
          legend.unshift(String(current));
        }
        const cell = (value) => (value == null ? '–' : energy(value));
        const chart = chartFor('monthly', element);
        chart.setOption({
          animation: false,
          grid: {left: 8, right: 16, top: element.clientWidth < 520 ? 74 : 34, bottom: 8, containLabel: true},
          legend: {data: legend, top: 0, textStyle: {color: colors.text, fontSize: scaledFont(12)}, itemWidth: 18, itemHeight: 8},
          xAxis: {
            type: 'category', data: labels, boundaryGap: false,
            axisLine: {lineStyle: {color: colors.grid}}, axisTick: {show: false},
            axisLabel: {color: colors.text, fontSize: scaledFont(11)}, splitLine: {lineStyle: {color: colors.grid}},
          },
          yAxis: {
            type: 'value', name: t('kWh'), nameTextStyle: {color: colors.text},
            axisLine: {lineStyle: {color: colors.grid}}, axisTick: {show: false},
            axisLabel: {color: colors.text, fontSize: scaledFont(11)}, splitLine: {lineStyle: {color: colors.grid}},
          },
          tooltip: {
            trigger: 'axis', confine: true,
            backgroundColor: colors.surface, borderColor: colors.grid,
            textStyle: {color: colors.ink, fontSize: scaledFont(12)},
            formatter: (params) => {
              const i = params[0].dataIndex;
              const running = i + 1 === m.current.month;
              const now = role.values[current] ? role.values[current][i] : null;
              let html = `<b>${escapeHtml(monthName(i + 1, 'long'))}</b>`;
              html += `<br>${current}: ${cell(now)}${running && now != null ? ' ' + escapeHtml(t('(bisher)')) : ''}`;
              if (lastPast != null) html += `<br>${lastPast}: ${cell(role.values[lastPast][i])}`;
              if (avg[i] != null) html += `<br>Ø (${counts[i]}): ${cell(avg[i])}`;
              return html;
            },
          },
          series,
        }, true);
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
        const {avg, counts} = monthStats(role, m);
        const closed = m.years.filter((year) => year < m.current.year);
        const fullSums = closed.map((year) => yearSum(role.values[year])).filter((value) => value != null);
        const reference = fullSums.length >= 2 ? mean(fullSums) : null;
        const sign = (diff) => (Math.round(Math.abs(diff)) === 0 ? '±' : (diff > 0 ? '+' : '−')) + num(Math.abs(diff), 0) + ' %';
        const flagText = (flag) => {
          const parts = [];
          if (flag.gaps) parts.push(t('Messlücken: {n} (sonst etwa {typical})', {n: flag.gaps, typical: flag.gaps_typical || 0}));
          if (flag.counter_decreases) parts.push(t('Zählerrückgänge: {n}', {n: flag.counter_decreases}));
          return parts.join(', ');
        };
        const cell = (value, ref, tip, opts) => {
          if (value == null) return '<td class="wt-tm-na">–</td>';
          const attrs = (tip ? ` data-tooltip-fixed="${escapeHtml(tip)}"` : '');
          const klass = [opts.strong ? 'wt-tm-year' : '', opts.flag ? 'wt-tm-flag' : ''].filter(Boolean).join(' ');
          const inner = (opts.href
            ? (content) => `<a class="wt-tm-link" href="${escapeHtml(opts.href)}">${content}</a>`
            : (content) => content);
          if (ref == null) {
            return `<td class="wt-tm-plain ${klass}"${attrs}>${inner(escapeHtml(num(value, 0)))}</td>`;
          }
          const diff = (value - ref) / ref * 100;
          const favorable = role.better === 'up' ? diff > 0 : diff < 0;
          const share = Math.round(Math.min(1, Math.abs(diff) / 30) * 70);
          const style = ` style="background:color-mix(in srgb, ${favorable ? colors.good : colors.bad} ${share}%, ${colors.base})"`;
          return `<td${style}${attrs}${klass ? ` class="${klass}"` : ''}>${inner(
            `<span class="wt-tm-v">${escapeHtml(num(value, 0))}</span><span class="wt-tm-d">${escapeHtml(sign(diff))}</span>`)}</td>`;
        };
        let html = `<thead><tr><th></th>${Array.from({length: 12}, (_x, i) => `<th>${escapeHtml(monthName(i + 1, 'short'))}</th>`).join('')}`
          + `<th class="wt-tm-year">${escapeHtml(t('Jahr Σ'))}</th></tr></thead><tbody>`;
        for (const year of m.years) {
          const running = year === m.current.year;
          html += `<tr><th>${year}${running ? '*' : ''}</th>`;
          role.values[year].forEach((value, i) => {
            const month = i + 1;
            const flag = role.flags[monthKey(year, month)];
            let tip = value == null ? '' : `${monthName(month, 'short')} ${year}: ${energy(value)}`;
            if (value != null && avg[i] != null && !(running && month >= m.current.month) && !isPartialStart(role, year, i)) {
              const diff = (value - avg[i]) / avg[i] * 100;
              tip += ` (${sign(diff)} ${t('zum Mittel')})`;
            }
            if (running && month === m.current.month && value != null) tip += ' – ' + t('laufender Monat, noch nicht abgeschlossen');
            const partialStart = isPartialStart(role, year, i);
            if (partialStart && value != null) tip += ' – ' + t('das Archiv beginnt in diesem Monat');
            if (flag) tip += ' – ' + flagText(flag);
            const href = value == null ? '' : this.matrixLink(role, year, month);
            // Der laufende Monat ist noch nicht abgeschlossen und bekommt keinen Vergleich.
            const ref = (running && month >= m.current.month) || partialStart ? null : (counts[i] >= 2 ? avg[i] : null);
            html += cell(value, ref, tip, {flag: !!flag, href});
          });
          const total = yearSum(role.values[year]);
          const partial = present(role.values[year]);
          const sum = total != null ? total : (partial.length ? partial.reduce((a, b) => a + b, 0) : null);
          const complete = !running && total != null;
          const tipSum = sum == null ? '' : (complete ? t('Summe {year}: {value}', {year, value: energy(sum)})
            : t('Summe {year} bis jetzt: {value}', {year, value: energy(sum)}));
          html += cell(sum, complete ? reference : null, tipSum, {strong: true});
          html += '</tr>';
        }
        html += `<tr class="wt-tm-avg"><th>Ø</th>${avg.map((value, i) => `<td>${value == null ? '–' : escapeHtml(num(value, 0))}</td>`).join('')}`
          + `<td class="wt-tm-year">${reference == null ? '–' : escapeHtml(num(reference, 0))}</td></tr></tbody>`;
        table.innerHTML = html;
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
