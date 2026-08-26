// Zentrale Zahlenformatierung für die gesamte Oberfläche (Konzept: einheitliches
// Zahlenformat statt der bisherigen, gewachsenen Mischung aus Punkt-Dezimal
// (entity_detail.html, chart_editor.html) und deutschem Komma-Dezimal
// (dashboard-tiles.js, table-compute.js, statistik.html) — jede Datei rundete
// zwar nach derselben "4 signifikante Stellen, überflüssige Nullen weg"-Regel,
// gab das Ergebnis aber unterschiedlich aus.
//
// LOCALE ist bewusst die einzige Stelle, die eine Sprache kennt: eine künftige
// Sprachumschaltung (z. B. Englisch, "en-US" mit Punkt-Dezimal) ändert nur
// diese eine Konstante — toLocaleString() liefert dafür automatisch das
// richtige Dezimal-/Tausendertrennzeichen, ohne dass die einzelnen
// Chart-/Tabellen-Dateien selbst etwas über das Format wissen müssen.
window.NumberFormat = (() => {
  const LOCALE = 'de-DE';

  // Wie fmtValue()/fmtNum() bisher pro Datei: mit explizitem decimals immer
  // exakt auf diese Stellenzahl (auch mit anhängenden Nullen), sonst bis zu
  // 4 signifikante Stellen mit abgeschnittenen überflüssigen Nullen.
  function fmt(value, decimals) {
    if (value == null || Number.isNaN(value)) return '';
    if (decimals != null) {
      return value.toLocaleString(LOCALE, {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
    }
    if (value === 0) return '0';
    return Number(value.toPrecision(4)).toLocaleString(LOCALE);
  }

  // Gegenstück zu fmt() für Freitext-Zahleneingaben im Oberflächenformat (z. B.
  // Formel-Konstanten in Vergleichstabellen) — Dezimal-/Tausendertrennzeichen
  // werden aus LOCALE abgeleitet statt hart auf "," / "." zu setzen, damit eine
  // künftige Sprachumschaltung automatisch mitzieht.
  const DECIMAL_SEP = (1.1).toLocaleString(LOCALE).replace(/\d/g, '');
  const THOUSANDS_SEP = (1000).toLocaleString(LOCALE).replace(/\d/g, '');

  function parse(text) {
    if (typeof text !== 'string') return text;
    let normalized = text.trim();
    if (THOUSANDS_SEP) normalized = normalized.split(THOUSANDS_SEP).join('');
    if (DECIMAL_SEP && DECIMAL_SEP !== '.') normalized = normalized.split(DECIMAL_SEP).join('.');
    return parseFloat(normalized);
  }

  return {LOCALE, DECIMAL_SEP, THOUSANDS_SEP, fmt, parse};
})();
