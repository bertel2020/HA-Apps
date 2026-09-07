    function showImportTab(name) {
      const url = new URL(window.location.href);
      // "reports" lädt serverseitig gefiltert/paginiert — braucht deshalb
      // einen vollen Reload statt des rein clientseitigen Umschaltens wie bei
      // Symcon/CSV/Home Assistant (deren Inhalt schon beim ersten Laden der
      // Seite mitgerendert wird).
      if (name === 'reports' && url.searchParams.get('tab') !== 'reports') {
        url.searchParams.set('tab', 'reports');
        window.location.href = url;
        return;
      }
      document.getElementById('tab-symcon').style.display = name === 'symcon' ? '' : 'none';
      document.getElementById('tab-csv').style.display = name === 'csv' ? '' : 'none';
      document.getElementById('tab-ha').style.display = name === 'ha' ? '' : 'none';
      document.getElementById('tab-reports').style.display = name === 'reports' ? '' : 'none';
      document.getElementById('tab-btn-symcon').classList.toggle('active', name === 'symcon');
      document.getElementById('tab-btn-csv').classList.toggle('active', name === 'csv');
      document.getElementById('tab-btn-ha').classList.toggle('active', name === 'ha');
      document.getElementById('tab-btn-reports').classList.toggle('active', name === 'reports');
      if (name === 'symcon') url.searchParams.delete('tab');
      else url.searchParams.set('tab', name);
      history.replaceState(null, '', url);
    }

    function stepReportsPage(delta) {
      const form = document.getElementById('report-filter');
      const page = document.getElementById('report-page');
      if (!form || !page) return;
      page.value = Math.max(1, REPORTS_PAGE + delta);
      form.submit();
    }

    function jumpReportsPage(v) {
      const form = document.getElementById('report-filter');
      const page = document.getElementById('report-page');
      if (!form || !page) return;
      page.value = v;
      form.submit();
    }

    function goToReportsPage(input) {
      const total = parseInt(input.max || '1', 10);
      let v = parseInt(input.value, 10);
      if (!Number.isFinite(v)) v = 1;
      v = Math.min(Math.max(1, v), total);
      input.value = v;
      jumpReportsPage(v);
    }

    function resetReportsPage() {
      const form = document.getElementById('report-filter');
      const page = document.getElementById('report-page');
      if (!form || !page) return;
      page.value = 1;
      form.submit();
    }

    // Quelle-/Status-Dropdowns nutzen das gemeinsame static/js/dd-picker.js
    // (toggleDDPicker/selectDDOption) — hier nur noch resetReportsPage() als
    // zusätzlicher Effekt bei der Auswahl, siehe onclick in _reports_panel.html.

    async function confirmReportsDelete(event, form) {
      event.preventDefault();
      const confirmed = await appConfirm(
        'Alle Import-Reports endgültig löschen? Diese Aktion lässt sich nicht rückgängig machen.',
        {danger: true, confirmLabel: 'Alle Reports löschen'}
      );
      if (confirmed) form.submit();
    }

    function toggleCustomTsPattern(value) {
      const field = document.getElementById('custom-ts-pattern-field');
      if (field) field.style.display = value === 'custom' ? '' : 'none';
    }

    function selectMappingValue(input) {
      if (input.value) input.select();
    }

    function clearMapping(button) {
      const input = button.closest('.map-picker').querySelector('.map-input');
      input.value = '';
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      input.focus();
      if (typeof input.showPicker === 'function') {
        try { input.showPicker(); } catch (e) {}
      }
    }

    function normalizeUnit(unit) {
      return String(unit || '').trim().toLocaleLowerCase('de-DE').replace(/\s+/g, '');
    }

    const UNIT_SCALES = {
      'lx': ['illuminance', 1], 'klx': ['illuminance', 1000],
      'w': ['power', 1], 'kw': ['power', 1000],
      'wh': ['energy', 1], 'kwh': ['energy', 1000], 'mwh': ['energy', 1000000],
      'v': ['voltage', 1], 'kv': ['voltage', 1000],
      'a': ['current', 1], 'ma': ['current', 0.001],
      'l': ['volume', 1], 'liter': ['volume', 1], 'm³': ['volume', 1000]
    };

    function suggestedFactor(sourceUnit, targetUnit) {
      const source = UNIT_SCALES[normalizeUnit(sourceUnit)];
      const target = UNIT_SCALES[normalizeUnit(targetUnit)];
      if (!source || !target || source[0] !== target[0]) return 1;
      return source[1] / target[1];
    }

    // Tooltip (Friendly Name + Entitäts-ID) über der HA-Zuordnung — dieselbe
    // .entity-tooltip-Optik wie überall sonst in der App, aber erst nach
    // Auswahl per JS befüllt statt fix beim Rendern, weil map-input erst zur
    // Laufzeit einen Wert bekommt. textContent statt innerHTML-Strings, damit
    // Friendly Names mit Sonderzeichen nicht als Markup interpretiert werden;
    // ein leer gelassener Tooltip bleibt dank .entity-tooltip:empty (app.css)
    // unsichtbar, solange keine Entität gewählt ist.
    function updateMapTooltip(input, option) {
      const tooltip = input.closest('.map-picker')?.querySelector('.entity-tooltip');
      if (!tooltip) return;
      tooltip.innerHTML = '';
      if (option && input.value && input.value !== '__ignore__') {
        const strong = document.createElement('strong');
        strong.textContent = option.textContent;
        const code = document.createElement('code');
        code.textContent = input.value;
        tooltip.append(strong, code);
      }
    }

    function updateUnitCheck(input) {
      const row = input.closest('tr[data-variable-id]');
      if (!row) return;
      const panel = row.querySelector('.unit-conversion');
      const factor = row.querySelector('.factor-input');
      const option = Array.from(document.querySelectorAll('#entity-datalist option'))
        .find(candidate => candidate.value === input.value);
      updateMapTooltip(input, option);
      const sourceUnit = row.dataset.symconUnit || '';
      const targetUnit = option ? option.dataset.unit || '' : '';
      const mismatch = sourceUnit && targetUnit && normalizeUnit(sourceUnit) !== normalizeUnit(targetUnit);
      const targetChanged = factor.dataset.target !== input.value;
      if (targetChanged) {
        factor.dataset.target = input.value;
        delete factor.dataset.userEdited;
        factor.value = mismatch ? String(suggestedFactor(sourceUnit, targetUnit)) : '1';
      }
      panel.hidden = !mismatch;
      if (mismatch) {
        panel.querySelector('.unit-warning').textContent =
          `Einheiten stimmen nicht überein: ${sourceUnit} → ${targetUnit}`;
      } else if (!factor.dataset.userEdited) {
        factor.value = '1';
      }
    }

    async function confirmSymconDelete(event, form) {
      event.preventDefault();
      const confirmed = await appConfirm(
        'Hochgeladene Symcon-Quelldaten einschließlich der optionalen settings.json wirklich löschen? Bereits importierte Zeitarchiv-Daten bleiben erhalten.',
        {danger: true, confirmLabel: 'Quelldaten löschen'}
      );
      if (confirmed) form.submit();
    }

    // Beide Uploadfelder sind echte Tastaturziele. Delegiert binden, damit
    // auch die per htmx neu eingesetzte CSV-Dropzone weiter funktioniert.
    document.addEventListener('keydown', (e) => {
      const zone = e.target.closest && e.target.closest('.dropzone');
      if (!zone || (e.key !== 'Enter' && e.key !== ' ')) return;
      const input = zone.querySelector('input[type="file"]');
      if (!input) return;
      e.preventDefault();
      input.click();
    });

    // CSV-Dropzone: delegierte Events auf document statt direkter Bindung ans
    // Element — #csv-section wird nach jedem Upload/Löschen/Vorschau-Wechsel
    // per htmx neu ins DOM geswapped, eine direkte addEventListener-Bindung
    // wäre nach dem ersten Swap tot. Der eigentliche Upload läuft komplett über
    // htmx (hx-post mit hx-encoding="multipart/form-data" auf dem File-Input,
    // siehe _csv_import_section.html) — hier wird nur die gedroppte Datei ins
    // Input geschrieben und ein change-Event ausgelöst, damit htmx die Datei
    // aufnimmt; die reine Klick-Auswahl deckt das umschließende <label> nativ ab.
    ['dragover', 'dragleave', 'drop'].forEach(evt => {
      document.addEventListener(evt, (e) => {
        const zone = e.target.closest && e.target.closest('#csv-dropzone');
        if (!zone) return;
        e.preventDefault();
        if (evt === 'dragover') zone.classList.add('dragging');
        if (evt === 'dragleave') zone.classList.remove('dragging');
        if (evt === 'drop') {
          zone.classList.remove('dragging');
          const input = document.getElementById('csv-file-input');
          if (input && e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
      });
    });

    // Drag & Drop + Klick-Auswahl fürs Upload-Feld — reines <input type="file">
    // in einem <label> deckt "klicken zum Auswählen" schon ab, die Events hier
    // sind nur für den Drop-Fall und die visuelle Rückmeldung nötig. Der
    // eigentliche Upload läuft über XMLHttpRequest statt eines normalen
    // Formular-Submits, weil nur XHR (nicht fetch) Fortschritts-Events beim
    // Hochladen liefert — bei ZIPs mit mehreren hundert MB sonst ein "hängt
    // das jetzt?"-Moment ohne jede Rückmeldung.
    // uploadArea/pollUploadProgress bewusst außerhalb von "if (dropzone)": beim
    // Aufruf von /import auf einen bereits entpackten, aber noch nicht
    // gescannten Ordner (z. B. nach einem Server-Neustart) rendert die Seite
    // direkt die Scan-Fortschrittsanzeige ohne Dropzone — braucht das Polling
    // trotzdem, nur ohne den Upload-Teil davor.
    const uploadArea = document.getElementById('upload-area');

    function appendUploadError(container, message, fullWidth = false) {
      const error = document.createElement('div');
      error.className = 'upload-error';
      if (fullWidth) error.style.width = '100%';
      error.textContent = String(message);
      container.appendChild(error);
    }

    function renderUploadProgress(container, labelText, percent, detailText, uploadMode = false) {
      container.replaceChildren();
      const box = document.createElement('div');
      box.className = 'upload-progress';
      const label = document.createElement('div');
      label.className = 'label';
      const labelSpan = document.createElement('span');
      labelSpan.textContent = labelText;
      const percentLabel = document.createElement('strong');
      percentLabel.textContent = percent === null ? '' : percent + '%';
      if (uploadMode) percentLabel.id = 'upload-percent';
      label.append(labelSpan, percentLabel);
      const bar = document.createElement('div');
      bar.className = 'bar';
      const fill = document.createElement('div');
      fill.className = 'bar-fill';
      if (uploadMode) fill.id = 'upload-bar';
      fill.style.width = (percent === null ? 100 : percent) + '%';
      if (percent === null) fill.style.opacity = '.4';
      bar.appendChild(fill);
      box.append(label, bar);
      if (detailText) {
        const detail = document.createElement('p');
        detail.className = 'filename';
        detail.textContent = detailText;
        box.appendChild(detail);
      }
      container.appendChild(box);
      return { fill, percentLabel };
    }

    // Nach dem reinen Byte-Upload läuft Entpacken + Scannen serverseitig im
    // Hintergrund weiter (bei tausenden Dateien nicht mehr trivial schnell) —
    // hier per fetch()-Polling verfolgt, mit eigenem Schritt-Label je Phase,
    // statt die Fortschrittsanzeige nach dem Upload einfach einfrieren zu lassen.
    const PHASE_LABELS = { extracting: 'Wird entpackt', scanning: 'Scanne Variablen' };
    function pollUploadProgress() {
      fetch('import/upload-progress').then(r => r.json()).then(state => {
        if (state.phase === 'done') {
          location.reload();
          return;
        }
        if (state.phase === 'error') {
          appendUploadError(uploadArea, state.error);
          return;
        }
        const label = PHASE_LABELS[state.phase] || 'Wird verarbeitet';
        const pct = state.total ? Math.round((state.done / state.total) * 100) : null;
        const stepNum = state.phase === 'scanning' ? '2/2' : '1/2';
        renderUploadProgress(
          uploadArea,
          `Schritt ${stepNum} · ${label}…`,
          pct,
          state.total ? `${state.done} von ${state.total}` : ''
        );
        setTimeout(pollUploadProgress, 400);
      });
    }

    const dropzone = document.getElementById('dropzone');
    if (dropzone) {
      const input = document.getElementById('zip-input');

      function uploadZip(file) {
        const progress = renderUploadProgress(
          uploadArea,
          'Wird hochgeladen…',
          0,
          `${file.name} · ${NumberFormat.fmt(file.size / 1024 / 1024, 1)} MB`,
          true
        );
        const bar = progress.fill;
        const percentLabel = progress.percentLabel;

        const formData = new FormData();
        formData.append('file', file);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', 'import/upload');
        xhr.upload.addEventListener('progress', (e) => {
          if (!e.lengthComputable) return;
          const pct = Math.round((e.loaded / e.total) * 100);
          bar.style.width = pct + '%';
          percentLabel.textContent = pct + '%';
        });
        xhr.onload = () => {
          if (xhr.status === 200) {
            pollUploadProgress();
          } else {
            let message = 'ZIP konnte nicht verarbeitet werden.';
            try { message = JSON.parse(xhr.responseText).detail || message; } catch (e) {}
            appendUploadError(uploadArea, message);
          }
        };
        xhr.onerror = () => {
          appendUploadError(uploadArea, 'Upload fehlgeschlagen — Verbindung unterbrochen.');
        };
        xhr.send(formData);
      }

      input.addEventListener('change', () => { if (input.files.length) uploadZip(input.files[0]); });
      ['dragover', 'dragleave', 'drop'].forEach(evt => dropzone.addEventListener(evt, e => e.preventDefault()));
      dropzone.addEventListener('dragover', () => dropzone.classList.add('dragging'));
      dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragging'));
      dropzone.addEventListener('drop', (e) => {
        dropzone.classList.remove('dragging');
        if (e.dataTransfer.files.length) uploadZip(e.dataTransfer.files[0]);
      });
    }

    // settings.json-Zusatz-Upload: anders als der ZIP-Upload synchron ohne
    // eigene Fortschrittsanzeige (reiner JSON-Text, Parsen dauert Millisekunden)
    // — bei Erfolg lädt die Seite neu, damit die Namensspalte + der Wegfall
    // dieses Hinweises serverseitig gerendert erscheinen statt es hier per JS
    // nachzubauen.
    function uploadSettingsJson(file) {
      const hint = document.getElementById('settings-hint');
      const formData = new FormData();
      formData.append('file', file);
      fetch('import/settings-upload', { method: 'POST', body: formData }).then(async (r) => {
        if (r.ok) {
          location.reload();
          return;
        }
        let message = 'settings.json konnte nicht verarbeitet werden.';
        try { message = (await r.json()).detail || message; } catch (e) {}
        appendUploadError(hint, message, true);
      }).catch(() => {
        appendUploadError(hint, 'Upload fehlgeschlagen — Verbindung unterbrochen.', true);
      });
    }

    // Markieren-Checkbox: sortiert die Zeile rein clientseitig an den
    // Tabellenanfang (bzw. zurück in die ID-Reihenfolge beim Entmarkieren) —
    // reine Navigationshilfe bei vielen erkannten Variablen, entscheidet nicht
    // über den Import (das macht weiterhin die Zuordnung in der letzten Spalte).
    function onMarkChange(checkbox) {
      checkbox.closest('tr').classList.toggle('marked', checkbox.checked);
      refreshMarkedRows();
    }

    // Header-Checkbox markiert/entmarkiert alle Zeilen der aktuell sichtbaren
    // Seite (clientseitiges Paging hier, Abschnitt 04/10 — anders als bei
    // Bereinigung bleiben ausgeblendete Zeilen im DOM, deshalb nur die
    // tatsächlich sichtbaren umschalten, nicht den ganzen Datensatz).
    function toggleAllImportRows(headerCheckbox) {
      const table = document.getElementById('import-table');
      if (!table) return;
      Array.from(table.querySelectorAll('tr[data-variable-id]')).forEach(row => {
        if (row.style.display === 'none') return;
        const cb = row.querySelector('.mark-checkbox');
        if (!cb) return;
        cb.checked = headerCheckbox.checked;
        row.classList.toggle('marked', headerCheckbox.checked);
      });
      refreshMarkedRows();
    }

    function refreshMarkedRows() {
      const table = document.getElementById('import-table');
      const rows = Array.from(table.querySelectorAll('tr[data-variable-id]'));
      rows.sort((a, b) => {
        const aMarked = a.classList.contains('marked');
        const bMarked = b.classList.contains('marked');
        if (aMarked !== bMarked) return aMarked ? -1 : 1;
        return Number(a.dataset.variableId) - Number(b.dataset.variableId);
      });
      rows.forEach(r => table.appendChild(r));
      const markedCount = table.querySelectorAll('tr.marked').length;
      const chip = document.getElementById('marked-count');
      chip.classList.toggle('active', markedCount > 0);
      chip.textContent = markedCount + ' markiert';
      applyImportPaging();  // Markieren ändert die Zeilenreihenfolge -> sichtbaren Bereich neu berechnen
    }

    // Client-seitige Paginierung (siehe Kommentar bei der Pager-Markup oben):
    // blendet nur Zeilen aus, entfernt nie etwas aus dem Formular. hideUnreadable
    // und importSearch filtern zusätzlich VOR der Seitenberechnung heraus, damit
    // "50/Seite" sich auf die tatsächlich relevanten Zeilen bezieht statt auf
    // ausgeblendete Zeilen mitzuzählen.
    let importPageSize = 50;
    let importPage = 1;
    let hideUnreadable = true;  // Standard: nicht lesbare Zeilen erstmal ausblenden (Chip startet "aktiv")
    let importSearch = '';
    function toggleHideUnreadable(btn) {
      hideUnreadable = !hideUnreadable;
      btn.classList.toggle('active', hideUnreadable);
      importPage = 1;
      applyImportPaging();
    }
    function onImportSearch(value) {
      importSearch = value.trim().toLowerCase();
      importPage = 1;
      applyImportPaging();
    }
    function applyImportPaging() {
      const table = document.getElementById('import-table');
      if (!table) return;
      const allRows = Array.from(table.querySelectorAll('tr[data-variable-id]'));
      const isFilteredOut = row =>
        (hideUnreadable && row.classList.contains('unreadable')) ||
        (importSearch &&
          !row.dataset.variableId.toLowerCase().includes(importSearch) &&
          !(row.dataset.search || '').includes(importSearch));
      const filteredOut = allRows.filter(isFilteredOut);
      filteredOut.forEach(row => { row.style.display = 'none'; });
      const rows = allRows.filter(row => !isFilteredOut(row));

      const total = rows.length;
      const size = importPageSize;
      const totalPages = Math.max(1, Math.ceil(total / size));
      importPage = Math.max(1, Math.min(importPage, totalPages));
      const start = (importPage - 1) * size;
      const end = Math.min(start + size, total);
      rows.forEach((row, i) => { row.style.display = (i >= start && i < end) ? '' : 'none'; });

      const range = document.getElementById('import-pager-range');
      if (range) range.textContent = total ? `${start + 1}–${end} von ${total}` : '0 von 0';
      const pageLabel = document.getElementById('import-pager-page');
      if (pageLabel) pageLabel.textContent = `Seite ${importPage} / ${totalPages}`;
      const prevBtn = document.getElementById('import-pager-prev');
      if (prevBtn) prevBtn.disabled = importPage <= 1;
      const nextBtn = document.getElementById('import-pager-next');
      if (nextBtn) nextBtn.disabled = importPage >= totalPages;
    }
    function setImportPageSize(value) {
      const parsed = parseInt(value, 10);
      importPageSize = Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 1000) : 50;
      importPage = 1;
      applyImportPaging();
    }
    function stepImportPage(delta) {
      importPage += delta;
      applyImportPaging();
    }
    applyImportPaging();

    if (SCANNING) document.addEventListener('DOMContentLoaded', () => pollUploadProgress());
