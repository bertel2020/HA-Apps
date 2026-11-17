/* Filterleiste der Import-Tabellen (Home Assistant, VictoriaMetrics, InfluxDB, Symcon).

   Zwei Arten von Filtern, beide UND-verknüpft:
   * Zustands-Chips ([data-filter-chip]): je Chip ein Test auf die Zeile, vom Aufrufer geliefert.
   * Attribute ([data-filter-attr], Gruppen Kategorie/Raum/Typ): innerhalb einer Gruppe ODER; der Wert
     steht in der Zeile als data-f-<Gruppe>.

   Das Modul hält nur den Zustand und zeichnet die Leiste; die Tabelle selbst (Suche, Sortierung, Seiten)
   bleibt Sache der Seite und wird über ``onChange`` benachrichtigt. Geladen VOR den Tabellenabschnitten,
   weil deren Skripte die Leiste schon beim Einlesen der Seite anlegen. */
(function () {
  'use strict';

  function create(opts) {
    const root = opts.root;
    const tests = opts.tests || {};
    const chips = new Set();
    const attrs = {};
    let menuOpen = false;

    root.querySelectorAll('[data-filter-group]').forEach((group) => { attrs[group.dataset.filterGroup] = new Set(); });

    // data-f-category -> dataset.fCategory
    const datasetKey = (group) => 'f' + group.charAt(0).toUpperCase() + group.slice(1);

    function matches(row) {
      for (const id of chips) {
        const test = tests[id];
        if (test && !test(row)) return false;
      }
      for (const group of Object.keys(attrs)) {
        if (attrs[group].size && !attrs[group].has(row.dataset[datasetKey(group)] || '')) return false;
      }
      return true;
    }

    function activeCount() {
      return Object.values(attrs).reduce((sum, set) => sum + set.size, 0);
    }

    function changed() {
      render();
      if (opts.onChange) opts.onChange();
    }

    function render() {
      const rows = opts.getRows ? opts.getRows() : [];
      root.querySelectorAll('[data-filter-chip]').forEach((chip) => {
        const id = chip.dataset.filterChip;
        const count = id === 'all' ? rows.length : rows.filter((row) => tests[id] && tests[id](row)).length;
        const n = chip.querySelector('.n');
        if (n) n.textContent = count;
        chip.classList.toggle('active', id === 'all' ? chips.size === 0 : chips.has(id));
      });
      root.querySelectorAll('[data-filter-attr]').forEach((box) => {
        box.checked = attrs[box.dataset.group] ? attrs[box.dataset.group].has(box.value) : false;
      });
      const total = activeCount();
      const menuButton = root.querySelector('[data-filter-menu-btn]');
      if (menuButton) {
        menuButton.classList.toggle('active', total > 0);
        menuButton.setAttribute('aria-expanded', menuOpen ? 'true' : 'false');
        const n = menuButton.querySelector('[data-filter-menu-count]');
        if (n) n.textContent = total ? total : '';
      }
      const pop = root.querySelector('[data-filter-pop]');
      if (pop) pop.hidden = !menuOpen;
      renderTags();
    }

    function renderTags() {
      const box = root.querySelector('[data-filter-tags]');
      if (!box) return;
      box.textContent = '';
      Object.keys(attrs).forEach((group) => {
        const label = root.querySelector('[data-filter-group="' + group + '"]').dataset.label;
        attrs[group].forEach((value) => {
          const tag = document.createElement('span');
          tag.className = 'ha-filter-tag';
          tag.textContent = label + ': ' + value;
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.textContent = '×';
          remove.dataset.filterRemove = group;
          remove.dataset.value = value;
          remove.setAttribute('aria-label', label + ' ' + value);
          tag.appendChild(remove);
          box.appendChild(tag);
        });
      });
      const any = activeCount() > 0 || chips.size > 0;
      if (any && activeCount() > 0) {
        const reset = document.createElement('button');
        reset.type = 'button';
        reset.className = 'ha-filter-reset';
        reset.dataset.filterReset = '';
        const labelHolder = root.dataset.resetLabel ? root : root.querySelector('[data-reset-label]');
        reset.textContent = (labelHolder && labelHolder.dataset.resetLabel) || '×';
        box.appendChild(reset);
      }
      box.hidden = activeCount() === 0;
    }

    root.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-filter-chip]');
      if (chip) {
        const id = chip.dataset.filterChip;
        if (id === 'all') chips.clear();
        else if (chips.has(id)) chips.delete(id);
        else chips.add(id);
        changed();
        return;
      }
      if (event.target.closest('[data-filter-menu-btn]')) {
        menuOpen = !menuOpen;
        render();
        return;
      }
      const remove = event.target.closest('[data-filter-remove]');
      if (remove) {
        attrs[remove.dataset.filterRemove].delete(remove.dataset.value);
        changed();
        return;
      }
      if (event.target.closest('[data-filter-reset]')) {
        Object.values(attrs).forEach((set) => set.clear());
        changed();
      }
    });
    root.addEventListener('change', (event) => {
      const box = event.target.closest('[data-filter-attr]');
      if (!box) return;
      if (box.checked) attrs[box.dataset.group].add(box.value);
      else attrs[box.dataset.group].delete(box.value);
      changed();
    });
    // Ein Klick außerhalb schließt das Menü. Die Sektion wird von htmx komplett ausgetauscht;
    // der Zuhörer am document merkt, wenn seine Leiste nicht mehr im Dokument steht, und geht.
    const closeOutside = (event) => {
      if (!root.isConnected) { document.removeEventListener('click', closeOutside); return; }
      if (menuOpen && !event.target.closest('.ha-filter-menu') && !root.contains(event.target)) {
        menuOpen = false;
        render();
      }
    };
    document.addEventListener('click', closeOutside);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && menuOpen && root.isConnected) { menuOpen = false; render(); }
    });

    return {
      matches,
      render,
      isActive: () => chips.size > 0 || activeCount() > 0,
      state: () => ({ chips: Array.from(chips), attrs: Object.fromEntries(Object.keys(attrs).map((k) => [k, Array.from(attrs[k])])) }),
      restore(saved) {
        if (!saved) return;
        chips.clear();
        (saved.chips || []).forEach((id) => { if (tests[id]) chips.add(id); });
        Object.keys(attrs).forEach((k) => { attrs[k] = new Set((saved.attrs || {})[k] || []); });
      },
    };
  }

  window.importFilter = { create };
})();
