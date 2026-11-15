// Jahre-Auswahl der Langzeit-Reiter (Wetter- und Energiedashboard): „Letzte 5“, „Letzte 10“, „Alle“ oder ein eigener Bereich
// „2019–2023“. Reine Funktionen über Jahreslisten, dazu ein Baustein (mixin) für die Alpine-Komponenten. Die Auswahl ist ein
// Text: 'all', '5', '10' oder 'JJJJ-JJJJ' (ältere gemerkte Zahlen 0, 5, 10 gelten weiter).
(function (root) {
  // Jahr eines Eintrags: eine Zahl, ein Text oder ein Objekt mit „year“.
  const yearOf = (entry) => Number(entry !== null && typeof entry === 'object' ? entry.year : entry);

  function parse(value) {
    const text = value === null || value === undefined ? '' : String(value);
    const span = text.match(/^(\d{4})-(\d{4})$/);
    if (span) {
      const a = Number(span[1]);
      const b = Number(span[2]);
      return {kind: 'range', from: Math.min(a, b), to: Math.max(a, b)};
    }
    return text === '5' || text === '10' ? {kind: 'last', n: Number(text)} : {kind: 'all'};
  }

  // Auswahl gegen die vorhandenen Jahre prüfen: was nichts mehr kürzt, ist „Alle“; ein Bereich wird auf die Jahre begrenzt.
  function normalize(value, years) {
    const list = years.map(Number).filter(Number.isFinite);
    if (!list.length) return parse(value).kind === 'all' ? 'all' : String(value);
    const selection = parse(value);
    const lowest = Math.min(...list);
    const highest = Math.max(...list);
    if (selection.kind === 'last') return selection.n >= list.length ? 'all' : String(selection.n);
    if (selection.kind === 'range') {
      const from = Math.max(selection.from, lowest);
      const to = Math.min(selection.to, highest);
      if (from > to || (from <= lowest && to >= highest)) return 'all';
      return `${from}-${to}`;
    }
    return 'all';
  }

  // Liste filtern. Standard: neuestes Jahr zuerst („Letzte N“ nimmt die ersten N); ascending: ältestes zuerst.
  function filter(list, value, ascending) {
    const selection = parse(value);
    if (selection.kind === 'last') return ascending ? list.slice(-selection.n) : list.slice(0, selection.n);
    if (selection.kind === 'range') return list.filter((entry) => yearOf(entry) >= selection.from && yearOf(entry) <= selection.to);
    return list;
  }

  // Abfrage für den Server: „years=N“ (0 = alle) oder „from=…&to=…“.
  function query(value) {
    const selection = parse(value);
    if (selection.kind === 'range') return `from=${selection.from}&to=${selection.to}`;
    return `years=${selection.kind === 'last' ? selection.n : 0}`;
  }

  // Die festen Knöpfe: „Letzte 5“, „Letzte 10“ (erst ab mehr als zehn Jahren) und „Alle (n)“.
  function options(count) {
    const t = root.t || ((text, vars) => text.replace(/\{(\w+)\}/g, (match, key) => (vars && key in vars ? vars[key] : match)));
    return [
      {value: '5', label: t('Letzte 5')},
      ...(count > 10 ? [{value: '10', label: t('Letzte 10')}] : []),
      {value: 'all', label: t('Alle ({n})', {n: count})},
    ];
  }

  // Bausteine für die Komponenten. Sie brauchen: yearSel (Text), availableYears() (Zahlen, beliebige Reihenfolge) und
  // setYearSel(value) (übernimmt und zeichnet neu).
  const mixin = {
    yearRangeOpen: false,
    draftFrom: '',
    draftTo: '',
    showYearChoice() {
      return this.availableYears().length > 0;
    },
    yearRangeOptions() {
      return options(this.availableYears().length);
    },
    shownYearSel() {
      return normalize(this.yearSel, this.availableYears());
    },
    isCustomYearSel() {
      return parse(this.shownYearSel()).kind === 'range';
    },
    yearSelLabel() {
      const selection = parse(this.shownYearSel());
      return selection.kind === 'range' ? `${selection.from}–${selection.to}` : t('Zeitraum');
    },
    yearRangeChoices() {
      return this.availableYears().map(Number).sort((a, b) => b - a);
    },
    openYearRange() {
      if (this.yearRangeOpen) { this.yearRangeOpen = false; return; }
      const years = this.availableYears().map(Number);
      const selection = parse(this.shownYearSel());
      this.draftFrom = String(selection.kind === 'range' ? selection.from : Math.min(...years));
      this.draftTo = String(selection.kind === 'range' ? selection.to : Math.max(...years));
      this.yearRangeOpen = true;
    },
    applyYearRange() {
      const a = Number(this.draftFrom);
      const b = Number(this.draftTo);
      this.yearRangeOpen = false;
      this.setYearSel(normalize(`${Math.min(a, b)}-${Math.max(a, b)}`, this.availableYears()));
    },
    pickYearSel(value) {
      this.yearRangeOpen = false;
      this.setYearSel(value);
    },
  };

  const api = {parse, normalize, filter, query, options, yearOf, mixin};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.YearRange = api;
})(typeof window !== 'undefined' ? window : globalThis);
