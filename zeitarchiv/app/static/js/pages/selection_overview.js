// Auswahl & Verlauf (ROADMAP 1.33). Zwei kleine Dinge, beide verzichtbar —
// ohne Skript bleibt die Seite vollständig bedienbar:
//  1. Nach dem Übernehmen fragt die Seite den Stand des Auftrags ab, solange er
//     auf die Integration wartet, und lädt neu, sobald er sich ändert
//     („wartet auf die Integration“ → „aktiv“). Höchstens 30 Minuten lang.
//  2. Die Knöpfe der Auswahlleiste (Entfernen, Ausschließen, Wieder aufnehmen)
//     sind gesperrt, solange kein Kästchen markiert ist; die Leiste zählt die
//     markierten Zeilen und zeigt ohne Markierung nur Zähler und kurzen Hinweis
//     (data-empty, siehe selection_overview.css).
(function () {
  'use strict';
  const banner = document.getElementById('ov-job');
  if (banner && banner.dataset.status === 'pending' && window.fetch) {
    const url = banner.dataset.statusUrl;
    const started = Date.now();
    const poll = () => {
      if (Date.now() - started > 30 * 60 * 1000) return;
      fetch(url, {headers: {Accept: 'application/json'}})
        .then((response) => (response.ok ? response.json() : null))
        .then((body) => {
          if (body && body.status && body.status !== 'pending') window.location.reload();
          else setTimeout(poll, 5000);
        })
        .catch(() => setTimeout(poll, 15000));
    };
    setTimeout(poll, 5000);
  }

  const form = document.getElementById('ov-bulk');
  const submit = document.getElementById('ov-bulk-submit');
  if (form && submit && !submit.disabled) {
    const buttons = form.querySelectorAll('.ov-bulk-btn');
    const bar = document.getElementById('ov-bulkbar');
    const count = document.getElementById('ov-bulk-n');
    const update = () => {
      const marked = form.querySelectorAll('input.ov-check:checked').length;
      buttons.forEach((button) => { button.disabled = !marked; });
      if (count) count.textContent = String(marked);
      if (bar) bar.toggleAttribute('data-empty', !marked);
    };
    if (bar) bar.setAttribute('data-live', '');
    form.addEventListener('change', update);
    update();
  }
})();
