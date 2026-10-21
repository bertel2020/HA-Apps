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

  function fmtShare(share) {
    const digits = share < 1 ? 1 : 0;
    return `${share.toLocaleString(window.ZA_FORMAT, {minimumFractionDigits: digits, maximumFractionDigits: digits})} %`;
  }

  return {
    UNAVAILABLE, color, swatchStyle, unavailablePattern, fill, label, raw,
    segments, bucketsFromSegments, bucketsFromShares, bucketFns, totals, fmtShare,
    RANGE_BUCKETS, SERVER_SHARE_RANGES, bucketStarts,
  };
})();
