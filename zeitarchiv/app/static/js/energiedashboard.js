// Energiedashboard (eigenständige Seite, siehe energiedashboard_routes.py) —
// Alpine-Komponente nach dem in entity_detail.html etablierten Muster
// (range/offset-State, load() gegen eine JSON-Route), hier deutlich schlanker
// (nur Tag/Monat/Jahr, keine Vergleichs-/Rohwerte-Optionen) und mit ECharts-
// Sankey statt Linie/Balken als Zielchart.
//
// chartInstance bewusst eine reine Closure-Variable statt eines reaktiven
// Alpine-Felds — dieselbe Begründung wie chartInstance in entity_detail.html:
// ECharts/zrender verlässt sich intern auf Objekt-Identität (this), ein
// Alpine-Proxy bricht das lautlos.
(() => {
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // Wird bei jedem load() neu gelesen (nicht einmalig beim Skriptstart) --
  // ein Hell/Dunkel- oder Farbschema-Wechsel ändert die Werte sonst nicht in
  // den bereits gecachten Strings hier.
  function colors() {
    const storage = cssVar('--chart-4');
    return {
      pv: cssVar('--chart-3'),
      grid: cssVar('--chart-1'),
      storage,
      // Entladung heller als Ladung (statt derselben Farbe für beide
      // Richtungen) — sonst liest sich der Speicher-Fluss im Sankey nicht
      // als "rein" vs. "raus", nur als ein einziger ununterscheidbarer
      // Block. Blend statt zweitem --chart-Token, damit die Verwandtschaft
      // zur Speicherfarbe (Legende) erhalten bleibt.
      storageOut: blendColors(storage, '#ffffff', 0.55),
      exportColor: cssVar('--chart-2'),
      use: cssVar('--ink-faint'),
      bus: cssVar('--border-strong'),
      ink: cssVar('--ink'),
      warning: cssVar('--warning'),
    };
  }

  function colorForNode(node, palette) {
    if (node.role === 'bus') return palette.bus;
    // kind statt Name-Substring: der Speicher-Knotenname enthält den frei
    // wählbaren Konfig-Namen (z. B. "Solarbank (Ladung)") und damit NICHT
    // zuverlässig das Wort "Speicher" — kind ist serverseitig fest gesetzt
    // und unabhängig vom Nutzernamen.
    if (node.kind === 'storage_out') return palette.storageOut;
    if (node.kind === 'storage_in') return palette.storage;
    if (node.name === 'Netzbezug') return palette.grid;
    if (node.name === 'Einspeisung') return palette.exportColor;
    if (node.role === 'source') return palette.pv;
    return palette.use;
  }

  // Farbmix statt Einheitsfarbe (Mockup-Vorgabe): Flüsse vom Bus zu
  // Verbrauchern/Grundlast bekommen EINEN Mischton aus PV- und Netzfarbe,
  // gewichtet nach dem tatsächlichen "grünen" Anteil der Periode
  // (green_ratio, serverseitig berechnet — nach der Vermischung am Bus
  // lässt sich kein Wert je einzelnem Verbraucher mehr zurückrechnen).
  // getComputedStyle(...).color normalisiert JEDE gültige CSS-Farbe
  // (Hex/rgb/named) zuverlässig auf "rgb(r, g, b)", ohne selbst einen
  // Hex-Parser zu brauchen.
  function toRgbTriplet(cssColor) {
    const probe = document.createElement('div');
    probe.style.color = cssColor;
    document.body.appendChild(probe);
    const rgb = getComputedStyle(probe).color;
    document.body.removeChild(probe);
    const m = rgb.match(/\d+/g);
    return m ? [parseInt(m[0], 10), parseInt(m[1], 10), parseInt(m[2], 10)] : [0, 0, 0];
  }

  function blendColors(colorA, colorB, ratioA) {
    const a = toRgbTriplet(colorA);
    const b = toRgbTriplet(colorB);
    const mix = a.map((channel, i) => Math.round(channel * ratioA + b[i] * (1 - ratioA)));
    return `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`;
  }

  // Identischer Algorithmus wie sparklinePaths() in dashboard-tiles.js
  // (main.py _sparkline_paths() serverseitig, .sparkline/.area/.line-CSS
  // aus app.css) — hier lokal statt importiert, da diese Seite
  // dashboard-tiles.js sonst nicht lädt.
  function sparklinePaths(values, width = 84, height = 22, padX = 0, padY = 2) {
    if (values.length < 2) return null;
    const lo = Math.min(...values), hi = Math.max(...values);
    const span = (hi - lo) || 1;
    const step = (width - 2 * padX) / (values.length - 1);
    const points = values.map((v, i) => [
      padX + i * step,
      padY + (height - 2 * padY) * (1 - (v - lo) / span),
    ]);
    const line = 'M' + points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L');
    const area = `${line} L${points[points.length - 1][0].toFixed(1)},${height} L${points[0][0].toFixed(1)},${height} Z`;
    return {line, area};
  }

  // Variante von sparklinePaths() für feste Slot-Anzahl mit Lücken (z. B.
  // 12 Monate/Jahr, manche noch ohne Daten) — anders als sparklinePaths()
  // dürfen hier einzelne Werte `null` sein: die x-Position bleibt trotzdem
  // für ALLE Slots reserviert (Jan..Dez immer an derselben Stelle über
  // mehrere Zeilen hinweg), nur die Linie bricht an der Lücke ab, statt sie
  // zu überbrücken oder die Slot-Anzahl stillschweigend zu verkleinern.
  // Gibt eine Liste von Pfad-"d"-Strings zurück (einer je zusammenhängendem
  // Abschnitt) statt eines einzelnen — mehrere <path>-Elemente im Aufrufer.
  function gappedSparklinePaths(values, width = 300, height = 40, padX = 4, padY = 6) {
    const defined = values.filter((v) => v != null);
    if (defined.length < 2) return null;
    const lo = Math.min(...defined), hi = Math.max(...defined);
    const span = (hi - lo) || 1;
    const step = (width - 2 * padX) / (values.length - 1);
    const points = values.map((v, i) => v == null ? null : [
      padX + i * step,
      padY + (height - 2 * padY) * (1 - (v - lo) / span),
    ]);
    const segments = [];
    let current = [];
    points.forEach((p) => {
      if (p) {
        current.push(p);
      } else if (current.length) {
        segments.push(current);
        current = [];
      }
    });
    if (current.length) segments.push(current);
    const lines = segments
      .filter((seg) => seg.length >= 2)
      .map((seg) => 'M' + seg.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L'));
    // points behält den Slot-Index (i) je Punkt, nicht nur x/y — der Aufrufer
    // braucht den Index, um z. B. den passenden Monatsnamen zuzuordnen.
    const indexedPoints = points
      .map((p, i) => (p ? {i, x: p[0], y: p[1]} : null))
      .filter((p) => p != null);
    return {lines, points: indexedPoints};
  }

  function sparklineSvg(values) {
    const paths = sparklinePaths(values || []);
    if (!paths) {
      // Platzhalter statt leerem Element: z. B. am 1. eines laufenden Monats
      // liegt noch kein zweiter Punkt für eine echte Linie vor — ohne
      // Platzhalter bliebe dieser Bereich leer und die Kachel dadurch (trotz
      // margin-top:auto) uneinheitlich zu den Nachbarkacheln mit Sparkline.
      return `<svg class="sparkline is-placeholder" viewBox="0 0 84 22" preserveAspectRatio="none">`
        + `<line x1="0" y1="19" x2="84" y2="19"/></svg>`;
    }
    return `<svg class="sparkline" viewBox="0 0 84 22" preserveAspectRatio="none">`
      + `<path class="area" d="${paths.area}"/><path class="line" d="${paths.line}"/></svg>`;
  }

  // "Stunde" nach demselben Muster wie formatPeriodLabel() in
  // entity_detail.html ("27.08.2026 · 14:00–15:00 Uhr") — windowEnd ist
  // exklusiv, dieselbe Sekunde-zurück-Korrektur wie dort.
  function periodLabel(data) {
    const start = new Date(data.window_start_ts * 1000);
    if (data.range === 'hour') {
      const end = new Date(data.window_end_ts * 1000 - 1000);
      const fmtTime = (d) => d.toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit'});
      const day = start.toLocaleDateString('de-DE', {day: '2-digit', month: '2-digit', year: 'numeric'});
      return `${day} · ${fmtTime(start)}–${fmtTime(end)} Uhr`;
    }
    let opts = {weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric'};
    if (data.range === 'month') opts = {month: 'long', year: 'numeric'};
    if (data.range === 'year') opts = {year: 'numeric'};
    return start.toLocaleDateString('de-DE', opts);
  }

  let chartInstance = null;
  let shareChartInstance = null;
  let heatmapChartInstance = null;
  let heatmapResizeObserver = null;
  let shareResizeObserver = null;
  let resizeListenerAdded = false;
  const SHARE_COLORS = ['--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5', '--chart-6', '--chart-7', '--chart-8'];
  // Dieselbe Breakpoint-Zahl wie die übrigen @media(max-width:560px)-Regeln
  // auf dieser Seite. Sankey-Orientierung wechselt nur bei tatsächlichem
  // Über-/Unterschreiten neu zu rendern (nicht bei jedem resize), und
  // arbeitet über modulweite Variablen statt this, damit auch ein Resize
  // NACH einem htmx-Swap (neue Alpine-Komponente, aber derselbe einmalig
  // registrierte Listener) die aktuelle Komponente trifft — dieselbe
  // Begründung wie bei chartInstance oben.
  const SANKEY_NARROW_BREAKPOINT = 560;
  // Über matchMedia statt window.innerWidth: innerWidth ist auf Mobilgeräten
  // die Breite des VISUELLEN Viewports und wächst mit, sobald irgendein
  // Element die Seite breiter als das Gerät macht (dann zoomt der Browser
  // heraus) — der Sankey wurde dadurch bei 390px Gerätebreite als "breit"
  // gerendert. matchMedia liefert exakt dasselbe Ergebnis wie die
  // @media(max-width:560px)-Regeln in energiedashboard.html.
  const sankeyIsNarrow = () => window.matchMedia(`(max-width:${SANKEY_NARROW_BREAKPOINT}px)`).matches;
  // Seitlicher Rand der vertikalen (mobilen) Sankey-Serie — halbe Breite der
  // 80px-Labels, die mittig über/unter dem äußersten Knoten sitzen. Steht hier
  // oben, weil sowohl left/right der Serie als auch die Berechnung des
  // Knotenabstands (narrowNodeGap in renderChart) denselben Wert brauchen.
  const SANKEY_NARROW_INSET = 40;
  // Platz über/unter den Knotenreihen für die (bis zu dreizeiligen) Labels
  // plus die zweite, versetzte Reihe aus staggerNarrowLabels().
  const SANKEY_NARROW_LABEL_RESERVE = 74;
  // Vertikaler Versatz der zweiten Label-Reihe.
  const SANKEY_NARROW_LABEL_STAGGER = 30;
  // Mindestabstand zweier Label-Mittelpunkte IN DERSELBEN Reihe, ab dem sie
  // sich nicht mehr überlappen — etwas weniger als die 80px Label-Breite, weil
  // kaum ein Name die volle Breite ausnutzt.
  const SANKEY_NARROW_LABEL_PITCH = 68;
  let lastSankeyData = null;
  let lastSankeyIsNarrow = null;
  let currentRenderChart = null;

  // Zeitraum/Offset spiegeln sich in der URL (?range=...&offset=...) statt
  // nur im Alpine-Zustand — sonst würde ein "zurück zum Energiedashboard"-
  // Link von einer Entitäts-Seite aus (siehe dynamic-back-link.js) immer auf
  // der Standardansicht ('Tag', heute) landen, egal welchen Zeitraum man
  // vorher gewählt hatte: der Link zeigt ja nur auf die URL, und die kannte
  // bisher gar keinen Zeitraum. replaceState statt pushState — jeder
  // Perioden-Klick soll die eine Dashboard-Seite im Verlauf ERSETZEN, nicht
  // einen eigenen Schritt anlegen (sonst müsste man sich durch jede einzelne
  // Vor/Zurück-Navigation zurückklicken, um die Seite zu verlassen).
  const RANGE_KEYS = ['hour', 'day', 'month', 'year'];

  function readInitialRangeOffset() {
    const params = new URLSearchParams(window.location.search);
    const range = params.get('range');
    const offset = parseInt(params.get('offset'), 10);
    return {
      range: RANGE_KEYS.includes(range) ? range : 'day',
      offset: Number.isInteger(offset) ? offset : 0,
    };
  }

  function syncUrlWithPeriod(range, offset) {
    const url = new URL(window.location.href);
    url.searchParams.set('range', range);
    url.searchParams.set('offset', String(offset));
    history.replaceState(null, '', url);
  }

  window.energieFlow = function energieFlow() {
    const initialPeriod = readInitialRangeOffset();
    return {
      ranges: [{key: 'hour', label: 'Stunde'}, {key: 'day', label: 'Tag'}, {key: 'month', label: 'Monat'}, {key: 'year', label: 'Jahr'}],
      range: initialPeriod.range,
      offset: initialPeriod.offset,
      loading: false,
      loadError: false,
      kpi: {},
      kpiCompare: {},
      compareLabel: '',
      quality: {plausible: true, checks: []},
      periodText: '',
      hasFlow: false,
      // Farblegende unter dem Sankey. Wird in renderChart() aus DERSELBEN
      // palette/colorForNode-Quelle gefüllt wie die Knoten selbst — eine
      // zweite, im Template gepflegte Farbliste würde beim nächsten
      // Farbschema-Wechsel unbemerkt auseinanderlaufen.
      legendItems: [],
      verbraucherBreakdown: [],
      erzeugerBreakdown: [],
      speicherBreakdown: [],
      speicherSocNowBreakdown: [],
      anomalien: [],
      // Die vier Ring-Trends kommen NICHT mehr mit load() mit, sondern erst
      // beim ersten Öffnen eines Trend-Popups (openTrend/loadTrends) — sie
      // machten serverseitig den Großteil der Rechenzeit eines /data-Requests
      // aus, obwohl man sie erst nach einem Klick zu sehen bekommt. Sie hängen
      // außerdem gar nicht am gewählten Zeitraum (immer die letzten drei
      // Kalenderjahre), müssen also auch beim Perioden-Wechsel nicht neu
      // geholt werden — ein Fetch je Seitenaufruf genügt.
      speicherEfficiencyTrend: [],
      speicherSocTrend: [],
      autarkieTrend: [],
      eigenverbrauchTrend: [],
      trendsLoaded: false,
      trendsLoading: false,
      trendsError: false,
      heatmap: {rows: [], max_value: 0},
      get canGoForward() { return this.offset < 0; },

      // Bilanz (Grundlast plausibel) und Datenqualität (die übrigen Checks)
      // waren serverseitig schon immer EINE flache quality.checks-Liste
      // (checks-Reihenfolge kann sich ändern) — hier per Label statt Index
      // in zwei Kacheln aufgeteilt, damit ein Umsortieren der Liste nicht
      // versehentlich die falsche Kachel als "Bilanz" ausgibt.
      get bilanzCheck() {
        return (this.quality.checks || []).find((c) => c.label === 'Grundlast plausibel') || {ok: true, detail: ''};
      },
      get otherChecks() {
        return (this.quality.checks || []).filter((c) => c.label !== 'Grundlast plausibel');
      },
      get otherChecksOk() {
        return this.otherChecks.every((c) => c.ok);
      },
      get grundlastShare() {
        const g = this.verbraucherBreakdown.find((v) => v.name === 'Grundlast');
        return g ? g.share : null;
      },

      init() {
        // Nach einem htmx-Swap (z. B. Rollen gespeichert → zurück zur
        // Ansicht) mountet Alpine diese Komponente NEU auf einem frischen
        // DOM-Knoten, aber chartInstance ist eine modulweite Closure-
        // Variable, die den ALTEN (jetzt aus dem DOM entfernten) Chart
        // überlebt. Ohne dispose() hier zeigte setOption() in renderChart()
        // beim zweiten Mount unsichtbar den toten alten Chart an, sichtbar
        // erst korrekt nach einem vollständigen Seiten-Reload.
        if (chartInstance) {
          chartInstance.dispose();
          chartInstance = null;
        }
        if (shareChartInstance) {
          shareChartInstance.dispose();
          shareChartInstance = null;
        }
        if (shareResizeObserver) {
          shareResizeObserver.disconnect();
          shareResizeObserver = null;
        }
        if (heatmapChartInstance) {
          heatmapChartInstance.dispose();
          heatmapChartInstance = null;
        }
        if (heatmapResizeObserver) {
          heatmapResizeObserver.disconnect();
          heatmapResizeObserver = null;
        }
        currentRenderChart = (data) => this.renderChart(data);
        this.load();
        if (!resizeListenerAdded) {
          resizeListenerAdded = true;
          window.addEventListener('resize', () => {
            if (chartInstance) chartInstance.resize();
            if (shareChartInstance) shareChartInstance.resize();
            if (heatmapChartInstance) heatmapChartInstance.resize();
            // Sankey-Orientierung (horizontal/vertikal) nur neu rendern, wenn
            // der Breakpoint wirklich über-/unterschritten wurde — sonst bei
            // jedem Pixel-Resize unnötig den ganzen Chart neu aufbauen.
            const isNarrow = sankeyIsNarrow();
            if (lastSankeyData && isNarrow !== lastSankeyIsNarrow && currentRenderChart) {
              currentRenderChart(lastSankeyData);
            }
          });
        }
      },

      // Jeder Klick auf Tag/Monat/Jahr springt zur aktuellen Periode (offset
      // 0) — auch wenn dieselbe, schon aktive Pille erneut geklickt wird
      // (z. B. 2 Tage zurücknavigiert, nochmal "Tag" geklickt → wieder
      // heute). Kein Anker/Übersetzen zwischen Auflösungen wie in
      // entity_detail.html — hier bewusst immer "zurück zu jetzt".
      setRange(key) {
        if (key === this.range && this.offset === 0) return;
        this.range = key;
        this.offset = 0;
        syncUrlWithPeriod(this.range, this.offset);
        this.load();
      },
      goBack() { this.offset -= 1; syncUrlWithPeriod(this.range, this.offset); this.load(); },
      goForward() {
        if (!this.canGoForward) return;
        this.offset += 1;
        syncUrlWithPeriod(this.range, this.offset);
        this.load();
      },

      fmt(value, decimals) {
        if (value == null) return '—';
        return window.NumberFormat ? window.NumberFormat.fmt(value, decimals) : String(value);
      },

      ratioText(value) {
        return value == null ? '—' : this.fmt(value, 0) + ' %';
      },

      // Preis-Sensoren werden als €/kWh vorausgesetzt (siehe Hinweistext im
      // Setup) — kein Einheiten-/Währungs-Handling darüber hinaus für v1.
      fmtCurrency(value) {
        return value == null ? '—' : this.fmt(value, 2) + ' €';
      },

      // Alltags-Vergleich für "Vermiedenes CO2" — bewusst nur eine grobe
      // Orientierung (echter Verbrauch schwankt stark je Fahrzeug/Fahrweise),
      // kein exakter Wert. ~130 g CO2/km ist ein gängiger Richtwert für
      // einen durchschnittlichen PKW (EU-Flottengrenzwert für Neuwagen).
      co2VermiedenKmText() {
        if (this.kpi.co2_vermieden == null) return '';
        const km = (this.kpi.co2_vermieden * 1000) / 130;
        return '≈ ' + this.fmt(km, 0) + ' km Autofahrt';
      },

      // Netto-CO2 für die dritte KPI-Kachel im CO2-Dialog — negativ heißt
      // "mehr vermieden als verursacht" (siehe .is-bilanz-win in
      // energiedashboard.html) und wird dort bewusst als kleiner Erfolg
      // hervorgehoben statt nur als weitere neutrale Zahl.
      co2Bilanz() {
        if (this.kpi.co2_ausstoss == null || this.kpi.co2_vermieden == null) return null;
        return this.kpi.co2_ausstoss - this.kpi.co2_vermieden;
      },

      // Erfolgstext für die Saldo-Kachel im Kostenanalyse-Dialog, analog zum
      // CO2-Pendant oben — nur bei negativem Saldo (Einspeisung-Erlös
      // überwiegt die Netzbezug-Kosten).
      costWinText() {
        return this.kpi.net_cost != null && this.kpi.net_cost < 0 ? '💶 Mehr erlöst als bezahlt!' : '';
      },

      // Eine Sparkline je Jahres-Zeile im Wirkungsgrad-Trend-Popup — 12
      // feste Monats-Slots (Jan..Dez), fehlende Monate (vor Inbetriebnahme
      // oder noch in der Zukunft beim laufenden Jahr) bleiben als Lücke im
      // Slot statt die Kurve zusammenzustauchen, damit Jan..Dez über alle
      // Jahres-Zeilen hinweg an derselben x-Position stehen und sich direkt
      // vergleichen lassen. Mehrere <path>-Segmente statt einem, weil die
      // Linie an jeder Lücke abbricht (gappedSparklinePaths()).
      //
      // Zusätzlich 12 unsichtbare Hover-Zonen (eine je Monats-Slot) mit dem
      // etablierten [data-tooltip]-Muster (siehe app.css) statt Tooltips
      // direkt IN das SVG zu legen — ::after-Pseudoelemente rendern auf
      // echten SVG-Formen (<path>/<rect>) in den meisten Browsern nicht
      // zuverlässig, auf normalen <div>s dagegen schon (dasselbe Muster wie
      // überall sonst in der App). Die divs liegen einfach als Geschwister
      // über dem SVG, gleich breite Prozent-Segmente statt exakt an die
      // SVG-Punkt-Koordinaten (die haben durch padX etwas Rand) —
      // ausreichend genau zum Darüberfahren, kein Pixel-genaues Fadenkreuz.
      // Generisch für alle 4 Ring-Trends (Wirkungsgrad/Autarkie/
      // Eigenverbrauch/Speicher-SOC) — braucht nur {year, months}, kein
      // Bezug auf eine bestimmte Kennzahl.
      trendYearRowHtml(row) {
        const months = row.months;
        const result = gappedSparklinePaths(months, 300, 32, 4, 5);
        const lines = (result?.lines || []).map((d) => `<path class="line" d="${d}"/>`).join('');
        // Punkte je tatsächlich vorhandenem Monat, nicht nur die Linie —
        // sonst ist bei kurzen Segmenten (z. B. nur 2 Monate) oder am Ende
        // einer Lücke kaum erkennbar, wo genau ein Datenpunkt sitzt.
        const dots = (result?.points || [])
          .map((p) => `<circle class="sparkline-dot" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="1.6"/>`)
          .join('');
        const svg = `<svg class="sparkline" viewBox="0 0 300 32" preserveAspectRatio="none" style="width:100%;height:32px;display:block;">${lines}${dots}</svg>`;
        const monthNames = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
        const slotWidth = 100 / 12;
        const slots = months.map((v, i) => {
          if (v == null) return '';
          const tooltip = `${monthNames[i]} ${row.year}: ${this.fmt(v, 1)} %`;
          return `<div style="position:absolute;top:0;bottom:0;left:${(slotWidth * i).toFixed(2)}%;width:${slotWidth.toFixed(2)}%;" data-tooltip="${tooltip}"></div>`;
        }).join('');
        return `<div style="position:relative;">${svg}${slots}</div>`;
      },

      // Neuestes Jahr zuerst (die Sparkline in jeder Zeile bleibt
      // chronologisch Jan->Dez, nur die ZEILEN-Reihenfolge dreht sich um) —
      // Methode statt Getter, weil sie für alle 4 Trends gebraucht wird.
      reversedTrend(trend) {
        return [...trend].reverse();
      },

      // Ø je Jahres-Zeile, rechts neben der Sparkline — nur über die
      // tatsächlich vorhandenen Monate (Lücken zählen nicht mit).
      yearAverageText(months) {
        const defined = months.filter((v) => v != null);
        if (!defined.length) return '—';
        return this.fmt(defined.reduce((a, b) => a + b, 0) / defined.length, 1) + ' %';
      },

      // Aufschlüsselung der Erzeuger als Tooltip auf der Erzeugung-Kachel —
      // nur bei mehr als einem Erzeuger sinnvoll (bei genau einem wäre die
      // "Aufschlüsselung" nur eine Wiederholung der Gesamtsumme). Zeilenumbruch
      // je Erzeuger — das umschließende Element trägt zusätzlich die Klasse
      // .tooltip-lines (white-space:pre-line), sonst würde die geteilte
      // [data-tooltip]::after-Regel (white-space:normal) \n zu einem
      // Leerzeichen zusammenfallen lassen.
      erzeugerBreakdownText() {
        // null statt '' — Alpines :data-tooltip entfernt das Attribut nur
        // bei null/false/undefined komplett, bei '' bliebe eine leere (aber
        // vorhandene) Tooltip-Blase beim Hover sichtbar.
        if (this.erzeugerBreakdown.length <= 1) return null;
        return this.erzeugerBreakdown.map((e) => `${e.name}: ${this.fmt(e.value, 1)} kWh`).join('\n');
      },

      // Analog zu erzeugerBreakdownText() für die Speicher-KPI-Kachel — Wert
      // ist hier bereits Laden minus Entladen (netto) je Speicher, siehe
      // speicher_breakdown im Backend.
      speicherBreakdownText() {
        if (this.speicherBreakdown.length <= 1) return null;
        return this.speicherBreakdown.map((s) => `${s.name}: ${this.fmt(s.value, 1)} kWh`).join('\n');
      },

      // Analog für den "Jetzt"-Ladezustand im Autarkie&Speicher-Ring — anders
      // als speicherBreakdownText() ein Momentanwert (%, plus kWh wenn die
      // Kapazität dieses Speichers bekannt ist), kein Perioden-Wert.
      speicherSocNowBreakdownText() {
        if (this.speicherSocNowBreakdown.length <= 1) return null;
        return this.speicherSocNowBreakdown
          .map((s) => `${s.name}: ${this.fmt(s.soc, 0)} %` + (s.kwh != null ? ` (${this.fmt(s.kwh, 1)} kWh)` : ''))
          .join('\n');
      },

      // Statischer Erklärtext des "Jetzt"-Ladezustands (siehe
      // _energiedashboard_view.html) plus, bei mehr als einem Speicher, die
      // Aufschlüsselung je Speicher darunter — sonst wüsste man bei "Jetzt
      // 89 %" nicht, wie sich das über mehrere Speicher unterschiedlicher
      // Kapazität zusammensetzt.
      socNowTooltipText() {
        const base = 'Aktueller Ladezustand, unabhängig vom gewählten Zeitraum — der Ring unten zeigt den Ø-Wert über die Periode.';
        const breakdown = this.speicherSocNowBreakdownText();
        return breakdown ? base + '\n\n' + breakdown : base;
      },

      // Gesamt-Linie oben im Popup, über allen Jahres-Zeilen — durchgehend
      // (keine Lücken nötig wie bei den Jahres-Sparklines, weil hier einfach
      // alle tatsächlich vorhandenen Monate chronologisch aneinandergereiht
      // werden statt fester Jan..Dez-Slots je Zeile) — dieselbe Fläche+Linie-
      // Technik wie die KPI-Kachel-Sparklines, nur größer. Parametrisiert
      // (trend statt this.speicherEfficiencyTrend), damit alle 4 Ring-Trends
      // dieselbe Methode nutzen.
      trendOverallSvg(trend) {
        const values = trend.flatMap((row) => row.months).filter((v) => v != null);
        const paths = sparklinePaths(values, 600, 60, 4, 6);
        if (!paths) return '';
        return `<svg class="sparkline" viewBox="0 0 600 60" preserveAspectRatio="none" style="width:100%;height:60px;">`
          + `<path class="area" d="${paths.area}"/><path class="line" d="${paths.line}"/></svg>`;
      },

      shareColor(idx) {
        return cssVar(SHARE_COLORS[idx % SHARE_COLORS.length]);
      },

      // Kosten-Spalte in der Verbraucheranteile-Tabelle nur einblenden, wenn
      // ein Netzbezug-Preis konfiguriert ist (siehe kosten in compute_flow) —
      // sonst ist item.kosten bei jeder Zeile null.
      get verbraucherHasKosten() {
        return this.verbraucherBreakdown.some((i) => i.kosten != null);
      },
      verbraucherKostenTotal() {
        if (!this.verbraucherHasKosten) return null;
        return this.verbraucherBreakdown.reduce((sum, i) => sum + (i.kosten || 0), 0);
      },

      // Fortschrittsring (Autarkie/Eigenverbrauch, siehe .edash-ring-* im
      // Template): stroke-dashoffset des zweiten (farbigen) Kreises auf dem
      // Umfang 263,894 (= 2·π·42, Radius 42 aus dem SVG) — 0 % Offset = voller
      // Umfang unsichtbar (nichts gezeichnet), 100 % = Offset 0 (ganzer Ring).
      ringOffset(pct) {
        const clamped = Math.max(0, Math.min(100, pct || 0));
        return 263.894 * (1 - clamped / 100);
      },

      // Bewusst keine Auf/Ab-Einfärbung (grün/rot) — "mehr" ist bei
      // Erzeugung/Einspeisung erwünscht, bei Netzbezug/Verbrauch eher nicht;
      // eine pauschale Farbregel würde für die Hälfte der Kacheln die
      // falsche Bedeutung suggerieren. Bleibt neutral, Zahl spricht für sich.
      deltaText(key) {
        const entry = this.kpiCompare[key];
        if (!entry || !this.compareLabel) return '';
        if (entry.pct != null) {
          const sign = entry.pct > 0 ? '+' : '';
          return `${sign}${this.fmt(entry.pct, 1)} % vs. ${this.compareLabel}`;
        }
        // Fallback für eine Vorperiode von exakt 0 (% wäre Division durch 0,
        // siehe _compare_kpi) — absolute kWh-Differenz statt gar nichts.
        if (entry.abs != null) {
          const sign = entry.abs > 0 ? '+' : '';
          return `${sign}${this.fmt(entry.abs, 1)} kWh vs. ${this.compareLabel}`;
        }
        return '';
      },

      // Netzenergiebilanz (Netzbezug − Einspeisung als EIN Netto-Wert):
      // Skala = größerer der beiden Werte, damit der Balken den vollen
      // Bereich (0 … 50 % je Richtung) sinnvoll ausnutzt, statt bei sehr
      // ungleichen Werten fast leer zu wirken.
      netBalanceValue() {
        if (this.kpi.netzbezug == null || this.kpi.einspeisung == null) return null;
        return this.kpi.netzbezug - this.kpi.einspeisung;
      },
      netBalanceFillStyle() {
        const v = this.netBalanceValue();
        if (v == null) return {};
        const scale = Math.max(this.kpi.netzbezug || 0, this.kpi.einspeisung || 0, 0.001);
        const pct = Math.min(50, Math.abs(v) / scale * 50);
        return v >= 0 ? {left: '50%', width: pct + '%'} : {left: (50 - pct) + '%', width: pct + '%'};
      },

      async load() {
        this.loading = true;
        this.loadError = false;
        try {
          const params = new URLSearchParams({range: this.range, offset: String(this.offset)});
          const res = await fetch(`energiedashboard/data?${params}`);
          if (!res.ok) { this.loadError = true; return; }
          const data = await res.json();
          this.kpi = data.kpi;
          this.kpiCompare = data.kpi_compare || {};
          this.compareLabel = data.compare_label || '';
          this.quality = data.quality;
          this.periodText = periodLabel(data);
          this.hasFlow = data.nodes.some(n => n.role !== 'bus' && n.value > 0);
          this.verbraucherBreakdown = data.verbraucher_breakdown || [];
          this.erzeugerBreakdown = data.erzeuger_breakdown || [];
          this.speicherBreakdown = data.speicher_breakdown || [];
          this.speicherSocNowBreakdown = data.speicher_soc_now_breakdown || [];
          this.anomalien = data.anomalien || [];
          this.renderChart(data);
          this.renderShareChart(this.verbraucherBreakdown);
          this.renderSparklines(data.kpi_series || {});
        } catch (e) {
          this.loadError = true;
        } finally {
          this.loading = false;
        }
        // Eigener, unabhängiger Fetch (eigenes try/catch unten) — ein
        // Fehlschlag hier soll die restliche Seite nicht als loadError
        // markieren, die Karte blendet sich per x-show einfach aus.
        this.loadHeatmap();
      },

      // Popup öffnen und die Trend-Daten dafür (einmalig) nachladen. Das
      // Öffnen passiert sofort, der Fetch läuft daneben — das Popup zeigt
      // solange "Trend wird geladen …" (siehe trend_dialog-Makro), statt auf
      // die Antwort zu warten und dadurch den Klick träge wirken zu lassen.
      openTrend(dialog) {
        dialog.showModal();
        this.loadTrends();
      },

      // Genau ein Fetch je Seitenaufruf: die vier Ring-Trends gehen immer über
      // die letzten drei Kalenderjahre und ändern sich beim Umschalten von
      // Stunde/Tag/Monat/Jahr nicht (siehe compute_trends() im Backend) —
      // deshalb kein range/offset und kein erneutes Laden bei load().
      // trendsLoading als Sperre gegen ein zweites paralleles Fetch, wenn man
      // schnell hintereinander zwei Ring-Popups öffnet.
      async loadTrends() {
        if (this.trendsLoaded || this.trendsLoading) return;
        this.trendsLoading = true;
        this.trendsError = false;
        try {
          const res = await fetch('energiedashboard/trends');
          if (!res.ok) { this.trendsError = true; return; }
          const data = await res.json();
          this.speicherEfficiencyTrend = data.speicher_efficiency_trend || [];
          this.speicherSocTrend = data.speicher_soc_trend || [];
          this.autarkieTrend = data.autarkie_trend || [];
          this.eigenverbrauchTrend = data.eigenverbrauch_trend || [];
          this.trendsLoaded = true;
        } catch (e) {
          this.trendsError = true;
        } finally {
          this.trendsLoading = false;
        }
      },

      // Bei Tag/Stunde unverändert die letzten 7 Kalendertage (vom Zeitraum-
      // Umschalter entkoppelt), bei Monat/Jahr wochentagsweise gemittelt über
      // den aktuell gewählten Zeitraum — siehe compute_heatmap_weekday() in
      // energiedashboard_routes.py. range/offset kommen aus demselben
      // Zustand wie load(), deshalb hier nicht als Parameter, sondern direkt
      // aus this gelesen.
      async loadHeatmap() {
        try {
          const params = new URLSearchParams({range: this.range, offset: String(this.offset)});
          const res = await fetch(`energiedashboard/heatmap?${params}`);
          if (!res.ok) return;
          this.heatmap = await res.json();
          // Die Karte hängt an x-show="heatmap.rows.length" (startet leer,
          // also unsichtbar) — ohne $nextTick() initialisiert echarts.init()
          // hier auf einem noch display:none-Element (0×0 Breite), bevor
          // Alpine die Sichtbarkeits-Änderung überhaupt ins DOM übernommen
          // hat, und der Chart bleibt dauerhaft auf 0 Breite hängen.
          this.$nextTick(() => this.renderHeatmap(this.heatmap));
        } catch (e) {
          // Stiller Fehlschlag — die Heatmap-Karte blendet sich per x-show
          // einfach aus (heatmap.rows bleibt leer), keine eigene Fehleranzeige
          // nötig für eine zusätzliche, nicht zentrale Kachel.
        }
      },

      // Kurzhinweis neben dem Kachel-Titel: bei Monat/Jahr sind die Zeilen
      // wochentagsweise gemittelt statt konkrete Kalendertage — ohne diesen
      // Hinweis wäre der Bedeutungswechsel der Zeilen nicht ersichtlich.
      // periodText kommt aus load() (derselbe range/offset), deshalb hier
      // ohne eigene Datumsformatierung wiederverwendet.
      heatmapNote() {
        if (this.range === 'month' || this.range === 'year') return `Ø je Wochentag · ${this.periodText}`;
        return 'letzte 7 Tage';
      },

      // Als ECharts-heatmap-Serie statt eigenem HTML/CSS-Grid gerendert —
      // dieselbe Bibliothek/Instanz wie Sankey und Donut auf dieser Seite,
      // Tooltip dadurch automatisch im selben Look statt eines optisch
      // abweichenden nativen title-Attributs. null-Werte (heutiger Tag,
      // Stunden in der Zukunft) werden einfach nicht in "cells" aufgenommen
      // — ECharts lässt die Zelle dann leer, statt sie einzufärben.
      renderHeatmap(data) {
        const el = this.$refs.heatmapEl;
        if (!el || typeof echarts === 'undefined' || !data.rows || !data.rows.length) return;
        if (!heatmapChartInstance) heatmapChartInstance = echarts.init(el);
        const palette = colors();
        const accent = cssVar('--accent-line');
        const surfaceAlt = cssVar('--surface-alt');
        const dayLabels = data.rows.map((row) => row.label);
        const hourLabels = Array.from({length: 24}, (_, h) => String(h));
        const cells = [];
        data.rows.forEach((row, rIdx) => {
          row.hours.forEach((value, hIdx) => {
            if (value != null) cells.push([hIdx, rIdx, value]);
          });
        });
        const fmt = (value) => this.fmt(value, value < 10 ? 2 : 1);
        heatmapChartInstance.setOption({
          tooltip: {
            trigger: 'item',
            formatter: (p) => `${p.marker}${dayLabels[p.value[1]]} ${p.value[0]}:00: <strong>${fmt(p.value[2])} kWh</strong>`,
          },
          grid: {containLabel: true, left: 4, right: 8, top: 8, bottom: 4},
          xAxis: {
            type: 'category', data: hourLabels, position: 'top',
            splitArea: {show: false}, axisLine: {show: false}, axisTick: {show: false},
            axisLabel: {
              color: palette.ink, fontFamily: 'IBM Plex Mono, monospace', fontSize: 9, interval: 2, margin: 4,
              formatter: (value) => value + ':00',
            },
          },
          yAxis: {
            type: 'category', data: dayLabels, inverse: true,
            axisLine: {show: false}, axisTick: {show: false},
            axisLabel: {color: palette.ink, fontSize: 10.5},
          },
          visualMap: {show: false, min: 0, max: data.max_value || 1, inRange: {color: [surfaceAlt, accent]}},
          series: [{
            type: 'heatmap',
            data: cells,
            itemStyle: {borderColor: cssVar('--surface'), borderWidth: 1, borderRadius: 2},
            emphasis: {itemStyle: {shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.25)'}},
          }],
        }, true);
        // Robuster als der $nextTick()-Zeitpunkt allein (der die Karte nur
        // beim JETZIGEN Sichtbarwerden trifft): ResizeObserver feuert bei
        // JEDER tatsächlichen Größenänderung des Containers — u. a. genau
        // dann, wenn x-show ihn von display:none auf sichtbar umschaltet,
        // unabhängig davon, wie viele Alpine-Ticks oder Layoutschritte
        // dazwischenliegen. Dasselbe etablierte Muster wie #storage-pie in
        // statistik.html. Bei jedem renderHeatmap()-Aufruf neu verbunden
        // (nicht nur einmalig wie der window-resize-Listener), da $refs.
        // heatmapEl bei einem htmx-Swap ein neuer DOM-Knoten ist.
        if (typeof ResizeObserver !== 'undefined') {
          if (heatmapResizeObserver) heatmapResizeObserver.disconnect();
          heatmapResizeObserver = new ResizeObserver(() => {
            if (heatmapChartInstance) heatmapChartInstance.resize();
          });
          heatmapResizeObserver.observe(el);
        }
      },

      renderSparklines(series) {
        const map = {
          erzeugung: 'sparkErzeugung', verbrauch: 'sparkVerbrauch', netzbezug: 'sparkNetzbezug',
          speicher_netto: 'sparkSpeicher', einspeisung: 'sparkEinspeisung',
        };
        Object.entries(map).forEach(([key, ref]) => {
          const el = this.$refs[ref];
          if (el) el.innerHTML = sparklineSvg(series[key]);
        });
      },

      // Zweiter Durchgang, nur für die vertikale (mobile) Darstellung: dort
      // stehen die Knoten einer Ebene NEBENEINANDER, und die 80px breiten,
      // mittig gesetzten Labels überlappten sich bei mehr als drei, vier
      // Knoten je Ebene. Welche Knoten nebeneinander landen, entscheidet aber
      // erst ECharts' Layout — vorher ist die Reihenfolge innerhalb einer
      // Ebene unbekannt. Deshalb einmal rendern, die Positionen auslesen und
      // die Labels dann in ZWEI Reihen versetzen: das verdoppelt den
      // waagerechten Platz je Label. Was sich danach immer noch überlappt,
      // wird weggelassen statt übereinandergedruckt — der größte Wert einer
      // Reihe gewinnt, weil er die Aussage des Diagramms trägt. Weggelassene
      // Knoten bleiben antippbar und zeigen Name, Wert und Anteil im Tooltip
      // (deshalb ist das Weglassen vertretbar, kein Informationsverlust).
      staggerNarrowLabels(baseNodes) {
        if (!chartInstance) return;
        const graph = chartInstance.getModel().getSeriesByIndex(0).getGraph();
        const proEbene = {};
        graph.nodes.forEach((n) => {
          const layout = n.getLayout();
          if (!layout) return;
          const meta = baseNodes.find((b) => b.name === n.id);
          // Nur Labels über/unter den Knotenreihen können sich waagerecht
          // überlappen. Der Bus ist der einzige Knoten seiner Ebene und trägt
          // sein Label rechts daneben (narrowLabelFor) — für ihn ist weder
          // Versatz noch Kollisionsprüfung nötig.
          if (!meta || !meta.label || (meta.label.position !== 'top' && meta.label.position !== 'bottom')) return;
          proEbene[layout.depth] = proEbene[layout.depth] || [];
          proEbene[layout.depth].push({
            id: n.id, mitte: layout.x + layout.dx / 2, wert: layout.value || 0,
          });
        });
        const versatz = {};
        const ausgeblendet = new Set();
        Object.values(proEbene).forEach((knoten) => {
          knoten.sort((a, b) => a.mitte - b.mitte);
          knoten.forEach((k, i) => { versatz[k.id] = i % 2; });
          // Je Reihe getrennt prüfen: absteigend nach Wert einsortieren und nur
          // behalten, was zu allen bereits behaltenen Labels dieser Reihe
          // genug Abstand hat.
          [0, 1].forEach((reihe) => {
            const behalten = [];
            knoten
              .filter((k) => versatz[k.id] === reihe)
              .sort((a, b) => b.wert - a.wert)
              .forEach((k) => {
                if (behalten.some((m) => Math.abs(m - k.mitte) < SANKEY_NARROW_LABEL_PITCH)) {
                  ausgeblendet.add(k.id);
                } else {
                  behalten.push(k.mitte);
                }
              });
          });
        });
        chartInstance.setOption({series: [{
          data: baseNodes.map((n) => {
            if (versatz[n.name] === undefined) return n;
            if (ausgeblendet.has(n.name)) return {...n, label: {...n.label, show: false}};
            const richtung = n.label.position === 'top' ? -1 : 1;
            return {...n, label: {
              ...n.label,
              offset: [0, richtung * versatz[n.name] * SANKEY_NARROW_LABEL_STAGGER],
            }};
          }),
        }]});
      },

      // Nur Einträge, die im aktuellen Sankey wirklich vorkommen — eine
      // Anlage ohne Speicher soll keine Speicherfarben erklärt bekommen.
      // Deshalb aus data.nodes abgeleitet statt aus der Konfiguration: die
      // Legende beschreibt genau das, was gerade gezeichnet ist.
      buildLegend(data, palette) {
        // Ist die Legende abgeschaltet (Allgemein -> Energiefluss), liefert der
        // Server ihr Markup gar nicht erst aus — dann auch nicht rechnen. Der
        // Mischton-Eintrag ruft blendColors() auf, das für die Farbumrechnung
        // kurz ein Element in den DOM hängt und getComputedStyle() erzwingt;
        // das für eine unsichtbare Liste zu tun wäre reine Verschwendung.
        if (!this.$refs.legendEl) { this.legendItems = []; return; }
        const nodes = data.nodes || [];
        const hat = (pruef) => nodes.some(pruef);
        const items = [];
        if (hat(n => n.role === 'source' && n.name !== 'Netzbezug' && n.kind !== 'storage_out')) {
          items.push({name: 'Erzeugung', color: palette.pv});
        }
        const netzbezug = nodes.find(n => n.name === 'Netzbezug');
        if (netzbezug) items.push({name: netzbezug.label || 'Netzbezug', color: palette.grid});
        if (hat(n => n.kind === 'storage_out')) {
          items.push({name: 'Speicher-Entladung', color: palette.storageOut});
        }
        if (hat(n => n.kind === 'storage_in')) {
          items.push({name: 'Speicher-Ladung', color: palette.storage});
        }
        const einspeisung = nodes.find(n => n.name === 'Einspeisung');
        if (einspeisung) items.push({name: einspeisung.label || 'Einspeisung', color: palette.exportColor});
        // Der Verbrauchs-Eintrag trägt den tatsächlichen Mischton der
        // Bus→Verbraucher-Bahnen (siehe green_ratio/blendColors in
        // renderChart) statt einer generischen Verbrauchsfarbe — genau dieser
        // Farbton ist ohne Erklärung sonst am schwersten zu deuten, und der
        // Prozentwert sagt direkt, woher die Mischung kommt.
        if (hat(n => n.role === 'sink' && n.name !== 'Einspeisung' && n.kind !== 'storage_in')) {
          if (data.green_ratio != null) {
            items.push({
              name: `Verbrauch (${this.fmt(data.green_ratio * 100, 0)} % aus eigener Erzeugung)`,
              color: blendColors(palette.pv, palette.grid, data.green_ratio),
            });
          } else {
            items.push({name: 'Verbrauch', color: palette.use});
          }
        }
        this.legendItems = items;
      },

      renderChart(data) {
        const el = this.$refs.sankeyEl;
        if (!el || typeof echarts === 'undefined') return;
        lastSankeyData = data;
        const isNarrow = sankeyIsNarrow();
        lastSankeyIsNarrow = isNarrow;
        const palette = colors();
        this.buildLegend(data, palette);
        if (!chartInstance) chartInstance = echarts.init(el);
        const nodeByName = {};
        data.nodes.forEach(n => { nodeByName[n.name] = n; });
        const links = data.links
          .filter(l => l.value > 0.001)
          .map(l => {
            // Farbmix statt Einheitsfarbe: Flüsse vom Bus zu Verbrauchern/
            // Grundlast (blend:true, siehe compute_flow()) mischen PV- und
            // Netzfarbe nach green_ratio der Periode statt des generischen
            // Quelle→Ziel-Verlaufs (lineStyle.color:'gradient' unten) —
            // zeigt "wie grün" der Verbrauch im Schnitt war.
            const target = nodeByName[l.target];
            if (target && target.blend && data.green_ratio != null) {
              return {...l, lineStyle: {color: blendColors(palette.pv, palette.grid, data.green_ratio)}};
            }
            return l;
          });
        // Ein- und ausgehende Link-Zahl je Knoten — nur für den Tooltip: der
        // "X % von …"-Zusatz (edge-Zweig des Formatters unten) ist nur dann
        // aussagekräftig, wenn EINE der beiden Seiten eines Links tatsächlich
        // mehrere Linien hat. Ziel bevorzugt (Zufluss-Aufschlüsselung, z. B.
        // "Haus" aus Netzbezug + Erzeugung); hat das Ziel dagegen nur eine
        // einzige eingehende Linie (jeder Verbraucher/jede Gruppe), aber die
        // QUELLE mehrere ausgehende (z. B. "Haushaltsgeräte" verzweigt sich
        // auf Waschmaschine + Trockner, oder "Haus" auf alle Senken),
        // stattdessen Anteil an der Quelle zeigen — das ist dann die
        // eigentlich interessante Aufteilung, nicht die sonst immer triviale
        // 100 % vom (einzigen) Ziel.
        // Bewusst aus data.links (VOR dem value>0.001-Filter unten) gezählt,
        // nicht aus der gefilterten links-Liste: eine Gruppe mit z. B. drei
        // zugeordneten Geräten soll ihren Anteil zeigen, auch wenn in einem
        // bestimmten Monat nur eines davon tatsächlich Verbrauch hatte (die
        // anderen beiden dann mit Wert 0 aus der gefilterten Liste
        // herausfallen) — sonst blinkt die %-Anzeige je nach Periode ein/aus,
        // obwohl die Gruppe strukturell weiterhin mehrere Mitglieder hat.
        // Bei einer Gruppe mit wirklich nur einem einzigen Mitglied (z. B.
        // "Mobilität" → nur Wallbox) bleibt die Zahl unverändert bei 1 —
        // der ursprüngliche "immer triviale 100%"-Fall wird also weiterhin
        // korrekt unterdrückt.
        const incomingLinkCount = {};
        const outgoingLinkCount = {};
        data.links.forEach(l => {
          incomingLinkCount[l.target] = (incomingLinkCount[l.target] || 0) + 1;
          outgoingLinkCount[l.source] = (outgoingLinkCount[l.source] || 0) + 1;
        });
        // Nur Knoten mit mindestens einem sichtbaren (nicht herausgefilterten)
        // Link aufnehmen — sonst bleibt z. B. ein Erzeuger ohne Ertrag in
        // diesem Zeitraum als unverbundener, "schwebender" Knoten im Sankey
        // stehen (ECharts zeichnet ihn trotzdem, nur ohne jede Flussbahn),
        // was wie ein fehlender/kaputter Knoten statt wie "0 kWh" wirkt.
        const connectedNames = new Set();
        links.forEach(l => { connectedNames.add(l.source); connectedNames.add(l.target); });
        // Vertikal: Labels je Ebene platzieren statt pauschal rechts neben
        // den Knoten — Quellen über ihrer (oberen) Reihe, Senken unter der
        // unteren, nur der Bus behält das Label rechts. Rechts neben dünnen,
        // nebeneinanderliegenden Knoten überlagerten sich die Namen sonst
        // komplett; oben/unten mit Umbruch auf 80px (overflow:'break') bleibt
        // jeder Name über/unter seinem Knoten lesbar. Die Serie bekommt dafür
        // oben/unten Reserve (top/bottom unten).
        const narrowLabelFor = (n) => {
          if (n.role === 'source') return {position: 'top', width: 80, overflow: 'break', lineHeight: 12};
          if (n.role === 'sink') return {position: 'bottom', width: 80, overflow: 'break', lineHeight: 12};
          return {position: 'right'};
        };
        const nodes = data.nodes
          .filter(n => n.role === 'bus' || connectedNames.has(n.name))
          .map(n => ({
            // "name" bleibt die interne Sankey-Knoten-ID (verlinkt über
            // links/nodeByName, siehe colorForNode-Kommentar zu "kind") —
            // Netzbezug/Einspeisung haben optional einen eigenen Anzeige-
            // Namen (config.netzbezug_name/.einspeisung_name), der NUR den
            // Label-Text überschreibt, nicht die Knoten-Identität.
            name: n.name,
            // Auffälligkeiten (Schwellenwert-Färbung, siehe compute_flow()):
            // Umrandung statt geänderter Füllfarbe — die Füllfarbe bleibt für
            // den PV-/Netz-Mix reserviert (colorForNode), eine zweite
            // Bedeutung auf demselben Kanal wäre nicht mehr eindeutig lesbar.
            itemStyle: {
              color: colorForNode(n, palette),
              ...(n.anomaly ? {borderColor: palette.warning, borderWidth: 2.5} : {}),
            },
            label: {color: palette.ink, formatter: n.label || n.name, ...(isNarrow ? narrowLabelFor(n) : {})},
          }));
        // Vertikal (mobil) laufen die Werte-Balken HORIZONTAL — die nutzbare
        // Breite teilen sich also alle Knoten EINER Ebene plus die Lücken
        // dazwischen. Mit einem festen nodeGap fraßen die Lücken bei mehreren
        // Verbrauchern die gesamte Breite auf: bei 305 px Gerätebreite und
        // sieben Senken standen 6 × 36 px = 216 px Lücke gegen 225 px nutzbare
        // Breite. Die Balken selbst fielen dadurch auf 0–4 px zusammen (mehrere
        // Knoten damit unsichtbar UND nicht antippbar) und die Labels lagen
        // übereinander. Deshalb kein fester Wert, sondern ein festes
        // Lücken-BUDGET, das auf die Lücken der dichtesten Ebene verteilt wird
        // — die Balken behalten so immer denselben Breitenanteil, egal wie
        // viele Verbraucher zugeordnet sind.
        //
        // Ebenen-Belegung wie ECharts sie mit dem Default nodeAlign:'justify'
        // aufbaut: Quellen links/oben, der Bus für sich, Gruppenknoten (Senken,
        // die selbst noch weiterführen) dazwischen, und ALLE blattlosen Senken
        // gemeinsam in der letzten Ebene.
        const outgoingNames = new Set(links.map(l => l.source));
        let sourceCount = 0;
        let gruppenCount = 0;
        let blattCount = 0;
        nodes.forEach(n => {
          const meta = nodeByName[n.name];
          if (!meta || meta.role === 'bus') return;
          if (meta.role === 'source') sourceCount += 1;
          else if (outgoingNames.has(n.name)) gruppenCount += 1;
          else blattCount += 1;
        });
        const dichtesteEbene = Math.max(sourceCount, gruppenCount, blattCount, 1);
        // Untergrenze 120px: beim allerersten Rendern kann clientWidth noch 0
        // sein (Layout noch nicht durch) — dann lieber ein enger, aber
        // brauchbarer Wert als eine Division gegen 0.
        const narrowExtent = Math.max(120, el.clientWidth - 2 * SANKEY_NARROW_INSET);
        // 35 % Lücken / 65 % Balken, gedeckelt auf den bisherigen Wert (36):
        // bei nur zwei, drei Knoten je Ebene soll der Abstand nicht ins
        // Absurde wachsen, dort war 36 bereits stimmig.
        const narrowNodeGap = Math.max(
          4, Math.min(36, (narrowExtent * 0.35) / Math.max(1, dichtesteEbene - 1)),
        );
        // Kartenhöhe an die dichteste Ebene koppeln — aber NUR horizontal.
        // Dort stehen die Knoten einer Ebene übereinander, die feste Höhe
        // teilte sich also auf immer mehr Bänder auf: ab etwa zehn Verbrauchern
        // wurden die kleinen zu Haarlinien, die man weder erkennen noch
        // anklicken konnte. Vertikal wirkt die Knotenzahl dagegen auf die
        // BREITE (siehe narrowNodeGap oben), die Höhe bleibt dort das, was die
        // CSS-Regel vorgibt — deshalb inline-Stil zurücksetzen statt setzen,
        // damit die @media-Regel wieder greift.
        // Untergrenze 420 = der bisherige Wert, damit kleine Anlagen exakt so
        // aussehen wie vorher; Obergrenze 760, sonst schiebt eine große Anlage
        // alle folgenden Karten aus dem Blickfeld.
        const wrap = el.parentElement;
        if (wrap && wrap.classList.contains('edash-sankey-wrap')) {
          const neueHoehe = isNarrow
            ? ''
            : Math.round(Math.min(760, Math.max(420, dichtesteEbene * 52))) + 'px';
          if (wrap.style.height !== neueHoehe) {
            wrap.style.height = neueHoehe;
            // ECharts hat seine Größe beim init() gemerkt — ohne resize()
            // zeichnet es in den alten Ausschnitt und der Rest bleibt leer.
            if (chartInstance) chartInstance.resize();
          }
        }
        const fmt = (value) => this.fmt(value, value < 10 ? 2 : 1);
        const labelFor = (name) => (nodeByName[name] && nodeByName[name].label) || name;
        // Der "X % von …"-Zusatz galt bisher NUR für Link-Tooltips; ein Knoten
        // zeigte bloß "Spülmaschine: 17,5 kWh". Dieselbe Frage ("wie viel ist
        // das im Verhältnis?") wurde also je nach Trefferfläche mal beantwortet
        // und mal nicht. Jetzt EINE Regel für beides, hier zentral: Anteil
        // bevorzugt am Ziel (Zufluss-Aufschlüsselung, z. B. "Haus" aus
        // Netzbezug + Erzeugung), sonst an der Quelle — und nur, wenn die
        // betreffende Seite tatsächlich mehrere Bahnen hat, sonst wären es
        // immer triviale 100 %.
        // Ein auf 100 % gerundeter Anteil wird unterdrückt: er sagt nichts aus
        // und führt sogar in die Irre. Der Fall tritt auf, weil die Link-Zahlen
        // bewusst aus den UNGEFILTERTEN data.links kommen (siehe oben) — eine
        // Gruppe mit drei Geräten, von denen in dieser Periode nur eines lief,
        // gilt weiterhin als mehrgliedrig, ihr einziger Beitrag ist dann aber
        // rechnerisch 100 % und liest sich wie "die Gruppe hat nur dieses eine
        // Gerät". Dieselbe Absicht wie beim Ausschluss von Knoten mit nur einer
        // Bahn, nur zusätzlich für den periodenabhängigen Fall.
        const anteil = (value, bezug, bezugName) => {
          if (!(bezug > 0)) return '';
          const pct = (value / bezug) * 100;
          if (pct >= 99.5) return '';
          return ` (${this.fmt(pct, 0)} % von ${labelFor(bezugName)})`;
        };
        const shareSuffix = (sourceName, targetName, value) => {
          const target = nodeByName[targetName];
          const source = nodeByName[sourceName];
          if ((incomingLinkCount[targetName] || 0) > 1 && target) {
            return anteil(value, target.value, targetName);
          }
          if ((outgoingLinkCount[sourceName] || 0) > 1 && source) {
            return anteil(value, source.value, sourceName);
          }
          return '';
        };
        // Für den Knoten-Tooltip: ein Knoten hat keine eigene Quelle/Ziel-
        // Beziehung, aber solange er GENAU EINE Bahn auf einer Seite hat, ist
        // sein Wert identisch mit der dieser Bahn — dann lässt sich dieselbe
        // Regel anwenden. Der Bus ("Haus") hat auf beiden Seiten mehrere und
        // bekommt deshalb keinen Zusatz: er ist die Bezugsgröße selbst, "100 %
        // von sich" wäre keine Information. Wie bei incomingLinkCount bewusst
        // aus data.links (vor dem value>0.001-Filter) — die Struktur soll
        // nicht je nach Periode wechseln.
        const soleIncoming = {};
        const soleOutgoing = {};
        data.links.forEach(l => {
          soleIncoming[l.target] = incomingLinkCount[l.target] === 1 ? l : null;
          soleOutgoing[l.source] = outgoingLinkCount[l.source] === 1 ? l : null;
        });
        const nodeShareSuffix = (name, value) => {
          if (soleIncoming[name]) return shareSuffix(soleIncoming[name].source, name, value);
          if (soleOutgoing[name]) return shareSuffix(name, soleOutgoing[name].target, value);
          return '';
        };
        chartInstance.setOption({
          tooltip: {
            trigger: 'item',
            formatter: (p) => {
              if (p.dataType !== 'edge') {
                const node = nodeByName[p.name];
                const label = labelFor(p.name);
                const head = `${label}: ${fmt(p.value)} kWh${nodeShareSuffix(p.name, p.value)}`;
                if (node && node.anomaly) {
                  return `${head}<br/>`
                    + `<span style="color:${palette.warning}">+${node.anomaly_pct} % über dem Schnitt `
                    + `der letzten Perioden (${fmt(node.anomaly_baseline)} kWh)</span>`;
                }
                return head;
              }
              return `${labelFor(p.data.source)} → ${labelFor(p.data.target)}: `
                + `${fmt(p.data.value)} kWh${shareSuffix(p.data.source, p.data.target, p.data.value)}`;
            },
          },
          series: [{
            type: 'sankey',
            // Mobil: vertikal statt horizontal (Quellen oben, Bus in der
            // Mitte, Verbraucher/Speicher/Netz darunter) — auf schmalen
            // Bildschirmen liest sich das deutlich besser als ein seitlich
            // gequetschter horizontaler Sankey. (Zwischenzeitlich horizontal
            // getestet und auf Wunsch wieder auf vertikal zurückgestellt.)
            orient: isNarrow ? 'vertical' : 'horizontal',
            // ECharts-Default wäre 'justify': das schiebt ALLE Knoten ohne
            // weiterführenden Fluss gemeinsam in die letzte Ebene — also
            // Einspeisung, Grundlast, Speicherladung und ungruppierte Geräte
            // neben die Mitglieder der Verbrauchergruppen, obwohl sie nur
            // EINEN Schritt vom Bus entfernt sind. Ihre Bänder mussten dadurch
            // quer durch die Gruppen-Ebene laufen (gemessen: 5 Links über zwei
            // Ebenen hinweg, u. a. "Haus → Trockner", der direkt am Bus hängt)
            // — das waren die sichtbaren Überschneidungen. 'left' setzt jeden
            // Knoten dorthin, wo er tatsächlich hingehört (eine Ebene hinter
            // seiner Quelle): keine ebenenüberspringenden Links mehr, Summe
            // der vertikalen Umlenkung von 2901 px auf 2137 px (−26 %).
            nodeAlign: 'left',
            data: nodes,
            links,
            emphasis: {focus: 'adjacency'},
            lineStyle: {color: 'gradient', opacity: 0.42, curveness: 0.5},
            // Vertikal stehen mehrere Knoten in derselben Ebene nebeneinander
            // (statt untereinander wie horizontal) — kleinere Schrift und
            // mehr nodeGap geben den Labels dort mehr Luft, bevor sie sich
            // überlappen.
            label: {fontFamily: 'IBM Plex Sans, sans-serif', fontSize: isNarrow ? 10 : 12},
            // Vertikal: Platz für die bis zu dreizeiligen Labels über der
            // oberen und unter der unteren Knotenreihe (siehe narrowLabelFor)
            // — und zusätzlich für die zweite, versetzte Label-Reihe
            // (staggerNarrowLabels() unten). Der Wert steht schon im ERSTEN
            // setOption(), damit der zweite Durchgang nur noch Labels ändert
            // und das Layout nicht verschiebt.
            top: isNarrow ? SANKEY_NARROW_LABEL_RESERVE : '5%',
            bottom: isNarrow ? SANKEY_NARROW_LABEL_RESERVE : '5%',
            // Seitlich SANKEY_NARROW_INSET: die 80px breiten, mittig über/unter
            // dem Knoten zentrierten Labels ragen sonst am äußersten Knoten aus
            // der Karte. Derselbe Wert geht in narrowExtent oben ein.
            left: isNarrow ? SANKEY_NARROW_INSET : '5%',
            right: isNarrow ? SANKEY_NARROW_INSET : '20%',
            nodeWidth: 14,
            // Vertikal mehr Abstand zwischen nebeneinanderliegenden Knoten,
            // damit sich die Labels dünner Nachbarknoten seltener überlagern —
            // aber nur so viel, wie neben den Balken übrig bleibt (siehe
            // narrowNodeGap oben).
            nodeGap: isNarrow ? narrowNodeGap : 10,
          }],
        }, true);
        chartInstance.off('click');
        chartInstance.on('click', (params) => {
          if (params.dataType !== 'node') return;
          const node = nodeByName[params.name];
          if (node && node.entity_id) {
            window.location.href = `entities/${encodeURIComponent(node.entity_id)}`;
          }
        });
        // Erst NACH dem Layout möglich (siehe staggerNarrowLabels) — und nur
        // vertikal, horizontal stehen die Knoten einer Ebene untereinander und
        // ihre Labels können sich gar nicht waagerecht überlagern.
        if (isNarrow) this.staggerNarrowLabels(nodes);
      },

      // Dieselbe Donut-Gestaltung wie #storage-pie in statistik.html
      // ("Speichernutzung"): radius/emphasis/Tooltip-Format 1:1 übernommen,
      // keine eigene Legende (die Tabelle daneben übernimmt das).
      renderShareChart(breakdown) {
        const el = this.$refs.shareChartEl;
        if (!el || typeof echarts === 'undefined' || !breakdown.length) return;
        if (!shareChartInstance) shareChartInstance = echarts.init(el);
        const surface = cssVar('--surface');
        const fmt = (value) => this.fmt(value, value < 10 ? 2 : 1);
        shareChartInstance.setOption({
          tooltip: {
            trigger: 'item',
            formatter: (p) => `${p.marker}${p.name}: <strong>${fmt(p.value)} kWh</strong> (${this.fmt(p.percent, 0)} %)`,
          },
          series: [{
            type: 'pie',
            radius: ['52%', '85%'],
            center: ['50%', '50%'],
            avoidLabelOverlap: true,
            itemStyle: {borderColor: surface, borderWidth: 2},
            label: {show: false},
            labelLine: {show: false},
            emphasis: {
              scaleSize: 6,
              itemStyle: {shadowBlur: 12, shadowColor: 'rgba(0,0,0,0.25)'},
            },
            data: breakdown.map((item, idx) => ({
              name: item.name, value: item.value,
              itemStyle: {color: this.shareColor(idx)},
            })),
          }],
        }, true);
        // Dasselbe ResizeObserver-Muster wie renderHeatmap()/#storage-pie:
        // die Kachel steht unter x-show und ist beim ersten setOption() oft
        // noch display:none — ECharts fällt dann auf seine 100px-Standard-
        // breite zurück und der Donut blieb (v. a. mobil, wo der Container
        // per CSS auf 260px zentriert wird) winzig und linksbündig. Der
        // window-resize-Listener allein greift beim Sichtbarwerden nicht.
        if (typeof ResizeObserver !== 'undefined') {
          if (shareResizeObserver) shareResizeObserver.disconnect();
          shareResizeObserver = new ResizeObserver(() => {
            if (shareChartInstance) shareChartInstance.resize();
          });
          shareResizeObserver.observe(el);
        }
      },

      // Tabellenzeile ↔ Donut-Segment verbinden (Hover), identisch zur
      // Zeile/Donut-Kopplung bei "Speichernutzung" in statistik.html.
      highlightShare(name) {
        if (!shareChartInstance) return;
        shareChartInstance.dispatchAction({type: 'highlight', seriesIndex: 0, name});
        shareChartInstance.dispatchAction({type: 'showTip', seriesIndex: 0, name});
      },
      unhighlightShare(name) {
        if (!shareChartInstance) return;
        shareChartInstance.dispatchAction({type: 'downplay', seriesIndex: 0, name});
        shareChartInstance.dispatchAction({type: 'hideTip'});
      },
    };
  };
})();
