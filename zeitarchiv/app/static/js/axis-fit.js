// Linker Rand eines Charts aus der tatsächlich gemessenen Breite der Y-Achsen-Beschriftungen.
//
// containLabel von ECharts misst die breiteste Beschriftung etwas zu knapp: bei langen Werten mit
// Einheit („36,8 ct/kWh“) fehlte links eine Ziffer, ein fester Zusatzrand (left: 24) war nur eine
// Notlösung für genau die Länge, an der es zuletzt auffiel. Hier wird stattdessen nach jedem setOption()
// die Breite der wirklich angezeigten Beschriftungen mit der tatsächlichen Schrift gemessen und der Rand
// daraus gesetzt. Nach dem Laden der Schriften wird nochmals gemessen, denn davor stimmt die Breite
// nicht zwingend.
//
// Greift nur bei einem Diagramm mit genau einem Raster und genau einer linken Werte-Achse; alles andere
// (mehrere Raster wie die Zustands-Panels, zwei Achsen links) bleibt bei containLabel.
window.AxisFit = (() => {
  const ctx = document.createElement('canvas').getContext('2d');
  // Abstand Beschriftung–Achse (8, ECharts-Standard) plus etwas Luft.
  const EXTRA = 12;
  // Breiter als dieser Anteil der Diagrammbreite ergibt keinen sinnvollen Rand mehr.
  const MAX_SHARE = 0.4;

  function neededLeft(chart) {
    let width = 0;
    let leftAxes = 0;
    for (const axisModel of chart.getModel().queryComponents({mainType: 'yAxis'})) {
      const axis = axisModel.axis;
      if (!axis || axis.position !== 'left') continue;
      leftAxes += 1;
      const labelModel = axisModel.getModel('axisLabel');
      if (axisModel.get('show') === false || labelModel.get('show') === false) continue;
      ctx.font = labelModel.getFont();
      for (const label of axis.getViewLabels()) {
        width = Math.max(width, ctx.measureText(label.formattedLabel).width);
      }
    }
    return leftAxes === 1 ? Math.ceil(width) + EXTRA : null;
  }

  function fit(chart) {
    if (!chart || chart.isDisposed()) return;
    const grid = chart.getOption().grid;
    if (!grid || grid.length !== 1) return;
    const need = neededLeft(chart);
    if (need == null) return;
    // Breite des Behälters statt der des Diagramms: beim ersten Zeichnen kennt ECharts oft nur
    // seine 100-px-Vorgabe, und Breite 0 heißt, dass der Behälter noch verborgen ist.
    const width = chart.getDom().clientWidth;
    if (width > 0 && need > width * MAX_SHARE) return;
    if (grid[0].containLabel === false && Math.abs(grid[0].left - need) < 1) return;
    chart.setOption({grid: {left: need, containLabel: false}});
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
