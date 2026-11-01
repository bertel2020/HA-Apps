(function () {
  'use strict';
  // Kategorien verwalten (ROADMAP 1.42). Die Seite ist serverseitig gerendert;
  // geändert wird per POST und danach neu geladen — bis auf die Reihenfolge, die
  // sich sofort in der Liste zeigt und im Hintergrund gespeichert wird.
  const root = document.getElementById('cat-root');
  const base = root.dataset.appRoot || '';
  const MAX = parseInt(root.dataset.max, 10);

  async function post(path, body) {
    let response;
    try {
      response = await fetch(base + path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {}),
      });
    } catch (_) {
      appAlert(t('Die Änderung konnte nicht gespeichert werden.'), {danger: true});
      return null;
    }
    let data = null;
    try { data = await response.json(); } catch (_) { /* leere Antwort */ }
    if (!response.ok) {
      appAlert((data && data.detail) || t('Die Änderung konnte nicht gespeichert werden.'), {danger: true});
      return null;
    }
    return data || {};
  }

  // ---- Leer-Zustand: Vorschläge übernehmen ----------------------------------------------
  const take = document.getElementById('cat-take');
  if (take) {
    const boxes = () => Array.from(document.querySelectorAll('#cat-suggest input'));
    const update = () => {
      const n = boxes().filter(cb => cb.checked).length;
      take.disabled = n === 0;
      take.textContent = t('{n} Vorschläge übernehmen', {n});
    };
    boxes().forEach(cb => cb.addEventListener('change', update));
    update();
    take.addEventListener('click', async () => {
      const names = boxes().filter(cb => cb.checked).map(cb => cb.value);
      take.disabled = true;
      if (await post('/entities/categories/suggestions', {names})) location.reload();
      else update();
    });
  }

  // ---- Anlegen ---------------------------------------------------------------------------
  const addForm = document.getElementById('cat-add');
  const newInput = document.getElementById('cat-new');
  addForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const name = newInput.value.trim();
    if (!name) { newInput.focus(); return; }
    if (await post('/entities/categories', {name})) location.reload();
  });
  const list = document.getElementById('cat-list');
  const limit = document.getElementById('cat-limit');
  if (list && list.children.length >= MAX) {
    addForm.hidden = true;
    limit.hidden = false;
  }
  if (!list) return;

  // ---- Reihenfolge -----------------------------------------------------------------------
  const rows = () => Array.from(list.querySelectorAll('.cat-row'));
  function refreshOrder() {
    const all = rows();
    all.forEach((row, i) => {
      row.querySelector('.cat-pos').textContent = String(i + 1);
      row.querySelector('[data-action=up]').disabled = i === 0;
      row.querySelector('[data-action=down]').disabled = i === all.length - 1;
    });
  }
  async function saveOrder() {
    refreshOrder();
    await post('/entities/categories/reorder', {ids: rows().map(row => parseInt(row.dataset.id, 10))});
  }
  refreshOrder();

  let dragged = null;
  list.addEventListener('dragstart', (event) => {
    const handle = event.target.closest('.cat-handle');
    if (!handle) return;
    dragged = handle.closest('.cat-row');
    dragged.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', dragged.dataset.id);
    event.dataTransfer.setDragImage(dragged, 12, 12);
  });
  function clearMarks() {
    list.querySelectorAll('.drop-before,.drop-after').forEach(el => el.classList.remove('drop-before', 'drop-after'));
  }
  list.addEventListener('dragover', (event) => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest('.cat-row');
    clearMarks();
    if (!target || target === dragged) return;
    const rect = target.getBoundingClientRect();
    target.classList.add(event.clientY < rect.top + rect.height / 2 ? 'drop-before' : 'drop-after');
  });
  list.addEventListener('drop', (event) => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest('.cat-row');
    if (target && target !== dragged) {
      const before = target.classList.contains('drop-before');
      list.insertBefore(dragged, before ? target : target.nextSibling);
      saveOrder();
    }
    clearMarks();
  });
  list.addEventListener('dragend', () => {
    if (dragged) dragged.classList.remove('is-dragging');
    dragged = null;
    clearMarks();
  });

  // ---- Zeilenaktionen --------------------------------------------------------------------
  function startRename(row) {
    const nameEl = row.querySelector('.cat-name');
    if (nameEl.querySelector('input')) return;
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = parseInt(newInput.maxLength, 10);
    input.value = nameEl.dataset.name;
    input.setAttribute('aria-label', t('Neuer Name'));
    nameEl.textContent = '';
    nameEl.appendChild(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const value = input.value.trim();
      if (save && value && value !== nameEl.dataset.name) {
        if (await post('/entities/categories/' + row.dataset.id + '/rename', {name: value})) { location.reload(); return; }
      }
      nameEl.textContent = nameEl.dataset.name;
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      else if (event.key === 'Escape') { event.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }

  async function removeCategory(row, button) {
    const n = parseInt(button.dataset.count, 10) || 0;
    const name = row.querySelector('.cat-name').dataset.name;
    const detail = n === 0
      ? ''
      : (n === 1
        ? t('1 Entität verliert diese Kategorie und steht danach ohne Kategorie.')
        : t('{n} Entitäten verlieren diese Kategorie und stehen danach ohne Kategorie.', {n}));
    const message = t('Kategorie „{name}“ löschen?', {name}) + (detail ? ' ' + detail : '') + ' ' + t('Die Entitäten selbst bleiben unberührt.');
    if (!await window.appConfirm(message, {danger: true, confirmLabel: t('Löschen')})) return;
    if (await post('/entities/categories/' + row.dataset.id + '/delete')) location.reload();
  }

  list.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const row = button.closest('.cat-row');
    switch (button.dataset.action) {
      case 'rename': startRename(row); break;
      case 'delete': removeCategory(row, button); break;
      case 'up': if (row.previousElementSibling) { list.insertBefore(row, row.previousElementSibling); saveOrder(); } break;
      case 'down': if (row.nextElementSibling) { list.insertBefore(row.nextElementSibling, row); saveOrder(); } break;
      case 'color':
        if (await post('/entities/categories/' + row.dataset.id + '/color', {slot: parseInt(button.dataset.slot, 10)})) location.reload();
        break;
      default: break;
    }
  });
})();
