// Gemeinsame Bausteine für Zustands-Entitäten (Typ "state", ROADMAP 1.29) —
// Entitätsseite, Chart-Editor und Dashboard-Kacheln zeichnen dieselben
// Zustände und müssen sie gleich färben, gleich benennen und gleich in
// Abschnitte und Buckets zerlegen. Bisher stand das nur in entity_detail.js.
//
// Farben: fest nach Zustandsindex aus der Chart-Palette, "nicht verfügbar"
// (-1) grau schraffiert. Der Index ist stabil (nie umnummeriert), also auch
// die Farbe eines Zustands — überall, wo er auftaucht.
window.StateEntities = (() => {
  const UNAVAILABLE = -1;

  function color(idx) {
    const css = getComputedStyle(document.body);
    return idx === UNAVAILABLE
      ? css.getPropertyValue('--ink-faint').trim()
      : css.getPropertyValue(`--chart-${(idx % 8) + 1}`).trim();
  }

  function swatchStyle(idx) {
    const c = color(idx);
    return idx === UNAVAILABLE
      ? `background:repeating-linear-gradient(135deg, ${c} 0 2px, transparent 2px 5px);border:1px solid ${c}`
      : `background:${c}`;
  }

  // Schraffur für "nicht verfügbar" — ECharts nimmt ein Canvas als
  // Füllmuster. Bei jedem Zeichnen neu, damit es dem aktuellen Farbschema folgt.
  function unavailablePattern() {
    const css = getComputedStyle(document.body);
    const c = document.createElement('canvas');
    c.width = c.height = 8;
    const x = c.getContext('2d');
    x.fillStyle = css.getPropertyValue('--surface-alt').trim();
    x.fillRect(0, 0, 8, 8);
    x.strokeStyle = css.getPropertyValue('--ink-faint').trim();
    x.lineWidth = 1.6;
    x.beginPath();
    x.moveTo(-2, 10); x.lineTo(10, -2); x.moveTo(6, 10); x.lineTo(10, 6); x.moveTo(-2, 2); x.lineTo(2, -2);
    x.stroke();
    return {image: c, repeat: 'repeat'};
  }

  // Füllung für ECharts: Farbe bzw. Schraffur. pattern einmal je Zeichnen
  // erzeugen und hereinreichen, nicht je Rechteck.
  function fill(idx, pattern) {
    return idx === UNAVAILABLE ? pattern : color(idx);
  }

  // Legende vom Server: [{idx, text, label}], "nicht verfügbar" zuletzt.
  function label(states, idx) {
    if (idx === UNAVAILABLE) return t('nicht verfügbar');
    const st = (states || []).find(x => x.idx === idx);
    return st ? (st.label || st.text) : String(idx);
  }

  function raw(states, idx) {
    const st = (states || []).find(x => x.idx === idx);
    return st ? st.text : '';
  }

  // Rohwechsel -> Abschnitte {s, e, idx, key} in Sekunden. Der letzte reicht
  // bis windowEnd (bei der laufenden Periode: jetzt). key ist der Beginn und
  // verbindet Band im Zeitstrahl und Zeile im Protokoll.
  function segments(points, windowEnd) {
    const end = windowEnd ?? 0;
    const out = [];
    points.forEach((p, i) => {
      const e = i + 1 < points.length ? points[i + 1].ts : end;
      if (!(e > p.ts)) return;
      const idx = Math.round(p.value);
      const last = out[out.length - 1];
      if (last && last.idx === idx && last.e === p.ts) last.e = e;
      else out.push({s: p.ts, e, idx, key: p.ts});
    });
    return out;
  }

  // Abschnitte -> Buckets [{ts, end, states: Map(idx -> {seconds, entries})}].
  // bucketStart(ts) liefert den Beginn des Buckets, bucketNext(start) den des
  // nächsten (beides in Sekunden). Dieselbe Regel wie die Rollups auf dem
  // Server: ein Abschnitt zählt dort als Beginn, wo er anfängt, seine Dauer
  // wird an jeder Bucket-Grenze geteilt.
  function bucketsFromSegments(segs, bucketStart, bucketNext) {
    const map = new Map();
    const at = ts => {
      const start = bucketStart(ts);
      if (!map.has(start)) map.set(start, {ts: start, end: bucketNext(start), states: new Map()});
      return map.get(start);
    };
    const acc = (bucket, idx) => {
      if (!bucket.states.has(idx)) bucket.states.set(idx, {seconds: 0, entries: 0});
      return bucket.states.get(idx);
    };
    let previous = null;
    for (const seg of segs) {
      if (previous === null || previous !== seg.idx) acc(at(seg.s), seg.idx).entries += 1;
      previous = seg.idx;
      let cur = seg.s;
      while (cur < seg.e) {
        const bucket = at(cur);
        const stop = Math.min(seg.e, bucket.end);
        acc(bucket, seg.idx).seconds += stop - cur;
        cur = stop;
      }
    }
    return [...map.values()].sort((a, b) => a.ts - b.ts);
  }

  // Antwort des Servers (chart_type "shares") -> dieselbe Form.
  function bucketsFromShares(points) {
    return points.map(p => ({
      ts: p.ts,
      end: null,
      states: new Map((p.states || []).map(([idx, seconds, entries]) => [idx, {seconds, entries}])),
    }));
  }

  // Lokale Kalendergrenzen für die Buckets aus Abschnitten.
  const bucketFns = {
    minutes(n) {
      const size = n * 60;
      return [ts => Math.floor(ts / size) * size, start => start + size];
    },
    hour() {
      return [
        ts => { const d = new Date(ts * 1000); d.setMinutes(0, 0, 0); return d.getTime() / 1000; },
        start => { const d = new Date(start * 1000); d.setHours(d.getHours() + 1); return d.getTime() / 1000; },
      ];
    },
    day() {
      return [
        ts => { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; },
        start => { const d = new Date(start * 1000); d.setDate(d.getDate() + 1); return d.getTime() / 1000; },
      ];
    },
    month() {
      return [
        ts => { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); d.setDate(1); return d.getTime() / 1000; },
        start => { const d = new Date(start * 1000); d.setMonth(d.getMonth() + 1); return d.getTime() / 1000; },
      ];
    },
    year() {
      return [
        ts => { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); d.setMonth(0, 1); return d.getTime() / 1000; },
        start => { const d = new Date(start * 1000); d.setFullYear(d.getFullYear() + 1); return d.getTime() / 1000; },
      ];
    },
  };

  // Bucket-Stufe der Anteile je Zeitraum — dieselbe Tabelle wie
  // STATE_SHARE_LEVEL in storage/query.py. Bis zur Woche entstehen die
  // Buckets im Browser aus den Rohwechseln, ab dem Monat kommen sie vom Server.
  const RANGE_BUCKETS = {
    hour: () => bucketFns.minutes(5), day: () => bucketFns.hour(), week: () => bucketFns.day(),
    month: () => bucketFns.day(), year: () => bucketFns.month(), decade: () => bucketFns.year(),
  };
  const SERVER_SHARE_RANGES = ['month', 'year', 'decade'];

  // Ansicht, wenn niemand eine gewählt hat: bis zur Woche der Zeitstrahl, ab dem
  // Monat die Anteile (ein Jahr Zeitstrahl wären tausende Rechtecke).
  const defaultMode = range => SERVER_SHARE_RANGES.includes(range) ? 'shares' : 'timeline';

  // Alle Bucket-Anfänge von start bis (exklusiv) end — damit ein Monat auch
  // dort einen Platz auf der Achse hat, wo (noch) nichts vorliegt.
  function bucketStarts(range, start, end) {
    const [bucketStart, bucketNext] = RANGE_BUCKETS[range]();
    const out = [];
    for (let ts = bucketStart(start); ts < end && out.length < 2000; ts = bucketNext(ts)) out.push(ts);
    return out;
  }

  // Summen je Zustand über alle Buckets, in Legendenreihenfolge (Index,
  // "nicht verfügbar" zuletzt). hidden: ausgeblendete Indizes.
  function totals(buckets, states, hidden = []) {
    const sum = new Map();
    for (const b of buckets) {
      for (const [idx, v] of b.states) {
        if (hidden.includes(idx)) continue;
        const a = sum.get(idx) || {seconds: 0, entries: 0};
        a.seconds += v.seconds;
        a.entries += v.entries;
        sum.set(idx, a);
      }
    }
    const order = (states || []).map(st => st.idx);
    for (const idx of sum.keys()) if (!order.includes(idx)) order.splice(order.length - 1, 0, idx);
    const total = [...sum.values()].reduce((s, v) => s + v.seconds, 0) || 1;
    return order.filter(idx => sum.has(idx)).map(idx => ({
      idx, seconds: sum.get(idx).seconds, entries: sum.get(idx).entries, share: sum.get(idx).seconds / total * 100,
    }));
  }

  // Buckets einer Zustands-Serie aus /api/query-multi: ab dem Monat kommen
  // die Anteile als chart_type "shares" vom Server, bis zur Woche die
  // Rohwechsel, aus denen der Browser die Buckets bildet.
  function bucketsForSeries(s, range, windowEnd) {
    if (s.chart_type === 'shares') return bucketsFromShares(s.points || []);
    const [bucketStart, bucketNext] = RANGE_BUCKETS[range]();
    return bucketsFromSegments(segments(s.points || [], windowEnd), bucketStart, bucketNext);
  }

  function bucketLabel(range, ts) {
    const d = new Date(ts * 1000);
    const loc = window.ZA_FORMAT;
    if (range === 'hour' || range === 'day') return d.toLocaleTimeString(loc, {hour: '2-digit', minute: '2-digit'});
    if (range === 'week') return d.toLocaleDateString(loc, {weekday: 'short', day: '2-digit', month: '2-digit'});
    if (range === 'month') return d.toLocaleDateString(loc, {day: '2-digit', month: '2-digit'});
    if (range === 'decade') return d.toLocaleDateString(loc, {year: 'numeric'});
    return d.toLocaleDateString(loc, {month: 'short', year: 'numeric'});
  }

  // Anteile (gestapelt auf 100 %) oder Wechsel (Beginne je Zustand) je Bucket,
  // ein eigenes Diagramm je Zustands-Entität untereinander: Zustandsindizes und
  // Farben gelten nur innerhalb einer Entität, ein gemeinsamer Stapel wäre
  // sinnlos. rows: [{name, states, buckets}]. Gibt eine vollständige
  // ECharts-Option zurück (ohne toolbox); Chart-Editor und Dashboard-Kachel
  // zeichnen damit dasselbe, ebenso die Entitätsseite. hidden: ausgeblendete
  // Zustandsindizes — sie fehlen, die Anteile verteilen sich auf die übrigen.
  function barPanels(rows, {mode, range, windowStart, periodEnd, hidden = []}) {
    const shares = mode === 'shares';
    const css = getComputedStyle(document.body);
    const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
    const muted = css.getPropertyValue('--ink-muted').trim();
    const lineColor = css.getPropertyValue('--border').trim();
    const starts = bucketStarts(range, windowStart, periodEnd);
    const [bucketStart] = RANGE_BUCKETS[range]();
    const labels = starts.map(ts => bucketLabel(range, ts));
    const pattern = unavailablePattern();
    const n = Math.max(rows.length, 1);
    const slot = 100 / n;
    const option = {
      textStyle: {fontFamily: css.getPropertyValue('--font-mono'), color: muted, fontSize: Math.round(12 * scale * 10) / 10},
      grid: [], xAxis: [], yAxis: [], series: [], title: [],
    };
    const panels = rows.map((row, k) => {
      const byStart = new Map(row.buckets.map(b => [bucketStart(b.ts + 1), b]));
      const present = new Set(row.buckets.flatMap(b => [...b.states.keys()]).filter(idx => !hidden.includes(idx)));
      const order = (row.states || []).map(st => st.idx).filter(idx => present.has(idx));
      for (const idx of present) if (!order.includes(idx)) order.splice(idx === UNAVAILABLE ? order.length : Math.max(order.length - 1, 0), 0, idx);
      const totalOf = bucket => bucket ? [...bucket.states].reduce((sum, [idx, v]) => sum + (hidden.includes(idx) ? 0 : v.seconds), 0) : 0;
      option.grid.push(n === 1
        ? {left: 10, right: 20, top: 24, bottom: 40, containLabel: true}
        : {left: 10, right: 20, top: `${k * slot + 7}%`, height: `${slot - 16}%`, containLabel: true});
      option.xAxis.push({type: 'category', gridIndex: k, data: labels, axisTick: {alignWithLabel: true}, axisLabel: {hideOverlap: true}});
      option.yAxis.push(shares
        ? {type: 'value', gridIndex: k, max: 100, splitNumber: n > 1 ? 2 : 5, axisLabel: {formatter: v => `${v} %`}, splitLine: {lineStyle: {color: lineColor}}}
        : {type: 'value', gridIndex: k, minInterval: 1, splitNumber: n > 1 ? 2 : 5, splitLine: {lineStyle: {color: lineColor}}});
      if (n > 1) option.title.push({text: row.name, left: 10, top: `${k * slot + 1}%`, textStyle: {color: muted, fontSize: Math.round(12 * scale * 10) / 10, fontWeight: 'normal'}});
      for (const idx of order) {
        option.series.push({
          id: `${k}:${idx}`, name: label(row.states, idx), type: 'bar', stack: `s${k}`, barCategoryGap: '20%',
          xAxisIndex: k, yAxisIndex: k,
          itemStyle: {color: fill(idx, pattern)}, emphasis: {focus: 'none'},
          data: starts.map(ts => {
            const bucket = byStart.get(ts);
            const v = bucket && bucket.states.get(idx);
            if (!v) return null;
            if (!shares) return v.entries || null;
            const total = totalOf(bucket);
            return total > 0 && v.seconds > 0 ? v.seconds / total * 100 : null;
          }),
        });
      }
      return {row, byStart, order, totalOf};
    });
    option.tooltip = {
      trigger: 'axis', axisPointer: {type: 'shadow'}, appendToBody: true,
      formatter: items => {
        if (!items.length) return '';
        const k = parseInt(String(items[0].seriesId).split(':')[0], 10);
        const panel = panels[k];
        const ts = starts[items[0].dataIndex];
        const head = `${n > 1 ? `${escapeHtml(panel.row.name)} · ` : ''}${escapeHtml(bucketLabel(range, ts))}`;
        const bucket = panel.byStart.get(ts);
        if (!bucket) return head;
        const total = panel.totalOf(bucket);
        const lines = panel.order.filter(idx => bucket.states.has(idx)).map(idx => {
          const v = bucket.states.get(idx);
          const value = shares
            ? `${NumberFormat.fmtDuration(v.seconds)} · ${fmtShare(total ? v.seconds / total * 100 : 0)}`
            : (v.entries === 1 ? t('1 Beginn') : t('{count} Beginne', {count: v.entries.toLocaleString(window.ZA_FORMAT)}));
          return `<span class="dot" style="display:inline-block;width:8px;height:8px;border-radius:2px;${swatchStyle(idx)}"></span> ${escapeHtml(label(panel.row.states, idx))} <strong>${value}</strong>`;
        });
        return `${head}<br>${lines.join('<br>')}`;
      },
    };
    return option;
  }

  // --- Heatmap: Wochentag x Stunde (ROADMAP 1.29, Phase 3) ----------------
  // cells vom Server (chart_type "heatmap"): [wochentag, stunde, beobachtungen,
  // verfügbare_sekunden, [[idx, sekunden], …]], Montag = 0, Ortszeit.
  // Eine Zelle zeigt den Anteil eines gewählten Zustands an der verfügbaren
  // Zeit; "nicht verfügbar" steht in der Liste, zählt aber nicht in den Nenner.
  const HEATMAP_MIN_OBSERVATIONS = 3;

  // Zustände, die in mindestens einer Zelle vorkommen — "nicht verfügbar" ist
  // keiner, den man wählen könnte (sein Anteil an der verfügbaren Zeit wäre
  // sinnlos).
  function heatmapStates(cells) {
    const found = new Set();
    for (const cell of cells) for (const [idx] of cell[4]) if (idx !== UNAVAILABLE) found.add(idx);
    return found;
  }

  function heatmapShare(cell, idx) {
    const available = cell[3];
    if (!(available > 0)) return null;
    const entry = cell[4].find(x => x[0] === idx);
    return entry ? entry[1] / available : 0;
  }

  // Vorbelegung: der Zustand mit der größten Streuung der Zellanteile, aber
  // nicht der häufigste. Der ist meist der Ruhezustand ("Aus", "Angedockt"),
  // fast überall gleich dunkel — und seine Streuung ist die der anderen
  // zusammen, er gewänne also fast immer. Bei zwei Zuständen sind beide
  // Spiegelbilder, dort bleibt der seltenere.
  function heatmapDefault(cells) {
    const stats = [];
    for (const idx of heatmapStates(cells)) {
      const values = cells.map(cell => heatmapShare(cell, idx)).filter(v => v !== null);
      if (!values.length) continue;
      const mean = values.reduce((a, b) => a + b, 0) / values.length;
      const variance = values.reduce((a, b) => a + (b - mean) * (b - mean), 0) / values.length;
      stats.push({idx, mean, variance});
    }
    if (stats.length > 1) {
      const dominant = stats.reduce((a, b) => (b.mean > a.mean ? b : a));
      stats.splice(stats.indexOf(dominant), 1);
    }
    let best = null;
    for (const st of stats) if (!best || st.variance > best.variance) best = st;
    return best ? best.idx : null;
  }

  // Gezeichneter Zustand einer Entität: der gespeicherte (Rohtext, siehe
  // saved_charts.heatmap_states), solange er in den Zellen vorkommt, sonst die
  // Vorbelegung. Der Text statt des Index, weil sich der Index bei einer
  // Neuzuordnung der Zustände verschieben kann.
  function heatmapIndexFor(cells, states, rawText) {
    if (rawText) {
      const st = (states || []).find(x => x.text === rawText && x.idx !== UNAVAILABLE);
      if (st && heatmapStates(cells).has(st.idx)) return st.idx;
    }
    return heatmapDefault(cells);
  }

  // Heatmaps untereinander, eine je Zustands-Entität (Zustandsindizes und
  // Farben gelten nur innerhalb einer Entität). rows: [{name, states, cells,
  // idx}]. Vollständige ECharts-Option ohne toolbox; Entitätsseite, Chart-
  // Editor und Dashboard-Kachel zeichnen damit dasselbe. Bei einer Entität
  // steht die Farbskala unter dem Raster, bei mehreren trägt jeder Titel
  // Entität und Zustand und die Skala entfällt (dunkler heißt überall: öfter).
  // compact: Kachel, kleinere Schrift und engere Ränder.
  function heatmapPanels(rows, {compact = false} = {}) {
    const css = getComputedStyle(document.body);
    const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
    const font = Math.round((compact ? 10 : 12) * scale * 10) / 10;
    const muted = css.getPropertyValue('--ink-muted').trim();
    const surface = css.getPropertyValue('--surface').trim();
    const surfaceAlt = css.getPropertyValue('--surface-alt').trim();
    const ink = css.getPropertyValue('--ink').trim();
    const loc = window.ZA_FORMAT;
    // 1. Januar 2024 war ein Montag.
    const weekdays = Array.from({length: 7}, (_, d) => new Date(2024, 0, 1 + d).toLocaleDateString(loc, {weekday: 'short'}));
    const weekdaysLong = Array.from({length: 7}, (_, d) => new Date(2024, 0, 1 + d).toLocaleDateString(loc, {weekday: 'long'}));
    const hours = Array.from({length: 24}, (_, h) => String(h));
    const n = Math.max(rows.length, 1);
    const single = n === 1 && !compact;
    const slot = 100 / n;
    const option = {
      textStyle: {fontFamily: css.getPropertyValue('--font-mono'), color: muted, fontSize: font},
      grid: [], xAxis: [], yAxis: [], series: [], title: [], visualMap: [],
    };
    rows.forEach((row, k) => {
      const stateName = label(row.states, row.idx);
      const data = row.cells.map(cell => {
        const share = heatmapShare(cell, row.idx);
        const item = {value: [cell[1], cell[0], share === null ? 0 : share * 100, cell[2], cell[3]]};
        // Wenig Beobachtungen: blass, nicht ausgeblendet — sonst wären kurze
        // Zeiträume leer.
        if (cell[2] < HEATMAP_MIN_OBSERVATIONS) item.itemStyle = {opacity: 0.4};
        return item;
      });
      if (single) option.grid.push({left: 10, right: 20, top: 10, bottom: 60, containLabel: true});
      else if (compact) option.grid.push(n === 1
        ? {left: 4, right: 6, top: 6, bottom: 4, containLabel: true}
        : {left: 4, right: 6, top: `${k * slot + 14}%`, height: `${slot - 18}%`, containLabel: true});
      else option.grid.push({left: 10, right: 20, top: `${k * slot + 7}%`, height: `${slot - 16}%`, containLabel: true});
      option.xAxis.push({
        type: 'category', gridIndex: k, data: hours,
        axisLabel: {interval: compact ? 5 : 2}, axisTick: {show: false}, axisLine: {show: false}, splitArea: {show: false},
      });
      option.yAxis.push({
        type: 'category', gridIndex: k, data: weekdays, inverse: true, axisTick: {show: false}, axisLine: {show: false},
        // Mehrere Panels in einer Kachel: für die Wochentage ist kein Platz,
        // ECharts ließe jeden zweiten Namen weg und sie lägen unter dem Titel.
        axisLabel: {show: !(compact && n > 1)},
      });
      if (n > 1) {
        option.title.push({
          text: `${row.name} · ${stateName}`, left: 10, top: `${k * slot + 1}%`,
          textStyle: {color: muted, fontSize: font, fontWeight: 'normal'},
        });
      }
      option.visualMap.push({
        type: 'continuous', seriesIndex: k, min: 0, max: 100, dimension: 2, calculable: false, orient: 'horizontal',
        show: single, left: 'center', bottom: 0, itemWidth: 12, itemHeight: 160, text: ['100 %', '0 %'],
        textStyle: {color: muted}, inRange: {color: [surfaceAlt, color(row.idx)]},
      });
      option.series.push({
        id: `heat:${k}`, type: 'heatmap', xAxisIndex: k, yAxisIndex: k, data,
        itemStyle: {borderColor: surface, borderWidth: compact ? 1 : 2, borderRadius: compact ? 1 : 2},
        emphasis: {itemStyle: {borderColor: ink, borderWidth: 2}},
        label: {show: false},
      });
    });
    option.tooltip = {
      trigger: 'item', appendToBody: true,
      formatter: p => {
        const row = rows[p.seriesIndex] || rows[0];
        const [hour, weekday, share, count, available] = p.value;
        const head = `${n > 1 ? `${escapeHtml(row.name)} · ` : ''}${escapeHtml(weekdays[weekday])}, ${hour}–${hour + 1} ${escapeHtml(t('Uhr'))}`;
        if (!count && !available) return `${head}<br>${escapeHtml(t('keine Daten'))}`;
        const fewer = count < HEATMAP_MIN_OBSERVATIONS;
        const num = {count: count.toLocaleString(loc)};
        let text;
        if (count === 1) text = fewer ? t('nur 1 Beobachtung') : t('1 Beobachtung');
        else text = fewer ? t('nur {count} Beobachtungen', num) : t('{count} Beobachtungen', num);
        return `${head}<br><span class="dot" style="display:inline-block;width:8px;height:8px;border-radius:2px;${swatchStyle(row.idx)}"></span> ` +
          `${escapeHtml(label(row.states, row.idx))} <strong>${fmtShare(share)}</strong><br>` +
          `${escapeHtml(t('im Schnitt {duration} je Stunde', {duration: NumberFormat.fmtDuration(share / 100 * 3600)}))}<br>` +
          `${escapeHtml(text)} · ${escapeHtml(weekdaysLong[weekday])}`;
      },
    };
    return option;
  }

  function fmtShare(share) {
    const digits = share < 1 ? 1 : 0;
    return `${share.toLocaleString(window.ZA_FORMAT, {minimumFractionDigits: digits, maximumFractionDigits: digits})} %`;
  }

  return {
    UNAVAILABLE, color, swatchStyle, unavailablePattern, fill, label, raw,
    segments, bucketsFromSegments, bucketsFromShares, bucketsForSeries, barPanels, bucketFns, totals, fmtShare,
    RANGE_BUCKETS, SERVER_SHARE_RANGES, bucketStarts, defaultMode,
    heatmapStates, heatmapDefault, heatmapIndexFor, heatmapPanels, HEATMAP_MIN_OBSERVATIONS,
  };
})();
