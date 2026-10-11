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
    const EXPORT_TYPE_LABELS = {standard: t('Standard'), counter: t('Zähler'), switch: t('Schalter')};
    function updateExportTypeFilterLabel() {
      const checked = Array.from(document.querySelectorAll('input[name=type]:checked'));
      const btn = document.getElementById('export-type-filter-btn');
      if (!checked.length || checked.some(cb => cb.value === 'all')) {
        btn.textContent = t('Typ: Alle ▾');
        return;
      }
      btn.textContent = t('Typ: ') + checked.map(cb => EXPORT_TYPE_LABELS[cb.value]).join(', ') + ' ▾';
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

    // ---- Assistent: Entitäten → Zeitraum → Auflösung → Prüfen ----------------
    // Die Auswahl lebt hier im Set und nicht in den Checkboxen: die Liste wird bei
    // Suche, Filter und Seitenwechsel neu geladen (htmx), die Auswahl muss das
    // überstehen. Zeilenzahlen und Datenspannen kommen vom Server (export/estimate),
    // der Assistent rechnet nichts selbst.
    (function () {
      const wizard = document.getElementById('export-wizard');
      if (!wizard) return;
      const $ = id => document.getElementById(id);
      const STEPS = [t('Entitäten'), t('Zeitraum'), t('Auflösung'), t('Prüfen')];
      const RESOLUTIONS = [
        {key: 'raw', label: t('Rohwerte'), desc: t('Jeder gespeicherte Wert')},
        {key: 'minute', label: t('Minute'), desc: t('Ein Wert je Minute')},
        {key: 'hour', label: t('Stunde'), desc: t('Ein Wert je Stunde')},
        {key: 'day', label: t('Tag'), desc: t('Ein Wert je Tag')},
      ];
      const MONTHS_SHORT = [t('Jan'), t('Feb'), t('Mär'), t('Apr'), t('Mai'), t('Jun'), t('Jul'), t('Aug'), t('Sep'), t('Okt'), t('Nov'), t('Dez')];
      const MONTHS_LONG = [t('Januar'), t('Februar'), t('März'), t('April'), t('Mai'), t('Juni'), t('Juli'), t('August'), t('September'), t('Oktober'), t('November'), t('Dezember')];
      const FALLBACK_LIMIT = parseInt(wizard.dataset.maxRows || '5000000', 10);
      const now = new Date();
      const state = {
        step: 1, selected: new Set(), mode: 'month', year: now.getFullYear(), month: now.getMonth() + 1,
        from: '', to: '', resolution: 'raw', info: null, failed: false, seq: 0, placed: false,
      };
      const number = n => Math.round(n).toLocaleString(window.ZA_FORMAT || undefined);
      const pad = n => String(n).padStart(2, '0');
      const day = text => { const [y, m, d] = text.split('-').map(Number); return new Date(y, m - 1, d); };
      const localDate = text => day(text).toLocaleDateString(window.ZA_FORMAT || undefined);
      const lastOfMonth = (y, m) => new Date(y, m, 0).getDate();

      function selection() { return Array.from(state.selected); }
      function limit() { return state.info ? state.info.limit : FALLBACK_LIMIT; }

      // Zeitraum als von/bis (ISO-Datum, beide Enden inklusive; leer = offen).
      function period() {
        if (state.mode === 'year') return {from: `${state.year}-01-01`, to: `${state.year}-12-31`};
        if (state.mode === 'month') {
          const m = pad(state.month);
          return {from: `${state.year}-${m}-01`, to: `${state.year}-${m}-${pad(lastOfMonth(state.year, state.month))}`};
        }
        if (state.mode === 'free') return {from: state.from, to: state.to};
        return {from: '', to: ''};
      }
      function periodText() {
        const p = period();
        if (state.mode === 'all') return t('Komplette Historie der gewählten Entitäten');
        if (state.mode === 'year') return `${t('Jahr')} ${state.year}, ${localDate(p.from)} – ${localDate(p.to)}`;
        if (state.mode === 'month') return `${MONTHS_LONG[state.month - 1]} ${state.year}, ${localDate(p.from)} – ${localDate(p.to)}`;
        if (!p.from && !p.to) return t('Von und Bis leer: komplette Historie');
        return `${p.from ? localDate(p.from) : t('Anfang')} – ${p.to ? localDate(p.to) : t('Ende')}`;
      }

      // Datenspanne der Auswahl (Union) für Jahres- und Monatsauswahl.
      function span() {
        const files = state.info ? state.info.files.filter(f => f.first && f.last) : [];
        if (!files.length) return null;
        const first = files.map(f => f.first).sort()[0];
        const last = files.map(f => f.last).sort().slice(-1)[0];
        return {first, last};
      }
      function monthHasData(y, m) {
        if (!state.info) return true;
        const start = `${y}-${pad(m)}-01`, end = `${y}-${pad(m)}-${pad(lastOfMonth(y, m))}`;
        return state.info.files.some(f => f.first && f.last && f.first <= end && f.last >= start);
      }
      // Beim ersten Eintreffen der Datenspanne den Vorgabemonat auf einen mit Daten setzen.
      function placeInData() {
        const s = span();
        if (!s || state.placed) return false;
        state.placed = true;
        if (monthHasData(state.year, state.month)) return false;
        const [y, m] = s.last.split('-').map(Number);
        state.year = y; state.month = m;
        return state.mode === 'month' || state.mode === 'year';
      }

      async function refresh() {
        const ids = selection();
        const seq = ++state.seq;
        if (!ids.length) { state.info = null; state.failed = false; state.placed = false; render(); return; }
        const p = period();
        try {
          const response = await fetch('export/estimate', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({entity_ids: ids, start: p.from, end: p.to}),
          });
          if (!response.ok) throw new Error(String(response.status));
          const data = await response.json();
          if (seq !== state.seq) return;
          state.info = data; state.failed = false;
          // Vorgabemonat ohne Daten: auf den letzten mit Daten setzen und mit diesem
          // Zeitraum neu schätzen, sonst zeigt die Fußleiste 0 Zeilen.
          if (placeInData()) { render(); refresh(); return; }
        } catch (_err) {
          if (seq !== state.seq) return;
          state.info = null; state.failed = true;
        }
        render();
      }

      // Zeilen je Datei bei der gewählten Auflösung; Summe und größte Datei.
      function stats(resolution) {
        const files = state.info ? state.info.files : [];
        const rows = files.map(f => f.rows[resolution]);
        return {
          count: files.length, total: rows.reduce((a, b) => a + b, 0),
          worst: rows.length ? Math.max(...rows) : 0,
          tooBig: files.filter(f => f.rows[resolution] > limit()).map(f => f.name),
        };
      }
      function bytesText(stat) {
        const bytes = stat.total * 40 * (stat.count > 1 ? 0.25 : 1);
        return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
      }

      function valid(step) {
        if (step === 1) return state.selected.size > 0;
        if (step === 2) return !state.failed && (state.mode !== 'free' || !state.from || !state.to || state.from <= state.to);
        if (step === 3) return !!state.info && stats(state.resolution).tooBig.length === 0;
        return !!state.info && stats(state.resolution).tooBig.length === 0;
      }
      function reachable(step) {
        for (let i = 1; i < step; i++) if (!valid(i)) return false;
        return true;
      }

      function renderSteps() {
        $('export-steps').innerHTML = STEPS.map((label, i) => {
          const k = i + 1;
          const cls = k === state.step ? 'cur' : (k < state.step ? 'done' : '') + (reachable(k) ? '' : ' locked');
          return `<li class="${cls.trim()}"><span class="dot">${k < state.step ? '✓' : k}</span>` +
            `<button type="button" data-step="${k}"${k === state.step ? ' aria-current="step"' : ''}${reachable(k) ? '' : ' disabled'}>${label}</button></li>`;
        }).join('');
      }

      function renderPeriod() {
        const modeButtons = document.querySelectorAll('#export-mode button');
        modeButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === state.mode)));
        const yearly = state.mode === 'year' || state.mode === 'month';
        $('export-yearstep').hidden = !yearly;
        $('export-months').hidden = state.mode !== 'month';
        $('export-free').hidden = state.mode !== 'free';
        $('export-year-label').textContent = state.year;
        const s = span();
        const minYear = s ? parseInt(s.first.slice(0, 4), 10) : state.year - 20;
        const maxYear = s ? parseInt(s.last.slice(0, 4), 10) : state.year + 1;
        $('export-year-prev').disabled = state.year <= minYear;
        $('export-year-next').disabled = state.year >= maxYear;
        $('export-months').innerHTML = MONTHS_SHORT.map((label, i) => {
          const m = i + 1, has = monthHasData(state.year, m);
          return `<button type="button" data-month="${m}" aria-pressed="${state.month === m}"${has ? '' : ` disabled title="${t('Keine Daten')}"`}>${label}</button>`;
        }).join('');
        $('export-period-text').textContent = periodText();
      }

      function renderResolutions() {
        $('export-resolutions').innerHTML = RESOLUTIONS.map(r => {
          const st = stats(r.key);
          const note = state.info ? `${t('größte Datei: {n} Zeilen', {n: number(st.worst)})}${st.tooBig.length ? ' ' + t('(zu groß)') : ''}` : '…';
          return `<button type="button" class="export-res" data-resolution="${r.key}" aria-pressed="${state.resolution === r.key}">` +
            `<b>${r.label}</b><span>${r.desc}</span><span class="n">${note}</span></button>`;
        }).join('');
        $('export-res-hint').textContent = state.resolution === 'raw'
          ? t('Rohwerte werden unverändert übernommen.')
          : t('Mittelwert je Intervall, bei Zählern und Schaltern der letzte Wert des Intervalls.');
      }

      function escapeHtml(text) {
        return String(text).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
      }
      function renderReview() {
        const st = stats(state.resolution);
        const error = $('export-error');
        if (state.info && st.tooBig.length) {
          error.hidden = false;
          error.textContent = t('Zu groß: {names}. Zurück zu Zeitraum oder Auflösung und eingrenzen.', {names: st.tooBig.join(', ')});
        } else if (state.failed) {
          error.hidden = false;
          error.textContent = t('Die Größe konnte nicht berechnet werden. Bitte noch einmal versuchen.');
        } else {
          error.hidden = true;
        }
        $('export-review-lead').textContent = st.count > 1
          ? t('Mehrere Entitäten: Du bekommst ein ZIP mit je einer CSV.')
          : t('Eine Entität: Du bekommst direkt eine CSV.');
        const names = state.info ? state.info.files.map(f => f.name) : [];
        const resLabel = RESOLUTIONS.find(r => r.key === state.resolution).label;
        const bar = Math.min(100, st.worst / limit() * 100);
        $('export-summary').innerHTML =
          `<dt>${t('Entitäten')}</dt><dd>${escapeHtml(names.length > 6 ? names.slice(0, 6).join(', ') + ' … (' + names.length + ')' : names.join(', '))}</dd>` +
          `<dt>${t('Zeitraum')}</dt><dd>${escapeHtml(periodText())}</dd>` +
          `<dt>${t('Auflösung')}</dt><dd>${resLabel}</dd>` +
          `<dt>${t('Umfang')}</dt><dd>${t('{rows} Zeilen, ca. {size}', {rows: number(st.total), size: bytesText(st)})}${st.count > 1 ? ' ' + t('als ZIP') : ''}` +
          `<div class="export-meter${st.tooBig.length ? ' over' : ''}"><i style="width:${bar}%"></i></div>` +
          `<span class="export-hint">${t('Größte Datei: {worst} von {limit} Zeilen', {worst: number(st.worst), limit: number(limit())})}</span></dd>`;
      }

      function renderFooter() {
        const count = state.selected.size;
        $('export-selcount').textContent = t('{n} ausgewählt', {n: count});
        const st = stats(state.resolution);
        let live;
        if (!count) live = `<b>${t('Noch nichts gewählt')}</b><span>${t('Wähle mindestens eine Entität.')}</span>`;
        else if (!state.info) live = `<b>${count > 1 ? t('{n} CSV-Dateien im ZIP', {n: count}) : t('1 CSV-Datei')}</b><span>${state.failed ? t('Größe nicht verfügbar') : '…'}</span>`;
        else live = `<b>${st.count > 1 ? t('{n} CSV-Dateien im ZIP', {n: st.count}) : t('1 CSV-Datei')}</b>` +
          `<span>${t('{rows} Zeilen, ca. {size}', {rows: number(st.total), size: bytesText(st)})}${st.tooBig.length ? ' ' + t('(mindestens eine Datei zu groß)') : ''}</span>`;
        $('export-live').innerHTML = live;
        $('export-back').hidden = state.step === 1;
        const next = $('export-next');
        next.textContent = state.step === 4 ? (count > 1 ? t('ZIP herunterladen') : t('CSV herunterladen')) : t('Weiter');
        next.disabled = !valid(state.step);
      }

      function render() {
        for (let k = 1; k <= 4; k++) $(`export-step-${k}`).hidden = k !== state.step;
        if (state.step === 2) renderPeriod();
        if (state.step === 3) renderResolutions();
        if (state.step === 4) renderReview();
        renderSteps();
        renderFooter();
      }

      function go(step) {
        if (step < 1 || step > 4 || !reachable(step)) return;
        state.step = step;
        render();
      }

      function download() {
        const form = $('export-form');
        const p = period();
        const fields = [['start', p.from], ['end', p.to], ['resolution', state.resolution], ...selection().map(id => ['entity_id', id])];
        form.replaceChildren(...fields.map(([name, value]) => {
          const input = document.createElement('input');
          input.type = 'hidden'; input.name = name; input.value = value;
          return input;
        }));
        form.submit();
      }

      // ---- Ereignisse
      function markChecked() {
        document.querySelectorAll('input[data-export-select]').forEach(cb => { cb.checked = state.selected.has(cb.value); });
        const boxes = Array.from(document.querySelectorAll('input[data-export-select]'));
        const all = $('export-page-all');
        if (all) all.checked = boxes.length > 0 && boxes.every(cb => cb.checked);
        $('export-selcount').textContent = t('{n} ausgewählt', {n: state.selected.size});
      }
      document.body.addEventListener('htmx:afterSwap', ev => { if (ev.target.id === 'export-table') markChecked(); });
      $('export-table').addEventListener('change', ev => {
        const target = ev.target;
        if (target.matches('input[data-export-select]')) {
          if (target.checked) state.selected.add(target.value); else state.selected.delete(target.value);
        } else if (target.id === 'export-page-all') {
          document.querySelectorAll('input[data-export-select]').forEach(cb => {
            if (target.checked) state.selected.add(cb.value); else state.selected.delete(cb.value);
          });
        } else return;
        markChecked();
        refresh();
      });
      $('export-select-none').addEventListener('click', () => { state.selected.clear(); markChecked(); refresh(); });
      $('export-select-all').addEventListener('click', async () => {
        // Alle Treffer zu den Filtern der Liste, nicht nur die sichtbare Seite.
        const params = new URLSearchParams();
        const search = document.querySelector('#controls [name=search]');
        if (search && search.value) params.set('search', search.value);
        document.querySelectorAll('input[name=type]:checked').forEach(cb => params.append('type', cb.value));
        const unit = $('export-unit-filter-input');
        if (unit) params.set('unit', unit.value);
        try {
          const response = await fetch('export/entity-ids?' + params.toString());
          if (!response.ok) return;
          (await response.json()).entity_ids.forEach(id => state.selected.add(id));
        } catch (_err) { return; }
        markChecked();
        refresh();
      });

      $('export-steps').addEventListener('click', ev => {
        const button = ev.target.closest('button[data-step]');
        if (button) go(parseInt(button.dataset.step, 10));
      });
      $('export-back').addEventListener('click', () => go(state.step - 1));
      $('export-next').addEventListener('click', () => { if (state.step < 4) go(state.step + 1); else download(); });

      $('export-mode').addEventListener('click', ev => {
        const button = ev.target.closest('button[data-mode]');
        if (!button) return;
        state.mode = button.dataset.mode;
        render(); refresh();
      });
      $('export-year-prev').addEventListener('click', () => { state.year--; render(); refresh(); });
      $('export-year-next').addEventListener('click', () => { state.year++; render(); refresh(); });
      $('export-months').addEventListener('click', ev => {
        const button = ev.target.closest('button[data-month]');
        if (!button || button.disabled) return;
        state.month = parseInt(button.dataset.month, 10);
        render(); refresh();
      });
      ['export-from', 'export-to'].forEach(id => $(id).addEventListener('change', () => {
        state.from = $('export-from').value; state.to = $('export-to').value;
        render(); refresh();
      }));
      $('export-resolutions').addEventListener('click', ev => {
        const button = ev.target.closest('button[data-resolution]');
        if (!button) return;
        state.resolution = button.dataset.resolution;
        render();
      });

      render();
    })();
