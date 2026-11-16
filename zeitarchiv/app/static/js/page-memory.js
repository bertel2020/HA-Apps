// Merkt sich je Browser-Tab, von welcher Seite man auf die jetzige gekommen ist (window.zaPrevPage), für
// dynamic-back-link.js. Unter HA-Ingress fehlt document.referrer oft; ohne dieses Gedächtnis gäbe es dort
// keinen Rücklink. Nur sessionStorage, kein Server-State.
//
// - Gleicher Pfad wie zuletzt (Neuladen, Filter per Formular): die Herkunft bleibt, nur die URL wird nachgeführt.
// - Zurück/Vor im Browser: Herkunft unbekannt, nichts erfinden (sonst zeigte der Link auf die Seite, die man verlässt).
// - Sonst wird die zuletzt gemerkte Seite zur Herkunft.
(() => {
  const KEY = 'za-pages';
  let state = {cur: null, prev: null};
  try {
    const stored = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    if (stored && typeof stored === 'object') state = {cur: stored.cur || null, prev: stored.prev || null};
  } catch (e) { /* ohne Speicher: kein Merken */ }

  const here = {path: location.pathname, href: location.href};
  let nav = null;
  try { nav = performance.getEntriesByType('navigation')[0]; } catch (e) { /* ältere Browser */ }

  if (state.cur && state.cur.path === here.path) {
    state.cur = here;
  } else if (nav && nav.type === 'back_forward') {
    state = {cur: here, prev: null};
  } else {
    state = {cur: here, prev: state.cur};
  }

  window.zaPrevPage = state.prev ? state.prev.href : null;
  try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* s. o. */ }
})();
