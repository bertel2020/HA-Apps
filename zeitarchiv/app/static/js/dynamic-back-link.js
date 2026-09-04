// Zweiter, dynamischer "Zurück"-Link neben dem statischen (siehe <p class="sub">
// in den jeweiligen Templates) — zeigt zusätzlich, WOHER der Nutzer tatsächlich
// kam, falls das eine andere Seite als die feste Hauptansicht war (z. B. ein
// Sankey-Knoten-Klick im Energiedashboard landet auf einer Entität, deren
// statischer Link aber immer "Entitäten" zeigt). Rein referrer-basiert, kein
// Server-State — fehlt der Referrer, ist er fremd, zeigt er auf dieselbe
// Seite oder passt er zu keiner bekannten Route, wird einfach nichts
// angezeigt (kein Fehlerfall).
(() => {
  // Spezifischere Muster (Detailseite mit ID) bewusst vor der allgemeinen
  // Abschnitts-Route (z. B. /dashboards/12 vor /dashboards) — find() nimmt
  // den ersten Treffer. Ergänzen für weitere Herkunftsseiten reicht als
  // neuer Eintrag.
  const ROUTES = [
    {re: /^\/energiedashboard(\/|$)/, text: 'zum Energiedashboard'},
    {re: /^\/dashboards\/\d+/, text: 'zum Dashboard'},
    {re: /^\/dashboards(\/|$)/, text: 'zu den Dashboards'},
    {re: /^\/entities\/[^/]+/, text: 'zur Entität'},
    {re: /^\/entities(\/|$)/, text: 'zu den Entitäten'},
    {re: /^\/charts\/\d+/, text: 'zum Chart'},
    {re: /^\/charts(\/|$)/, text: 'zu den Charts'},
    {re: /^\/tables(\/|$)/, text: 'zu den Tabellen'},
    {re: /^\/statistik(\/|$)/, text: 'zur Statistik'},
    {re: /^\/housekeeping(\/|$)/, text: 'zum Housekeeping'},
    {re: /^\/import(\/|$)/, text: 'zum Import'},
  ];

  // Dieselbe app_root-Abzieh-Logik wie _rel in _topnav.html — macht den
  // Vergleich unabhängig vom HA-Ingress-Präfix (das ist pro Installation
  // unterschiedlich und in document.referrer/location.pathname enthalten).
  function stripRoot(pathname, root) {
    if (root && pathname.startsWith(root)) return pathname.slice(root.length) || '/';
    return pathname;
  }

  function init() {
    const container = document.querySelector('[data-dynamic-back]');
    if (!container || !document.referrer) return;
    let ref;
    try { ref = new URL(document.referrer); } catch (e) { return; }
    if (ref.origin !== location.origin) return;

    const root = container.dataset.appRoot || '';
    const refRel = stripRoot(ref.pathname, root);
    const hereRel = stripRoot(location.pathname, root);
    if (refRel === hereRel) return;

    // Nicht doppeln, wenn der statische Link daneben schon exakt dorthin zeigt.
    const alreadyLinked = Array.from(container.querySelectorAll('a')).some((a) => {
      try { return new URL(a.href, location.href).pathname === ref.pathname; } catch (e) { return false; }
    });
    if (alreadyLinked) return;

    const match = ROUTES.find((r) => r.re.test(refRel));
    if (!match) return;

    container.appendChild(document.createTextNode(' · '));
    const a = document.createElement('a');
    // document.referrer trägt inzwischen selbst den zuletzt gewählten
    // Zeitraum/Offset, sofern die Herkunftsseite ihn in die URL spiegelt
    // (siehe syncUrlWithPeriod() in energiedashboard.js) — ein normaler
    // Link-Klick landet dadurch wieder genau dort, ohne auf Browser-
    // Verlauf/bfcache angewiesen zu sein (funktioniert auch in einem neuen
    // Tab oder nach einem harten Reload).
    a.href = document.referrer;
    a.textContent = '← zurück ' + match.text;
    container.appendChild(a);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
