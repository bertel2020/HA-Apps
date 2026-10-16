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
    const TYPE_LABELS = {standard: t('Standard'), counter: t('Zähler'), switch: t('Schalter')};
    function updateTypeFilterLabel() {
      const checked = Array.from(document.querySelectorAll('input[name=type]:checked'));
      const btn = document.getElementById('type-filter-btn');
      if (!checked.length || checked.some(cb => cb.value === 'all')) {
        btn.textContent = t('Typ: Alle ▾');
        return;
      }
      btn.textContent = t('Typ: ') + checked.map(cb => TYPE_LABELS[cb.value]).join(', ') + ' ▾';
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
          document.getElementById('unit-filter-btn').textContent = t('Einheit: Alle ▾');
        }
      }
    }

    // ---- Auswahl für „Löschen …“ ------------------------------------------------------------
    // Die Auswahl (entity_id → Anzeigename) liegt hier statt in der Tabelle, weil die bei jeder
    // Suche, jedem Filter und jedem Seitenwechsel neu geladen wird. So lässt sich über mehrere
    // Suchen hinweg gezielt sammeln. sessionStorage hält sie über einen Seiten-Reload, ist aber nur
    // Komfort — ohne (privates Fenster, gesperrt) läuft alles im Speicher weiter.
    const SELECTION_KEY = 'zeitarchiv.entities.selection';
    const selection = new Map();
    try {
      const stored = JSON.parse(sessionStorage.getItem(SELECTION_KEY) || '[]');
      if (Array.isArray(stored)) stored.forEach(([id, name]) => selection.set(String(id), String(name)));
    } catch (_) { /* leere Auswahl */ }

    function persistSelection() {
      try { sessionStorage.setItem(SELECTION_KEY, JSON.stringify([...selection])); } catch (_) { /* nur Komfort */ }
    }

    function selectionPanelOpen() {
      return !document.getElementById('sel-panel').hidden;
    }

    function renderSelectionPanel() {
      const panel = document.getElementById('sel-panel');
      panel.textContent = '';
      selection.forEach((name, id) => {
        const row = document.createElement('div');
        row.className = 'sel-item';
        const label = document.createElement('span');
        label.className = 'sel-item-name';
        label.textContent = name;
        const idEl = document.createElement('span');
        idEl.className = 'sel-item-id';
        idEl.textContent = id;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '×';
        remove.title = t('Aus der Auswahl entfernen');
        remove.setAttribute('aria-label', t('Aus der Auswahl entfernen'));
        remove.addEventListener('click', () => { selection.delete(id); persistSelection(); syncSelectionUI(); });
        row.append(label, idEl, remove);
        panel.appendChild(row);
      });
    }

    // Gleicht Haken, Kopf-Checkbox, Leiste und Liste mit dem gemerkten Bestand ab — nach jedem
    // Laden der Tabelle und nach jeder Änderung der Auswahl.
    function syncSelectionUI() {
      const rows = Array.from(document.querySelectorAll('#entities-table .sel-row'));
      rows.forEach(cb => { cb.checked = selection.has(cb.value); });
      const all = document.querySelector('#entities-table .sel-all');
      if (all) {
        const checked = rows.filter(cb => cb.checked).length;
        all.checked = rows.length > 0 && checked === rows.length;
        all.indeterminate = checked > 0 && checked < rows.length;
      }
      const bar = document.getElementById('sel-bar');
      bar.hidden = selection.size === 0;
      document.getElementById('sel-count').textContent = t('{n} gewählt', {n: selection.size});
      if (selection.size === 0) {
        document.getElementById('sel-panel').hidden = true;
        document.getElementById('sel-show').setAttribute('aria-expanded', 'false');
      } else if (selectionPanelOpen()) {
        renderSelectionPanel();
      }
    }

    const entitiesTableEl = document.getElementById('entities-table');
    entitiesTableEl.addEventListener('change', (event) => {
      const target = event.target;
      if (target.classList.contains('sel-row')) {
        if (target.checked) selection.set(target.value, target.dataset.name || target.value);
        else selection.delete(target.value);
      } else if (target.classList.contains('sel-all')) {
        // Nur die gerade sichtbaren Zeilen — Treffer anderer Suchen bleiben unberührt.
        document.querySelectorAll('#entities-table .sel-row').forEach(cb => {
          if (target.checked) selection.set(cb.value, cb.dataset.name || cb.value);
          else selection.delete(cb.value);
        });
      } else {
        return;
      }
      persistSelection();
      syncSelectionUI();
    });
    entitiesTableEl.addEventListener('htmx:afterSwap', syncSelectionUI);

    document.getElementById('sel-clear').addEventListener('click', () => {
      selection.clear();
      persistSelection();
      syncSelectionUI();
    });
    document.getElementById('sel-show').addEventListener('click', () => {
      const panel = document.getElementById('sel-panel');
      panel.hidden = !panel.hidden;
      document.getElementById('sel-show').setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) renderSelectionPanel();
    });

    function usageLine(usage) {
      const kinds = {chart: t('Diagramm'), table: t('Tabelle'), tile: t('Dashboard-Kachel'), energy: t('Energiedashboard')};
      let text = (kinds[usage.kind] || usage.kind) + ': ' + usage.name;
      if (usage.detail && usage.kind !== 'energy') text += ' (' + usage.detail + ')';
      return text;
    }

    // Inhalt des Bestätigungsdialogs: Anzahl, Verwendungen, die nach dem Löschen ins Leere zeigen,
    // und die vollständige Liste der Gewählten (auch die, die gerade nicht in der Suche stehen).
    function buildDeleteConfirmTemplate(ids, usages, summary) {
      const tpl = document.createElement('template');
      const el = (tag, text, cls) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (cls) node.className = cls;
        return node;
      };
      tpl.content.appendChild(el('p', t('{n} Entitäten werden mit allen Werten und ihrer Konfiguration endgültig gelöscht. Das kann nicht rückgängig gemacht werden. Sendet die Home-Assistant-Integration eine Entität weiter, wird sie automatisch neu angelegt.', {n: ids.length}), 'cbc-title'));

      const attention = ids.filter(id => (usages[id] || []).some(u => !u.auto_cleaned));
      if (attention.length) {
        const warn = el('div', undefined, 'sel-confirm-warn');
        warn.appendChild(el('strong', t('Noch in Verwendung ({n}). Diese Stellen zeigen danach ins Leere:', {n: attention.length})));
        const list = el('ul');
        attention.forEach(id => {
          const lines = (usages[id] || []).filter(u => !u.auto_cleaned).map(usageLine);
          list.appendChild(el('li', (selection.get(id) || id) + ' — ' + lines.join('; ')));
        });
        warn.appendChild(list);
        tpl.content.appendChild(warn);
      }
      if (ids.some(id => (usages[id] || []).some(u => u.auto_cleaned))) {
        tpl.content.appendChild(el('p', t('Wert-Kacheln auf Dashboards werden mit entfernt.')));
      }

      const names = el('div', undefined, 'sel-confirm-list');
      ids.forEach(id => names.appendChild(el('div', (selection.get(id) || id) + '  ·  ' + id)));
      tpl.content.appendChild(names);
      return tpl;
    }

    async function deleteSelection() {
      const ids = Array.from(selection.keys());
      if (!ids.length) return;
      const button = document.getElementById('sel-delete');
      button.disabled = true;
      try {
        const usageForm = new FormData();
        ids.forEach(id => usageForm.append('entity_id', id));
        let check;
        try {
          const response = await fetch('entities/usage-check', {method: 'POST', body: usageForm});
          if (!response.ok) throw new Error('usage check failed');
          check = await response.json();
        } catch (error) {
          appAlert(t('Die Verwendung der Entitäten konnte nicht geprüft werden. Es wurde nichts gelöscht.'));
          return;
        }
        const confirmed = await window.appConfirm('', {
          danger: true,
          confirmLabel: t('{n} Entitäten endgültig löschen', {n: ids.length}),
          template: buildDeleteConfirmTemplate(ids, check.usages || {}, check.summary || {}),
        });
        if (!confirmed) return;

        const deleteForm = new FormData();
        ids.forEach(id => deleteForm.append('entity_id', id));
        let result;
        try {
          const response = await fetch('entities/delete-selected', {method: 'POST', body: deleteForm});
          if (!response.ok) throw new Error('delete failed');
          result = await response.json();
        } catch (error) {
          appAlert(t('Das Löschen ist fehlgeschlagen.'));
          return;
        }
        // Gelöschte und inzwischen nicht mehr vorhandene Entitäten verlassen die Auswahl,
        // fehlgeschlagene bleiben gewählt.
        (result.deleted || []).concat(result.missing || []).forEach(id => selection.delete(id));
        persistSelection();
        htmx.trigger('#controls', 'change');
        syncSelectionUI();
        if ((result.failed || []).length) {
          appAlert(t('{ok} gelöscht, {failed} fehlgeschlagen: {names}', {
            ok: (result.deleted || []).length,
            failed: result.failed.length,
            names: result.failed.map(f => f.entity_id).join(', '),
          }), {danger: true});
        }
      } finally {
        button.disabled = false;
      }
    }
    document.getElementById('sel-delete').addEventListener('click', deleteSelection);
    syncSelectionUI();
