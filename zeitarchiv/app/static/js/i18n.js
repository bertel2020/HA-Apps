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
