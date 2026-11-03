// Energiedashboard, Reiter „Langzeit“ (Roadmap 1.45): Zeiträume im Vergleich mit dem Vorjahr. Der Server
// rechnet alle Summen (POST energiedashboard/langzeit/table); dieses Skript formatiert und zeichnet nur.
// Aufbau und Bedienung wie die Vergleichstabelle des Wetterdashboards (wetter.js). Kalenderangaben laufen
// über ServerTime in der Zeitzone des Servers.
(function () {
  const COLUMN_KEYS = ['vormonat', 'monat', 'jahr'];
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

  // Differenz zum Vorjahr unter dem Wert; die Farbe sagt, ob die Änderung günstig ist (mehr Erzeugung
  // und Einspeisung, weniger Bezug und Verbrauch), nicht, ob die Zahl steigt oder fällt.
  function delta(cur, previous, better) {
    if (cur == null || previous == null) return '';
    const diff = cur - previous;
    const zero = Math.round(Math.abs(diff) * 10) === 0;
    let text = (zero ? '± ' : diff > 0 ? '▲ +' : '▼ −') + energy(Math.abs(diff));
    if (previous !== 0 && !zero) {
      const percent = diff / previous * 100;
      text += ' (' + (percent > 0 ? '+' : '−') + num(Math.abs(percent), 0) + ' %)';
    }
    const good = !zero && (diff > 0) === (better === 'up');
    const cls = zero ? '' : (good ? ' wt-dl-good' : ' wt-dl-bad');
    return `<span class="wt-dl${cls}">${escapeHtml(text)}</span>`;
  }

  function cellHtml(value, fromTs, vjCell, deltaHtml) {
    const cls = vjCell ? 'wt-vj' : 'wt-sep';
    if (value == null) {
      const title = fromTs ? ` title="${escapeHtml(t('Daten ab {date}', {date: fmtDate(formats.dmy, fromTs)}))}"` : '';
      return `<td class="wt-na ${cls}"${title}><span class="wt-v">–</span></td>`;
    }
    return `<td class="${cls}"><span class="wt-v">${energy(value)}</span>${deltaHtml || ''}</td>`;
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
        const deltaHtml = showDiff && data.vorjahr ? delta(cell.cur, cell.vj, row.better) : '';
        html += cellHtml(cell.cur, cell.cur_from, false, deltaHtml);
        if (data.vorjahr) html += cellHtml(cell.vj, cell.vj_from, true, '');
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

  let lastTable = null;

  window.energieLangzeit = function energieLangzeit() {
    return {
      columnKeys: COLUMN_KEYS,
      columns: [...COLUMN_KEYS],
      vorjahr: true,
      diff: true,
      mode: 'bis_jetzt',
      error: '',
      opened: false,
      _seq: 0,

      init() {
        const saved = readStore();
        if (Array.isArray(saved.columns)) this.columns = COLUMN_KEYS.filter((key) => saved.columns.includes(key));
        if (typeof saved.vorjahr === 'boolean') this.vorjahr = saved.vorjahr;
        if (typeof saved.diff === 'boolean') this.diff = saved.diff;
        if (saved.mode === 'bis_jetzt' || saved.mode === 'komplett') this.mode = saved.mode;
        // Geladen wird erst beim ersten Öffnen des Reiters: Die Übersicht ist die Startansicht und
        // soll nicht auf eine verborgene Tabelle warten.
        window.addEventListener('energie-tab', (event) => {
          if (event.detail === 'long' && !this.opened) {
            this.opened = true;
            this.loadTable();
          }
        });
        // Stichtag-Leiste des App-Standards: bei jeder Änderung die Tabelle neu laden.
        window.addEventListener('dashboard-stichtag', () => { if (this.opened) this.loadTable(); });
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
