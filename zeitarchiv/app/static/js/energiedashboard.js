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
  let lastSankeyData = null;
  let lastSankeyIsNarrow = null;
  let currentRenderChart = null;

  window.energieFlow = function energieFlow() {
    return {
      ranges: [{key: 'hour', label: 'Stunde'}, {key: 'day', label: 'Tag'}, {key: 'month', label: 'Monat'}, {key: 'year', label: 'Jahr'}],
      range: 'day',
      offset: 0,
      loading: false,
      loadError: false,
      kpi: {},
      kpiCompare: {},
      compareLabel: '',
      quality: {plausible: true, checks: []},
      periodText: '',
      hasFlow: false,
      verbraucherBreakdown: [],
      erzeugerBreakdown: [],
      speicherEfficiencyTrend: [],
      speicherSocTrend: [],
      autarkieTrend: [],
      eigenverbrauchTrend: [],
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
        // Unabhängig von Tag/Monat/Jahr-Auswahl — die Heatmap zeigt immer
        // die letzten 7 Tage, muss also nicht bei jedem setRange()/goBack()
        // neu geladen werden.
        this.loadHeatmap();
        if (!resizeListenerAdded) {
          resizeListenerAdded = true;
          window.addEventListener('resize', () => {
            if (chartInstance) chartInstance.resize();
            if (shareChartInstance) shareChartInstance.resize();
            if (heatmapChartInstance) heatmapChartInstance.resize();
            // Sankey-Orientierung (horizontal/vertikal) nur neu rendern, wenn
            // der Breakpoint wirklich über-/unterschritten wurde — sonst bei
            // jedem Pixel-Resize unnötig den ganzen Chart neu aufbauen.
            const isNarrow = window.innerWidth < SANKEY_NARROW_BREAKPOINT;
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
        this.load();
      },
      goBack() { this.offset -= 1; this.load(); },
      goForward() { if (this.canGoForward) { this.offset += 1; this.load(); } },

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
          this.speicherEfficiencyTrend = data.speicher_efficiency_trend || [];
          this.speicherSocTrend = data.speicher_soc_trend || [];
          this.autarkieTrend = data.autarkie_trend || [];
          this.eigenverbrauchTrend = data.eigenverbrauch_trend || [];
          this.renderChart(data);
          this.renderShareChart(this.verbraucherBreakdown);
          this.renderSparklines(data.kpi_series || {});
        } catch (e) {
          this.loadError = true;
        } finally {
          this.loading = false;
        }
      },

      async loadHeatmap() {
        try {
          const res = await fetch('energiedashboard/heatmap');
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

      renderChart(data) {
        const el = this.$refs.sankeyEl;
        if (!el || typeof echarts === 'undefined') return;
        lastSankeyData = data;
        const isNarrow = window.innerWidth < SANKEY_NARROW_BREAKPOINT;
        lastSankeyIsNarrow = isNarrow;
        const palette = colors();
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
        // Nur Knoten mit mindestens einem sichtbaren (nicht herausgefilterten)
        // Link aufnehmen — sonst bleibt z. B. ein Erzeuger ohne Ertrag in
        // diesem Zeitraum als unverbundener, "schwebender" Knoten im Sankey
        // stehen (ECharts zeichnet ihn trotzdem, nur ohne jede Flussbahn),
        // was wie ein fehlender/kaputter Knoten statt wie "0 kWh" wirkt.
        const connectedNames = new Set();
        links.forEach(l => { connectedNames.add(l.source); connectedNames.add(l.target); });
        const nodes = data.nodes
          .filter(n => n.role === 'bus' || connectedNames.has(n.name))
          .map(n => ({
            name: n.name,
            itemStyle: {color: colorForNode(n, palette)},
            label: {color: palette.ink},
          }));
        const fmt = (value) => this.fmt(value, value < 10 ? 2 : 1);
        chartInstance.setOption({
          tooltip: {
            trigger: 'item',
            formatter: (p) => {
              if (p.dataType !== 'edge') return `${p.name}: ${fmt(p.value)} kWh`;
              const target = nodeByName[p.data.target];
              const pct = target && target.value > 0 ? this.fmt((p.data.value / target.value) * 100, 0) : null;
              const share = pct != null ? ` (${pct} % von ${p.data.target})` : '';
              return `${p.data.source} → ${p.data.target}: ${fmt(p.data.value)} kWh${share}`;
            },
          },
          series: [{
            type: 'sankey',
            // Mobil: vertikal statt horizontal (Quellen oben, Bus in der
            // Mitte, Verbraucher/Speicher/Netz darunter) — auf schmalen
            // Bildschirmen liest sich das deutlich besser als ein seitlich
            // gequetschter horizontaler Sankey.
            orient: isNarrow ? 'vertical' : 'horizontal',
            data: nodes,
            links,
            emphasis: {focus: 'adjacency'},
            lineStyle: {color: 'gradient', opacity: 0.42, curveness: 0.5},
            // Vertikal stehen mehrere Knoten in derselben Ebene nebeneinander
            // (statt untereinander wie horizontal) — kleinere Schrift und
            // mehr nodeGap geben den Labels dort mehr Luft, bevor sie sich
            // überlappen.
            label: {fontFamily: 'IBM Plex Sans, sans-serif', fontSize: isNarrow ? 10 : 12},
            nodeWidth: 14,
            nodeGap: isNarrow ? 20 : 10,
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
