    function onExportTypeAllChange(checkbox) {
      if (checkbox.checked) {
        document.querySelectorAll('input[name=type]:not([value=all])').forEach(cb => cb.checked = false);
      } else {
        checkbox.checked = true;
      }
    }
    function onExportTypeSpecificChange(checkbox) {
      document.querySelector('input[name=type][value=all]').checked = false;
      const anyChecked = Array.from(document.querySelectorAll('input[name=type]:not([value=all])')).some(cb => cb.checked);
      if (!anyChecked) document.querySelector('input[name=type][value=all]').checked = true;
    }

    // Öffnen/Schließen/Auswählen der .dd-picker-Dropdowns (Typ, Einheit)
    // kommt aus dem gemeinsamen static/js/dd-picker.js.
    const EXPORT_TYPE_LABELS = {standard: 'Standard', counter: 'Zähler', switch: 'Schalter'};
    function updateExportTypeFilterLabel() {
      const checked = Array.from(document.querySelectorAll('input[name=type]:checked'));
      const btn = document.getElementById('export-type-filter-btn');
      if (!checked.length || checked.some(cb => cb.value === 'all')) {
        btn.textContent = 'Typ: Alle ▾';
        return;
      }
      btn.textContent = 'Typ: ' + checked.map(cb => EXPORT_TYPE_LABELS[cb.value]).join(', ') + ' ▾';
    }

    // page-field ist die einzige Quelle der Wahrheit für die Seiten-Navigation —
    // jeder Control, der die zugrunde liegende Trefferliste ändert (Suche, Typ-/
    // Einheit-Filter, Zeilen/Seite), setzt page zurück auf 1.
    function resetExportPage() {
      document.getElementById('export-page-field').value = '1';
    }
    function setExportPage(v) {
      const field = document.getElementById('export-page-field');
      field.value = Math.max(1, v);
      field.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function stepExportPage(delta) {
      const current = parseInt(document.getElementById('export-page-field').value || '1', 10);
      setExportPage(current + delta);
    }
    function goToExportPage(input) {
      const total = parseInt(input.max || '1', 10);
      let v = parseInt(input.value, 10);
      if (!Number.isFinite(v)) v = 1;
      v = Math.min(Math.max(1, v), total);
      input.value = v;
      setExportPage(v);
    }
