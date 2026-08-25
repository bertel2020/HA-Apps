// Kalender-Widget für den Datums-Sprung — von der Bereinigungs-Seite
// (cleanup.html) und der Entität-eigenen Chart-Seite (entity_detail.html)
// gemeinsam genutzt. Grenzt die Jahr-/Monatsauswahl auf den Zeitraum ein, in
// dem die Entität überhaupt Daten hat (first/last), und fragt für den jeweils
// gewählten Monat zusätzlich /data-days ab, um auch einzelne Tage MIT Lücke
// innerhalb dieses Bereichs auszugrauen — das konnte ein natives
// <input type="date"> nicht (siehe Konzept, "Offene Punkte", jetzt
// geschlossen).
//
// Jahr/Monat sind zwei <select>-Dropdowns statt ‹/›-Einzelschritten: bei
// einer Historie über mehrere Jahre (real: 2014–heute) wäre ein Sprung z. B.
// von "heute" zu "März 2015" über Monat-für-Monat-Klicks schlicht unbenutzbar
// (>100 Klicks) — mit den Dropdowns sind es zwei Klicks, unabhängig davon,
// wie lang die Historie ist.
//
// Ruft bei einem gültigen Klick die globale Funktion jumpToDate(dateStr) auf
// — jede einbindende Seite definiert diese selbst, weil "zu einem Datum
// springen" pro Seite unterschiedlich in Zustand übersetzt wird (cleanup.html:
// ein Tage-Block-Offset, entity_detail.html: ein Perioden-Offset je nach
// gewähltem Zeitraum).
function calendarPicker(entityId, base, firstDate, lastDate) {
  const MONTH_NAMES = [
    'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
  ];
  const pad2 = n => String(n).padStart(2, '0');
  const parseYm = s => { const [y, m] = s.split('-').map(Number); return {y, m}; };
  const firstYm = firstDate ? parseYm(firstDate) : null;
  const lastYm = lastDate ? parseYm(lastDate) : null;

  return {
    open: false,
    loading: false,
    year: 0,
    month: 0,  // 1-12
    dataDays: [],  // Tage des gewählten Monats mit mindestens einem Rohwert
    _loadSeq: 0,  // gegen ein Wettrennen bei schnell hintereinander gewähltem Jahr/Monat (siehe loadMonth)

    init() {
      const ref = lastYm || { y: new Date().getFullYear(), m: new Date().getMonth() + 1 };
      this.year = ref.y;
      this.month = ref.m;
      window.addEventListener('resize', () => { if (this.open) this.reposition(); });
    },
    // Wie viel vom Kalender überhaupt sinnvoll ist, hängt vom gewählten
    // Zeitraum der einbindenden Seite ab (entity_detail.html: "range" auf
    // entityChart(), eine Ebene weiter oben im DOM) — ein einzelner Tag ergibt
    // bei Zeitraum "Jahr" keinen Sinn, ein Sprung dorthin soll direkt einen
    // Monat bzw. ein Jahr treffen, nicht erst einen beliebigen Tag darin.
    // Über Alpine.$data statt eines Props gelesen, weil calendarPicker() sonst
    // bei jeder Range-Änderung neu instanziiert werden müsste (eigener
    // x-data-Aufruf, der nur einmal beim Laden ausgewertet wird) — cleanup.html
    // hat gar kein "range" (dort immer der volle Tage-Picker, siehe Fallback
    // 'day' unten).
    get pickerMode() {
      const pageRoot = document.querySelector('.page');
      const parentData = pageRoot ? Alpine.$data(pageRoot) : null;
      const range = parentData ? parentData.range : null;
      if (range === 'month') return 'month';
      if (range === 'year') return 'year';
      // "decade": nur auf entity_detail.html möglich. "all" ("Gesamt"): nur
      // auf cleanup.html — beide haben keinen sinnvollen Kalender-Sprung (kein
      // fester Perioden-Anker), der auslösende Button bleibt dafür deaktiviert.
      if (range === 'decade' || range === 'all') return 'none';
      return 'day';
    },
    // Bei jedem Öffnen auf den gerade angezeigten Zeitraum springen, statt die
    // Auswahl vom letzten Öffnen (oder init()s Fallback auf lastYm/heute)
    // stehen zu lassen — sonst zeigt der Kalender z. B. weiter "Januar 2025",
    // obwohl längst auf "Juni 2026" navigiert wurde. windowStart lebt auf
    // beiden Seiten im .page-Scope: entity_detail.html hält es direkt in
    // seinem Alpine-Zustand nach, cleanup.html (kein reaktiver Fetch, sondern
    // ein Formular-Roundtrip über htmx) schreibt es nach jedem Swap aus dem
    // servergerechneten Fenster hinein (siehe _rows_table.html). Fehlt es
    // (z. B. vor dem ersten Laden), bleibt currentPeriodYm null und init()s
    // Fallback greift unverändert.
    get currentPeriodYm() {
      const pageRoot = document.querySelector('.page');
      const parentData = pageRoot ? Alpine.$data(pageRoot) : null;
      const windowStart = parentData ? parentData.windowStart : null;
      if (windowStart == null) return null;
      const d = new Date(windowStart * 1000);
      return { y: d.getFullYear(), m: d.getMonth() + 1 };
    },
    toggle() {
      this.open = !this.open;
      if (this.open) {
        const cur = this.currentPeriodYm;
        if (cur) { this.year = cur.y; this.month = cur.m; }
        if (this.pickerMode === 'day') this.loadMonth();
        this.$nextTick(() => this.reposition());
      }
    },
    // Die feste CSS-Positionierung (zentriert bzw. linksbündig zum Anker,
    // siehe entity_detail.html/cleanup.html) ragt bei einem Anker nahe am
    // Viewport-Rand (v. a. schmale/mobile Fenster) über den Rand hinaus —
    // <html> hat overflow-x:hidden (app.css, wegen position:sticky), das
    // schneidet den überstehenden Teil dann nicht nur optisch ab, sondern
    // macht ihn wirklich unklickbar (z. B. die ganze Montag-Spalte). Deshalb
    // hier statt reiner CSS-Zentrierung eine an den Viewport geklemmte
    // Position berechnen, sobald das Popover öffnet.
    reposition() {
      const popEl = this.$refs.popover;
      if (!popEl) return;
      const wrapRect = popEl.parentElement.getBoundingClientRect();
      const margin = 8;
      let left = wrapRect.left + wrapRect.width / 2 - popEl.offsetWidth / 2;
      left = Math.max(margin, Math.min(left, window.innerWidth - popEl.offsetWidth - margin));
      // left ist bislang eine Fenster-Koordinate — zurückrechnen auf die
      // Position relativ zum (position:relative) Anker, da das Popover
      // selbst position:absolute innerhalb von .cal-wrap ist.
      popEl.style.left = `${left - wrapRect.left}px`;
      popEl.style.transform = 'none';
    },
    async loadMonth() {
      // Eine zweite Auswahl vor Antwort der ersten darf deren (jetzt
      // veraltete) Antwort nicht mehr übernehmen dürfen — sonst kann eine
      // spät eintreffende alte Antwort dataDays für den FALSCHEN, längst
      // verlassenen Monat überschreiben. requestId markiert, welcher Aufruf
      // gerade der aktuellste ist; nur dessen Ergebnis wird übernommen.
      const requestId = ++this._loadSeq;
      this.loading = true;
      try {
        const res = await fetch(`${base}/entities/${entityId}/data-days?year=${this.year}&month=${this.month}`);
        const data = await res.json();
        if (requestId !== this._loadSeq) return;
        this.dataDays = data.days || [];
      } catch (e) {
        if (requestId === this._loadSeq) this.dataDays = [];
      } finally {
        if (requestId === this._loadSeq) this.loading = false;
      }
    },
    // Grenzen für die ‹/›-Monatspfeile — dieselbe Jahresspanne wie yearOptions,
    // nur zusätzlich auf Monatsebene, damit sich nicht über first/lastDate
    // hinaus navigieren lässt (die Jahres-Auswahl deckelt das nur pro Jahr,
    // nicht pro Monat).
    get canGoPrevMonth() {
      if (!firstYm) return true;
      return this.year > firstYm.y || (this.year === firstYm.y && this.month > firstYm.m);
    },
    get canGoNextMonth() {
      if (!lastYm) return true;
      return this.year < lastYm.y || (this.year === lastYm.y && this.month < lastYm.m);
    },
    // Im Tage-Picker (pickerMode "day") navigieren die Pfeile nur den
    // angezeigten Monat, ohne selbst schon zu springen — erst ein Klick auf
    // einen Tag löst jumpToDate aus. Im reduzierten Monats-Picker gibt es
    // diesen zweiten Schritt nicht (kein Tage-Raster mehr, das die Auswahl
    // bestätigen könnte) — hier springen die Pfeile deshalb direkt.
    prevMonth() {
      if (!this.canGoPrevMonth) return;
      this.month -= 1;
      if (this.month < 1) { this.month = 12; this.year -= 1; }
      if (this.pickerMode === 'month') this.pickMonth(); else this.loadMonth();
    },
    nextMonth() {
      if (!this.canGoNextMonth) return;
      this.month += 1;
      if (this.month > 12) { this.month = 1; this.year += 1; }
      if (this.pickerMode === 'month') this.pickMonth(); else this.loadMonth();
    },
    // Jahre zwischen erstem und letztem Wert der Entität — ohne first/last
    // (sollte praktisch nie vorkommen, eine Entität ohne jeden Wert hat auch
    // keinen sinnvollen Kalender) zur Sicherheit nur das aktuelle Jahr.
    get yearOptions() {
      const from = firstYm ? firstYm.y : this.year;
      const to = lastYm ? lastYm.y : this.year;
      const years = [];
      for (let y = to; y >= from; y--) years.push(y);
      return years;
    },
    get monthNames() {
      return MONTH_NAMES;
    },
    get days() {
      const daysInMonth = new Date(this.year, this.month, 0).getDate();
      const firstWeekday = (new Date(this.year, this.month - 1, 1).getDay() + 6) % 7;  // Montag = 0
      const cells = new Array(firstWeekday).fill(null);
      for (let d = 1; d <= daysInMonth; d++) {
        const dateStr = `${this.year}-${pad2(this.month)}-${pad2(d)}`;
        const inRange = (!firstDate || dateStr >= firstDate) && (!lastDate || dateStr <= lastDate);
        cells.push({ day: d, dateStr, inRange, hasData: this.dataDays.includes(d) });
      }
      return cells;
    },
    pick(cell) {
      if (!cell || !cell.inRange || !cell.hasData) return;
      jumpToDate(cell.dateStr);
      this.open = false;
    },
    // pickMonth/pickYear sind das Gegenstück zu pick() für die reduzierten
    // Picker (siehe pickerMode) — der Tag selbst ist dort irrelevant,
    // jumpToDate() liest je nach range() ohnehin nur Jahr bzw. Jahr+Monat aus
    // dem übergebenen Datum, der 1. reicht als Platzhalter.
    pickMonth() {
      jumpToDate(`${this.year}-${pad2(this.month)}-01`);
      this.open = false;
    },
    pickYear() {
      jumpToDate(`${this.year}-01-01`);
      this.open = false;
    },
    onMonthChange(value) {
      this.month = parseInt(value, 10);
      if (this.pickerMode === 'month') this.pickMonth(); else this.loadMonth();
    },
    onYearChange(value) {
      this.year = parseInt(value, 10);
      if (this.pickerMode === 'year') this.pickYear();
      else if (this.pickerMode === 'month') this.pickMonth();
      else this.loadMonth();
    },
    async goToday() {
      const today = new Date();
      const y = today.getFullYear();
      const m = today.getMonth() + 1;
      const d = today.getDate();
      if (y !== this.year || m !== this.month) {
        this.year = y;
        this.month = m;
        await this.loadMonth();
      }
      const todayStr = `${y}-${pad2(m)}-${pad2(d)}`;
      const inRange = (!firstDate || todayStr >= firstDate) && (!lastDate || todayStr <= lastDate);
      if (inRange && this.dataDays.includes(d)) {
        jumpToDate(todayStr);
        this.open = false;
      }
      // Kein Datenpunkt heute: Kalender bleibt offen und zeigt zumindest
      // den aktuellen Monat, statt stumm nichts zu tun.
    },
  };
}
