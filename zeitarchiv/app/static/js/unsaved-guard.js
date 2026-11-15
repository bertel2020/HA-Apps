// Warnung vor ungespeicherten Änderungen in den Einrichtungen des Wetter- und Energiedashboards.
//
// Die Einrichtung wird per htmx in die Seite geladen und erst mit „Speichern“ übernommen; Dialoge (Entwürfe) und
// Vorschläge ändern nur den Stand im Formular. Ein Formular mit data-unsaved-guard wird deshalb beim Laden
// (nach Alpine) als Stand gemerkt und beim Verlassen mit dem aktuellen verglichen. Der Vergleich nutzt die gesendeten
// Felder, das erfasst auch die versteckten Felder, die Alpine füllt, und keine Entwurfsfelder der Dialoge.
// Warnt wird beim Klick auf „Abbrechen“ und auf Links der App (eigener Dialog) sowie beim Schließen, Neuladen und
// Zurückgehen (Browserdialog, ein eigener ist dort nicht möglich). Nach „Speichern“ ist das Formular aus der Seite, damit auch die Warnung weg.
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

  // Ein Link, der die Seite wirklich verlässt: gleiche Quelle, kein neues Fenster oder Download, nicht nur ein Sprung
  // innerhalb der Seite, kein htmx-Link (der lädt nur einen Teil nach) und kein Klick mit Tastenkombination.
  const leavesPage = (event, link) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.target && link.target !== '_self') return false;
    if (link.hasAttribute('download') || [...link.attributes].some((attr) => attr.name.startsWith('hx-'))) return false;
    let url;
    try { url = new URL(link.href, window.location.href); } catch (error) { return false; }
    if (url.origin !== window.location.origin || !/^https?:$/.test(url.protocol)) return false;
    return !(url.pathname === window.location.pathname && url.search === window.location.search && url.hash);
  };

  // Eigener Dialog statt des Browserdialogs: beim Klick auf „Abbrechen“ und auf jeden Link der App (Menü, Reiter, …).
  // Der Browserdialog bleibt für Schließen, Neuladen, Zurück-Taste und eine getippte Adresse; dort ist kein eigener möglich.
  document.addEventListener('click', async (event) => {
    const link = event.target.closest && event.target.closest('a[href]');
    if (!link || !dirty() || !window.appConfirm) return;
    if (!link.hasAttribute('data-unsaved-cancel') && !leavesPage(event, link)) return;
    event.preventDefault();
    const ok = await window.appConfirm(t('Nicht gespeicherte Änderungen verwerfen?'), {danger: true, confirmLabel: t('Verwerfen')});
    if (!ok) return;
    leaving = true;
    window.location.href = link.href;
  }, true);

  // „Speichern“ zeigt an, ob es etwas zu speichern gibt: .is-dirty am Formular (siehe app.css). Dialoge und Alpine ändern Felder
  // ohne eigene Ereignisse, deshalb prüft zusätzlich ein Timer; der Vergleich ist billig.
  const sync = () => forms.forEach((saved, form) => {
    if (form.isConnected) form.classList.toggle('is-dirty', snapshot(form) !== saved);
  });
  ['input', 'change', 'click', 'keyup'].forEach((name) => document.addEventListener(name, () => setTimeout(sync, 0), true));
  document.addEventListener('htmx:load', () => setTimeout(sync, 200));
  setInterval(() => { if (forms.size) sync(); }, 500);

  window.unsavedGuard = {dirty};
})();
