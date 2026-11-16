// Lücken-Erkennung und Ausreißer-Erkennung (Housekeeping, je ein Abschnitt mit data-gc-root): Auswahlleiste über den Kontrollkästchen der Tabelle. Der Abschnitt wird per htmx
// getauscht, deshalb Ereignisse am Dokument statt an den Elementen; die Leiste zählt nur, die Aktionen stehen in der
// Vorlage (hx-post).
(function () {
  function roots() { return Array.from(document.querySelectorAll('[data-gc-root]')); }
  function boxes(r) { return Array.from(r.querySelectorAll('[data-gc-check]')); }
  function visible(box) { return box.closest('tr').offsetParent !== null; }

  function update(r) {
    const bar = r && r.querySelector('[data-gc-bar]');
    if (!bar) return;
    const chosen = boxes(r).filter(function (b) { return b.checked; });
    const n = chosen.length;
    const count = bar.querySelector('[data-gc-count]');
    const sel = bar.querySelector('[data-gc-sel]');
    const all = bar.querySelector('[data-gc-all]');
    bar.classList.toggle('has-selection', n > 0);
    count.textContent = n ? count.dataset.nSel.replace('{count}', n) : count.dataset.nAll;
    if (sel) {
      sel.hidden = !n;
      sel.textContent = n ? sel.dataset.label.replace('{count}', n) : sel.textContent;
    }
    bar.querySelector('[data-gc-clear]').hidden = !n;
    if (all) all.hidden = n > 0;
    const recSel = bar.querySelector('[data-gc-rec-sel]');
    recSel.hidden = !n;
    if (n) recSel.textContent = recSel.dataset.label.replace('{count}', n);
    const recAll = bar.querySelector('[data-gc-rec-all]');
    if (recAll) recAll.hidden = n > 0;
    const head = r.querySelector('[data-gc-check-all]');
    const shown = boxes(r).filter(visible);
    if (head) head.checked = shown.length > 0 && shown.every(function (b) { return b.checked; });
  }

  document.addEventListener('change', function (e) {
    const r = e.target.closest('[data-gc-root]');
    if (!r) return;
    if (e.target.matches('[data-gc-check-all]')) {
      boxes(r).filter(visible).forEach(function (b) { b.checked = e.target.checked; });
    }
    update(r);
  });
  document.addEventListener('click', function (e) {
    const r = e.target.closest('[data-gc-root]');
    if (r && e.target.closest('[data-gc-clear]')) {
      boxes(r).forEach(function (b) { b.checked = false; });
      update(r);
    }
  });
  document.body.addEventListener('htmx:afterSwap', function (e) {
    const r = e.target.closest ? e.target.closest('[data-gc-root]') : null;
    if (r) update(r);
  });
  document.addEventListener('DOMContentLoaded', function () { roots().forEach(update); });

  // Bestätigung als <dialog>: nach jedem Tausch des Abschnitts öffnen. Schließen ohne Entscheidung (Escape, Klick auf den
  // Hintergrund) verhält sich wie „Abbrechen“ und lädt den Abschnitt neu.
  function openModal() {
    roots().forEach(openModalIn);
  }
  function openModalIn(r) {
    const dlg = r.querySelector('dialog[data-gc-modal]');
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
