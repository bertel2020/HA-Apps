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
    syncGroups();
    if (allowed) submit.disabled = checked === 0 || checked > max;
  }

  // Sammelkästchen einer Gruppe: setzt alle wählbaren Zeilen bis zur nächsten Gruppenzeile.
  function groupBoxes(head) {
    const boxes = [];
    for (let row = head.nextElementSibling; row && !row.classList.contains('sel-group-row'); row = row.nextElementSibling) {
      const box = row.querySelector('input.sel-check');
      if (box && !box.disabled) boxes.push(box);
    }
    return boxes;
  }
  function syncGroups() {
    form.querySelectorAll('tr.sel-group-row').forEach((head) => {
      const all = head.querySelector('input.sel-group-all');
      const boxes = groupBoxes(head);
      if (!all) return;
      all.disabled = boxes.length === 0;
      all.checked = boxes.length > 0 && boxes.every((box) => box.checked);
      all.indeterminate = !all.checked && boxes.some((box) => box.checked);
    });
  }

  form.addEventListener('change', (event) => {
    const target = event.target;
    if (!target.classList) return;
    if (target.classList.contains('sel-group-all')) {
      groupBoxes(target.closest('tr')).forEach((box) => { box.checked = target.checked; });
      update();
    } else if (target.classList.contains('sel-check')) update();
  });
  update();
})();
