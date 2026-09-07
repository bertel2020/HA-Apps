    // Die Liste bleibt beim gewählten sort (kein automatisches Nach-oben-
    // Sortieren von Favoriten, siehe list_entities()) — ein Umschalten ändert
    // also nie die Reihenfolge, kann die Zeile aber aus der aktiven "Nur
    // Favoriten"-Ansicht verschwinden lassen, deshalb hier die ganze Tabelle
    // per htmx neu laden statt nur die Stern-Klasse lokal zu drehen (macht
    // toggleFavorite() in favorite-toggle.js bereits, das reicht für die
    // Optik der geklickten Zeile, aber nicht dafür, sie ggf. auszublenden).
    document.getElementById('entities-table').addEventListener('favorite-changed', () => {
      htmx.trigger('#controls', 'change');
    });
    // "Alle" und die konkreten Typ-Chips schließen sich gegenseitig aus: "Alle"
    // abwählen ergibt ohne Ersatz keinen Sinn (dann wäre nichts mehr gewählt),
    // umgekehrt hebt jede konkrete Auswahl "Alle" auf. Mehrere konkrete Typen
    // lassen sich gleichzeitig anhaken (Mehrfachauswahl) — läuft synchron im
    // "change"-Event der angeklickten Checkbox, bevor es zu #controls hochblubbert
    // und htmx den Reload auslöst, die DOM-Änderungen sind also rechtzeitig gesetzt.
    function onTypeAllChange(checkbox) {
      if (checkbox.checked) {
        document.querySelectorAll('input[name=type]:not([value=all])').forEach(cb => cb.checked = false);
      } else {
        checkbox.checked = true;
      }
    }
    function onTypeSpecificChange(checkbox) {
      document.querySelector('input[name=type][value=all]').checked = false;
      const anyChecked = Array.from(document.querySelectorAll('input[name=type]:not([value=all])')).some(cb => cb.checked);
      if (!anyChecked) document.querySelector('input[name=type][value=all]').checked = true;
    }

    // page-field ist die einzige Quelle der Wahrheit für die Seiten-Navigation —
    // jeder Control, der die zugrunde liegende Trefferliste ändert (Suche, Typ-
    // Filter, Zeilen/Seite), setzt page zurück auf 1.
    function resetEntitiesPage() {
      document.getElementById('page-field').value = '1';
    }
    function setEntitiesPage(v) {
      const field = document.getElementById('page-field');
      field.value = Math.max(1, v);
      field.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function stepEntitiesPage(delta) {
      const current = parseInt(document.getElementById('page-field').value || '1', 10);
      setEntitiesPage(current + delta);
    }
    function goToEntitiesPage(input) {
      const total = parseInt(input.max || '1', 10);
      let v = parseInt(input.value, 10);
      if (!Number.isFinite(v)) v = 1;
      v = Math.min(Math.max(1, v), total);
      input.value = v;
      setEntitiesPage(v);
    }
    // Öffnen/Schließen/Auswählen der .dd-picker-Dropdowns (Spalten, Einheit)
    // kommt aus dem gemeinsamen static/js/dd-picker.js (toggleDDPicker/
    // selectDDOption). Der Typ-Filter ist Mehrfachauswahl über Checkboxen
    // und bleibt deshalb eigene Logik — nutzt von dort nur toggleDDPicker()
    // fürs Öffnen/Schließen des Popovers.

    // Button-Beschriftung spiegelt die aktuelle Typ-Auswahl, damit sie ohne
    // geöffnetes Popover erkennbar bleibt (analog zu "Vergleichen"-Button in
    // chart_editor.html, der ebenfalls den aktiven Modus im Label zeigt).
    const TYPE_LABELS = {standard: 'Standard', counter: 'Zähler', switch: 'Schalter'};
    function updateTypeFilterLabel() {
      const checked = Array.from(document.querySelectorAll('input[name=type]:checked'));
      const btn = document.getElementById('type-filter-btn');
      if (!checked.length || checked.some(cb => cb.value === 'all')) {
        btn.textContent = 'Typ: Alle ▾';
        return;
      }
      btn.textContent = 'Typ: ' + checked.map(cb => TYPE_LABELS[cb.value]).join(', ') + ' ▾';
    }

    // Filter, die zu einer gerade ausgeblendeten Spalte gehören (aktuell nur
    // Typ und Einheit), sofort mitausblenden UND zurücksetzen — ein aktiver,
    // aber unsichtbarer Filter wäre sonst verwirrend ("warum fehlen Zeilen,
    // ich sehe doch gar keinen Filter dafür?"). Die eigentliche Spalte selbst
    // holt sich der reguläre htmx-Reload von #controls (change-Trigger),
    // das hier ist nur der Filter-Teil, den htmx nicht mitbekommt (er liegt
    // in #controls selbst, wird also nicht neu vom Server gerendert).
    function onColumnToggle(key, visible) {
      if (key === 'type') {
        document.getElementById('type-filter-wrap').style.display = visible ? 'inline-block' : 'none';
        if (!visible) {
          document.querySelectorAll('input[name=type]').forEach(cb => cb.checked = cb.value === 'all');
          updateTypeFilterLabel();
        }
      }
      if (key === 'unit') {
        document.getElementById('unit-filter-wrap').style.display = visible ? 'inline-block' : 'none';
        if (!visible) {
          document.getElementById('unit-filter-input').value = 'all';
          document.getElementById('unit-filter-btn').textContent = 'Einheit: Alle ▾';
        }
      }
    }
