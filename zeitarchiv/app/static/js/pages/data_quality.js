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
