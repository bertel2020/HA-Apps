// Rechenkern des Reiters „Heizen“ (Wetterdashboard): Gradtagzahl, Verbrauch je Gradtag, Ausgleichsgerade
// und Anteile je Zähler. Reine Funktionen über die Tageszeilen des Servers
// (wetterdashboard/heizen): days = [[ISO-Datum, Tages-Ø °C, [kWh je Zähler]], …].
//
// Im Browser statt auf dem Server, damit Heizgrenze, Raumtemperatur und die Auswahl der Zähler ohne
// Rundlauf änderbar sind. Datumsvergleiche laufen über ISO-Strings (sortieren wie Datum), so gibt es
// keine Zeitzonenfalle. Die Heizperiode ist fest 1. Oktober bis 30. April.
(function (root) {
  const MONTHS = [10, 11, 12, 1, 2, 3, 4];
  const LAST_DAY = {10: 31, 11: 30, 12: 31, 1: 31, 2: 29, 3: 31, 4: 30};

  const pad = (n) => String(n).padStart(2, '0');

  // Startjahr der Heizperiode, zu der ein Tag gehört (Jan–Apr gehören zu der des Vorjahres); null im Sommer.
  function periodOf(iso) {
    const year = Number(iso.slice(0, 4));
    const month = Number(iso.slice(5, 7));
    if (!MONTHS.includes(month)) return null;
    return month >= 10 ? year : year - 1;
  }

  // "2025/26"
  function label(period) {
    return `${period}/${pad((period + 1) % 100)}`;
  }

  // Gradtagzahl G<room>/<limit> (VDI 3807): nur Tage unter der Heizgrenze zählen.
  function gradtage(tm, room, limit) {
    return tm < limit ? room - tm : 0;
  }

  function dayKwh(day, on) {
    let sum = 0;
    day[2].forEach((value, index) => {
      if (!on || on[index]) sum += value;
    });
    return sum;
  }

  // Summe über [from, to] (ISO, beide inklusive): Gradtage, kWh, Tage und kWh je Gradtag.
  function stat(days, opts, from, to) {
    let gt = 0;
    let kwh = 0;
    let n = 0;
    for (const day of days) {
      if (day[0] < from || day[0] > to) continue;
      kwh += dayKwh(day, opts.on);
      gt += gradtage(day[1], opts.room, opts.limit);
      n += 1;
    }
    return {gt, kwh, n, q: gt > 0 ? kwh / gt : null};
  }

  function monthRange(period, month) {
    const year = month >= 10 ? period : period + 1;
    const last = month === 2 ? 29 : LAST_DAY[month];
    return [`${year}-${pad(month)}-01`, `${year}-${pad(month)}-${pad(last)}`];
  }

  function periodRange(period) {
    return [`${period}-10-01`, `${period + 1}-04-30`];
  }

  // Heizperiode bis zum selben Kalendertag wie „gestern“ (für den fairen Vergleich der laufenden Periode);
  // außerhalb der Heizperiode (Mai–September) die ganze Periode.
  function cutRange(period, todayIso) {
    const [from, to] = periodRange(period);
    const month = Number(todayIso.slice(5, 7));
    if (!MONTHS.includes(month)) return {from, to, label: null};
    const y = new Date(Date.UTC(Number(todayIso.slice(0, 4)), month - 1, Number(todayIso.slice(8, 10))) - 86400000);
    const ym = y.getUTCMonth() + 1;
    if (!MONTHS.includes(ym)) return {from, to, label: null};
    const year = ym >= 10 ? period : period + 1;
    const end = `${year}-${pad(ym)}-${pad(y.getUTCDate())}`;
    return {from, to: end, label: {day: y.getUTCDate(), month: ym}};
  }

  // Aktuelle Heizperiode: die zuletzt begonnene.
  function currentPeriod(todayIso) {
    const year = Number(todayIso.slice(0, 4));
    return Number(todayIso.slice(5, 7)) >= 10 ? year : year - 1;
  }

  // Heizperioden mit Daten, neueste zuerst.
  function periods(days) {
    return [...new Set(days.map((day) => periodOf(day[0])).filter((p) => p != null))].sort((a, b) => b - a);
  }

  // Kennzahlen einer Heizperiode für die Kacheln: Summe, Heiztage (Tage unter der Heizgrenze) und kWh je Zähler.
  // Die laufende Periode zählt bis gestern und wird gegen denselben Ausschnitt der Vorperiode gestellt, jede andere
  // als ganze Periode gegen die ganze Vorperiode. before ist null, wenn es keine Vorperiode mit Daten gibt.
  function summary(days, opts, period, todayIso) {
    const live = MONTHS.includes(Number(todayIso.slice(5, 7))) && period === currentPeriod(todayIso);
    const measure = (p) => {
      let [from, to] = periodRange(p);
      if (live) ({from, to} = cutRange(p, todayIso));
      const base = stat(days, opts, from, to);
      let heatDays = 0;
      const perCounter = [];
      for (const day of days) {
        if (day[0] < from || day[0] > to) continue;
        if (day[1] < opts.limit) heatDays += 1;
        day[2].forEach((value, index) => { perCounter[index] = (perCounter[index] || 0) + value; });
      }
      return {...base, heatDays, perCounter};
    };
    const now = measure(period);
    const hasBefore = periods(days).includes(period - 1);
    return {now, before: hasBefore ? measure(period - 1) : null, running: live};
  }

  // Ausgleichsgerade (kleinste Quadrate) über die Tage unter der Heizgrenze einer Periode:
  // kWh je Tag gegen Tages-Ø. slope ist negativ; -slope sind kWh je Kelvin. Null ohne tragfähige Grundlage.
  // from (optional) schneidet die kalten Tage ab: unter etwa 0 °C flacht der Verbrauch ab, eine Gerade über alles
  // zöge die Steigung dort herunter.
  function fit(days, opts, period, from = -Infinity) {
    const xs = [];
    const ys = [];
    for (const day of days) {
      if (periodOf(day[0]) !== period || day[1] >= opts.limit || day[1] < from) continue;
      xs.push(day[1]);
      ys.push(dayKwh(day, opts.on));
    }
    const n = xs.length;
    if (n < 5) return null;
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    let sxx = 0;
    let sxy = 0;
    let syy = 0;
    xs.forEach((x, i) => {
      sxx += (x - mx) * (x - mx);
      sxy += (x - mx) * (ys[i] - my);
      syy += (ys[i] - my) * (ys[i] - my);
    });
    if (sxx === 0) return null;
    const slope = sxy / sxx;
    return {slope, intercept: my - slope * mx, n, r2: syy === 0 ? 1 : (sxy * sxy) / (sxx * syy)};
  }

  // Geglätteter Verlauf der Tageswerte einer Periode (lokale Gerade mit Dreiecksgewichten, „LOESS“): folgt dem Knick,
  // wo der Verbrauch im Frost abflacht. Punkte von der kältesten bis zur wärmsten Tag unter der Heizgrenze, im Abstand
  // von etwa 0,5 °C. Null bei zu wenigen Tagen.
  const MIN_SMOOTH_DAYS = 20;

  function smooth(days, opts, period) {
    const pts = [];
    for (const day of days) {
      if (periodOf(day[0]) === period && day[1] < opts.limit) pts.push([day[1], dayKwh(day, opts.on)]);
    }
    const n = pts.length;
    if (n < MIN_SMOOTH_DAYS) return null;
    pts.sort((a, b) => a[0] - b[0]);
    const k = Math.max(12, Math.ceil(n * 0.35));
    const lo = pts[0][0];
    const hi = pts[n - 1][0];
    if (hi - lo < 1) return null;
    const steps = Math.max(2, Math.round((hi - lo) / 0.5));
    const out = [];
    for (let i = 0; i <= steps; i++) {
      const x = lo + (hi - lo) * i / steps;
      const dist = pts.map((p) => Math.abs(p[0] - x)).sort((a, b) => a - b);
      const h = Math.max(dist[Math.min(k, n) - 1], 1e-9);
      let sw = 0;
      let swx = 0;
      let swy = 0;
      let swxx = 0;
      let swxy = 0;
      for (const [px, py] of pts) {
        const u = Math.abs(px - x) / h;
        if (u >= 1) continue;
        const w = (1 - u * u * u) ** 3;
        sw += w;
        swx += w * px;
        swy += w * py;
        swxx += w * px * px;
        swxy += w * px * py;
      }
      if (sw === 0) continue;
      const denom = sw * swxx - swx * swx;
      const y = Math.abs(denom) < 1e-9 ? swy / sw : ((swxx * swy - swx * swxy) + (sw * swxy - swx * swy) * x) / denom;
      out.push([Math.round(x * 100) / 100, Math.round(y * 100) / 100]);
    }
    return out.length >= 2 ? out : null;
  }

  // kWh je Monat und Zähler einer Heizperiode (Reihenfolge Oktober bis April).
  function shares(days, counters, period) {
    const totals = MONTHS.map(() => counters.map(() => 0));
    for (const day of days) {
      if (periodOf(day[0]) !== period) continue;
      const index = MONTHS.indexOf(Number(day[0].slice(5, 7)));
      day[2].forEach((value, i) => {
        totals[index][i] += value;
      });
    }
    return totals;
  }

  // ---- Sonne als zweite Erklärung des Verbrauchs (experimentell) ---------------------------------
  // sun = {ISO-Datum: "sunny" | "mixed" | "overcast"} vom Server. Verglichen wird kWh je Gradtag, nicht kWh je
  // Tag (klare Tage sind oft kälter), und nur an Heiztagen mit mindestens einem Gradtag: bei Gt knapp über 0
  // würde der Tageswert durch eine winzige Zahl geteilt.
  const SUN_CLASSES = ['sunny', 'mixed', 'overcast'];
  const MIN_CLASS_DAYS = 8;
  const MIN_CLEAR_T = 2;

  function mean(values) {
    return values.reduce((a, b) => a + b, 0) / values.length;
  }

  // Je Klasse: Tage, Ø Außentemperatur, Ø kWh je Tag, kWh je Gradtag (Summe durch Summe) und die Tageswerte.
  function classStats(days, opts, sun) {
    const groups = {};
    SUN_CLASSES.forEach((kind) => { groups[kind] = {temps: [], kwh: [], gt: 0, rates: []}; });
    for (const day of days) {
      const kind = sun && sun[day[0]];
      const gt = gradtage(day[1], opts.room, opts.limit);
      if (!groups[kind] || gt < 1) continue;
      const kwh = dayKwh(day, opts.on);
      const group = groups[kind];
      group.temps.push(day[1]);
      group.kwh.push(kwh);
      group.gt += gt;
      group.rates.push(kwh / gt);
    }
    const out = {};
    for (const kind of SUN_CLASSES) {
      const group = groups[kind];
      const total = group.kwh.reduce((a, b) => a + b, 0);
      out[kind] = {
        n: group.kwh.length,
        temp: group.temps.length ? mean(group.temps) : null,
        kwh: group.kwh.length ? mean(group.kwh) : null,
        q: group.gt > 0 ? total / group.gt : null,
        rates: group.rates,
      };
    }
    return out;
  }

  // Unterschied von kWh je Gradtag gegen eine Bezugsklasse (trübe Tage). status: "few" (zu wenig Tage),
  // "unclear" (hebt sich nicht vom Zufall ab: Welch-t unter 2) oder "clear" mit percent (negativ = weniger).
  function compare(group, base) {
    if (!group || !base || group.n < MIN_CLASS_DAYS || base.n < MIN_CLASS_DAYS) return {status: 'few', percent: null};
    const variance = (values) => {
      const m = mean(values);
      return values.reduce((a, v) => a + (v - m) * (v - m), 0) / (values.length - 1);
    };
    const m1 = mean(group.rates);
    const m2 = mean(base.rates);
    const se = Math.sqrt(variance(group.rates) / group.rates.length + variance(base.rates) / base.rates.length);
    const percent = group.q != null && base.q ? (group.q / base.q - 1) * 100 : null;
    if (percent == null) return {status: 'few', percent: null};
    if (se === 0 ? m1 === m2 : Math.abs(m1 - m2) / se < MIN_CLEAR_T) return {status: 'unclear', percent};
    return {status: 'clear', percent};
  }

  const api = {SUN_CLASSES, MIN_CLASS_DAYS, classStats, compare, MONTHS, periodOf, label, gradtage, dayKwh, stat, monthRange, periodRange, cutRange, currentPeriod, periods, fit, smooth, MIN_SMOOTH_DAYS, shares, summary};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WetterHeating = api;
})(typeof window !== 'undefined' ? window : globalThis);
