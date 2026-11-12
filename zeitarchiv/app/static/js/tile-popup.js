// Klick auf eine Wert-, Chart- oder Tabellenkachel öffnet ein nur lesendes Popup statt die Seite zu
// wechseln (Roadmap 1.55). Der Inhalt ist die bestehende Seite der Kachel (?popup=1), eingebettet in
// einen iframe — so gibt es keine zweite Pflege von Verlauf, Chart und Tabelle. „Bearbeiten“ führt auf
// die bisherige Seite. Strg/Cmd/Shift/Mittelklick behalten das normale Verhalten (neuer Tab).
(function () {
  const dialog = document.getElementById('tile-popup');
  if (!dialog) return;
  const frame = dialog.querySelector('.tile-popup-frame');
  const title = dialog.querySelector('.tile-popup-title');
  const editLink = dialog.querySelector('.tile-popup-edit');
  const pageLink = dialog.querySelector('.tile-popup-page');
  const chipWrap = dialog.querySelector('.tile-popup-refresh-wrap');
  const chip = chipWrap.querySelector('.tile-popup-refresh');
  const chipText = chip.querySelector('.tile-popup-refresh-text');
  const menu = chipWrap.querySelector('.tile-popup-refresh-menu');
  let pushed = false;

  // Alpine-Daten der Seite im iframe (gleiche Herkunft): dort steckt der Zustand des Chips „Aktuell“.
  function pageData() {
    try {
      const w = frame.contentWindow;
      if (!w || !w.Alpine) return null;
      for (const el of w.document.querySelectorAll('[x-data]')) {
        const data = w.Alpine.$data(el);
        if (data && typeof data.refreshChipText === 'function') return data;
      }
    } catch (e) { /* Seite lädt noch */ }
    return null;
  }
  function closeMenu() {
    menu.hidden = true;
    chip.setAttribute('aria-expanded', 'false');
    const data = pageData();
    if (data) data.refreshMenuOpen = false;
  }
  function syncChip() {
    const data = dialog.open ? pageData() : null;
    chipWrap.hidden = !data;
    if (!data) { closeMenu(); return; }
    chipText.textContent = data.refreshChipText();
    chip.classList.toggle('paused', !!data.refreshPause());
    chip.title = data.refreshChipTitle ? data.refreshChipTitle() : '';
    if (!menu.hidden) {
      menu.querySelector('.refresh-info').textContent = data.refreshInfo();
      menu.querySelector('.refresh-state').textContent = data.refreshOn ? window.t('An') : window.t('Aus');
    }
  }
  // Dasselbe Menü wie auf der Seite selbst: Info, jetzt aktualisieren, automatisch an/aus.
  chip.addEventListener('click', () => {
    const data = pageData();
    if (!data) return;
    const open = menu.hidden;
    menu.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
    // Die Seite zählt das Alter nur bei offenem Menü hoch (refreshTick).
    data.refreshMenuOpen = open;
    if (open) data.refreshAge = Math.round((Date.now() - data._lastRefresh) / 1000);
    syncChip();
  });
  menu.addEventListener('click', (e) => {
    const row = e.target.closest('[data-refresh]');
    const data = pageData();
    if (!row || !data) return;
    if (row.dataset.refresh === 'now') { data.refreshNow(); closeMenu(); } else { data.refreshOn = !data.refreshOn; syncChip(); }
  });
  document.addEventListener('click', (e) => { if (!menu.hidden && !chipWrap.contains(e.target)) closeMenu(); });
  setInterval(syncChip, 1000);

  function withParams(href, params) {
    const url = new URL(href, location.href);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    return url.pathname + url.search;
  }

  function open(anchor) {
    const tile = anchor.closest('.dtile');
    const type = tile && tile.dataset.itemType;
    const params = {};
    // Wertekachel: der Verlauf startet mit dem Zeitraum der Kachel.
    if (type === 'entity' && anchor.dataset.range) params.range = anchor.dataset.range;
    const base = anchor.getAttribute('href');
    title.textContent = (anchor.querySelector('.dtile-title') || {}).textContent || '';
    frame.src = withParams(base, { ...params, popup: '1' });
    // Chart und Tabelle haben einen eigenen Bearbeiten-Modus (?edit=1); die Entität nur ihre Seite.
    editLink.href = type === 'entity' ? withParams(base, params) : withParams(base, { edit: '1' });
    pageLink.href = withParams(base, params);
    if (!dialog.open) dialog.showModal();
    if (!pushed) { history.pushState({ zaTilePopup: true }, ''); pushed = true; }
  }

  function closed() {
    frame.src = 'about:blank';
    chipWrap.hidden = true;
    menu.hidden = true;
    if (pushed) { pushed = false; if (history.state && history.state.zaTilePopup) history.back(); }
  }

  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const anchor = e.target.closest('a.dtile-body[href]');
    if (!anchor || !anchor.closest('.dtile-entity, .dtile[data-item-type="chart"], .dtile[data-item-type="table"]')) return;
    e.preventDefault();
    open(anchor);
  });

  dialog.addEventListener('close', closed);
  dialog.querySelector('.tile-popup-close').addEventListener('click', () => dialog.close());
  // Klick auf den abgedunkelten Hintergrund schließt.
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  // Zurück-Knopf des Browsers schließt das Popup (statt die Dashboard-Seite zu verlassen).
  window.addEventListener('popstate', () => { if (dialog.open) { pushed = false; dialog.close(); } });
  // Esc im iframe (siehe base.html) erreicht das Elternfenster nur über diese Nachricht.
  window.addEventListener('message', (e) => {
    if (e.origin === location.origin && e.data && e.data.zaPopupClose && dialog.open) dialog.close();
  });
})();
