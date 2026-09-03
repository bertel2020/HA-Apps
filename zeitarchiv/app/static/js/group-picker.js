// Durchsuchbares Dropdown für Verbraucher-Gruppen im Energiedashboard-Setup,
// mit Inline-Neuanlage — Gegenstück zu entity-picker.js, aber ohne feste
// Optionsliste: Gruppen sind Freitext, die geteilte Liste wächst, sobald eine
// neue Gruppe angelegt wird (ähnlich Tags/Labels in Home Assistant: bestehende
// auswählen oder per Freitext eine neue erzeugen).
//
// Verwendung: x-data="groupPicker(groupsRef, initialValue, onSelectFn)"
//   groupsRef:     die REAKTIVE Gruppen-Liste des umgebenden Alpine-Scopes
//                  (z. B. "gruppen" im Root-x-data von
//                  _energiedashboard_setup.html) — bewusst per Referenz statt
//                  Kopie übergeben, damit eine hier neu angelegte Gruppe
//                  sofort auch in jedem anderen Gruppen-Feld UND im
//                  Gruppen-Verwalten-Popup auftaucht, ganz ohne Server-
//                  Rundtrip (erst der finale Formular-Submit persistiert).
//   initialValue:  bereits zugewiesene Gruppe oder ''
//   onSelectFn:    optional — wird mit dem neuen Namen aufgerufen (auch mit
//                  '' beim Leeren). Anders als bei entity-picker.js (wo das
//                  nur ein Zusatzfeld wie den Namen mitpflegt) ist das hier
//                  PFLICHT für korrekte Zähler: das Gruppen-Verwalten-Popup
//                  liest Belegung/Umbenennen/Löschen direkt aus row.gruppe
//                  im Root-Scope — ohne Rückschreiben bliebe das nach einer
//                  Auswahl in DIESEM Formularaufruf veraltet.
function groupPicker(groupsRef, initialValue, onSelectFn) {
  return {
    open: false,
    search: '',
    value: initialValue || '',
    groups: groupsRef,

    filtered() {
      const q = this.search.trim().toLowerCase();
      if (!q) return this.groups;
      return this.groups.filter((g) => g.toLowerCase().includes(q));
    },
    exactMatch() {
      const q = this.search.trim().toLowerCase();
      return this.groups.some((g) => g.toLowerCase() === q);
    },
    toggleOpen(searchInputEl) {
      this.open = !this.open;
      this.search = '';
      // scrollIntoView zusätzlich zum Fokus — dasselbe Problem wie beim
      // Entitäts-Picker (siehe entity-picker.js): in einem kleinen, selbst
      // scrollbaren Popup kann das Feld nahe am unteren Rand liegen, das
      // aufklappende Popover würde sonst über den sichtbaren Bereich
      // hinausragen. block:'nearest' scrollt nur, wenn nötig.
      if (this.open) this.$nextTick(() => {
        if (!searchInputEl) return;
        searchInputEl.focus();
        const popover = searchInputEl.closest('.dd-picker-popover');
        if (popover) popover.scrollIntoView({block: 'nearest'});
      });
    },
    select(name) {
      this.value = name;
      this.open = false;
      if (typeof onSelectFn === 'function') onSelectFn(name);
    },
    createAndSelect() {
      const name = this.search.trim();
      if (!name) return;
      if (!this.groups.includes(name)) this.groups.push(name);
      this.select(name);
    },
    clearSelection() {
      this.value = '';
      if (typeof onSelectFn === 'function') onSelectFn('');
    },
  };
}
