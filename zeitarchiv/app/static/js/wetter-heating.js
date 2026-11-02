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

  // Ausgleichsgerade (kleinste Quadrate) über die Tage unter der Heizgrenze einer Periode:
  // kWh je Tag gegen Tages-Ø. slope ist negativ; -slope sind kWh je Kelvin. Null ohne tragfähige Grundlage.
  function fit(days, opts, period) {
    const xs = [];
    const ys = [];
    for (const day of days) {
      if (periodOf(day[0]) !== period || day[1] >= opts.limit) continue;
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

  const api = {MONTHS, periodOf, label, gradtage, dayKwh, stat, monthRange, periodRange, cutRange, currentPeriod, periods, fit, shares};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WetterHeating = api;
})(typeof window !== 'undefined' ? window : globalThis);
