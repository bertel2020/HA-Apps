// Leichtgewichtiges, EINMAL geteiltes Dropdown für die "→ HA-Entität"-
// Zuordnung im Symcon-Import — bewusst NICHT der Alpine-basierte
// entity-picker.js: eine einzige gemeinsame Popover-/Tooltip-Instanz für
// ALLE Zeilen statt einer eigenen Komponente pro Zeile, weil ein Symcon-
// Import hunderte Variablen gleichzeitig im DOM halten kann (siehe
// Kommentar bei .map-input in import.html) — pro Zeile ein eigener
// Alpine-Zustand wäre bei dieser Größenordnung spürbar langsamer.
//
// Das zugrunde liegende <input class="map-input" list="entity-datalist">
// bleibt unverändert (Formularwert, Einheiten-Abgleich, "Ignorieren" —
// siehe updateUnitCheck() weiter unten in import.html). Dieses Skript
// ersetzt nur die BEDIENUNG: das native Datalist-Popup lässt sich weder
// scrollen noch mit Tooltip versehen, deshalb eine eigene, scrollbare
// Liste mit denselben Einträgen (nur Friendly Names, Entity-ID per
// verzögertem Tooltip — dieselbe 450ms-Verzögerung wie beim Entity-Picker).
(function () {
  const datalist = document.getElementById('entity-datalist');
  if (!datalist) return;
  const options = Array.from(datalist.querySelectorAll('option')).map((o) => ({
    value: o.value,
    label: o.textContent,
  }));
  if (!options.length) return;

  let popover = null;
  let tooltip = null;
  let activeInput = null;
  let hoverTimer = null;

  function ensureElements() {
    if (popover) return;
    popover = document.createElement('div');
    popover.className = 'dd-picker-popover map-entity-popover';
    popover.style.position = 'fixed';
    popover.style.display = 'none';
    popover.style.maxHeight = '280px';
    popover.style.overflowY = 'auto';
    document.body.appendChild(popover);
    // Verhindert, dass ein Klick auf eine Zeile den Input vorher per Blur
    // schließt, bevor der click-Handler der Zeile überhaupt feuert.
    popover.addEventListener('mousedown', (e) => e.preventDefault());

    tooltip = document.createElement('div');
    tooltip.className = 'entity-tooltip entity-tooltip-floating';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);
  }

  function clearHoverTimer() {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
  }

  function hideTooltip() {
    clearHoverTimer();
    if (tooltip) tooltip.style.display = 'none';
  }

  function scheduleTooltip(opt, rowEl) {
    clearHoverTimer();
    const r = rowEl.getBoundingClientRect();
    hoverTimer = setTimeout(() => {
      tooltip.innerHTML = '';
      const strong = document.createElement('strong');
      strong.textContent = opt.label;
      const code = document.createElement('code');
      code.textContent = opt.value;
      tooltip.append(strong, code);
      tooltip.style.top = r.top - 6 + 'px';
      tooltip.style.left = r.left + 'px';
      tooltip.style.transform = 'translateY(-100%)';
      tooltip.style.display = 'block';
    }, 450);
  }

  function render(filterText) {
    const q = (filterText || '').trim().toLowerCase();
    popover.innerHTML = '';
    let shown = 0;
    options.forEach((opt) => {
      const isIgnore = opt.value === '__ignore__';
      if (!isIgnore && q && !(opt.label + ' ' + opt.value).toLowerCase().includes(q)) return;
      shown += 1;
      const row = document.createElement('div');
      row.className = 'dd-picker-row';
      row.textContent = opt.label;
      row.addEventListener('click', () => {
        const input = activeInput;
        input.value = opt.value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        close();
        input.focus();
      });
      if (!isIgnore) {
        row.addEventListener('mouseenter', () => scheduleTooltip(opt, row));
        row.addEventListener('mouseleave', hideTooltip);
      }
      popover.appendChild(row);
    });
    if (!shown) {
      const empty = document.createElement('div');
      empty.className = 'dd-picker-row';
      empty.style.color = 'var(--ink-faint)';
      empty.style.cursor = 'default';
      empty.textContent = 'Keine Treffer';
      popover.appendChild(empty);
    }
  }

  function position() {
    const r = activeInput.getBoundingClientRect();
    popover.style.left = r.left + 'px';
    popover.style.top = r.bottom + 4 + 'px';
    popover.style.width = Math.max(r.width, 220) + 'px';
  }

  function open(input) {
    ensureElements();
    activeInput = input;
    render('');
    position();
    popover.style.display = 'block';
  }

  function close() {
    if (popover) popover.style.display = 'none';
    hideTooltip();
    activeInput = null;
  }

  document.addEventListener('focusin', (e) => {
    if (e.target.matches && e.target.matches('.map-input')) open(e.target);
  });
  document.addEventListener('input', (e) => {
    if (activeInput && e.target === activeInput) render(e.target.value);
  });
  window.addEventListener(
    'scroll',
    () => {
      if (activeInput) position();
    },
    true
  );
  document.addEventListener('click', (e) => {
    if (!activeInput) return;
    if (e.target === activeInput) return;
    if (popover && popover.contains(e.target)) return;
    close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activeInput) {
      close();
      activeInput.blur();
    }
  });
})();
