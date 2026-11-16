// Lücken-Erkennung (Housekeeping): Auswahlleiste über den Kontrollkästchen der Tabelle. Der Abschnitt wird per htmx
// getauscht, deshalb Ereignisse am Dokument statt an den Elementen; die Leiste zählt nur, die Aktionen stehen in der
// Vorlage (hx-post).
(function () {
  function root() { return document.getElementById('gap-conflicts'); }
  function boxes() { return Array.from(root().querySelectorAll('[data-gc-check]')); }
  function visible(box) { return box.closest('tr').offsetParent !== null; }

  function update() {
    const r = root();
    const bar = r && r.querySelector('[data-gc-bar]');
    if (!bar) return;
    const chosen = boxes().filter(function (b) { return b.checked; });
    const n = chosen.length;
    const count = bar.querySelector('[data-gc-count]');
    const sel = bar.querySelector('[data-gc-sel]');
    bar.classList.toggle('has-selection', n > 0);
    count.textContent = n ? count.dataset.nSel.replace('{count}', n) : count.dataset.nAll;
    sel.hidden = !n;
    sel.textContent = n ? sel.dataset.label.replace('{count}', n) : sel.textContent;
    bar.querySelector('[data-gc-clear]').hidden = !n;
    bar.querySelector('[data-gc-all]').hidden = n > 0;
    const recSel = bar.querySelector('[data-gc-rec-sel]');
    recSel.hidden = !n;
    if (n) recSel.textContent = recSel.dataset.label.replace('{count}', n);
    const recAll = bar.querySelector('[data-gc-rec-all]');
    if (recAll) recAll.hidden = n > 0;
    const head = r.querySelector('[data-gc-check-all]');
    const shown = boxes().filter(visible);
    if (head) head.checked = shown.length > 0 && shown.every(function (b) { return b.checked; });
  }

  document.addEventListener('change', function (e) {
    const r = root();
    if (!r || !r.contains(e.target)) return;
    if (e.target.matches('[data-gc-check-all]')) {
      boxes().filter(visible).forEach(function (b) { b.checked = e.target.checked; });
    }
    update();
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-gc-clear]')) {
      boxes().forEach(function (b) { b.checked = false; });
      update();
    }
  });
  document.body.addEventListener('htmx:afterSwap', function (e) { if (e.target.id === 'gap-conflicts') update(); });
  document.addEventListener('DOMContentLoaded', update);

  // Bestätigung als <dialog>: nach jedem Tausch des Abschnitts öffnen. Schließen ohne Entscheidung (Escape, Klick auf den
  // Hintergrund) verhält sich wie „Abbrechen“ und lädt den Abschnitt neu.
  function openModal() {
    const r = root();
    const dlg = r && r.querySelector('dialog[data-gc-modal]');
    if (!dlg || dlg.open) return;
    dlg.showModal();
    dlg.addEventListener('close', function () {
      if (!dlg.isConnected) return;
      const cancel = dlg.querySelector('[data-gc-cancel]');
      if (cancel) cancel.click();
    }, {once: true});
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
    const focus = dlg.querySelector('.btn.primary') || dlg.querySelector('[data-gc-cancel]');
    if (focus) focus.focus();
  }
  document.addEventListener('htmx:afterSettle', openModal);
})();
