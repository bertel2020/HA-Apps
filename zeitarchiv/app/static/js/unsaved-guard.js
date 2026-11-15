// Warnung vor ungespeicherten Änderungen in den Einrichtungen des Wetter- und Energiedashboards.
//
// Die Einrichtung wird per htmx in die Seite geladen und erst mit „Speichern“ übernommen; Dialoge (Entwürfe) und
// Vorschläge ändern nur den Stand im Formular. Ein Formular mit data-unsaved-guard wird deshalb beim Laden
// (nach Alpine) als Stand gemerkt und beim Verlassen mit dem aktuellen verglichen. Der Vergleich nutzt die gesendeten
// Felder, das erfasst auch die versteckten Felder, die Alpine füllt, und keine Entwurfsfelder der Dialoge.
// Warnt wird beim Schließen, Neuladen und Seitenwechsel (Browserdialog) und beim Klick auf „Abbrechen“
// (a[data-unsaved-cancel], eigener Dialog). Nach „Speichern“ ist das Formular aus der Seite, damit auch die Warnung weg.
(function () {
  const forms = new Map();
  const snapshot = (form) => JSON.stringify([...new FormData(form).entries()].filter(([, value]) => typeof value === 'string'));
  const dirty = () => [...forms].some(([form, saved]) => form.isConnected && snapshot(form) !== saved);

  const remember = (root) => {
    // htmx meldet jedes eingesetzte Element für sich: das Formular selbst kann die Wurzel sein.
    const found = root.matches && root.matches('form[data-unsaved-guard]') ? [root] : [];
    [...found, ...root.querySelectorAll('form[data-unsaved-guard]')].forEach((form) => {
      // Kurz warten: Alpine befüllt die versteckten Felder erst nach dem Einsetzen der Seite. Ein Timer statt
      // requestAnimationFrame, denn der pausiert in einem Hintergrund-Tab und der Stand fehlte dann.
      const take = () => setTimeout(() => forms.set(form, snapshot(form)), 100);
      if (window.Alpine && Alpine.nextTick) Alpine.nextTick(take); else take();
    });
  };
  document.addEventListener('htmx:load', (event) => remember(event.detail.elt));
  document.addEventListener('DOMContentLoaded', () => remember(document));

  let leaving = false;
  window.addEventListener('beforeunload', (event) => {
    if (leaving || !dirty()) return;
    event.preventDefault();
    event.returnValue = '';
  });

  document.addEventListener('click', async (event) => {
    const link = event.target.closest && event.target.closest('a[data-unsaved-cancel]');
    if (!link || !dirty() || !window.appConfirm) return;
    event.preventDefault();
    const ok = await window.appConfirm(t('Nicht gespeicherte Änderungen verwerfen?'), {danger: true, confirmLabel: t('Verwerfen')});
    if (!ok) return;
    leaving = true;
    window.location.href = link.href;
  });

  window.unsavedGuard = {dirty};
})();
