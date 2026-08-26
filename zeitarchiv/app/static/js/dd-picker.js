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
  // Tabellen-Editor).
  if (opening) {
    popover.querySelector('.dd-picker-row.active')?.scrollIntoView({block: 'center'});
  }
}

function selectDDOption(prefix, value, label) {
  const input = document.getElementById(prefix + '-input');
  input.value = value;
  const btn = document.getElementById(prefix + '-btn');
  if (btn) btn.textContent = label + ' ▾';
  document.getElementById(prefix + '-popover').classList.remove('open');
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
