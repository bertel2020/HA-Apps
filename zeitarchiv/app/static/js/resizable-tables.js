// Gemeinsame Spaltenbreiten-Anpassung für technische Datentabellen.
// Das Modul wird bewusst nur auf Seiten außerhalb der Funktion "Tabellen"
// geladen. Es erkennt auch per htmx nachgeladene Fragmente automatisch.
(function () {
  'use strict';

  const STORAGE_PREFIX = 'zeitarchiv.table-widths.v1:';
  const MIN_COLUMN_WIDTH = 56;
  const states = new WeakMap();

  function normalizeHeader(text) {
    return text.replace(/[↑↓]/g, '').replace(/\s+/g, ' ').trim() || 'leer';
  }

  function storageKey(table, headers) {
    const explicit = table.dataset.resizeKey;
    const signature = explicit || headers.map(th => normalizeHeader(th.textContent)).join('|');
    return `${STORAGE_PREFIX}${location.pathname}:${signature}`;
  }

  function loadWidths(key, count) {
    try {
      const widths = JSON.parse(localStorage.getItem(key) || 'null');
      if (Array.isArray(widths) && widths.length === count && widths.every(v => Number.isFinite(v) && v >= MIN_COLUMN_WIDTH)) {
        return widths;
      }
    } catch (_) {
      // localStorage kann in restriktiven Browser-Kontexten deaktiviert sein;
      // Ziehen funktioniert dann weiterhin, lediglich ohne Persistenz.
    }
    return null;
  }

  function saveWidths(key, widths) {
    try { localStorage.setItem(key, JSON.stringify(widths.map(Math.round))); } catch (_) {}
  }

  function clearWidths(key) {
    try { localStorage.removeItem(key); } catch (_) {}
  }

  function ensureColgroup(table, count) {
    let colgroup = Array.from(table.children).find(el => el.tagName === 'COLGROUP');
    if (!colgroup) {
      colgroup = document.createElement('colgroup');
      table.insertBefore(colgroup, table.firstChild);
    }
    while (colgroup.children.length < count) colgroup.appendChild(document.createElement('col'));
    return Array.from(colgroup.children).slice(0, count);
  }

  function measuredWidths(headers) {
    return headers.map(th => Math.max(MIN_COLUMN_WIDTH, Math.round(th.getBoundingClientRect().width)));
  }

  function applyWidths(state, widths) {
    state.widths = widths.map(width => Math.max(MIN_COLUMN_WIDTH, Math.round(width)));
    state.cols.forEach((col, index) => { col.style.width = `${state.widths[index]}px`; });
    state.table.style.tableLayout = 'fixed';
    state.table.style.width = `${state.widths.reduce((sum, width) => sum + width, 0)}px`;
  }

  function createResetAction(state) {
    const actions = document.createElement('div');
    actions.className = 'table-resize-actions';
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'table-resize-reset';
    reset.textContent = 'Spaltenbreiten zurücksetzen';
    reset.title = 'Auf die ursprünglichen Spaltenbreiten dieser Tabelle zurücksetzen';
    reset.addEventListener('click', () => {
      clearWidths(state.key);
      state.cols.forEach(col => { col.style.width = ''; });
      state.table.style.tableLayout = state.initialTableLayout;
      state.table.style.width = state.initialTableWidth;
      actions.hidden = true;
      requestAnimationFrame(() => { state.widths = measuredWidths(state.headers); });
    });
    actions.appendChild(reset);
    state.wrapper.insertBefore(actions, state.table);
    state.actions = actions;
  }

  function startResize(event, state, columnIndex) {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidths = measuredWidths(state.headers);
    applyWidths(state, startWidths);
    let moved = false;
    document.body.classList.add('table-column-resizing');

    const move = moveEvent => {
      moved = true;
      const widths = [...startWidths];
      widths[columnIndex] = Math.max(MIN_COLUMN_WIDTH, startWidths[columnIndex] + moveEvent.clientX - startX);
      applyWidths(state, widths);
    };
    const stop = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', stop);
      document.body.classList.remove('table-column-resizing');
      if (moved) {
        saveWidths(state.key, state.widths);
        state.actions.hidden = false;
      }
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', stop, {once: true});
  }

  function keyboardResize(event, state, columnIndex) {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const widths = state.table.style.tableLayout === 'fixed' ? [...state.widths] : measuredWidths(state.headers);
    const step = event.shiftKey ? 20 : 8;
    widths[columnIndex] = Math.max(MIN_COLUMN_WIDTH, widths[columnIndex] + (event.key === 'ArrowRight' ? step : -step));
    applyWidths(state, widths);
    saveWidths(state.key, state.widths);
    state.actions.hidden = false;
  }

  function enhance(table) {
    if (states.has(table) || table.dataset.resizable === 'off' || table.offsetParent === null) return;
    const headerRow = Array.from(table.querySelectorAll('tr')).find(row => row.querySelector('th'));
    if (!headerRow) return;
    const headers = Array.from(headerRow.children).filter(cell => cell.tagName === 'TH');
    if (headers.length < 2) return;
    const wrapper = table.closest('.tbl-wrap') || table.parentElement;
    const state = {
      table, headers, wrapper,
      cols: ensureColgroup(table, headers.length),
      key: storageKey(table, headers),
      widths: measuredWidths(headers),
      initialTableLayout: table.style.tableLayout,
      initialTableWidth: table.style.width,
      actions: null,
    };
    states.set(table, state);
    wrapper.classList.add('table-resize-enabled');
    createResetAction(state);

    const stored = loadWidths(state.key, headers.length);
    if (stored) {
      applyWidths(state, stored);
      state.actions.hidden = false;
    } else {
      state.actions.hidden = true;
    }

    headers.forEach((header, index) => {
      const handle = document.createElement('span');
      handle.className = 'table-column-resize-handle';
      handle.setAttribute('role', 'separator');
      handle.setAttribute('aria-orientation', 'vertical');
      handle.setAttribute('aria-label', `Spaltenbreite ${normalizeHeader(header.textContent)} ändern`);
      handle.setAttribute('hx-disable', '');
      handle.tabIndex = 0;
      handle.addEventListener('pointerdown', event => startResize(event, state, index));
      // Sortierlinks liegen in denselben Headerzellen. Ein abgeschlossener
      // Klick auf den Ziehgriff darf deshalb niemals als Sortierklick bis zum
      // Header weitergereicht werden.
      handle.addEventListener('click', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        event.stopPropagation();
      });
      handle.addEventListener('keydown', event => keyboardResize(event, state, index));
      const sortLink = header.querySelector(':scope > a');
      if (sortLink) {
        // Die rechten 10 px gehören vollständig dem Ziehgriff. Der Capture-
        // Schutz greift auch dann, wenn ein Browser den leeren Griff beim
        // einfachen Klicken geometrisch dem darunterliegenden Link zuordnet.
        sortLink.addEventListener('click', event => {
          if (event.clientX >= header.getBoundingClientRect().right - 10) {
            event.preventDefault();
            event.stopImmediatePropagation();
          }
        }, true);
      }
      header.appendChild(handle);
    });
  }

  function scan() {
    document.querySelectorAll('table.dt').forEach(enhance);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
  document.addEventListener('htmx:afterSwap', scan);
  document.addEventListener('click', () => setTimeout(scan, 0));
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
