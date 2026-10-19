// Gemeinsames Öffnen/Schließen/Auswählen für alle .dd-picker-Dropdowns
// (Einfachauswahl-Filter, Einstellungs-Selects, Spalten-Auswahl) — eine
// Implementierung statt der früher pro Seite duplizierten Varianten, damit
// jedes Dropdown in der App exakt gleich aussieht UND sich gleich verhält.
// Erwartete IDs je Dropdown: "{prefix}-btn" (Auslöser), "{prefix}-input"
// (verdecktes Formularfeld, trägt Name + evtl. hx-*/onchange-Attribute wie
// vorher der native <select>) und "{prefix}-popover" (Zeilen-Container).
// Mehrfachauswahl (z. B. der Typ-Filter) nutzt eigene Checkbox-Logik und ruft
// hier nur toggleDDPicker() für das Öffnen/Schließen mit.
function toggleDDPicker(prefix) {
  const popover = document.getElementById(prefix + '-popover');
  const opening = !popover.classList.contains('open');
  popover.classList.toggle('open');
  // Beim Öffnen die aktuell aktive Zeile in den sichtbaren Bereich scrollen
  // (relevant für lange, scrollbare Listen wie den Zeitraum-Typ-Picker im
  // Tabellen-Editor oder den Entität-Filter in Housekeeping → Aktivität).
  // 'nearest' statt 'center': scrollt nur das Minimum, das nötig ist, um die
  // Zeile innerhalb des (jetzt selbst scrollbaren, siehe app.css) Popovers
  // sichtbar zu machen — 'center' zog sonst auch die ganze Seite mit, wenn
  // die aktive Zeile weit unten in einer langen Liste stand.
  if (opening) {
    popover.querySelector('.dd-picker-row.active')?.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
}

function selectDDOption(prefix, value, label) {
  const input = document.getElementById(prefix + '-input');
  input.value = value;
  const btn = document.getElementById(prefix + '-btn');
  if (btn) btn.textContent = label + ' ▾';
  const popover = document.getElementById(prefix + '-popover');
  const fromKeyboard = popover.contains(document.activeElement);
  popover.classList.remove('open');
  // Per Tastatur gewählt: Fokus zurück auf den Auslöser, sonst stünde er auf
  // einer jetzt unsichtbaren Zeile und Tab spränge an den Seitenanfang.
  if (fromKeyboard && btn) btn.focus();
  // bubbles:true, damit sowohl ein onchange direkt am Feld als auch ein
  // htmx hx-trigger="change" auf einem umschließenden Container (z. B.
  // #controls) unverändert weiter greifen — dieselbe Erwartung wie bei
  // einem echten <select>, dessen change-Event ebenfalls bubbelt.
  input.dispatchEvent(new Event('change', {bubbles: true}));
}

// Uhrzeit-Eingabe: zwei editierbare Zahlenfelder (Stunde/Minute) mit
// Pfeiltasten statt eines Dropdowns — Ersatz für das native
// <input type="time">, dessen Auswahl-Popup sich nicht im App-Stil
// gestalten lässt, UND für einen zuvor erprobten Listen-Picker, der sich
// als unhandlich erwies. Direktes Tippen (Feld fokussiert automatisch den
// ganzen Wert, siehe onfocus im Template) oder Klick auf ▲/▼ ändert den
// Wert, mit Umlauf an den Grenzen (23→00, 00→23 bzw. 59→00, 00→59). Beide
// Felder schreiben gemeinsam in ein verdecktes "HH:MM"-Feld.
function _ddTimeMax(part) {
  return part === 'hour' ? 24 : 60;
}

function stepDDTime(prefix, part, delta) {
  const field = document.getElementById(`${prefix}-${part}-field`);
  const max = _ddTimeMax(part);
  const current = parseInt(field.value, 10) || 0;
  const next = ((current + delta) % max + max) % max;
  field.value = String(next).padStart(2, '0');
  commitDDTime(prefix);
}

function commitDDTimeField(prefix, part, fieldEl) {
  const max = _ddTimeMax(part) - 1;
  let n = parseInt(fieldEl.value, 10);
  if (Number.isNaN(n)) n = 0;
  n = Math.min(max, Math.max(0, n));
  fieldEl.value = String(n).padStart(2, '0');
  commitDDTime(prefix);
}

function commitDDTime(prefix) {
  const hour = document.getElementById(`${prefix}-hour-field`).value;
  const minute = document.getElementById(`${prefix}-minute-field`).value;
  const input = document.getElementById(`${prefix}-input`);
  input.value = `${hour}:${minute}`;
  const btn = document.getElementById(`${prefix}-btn`);
  if (btn) btn.textContent = `${hour}:${minute}`;
  input.dispatchEvent(new Event('change', {bubbles: true}));
}

document.addEventListener('click', (e) => {
  document.querySelectorAll('.dd-picker-wrap').forEach(wrap => {
    if (!wrap.contains(e.target)) {
      wrap.querySelector('.dd-picker-popover')?.classList.remove('open');
    }
  });
});

// Tastaturbedienung für alle .dd-picker-Popover (Review vom 16.10., Punkt 6).
// Zentral hier statt je Template: die Zeilen sind <div>s mit onclick und
// ohne eigenen Fokus. Sie werden beim Öffnen per Tastatur fokussierbar
// gemacht (tabindex=-1, also nur per Pfeiltaste, nicht per Tab erreichbar):
// Enter/Leertaste/Pfeil runter am Auslöser öffnet und fokussiert die aktive
// Zeile, Pfeile/Pos1/Ende bewegen, Enter/Leertaste wählt (= Klick), Escape
// schließt und gibt den Fokus an den Auslöser zurück, Tab schließt.
function _ddRows(popover) {
  return Array.from(popover.querySelectorAll('.dd-picker-row')).filter(
    (row) => _ddVisible(row) && !row.classList.contains('is-empty')
  );
}

// getClientRects statt offsetParent: die Alpine-Popover im Tabellen-Editor
// sind position:fixed, dort ist offsetParent auch sichtbar immer null.
function _ddVisible(el) {
  return el.getClientRects().length > 0;
}

function _ddIsOpen(popover) {
  return _ddVisible(popover);
}

function _ddFocusRow(popover, which) {
  const rows = _ddRows(popover);
  if (!rows.length) return;
  rows.forEach((row) => { if (!row.hasAttribute('tabindex')) row.setAttribute('tabindex', '-1'); });
  let target = rows[0];
  if (which === 'active') target = popover.querySelector('.dd-picker-row.active') || rows[0];
  else if (which === 'last') target = rows[rows.length - 1];
  else if (typeof which === 'number') target = rows[Math.max(0, Math.min(rows.length - 1, which))];
  if (!rows.includes(target)) target = rows[0];
  target.focus({preventScroll: true});
  target.scrollIntoView({block: 'nearest', inline: 'nearest'});
}

function _ddClose(wrap, popover) {
  if (popover.classList.contains('open')) {
    popover.classList.remove('open');
  } else if (_ddVisible(popover)) {
    // Alpine-gesteuerte Variante (x-show statt .open): deren Auslöser schaltet um.
    wrap.querySelector('button')?.click();
  }
}

document.addEventListener('keydown', (e) => {
  const wrap = e.target.closest && e.target.closest('.dd-picker-wrap');
  if (!wrap) return;
  const popover = wrap.querySelector('.dd-picker-popover');
  if (!popover) return;
  const trigger = wrap.querySelector('button');
  const inPopover = popover.contains(e.target);
  const typing = e.target.matches('input:not([type=checkbox]):not([type=radio]), textarea');

  if (!inPopover) {
    if (e.target !== trigger) return;
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      // Selbst klicken statt auf den Browser zu warten: die Leertaste löst den
      // Button-Klick erst beim Loslassen aus, also nach diesem Handler.
      e.preventDefault();
      if (!(e.key === 'ArrowDown' && _ddIsOpen(popover))) trigger.click();
      // Alpine rendert x-show asynchron — erst danach die Zeile fokussieren.
      setTimeout(() => { if (_ddIsOpen(popover)) _ddFocusRow(popover, 'active'); }, 0);
    } else if (e.key === 'Escape' && _ddIsOpen(popover)) {
      e.preventDefault();
      _ddClose(wrap, popover);
    }
    return;
  }

  if (e.key === 'Escape') {
    e.preventDefault();
    _ddClose(wrap, popover);
    trigger?.focus();
    return;
  }
  if (e.key === 'Tab') {
    _ddClose(wrap, popover);
    return;
  }
  const rows = _ddRows(popover);
  const index = rows.indexOf(e.target.closest('.dd-picker-row'));
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    _ddFocusRow(popover, index + 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (index > 0) _ddFocusRow(popover, index - 1);
    else if (index === 0) trigger?.focus();
  } else if (!typing && e.key === 'Home') {
    e.preventDefault();
    _ddFocusRow(popover, 'first');
  } else if (!typing && e.key === 'End') {
    e.preventDefault();
    _ddFocusRow(popover, 'last');
  } else if (!typing && (e.key === 'Enter' || e.key === ' ') && index >= 0) {
    e.preventDefault();
    e.target.closest('.dd-picker-row').click();
  }
});
