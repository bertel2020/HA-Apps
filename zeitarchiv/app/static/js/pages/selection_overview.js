// Auswahl & Verlauf (ROADMAP 1.33). Zwei kleine Dinge, beide verzichtbar —
// ohne Skript bleibt die Seite vollständig bedienbar:
//  1. Nach dem Übernehmen fragt die Seite den Stand des Auftrags ab, solange er
//     auf die Integration wartet, und lädt neu, sobald er sich ändert
//     („wartet auf die Integration“ → „aktiv“). Höchstens 30 Minuten lang.
//  2. Die Knöpfe der Massenleiste (Entfernen, Ausschließen, Wieder aufnehmen)
//     sind gesperrt, solange kein Kästchen markiert ist.
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
    const update = () => {
      const none = !form.querySelector('input.ov-check:checked');
      buttons.forEach((button) => { button.disabled = none; });
    };
    form.addEventListener('change', update);
    update();
  }
})();
