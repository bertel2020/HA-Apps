// Wiederverwendbare Alpine-Komponente für ein durchsuchbares
// Einzel-Entität-Dropdown mit Leeren-Button (Feld-innenliegendes "✕") und
// verzögertem Tooltip (Entity-ID). Gemeinsame Basis für Einstellungen →
// Diagnose → "Entität verfolgen" UND den Tabellen-Editor (Zeilentyp
// "Entität") — beide sahen vorher wie zwei unabhängig gebaute, leicht
// unterschiedliche Varianten desselben Bausteins aus.
//
// Verwendung: x-data="entityPicker(optionsArray, initialEntityId, onSelectFn)"
//   optionsArray:   [{entity_id, label}, …]
//   initialEntityId: bereits ausgewählte Entity-ID oder ''
//   onSelectFn:     optional — wird mit der neuen Entity-ID aufgerufen
//                   (auch mit '' beim Leeren), für Fälle wie den Tabellen-
//                   Editor, der bei jeder Änderung zusätzlich load() braucht.
function entityPicker(options, initialEntityId, onSelectFn) {
  return {
    open: false,
    search: '',
    entityId: initialEntityId || '',
    options: options || [],
    hoverTip: null,
    hoverPos: { top: 0, left: 0 },
    _hoverTimer: null,

    label(id) {
      const o = this.options.find((o) => o.entity_id === id);
      return o ? o.label : id;
    },
    filtered() {
      const q = this.search.toLowerCase();
      return this.options.filter(
        (o) => !q || (o.label + ' ' + o.entity_id).toLowerCase().includes(q)
      );
    },
    toggleOpen(searchInputEl) {
      this.open = !this.open;
      this.search = '';
      if (this.open) this.$nextTick(() => searchInputEl && searchInputEl.focus());
    },
    select(id) {
      this.entityId = id;
      this.open = false;
      this.clearHover();
      if (typeof onSelectFn === 'function') onSelectFn(id);
    },
    clearSelection() {
      this.entityId = '';
      if (typeof onSelectFn === 'function') onSelectFn('');
    },
    // Verzögerung wie ein natives Tooltip (~450ms) — bei schnellem
    // Drüberfahren beim Scannen der Liste soll nicht jede Zeile aufblitzen.
    scheduleHover(opt, el) {
      this._clearHoverTimer();
      const rect = el.getBoundingClientRect();
      this._hoverTimer = setTimeout(() => {
        this.hoverTip = opt;
        this.hoverPos = { top: rect.top - 6, left: rect.left };
      }, 450);
    },
    hoverSelf(el) {
      if (!this.entityId) return;
      this.scheduleHover({ label: this.label(this.entityId), entity_id: this.entityId }, el);
    },
    clearHover() {
      this._clearHoverTimer();
      this.hoverTip = null;
    },
    _clearHoverTimer() {
      if (this._hoverTimer) {
        clearTimeout(this._hoverTimer);
        this._hoverTimer = null;
      }
    },
  };
}
