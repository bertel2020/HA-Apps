// Spaltenzahl der Kachelreihe des Wetterdashboards: so, dass keine einzelnen Kacheln allein in einer
// letzten Reihe stehen (8 Kacheln: 4+4 statt 6+2). Reine Funktion, damit sie unter node getestet werden kann.
(function (root) {
  const MIN_TILE = 150;
  const GAP = 10;

  // count: Anzahl Kacheln, width: verfügbare Breite in Pixeln. Gibt die Zahl der Spalten zurück.
  function tileColumns(count, width) {
    if (count <= 1) return 1;
    const fit = Math.max(2, Math.floor((width + GAP) / (MIN_TILE + GAP)));
    const most = Math.min(count, fit);
    let best = most;
    let bestFill = -1;
    // Gefüllteste letzte Reihe gewinnt (ein Teiler der Anzahl füllt sie ganz); bei Gleichstand mehr Spalten.
    for (let columns = most; columns >= 2; columns -= 1) {
      const fill = count % columns === 0 ? columns : count % columns;
      if (fill > bestFill) {
        best = columns;
        bestFill = fill;
      }
    }
    return best;
  }

  const api = {tileColumns};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WetterLayout = api;
})(typeof window !== 'undefined' ? window : globalThis);
