// Auswahl-Assistent (ROADMAP 1.33): zählt die markierten Kästchen in der
// Fußleiste mit und sperrt „Übernehmen …“, solange nichts oder zu viel markiert
// ist. Alles Übrige (Bestätigung, Prüfung, Auftrag) macht der Server; ohne
// dieses Skript funktioniert die Seite genauso, nur ohne Live-Zähler.
(function () {
  'use strict';
  const form = document.getElementById('sel-form');
  const counter = document.getElementById('sel-checked');
  const submit = document.getElementById('sel-submit');
  if (!form || !counter || !submit) return;
  const max = parseInt(form.dataset.max, 10) || 200;
  const allowed = !submit.disabled;

  function update() {
    const checked = form.querySelectorAll('input.sel-check:checked').length;
    counter.textContent = String(checked);
    if (allowed) submit.disabled = checked === 0 || checked > max;
  }

  form.addEventListener('change', (event) => {
    if (event.target.classList && event.target.classList.contains('sel-check')) update();
  });
  update();
})();
