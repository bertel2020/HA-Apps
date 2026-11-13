// Hält beim Wechsel der Zeitraum-Auflösung den aktuell sichtbaren zeitlichen
// Kontext fest. Vergangene Perioden verwenden ihren Mittelpunkt als stabilen
// Zoom-Anker; für die laufende Periode bleibt "jetzt" der Anker. Das Ergebnis
// ist wieder derselbe relative Perioden-Offset, den /api/query erwartet.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.PeriodNavigation = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  const HOUR_MS = 60 * 60 * 1000;
  const DAY_MS = 24 * HOUR_MS;

  // Kalenderbestandteile in der Zeitzone des SERVERS (ServerTime, static/js/server-time.js): Der Server rechnet
  // jeden Zeitraum in seiner Zone, "heute" und das gewählte Datum müssen deshalb dort bestimmt werden. Mit der
  // Browserzone verschöbe sich bei abweichender Zone der Versatz um eine Periode (Browser noch beim Vortag). Ohne
  // ServerTime (Tests, Karte) gilt wie früher die Browserzone.
  function serverTime() {
    const st = root && root.ServerTime;
    return st && st.zone ? st : null;
  }

  function calendar(ms) {
    const st = serverTime();
    if (st) {
      const p = st.parts(ms / 1000);
      return {year: p.year, month: p.month - 1, day: p.day, hour: p.hour, weekday: p.weekday};
    }
    const d = new Date(ms);
    return {year: d.getFullYear(), month: d.getMonth(), day: d.getDate(), hour: d.getHours(), weekday: d.getDay()};
  }

  // Date.UTC wird hier absichtlich nur mit den KALENDERbestandteilen gefüttert. Dadurch ist die Differenz "ein
  // Kalendertag" auch über eine Sommer-/Winterzeit-Umstellung exakt 1 statt 23 bzw. 25 Stunden.
  function hourIndex(c) {
    return Math.floor(Date.UTC(c.year, c.month, c.day, c.hour) / HOUR_MS);
  }

  function dayIndex(c) {
    return Math.floor(Date.UTC(c.year, c.month, c.day) / DAY_MS);
  }

  function mondayIndex(c) {
    return dayIndex(c) - (c.weekday + 6) % 7; // Montag = 0
  }

  // 12 Uhr des Kalendertags y-m-d (m = 1–12) in der Serverzone als Anker in ms: Mittags, damit eine Zeitumstellung
  // den Tag nicht verschiebt.
  function dayAnchor(y, m, d) {
    const st = serverTime();
    return st ? st.toEpoch(y, m, d, 12) * 1000 : new Date(y, m - 1, d, 12).getTime();
  }

  // Umkehrung für die Adresse eines festen Zeitraums: das Datum JJJJ-MM-TT von ms in der Serverzone.
  function anchorDate(ms) {
    const c = calendar(ms);
    const pad = n => String(n).padStart(2, '0');
    return `${c.year}-${pad(c.month + 1)}-${pad(c.day)}`;
  }

  function anchorForWindow(windowStart, windowEnd, isCurrent, nowMs = Date.now()) {
    if (isCurrent || windowStart == null || windowEnd == null) return nowMs;
    const startMs = windowStart * 1000;
    const endMs = windowEnd * 1000;
    return startMs + (endMs - startMs) / 2;
  }

  function offsetForRange(range, anchorMs, nowMs = Date.now()) {
    const anchor = calendar(anchorMs);
    const now = calendar(nowMs);
    let offset = 0;
    switch (range) {
      case 'hour':
        offset = hourIndex(anchor) - hourIndex(now);
        break;
      case 'day':
        offset = dayIndex(anchor) - dayIndex(now);
        break;
      case 'week':
        offset = Math.round((mondayIndex(anchor) - mondayIndex(now)) / 7);
        break;
      case 'month':
        offset = (anchor.year - now.year) * 12 + anchor.month - now.month;
        break;
      case 'year':
        offset = anchor.year - now.year;
        break;
      case 'decade':
        offset = Math.floor(anchor.year / 10) - Math.floor(now.year / 10);
        break;
      default:
        throw new Error(t('Unbekannter Zeitraum: {range}',{range}));
    }
    // Die Query-Engine erlaubt bewusst keine Navigation in die Zukunft.
    return Math.min(offset, 0);
  }

  return {anchorForWindow, offsetForRange, dayAnchor, anchorDate};
});
