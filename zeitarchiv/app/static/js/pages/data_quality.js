// Datenqualität: Fehleranzeige mit „Erneut versuchen“ für die aufgeklappte Zeile.
// Die Zeile lädt ihre Einzelbefunde einmal per htmx (hx-trigger "click once"); scheitert der Abruf, bliebe sonst
// der Ladehinweis ewig stehen. Der Text kommt als <template> aus der Seite (übersetzt), nicht aus diesem Skript.
(function () {
  const FAILURE_EVENTS = ['htmx:responseError', 'htmx:sendError', 'htmx:timeout'];
  FAILURE_EVENTS.forEach(function (name) {
    document.body.addEventListener(name, function (evt) {
      const row = evt.target.closest && evt.target.closest('tr.dq-row');
      const template = document.getElementById('dq-error-template');
      if (!row || !template) return;
      const body = document.querySelector(row.getAttribute('hx-target'));
      if (!body) return;
      body.replaceChildren(template.content.cloneNode(true));
      body.querySelector('button').addEventListener('click', function () {
        body.querySelector('.dq-error').remove();
        body.innerHTML = template.dataset.loading;
        htmx.trigger(row, 'dq-retry');
      });
    });
  });
})();

// Neu messen: Der Knopf (Statuszeile, Auswahlleiste, Zeile) fordert die Messung nur an; sie läuft im Wartungstakt.
// Solange die Statuszeile „Messung läuft“ zeigt, holt die Seite sie alle paar Sekunden neu und ersetzt nur diese Zeile
// (die Tabelle bleibt unberührt, geöffnete Zeilen und die Auswahl gehen nicht verloren). Ist die Messung fertig und hat
// jemand sie angefordert, lädt die Seite einmal neu, damit die Zahlen stimmen.
(function () {
  const POLL_MS = 4000;
  let timer = null;
  let requested = false;

  function status() { return document.getElementById('dq-status'); }

  function poll() {
    timer = null;
    fetch(window.location.href, { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
      .then(function (html) {
        const fresh = new DOMParser().parseFromString(html, 'text/html').getElementById('dq-status');
        const current = status();
        if (!fresh || !current) return;
        const running = fresh.classList.contains('is-running');
        if (!running && requested) { window.location.reload(); return; }
        current.replaceWith(fresh);
        if (window.htmx) htmx.process(fresh);
        if (running) schedule(); else requested = false;
      })
      .catch(function () { schedule(); });
  }

  function schedule() { if (!timer) timer = setTimeout(poll, POLL_MS); }

  document.body.addEventListener('htmx:afterRequest', function (evt) {
    const el = evt.target;
    if (!el.hasAttribute || !el.hasAttribute('data-dq-remeasure')) return;
    el.classList.remove('is-busy');
    let n = 0;
    try { n = JSON.parse(evt.detail.xhr.responseText).requested || 0; } catch (e) { n = 0; }
    if (!evt.detail.successful) return;
    if (n === 0) {
      const label = el.querySelector('.dq-remeasure-label');
      if (label && el.dataset.none) {
        const old = label.textContent;
        label.textContent = el.dataset.none;
        setTimeout(function () { label.textContent = old; }, 2500);
      }
      return;
    }
    requested = true;
    el.disabled = true;
    schedule();
    setTimeout(poll, 600);
  });
  document.body.addEventListener('htmx:beforeRequest', function (evt) {
    const el = evt.target;
    if (el.hasAttribute && el.hasAttribute('data-dq-remeasure')) el.classList.add('is-busy');
  });

  const first = status();
  if (first && first.classList.contains('is-running')) schedule();
})();
