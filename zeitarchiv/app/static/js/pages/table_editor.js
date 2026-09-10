    const DEFAULT_STYLE = {
      zebra: false, borders: 'horizontal', density: 'comfortable', header_accent: false,
      first_col_accent: false, first_col_bold: false,
      sticky_first_col: false, sticky_header: false, comparison_columns: false,
      show_deviation: false, explicit_missing: false, show_units: true, align_units: false,
      small_units: false, align_numbers: false, label_col_width: null,
      header_align: 'right', value_align: 'right', equal_value_cols: false,
    };
    const ENTITY_LABEL_BY_ID = Object.fromEntries(ENTITY_OPTIONS.map(o => [o.entity_id, o.label]));

    let _uidSeq = 0;
    function uid() { return `u${++_uidSeq}`; }

    // Namensvorschläge für Spalten (Zeitraum-Typ + Versatz) — dieselbe
    // Wortwahl wie compareLabel/comparePreviousLabel auf der Entität-eigenen
    // Chart-Seite (entity_detail.html), hier nur als Platzhalter/Default
    // statt als Button-Beschriftung. Werden nur verwendet, wenn die Spalte
    // keine eigene Beschriftung hat (Platzhalter im Feld UND Fallback beim
    // Speichern).
    // Dieselben Bezeichnungen wie das entity-eigene "Nachkommastellen"-Feld
    // (formatting.DECIMALS_LABELS, siehe _entity_config_form.html) — hier nur
    // als JS-Konstante dupliziert, weil die Optionsliste (5 feste Werte) sich
    // praktisch nie ändert und die anderen Picker in dieser Datei (BORDER_
    // OPTIONS, DENSITY_OPTIONS, …) genauso lokal statt aus Jinja befüllt sind.
    const DECIMALS_LABELS = {auto: 'Automatisch', '0': '0 Nachkommastellen', '1': '1 Nachkommastelle', '2': '2 Nachkommastellen', '3': '3 Nachkommastellen'};
    const RANGE_LABELS = {hour: 'Stunde', day: 'Tag', week: 'Woche', month: 'Monat', year: 'Jahr', decade: 'Dekade'};
    const PREVIOUS_LABELS = {hour: 'Vorherige Stunde', day: 'Vortag', week: 'Vorwoche', month: 'Vormonat', year: 'Vorjahr', decade: 'Vorherige Dekade'};
    const PLURAL_UNITS = {hour: 'Stunden', day: 'Tagen', week: 'Wochen', month: 'Monaten', year: 'Jahren', decade: 'Dekaden'};
    // Einheit für die Versatz-Feldbeschriftung ("Versatz (Wochen)") — Plural
    // ohne Dativ-"n", anders als PLURAL_UNITS oben (für Fließtext wie
    // "Vor 3 Monaten" gedacht). Statisch je Zeitraum-Typ, unabhängig vom
    // aktuellen Zahlenwert — deshalb an der Feldbeschriftung statt im Feld
    // selbst, das bleibt dadurch ein normales, unverändertes Zahlenfeld
    // (Spinner-Pfeile bleiben rechtsbündig wie bei jedem anderen Zahlenfeld).
    const UNIT_PLURAL_SIMPLE = {hour: 'Stunden', day: 'Tage', week: 'Wochen', month: 'Monate', year: 'Jahre', decade: 'Dekaden'};
    // Dieselbe Wortwahl wie previousYearPeriodLabel() in entity_detail.html
    // (Chart-Vorjahresvergleich) — hier als Vorschlag statt Umschalt-Label,
    // damit "Tag, Versatz 0, Vorjahresvergleich" ohne eigene Beschriftung
    // "Vorjahrestag" statt nur "Tag" vorschlägt.
    const YEAR_OVER_YEAR_LABELS = {hour: 'Vorjahresstunde', day: 'Vorjahrestag', week: 'Vorjahreswoche', month: 'Vorjahresmonat', year: 'Vorjahr', decade: 'Vorjahresdekade'};
    // Einfügehilfe für Beschriftungs-Platzhalter (siehe TableCompute.resolveLabel) —
    // Token + Anzeigename kommen aus table-compute.js, damit Editor und die
    // Ersetzungs-Logik nie auseinanderlaufen können.
    const LABEL_VARIABLES = TableCompute.LABEL_VARIABLES;

    function suggestColumnLabel(col) {
      const offset = col.offset || 0;
      if (col.year_over_year) {
        if (offset === 0) return YEAR_OVER_YEAR_LABELS[col.range_key] || 'Vorjahreszeitraum';
        if (offset === -1) return `${PREVIOUS_LABELS[col.range_key] || 'Vorperiode'} (Vorjahr)`;
        const unit = PLURAL_UNITS[col.range_key] || 'Perioden';
        const base = offset < 0 ? `Vor ${Math.abs(offset)} ${unit}` : `In ${offset} ${unit}`;
        return `${base} (Vorjahr)`;
      }
      if (offset === 0) return RANGE_LABELS[col.range_key] || 'Zeitraum';
      if (offset === -1) return PREVIOUS_LABELS[col.range_key] || 'Vorperiode';
      const unit = PLURAL_UNITS[col.range_key] || 'Perioden';
      return offset < 0 ? `Vor ${Math.abs(offset)} ${unit}` : `In ${offset} ${unit}`;
    }

    // Fügt einen Beschriftungs-Platzhalter ("{jahr}" etc.) an der aktuellen
    // Cursor-Position im Beschriftungsfeld ein, statt ihn einfach anzuhängen —
    // das Feld bleibt dadurch frei weiter editierbar (z. B. "Ist {jahr}" durch
    // Klick zwischen "Ist " und dem Cursor). inputEl kommt als $refs.input aus
    // dem lokalen x-data-Scope der Spaltenkarte (siehe Markup), nicht aus
    // Alpines flachem $refs-Register, das innerhalb von x-for sonst nur den
    // zuletzt gerenderten Treffer behielte.
    function insertColumnVariable(col, token, inputEl) {
      const text = col.label || '';
      const start = inputEl ? (inputEl.selectionStart ?? text.length) : text.length;
      const end = inputEl ? (inputEl.selectionEnd ?? text.length) : text.length;
      const insertText = `{${token}}`;
      col.label = text.slice(0, start) + insertText + text.slice(end);
      if (!inputEl) return;
      const pos = start + insertText.length;
      Alpine.nextTick(() => {
        inputEl.focus();
        inputEl.setSelectionRange(pos, pos);
      });
    }

    function versatzUnit(col) {
      return UNIT_PLURAL_SIMPLE[col.range_key] || 'Perioden';
    }

    // Namensvorschlag für eine Entität-Zeile ohne eigene Beschriftung — der
    // friendly_name der gewählten Entität. Gruppen/Formeln haben keine
    // einzelne Entität, die dafür herhalten könnte, deshalb dort kein
    // Vorschlag (leerer String, Aufrufer fällt dann auf "Beschriftung …"
    // bzw. "(ohne Namen)" zurück).
    function suggestRowLabel(row) {
      if (row.row_type === 'entity' && row.entity_ids.length === 1) {
        return ENTITY_LABEL_BY_ID[row.entity_ids[0]] || '';
      }
      if (row.row_type === 'summary') return row.aggregation === 'avg' ? 'Durchschnitt' : 'Summe';
      return '';
    }

    // Spalten per Drag & Drop neu anordnen. Anders als bei den Dashboard-
    // Kacheln (dashboard-tiles.js, setupDragAndDrop() — dort reines DOM ohne
    // konkurrierendes Framework) werden die Karten hier von Alpines x-for
    // gerendert. Während des Ziehens per insertBefore direkt im DOM zu
    // verschieben (die ursprüngliche Fassung) gerät mit Alpines eigener,
    // keyed x-for-Verwaltung derselben Elemente in Konflikt: die Vorschau-
    // Tabelle (die NIE manuell angefasst wird) aktualisiert sich sofort
    // korrekt, aber die manuell verschobene Karte selbst hinkt hinterher und
    // "springt" erst beim nächsten x-for-Durchlauf (z. B. der übernächste
    // Drag) an ihre neue Position — Alpines interne Zuordnung Key→Knoten
    // verliert durch das externe insertBefore den Bezug zur tatsächlichen
    // DOM-Reihenfolge. Deshalb hier stattdessen: WÄHREND des Ziehens direkt
    // das reaktive Array (this.columns) umsortieren und dem DOM die
    // Neupositionierung vollständig überlassen — Alpine bleibt die einzige
    // Instanz, die diese Knoten je bewegt.
    function setupColumnDrag() {
      const container = document.querySelector('.tbl-columns');
      if (!container || container.dataset.dragBound) return;
      container.dataset.dragBound = '1';
      let draggedUid = null;
      let draggedEl = null;
      // dragstart selbst kann nicht prüfen, ob der Griff angefasst wurde —
      // event.target ist dort laut Spec immer das draggable-Element (die
      // ganze Karte), nicht der tatsächlich angeklickte Kindknoten. Deshalb
      // hier per mousedown merken, dort ist event.target noch der reale
      // Klick-Zielknoten wie bei jedem anderen Maus-Event.
      let handleGrabbed = false;
      container.addEventListener('mousedown', e => {
        handleGrabbed = !!e.target.closest('.drag-handle');
      });
      container.addEventListener('dragstart', e => {
        const card = e.target.closest('.tbl-col-card');
        if (!card) return;
        // Nur vom Ziehgriff aus starten — sonst interpretiert der Browser
        // einen Drag-Start auf einem input/select als Textauswahl/Fokus
        // statt als natives Drag (siehe Kommentar bei .drag-handle oben).
        if (!handleGrabbed) { e.preventDefault(); return; }
        draggedEl = card;
        draggedUid = card.dataset.colUid;
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedUid || '');
      });
      container.addEventListener('dragover', e => {
        if (!draggedUid) return;
        e.preventDefault();
        const card = e.target.closest('.tbl-col-card');
        if (!card || card.dataset.colUid === draggedUid) return;
        const pageData = Alpine.$data(document.querySelector('.page'));
        const fromIdx = pageData.columns.findIndex(c => c.uid === draggedUid);
        const overIdx = pageData.columns.findIndex(c => c.uid === card.dataset.colUid);
        if (fromIdx === -1 || overIdx === -1) return;
        const rect = card.getBoundingClientRect();
        const before = (e.clientX - rect.left) < rect.width / 2;
        let targetIdx = overIdx + (before ? 0 : 1);
        if (targetIdx > fromIdx) targetIdx -= 1;
        if (targetIdx === fromIdx) return;
        const [col] = pageData.columns.splice(fromIdx, 1);
        pageData.columns.splice(targetIdx, 0, col);
      });
      container.addEventListener('dragend', () => {
        if (draggedEl) draggedEl.classList.remove('dragging');
        draggedEl = null;
        draggedUid = null;
      });
    }

    // Zeilen per Drag & Drop neu anordnen — dasselbe Muster wie
    // setupColumnDrag() oben (Array live während des Ziehens umsortieren,
    // kein manuelles insertBefore), nur vertikal (Zeilen untereinander statt
    // Karten nebeneinander): Reihenfolge-Vergleich per rect.top/height-
    // Mittelpunkt statt rect.left/width. Anders als bei Spalten wird beim
    // Loslassen zusätzlich correctFormulaLetters() aufgerufen — sie
    // korrigiert die Buchstaben-Referenzen in Formel-Zeilen anhand der beim
    // Start des Ziehens gemerkten alten Zuordnung, genau wie moveRow() per
    // Pfeil-Buttons — und danach neu geladen.
    function setupRowDrag() {
      const container = document.getElementById('tbl-rows');
      if (!container || container.dataset.dragBound) return;
      container.dataset.dragBound = '1';
      let draggedUid = null;
      let draggedEl = null;
      let oldLetterToUid = null;
      // Siehe Kommentar zu handleGrabbed in setupColumnDrag() oben —
      // event.target ist bei dragstart selbst nicht der angeklickte
      // Kindknoten, deshalb per mousedown vormerken.
      let handleGrabbed = false;
      container.addEventListener('mousedown', e => {
        handleGrabbed = !!e.target.closest('.drag-handle');
      });
      container.addEventListener('dragstart', e => {
        const card = e.target.closest('.tbl-row-card');
        if (!card) return;
        // Nur vom Ziehgriff aus starten — siehe derselbe Kommentar in
        // setupColumnDrag() oben.
        if (!handleGrabbed) { e.preventDefault(); return; }
        draggedEl = card;
        draggedUid = card.dataset.rowUid;
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedUid || '');
        const pageData = Alpine.$data(document.querySelector('.page'));
        oldLetterToUid = pageData.snapshotRowLetters();
      });
      container.addEventListener('dragover', e => {
        if (!draggedUid) return;
        e.preventDefault();
        const card = e.target.closest('.tbl-row-card');
        if (!card || card.dataset.rowUid === draggedUid) return;
        const pageData = Alpine.$data(document.querySelector('.page'));
        const fromIdx = pageData.rows.findIndex(r => r.uid === draggedUid);
        const overIdx = pageData.rows.findIndex(r => r.uid === card.dataset.rowUid);
        if (fromIdx === -1 || overIdx === -1) return;
        const rect = card.getBoundingClientRect();
        const before = (e.clientY - rect.top) < rect.height / 2;
        let targetIdx = overIdx + (before ? 0 : 1);
        if (targetIdx > fromIdx) targetIdx -= 1;
        if (targetIdx === fromIdx) return;
        const [row] = pageData.rows.splice(fromIdx, 1);
        pageData.rows.splice(targetIdx, 0, row);
      });
      container.addEventListener('dragend', () => {
        if (draggedEl) draggedEl.classList.remove('dragging');
        if (draggedUid && oldLetterToUid) {
          const pageData = Alpine.$data(document.querySelector('.page'));
          pageData.correctFormulaLetters(oldLetterToUid);
          pageData.load();
        }
        draggedEl = null;
        draggedUid = null;
        oldLetterToUid = null;
      });
    }

    // Options-Listen für die drei Dropdown-Picker unten (Zeilentyp, Rahmen,
    // Zeilenabstand) — dasselbe .dd-picker-Muster wie im Rest der App, hier
    // aber Alpine-gebunden statt static/js/dd-picker.js: der Zeilentyp-Picker
    // existiert pro Zeile (x-for), ein modulweites Öffnen/Schließen über
    // IDs wie bei dd-picker.js würde bei mehreren gleichzeitig gerenderten
    // Zeilen kollidieren. row.typePickerOpen (analog zu row.pickerOpen beim
    // bestehenden Gruppen-Picker) hält den Zustand deshalb pro Zeile.
    // Dieselbe Zahl wie main.py MAX_TABLE_ROW_LABEL_LENGTH (dort zusätzlich
    // serverseitig durchgesetzt, ein Request kann das Feld-maxlength umgehen).
    const MAX_ROW_LABEL_LENGTH = 30;
    const ROW_TYPE_OPTIONS = [['entity', 'Entität'], ['group', 'Gruppe'], ['formula', 'Formel'], ['summary', 'Summenzeile'], ['separator', 'Trennlinie']];
    const ROW_TYPE_LABELS = Object.fromEntries(ROW_TYPE_OPTIONS);
    // Aggregation je Entität/Gruppen-Zeile — "auto" ist das bisherige,
    // implizite Verhalten (Zähler/Schalter -> Summe, sonst Durchschnitt),
    // siehe TableCompute.computeValues()/memberValueFor(). Kurze
    // Button-Labels (siehe .tbl-agg-picker-Kommentar oben im CSS), volle
    // Bezeichnung im Popover UND als title-Tooltip auf dem Button.
    const AGG_OPTIONS = [['auto', 'Automatisch'], ['avg', 'Ø Durchschnitt'], ['min', 'Min'], ['max', 'Max'], ['sum', 'Σ Summe']];
    // Summenzeile kennt nur Summe/Durchschnitt — "Automatisch"/"Min"/"Max"
    // ergeben für eine Summenzeile keinen Sinn (die bezieht sich immer auf
    // MEHRERE bereits aggregierte Zeilen, nicht auf einzelne Rohwerte).
    const SUMMARY_AGG_OPTIONS = [['sum', 'Σ Summe'], ['avg', 'Ø Durchschnitt']];
    const AGG_SHORT_LABELS = {auto: 'Auto', avg: 'Ø', min: 'Min', max: 'Max', sum: 'Σ'};
    const AGG_TITLES = {
      auto: 'Automatisch (Zähler/Schalter → Summe, sonst Durchschnitt)',
      avg: 'Durchschnitt', min: 'Minimum', max: 'Maximum', sum: 'Summe',
    };
    const BORDER_OPTIONS = [['horizontal', 'Horizontal'], ['grid', 'Gitter'], ['none', 'Ohne Rahmen']];
    const BORDER_LABELS = Object.fromEntries(BORDER_OPTIONS);
    const DENSITY_OPTIONS = [['comfortable', 'Komfortabel'], ['compact', 'Kompakt']];
    const DENSITY_LABELS = Object.fromEntries(DENSITY_OPTIONS);
    const ALIGN_OPTIONS = [['left', 'Linksbündig'], ['center', 'Zentriert'], ['right', 'Rechtsbündig']];
    const ALIGN_LABELS = Object.fromEntries(ALIGN_OPTIONS);

    function tableEditor() {
      return {
        tableId: TABLE_ID,
        name: TABLE_NAME,
        // Der "Bearbeiten"-Button auf der Tabellen-Übersicht verlinkt mit
        // ?edit=1 hierher, um direkt im Bearbeiten-Modus zu starten statt
        // erst über "Anpassen" umschalten zu müssen.
        editing: !TABLE_ID || new URLSearchParams(location.search).has('edit'),
        columns: INITIAL_COLUMNS.map(c => ({decimals: 'auto', year_over_year: false, hidden: false, group_label: '', heatmap: false, width: null, ...c, uid: uid(), offset: Math.min(c.offset || 0, 0)})),
        rows: INITIAL_ROWS.map(r => ({
          formula_unit: '', aggregation: 'auto', hidden: false, show_label: false, accent: false,
          percent_of_total: false, hide_if_empty: false,
          ...r, uid: uid(), pickerOpen: false, typePickerOpen: false, aggPickerOpen: false, optsPickerOpen: false, search: '',
        })),
        style: {...DEFAULT_STYLE, ...INITIAL_STYLE},
        values: {},  // col.uid -> row.uid -> {value, unit, error} | null
        windowStarts: {},  // col.uid -> tatsächlich aufgelöster Fensterbeginn (Sekunden) | null
        elapsedSeconds: {},  // col.uid -> same_elapsed-Kappung ab windowStarts[uid] (Sekunden) | null
        loading: false,
        saving: false,
        savedMessage: '',
        _loadSeq: 0,
        bordersPickerOpen: false,
        densityPickerOpen: false,
        headerAlignPickerOpen: false,
        valueAlignPickerOpen: false,
        letterPositions: {},  // row.uid -> {top, height} in px, aus der echten Tabelle gemessen
        gutterHeight: 0,

        // Buchstaben-Zuordnung bewusst über ALLE Zeilen (auch ausgeblendete)
        // — sonst würde eine Formel, die auf eine versteckte Hilfszeile
        // verweist, bei jedem Ausblenden ihre Referenz verlieren.
        get rowLetters() {
          const m = {};
          let dataIndex = 0;
          this.rows.forEach(r => {
            m[r.uid] = r.row_type === 'separator' ? null : String.fromCharCode(65 + ((dataIndex++) % 26));
          });
          return m;
        },

        get visibleColumnCount() {
          return this.columns.filter(c => !c.hidden).length;
        },

        // Der Menü-Button selbst wird aktiv, sobald irgendeine enthaltene
        // Option an ist — sonst müsste man das Menü öffnen, um das zu sehen.
        rowOptionsActive(row) {
          return !!(row.bold || row.show_label || row.accent || row.percent_of_total || row.hide_if_empty);
        },

        // Manuelle Spaltenbreite (col.width/style.label_col_width, per
        // Ziehgriff gesetzt) — leer/null heißt automatische Breite (Standard-
        // Tabellenlayout, richtet sich nach dem Inhalt). Nur bei gesetzter
        // Breite wird der Text abgeschnitten (Ellipsis) statt umzubrechen
        // oder die Nachbarspalte zu verdrängen — ohne gesetzte Breite bleibt
        // das bisherige Verhalten (voll, nichts abgeschnitten).
        colWidthStyle(col) {
          if (!col.width) return '';
          return `width:${col.width}px;max-width:${col.width}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`;
        },
        labelColStyle() {
          const w = this.style.label_col_width;
          if (!w) return '';
          return `width:${w}px;max-width:${w}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;`;
        },
        // Ziehen am rechten Rand einer Spaltenkopfzelle — Doppelklick auf den
        // Griff setzt col.width wieder auf null (automatische Breite) statt
        // hier ein zweites Reset-Steuerelement zu brauchen.
        startColResize(event, col) {
          const th = event.currentTarget.closest('th');
          const startX = event.clientX;
          const startWidth = col.width || th.getBoundingClientRect().width;
          const onMove = (e) => {
            col.width = Math.max(40, Math.min(800, Math.round(startWidth + (e.clientX - startX))));
          };
          const onUp = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
          };
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        },
        startLabelColResize(event) {
          const th = event.currentTarget.closest('th');
          const startX = event.clientX;
          const startWidth = this.style.label_col_width || th.getBoundingClientRect().width;
          const onMove = (e) => {
            this.style.label_col_width = Math.max(40, Math.min(800, Math.round(startWidth + (e.clientX - startX))));
          };
          const onUp = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
          };
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        },
        // "Spalten gleichmäßig" (style.equal_value_cols) braucht table-
        // layout:fixed, das sich an den Zellbreiten der ersten Zeile
        // orientiert — ohne eigene label_col_width würde die
        // Beschriftungsspalte dann fälschlich MIT gleichmäßig aufgeteilt
        // statt ihre bisherige (automatische) Breite zu behalten. Deshalb
        // beim Einschalten einmalig die AKTUELLE, gerenderte Breite als
        // label_col_width einfrieren — Doppelklick auf den Ziehgriff setzt
        // sie bei Bedarf wieder auf automatisch zurück.
        toggleEqualValueCols() {
          if (!this.style.equal_value_cols && !this.style.label_col_width) {
            // NICHT einfach "th.tbl-label-col" (erste Treffer wäre bei
            // fehlenden Spaltengruppen die UNSICHTBARE Gruppen-Kopfzeile,
            // x-show="hasColumnGroups()" lässt ihre Zelle mit Breite 0 im DOM
            // stehen) — explizit die sichtbare Zeitraum-Kopfzeile.
            const labelTh = this.$refs.previewTable?.querySelector('tr.tbl-header-row:not(.tbl-group-header-row) th.tbl-label-col');
            if (labelTh) this.style.label_col_width = Math.round(labelTh.getBoundingClientRect().width);
          }
          this.style.equal_value_cols = !this.style.equal_value_cols;
        },


        // Mehrstufige Kopfzeile (col.group_label, z. B. "2025" über mehreren
        // Monatsspalten) — nur sichtbare Spalten, aufeinanderfolgende Spalten
        // mit demselben (nicht-leeren) group_label bekommen EINE gemeinsame,
        // überspannende Zelle; eine leere group_label bleibt eine eigene
        // 1-Spalten-Lücke, damit die zweite Kopfzeile darunter (Zeiträume)
        // spaltengenau ausgerichtet bleibt.
        groupHeaderCells() {
          const visible = this.columns.filter(c => !c.hidden);
          const cells = [];
          let i = 0;
          while (i < visible.length) {
            const label = (visible[i].group_label || '').trim();
            let span = 1;
            while (label && i + span < visible.length && (visible[i + span].group_label || '').trim() === label) span++;
            cells.push({label, span});
            i += span;
          }
          return cells;
        },
        hasColumnGroups() {
          return this.columns.some(c => !c.hidden && (c.group_label || '').trim());
        },

        // CSV-Export der aktuell berechneten (nicht der rohen) Tabelle —
        // dieselben sichtbaren Spalten/Zeilen wie die Vorschau, inkl.
        // Anteils-/Summenzeilen-Transformation und Dezimal-/Einheiten-
        // Einstellungen. Semikolon statt Komma als Trennzeichen, da das
        // deutsche Dezimaltrennzeichen (siehe NumberFormat) sonst mit dem
        // CSV-Trennzeichen kollidieren würde — Excel (de) erkennt Semikolon-
        // CSVs automatisch.
        exportCsv() {
          const cols = this.columns.filter(c => !c.hidden);
          const csvEscape = s => `"${String(s).replace(/"/g, '""')}"`;
          const lines = [];
          lines.push(['', ...cols.map(c => csvEscape(this.renderedColumnLabel(c)))].join(';'));
          this.rows.forEach(row => {
            if (row.row_type === 'separator' || !this.rowVisible(row)) return;
            const cells = cols.map(col => {
              const parts = this.cellNumberParts(row, col);
              const unit = this.cellUnit(row, col);
              const number = `${parts.whole}${parts.separator}${parts.fraction}`;
              return csvEscape(unit ? `${number} ${unit}` : number);
            });
            lines.push([csvEscape(this.resolvedRowLabel(row)), ...cells].join(';'));
          });
          const blob = new Blob(['﻿' + lines.join('\r\n')], {type: 'text/csv;charset=utf-8;'});
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${(this.name || 'tabelle').trim() || 'tabelle'}.csv`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        },

        get hasVisibleDataRow() {
          return this.rows.some(r => r.row_type !== 'separator' && this.rowVisible(r));
        },

        // Zebra-Parität nur über inhaltliche, SICHTBARE Zeilen bestimmen:
        // eine Trennlinie ist reine Gliederung und darf den Streifenwechsel
        // nicht verschieben, eine ausgeblendete (manuell oder automatisch
        // wegen hide_if_empty) Zeile wird gar nicht gerendert und darf ihn
        // ebenfalls nicht verschieben.
        isAlternateDataRow(rowUid) {
          let dataIndex = 0;
          for (const row of this.rows) {
            if (row.row_type === 'separator' || !this.rowVisible(row)) continue;
            if (row.uid === rowUid) return dataIndex % 2 === 1;
            dataIndex += 1;
          }
          return false;
        },

        addColumn() {
          this.columns.push({uid: uid(), label: '', range_key: 'month', offset: 0, year_over_year: false, decimals: 'auto', hidden: false, group_label: '', heatmap: false, width: null});
        },
        // Kopie mit neuer uid direkt nach dem Original — dieselbe Idee wie
        // "Tabelle duplizieren" auf der Übersichtsseite, nur für eine
        // einzelne Spalte/Zeile statt der ganzen Tabelle.
        duplicateColumn(colUid) {
          const index = this.columns.findIndex(c => c.uid === colUid);
          if (index < 0) return;
          this.columns.splice(index + 1, 0, {...this.columns[index], uid: uid()});
        },
        duplicateRow(rowUid) {
          const index = this.rows.findIndex(r => r.uid === rowUid);
          if (index < 0) return;
          this.rows.splice(index + 1, 0, {...this.rows[index], uid: uid()});
          this.load();
        },
        // Verschiebt eine Spalte um eine Position nach links (-1) oder rechts
        // (+1) — dieselbe Splice-Logik wie moveRow() unten, als Alternative
        // zum Ziehen (setupColumnDrag()) für Tastatur-/Klickbedienung. Kein
        // load() nötig — reine Anzeigereihenfolge, dieselben bereits
        // geladenen Werte (values ist über col.uid indiziert, siehe unten).
        moveColumn(colUid, direction) {
          const index = this.columns.findIndex(c => c.uid === colUid);
          const target = index + direction;
          if (index < 0 || target < 0 || target >= this.columns.length) return;
          const [col] = this.columns.splice(index, 1);
          this.columns.splice(target, 0, col);
        },
        removeColumn(colUid) {
          this.columns = this.columns.filter(c => c.uid !== colUid);
          this.load();
        },
        addRow(type) {
          this.rows.push({
            uid: uid(), label: '', row_type: type, entity_ids: [], formula: '', formula_unit: '',
            aggregation: type === 'summary' ? 'sum' : 'auto', bold: false, hidden: false, show_label: false,
            accent: false, percent_of_total: false, hide_if_empty: false,
            pickerOpen: false, typePickerOpen: false, aggPickerOpen: false, optsPickerOpen: false, search: '',
          });
          this.load();
        },
        setRowType(row, type) {
          row.row_type = type;
          if (type === 'entity') row.entity_ids = row.entity_ids.slice(0, 1);
          if (type === 'separator') {
            row.label = '';
            row.entity_ids = [];
            row.formula = '';
            row.formula_unit = '';
            row.bold = false;
          }
          if (type === 'summary') {
            row.entity_ids = [];
            row.formula = '';
            row.formula_unit = '';
            if (row.aggregation !== 'sum' && row.aggregation !== 'avg') row.aggregation = 'sum';
          }
          this.load();
        },
        moveRow(rowUid, direction) {
          const index = this.rows.findIndex(r => r.uid === rowUid);
          const target = index + direction;
          if (index < 0 || target < 0 || target >= this.rows.length) return;
          const oldLetterToUid = this.snapshotRowLetters();
          const [row] = this.rows.splice(index, 1);
          this.rows.splice(target, 0, row);
          this.correctFormulaLetters(oldLetterToUid);
          this.load();
        },
        // Buchstabe→UID-Momentaufnahme der aktuellen Reihenfolge, vor einer
        // Umsortierung zu ziehen (von moveRow() per Pfeil-Button UND von
        // setupRowDrag() bei dragstart genutzt) — Grundlage für
        // correctFormulaLetters() danach.
        snapshotRowLetters() {
          const letterToUid = {};
          Object.entries(this.rowLetters).forEach(([rowUid, letter]) => {
            if (letter) letterToUid[letter] = rowUid;
          });
          return letterToUid;
        },
        // Korrigiert die Buchstaben-Referenzen (A/B/C …) in Formel-Zeilen
        // nach einer Umsortierung von this.rows: die Buchstaben sind rein
        // positionsabhängig (rowLetters-Getter), eine Formel wie "A/B"
        // speichert aber nur den Buchstaben, nicht die referenzierte Zeile
        // selbst — ohne diese Korrektur würde eine Formel nach dem
        // Umsortieren stillschweigend eine ANDERE Zeile referenzieren als
        // vorher gemeint. oldLetterToUid ist die vor der Umsortierung per
        // snapshotRowLetters() gezogene Momentaufnahme.
        correctFormulaLetters(oldLetterToUid) {
          const newLetterOfUid = this.rowLetters;
          this.rows.forEach(r => {
            if (r.row_type !== 'formula' || !r.formula) return;
            r.formula = r.formula.replace(/[A-Za-z]/g, ch => {
              const referencedUid = oldLetterToUid[ch.toUpperCase()];
              if (!referencedUid) return ch;
              return newLetterOfUid[referencedUid] || ch;
            });
          });
        },
        removeRow(rowUid) {
          this.rows = this.rows.filter(r => r.uid !== rowUid);
          this.load();
        },
        toggleGroupEntity(row, entityId) {
          const i = row.entity_ids.indexOf(entityId);
          if (i === -1) row.entity_ids.push(entityId); else row.entity_ids.splice(i, 1);
          this.load();
        },

        suggestRowLabel(row) { return suggestRowLabel(row); },
        suggestColumnLabel(col) { return suggestColumnLabel(col); },
        // Tatsächlich angezeigter Spaltenname: eigene Beschriftung (mit
        // aufgelösten Platzhaltern) oder sonst der Vorschlag — dasselbe
        // Fallback-Prinzip wie resolvedRowLabel() unten.
        renderedColumnLabel(col) {
          const raw = col.label.trim();
          if (!raw) return suggestColumnLabel(col);
          return TableCompute.resolveLabel(raw, this.windowStarts[col.uid]);
        },
        suggestFormulaUnit(row) {
          for (const col of this.columns) {
            const cell = this.values[col.uid] && this.values[col.uid][row.uid];
            if (cell && cell.unit) return cell.unit;
          }
          return '';
        },
        versatzUnit(col) { return versatzUnit(col); },
        insertColumnVariable(col, token, inputEl) { insertColumnVariable(col, token, inputEl); },
        clampOffset(col) { if (col.offset > 0) col.offset = 0; },
        // Tatsächlich angezeigter/gespeicherter Name — eigene Beschriftung,
        // sonst der Vorschlag (friendly_name der Entität), sonst zuletzt
        // "(ohne Namen)". Eine Methode statt zweier getrennter Stellen, damit
        // Vorschau-Tabelle und save() garantiert denselben Namen verwenden.
        // Sowohl die eigene Beschriftung (bei einer VOR MAX_ROW_LABEL_LENGTH
        // gespeicherten Tabelle länger als das jetzige maxlength auf dem
        // Feld) als auch der Vorschlag (1:1 der Entitäts-friendly_name, nie
        // durch maxlength begrenzt) werden hier gekappt — sonst würde eine
        // unangetastete alte Zeile beim nächsten Speichern an
        // MAX_TABLE_ROW_LABEL_LENGTH (main.py) scheitern, obwohl der Nutzer
        // an genau dieser Zeile nichts geändert hat.
        resolvedRowLabel(row) {
          const cap = s => s.length > MAX_ROW_LABEL_LENGTH ? s.slice(0, MAX_ROW_LABEL_LENGTH - 1) + '…' : s;
          if (row.row_type === 'separator') return cap(row.label.trim());
          const own = row.label.trim();
          if (own) return cap(own);
          const suggested = suggestRowLabel(row);
          return suggested ? cap(suggested) : '(ohne Namen)';
        },

        // Alle Entität-/Gruppen-Zellen DERSELBEN Spalte im selben Abschnitt
        // (zwischen zwei Trennlinien) wie row — Grundlage für row.percent_of_total
        // (TableCompute.percentOfTotalCell rechnet nur noch, sucht sich die
        // Mitglieder aber nicht selbst, siehe Kommentar dort).
        sectionMemberCells(row, col) {
          const idx = this.rows.findIndex(r => r.uid === row.uid);
          if (idx < 0) return [];
          let start = 0;
          for (let j = idx - 1; j >= 0; j--) { if (this.rows[j].row_type === 'separator') { start = j + 1; break; } }
          let end = this.rows.length;
          for (let j = idx + 1; j < this.rows.length; j++) { if (this.rows[j].row_type === 'separator') { end = j; break; } }
          const cells = [];
          for (let j = start; j < end; j++) {
            const r = this.rows[j];
            if (r.row_type !== 'entity' && r.row_type !== 'group') continue;
            cells.push(this.values[col.uid] && this.values[col.uid][r.uid]);
          }
          return cells;
        },
        // Einziger Ort, an dem row.percent_of_total den rohen Zellwert durch
        // den Anteilswert ersetzt — cellNumberParts/cellUnit lesen beide von
        // hier, damit Zahl und Einheit (bzw. "%") nie auseinanderlaufen.
        displayCell(row, col) {
          const cell = this.values[col.uid] && this.values[col.uid][row.uid];
          if (row.percent_of_total) return TableCompute.percentOfTotalCell(cell, this.sectionMemberCells(row, col));
          return cell;
        },
        cellNumberParts(row, col) {
          const cell = this.displayCell(row, col);
          // Anteil bewusst immer mit fester 1 Nachkommastelle statt der
          // spaltenweiten Einstellung — "23,4 %" ist unabhängig davon
          // sinnvoll lesbar, wie die Spalte sonst gerundet wird.
          return TableCompute.cellNumberParts(cell, row.percent_of_total ? '1' : col.decimals, this.style.explicit_missing);
        },
        cellUnit(row, col) {
          return TableCompute.cellUnit(this.displayCell(row, col));
        },
        cellError(row, col) {
          const cell = this.values[col.uid] && this.values[col.uid][row.uid];
          return !!(cell && cell.error);
        },
        // Minimum/Maximum DERSELBEN Spalte über alle Entität-/Gruppen-Zeilen
        // im selben Abschnitt (zwischen zwei Trennlinien) — bewusst
        // spaltenweise statt zeilenweise: eine Zeile enthält hier oft sehr
        // unterschiedliche Größenordnungen nebeneinander (Tag vs. Jahr
        // desselben Werts), ein Vergleich über die eigene Zeile hinweg wäre
        // daher irreführend ("Jahr" sticht immer heraus, nur weil es ein
        // längerer Zeitraum ist). Sinnvoll ist stattdessen der Vergleich
        // MEHRERER Zeilen INNERHALB derselben Spalte (z. B. welches Gerät
        // verbraucht diesen Monat am meisten). Bewusst NUR Entität-/
        // Gruppen-Zeilen (nicht Formel/Summenzeile): eine Summenzeile ist
        // fast immer das Maximum ihres Abschnitts, rein weil sie andere
        // Zeilen aufaddiert — würde sie mitzählen, würde sie die Skala
        // verzerren UND selbst immer "heiß" erscheinen, ohne dass das
        // etwas über tatsächliches Verbrauchsverhalten aussagt.
        columnHeatmapRange(row, col) {
          const idx = this.rows.findIndex(r => r.uid === row.uid);
          if (idx < 0) return null;
          let start = 0;
          for (let j = idx - 1; j >= 0; j--) { if (this.rows[j].row_type === 'separator') { start = j + 1; break; } }
          let end = this.rows.length;
          for (let j = idx + 1; j < this.rows.length; j++) { if (this.rows[j].row_type === 'separator') { end = j; break; } }
          const vals = [];
          for (let j = start; j < end; j++) {
            if (this.rows[j].row_type !== 'entity' && this.rows[j].row_type !== 'group') continue;
            const cell = this.values[col.uid] && this.values[col.uid][this.rows[j].uid];
            if (cell && !cell.error && cell.value != null) vals.push(cell.value);
          }
          if (!vals.length) return null;
          return {min: Math.min(...vals), max: Math.max(...vals)};
        },
        heatmapStyleFor(row, col) {
          if (!col.heatmap || (row.row_type !== 'entity' && row.row_type !== 'group')) return '';
          const cell = this.values[col.uid] && this.values[col.uid][row.uid];
          return TableCompute.heatmapStyle(cell, this.columnHeatmapRange(row, col));
        },
        // row.hide_if_empty: automatisch ausblenden, wenn in JEDER sichtbaren
        // Spalte entweder kein Wert oder 0 steht (z. B. ein stillgelegtes
        // Gerät) — zusätzlich zum manuellen row.hidden-Toggle, nicht anstelle
        // davon (beide zusammen in rowVisible() unten geprüft).
        rowIsEmpty(row) {
          if (!row.hide_if_empty) return false;
          return this.columns.filter(c => !c.hidden).every(c => {
            const cell = this.values[c.uid] && this.values[c.uid][row.uid];
            return !cell || cell.value == null || cell.value === 0;
          });
        },
        rowVisible(row) {
          return !row.hidden && !this.rowIsEmpty(row);
        },
        isComparisonColumn(col) { return TableCompute.isComparisonColumn(col); },
        deviationText(row, col) {
          const baseIndex = this.columns.findIndex(candidate => candidate.uid === col.uid);
          const comparisonIndex = TableCompute.comparisonIndexForBase(this.columns, baseIndex);
          if (comparisonIndex < 0) return '';
          const baseCell = this.values[col.uid] && this.values[col.uid][row.uid];
          const comparisonCol = this.columns[comparisonIndex];
          const comparisonCell = this.values[comparisonCol.uid] && this.values[comparisonCol.uid][row.uid];
          return TableCompute.deviationText(baseCell, comparisonCell);
        },
        deviationTitle(row, col) {
          const baseIndex = this.columns.findIndex(candidate => candidate.uid === col.uid);
          const comparisonIndex = TableCompute.comparisonIndexForBase(this.columns, baseIndex);
          if (comparisonIndex < 0) return '';
          const comparisonCol = this.columns[comparisonIndex];
          const comparisonCell = this.values[comparisonCol.uid] && this.values[comparisonCol.uid][row.uid];
          const comparisonLabel = this.renderedColumnLabel(comparisonCol);
          // Zusatzhinweis samt Vergleichswert wie in dashboard-tiles.js: die
          // Zelle selbst zeigt weiterhin den vollständigen Zeitraum, nur der
          // Prozent-Vergleich ist auf "bisher vergangen" gekappt
          // (comparisonValue, siehe TableCompute.deviationText()). Nur für
          // Entität-/Gruppenzeilen vorhanden, siehe computeValues() in
          // table-compute.js. Statt einer vagen Phrase ("...zum aktuellen
          // Zeitpunkt") die tatsächliche Uhrzeit des Vergleichs-Cutoffs
          // (windowStart + elapsedSeconds derselben Spalte).
          const comparisonValueStr = TableCompute.comparisonValueText(comparisonCell, comparisonCol.decimals);
          if (!comparisonValueStr) return `Gegenüber ${comparisonLabel}`;
          const comparisonTimeStr = TableCompute.comparisonElapsedTimeText(
            this.windowStarts[comparisonCol.uid], this.elapsedSeconds[comparisonCol.uid]);
          return `Gegenüber ${comparisonLabel}${comparisonTimeStr ? ` bis ${comparisonTimeStr}` : ''}: ${comparisonValueStr}`;
        },

        // Rechenkern in static/js/table-compute.js (TableCompute.computeValues) —
        // von der Dashboard-Kachel für dieselbe Tabelle genutzt, damit beide
        // garantiert dieselben Werte zeigen. Das arbeitet auf einfachen,
        // Index-basierten Arrays statt der hier üblichen uid-Objekte, deshalb
        // hier nur die Umwandlung dorthin und die Ergebnisse zurück in
        // this.values[col.uid][row.uid].
        async load() {
          if (!this.columns.length || !this.rows.length) { this.values = {}; this.windowStarts = {}; this.elapsedSeconds = {}; return; }
          const requestId = ++this._loadSeq;
          this.loading = true;
          const plainColumns = this.columns.map(c => ({range_key: c.range_key, offset: c.offset, year_over_year: c.year_over_year}));
          const plainRows = this.rows.map(r => ({
            row_type: r.row_type, entity_ids: r.entity_ids, formula: r.formula,
            formula_unit: r.formula_unit || '', aggregation: r.aggregation || 'auto',
          }));
          const {values: computed, windowStarts, elapsedSeconds} = await TableCompute.computeValues(BASE, plainColumns, plainRows);
          if (requestId !== this._loadSeq) return;  // überholt von einer neueren Anfrage
          const newValues = {};
          const newWindowStarts = {};
          const newElapsedSeconds = {};
          this.columns.forEach((col, ci) => {
            newValues[col.uid] = {};
            this.rows.forEach((row, ri) => { newValues[col.uid][row.uid] = computed[ci][ri]; });
            newWindowStarts[col.uid] = windowStarts[ci];
            newElapsedSeconds[col.uid] = elapsedSeconds[ci];
          });
          this.values = newValues;
          this.windowStarts = newWindowStarts;
          this.elapsedSeconds = newElapsedSeconds;
          this.loading = false;
        },

        async save() {
          if (!this.name.trim()) { appAlert('Bitte einen Namen für die Tabelle angeben.'); return; }
          if (!this.columns.length) { appAlert('Bitte mindestens eine Spalte anlegen.'); return; }
          if (!this.rows.length) { appAlert('Bitte mindestens eine Zeile anlegen.'); return; }
          for (const row of this.rows) {
            if ((row.row_type === 'entity' || row.row_type === 'group') && !row.entity_ids.length) {
              appAlert(`Zeile "${this.resolvedRowLabel(row)}" braucht mindestens eine Entität.`);
              return;
            }
            if (row.row_type === 'formula' && !row.formula.trim()) {
              appAlert(`Zeile "${this.resolvedRowLabel(row)}" braucht eine Formel.`);
              return;
            }
          }
          this.saving = true;
          this.savedMessage = '';
          const body = {
            name: this.name.trim(),
            columns: this.columns.map(c => ({
              label: c.label.trim() || suggestColumnLabel(c), range_key: c.range_key, offset: c.offset || 0,
              year_over_year: !!c.year_over_year, decimals: c.decimals || 'auto', hidden: !!c.hidden,
              group_label: (c.group_label || '').trim(), heatmap: !!c.heatmap,
              width: c.width || null,
            })),
            rows: this.rows.map(r => ({
              label: this.resolvedRowLabel(r), row_type: r.row_type,
              entity_ids: (r.row_type === 'separator' || r.row_type === 'summary') ? [] : r.entity_ids,
              formula: (r.row_type === 'separator' || r.row_type === 'summary') ? '' : r.formula,
              formula_unit: r.row_type === 'formula' ? (r.formula_unit || '').trim() : '',
              aggregation: r.row_type === 'summary'
                ? (r.aggregation === 'avg' ? 'avg' : 'sum')
                : ((r.row_type === 'entity' || r.row_type === 'group') ? (r.aggregation || 'auto') : 'auto'),
              bold: !!r.bold,
              hidden: !!r.hidden,
              show_label: r.row_type === 'separator' ? !!r.show_label : false,
              accent: r.row_type === 'formula' ? !!r.accent : false,
              percent_of_total: (r.row_type === 'entity' || r.row_type === 'group') ? !!r.percent_of_total : false,
              hide_if_empty: (r.row_type === 'entity' || r.row_type === 'group') ? !!r.hide_if_empty : false,
            })),
            style: this.style,
          };
          try {
            const url = TABLE_ID ? `${BASE}/tables/${TABLE_ID}` : `${BASE}/tables`;
            const res = await fetch(url, {
              method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
            });
            if (!res.ok) {
              const err = await res.json().catch(() => ({}));
              appAlert(err.detail || 'Speichern fehlgeschlagen.');
              return;
            }
            const data = await res.json();
            if (!TABLE_ID) {
              window.location.href = `${BASE}/tables/${data.id}`;
              return;
            }
            this.savedMessage = '✓ Gespeichert';
            this.editing = false;
            setTimeout(() => { this.savedMessage = ''; }, 2000);
          } finally {
            this.saving = false;
          }
        },

        async deleteTable() {
          if (!await appConfirm('Diese Tabelle wirklich löschen?', {danger: true})) return;
          await fetch(`${BASE}/tables/${TABLE_ID}/delete`, {method: 'POST'});
          window.location.href = `${BASE}/tables`;
        },

        // Verwirft unsaved Änderungen — dieselbe Begründung wie cancelEdit()
        // in chart_editor.js: ohne TABLE_ID gibt es keinen gespeicherten
        // Stand, sonst neu laden statt jedes reaktive Feld einzeln
        // zurückzusetzen.
        cancelEdit() {
          if (!TABLE_ID) { window.location.href = `${BASE}/tables`; return; }
          window.location.reload();
        },

        // Position/Höhe EINES Buchstaben-Badges in .tbl-letters-gutter — aus
        // letterPositions (von syncLetterPositions() gemessen), nicht
        // errechnet. Ohne Eintrag (z. B. bevor die erste Messung lief) bleibt
        // der Slot unsichtbar statt bei 0/0 zu "kleben".
        letterSlotStyle(rowUid) {
          const p = this.letterPositions[rowUid];
          if (!p) return 'display:none;';
          return `top:${p.top}px;height:${p.height}px;`;
        },
        // Liest die TATSÄCHLICH gerenderten Zeilen-Positionen der echten
        // Tabelle (x-ref="previewTable") aus und überträgt sie unverändert
        // auf die Badges nebenan — siehe ausführlichen Kommentar bei
        // .tbl-letters-gutter im CSS oben, warum eine Messung statt eines
        // per CSS nachgebauten Zwillings die robuste Wahl ist. Läuft über
        // getBoundingClientRect()-Differenzen (nicht offsetTop), weil
        // offsetTop vom nächsten POSITIONIERTEN Vorfahren abhängt, der hier
        // nicht zuverlässig die Tabelle selbst ist.
        syncLetterPositions() {
          const table = this.$refs.previewTable;
          if (!table || !table.isConnected) return;
          const tableRect = table.getBoundingClientRect();
          const positions = {};
          table.querySelectorAll('tr[data-row-uid]').forEach(tr => {
            // Ausgeblendete Zeilen (x-show="!row.hidden") stehen weiter im
            // DOM (display:none), liefern aber ein Nullrechteck — ohne
            // diesen Filter würde ihr Badge fälschlich oben an der Tabelle
            // "kleben" statt (über letterSlotStyle()) unsichtbar zu bleiben.
            if (tr.offsetParent === null) return;
            const r = tr.getBoundingClientRect();
            positions[tr.dataset.rowUid] = {top: r.top - tableRect.top, height: r.height};
          });
          this.letterPositions = positions;
          this.gutterHeight = tableRect.height;
          // Höhe der Gruppen-Kopfzeile für die zweite (Zeitraum-)Kopfzeile
          // bei "Header fixieren" — siehe Kommentar bei
          // .tbl-style-sticky-header tr.tbl-header-row th oben.
          const groupRow = table.querySelector('tr.tbl-group-header-row');
          const groupH = (groupRow && groupRow.offsetParent !== null) ? groupRow.getBoundingClientRect().height : 0;
          table.style.setProperty('--tbl-group-header-h', groupH + 'px');
        },

        init() {
          this.load();
          // .tbl-columns/.tbl-rows stecken in einem x-if="editing" — bei
          // jedem Wechsel auf "editing" wird der Container neu erzeugt (der
          // dragBound-Marker geht dabei verloren), deshalb hier neu binden,
          // nicht nur einmalig beim ersten Laden.
          this.$watch('editing', v => { if (v) this.$nextTick(() => { setupColumnDrag(); setupRowDrag(); this.syncLetterPositions(); }); });
          if (this.editing) this.$nextTick(() => { setupColumnDrag(); setupRowDrag(); });
          // ResizeObserver statt einzelner Watcher auf rows/columns/style —
          // jede Änderung, die die Zeilenhöhen der echten Tabelle beeinflussen
          // könnte (Zeile hinzugefügt/entfernt, Dichte/Rahmen umgeschaltet,
          // geladene Werte ändern die Zellenbreite/Umbruch, Schriftgrößen-
          // Skalierung), ändert zwangsläufig auch deren Gesamthöhe — genau
          // das beobachtet der ResizeObserver, ganz ohne jede einzelne
          // mögliche Ursache selbst auflisten zu müssen.
          this.$nextTick(() => {
            const table = this.$refs.previewTable;
            if (table && window.ResizeObserver) {
              new ResizeObserver(() => this.syncLetterPositions()).observe(table);
            }
            this.syncLetterPositions();
          });
        },
      };
    }
