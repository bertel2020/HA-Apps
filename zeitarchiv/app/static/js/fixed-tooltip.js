// Abweichungs-Tooltip für Vergleichstabellen (data-tooltip-fixed, siehe
// .dtile-tooltip-fixed in app.css) — eigene Datei statt Teil von
// dashboard-tiles.js oder table-compute.js, weil BEIDE Kontexte, die ihn
// brauchen, unterschiedliche Skripte laden: Dashboard-Kacheln
// (entities.html/dashboard_detail.html, dashboard-tiles.js) UND die
// eigenständige Tabellen-Bearbeitung (table_editor.html, kein
// dashboard-tiles.js). table-compute.js bleibt bewusst reiner
// Berechnungscode ohne DOM-Abhängigkeit (siehe Kommentar dort).
//
// Bewusst NICHT das generische CSS-Tooltip-System ([data-tooltip]::after,
// siehe app.css), weil dessen absolute Positionierung relativ zur
// auslösenden Zelle von überlaufenden/scrollenden Vorschau-Containern
// (.dtile-table-preview/.dtile-body in der Kachel, .tbl-preview im Editor)
// abgeschnitten würde. position:fixed mit selbst berechneten Koordinaten
// umgeht das, weil das erzeugte Element direkt an document.body hängt,
// außerhalb jeder Beschneidung. Selbst-initialisierend (wire() läuft am
// Ende dieser Datei), damit jede einbindende Seite nichts weiter tun muss.
window.FixedTooltip = (() => {
  let tipEl = null;
  let timer = null;
  let target = null;
  let wired = false;

  function hide() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (tipEl) { tipEl.remove(); tipEl = null; }
    target = null;
  }

  function show(el) {
    const text = el.getAttribute('data-tooltip-fixed');
    if (!text) return;
    target = el;
    tipEl = document.createElement('div');
    tipEl.className = 'dtile-tooltip-fixed';
    tipEl.textContent = text;
    document.body.appendChild(tipEl);
    const rect = el.getBoundingClientRect();
    const tipRect = tipEl.getBoundingClientRect();
    let left = Math.min(rect.left, window.innerWidth - tipRect.width - 8);
    left = Math.max(8, left);
    let top = rect.top - tipRect.height - 6;
    if (top < 8) top = rect.bottom + 6;
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
  }

  // Ein einziger delegierter Listener an document.body (statt je Zelle) —
  // funktioniert auch für Zellen, die erst nach dem Laden dieser Datei
  // gerendert werden (z. B. Kachel-Refresh, Tabellen-Neuberechnung).
  function wire() {
    if (wired) return;
    wired = true;
    document.body.addEventListener('mouseover', (e) => {
      const el = e.target.closest('[data-tooltip-fixed]');
      if (!el || el === target) return;
      hide();
      timer = setTimeout(() => show(el), 600);
    });
    document.body.addEventListener('mouseout', (e) => {
      if (e.target.closest('[data-tooltip-fixed]')) hide();
    });
    document.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }

  return {wire};
})();
