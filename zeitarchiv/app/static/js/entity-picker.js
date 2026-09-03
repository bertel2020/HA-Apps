// Wiederverwendbare Alpine-Komponente für ein durchsuchbares
// Einzel-Entität-Dropdown mit Leeren-Button (Feld-innenliegendes "✕") und
// verzögertem Tooltip (Entity-ID). Gemeinsame Basis für Einstellungen →
// Diagnose → "Entität verfolgen" UND den Tabellen-Editor (Zeilentyp
// "Entität") — beide sahen vorher wie zwei unabhängig gebaute, leicht
// unterschiedliche Varianten desselben Bausteins aus.
//
// Verwendung: x-data="entityPicker(optionsArray, initialEntityId, onSelectFn, allowUnits, denyUnits, requireCounter)"
//   optionsArray:   [{entity_id, label, unit, aggregation_type}, …]
//   initialEntityId: bereits ausgewählte Entity-ID oder ''
//   onSelectFn:     optional — wird mit der neuen Entity-ID aufgerufen
//                   (auch mit '' beim Leeren), für Fälle wie den Tabellen-
//                   Editor, der bei jeder Änderung zusätzlich load() braucht.
//   allowUnits:     optional Array — strikte Positivliste (z. B. ['kWh']),
//                   für Rollen mit klar standardisierter Einheit (Energie-
//                   zähler, Prozent). Eine Entität OHNE erfasste Einheit wird
//                   trotzdem gezeigt (unbekannt ≠ falsch) — sonst würden
//                   Entitäten mit lückenhaften Metadaten grundlos aus der
//                   Auswahl verschwinden. AUSNAHME: aggregation_type
//                   "switch" (An/Aus-Schalter) hat zwar auch keine Einheit,
//                   kann aber nie kWh/% sein — wird immer ausgeschlossen,
//                   sobald allowUnits/denyUnits/requireCounter gesetzt ist
//                   (siehe unitMatches).
//   denyUnits:      optional Array — grobe Negativliste (z. B. offensichtlich
//                   falsche Einheiten wie "kWh"/"%"/"°C" bei einem Preis-
//                   oder CO2-Feld) für Rollen ohne festen Einheiten-Standard
//                   (Preis, CO2-Faktor) — blendet nur eindeutig Falsches aus,
//                   statt (wie allowUnits) auf wenige erlaubte Werte zu
//                   verengen.
//   requireCounter: optional bool — zusätzlich zu allowUnits nur echte
//                   Zähler (aggregation_type "counter") zulassen, für die
//                   Rollen, die einen monoton steigenden Energiezähler
//                   erwarten (Netzbezug/Einspeisung/Erzeuger/Speicher-Laden
//                   &Entladen/Verbraucher) — grenzt z. B. eine
//                   Momentanleistungs- oder Prognose-Entität mit zufällig
//                   passender Einheit aus. Bei Gauge-artigen kWh-Rollen
//                   (Speicher-Kapazität, PV-Prognose) bewusst NICHT gesetzt.
function entityPicker(options, initialEntityId, onSelectFn, allowUnits, denyUnits, requireCounter) {
  return {
    open: false,
    search: '',
    entityId: initialEntityId || '',
    options: options || [],
    allowUnits: allowUnits || null,
    denyUnits: denyUnits || null,
    requireCounter: !!requireCounter,
    hoverTip: null,
    hoverPos: { top: 0, left: 0 },
    _hoverTimer: null,

    label(id) {
      const o = this.options.find((o) => o.entity_id === id);
      return o ? o.label : id;
    },
    unitMatches(o) {
      if (!this.allowUnits && !this.denyUnits && !this.requireCounter) return true;
      // Ein Schalter (An/Aus, z. B. binary_sensor) hat nie eine erfasste
      // Einheit — würde durch die "unbekannt ≠ falsch"-Kulanz unten sonst
      // in JEDER gefilterten Liste auftauchen, kann aber nie zu einer der
      // hier gefilterten Rollen passen (Energiezähler, Prozent, Preis,
      // CO2-Faktor, …).
      if (o.aggregation_type === 'switch') return false;
      if (this.requireCounter && o.aggregation_type && o.aggregation_type !== 'counter') return false;
      if (this.allowUnits && o.unit && !this.allowUnits.includes(o.unit)) return false;
      if (this.denyUnits && o.unit && this.denyUnits.includes(o.unit)) return false;
      return true;
    },
    filtered() {
      const q = this.search.toLowerCase();
      return this.options.filter(
        (o) => this.unitMatches(o) && (!q || (o.label + ' ' + o.entity_id).toLowerCase().includes(q))
      );
    },
    toggleOpen(searchInputEl) {
      this.open = !this.open;
      this.search = '';
      // scrollIntoView zusätzlich zum Fokus: in einem kleinen, selbst
      // scrollbaren Popup (z. B. die Rollen-Kachel-Popups im Energiedash-
      // board-Setup) kann ein Feld nahe am unteren Rand liegen — das
      // aufklappende Popover (absolut positioniert, unterhalb des Feldes)
      // würde sonst über den sichtbaren/scrollbaren Bereich hinausragen und
      // wirkt dann wie ein losgelöstes, "kaputtes" Element statt sichtbar
      // zum Feld zu gehören. block:'nearest' scrollt nur, wenn nötig — auf
      // normal großen Seiten (wo das Popover ohnehin passt) ändert sich
      // nichts.
      if (this.open) this.$nextTick(() => {
        if (!searchInputEl) return;
        searchInputEl.focus();
        const popover = searchInputEl.closest('.dd-picker-popover');
        if (popover) popover.scrollIntoView({block: 'nearest'});
      });
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
    // Verzögerung wie ein natives Tooltip (~600ms) — bei schnellem
    // Drüberfahren beim Scannen der Liste soll nicht jede Zeile aufblitzen.
    scheduleHover(opt, el) {
      this._clearHoverTimer();
      const rect = el.getBoundingClientRect();
      this._hoverTimer = setTimeout(() => {
        this.hoverTip = opt;
        this.hoverPos = { top: rect.top - 6, left: rect.left };
      }, 600);
    },
    hoverSelf(el) {
      if (!this.entityId) return;
      const o = this.options.find((o) => o.entity_id === this.entityId);
      this.scheduleHover(o || { label: this.label(this.entityId), entity_id: this.entityId }, el);
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
