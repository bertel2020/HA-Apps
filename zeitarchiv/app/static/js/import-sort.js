/* Sortierung der Import-Tabellen (Symcon, VictoriaMetrics, InfluxDB).

   Die Zeilen bleiben dauerhaft im Formular (Paging blendet nur aus), sortiert
   wird deshalb die DOM-Reihenfolge. Geladen VOR den Tabellenabschnitten, weil
   deren Skripte schon beim Einlesen der Seite einmal sortieren und paginieren. */
(function () {
  'use strict';

  // Vergleicht zwei Werte aus data-sort-*: Zahlen als Zahlen, sonst Text (deutsche
  // Reihenfolge). Leere Werte stehen immer am Ende, unabhängig von der Richtung.
  function compareValues(av, bv, dir) {
    if (av === '' && bv === '') return 0;
    if (av === '') return 1;
    if (bv === '') return -1;
    const an = Number(av);
    const bn = Number(bv);
    const cmp = (av.trim() !== '' && bv.trim() !== '' && !Number.isNaN(an) && !Number.isNaN(bn))
      ? an - bn : av.localeCompare(bv, document.documentElement.lang || 'de');
    return dir === 'desc' ? -cmp : cmp;
  }

  // Liest den Sortierwert einer Zeile. ``getValue`` darf für Spalten ohne festes
  // data-Attribut (z. B. die gewählte HA-Entität eines Eingabefelds) einen eigenen Wert liefern.
  function rowValue(row, key, getValue) {
    if (getValue) {
      const own = getValue(row, key);
      if (own !== undefined) return String(own).toLowerCase();
    }
    return row.dataset['sort' + key.charAt(0).toUpperCase() + key.slice(1)] ?? '';
  }

  // Ordnet die Zeilen: markierte zuerst (wenn ``markedFirst``), innerhalb der Gruppen nach
  // der gewählten Spalte. Ohne Spalte bleibt die bisherige Reihenfolge je Gruppe (stabil).
  function sortRows(rows, key, dir, options) {
    const opts = options || {};
    return rows.slice().sort((a, b) => {
      if (opts.markedFirst) {
        const am = a.classList.contains('marked');
        const bm = b.classList.contains('marked');
        if (am !== bm) return am ? -1 : 1;
      }
      if (!key) return 0;
      return compareValues(rowValue(a, key, opts.getValue), rowValue(b, key, opts.getValue), dir);
    });
  }

  // Pfeile an den Kopfzeilen-Links (data-sort-key) der Tabelle.
  function updateArrows(table, key, dir) {
    if (!table) return;
    table.querySelectorAll('th a[data-sort-key]').forEach((link) => {
      const active = link.dataset.sortKey === key;
      link.classList.toggle('active', active);
      const arrow = link.querySelector('.sort-arrow');
      if (arrow) arrow.textContent = active ? (dir === 'asc' ? ' ↑' : ' ↓') : '';
    });
  }

  window.importSort = { sortRows, updateArrows };
})();
