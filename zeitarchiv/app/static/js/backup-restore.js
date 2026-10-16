/* Restore-Assistent (backup_restore.html): Schritte, Auswahl-Zähler, Filter und die
   automatische Abhängigkeits-Markierung bei Dashboards. Alle Texte kommen vom Server. */
(function () {
  const root = document.querySelector('.rw');
  const form = document.getElementById('rw');
  if (!root || !form) return;
  const base = root.dataset.restoreUrl;
  const $ = (sel, scope) => (scope || document).querySelector(sel);
  const $$ = (sel, scope) => Array.from((scope || document).querySelectorAll(sel));
  const next = $('#rw-next'), back = $('#rw-back'), apply = $('#rw-apply'), done = $('#rw-done'), cancel = $('#rw-cancel');
  const info = $('#rw-info');
  let step = 1;
  let loadedMode = '';

  const mode = () => (form.querySelector('input[name=mode]:checked') || {}).value || '';

  function show(n) {
    step = n;
    $$('[data-panel]').forEach((p) => { p.hidden = Number(p.dataset.panel) !== n; });
    const skip2 = mode() === 'all';
    $$('#rw-steps li').forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle('is-done', s < n && !(skip2 && s === 2));
      li.classList.toggle('is-current', s === n);
      li.classList.toggle('is-skipped', skip2 && s === 2);
      if (s === n) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
    });
    back.hidden = !(n === 2 || n === 3);
    next.hidden = n >= 3;
    apply.hidden = n !== 3;
    done.hidden = n !== 4;
    cancel.hidden = n === 4;
    info.textContent = '';
    refresh();
    window.scrollTo({ top: 0 });
  }

  function refresh() {
    if (step === 1) next.disabled = !mode();
    if (step === 2) {
      const total = options.total();
      next.disabled = total === 0;
    }
    if (step === 3) {
      const box = $('#rw-confirm');
      const review = $('.rw-review', $('#rw-review'));
      apply.disabled = !(box && box.checked) || !review;
      if (review && review.dataset.applyLabel) apply.textContent = review.dataset.applyLabel;
      apply.classList.toggle('danger', !!review);
    }
  }

  // ------------------------------------------------------------------ Optionen
  const options = {
    total() {
      const sel = $('#rw-options .rw-selbar [data-count]');
      return sel ? Number(sel.textContent) : 0;
    },
    setCount(n) {
      const el = $('#rw-options .rw-selbar [data-count]');
      if (el) el.textContent = String(n);
      refresh();
    },
    initEntities(scope) {
      const rows = $$('tbody tr', scope);
      const all = $('.rw-all', scope);
      const search = $('.rw-search', scope);
      const pickAll = $('.rw-pick-all', scope);
      const clear = $('.rw-clear', scope);
      const pager = $('.rw-pager', scope);
      const sizeSel = $('.rw-size', pager);
      const pageInput = $('.pager-page-input', pager);
      const empty = $('.rw-empty', scope);
      const box = (r) => $('input[name=ent]', r);
      const st = { page: 1, size: Number(sizeSel.value), filter: 'all' };
      let matches = [];
      let pageRows = [];

      const count = () => options.setCount(rows.filter((r) => box(r).checked).length);
      const syncBar = () => {
        all.checked = pageRows.length > 0 && pageRows.every((r) => box(r).checked);
        const open = matches.filter((r) => !box(r).checked).length;
        pickAll.hidden = !(matches.length > pageRows.length && open > 0);
        pickAll.textContent = pickAll.dataset.tpl.replace('{n}', String(matches.length));
        clear.hidden = !rows.some((r) => box(r).checked);
      };
      const render = () => {
        const q = (search.value || '').trim().toLowerCase();
        matches = rows.filter((r) => (st.filter === 'all' || r.dataset.st === st.filter) && (!q || r.dataset.text.includes(q)));
        const pages = Math.max(1, Math.ceil(matches.length / st.size));
        st.page = Math.min(Math.max(1, st.page), pages);
        const from = (st.page - 1) * st.size;
        pageRows = matches.slice(from, from + st.size);
        const shown = new Set(pageRows);
        rows.forEach((r) => { r.hidden = !shown.has(r); });
        empty.hidden = matches.length > 0;
        $('[data-range]', pager).textContent = matches.length
          ? pager.dataset.rangeTpl.replace('{start}', String(from + 1)).replace('{end}', String(from + pageRows.length)).replace('{total}', String(matches.length))
          : '';
        $('[data-pages]', pager).textContent = String(pages);
        pageInput.value = String(st.page);
        pageInput.max = String(pages);
        $$('[data-go]', pager).forEach((b) => {
          const first = b.dataset.go === 'first' || b.dataset.go === 'prev';
          b.disabled = first ? st.page <= 1 : st.page >= pages;
        });
        syncBar();
      };
      const goto = (n) => { st.page = n; render(); };

      search.addEventListener('input', () => goto(1));
      sizeSel.addEventListener('change', () => { st.size = Number(sizeSel.value); goto(1); });
      pageInput.addEventListener('change', () => goto(parseInt(pageInput.value, 10) || 1));
      $$('[data-go]', pager).forEach((b) => b.addEventListener('click', () => {
        const to = { first: 1, prev: st.page - 1, next: st.page + 1, last: Infinity }[b.dataset.go];
        goto(to);
      }));
      all.addEventListener('change', () => {
        pageRows.forEach((r) => { box(r).checked = all.checked; });
        count();
        syncBar();
      });
      pickAll.addEventListener('click', () => {
        matches.forEach((r) => { box(r).checked = true; });
        count();
        syncBar();
      });
      clear.addEventListener('click', () => {
        rows.forEach((r) => { box(r).checked = false; });
        count();
        syncBar();
      });
      scope.addEventListener('change', (e) => { if (e.target.name === 'ent') { count(); syncBar(); } });
      $$('.rw-chip', scope).forEach((chip) => chip.addEventListener('click', () => {
        $$('.rw-chip', scope).forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
        st.filter = chip.dataset.filter;
        goto(1);
      }));
      count();
      render();
    },
    initSettings(scope) {
      const boxes = () => $$('input[type=checkbox][data-key]', scope);
      const panel = (kind) => $('[data-panel-kind="' + kind + '"]', scope);
      const tabs = $$('.rw-tab', scope);
      const showTab = (kind) => {
        tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === kind)));
        $$('.rw-subpanel', scope).forEach((p) => { p.hidden = p.dataset.panelKind !== kind; });
      };
      tabs.forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));
      showTab('d');

      function recompute() {
        // Abhängigkeiten: ausgewählte Dashboards ziehen ihre Charts/Tabellen mit.
        const needed = new Set();
        boxes().forEach((b) => {
          if (b.dataset.key.startsWith('d:') && b.checked) (b.dataset.needs || '').split(' ').filter(Boolean).forEach((k) => needed.add(k));
        });
        boxes().forEach((b) => {
          const k = b.dataset.key;
          if (!(k.startsWith('c:') || k.startsWith('t:'))) return;
          const row = b.closest('tr');
          const auto = needed.has(k) && b.dataset.manual !== '1' && row.dataset.st !== 'same';
          if (auto) { b.checked = true; b.disabled = true; }
          else if (b.dataset.auto === '1') { b.checked = false; b.disabled = row.dataset.st === 'same'; }
          b.dataset.auto = auto ? '1' : '0';
          row.classList.toggle('is-auto', auto);
          const note = $('.rw-act-auto', row);
          if (note) note.hidden = !auto;
          $$('.rw-act-plain', row).forEach((el) => { el.hidden = auto; });
        });
        const counts = { d: 0, c: 0, t: 0, e: 0, g: 0 };
        boxes().forEach((b) => {
          if (!b.checked) return;
          const kind = b.dataset.key[0];
          if (kind in counts) counts[kind] += 1;
          else if (kind === 'x') counts.g += 1;
        });
        tabs.forEach((t) => {
          const n = counts[t.dataset.tab] || 0;
          const badge = $('.rw-n', t);
          badge.textContent = String(n);
          badge.classList.toggle('is-on', n > 0);
        });
        options.setCount(Object.values(counts).reduce((a, b) => a + b, 0));
      }

      scope.addEventListener('change', (e) => {
        const b = e.target;
        if (b.matches && b.matches('input[type=checkbox][data-key]')) {
          b.dataset.manual = b.checked ? '1' : '0';
          recompute();
        }
      });
      $$('.rw-subpanel', scope).forEach((p) => {
        $$('.rw-chip', p).forEach((chip) => chip.addEventListener('click', () => {
          $$('.rw-chip', p).forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
          p.dataset.filter = chip.dataset.filter;
        }));
        const bulk = $('.rw-bulk', p);
        if (bulk) bulk.addEventListener('click', () => {
          $$('input[type=checkbox][data-key]', p).forEach((b) => {
            if (b.closest('tr').dataset.st !== 'same' && !b.disabled) { b.checked = true; b.dataset.manual = '1'; }
          });
          recompute();
        });
      });
      recompute();
    },
  };

  // ------------------------------------------------------------------ Navigation
  function request(verb, path, target) {
    return htmx.ajax(verb.toUpperCase(), base + path, { source: form, target: target, swap: 'innerHTML' });
  }

  function loadReview() {
    return request('post', '/review', '#rw-review').then(() => show(3));
  }

  next.addEventListener('click', () => {
    if (step === 1) {
      if (mode() === 'all') return loadReview();
      if (loadedMode === mode()) { show(2); return undefined; }
      next.disabled = true;
      htmx.ajax('GET', base + '/options?mode=' + encodeURIComponent(mode()), { target: '#rw-options', swap: 'innerHTML' }).then(() => {
        loadedMode = mode();
        const scope = $('#rw-options .rw-options');
        if (scope && scope.dataset.kind === 'entities' && $('.rw-all', scope)) options.initEntities(scope);
        else if (scope && scope.dataset.kind === 'settings') options.initSettings(scope);
        show(2);
      });
      return undefined;
    }
    if (step === 2) return loadReview();
    return undefined;
  });
  back.addEventListener('click', () => show(step === 3 && mode() === 'all' ? 1 : step - 1));
  apply.addEventListener('click', () => {
    apply.disabled = true;
    request('post', '/apply', '#rw-result').then(() => show(4));
  });
  form.addEventListener('change', (e) => {
    if (e.target.name === 'mode') refresh();
    if (e.target.id === 'rw-confirm') refresh();
  });
  show(1);
})();
