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
  function cellText(cell, decimals) {
    if (!cell) return '–';
    if (cell.error) return 'Fehler';
    if (cell.value == null) return '–';
    const decimalsInt = decimals && decimals !== 'auto' ? parseInt(decimals, 10) : null;
    return fmtNum(cell.value, decimalsInt) + (cell.unit ? ' ' + cell.unit : '');
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

  // Berechnet values[colIndex][rowIndex] = {value, unit} | {error:true} | null
  // — ein Request je Spalte an /api/query-multi (alle in dieser Spalte
  // gebrauchten Entitäten auf einmal), Formel-Zeilen danach in Zeilen-
  // Reihenfolge ausgewertet (Verkettung: eine Formel darf eine bereits
  // berechnete FRÜHERE Formel-Zeile referenzieren).
  async function computeValues(base, columns, rows) {
    const letters = rowLetters(rows);
    const entityRows = rows.filter(r => r.row_type === 'entity' || r.row_type === 'group');
    const allEntityIds = [...new Set(entityRows.flatMap(r => r.entity_ids))];
    const values = columns.map(() => new Array(rows.length).fill(null));

    for (let ci = 0; ci < columns.length; ci++) {
      if (!allEntityIds.length) continue;
      const col = columns[ci];
      const params = new URLSearchParams({
        entity_ids: allEntityIds.join(','), range: col.range_key, offset: String(col.offset || 0),
        continuous: 'false', year_over_year: String(!!col.year_over_year),
      });
      let data;
      try {
        const res = await fetch(`${base}/api/query-multi?${params}`);
        data = await res.json();
      } catch (e) {
        data = {series: []};
      }
      const byEntity = {};
      (data.series || []).forEach(s => { byEntity[s.entity_id] = s; });
      rows.forEach((row, ri) => {
        if (row.row_type === 'formula' || row.row_type === 'separator') return;
        const members = row.entity_ids.map(id => byEntity[id]).filter(Boolean);
        if (!members.length) { values[ci][ri] = null; return; }
        const aggregation = row.aggregation || 'auto';
        const memberValues = members.map(s => memberValueFor(s, aggregation));
        let value;
        if (aggregation === 'min' || aggregation === 'max') {
          // Ein Mitglied ganz ohne Datenpunkte fließt hier NICHT als 0 ein
          // (anders als bei avg/sum/auto unten) — sonst würde eine Entität
          // ohne Daten im Zeitraum fälschlich zum globalen Minimum. Sind ALLE
          // Mitglieder ohne Daten, bleibt die Zelle leer statt 0.
          const valid = memberValues.filter(v => v != null);
          if (!valid.length) { values[ci][ri] = null; return; }
          value = aggregation === 'min' ? Math.min(...valid) : Math.max(...valid);
        } else {
          // Wie bisher: eine Gruppen-Zeile summiert die (je nach Modus schon
          // aggregierten) Mitglieder-Werte zusätzlich noch einmal auf.
          value = memberValues.reduce((a, v) => a + (v || 0), 0);
        }
        const memberUnits = [...new Set(members.map(member => member.unit || ''))];
        values[ci][ri] = {value, unit: memberUnits.length === 1 ? memberUnits[0] : ''};
      });
    }

    columns.forEach((col, ci) => {
      rows.forEach((row, ri) => {
        if (row.row_type !== 'formula') return;
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
      });
    });

    return values;
  }

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
    return classes.join(' ');
  }

  return {evalFormula, inheritedFormulaUnit, fmtNum, cellText, rowLetters, computeValues, styleClasses};
})();
