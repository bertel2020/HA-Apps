// Format der Oberfläche (de-DE / en-GB / en-US): der Server löst es pro Anfrage auf (Einstellung
// "Zahlen- und Datumsformat", sonst Browser) und trägt es als <html data-format> ein — dadurch
// formatieren Server und Skripte immer gleich. Siehe app/formats.py.
window.ZA_FORMAT = document.documentElement.dataset.format || 'de-DE';
// Währung der Oberfläche (EUR / GBP / USD / CHF): der Server löst sie pro Anfrage auf (Einstellung
// "Währung", sonst Home Assistant) und trägt Symbol und Stellung als <html data-currency> (JSON) ein.
// Siehe app/currency.py.
window.ZA_CURRENCY = (() => {
  try { return JSON.parse(document.documentElement.dataset.currency); } catch (_) {
    return {code: 'EUR', symbol: '€', spaced: false, position: 'after', short: 'Ct'};
  }
})();
// Übersetzung für Skripte: der deutsche Text ist der Schlüssel, wie bei _() in den
// Templates (siehe app/i18n/__init__.py). Der Katalog (window.ZA_CATALOG) wird nur für
// Nicht-Deutsch inline in die Seite geschrieben; ohne Eintrag bleibt der Text deutsch.
// {name}-Platzhalter werden aus values gefüllt (zweites Argument, z. B. {n: 3}).
window.t = (text, values) => {
  const catalog = window.ZA_CATALOG || {};
  let result = Object.prototype.hasOwnProperty.call(catalog, text) ? catalog[text] : text;
  if (values) result = result.replace(/\{(\w+)\}/g, (match, key) => (key in values ? values[key] : match));
  return result;
};
