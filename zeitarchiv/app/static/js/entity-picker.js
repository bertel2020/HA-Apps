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

// Mehrfach-Variante für den Chart-Editor: Suchfeld mit aufklappender
// Trefferliste statt eines Rasters aus Kästchen für ALLE Entitäten — das
// wurde mit jeder erfassten Entität länger und zeigte Namen wie IDs in den
// schmalen Spalten nur noch angeschnitten. Gewählt wird hier nur; Anzeige-
// name, Reihenfolge und Entfernen stehen in der Liste "Ausgewählt" darunter
// (chart_editor.html), nicht in dieser Komponente.
//
// Verwendung: x-data="entityMultiPicker(optionsArray, getSelectedFn, onAddFn)"
//   optionsArray:  [{entity_id, label, unit, aggregation_type, is_favorite}, …]
//   getSelectedFn: liefert die aktuell gewählten IDs — als Funktion statt
//                  Array, weil die Auswahl in der umgebenden Komponente lebt
//                  und sich ändert (Entfernen per ✕, Soll-Feld).
//   onAddFn:       wird mit der hinzugefügten Entity-ID aufgerufen.
//
// Gewählte Entitäten fehlen in den Treffern. Gruppiert wird so: Favoriten
// zuerst; ist schon etwas gewählt, danach "Passt zu vorhandener Achse"
// (gleiche Einheit wie eine gewählte Entität) vor "Neue Y-Achse" — ein
// Hinweis, kein Filter, wer bewusst W und kWh mischen will, findet beides.
// Mit aktivem Einheiten-Filter entfällt diese Teilung, sie unterschiede
// dann nichts mehr.
const ENTITY_MULTI_PICKER_UNIT_CHIPS = 5;
function entityMultiPicker(options, getSelectedFn, onAddFn) {
  return {
    open: false,
    search: '',
    activeId: '',
    typeFilter: '',
    unitFilter: '',
    allUnits: false,
    options: options || [],

    selected() { return getSelectedFn() || []; },
    // Vor Typ-/Einheiten-Filter: Basis der Zähler an den Chips, damit ein
    // Chip zeigt, wie viele Treffer er zum aktuellen Suchtext liefern würde.
    available() {
      const selected = new Set(this.selected());
      const q = this.search.trim().toLowerCase();
      return this.options.filter(
        (o) => !selected.has(o.entity_id) && (!q || (o.label + ' ' + o.entity_id).toLowerCase().includes(q))
      );
    },
    // Nur Typen, die es überhaupt gibt — ein "Zustand"-Chip ohne eine
    // einzige Zustands-Entität wäre ein toter Knopf.
    typeChips() {
      const base = this.available().filter((o) => !this.unitFilter || o.unit === this.unitFilter);
      const present = new Set(this.options.map((o) => o.aggregation_type));
      return [
        ['standard', t('Standard')], ['counter', t('Zähler')],
        ['switch', t('Schalter')], ['state', t('Zustand')],
      ].filter(([key]) => present.has(key)).map(([key, label]) => ({
        key, label, count: base.filter((o) => o.aggregation_type === key).length,
      }));
    },
    // Reihenfolge nach Häufigkeit über ALLE Entitäten, nicht über die
    // aktuellen Treffer — sonst springen die Chips bei jedem Tastendruck.
    // Nur die häufigsten stehen da, der Rest hinter "mehr …"; eine gewählte
    // Einheit bleibt sichtbar, auch wenn sie nicht dazugehört.
    allUnitsSorted() {
      const total = {};
      this.options.forEach((o) => { if (o.unit) total[o.unit] = (total[o.unit] || 0) + 1; });
      return Object.keys(total).sort((a, b) => total[b] - total[a] || a.localeCompare(b));
    },
    unitChips() {
      const base = this.available().filter((o) => !this.typeFilter || o.aggregation_type === this.typeFilter);
      const counts = {};
      base.forEach((o) => { if (o.unit) counts[o.unit] = (counts[o.unit] || 0) + 1; });
      let units = this.allUnitsSorted();
      if (!this.allUnits) {
        const top = units.slice(0, ENTITY_MULTI_PICKER_UNIT_CHIPS);
        if (this.unitFilter && !top.includes(this.unitFilter)) top.push(this.unitFilter);
        units = top;
      }
      return units.map((unit) => ({ unit, count: counts[unit] || 0 }));
    },
    hiddenUnitCount() {
      return Math.max(0, this.allUnitsSorted().length - ENTITY_MULTI_PICKER_UNIT_CHIPS);
    },
    hits() {
      return this.available().filter(
        (o) => (!this.typeFilter || o.aggregation_type === this.typeFilter)
          && (!this.unitFilter || o.unit === this.unitFilter)
      );
    },
    // Flache Liste aus Gruppenüberschriften und Treffern, damit das Template
    // mit einem einzigen x-for auskommt.
    rows() {
      const hits = this.hits();
      const favorites = hits.filter((o) => o.is_favorite);
      const rest = hits.filter((o) => !o.is_favorite);
      const units = [...new Set(this.selected().map((id) => {
        const o = this.options.find((x) => x.entity_id === id);
        return o ? o.unit : '';
      }).filter(Boolean))];
      let groups;
      if (units.length && !this.unitFilter) {
        groups = [
          [t('Favoriten'), favorites],
          [t('Passt zu vorhandener Achse ({units})', { units: units.join(', ') }), rest.filter((o) => units.includes(o.unit))],
          [t('Neue Y-Achse'), rest.filter((o) => !units.includes(o.unit))],
        ];
      } else {
        const filtered = this.typeFilter || this.unitFilter || this.search.trim();
        groups = [[t('Favoriten'), favorites], [filtered ? t('Treffer') : t('Alle Entitäten'), rest]];
      }
      const out = [];
      groups.forEach(([title, items]) => {
        if (!items.length) return;
        out.push({ key: 'h:' + title, header: title });
        items.forEach((o) => out.push({ key: o.entity_id, opt: o }));
      });
      return out;
    },
    typeLabel(key) {
      const chip = this.typeChips().find((c) => c.key === key);
      return chip ? chip.label : '';
    },
    toggleType(key) { this.typeFilter = this.typeFilter === key ? '' : key; this.activeId = ''; },
    toggleUnit(unit) { this.unitFilter = this.unitFilter === unit ? '' : unit; this.activeId = ''; },
    // Die Liste bleibt offen und der Filter stehen, damit sich mehrere
    // Entitäten nacheinander aus derselben Auswahl übernehmen lassen.
    add(id) {
      this.search = '';
      this.activeId = '';
      if (typeof onAddFn === 'function') onAddFn(id);
    },
    onKey(event) {
      if (event.key === 'Escape') { this.open = false; return; }
      if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
      event.preventDefault();
      this.open = true;
      const ids = this.rows().filter((r) => r.opt).map((r) => r.opt.entity_id);
      if (!ids.length) return;
      const idx = ids.indexOf(this.activeId);
      if (event.key === 'Enter') {
        // Ohne Markierung übernimmt Enter den ersten Treffer — bei einem
        // eindeutigen Suchtext der schnellste Weg.
        this.add(idx === -1 ? ids[0] : ids[idx]);
        return;
      }
      const next = event.key === 'ArrowDown'
        ? Math.min(idx + 1, ids.length - 1)
        : Math.max(idx - 1, 0);
      this.activeId = ids[next];
      this.$nextTick(() => {
        const row = this.$root.querySelector(`[data-entity-id="${CSS.escape(this.activeId)}"]`);
        if (row) row.scrollIntoView({ block: 'nearest' });
      });
    },
  };
}
