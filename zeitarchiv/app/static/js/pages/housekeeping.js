  // table VOR dem .remove() der Zeile merken — danach hat die Zeile keinen
  // Elternknoten mehr, closest('table') liefe dann ins Leere und der Pager
  // ("1–10 von 13") bliebe nach dem Löschen auf dem alten Stand stehen.
  // hkRemoveRow wird ausschließlich von hkDeleteChart/hkDeleteTable
  // aufgerufen (nur die "Ungenutzte Elemente"-Tabelle hat data-hk-row-Zeilen)
  // — die "X von Y"-Kennzahl direkt darüber lässt sich deshalb hier immer
  // gefahrlos mitaktualisieren, ohne den Abschnitt gegenprüfen zu müssen.
  function hkRemoveRow(btn) {
    const row = btn.closest('[data-hk-row]');
    const table = row.closest('table');
    row.remove();
    if (window.refreshSortableTable) window.refreshSortableTable(table);
    const unusedEl = document.getElementById('hk-unused-count');
    const totalEl = document.getElementById('hk-total-count');
    if (unusedEl) unusedEl.textContent = String(Math.max(0, parseInt(unusedEl.textContent, 10) - 1));
    if (totalEl) totalEl.textContent = String(Math.max(0, parseInt(totalEl.textContent, 10) - 1));
  }

  async function hkDeleteChart(id, btn) {
    if (!await appConfirm('Dieses Chart wirklich löschen?', {danger: true})) return;
    btn.disabled = true;
    fetch(`${BASE}/charts/${id}/delete`, {method: 'POST'}).then(r => {
      if (r.ok) {
        hkRemoveRow(btn);
      } else {
        btn.disabled = false;
        appAlert('Löschen fehlgeschlagen.');
      }
    }).catch(() => { btn.disabled = false; appAlert('Löschen fehlgeschlagen.'); });
  }

  async function hkDeleteTable(id, btn) {
    if (!await appConfirm('Diese Tabelle wirklich löschen?', {danger: true})) return;
    btn.disabled = true;
    fetch(`${BASE}/tables/${id}/delete`, {method: 'POST'}).then(r => {
      if (r.ok) {
        hkRemoveRow(btn);
      } else {
        btn.disabled = false;
        appAlert('Löschen fehlgeschlagen.');
      }
    }).catch(() => { btn.disabled = false; appAlert('Löschen fehlgeschlagen.'); });
  }
