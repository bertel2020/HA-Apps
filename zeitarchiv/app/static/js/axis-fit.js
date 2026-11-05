// Linker Rand eines Charts aus der tatsächlich gemessenen Breite der Y-Achsen-Beschriftungen.
//
// containLabel von ECharts misst die breiteste Beschriftung etwas zu knapp: bei langen Werten mit
// Einheit („36,8 ct/kWh“) fehlte links eine Ziffer, ein fester Zusatzrand (left: 24) war nur eine
// Notlösung für genau die Länge, an der es zuletzt auffiel. Hier wird stattdessen nach jedem setOption()
// die Breite der wirklich angezeigten Beschriftungen mit der tatsächlichen Schrift gemessen und der Rand
// daraus gesetzt. Nach dem Laden der Schriften wird nochmals gemessen, denn davor stimmt die Breite
// nicht zwingend.
//
// Greift nur bei einem Diagramm mit genau einem Raster und genau einer linken Werte-Achse (höchstens eine
// rechte, deren Rand dann ebenfalls gemessen wird); alles andere (mehrere Raster wie die Zustands-Panels,
// zwei Achsen auf einer Seite) bleibt bei containLabel.
window.AxisFit = (() => {
  const ctx = document.createElement('canvas').getContext('2d');
  // Abstand Beschriftung–Achse (8, ECharts-Standard) plus etwas Luft.
  const EXTRA = 12;
  // Breiter als dieser Anteil der Diagrammbreite ergibt keinen sinnvollen Rand mehr.
  const MAX_SHARE = 0.4;

  function neededMargins(chart) {
    const widths = {left: 0, right: 0};
    const counts = {left: 0, right: 0};
    for (const axisModel of chart.getModel().queryComponents({mainType: 'yAxis'})) {
      const axis = axisModel.axis;
      if (!axis || !(axis.position in widths)) continue;
      counts[axis.position] += 1;
      const labelModel = axisModel.getModel('axisLabel');
      if (axisModel.get('show') === false || labelModel.get('show') === false) continue;
      ctx.font = labelModel.getFont();
      for (const label of axis.getViewLabels()) {
        widths[axis.position] = Math.max(widths[axis.position], ctx.measureText(label.formattedLabel).width);
      }
    }
    // Mehrere Achsen auf einer Seite (versetzt) bleiben bei containLabel.
    if (counts.left !== 1 || counts.right > 1) return null;
    // Rechts nur mit Beschriftung vergrößern: ohne rechte Achse bleibt der bisherige Rand.
    return {left: Math.ceil(widths.left) + EXTRA, right: counts.right === 1 ? Math.ceil(widths.right) + EXTRA : null};
  }

  function fit(chart) {
    if (!chart || chart.isDisposed()) return;
    const grid = chart.getOption().grid;
    if (!grid || grid.length !== 1) return;
    const margins = neededMargins(chart);
    if (margins == null) return;
    const need = margins.left;
    // Breite des Behälters statt der des Diagramms: beim ersten Zeichnen kennt ECharts oft nur
    // seine 100-px-Vorgabe, und Breite 0 heißt, dass der Behälter noch verborgen ist.
    const width = chart.getDom().clientWidth;
    if (width > 0 && (need > width * MAX_SHARE || (margins.right ?? 0) > width * MAX_SHARE)) return;
    const rightSame = margins.right == null || Math.abs(grid[0].right - margins.right) < 1;
    if (grid[0].containLabel === false && Math.abs(grid[0].left - need) < 1 && rightSame) return;
    // containLabel:false schaltet auch den Platz für rechte Beschriftungen ab — dann wird er hier gesetzt,
    // sonst werden die Werte einer zweiten (rechten) Achse abgeschnitten.
    chart.setOption({grid: margins.right == null ? {left: need, containLabel: false} : {left: need, right: margins.right, containLabel: false}});
  }

  // Misst direkt nach jedem setOption() statt auf das 'finished'-Ereignis zu warten: das kommt erst
  // nach dem Zeichnen (ein Bild mit zu knappem Rand wäre kurz sichtbar) und bleibt aus, solange der
  // Browser nicht zeichnet (versteckter Tab).
  function attach(chart) {
    const original = chart.setOption.bind(chart);
    let fitting = false;
    chart.setOption = (...args) => {
      const result = original(...args);
      if (!fitting) {
        fitting = true;
        try { fit(chart); } finally { fitting = false; }
      }
      return result;
    };
    // Beim ersten Zeichnen hat der Behälter oft noch nicht seine endgültige Breite (ECharts rechnet dann mit
    // 100 px und der Rand erschiene unverhältnismäßig); die Größenänderung danach misst deshalb neu.
    const originalResize = chart.resize.bind(chart);
    chart.resize = (...args) => {
      const result = originalResize(...args);
      fit(chart);
      return result;
    };
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => fit(chart));
  }

  return {attach, fit};
})();
