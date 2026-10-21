// Zeitraum-Navigation eines Dashboards (Einstellung "Zeitraum-Navigation" im
// Dashboard-Editor): ein Stichtag für das ganze Dashboard statt einer
// Navigation je Kachel. Jede Kachel zeigt die Periode ihres eigenen Zeitraums,
// die diesen Tag enthält — die Umrechnung macht der Server
// (chart_period.stichtag_window()), hier lebt nur der Tag selbst.
//
// Ein Alpine-Store statt eines x-data-Scopes, weil Leiste und Hinweis an
// verschiedenen Stellen der Seite stehen (_dashboard_period_nav.html).
// dashboard-tiles.js liest den Tag über window.DashboardStichtag und lädt bei
// "dashboard-stichtag" alle bereits gezeichneten Kacheln neu.
//
// Der Tag steht in der URL (?date=JJJJ-MM-TT), damit ein Rückblick sich teilen
// und neu laden lässt. Gespeichert wird er nicht: ohne Parameter zeigt das
// Dashboard immer heute.
(function () {
  const firstDate = document.currentScript.dataset.firstDate || null;
  const pad2 = n => String(n).padStart(2, '0');
  // Reine Kalenderrechnung in UTC: kein Sommerzeit-Versatz kann einen Tag
  // verschlucken. Welcher Tag "heute" ist, entscheidet die Server-Zeitzone.
  const parse = s => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const iso = d => `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
  const daysIn = (y, m) => new Date(Date.UTC(y, m + 1, 0)).getUTCDate();

  function todayStr() {
    const p = window.ServerTime.parts(Date.now() / 1000);
    return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
  }

  // Monat/Jahr bleiben am selben Tag, am Monatsende auf den letzten Tag
  // gekappt (31. März minus ein Monat = 28./29. Februar).
  function shift(s, step, dir) {
    const d = parse(s);
    const y = d.getUTCFullYear(), m = d.getUTCMonth(), day = d.getUTCDate();
    if (step === 'day') return iso(new Date(Date.UTC(y, m, day + dir)));
    if (step === 'week') return iso(new Date(Date.UTC(y, m, day + 7 * dir)));
    if (step === 'month') {
      const t = new Date(Date.UTC(y, m + dir, 1));
      const ty = t.getUTCFullYear(), tm = t.getUTCMonth();
      return iso(new Date(Date.UTC(ty, tm, Math.min(day, daysIn(ty, tm)))));
    }
    return iso(new Date(Date.UTC(y + dir, m, Math.min(day, daysIn(y + dir, m)))));
  }

  // null = heute. Zukunft und "vor dem Archiv" gibt es nicht.
  function clamp(value) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value >= todayStr()) return null;
    if (firstDate && value < firstDate) return firstDate;
    return value;
  }

  const initial = clamp(new URLSearchParams(window.location.search).get('date'));
  window.DashboardStichtag = initial;

  document.addEventListener('alpine:init', () => {
    Alpine.store('dashStichtag', {
      date: initial,
      step: 'day',
      steps: [
        {key: 'day', label: t('Tag')}, {key: 'week', label: t('Woche')},
        {key: 'month', label: t('Monat')}, {key: 'year', label: t('Jahr')},
      ],
      get isToday() { return this.date === null; },
      get current() { return this.date || todayStr(); },
      get label() {
        return new Intl.DateTimeFormat(window.ZA_FORMAT, {
          weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
        }).format(parse(this.current));
      },
      get canGoBack() { return !firstDate || this.current > firstDate; },
      go(dir) { this.set(shift(this.current, this.step, dir)); },
      set(value) {
        const next = clamp(value);
        if (next === this.date) return;
        this.date = next;
        window.DashboardStichtag = next;
        const url = new URL(window.location.href);
        if (next) url.searchParams.set('date', next); else url.searchParams.delete('date');
        window.history.replaceState(null, '', url);
        window.dispatchEvent(new CustomEvent('dashboard-stichtag'));
      },
    });
  });

  // Rückruf des Kalenders (calendar-picker.js ruft jumpToDate global auf).
  window.jumpToDate = dateStr => Alpine.store('dashStichtag').set(dateStr);
})();
