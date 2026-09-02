// Leichtgewichtige Sortierung + Seitenweise-Anzeige für einmalig serverseitig
// gerenderte <table class="dt">-Listen (kein Server-Roundtrip nötig, anders
// als z. B. die Entitäten-/Export-Tabelle mit eigenem sort=/dir=-Query).
// Opt-in per Attribut auf dem <table>-Element:
//   data-sortable              — Klick auf <th> sortiert nach dieser Spalte
//   data-paginate              — Zeilen seitenweise anzeigen (Pager nur bei
//                                 mehr Zeilen als Seitengröße)
//   data-page-size="N"         — Seitengröße, Standard 10
// Auf einem <th> zusätzlich:
//   data-no-sort               — diese Spalte vom Sortieren ausnehmen
// Auf einem <td> optional:
//   data-sort="rohwert"        — Sortierwert statt des angezeigten (formatierten)
//                                 Textes, z. B. bei "622.491 / 622.491".
(function () {
  function cellSortValue(td) {
    const raw = (td.dataset.sort !== undefined ? td.dataset.sort : td.textContent).trim();
    const num = parseFloat(raw.replace(/\./g, '').replace(',', '.').replace(/[^0-9.-]/g, ''));
    return {raw, num: Number.isNaN(num) ? null : num};
  }

  function sortRows(table, colIndex, dir) {
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.children).filter((r) => r.tagName === 'TR');
    rows.sort((a, b) => {
      const av = cellSortValue(a.children[colIndex]);
      const bv = cellSortValue(b.children[colIndex]);
      let cmp;
      if (av.num !== null && bv.num !== null) cmp = av.num - bv.num;
      else cmp = av.raw.localeCompare(bv.raw, 'de');
      return dir === 'asc' ? cmp : -cmp;
    });
    rows.forEach((r) => tbody.appendChild(r));
  }

  function dataRows(table) {
    return Array.from(table.tBodies[0].children).filter((r) => r.tagName === 'TR');
  }

  function updatePager(table) {
    const pageSize = parseInt(table.dataset.pageSize || '10', 10);
    const rows = dataRows(table);
    const scrollWrap = table.closest('.tbl-wrap') || table;
    const nextEl = scrollWrap.nextElementSibling;
    let pager = nextEl && nextEl.classList.contains('sortable-table-pager') ? nextEl : null;

    if (rows.length <= pageSize) {
      if (pager) pager.remove();
      rows.forEach((r) => { r.style.display = ''; });
      return;
    }

    let page = parseInt(table.dataset.page || '1', 10);
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    page = Math.min(Math.max(1, page), totalPages);
    table.dataset.page = String(page);
    rows.forEach((r, i) => {
      const rowPage = Math.floor(i / pageSize) + 1;
      r.style.display = rowPage === page ? '' : 'none';
    });

    if (!pager) {
      pager = document.createElement('div');
      pager.className = 'pager sortable-table-pager';
      scrollWrap.insertAdjacentElement('afterend', pager);
    }
    const start = (page - 1) * pageSize + 1;
    const end = Math.min(rows.length, page * pageSize);
    pager.innerHTML =
      '<span class="pager-range">' + start + '–' + end + ' von ' + rows.length + '</span>' +
      '<button class="btn navbtn" type="button" data-page-action="first"' + (page <= 1 ? ' disabled' : '') + ' title="Erste Seite">«</button>' +
      '<button class="btn navbtn" type="button" data-page-action="prev"' + (page <= 1 ? ' disabled' : '') + '>‹</button>' +
      '<span class="pager-page">Seite ' + page + ' / ' + totalPages + '</span>' +
      '<button class="btn navbtn" type="button" data-page-action="next"' + (page >= totalPages ? ' disabled' : '') + '>›</button>' +
      '<button class="btn navbtn" type="button" data-page-action="last"' + (page >= totalPages ? ' disabled' : '') + ' title="Letzte Seite">»</button>';
    pager.querySelectorAll('[data-page-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.pageAction;
        if (action === 'first') table.dataset.page = '1';
        else if (action === 'prev') table.dataset.page = String(page - 1);
        else if (action === 'next') table.dataset.page = String(page + 1);
        else if (action === 'last') table.dataset.page = String(totalPages);
        updatePager(table);
      });
    });
  }

  function initTable(table) {
    if (table.dataset.sortableInit === 'true') return;
    table.dataset.sortableInit = 'true';

    if (table.hasAttribute('data-sortable')) {
      const headRow = table.tHead ? table.tHead.rows[0] : table.rows[0];
      Array.from(headRow.cells).forEach((th, idx) => {
        if (th.hasAttribute('data-no-sort')) return;
        th.classList.add('dt-sortable-col');
        th.addEventListener('click', () => {
          const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
          Array.from(headRow.cells).forEach((h) => { delete h.dataset.dir; h.classList.remove('dt-sort-asc', 'dt-sort-desc'); });
          th.dataset.dir = dir;
          th.classList.add(dir === 'asc' ? 'dt-sort-asc' : 'dt-sort-desc');
          sortRows(table, idx, dir);
          if (table.hasAttribute('data-paginate')) { table.dataset.page = '1'; }
          updatePager(table);
        });
      });
    }

    if (table.hasAttribute('data-paginate')) updatePager(table);
  }

  function initAll(root) {
    (root || document).querySelectorAll('table.dt[data-sortable], table.dt[data-paginate]').forEach(initTable);
  }

  document.addEventListener('DOMContentLoaded', () => initAll());
  document.body.addEventListener('htmx:afterSwap', (e) => initAll(e.target));

  // Für Aufrufer, die eine Zeile außerhalb von htmx (z. B. per fetch() +
  // .remove(), siehe hkDeleteChart/hkDeleteTable in housekeeping.html) aus
  // einer paginierten Tabelle entfernen — ohne diesen Aufruf bliebe "1–10 von
  // 13" im Pager stehen, obwohl nur noch 12 Zeilen übrig sind.
  window.refreshSortableTable = function (table) {
    if (table) updatePager(table);
  };
})();
