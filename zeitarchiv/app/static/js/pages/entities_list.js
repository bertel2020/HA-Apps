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
    const TYPE_LABELS = {standard: t('Standard'), counter: t('Zähler'), switch: t('Schalter'), state: t('Zustand')};
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

    // Auswahlmodus: ohne ihn bleibt die Markierspalte ausgeblendet. Beenden verwirft die Markierungen,
    // damit nichts unsichtbar markiert stehen bleibt und später ungesehen mitgelöscht wird.
    const MODE_KEY = 'zeitarchiv.entities.selecting';
    const panelEl = document.getElementById('entities-panel');
    const toggleBtn = document.getElementById('sel-toggle');
    let selecting = selection.size > 0;
    try { selecting = selecting || sessionStorage.getItem(MODE_KEY) === '1'; } catch (_) { /* nur Komfort */ }

    function applyMode() {
      panelEl.classList.toggle('is-selecting', selecting);
      toggleBtn.setAttribute('aria-pressed', String(selecting));
      toggleBtn.classList.toggle('active', selecting);
      toggleBtn.textContent = selecting ? toggleBtn.dataset.labelOn : toggleBtn.dataset.labelOff;
      try { sessionStorage.setItem(MODE_KEY, selecting ? '1' : '0'); } catch (_) { /* nur Komfort */ }
    }

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
        const text = document.createElement('div');
        text.className = 'sel-item-text';
        const label = document.createElement('span');
        label.className = 'sel-item-name';
        label.textContent = name;
        const idEl = document.createElement('span');
        idEl.className = 'sel-item-id';
        idEl.textContent = id;
        text.append(label, idEl);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '×';
        remove.title = t('Aus der Auswahl entfernen');
        remove.setAttribute('aria-label', t('Aus der Auswahl entfernen'));
        remove.addEventListener('click', () => { selection.delete(id); persistSelection(); syncSelectionUI(); });
        row.append(text, remove);
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

    toggleBtn.addEventListener('click', () => {
      selecting = !selecting;
      if (!selecting) {
        selection.clear();
        persistSelection();
        syncSelectionUI();
      }
      applyMode();
    });
    applyMode();

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

    // Inhalt des Bestätigungsdialogs: Überschrift, die Gewählten als eine Liste (auch die, die gerade
    // nicht in der Suche stehen) und bei jeder Entität darunter, was nach dem Löschen ins Leere zeigt.
    function buildDeleteConfirmTemplate(ids, usages, backupInfo) {
      const tpl = document.createElement('template');
      const el = (tag, text, cls) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (cls) node.className = cls;
        return node;
      };
      const kinds = {chart: t('Diagramm'), table: t('Tabelle'), tile: t('Dashboard-Kachel'), energy: t('Energiedashboard')};
      const blocking = id => (usages[id] || []).filter(u => !u.auto_cleaned);
      const attention = ids.filter(id => blocking(id).length);

      tpl.content.appendChild(el('p', ids.length === 1 ? t('{n} Entität endgültig löschen?', {n: 1}) : t('{n} Entitäten endgültig löschen?', {n: ids.length}), 'del-head'));
      tpl.content.appendChild(el('p', t('Mit allen Werten und ihrer Konfiguration. Das kann nicht rückgängig gemacht werden.'), 'del-sub'));
      if (attention.length) {
        tpl.content.appendChild(el('p', '⚠ ' + t('Noch in Verwendung ({n}). Diese Stellen zeigen danach ins Leere:', {n: attention.length}), 'del-warn'));
      }

      const list = el('ul', undefined, 'del-list');
      // Entitäten mit offenen Verwendungen zuerst, damit sie nicht in einer langen Liste untergehen.
      const ordered = attention.concat(ids.filter(id => !attention.includes(id)));
      ordered.forEach(id => {
        const used = blocking(id);
        const item = el('li', undefined, 'del-item' + (used.length ? ' is-used' : ''));
        item.appendChild(el('span', selection.get(id) || id, 'del-name'));
        item.appendChild(el('span', id, 'del-id'));
        if (used.length) {
          const uses = el('ul', undefined, 'del-uses');
          used.forEach(u => {
            const line = el('li');
            line.appendChild(el('span', kinds[u.kind] || u.kind, 'del-kind'));
            line.appendChild(document.createTextNode(u.name));
            if (u.detail && u.kind !== 'energy') line.appendChild(el('span', ' (' + u.detail + ')', 'del-detail'));
            uses.appendChild(line);
          });
          item.appendChild(uses);
        }
        list.appendChild(item);
      });
      tpl.content.appendChild(list);

      const notes = [];
      if (ids.some(id => (usages[id] || []).some(u => u.auto_cleaned))) notes.push(t('Wert-Kacheln auf Dashboards werden mit entfernt.'));
      notes.push(t('Sendet die Home-Assistant-Integration eine Entität weiter, wird sie automatisch neu angelegt.'));
      tpl.content.appendChild(el('p', notes.join(' '), 'del-foot'));

      // Backup vorab anbieten. Ein Backup sichert das ganze Archiv; einzelne Entitäten lassen sich
      // daraus nicht zurückholen, ein Restore setzt alles auf den Backup-Stand — das sagt der Text.
      const box = el('div', undefined, 'del-backup');
      const lastTs = backupInfo && backupInfo.last_success_ts;
      box.appendChild(el('p', lastTs
        ? t('Letztes Backup: {when}', {when: backupInfo.last_success_text})
        : t('Es gibt noch kein Backup.'), 'del-backup-last'));
      const label = el('label', undefined, 'del-backup-opt');
      const check = el('input');
      check.type = 'checkbox';
      check.id = 'del-backup';
      // Vorausgewählt, wenn es noch kein Backup gibt oder das letzte älter als eine Woche ist.
      check.checked = !lastTs || (Date.now() / 1000 - lastTs) > 7 * 86400;
      label.append(check, el('span', t('Vorher ein Backup erstellen')));
      box.appendChild(label);
      box.appendChild(el('p', t('Das Backup sichert das gesamte Archiv. Eine Wiederherstellung setzt alles auf diesen Stand zurück, auch die Daten der übrigen Entitäten.'), 'del-backup-hint'));
      box.appendChild(el('p', undefined, 'del-backup-status'));
      tpl.content.appendChild(box);
      return tpl;
    }

    // Wartet im offenen Dialog auf das Backup (falls gewünscht); erst danach wird gelöscht. Schlägt es
    // fehl oder bricht man ab, bleibt alles, wie es ist.
    async function runBackupIfWanted(ctl) {
      const wanted = ctl.rich.querySelector('#del-backup');
      if (!wanted || !wanted.checked) return true;
      const status = ctl.rich.querySelector('.del-backup-status');
      const okLabel = ctl.ok.textContent;
      const fail = (text) => { status.textContent = text; status.classList.add('is-error'); ctl.ok.textContent = okLabel; return false; };
      status.classList.remove('is-error');
      status.textContent = '';
      wanted.disabled = true;
      try {
        let jobId;
        try {
          const response = await fetch('entities/backup-start', {method: 'POST'});
          if (!response.ok) throw new Error('backup start failed');
          jobId = (await response.json()).job_id;
        } catch (error) {
          return fail(t('Das Backup konnte nicht gestartet werden. Es wurde nichts gelöscht.'));
        }
        while (!ctl.cancelled) {
          let info;
          try {
            const response = await fetch('entities/backup-status?job_id=' + encodeURIComponent(jobId));
            if (!response.ok) throw new Error('status failed');
            info = await response.json();
          } catch (error) {
            return fail(t('Der Stand des Backups konnte nicht abgefragt werden. Es wurde nichts gelöscht.'));
          }
          const job = info.job;
          if (job && job.final) {
            if (job.status === 'success') return true;
            return fail(t('Das Backup ist fehlgeschlagen. Es wurde nichts gelöscht.') + (job.error ? ' ' + job.error : ''));
          }
          ctl.ok.textContent = t('Backup läuft … {n} %', {n: info.percent});
          status.textContent = t('Gelöscht wird erst, wenn das Backup fertig ist.');
          await new Promise(resolve => setTimeout(resolve, 1500));
        }
        return false;
      } finally {
        wanted.disabled = false;
      }
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
        let backupInfo = null;
        try {
          const [response, backupResponse] = await Promise.all([
            fetch('entities/usage-check', {method: 'POST', body: usageForm}),
            fetch('entities/backup-status').catch(() => null),
          ]);
          if (!response.ok) throw new Error('usage check failed');
          check = await response.json();
          // Ohne Backup-Stand läuft der Dialog trotzdem; die Option zeigt dann „noch kein Backup“.
          backupInfo = backupResponse && backupResponse.ok ? await backupResponse.json() : null;
        } catch (error) {
          appAlert(t('Die Verwendung der Entitäten konnte nicht geprüft werden. Es wurde nichts gelöscht.'));
          return;
        }
        const confirmed = await window.appConfirm('', {
          danger: true,
          confirmLabel: ids.length === 1 ? t('{n} Entität endgültig löschen', {n: 1}) : t('{n} Entitäten endgültig löschen', {n: ids.length}),
          template: buildDeleteConfirmTemplate(ids, check.usages || {}, backupInfo),
          onConfirm: runBackupIfWanted,
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
