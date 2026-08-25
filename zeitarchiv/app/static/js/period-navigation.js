// Hält beim Wechsel der Zeitraum-Auflösung den aktuell sichtbaren zeitlichen
// Kontext fest. Vergangene Perioden verwenden ihren Mittelpunkt als stabilen
// Zoom-Anker; für die laufende Periode bleibt "jetzt" der Anker. Das Ergebnis
// ist wieder derselbe relative Perioden-Offset, den /api/query erwartet.
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.PeriodNavigation = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const HOUR_MS = 60 * 60 * 1000;
  const DAY_MS = 24 * HOUR_MS;

  // Date.UTC wird hier absichtlich nur mit den LOKALEN Kalenderbestandteilen
  // gefüttert. Dadurch ist die Differenz "ein Kalendertag" auch über eine
  // Sommer-/Winterzeit-Umstellung exakt 1 statt 23 bzw. 25 Stunden.
  function localHourIndex(date) {
    return Math.floor(Date.UTC(
      date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()
    ) / HOUR_MS);
  }

  function localDayIndex(date) {
    return Math.floor(Date.UTC(
      date.getFullYear(), date.getMonth(), date.getDate()
    ) / DAY_MS);
  }

  function monday(date) {
    const weekday = (date.getDay() + 6) % 7; // Montag = 0
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() - weekday);
  }

  function anchorForWindow(windowStart, windowEnd, isCurrent, nowMs = Date.now()) {
    if (isCurrent || windowStart == null || windowEnd == null) return nowMs;
    const startMs = windowStart * 1000;
    const endMs = windowEnd * 1000;
    return startMs + (endMs - startMs) / 2;
  }

  function offsetForRange(range, anchorMs, nowMs = Date.now()) {
    const anchor = new Date(anchorMs);
    const now = new Date(nowMs);
    let offset = 0;
    switch (range) {
      case 'hour':
        offset = localHourIndex(anchor) - localHourIndex(now);
        break;
      case 'day':
        offset = localDayIndex(anchor) - localDayIndex(now);
        break;
      case 'week':
        offset = Math.round((localDayIndex(monday(anchor)) - localDayIndex(monday(now))) / 7);
        break;
      case 'month':
        offset = (anchor.getFullYear() - now.getFullYear()) * 12
          + anchor.getMonth() - now.getMonth();
        break;
      case 'year':
        offset = anchor.getFullYear() - now.getFullYear();
        break;
      case 'decade':
        offset = Math.floor(anchor.getFullYear() / 10) - Math.floor(now.getFullYear() / 10);
        break;
      default:
        throw new Error(`Unbekannter Zeitraum: ${range}`);
    }
    // Die Query-Engine erlaubt bewusst keine Navigation in die Zukunft.
    return Math.min(offset, 0);
  }

  return {anchorForWindow, offsetForRange};
});
