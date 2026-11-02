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

  window.wetterDashboard = function wetterDashboard() {
    return {
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
