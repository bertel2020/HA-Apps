    // Reiter-Umschalter (Konzept-Erweiterung "Bearbeitungsbereich") — "tab"
    // steuert nur die Sichtbarkeit (x-show), "mode-field" ist zusätzlich die
    // tatsächlich an den Server geschickte Zeilen-Aktion für "Bereinigen"
    // ("cleanup") vs. "Korrigieren" ("correct"); "Hinzufügen" braucht kein
    // mode, weil es #controls/#rows-table gar nicht anspricht (eigenständiges
    // Formular, siehe submitAddValue()).
    function setTab(tab) {
      const pageData = Alpine.$data(document.querySelector('.page'));
      pageData.tab = tab;
      const modeField = document.getElementById('mode-field');
      const newMode = tab === 'correct' ? 'correct' : 'cleanup';
      if (modeField.value !== newMode) {
        modeField.value = newMode;
        modeField.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }

    async function submitAddValue() {
      const statusEl = document.getElementById('add-value-status');
      const dtStr = document.getElementById('add-datetime').value;
      const valueStr = document.getElementById('add-value').value;
      statusEl.className = 'add-value-status';
      statusEl.textContent = '';
      if (!dtStr || valueStr === '') {
        statusEl.className = 'add-value-status err';
        statusEl.textContent = 'Bitte Datum/Uhrzeit und Wert angeben.';
        return;
      }
      const ts = new Date(dtStr).getTime() / 1000;
      const value = NumberFormat.parse(valueStr);
      if (Number.isNaN(value)) {
        statusEl.className = 'add-value-status err';
        statusEl.textContent = 'Ungültiger Wert.';
        return;
      }
      try {
        const res = await fetch(`${BASE}/entities/${ENTITY_ID}/rows/add`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ts, value}),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          statusEl.className = 'add-value-status err';
          statusEl.textContent = err.detail || 'Hinzufügen fehlgeschlagen.';
          return;
        }
        statusEl.className = 'add-value-status ok';
        statusEl.textContent = '✓ Wert hinzugefügt.';
        document.getElementById('add-value').value = '';
      } catch (e) {
        statusEl.className = 'add-value-status err';
        statusEl.textContent = 'Hinzufügen fehlgeschlagen.';
      }
    }

    // Korrigieren: die Wert-Zelle einer Zeile wird per Klick auf den
    // Stift-Button zu einem Inline-Formular (Zahl-Feld + ✓/✗) — dieselbe
    // Zelle, kein separater Dialog, damit Zeitstempel/aktueller Wert der
    // Zeile beim Bearbeiten sichtbar bleiben. Bei Erfolg wird die ganze
    // Tabelle über #controls neu geladen (htmx.trigger), nicht nur die eine
    // Zeile lokal aktualisiert — einfacher, und Korrekturen sind kein
    // Vorgang, bei dem es auf jede Millisekunde ankäme.
    function startCorrect(btn) {
      // btn (Stift) sitzt in der ersten Spalte, .correct-value-cell (Wert)
      // in der dritten — closest() findet nur Vorfahren, deshalb hier über
      // die Zeile zur Geschwister-Zelle statt über eine (nicht vorhandene)
      // gemeinsame Elternschaft.
      const cell = btn.closest('tr').querySelector('.correct-value-cell');
      const oldValue = cell.dataset.oldValue;
      cell.dataset.editing = 'true';
      cell.replaceChildren();
      const wrapper = document.createElement('span');
      wrapper.className = 'correct-inline';
      const input = document.createElement('input');
      input.type = 'number';
      input.step = 'any';
      input.value = oldValue;
      const confirm = document.createElement('button');
      confirm.type = 'button';
      confirm.className = 'correct-confirm';
      confirm.title = 'Speichern';
      confirm.textContent = '✓';
      confirm.addEventListener('click', () => confirmCorrect(confirm));
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'correct-cancel';
      cancel.title = 'Abbrechen';
      cancel.textContent = '✗';
      cancel.addEventListener('click', () => htmx.trigger('#controls', 'change'));
      wrapper.append(input, confirm, cancel);
      cell.append(wrapper);
      input.focus();
    }

    async function confirmCorrect(btn) {
      const cell = btn.closest('.correct-value-cell');
      const input = cell.querySelector('input');
      const newValue = parseFloat(input.value);
      if (Number.isNaN(newValue)) { input.focus(); return; }
      btn.disabled = true;
      try {
        const res = await fetch(`${BASE}/entities/${ENTITY_ID}/rows/correct`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ts: parseFloat(cell.dataset.ts), old_value: parseFloat(cell.dataset.oldValue), new_value: newValue}),
        });
        if (!res.ok) { appAlert('Korrigieren fehlgeschlagen.'); btn.disabled = false; return; }
        htmx.trigger('#controls', 'change');
      } catch (e) {
        appAlert('Korrigieren fehlgeschlagen.');
        btn.disabled = false;
      }
    }

    // range-field/offset-field (in #controls) sind die einzige Quelle der
    // Wahrheit für die Zeitraum-Navigation — setRange()/setOffset() schreiben
    // hinein und lösen "change" aus, wodurch #controls' bestehender
    // hx-trigger="change" den Reload übernimmt. Kein weiterer Zustand nötig:
    // beide Felder leben außerhalb des per htmx ausgetauschten Fragments und
    // bleiben darum über Swaps hinweg erhalten.
    //
    // Der Zeitraum-TYP (range) lebt zusätzlich noch reaktiv im .page-x-data
    // (siehe cleanup.html-Markup oben) — nicht als Ersatz für das Formularfeld
    // (das bleibt die Quelle der Wahrheit für den htmx-Request), sondern weil
    // _calendar_popover.html seine Sichtbarkeit (Monat-Auswahl/Pfeile/
    // Tage-Raster) per Alpine-Scope-Kette daraus liest (siehe dortiger
    // Kommentar) und calendarPicker()s pickerMode-Getter ebenfalls darauf
    // zugreift.
    function setRange(key) {
      const field = document.getElementById('range-field');
      if (key === field.value) return;
      resetPage();
      const pageData = Alpine.$data(document.querySelector('.page'));
      // Dieselbe stabile Zoom-Logik wie im übergeordneten Verlauf: Als Anker
      // dient das tatsächlich angezeigte Fenster. Der Anker bleibt auch über
      // grobe Zwischenstufen und "Gesamt" erhalten, damit z. B.
      // Tag→Monat→Stunde weiterhin zum ursprünglich gewählten Datum führt.
      const anchorMs = pageData.rangeAnchorMs ?? PeriodNavigation.anchorForWindow(
        pageData.windowStart, pageData.windowEnd, pageData.isCurrent
      );
      pageData.rangeAnchorMs = anchorMs;
      const nextOffset = key === 'all' ? 0 : PeriodNavigation.offsetForRange(key, anchorMs);
      document.getElementById('offset-field').value = String(nextOffset);
      document.querySelectorAll('#range-seg button').forEach(b => b.classList.toggle('active', b.dataset.range === key));
      pageData.range = key;
      field.value = key;
      field.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function setOffset(v, preserveAnchor = false) {
      resetPage();  // ein anderes Zeitfenster hat eine andere Zeilenmenge
      if (!preserveAnchor) {
        const pageData = Alpine.$data(document.querySelector('.page'));
        pageData.rangeAnchorMs = null;
      }
      const field = document.getElementById('offset-field');
      field.value = Math.min(v, 0);
      field.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function stepOffset(delta) {
      const current = parseInt(document.getElementById('offset-field').value || '0', 10);
      setOffset(current + delta);
    }
    // page-field analog zu offset-field: die Pager-Buttons im rows-table-Fragment
    // schreiben nur hierhin. Jeder andere Control, der die zugrunde liegende
    // Zeilenmenge ändert (Zeitraum, Filter, Zeilen/Seite), setzt page auf 1 zurück
    // — sonst könnte z. B. "Duplikate" mit nur 2 Treffern auf einer Seite 5 landen,
    // die es für diesen Filter gar nicht gibt.
    function resetPage() {
      document.getElementById('page-field').value = '1';
    }
    function setPage(v) {
      document.getElementById('page-field').value = Math.max(1, v);
      document.getElementById('page-field').dispatchEvent(new Event('change', {bubbles: true}));
    }
    function stepPage(delta) {
      const current = parseInt(document.getElementById('page-field').value || '1', 10);
      setPage(current + delta);
    }
    function goToPage(input) {
      const total = parseInt(input.max || '1', 10);
      let v = parseInt(input.value, 10);
      if (!Number.isFinite(v)) v = 1;
      v = Math.min(Math.max(1, v), total);
      input.value = v;
      setPage(v);
    }
    // Vom Kalender-Widget (static/js/calendar-picker.js) beim Klick auf einen
    // Tag mit Daten aufgerufen — rechnet den gewählten Tag in "wie viele
    // Perioden des aktuell gewählten Zeitraum-Typs liegt er hinter der
    // jeweils laufenden Periode" um, dieselbe Offset-Definition wie
    // query._window() in query.py (0 = aktuell, -1 = eine Periode zurück, …),
    // pro Zeitraum-Typ an dessen eigener Kalendergrenze ausgerichtet
    // (Kalenderwoche Mo–So bei "week", Kalendermonat bei "month", …) —
    // inhaltlich identisch zu jumpToDate() in entity_detail.html. 12 Uhr ist
    // für Stunde/Tag ein stabiler Anker mitten im gewählten Datum und vermeidet
    // Randfälle an Sommerzeitwechseln.
    function jumpToDate(dateStr) {
      if (!dateStr) return;
      const rangeKey = document.getElementById('range-field').value;
      const [y, m, d] = dateStr.split('-').map(Number);
      const picked = new Date(y, m - 1, d, 12);
      const pageData = Alpine.$data(document.querySelector('.page'));
      pageData.rangeAnchorMs = picked.getTime();
      const offset = rangeKey === 'all' ? 0 : PeriodNavigation.offsetForRange(rangeKey, pageData.rangeAnchorMs);
      setOffset(offset, true);
    }
    function updateSelectedCount() {
      const count = document.querySelectorAll('[name=ts]:checked').length;
      const el = document.getElementById('selected-count');
      if (el) el.textContent = count;
      document.querySelectorAll('.needs-selection').forEach(btn => btn.disabled = count === 0);
    }
    // Header-Checkbox markiert/entmarkiert alle Zeilen der aktuell angezeigten
    // Seite (serverseitiges Paging hier, Abschnitt 04/10 — im DOM stehen ohnehin
    // nur die Zeilen der aktuellen Seite, "alle" und "alle dieser Seite" sind
    // hier also dasselbe).
    function toggleAllRows(headerCheckbox) {
      document.querySelectorAll('[name=ts]').forEach(cb => { cb.checked = headerCheckbox.checked; });
      updateSelectedCount();
    }
    document.body.addEventListener('change', e => { if (e.target.name === 'ts') updateSelectedCount(); });
    document.body.addEventListener('htmx:afterSwap', updateSelectedCount);

    // Die Kennzahl-Spalten (je Kennzahl, NICHT das Tag-Feld — das hat eine
    // feste CSS-Breite, siehe .entity-stats-tag) sollen zwischen "Gesamter
    // Zeitraum" und der ausgewählten Periode exakt fluchten, obwohl beide
    // Zeilen eigene, unabhängig breite Boxen sind (kein <table>, das Spalten
    // automatisch über Zeilen hinweg ausrichten würde). Deshalb hier per JS:
    // Inline-Breiten zurücksetzen, dann je Spaltenindex die breitere der
    // beiden Zellen messen und beiden dieselbe Breite geben.
    function alignEntityStatsColumns() {
      const container = document.getElementById('rows-summary');
      if (!container) return;
      const rows = Array.from(container.children).filter(el => el.classList.contains('entity-stats'));
      if (rows.length < 2) return;
      // erstes Kind je Zeile ist .entity-stats-tag (feste Breite) — ab Index 1.
      const cellRows = rows.map(row => Array.from(row.children).slice(1));
      const colCount = Math.min(...cellRows.map(cells => cells.length));
      cellRows.forEach(cells => cells.forEach(cell => { cell.style.width = ''; }));
      for (let i = 0; i < colCount; i++) {
        const widest = Math.max(...cellRows.map(cells => cells[i].getBoundingClientRect().width));
        cellRows.forEach(cells => { cells[i].style.width = Math.ceil(widest) + 'px'; });
      }
    }
    document.body.addEventListener('htmx:afterSwap', alignEntityStatsColumns);

    // Status-Badge-Tooltips: siehe Kommentar bei .js-tooltip oben — ein an
    // <body> gehängtes fixed-Element statt des CSS-::after, damit lange
    // Begründungstexte (Vorwert, betroffene Werte) nicht von der scrollbaren
    // Rohwert-Tabelle abgeschnitten werden. Event-Delegation auf document,
    // weil #rows-table bei jeder Filter-/Seiten-Änderung per htmx neu
    // eingesetzt wird.
    (function () {
      let tip = null;
      let tipTimer = null;
      function ensureTip() {
        if (!tip) {
          tip = document.createElement('div');
          tip.className = 'js-tooltip';
          document.body.appendChild(tip);
        }
        return tip;
      }
      function showTip(badge) {
        const text = badge.getAttribute('data-tooltip');
        if (!text) return;
        const el = ensureTip();
        el.textContent = text;
        el.classList.add('open');
        const anchor = badge.getBoundingClientRect();
        const box = el.getBoundingClientRect();
        let left = Math.min(anchor.left, window.innerWidth - box.width - 8);
        left = Math.max(8, left);
        let top = anchor.top - box.height - 6;
        if (top < 8) top = anchor.bottom + 6;
        el.style.left = left + 'px';
        el.style.top = top + 'px';
      }
      function hideTip() {
        if (tipTimer) {
          clearTimeout(tipTimer);
          tipTimer = null;
        }
        if (tip) tip.classList.remove('open');
      }
      document.addEventListener('mouseover', e => {
        const badge = e.target.closest('.flag-badge[data-tooltip]');
        if (badge) {
          if (tipTimer) clearTimeout(tipTimer);
          tipTimer = setTimeout(() => {
            tipTimer = null;
            showTip(badge);
          }, 600);
        }
      });
      document.addEventListener('mouseout', e => {
        const badge = e.target.closest('.flag-badge[data-tooltip]');
        if (badge) hideTip();
      });
      document.addEventListener('scroll', hideTip, true);
    })();

    // Direkteinstieg aus dem Markierungs-Menü des Entitäts-Charts
    // (…/cleanup?undo=1): die Rückgängig-Vorschau gleich aufklappen, statt den
    // Nutzer den Knopf suchen zu lassen. Genau dafür verweist das Menü
    // hierher — die Vorschau zeigt die betroffenen Zeilen, bevor irgendetwas
    // passiert, während eine Rückfrage im Menü nur eine Zahl nennen könnte
    // (und die letzte Charge kann sechsstellig sein).
    //
    // Warum ein Warteschritt und nicht einfach der erste htmx-Austausch: die
    // Zeilentabelle wird beim Seitenaufbau ZWEIMAL geholt. Erst durch
    // hx-trigger="load" auf #controls, und gleich darauf noch einmal, weil das
    // frisch eingesetzte Fragment das Seitengrößen-Feld schreibt und damit ein
    // "change" auslöst — sichtbar an den beiden /rows-Anfragen, von denen nur
    // die zweite page_size trägt. Wer die Vorschau nach dem ERSTEN Austausch
    // öffnet, sieht sie vom zweiten sofort wieder überschrieben; am laufenden
    // Stand war genau das der Fall, und es sah aus, als hätte der Klick nie
    // stattgefunden. Deshalb erst öffnen, wenn kurz kein Austausch mehr kam.
    if (new URLSearchParams(location.search).get('undo') === '1') {
      let warte = null;
      const versuche = () => {
        const knopf = document.getElementById('undo-preview-btn');
        if (!knopf) return;
        // Deaktiviert heißt: es gibt gar keine Löschung zum Rückgängigmachen.
        // Dann ist der Direkteinstieg gegenstandslos, aber erledigt.
        if (!knopf.disabled) knopf.click();
        document.body.removeEventListener('htmx:afterSwap', beobachte);
      };
      const beobachte = (e) => {
        if (!e.target || e.target.id !== 'rows-table') return;
        clearTimeout(warte);
        warte = setTimeout(versuche, 300);
      };
      document.body.addEventListener('htmx:afterSwap', beobachte);
      // Falls die Tabelle schon steht, bevor diese Datei ausgeführt wird.
      if (document.getElementById('undo-preview-btn')) warte = setTimeout(versuche, 300);
    }
