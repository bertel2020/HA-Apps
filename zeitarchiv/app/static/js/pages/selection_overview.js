// Auswahl & Verlauf (ROADMAP 1.33). Zwei kleine Dinge, beide verzichtbar —
// ohne Skript bleibt die Seite vollständig bedienbar:
//  1. Nach dem Übernehmen fragt die Seite den Stand des Auftrags ab, solange er
//     auf die Integration wartet, und lädt neu, sobald er sich ändert
//     („wartet auf die Integration“ → „aktiv“). Höchstens 30 Minuten lang.
//  2. Die Knöpfe der Auswahlleiste (Entfernen, Ausschließen, Wieder aufnehmen)
//     sind gesperrt, solange kein Kästchen markiert ist; die Leiste zählt die
//     markierten Zeilen und zeigt ohne Markierung nur Zähler und kurzen Hinweis
//     (data-empty, siehe selection_overview.css).
//  3. Von den Zeilenmenüs (⋮) bleibt höchstens eines offen; ein Klick daneben
//     oder Escape schließt es.
//  4. Entfernen, Ausschließen und Wieder aufnehmen fragen in einem Popup nach,
//     statt auf eine eigene Seite zu wechseln (Inhalt: die Bestätigungsseite der Route).
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

  const menus = document.querySelectorAll('details.ov-menu');
  if (menus.length) {
    const closeOthers = (keep) => menus.forEach((menu) => {
      if (menu !== keep) menu.open = false;
    });
    menus.forEach((menu) => menu.addEventListener('toggle', () => {
      if (menu.open) closeOthers(menu);
    }));
    document.addEventListener('click', (event) => {
      if (!event.target.closest('details.ov-menu')) closeOthers(null);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeOthers(null);
    });
  }
  // ---- Bestätigung im Popup ---------------------------------------------------------------
  const dialog = document.getElementById('sel-confirm');
  const body = document.getElementById('sel-confirm-body');
  const ACTION = /\/selection\/(remove|exclude|include)$/;
  if (form && dialog && body && window.fetch && window.DOMParser && typeof dialog.showModal === 'function') {
    let bypass = false;
    const closeDialog = () => { if (dialog.open) dialog.close(); };

    const show = (html) => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const page = doc.querySelector('.page');
      if (!page) return false;
      const crumb = page.querySelector('.crumb');
      if (crumb) crumb.remove();
      body.replaceChildren(...page.childNodes);
      const title = body.querySelector('h1');
      if (title) { title.id = 'sel-confirm-title'; }
      document.querySelectorAll('details.ov-menu[open]').forEach((menu) => { menu.open = false; });
      if (!dialog.open) dialog.showModal();
      const first = body.querySelector('input[type=checkbox], button[type=submit]');
      if (first) first.focus();
      return true;
    };

    form.addEventListener('submit', (event) => {
      const submitter = event.submitter;
      // formAction ohne Attribut liefert die Seiten-URL, nicht die des Formulars: das Attribut selbst lesen.
      const action = (submitter && submitter.getAttribute('formaction')) || form.action;
      if (bypass || !ACTION.test(new URL(action, location.href).pathname)) return;
      event.preventDefault();
      const data = new FormData(form, submitter);
      const url = new URL(action, location.href);
      url.searchParams.set('popup', '1');
      fetch(url, {method: 'POST', body: data, headers: {Accept: 'text/html'}})
        .then((response) => response.text())
        .then((html) => {
          if (!show(html)) throw new Error('no confirmation page');
        })
        .catch(() => { bypass = true; form.requestSubmit(submitter); });
    });

    // Im Popup: Abbrechen/Zurück schließt; ohne Haken bei „von Hand gewählt“ bleibt es offen und weist darauf hin.
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) { closeDialog(); return; }
      const link = event.target.closest('a.btn');
      if (link) { event.preventDefault(); closeDialog(); }
    });
    dialog.addEventListener('submit', (event) => {
      const confirmForm = event.target;
      const manual = confirmForm.querySelector('input[name=confirm_manual]');
      if (manual && !manual.checked) {
        event.preventDefault();
        const alert = confirmForm.querySelector('[data-manual-missing]');
        if (alert) alert.hidden = false;
        manual.focus();
      }
    });
    dialog.addEventListener('close', () => body.replaceChildren());
  }
})();
