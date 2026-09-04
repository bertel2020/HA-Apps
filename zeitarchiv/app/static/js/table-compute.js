// Geteilte Rechenlogik für Vergleichstabellen (Konzept "Offene Punkte") —
// von table_editor.html (volle Bearbeitung) UND dashboard-tiles.js (kompakte
// Dashboard-Kachel) genutzt, damit beide garantiert dieselben Zahlen zeigen
// und ein Fix an einer Stelle nicht an der anderen vergessen wird. Reiner
// Berechnungscode, keine DOM-Abhängigkeit — arbeitet auf einfachen
// Columns-/Rows-Arrays (Index statt Alpine-uid), nicht auf dem reaktiven
// Zustand des Editors.
window.TableCompute = (() => {
  // Akzeptiertes Dezimaltrennzeichen für Zahl-Literale in Formeln, aus dem
  // zentralen Oberflächenformat abgeleitet (static/js/number-format.js) statt
  // hart auf "," verdrahtet — ein deutschsprachiger Nutzer tippt in einer
  // Formel-Konstante natürlicherweise Komma (z. B. "A * 3,5"), das bisher am
  // Parser scheiterte ("Unerwartetes Zeichen ','"). "." bleibt IMMER zusätzlich
  // gültig (unabhängig vom Trennzeichen der aktuellen Sprache), da Formeln als
  // Mini-Code gelesen werden, nicht als natürlichsprachige Zahl — keine
  // Tausendertrennung hier, das wäre in einer Formel ohnehin nicht sinnvoll.
  const FORMULA_DECIMAL_SEP = (window.NumberFormat && window.NumberFormat.DECIMAL_SEP) || ',';

  // Sicherer, kleiner Formel-Interpreter statt eval()/Function() — Formeln
  // stehen zwar nur in der eigenen Datenbank (kein Angriffsvektor von
  // außen), ein handgebauter Parser bleibt trotzdem die sauberere Wahl
  // gegenüber beliebigem Code-Eval für etwas so Einfaches wie "A / B * 100".
  // Unterstützt +, -, *, /, Klammern, Zeilen-Buchstaben (A-Z) und Zahlen.
  function evalFormula(expr, scope) {
    const s = (expr || '').replace(/\s+/g, '').toUpperCase();
    if (!s) throw new Error('Leere Formel');
    let pos = 0;
    const peek = () => s[pos];
    function parseExpr() {
      let v = parseTerm();
      while (peek() === '+' || peek() === '-') {
        const op = s[pos++];
        const rhs = parseTerm();
        v = op === '+' ? v + rhs : v - rhs;
      }
      return v;
    }
    function parseTerm() {
      let v = parseFactor();
      while (peek() === '*' || peek() === '/') {
        const op = s[pos++];
        const rhs = parseFactor();
        v = op === '*' ? v * rhs : v / rhs;
      }
      return v;
    }
    function parseFactor() {
      if (peek() === '-') { pos++; return -parseFactor(); }
      if (peek() === '(') {
        pos++;
        const v = parseExpr();
        if (peek() !== ')') throw new Error('Schließende Klammer fehlt');
        pos++;
        return v;
      }
      if (peek() && /[A-Z]/.test(peek())) {
        const letter = s[pos++];
        if (!(letter in scope)) throw new Error(`Zeile ${letter} ist hier nicht verfügbar`);
        const v = scope[letter];
        if (v == null) throw new Error(`Zeile ${letter} hat keinen Wert`);
        return v;
      }
      const start = pos;
      while (peek() && (/[0-9.]/.test(peek()) || peek() === FORMULA_DECIMAL_SEP)) pos++;
      if (pos === start) throw new Error(`Unerwartetes Zeichen "${peek() ?? ''}"`);
      const numText = s.slice(start, pos);
      return parseFloat(FORMULA_DECIMAL_SEP === '.' ? numText : numText.replace(FORMULA_DECIMAL_SEP, '.'));
    }
    const result = parseExpr();
    if (pos < s.length) throw new Error('Unerwarteter Rest in der Formel');
    if (!Number.isFinite(result)) throw new Error('Ergebnis ist nicht endlich (z. B. Division durch 0)');
    return result;
  }

  // Zentral in static/js/number-format.js (window.NumberFormat) — dieselbe
  // Formatierung wie überall sonst in der Oberfläche, siehe Kommentar dort.
  const fmtNum = (window.NumberFormat && window.NumberFormat.fmt) || (v => String(v));

  // decimals: der Spalten-eigene "Nachkommastellen"-String ("auto"/"0"/"1"/…,
  // dieselbe Konvention wie das entity-eigene Feld, siehe _TableColumnBody in
  // main.py) — rein optisch, fließt nirgends in eine Berechnung ein. Ohne
  // Angabe (ältere, vor dieser Funktion gespeicherte Tabellen) unverändert
  // NumberFormat.fmt()s Default (bis zu 4 signifikante Stellen).
  function cellValueText(cell, decimals, explicitMissing = false) {
    if (!cell) return explicitMissing ? 'Keine Daten' : '–';
    if (cell.error) return 'Fehler';
    if (cell.value == null) return explicitMissing ? 'Keine Daten' : '–';
    const decimalsInt = decimals && decimals !== 'auto' ? parseInt(decimals, 10) : null;
    return fmtNum(cell.value, decimalsInt);
  }

  function cellUnit(cell) {
    return cell && !cell.error && cell.value != null ? (cell.unit || '') : '';
  }

  function cellNumberParts(cell, decimals, explicitMissing = false) {
    const text = cellValueText(cell, decimals, explicitMissing);
    if (!cell || cell.error || cell.value == null) return {whole: text, separator: '', fraction: ''};
    const separator = (window.NumberFormat && window.NumberFormat.DECIMAL_SEP) || ',';
    const pos = text.lastIndexOf(separator);
    if (pos < 0) return {whole: text, separator: '', fraction: ''};
    return {whole: text.slice(0, pos), separator, fraction: text.slice(pos + separator.length)};
  }

  function cellText(cell, decimals, options = {}) {
    const value = cellValueText(cell, decimals, !!options.explicitMissing);
    const unit = options.showUnits === false ? '' : cellUnit(cell);
    return value + (unit ? ' ' + unit : '');
  }

  function isComparisonColumn(col) {
    return !!(col && ((col.offset || 0) < 0 || col.year_over_year));
  }

  // Zugehörige Vergleichsspalte für Tag/Monat/Jahr: gleicher Zeitraum,
  // aber mit Versatz oder Vorjahresmodus. Bewusst nur vorwärts suchen,
  // damit die Tabellenreihenfolge die visuelle Paarung bestimmt und die
  // Abweichung unter dem aktuellen statt unter dem historischen Wert steht.
  function comparisonIndexForBase(columns, baseIndex) {
    const base = columns[baseIndex];
    if (!base || isComparisonColumn(base)) return -1;
    for (let i = baseIndex + 1; i < columns.length; i++) {
      const candidate = columns[i];
      if (candidate.range_key === base.range_key && isComparisonColumn(candidate)) return i;
    }
    return -1;
  }

  function deviationText(baseCell, comparisonCell) {
    if (!baseCell || !comparisonCell || baseCell.error || comparisonCell.error ||
        baseCell.value == null || comparisonCell.value == null) return '';
    // comparisonValue (falls vorhanden): fairer, auf denselben Zeitabstand
    // gekappter Vergleichswert (same_elapsed, siehe memberComparisonValueFor()
    // unten) statt des vollen, immer angezeigten Zellwerts — sonst verglicht
    // z. B. "Tag bisher" unfair gegen den ganzen "Vortag" statt gegen "Vortag
    // bis zur selben Uhrzeit". null (Gruppen-/Formel-/Summenzeilen, oder wenn
    // der Server keinen same_elapsed-Vergleich geliefert hat): unverändert
    // der volle Wert wie bisher.
    const comparisonBase = comparisonCell.comparisonValue != null ? comparisonCell.comparisonValue : comparisonCell.value;
    if (comparisonBase === 0) return '';
    const percent = (baseCell.value - comparisonBase) / Math.abs(comparisonBase) * 100;
    if (!Number.isFinite(percent)) return '';
    const rounded = Math.abs(percent) >= 10 ? Math.round(percent) : Math.round(percent * 10) / 10;
    return `${rounded > 0 ? '+' : ''}${fmtNum(rounded, Number.isInteger(rounded) ? 0 : 1)} %`;
  }

  // row.percent_of_total: zeigt statt des absoluten Zellwerts den Anteil an
  // der Summe aller Entität-/Gruppen-Zeilen DERSELBEN Spalte im selben
  // Abschnitt (zwischen zwei Trennlinien bzw. Tabellenanfang/-ende) — anders
  // als bei der Summenzeile (nur Zeilen OBERHALB) hier bewusst der GANZE
  // Abschnitt, weil ein Anteil sich auf alle Geschwister-Zeilen bezieht, nicht
  // nur auf vorherige. memberCells kommt vorberechnet vom Aufrufer (uid- bzw.
  // index-basiert je nach Kontext, siehe sectionMemberCells() in
  // table_editor.html bzw. dashboard-tiles.js), damit dieser gemeinsame Kern
  // nicht selbst durchs rows-Array laufen muss.
  function percentOfTotalCell(cell, memberCells) {
    if (!cell || cell.error || cell.value == null) return cell;
    const total = memberCells.reduce((a, c) => a + (c && c.value != null ? c.value : 0), 0);
    if (!total) return {value: null, unit: '%'};
    return {value: (cell.value / total) * 100, unit: '%'};
  }

  // row.heatmap: Zellhintergrund nach Wert innerhalb der eigenen Zeile
  // (heller = niedrig, kräftiger = hoch) — range kommt vom Aufrufer
  // (Minimum/Maximum der sichtbaren Zellen DERSELBEN Zeile, siehe
  // rowHeatmapRange() in table_editor.html). --accent-bar (die zweite
  // Akzentfarbe, sonst für Balkendiagramme) statt --accent-line, damit eine
  // "heiße" Zelle nicht wie ein aktiver An/Aus-Chip (dort --accent-line)
  // aussieht. Deckelung bei 60%, damit der Zahlentext bei einem Maximalwert
  // noch lesbar bleibt.
  function heatmapStyle(cell, range) {
    if (!cell || cell.error || cell.value == null || !range || range.max === range.min) return '';
    const t = (cell.value - range.min) / (range.max - range.min);
    const pct = Math.round(Math.max(0, Math.min(1, t)) * 60);
    return `background:color-mix(in srgb, var(--accent-bar) ${pct}%, var(--surface));`;
  }

  // Buchstaben-Kürzel je Datenzeile (A, B, C, … Z, dann wieder von vorn) —
  // Trennlinien sind rein optisch und verbrauchen deshalb bewusst keinen
  // Buchstaben. Dieselbe Zuordnung wie rowLetters in table_editor.html, hier
  // als reine Funktion über ein rows-Array statt eines Alpine-Getters.
  function rowLetters(rows) {
    let dataIndex = 0;
    return rows.map(r => {
      if (r.row_type === 'separator') return null;
      return String.fromCharCode(65 + ((dataIndex++) % 26));
    });
  }

  // Leere Formel-Einheit = automatische Übernahme von der ersten in der
  // Formel referenzierten Zeile, die eine Einheit besitzt. Dimensionslose
  // oder umgerechnete Ergebnisse können im Editor explizit überschrieben
  // werden (z. B. "%" für A / B * 100).
  function inheritedFormulaUnit(expr, unitScope) {
    const references = (expr || '').toUpperCase().match(/[A-Z]/g) || [];
    for (const letter of references) {
      if (unitScope[letter]) return unitScope[letter];
    }
    return '';
  }

  // Aggregiert die Buckets EINER Entität innerhalb einer Spalte (Zeitraum) zu
  // einem Zahlenwert, je nach gewählter Zeilen-Aggregation. "auto" ist das
  // historische Verhalten (Zähler/Schalter -> Summe, sonst Durchschnitt der
  // Bucket-Werte) und bleibt für bestehende Tabellen unverändert. min/max
  // nutzen das echte Bucket-Minimum/-Maximum (server-seitig aus den
  // Rohwerten berechnet, siehe storage/query.py `min_value`/`max_value`),
  // NICHT das Minimum/Maximum der Bucket-DURCHSCHNITTE — sonst würde z. B.
  // eine kurze Temperaturspitze innerhalb eines Stunden-Buckets im
  // Tages-/Monats-Maximum verschwinden. Kein Datenpunkt im Zeitraum: bei
  // avg/sum/auto weiterhin 0 (bisheriges Verhalten, wichtig für Gruppen-
  // Summen aus mehreren Mitgliedern), bei min/max stattdessen null — 0 wäre
  // dort kein neutrales Element, sondern ein plausibler, aber falscher Wert.
  function memberValueFor(series, aggregation) {
    // Der Tabellen-Batch-Endpunkt verdichtet die Chart-Punkte bereits auf
    // genau diese fünf Werte. Der points-Fallback hält den Rechenkern zugleich
    // mit älteren/anderen Aufrufern kompatibel.
    if (series.aggregates && Object.prototype.hasOwnProperty.call(series.aggregates, aggregation)) {
      return series.aggregates[aggregation];
    }
    const pts = series.points || [];
    if (aggregation === 'min' || aggregation === 'max') {
      if (!pts.length) return null;
      const key = aggregation === 'min' ? 'min' : 'max';
      const vals = pts.map(p => (p[key] != null ? p[key] : p.value)).filter(v => v != null);
      return vals.length ? Math[aggregation](...vals) : null;
    }
    if (!pts.length) return 0;
    const total = pts.reduce((a, p) => a + (p.value || 0), 0);
    if (aggregation === 'sum') return total;
    if (aggregation === 'avg') return total / pts.length;
    const isSum = series.aggregation_type === 'counter' || series.aggregation_type === 'switch';
    return isSum ? total : total / pts.length;
  }

  // Fairer Vergleichswert für eine same_elapsed-Spalte (Vortag/Vorwoche/…
  // neben Tag/Woche/…, siehe deviationText()) — server-seitig auf denselben
  // Zeitabstand vom Periodenanfang gekappt wie die noch laufende aktuelle
  // Periode (comparison_aggregates, siehe _table_comparison_aggregates() in
  // api_routes.py). null, wenn der Server keinen same_elapsed-Vergleich
  // geliefert hat — deviationText() fällt dann auf den vollen Wert zurück.
  function memberComparisonValueFor(series, aggregation) {
    if (series.comparison_aggregates
        && Object.prototype.hasOwnProperty.call(series.comparison_aggregates, aggregation)) {
      return series.comparison_aggregates[aggregation];
    }
    return null;
  }

  // Berechnet values[colIndex][rowIndex] = {value, unit} | {error:true} | null
  // — ein gemeinsamer Request an /api/query-table für alle Spalten und
  // gebrauchten Entitäten, Formel-Zeilen danach in Zeilen-
  // Reihenfolge ausgewertet (Verkettung: eine Formel darf eine bereits
  // berechnete FRÜHERE Formel-Zeile referenzieren). windowStarts[colIndex]
  // ist der von derselben Anfrage mitgelieferte, tatsächlich aufgelöste
  // Fensterbeginn (Sekunden, siehe query.py `window_start`) — Grundlage für
  // die Beschriftungs-Platzhalter ({jahr}, {monat}, …, siehe resolveLabel()
  // unten). Bleibt null, solange die Spalte keine Entität/Gruppen-Zeile
  // referenziert (kein Request nötig) — resolveLabel() liefert dann
  // unverändert die rohe Beschriftung zurück.
  async function computeValues(base, columns, rows) {
    const letters = rowLetters(rows);
    const entityRows = rows.filter(r => r.row_type === 'entity' || r.row_type === 'group');
    const allEntityIds = [...new Set(entityRows.flatMap(r => r.entity_ids))];
    const values = columns.map(() => new Array(rows.length).fill(null));
    const windowStarts = columns.map(() => null);

    let columnData = [];
    if (allEntityIds.length) {
      try {
        const res = await fetch(`${base}/api/query-table`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            entity_ids: allEntityIds,
            columns: columns.map(col => ({
              range_key: col.range_key,
              offset: col.offset || 0,
              year_over_year: !!col.year_over_year,
              // "Gleicher Zeitpunkt"-Vergleich: eine Vergleichsspalte (Versatz<0)
              // wird auf denselben Abstand vom Periodenanfang gekappt wie die
              // noch laufende aktuelle Periode — aber NUR, wenn diese Tabelle
              // überhaupt eine laufende Basis-Spalte (Versatz 0, kein
              // Vorjahresvergleich) mit demselben Zeitraum-Typ enthält. Ohne
              // Basis-Spalte bleibt die Vergleichsspalte unverändert vollständig
              // (z. B. eine reine "Vormonat"-Spalte ohne "Monat" daneben).
              same_elapsed: (col.offset || 0) < 0 && !col.year_over_year
                && columns.some(c => c.range_key === col.range_key && (c.offset || 0) === 0 && !c.year_over_year),
            })),
          }),
        });
        if (!res.ok) throw new Error(`Tabellenabfrage fehlgeschlagen (${res.status})`);
        columnData = (await res.json()).columns || [];
      } catch (e) {
        columnData = [];
      }
    }

    columns.forEach((col, ci) => {
      const data = columnData[ci] || {series: []};
      windowStarts[ci] = data.window_start ?? null;
      const byEntity = {};
      (data.series || []).forEach(s => { byEntity[s.entity_id] = s; });
      rows.forEach((row, ri) => {
        if (row.row_type === 'formula' || row.row_type === 'separator' || row.row_type === 'summary') return;
        const members = row.entity_ids.map(id => byEntity[id]).filter(Boolean);
        if (!members.length) { values[ci][ri] = null; return; }
        const aggregation = row.aggregation || 'auto';
        const memberValues = members.map(s => memberValueFor(s, aggregation));
        const memberComparisonValues = members.map(s => memberComparisonValueFor(s, aggregation));
        let value;
        let comparisonValue;
        if (aggregation === 'min' || aggregation === 'max') {
          // Ein Mitglied ganz ohne Datenpunkte fließt hier NICHT als 0 ein
          // (anders als bei avg/sum/auto unten) — sonst würde eine Entität
          // ohne Daten im Zeitraum fälschlich zum globalen Minimum. Sind ALLE
          // Mitglieder ohne Daten, bleibt die Zelle leer statt 0.
          const valid = memberValues.filter(v => v != null);
          if (!valid.length) { values[ci][ri] = null; return; }
          value = aggregation === 'min' ? Math.min(...valid) : Math.max(...valid);
          const validComparison = memberComparisonValues.filter(v => v != null);
          comparisonValue = validComparison.length
            ? (aggregation === 'min' ? Math.min(...validComparison) : Math.max(...validComparison))
            : null;
        } else {
          // Wie bisher: eine Gruppen-Zeile summiert die (je nach Modus schon
          // aggregierten) Mitglieder-Werte zusätzlich noch einmal auf.
          value = memberValues.reduce((a, v) => a + (v || 0), 0);
          comparisonValue = memberComparisonValues.every(v => v == null)
            ? null
            : memberComparisonValues.reduce((a, v) => a + (v || 0), 0);
        }
        const memberUnits = [...new Set(members.map(member => member.unit || ''))];
        values[ci][ri] = {value, unit: memberUnits.length === 1 ? memberUnits[0] : '', comparisonValue};
      });
    });

    columns.forEach((col, ci) => {
      rows.forEach((row, ri) => {
        if (row.row_type === 'formula') {
          const scope = {};
          const unitScope = {};
          for (let j = 0; j < ri; j++) {
            if (!letters[j]) continue;
            const cell = values[ci][j];
            scope[letters[j]] = cell ? cell.value : null;
            unitScope[letters[j]] = cell ? (cell.unit || '') : '';
          }
          const unit = (row.formula_unit || '').trim() || inheritedFormulaUnit(row.formula, unitScope);
          try {
            values[ci][ri] = {value: evalFormula(row.formula, scope), unit};
          } catch (e) {
            values[ci][ri] = {value: null, unit, error: true};
          }
        } else if (row.row_type === 'summary') {
          // Summe/Durchschnitt aller Entität-/Gruppen-Zeilen SEIT DER LETZTEN
          // TRENNLINIE (nicht ab Tabellenanfang) — dieselbe Abschnitts-Logik
          // wie sectionMemberCells() in table_editor.html, hier nur über den
          // Index statt uid. Absichtlich ohne Formel-/andere Summenzeilen,
          // sonst würde ein bereits aufsummierter Wert doppelt gezählt.
          let sectionStart = 0;
          for (let j = ri - 1; j >= 0; j--) {
            if (rows[j].row_type === 'separator') { sectionStart = j + 1; break; }
          }
          const memberCells = [];
          for (let j = sectionStart; j < ri; j++) {
            if (rows[j].row_type !== 'entity' && rows[j].row_type !== 'group') continue;
            const cell = values[ci][j];
            if (cell && cell.value != null) memberCells.push(cell);
          }
          if (!memberCells.length) { values[ci][ri] = null; return; }
          const total = memberCells.reduce((a, c) => a + c.value, 0);
          const value = row.aggregation === 'avg' ? total / memberCells.length : total;
          const units = [...new Set(memberCells.map(c => c.unit || ''))];
          values[ci][ri] = {value, unit: units.length === 1 ? units[0] : ''};
        }
      });
    });

    return {values, windowStarts};
  }

  // Monatsnamen/-kürzel für resolveLabel() unten — dieselbe Wortwahl wie der
  // Rest der Oberfläche (z. B. previousYearPeriodLabel in entity_detail.html),
  // hier nur als Kalender-Vokabular statt Zeitraum-Namen.
  const MONTH_NAMES = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
  const MONTH_NAMES_SHORT = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];

  // ISO-8601-Kalenderwoche (Woche 1 = die Woche mit dem ersten Donnerstag des
  // Jahres) — Standardalgorithmus über den nächsten Donnerstag derselben
  // Woche, damit Jahreswechsel innerhalb einer Woche (z. B. 30./31. Dezember)
  // korrekt der Woche des jeweils überwiegenden Jahres zugeordnet werden.
  function isoWeekNumber(date) {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - dayNum + 3);
    const firstThursday = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
    const diff = d - firstThursday;
    return 1 + Math.round(diff / (7 * 24 * 3600 * 1000));
  }

  // Bekannte Platzhalter für Spalten-Beschriftungen — siehe LABEL_VARIABLES
  // unten (Einfügehilfe im Editor) für die Anzeige-Beschriftung je Token.
  // Nur diese Namen werden ersetzt; ein Tippfehler wie "{jhar}" matcht das
  // Muster nicht und bleibt deshalb sichtbar im Text stehen, statt
  // kommentarlos zu verschwinden.
  const LABEL_TOKEN_PATTERN = /\{(jahr|jahr_kurz|quartal|monat|monat_kurz|monat_nr|woche|tag|dekade)\}/g;

  // Ersetzt Platzhalter in einer Spalten-Beschriftung durch Kalenderwerte des
  // TATSÄCHLICH aufgelösten Zeitraums dieser Spalte (windowStartEpoch, aus
  // computeValues() oben) — nicht des heutigen Datums. Eine Spalte "Jahr,
  // Versatz -1" mit Beschriftung "{jahr}" zeigt dadurch automatisch das
  // Vorjahr, nächstes Jahr automatisch das dann aktuelle Vorjahr, ganz ohne
  // manuelles Nachpflegen. Labels ohne "{" sowie windowStartEpoch == null
  // (z. B. bevor computeValues() das erste Mal gelaufen ist) geben die
  // Beschriftung unverändert zurück.
  function resolveLabel(label, windowStartEpoch) {
    if (!label || !label.includes('{') || windowStartEpoch == null) return label;
    const d = new Date(windowStartEpoch * 1000);
    const tokenValues = {
      jahr: String(d.getFullYear()),
      jahr_kurz: String(d.getFullYear()).slice(-2),
      // Bewusst nur die Ziffer, kein "Q"-Präfix — konsistent mit den übrigen
      // numerischen Token (woche/tag/monat_nr). Wer "Q3" will, schreibt
      // "Q{quartal}"; mit eingebautem Präfix würde genau das zu "QQ3" führen.
      quartal: String(Math.floor(d.getMonth() / 3) + 1),
      monat: MONTH_NAMES[d.getMonth()],
      monat_kurz: MONTH_NAMES_SHORT[d.getMonth()],
      monat_nr: String(d.getMonth() + 1).padStart(2, '0'),
      woche: String(isoWeekNumber(d)),
      // Zweistellig wie monat_nr (01 statt 1) — konsistent mit dem
      // "DD.MM."-Format, in dem Tag/Monat sonst überall in der App erscheinen.
      tag: String(d.getDate()).padStart(2, '0'),
      dekade: `${Math.floor(d.getFullYear() / 10) * 10}er`,
    };
    return label.replace(LABEL_TOKEN_PATTERN, (match, token) => tokenValues[token]);
  }

  // Einfügehilfe im Editor (siehe table_editor.html) — Token + Anzeigename,
  // in der Reihenfolge, in der sie im Popover erscheinen. Eigene Liste statt
  // Object.keys() auf tokenValues in resolveLabel(), weil die Anzeige-
  // Reihenfolge (grob → fein) bewusst anders ist als der Ersetzungs-Code sie
  // bräuchte.
  const LABEL_VARIABLES = [
    ['jahr', 'Jahr'], ['jahr_kurz', 'Jahr (kurz)'], ['quartal', 'Quartal'],
    ['monat', 'Monat'], ['monat_kurz', 'Monat (kurz)'], ['monat_nr', 'Monat (Nr.)'],
    ['woche', 'Woche'], ['tag', 'Tag'], ['dekade', 'Dekade'],
  ];

  // CSS-Klassen für die Darstellungs-Optionen (Zebra/Rahmen/Dichte/Kopfzeile)
  // — dieselbe Zuordnung für die volle Tabellen-Seite UND die Dashboard-
  // Kachel, damit eine gespeicherte Tabelle an beiden Stellen gleich aussieht.
  // Die zugehörigen CSS-Regeln selbst leben lokal in jeder Seite (entities.html/
  // table_editor.html, siehe .tbl-style-* dort) — hier nur die Namenszuordnung.
  function styleClasses(style) {
    style = style || {};
    const classes = [];
    if (style.zebra) classes.push('tbl-style-zebra');
    if (style.borders === 'grid') classes.push('tbl-style-border-grid');
    else if (style.borders === 'none') classes.push('tbl-style-border-none');
    if (style.density === 'compact') classes.push('tbl-style-compact');
    if (style.header_accent) classes.push('tbl-style-header-accent');
    if (style.first_col_accent) classes.push('tbl-style-first-col-accent');
    if (style.first_col_bold) classes.push('tbl-style-first-col-bold');
    if (style.sticky_first_col) classes.push('tbl-style-sticky-first-col');
    if (style.sticky_header) classes.push('tbl-style-sticky-header');
    if (style.comparison_columns) classes.push('tbl-style-comparison-columns');
    if (style.align_units) classes.push('tbl-style-align-units');
    if (style.small_units) classes.push('tbl-style-small-units');
    if (style.align_numbers) classes.push('tbl-style-align-numbers');
    // Nur bei Abweichung vom Standard eine Klasse — Standard ist bei beiden
    // rechtsbündig (dieselbe Regel wie bisher fest im CSS), spart in den
    // meisten Fällen die Klasse.
    if (style.header_align && style.header_align !== 'right') classes.push(`tbl-style-header-align-${style.header_align}`);
    if (style.value_align && style.value_align !== 'right') classes.push(`tbl-style-value-align-${style.value_align}`);
    if (style.equal_value_cols) classes.push('tbl-style-equal-cols');
    return classes.join(' ');
  }

  return {
    evalFormula, inheritedFormulaUnit, fmtNum, cellValueText, cellUnit, cellNumberParts, cellText,
    isComparisonColumn, comparisonIndexForBase, deviationText, percentOfTotalCell, heatmapStyle,
    rowLetters, computeValues, styleClasses,
    resolveLabel, LABEL_VARIABLES,
  };
})();
