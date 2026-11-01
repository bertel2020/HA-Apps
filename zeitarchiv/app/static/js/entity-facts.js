// Füllt die Min/Max-Zelle der Kennzahlen-Leiste (_entity_facts.html) nach: der
// Server rechnet sie erst auf Anforderung, ein Vollscan gehört nicht in den
// Seitenaufbau.
(function () {
  var cell = document.querySelector('[data-value-range-url]');
  if (!cell) return;
  var target = cell.querySelector('.entity-fact-text');
  fetch(cell.getAttribute('data-value-range-url'), { headers: { Accept: 'application/json' } })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (data) { target.textContent = data.text; })
    .catch(function () { target.textContent = '—'; });
})();
