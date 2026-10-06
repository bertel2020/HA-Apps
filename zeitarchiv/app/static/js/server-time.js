// Kalenderangaben in der Zeitzone des SERVERS statt der des Browsers.
//
// Der Server rechnet jeden Zeitraum in seiner Zeitzone (Einstellung des Add-ons)
// und liefert Zeitstempel. Wer daraus im Browser mit getFullYear()/getMonth()
// oder toLocaleDateString() ohne timeZone ein Datum macht, bekommt das Datum der
// BROWSER-Zeitzone: liegt sie hinter dem Server (z. B. Portugal bei Server in
// Deutschland), wird aus dem 01.10. 00:00 der 30.09. 23:00, und die Oberfläche
// zeigt "September" bzw. das Vorjahr. Die Zone steht als data-tz am <html>.
window.ServerTime = (() => {
  const zoneName = (() => {
    const name = document.documentElement.dataset.tz || '';
    if (!name) return undefined;
    try {
      new Intl.DateTimeFormat('en-US', {timeZone: name});
      return name;
    } catch (e) {
      return undefined;  // unbekannte Zone: wie bisher die des Browsers
    }
  })();

  // Optionen für toLocale*String() um die Serverzone ergänzen.
  function withZone(options) {
    return zoneName ? {...options, timeZone: zoneName} : options;
  }

  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const partsFormat = new Intl.DateTimeFormat('en-US', withZone({
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', weekday: 'short', hourCycle: 'h23',
  }));

  // Kalenderbestandteile eines Zeitstempels in der Serverzone:
  // month 1–12, weekday 0 = Sonntag (wie Date.getDay()).
  function parts(epochSeconds) {
    const p = {};
    for (const {type, value} of partsFormat.formatToParts(new Date(epochSeconds * 1000))) p[type] = value;
    return {
      year: parseInt(p.year, 10), month: parseInt(p.month, 10), day: parseInt(p.day, 10),
      hour: parseInt(p.hour, 10), minute: parseInt(p.minute, 10), weekday: WEEKDAYS.indexOf(p.weekday),
    };
  }

  return {zone: zoneName, withZone, parts};
})();
