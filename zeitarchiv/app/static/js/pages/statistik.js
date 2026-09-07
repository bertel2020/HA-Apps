    const themeStyle = getComputedStyle(document.documentElement);
    const chartColor = number => themeStyle.getPropertyValue(`--chart-${number}`).trim();
    // Dieselbe schemaabhängige Farbfolge wie die Multi-Entitäts-Charts.
    const STORAGE_COLORS = {
      archive: chartColor(1), rollup: chartColor(2), hot: chartColor(3),
      backups: chartColor(4), import: chartColor(5), index: chartColor(8),
    };

    function fmtSize(bytes) {
      if (bytes <= 0) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let value = bytes, i = 0;
      while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
      return NumberFormat.fmt(value, i === 0 ? 0 : 1) + ' ' + units[i];
    }

    let growthChart = null;
    function renderGrowth(points) {
      if (points.length < 2) return;
      if (!growthChart) growthChart = echarts.init(document.getElementById('growth-chart'));
      const rowsData = points.map(p => [p.ts * 1000, p.total_rows]);
      const sizeData = points.map(p => [p.ts * 1000, p.total_size_bytes]);
      const inkMuted = getComputedStyle(document.body).getPropertyValue('--ink-muted');
      const fontMono = getComputedStyle(document.body).getPropertyValue('--font-mono');
      const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
      growthChart.setOption({
        textStyle: {fontFamily: fontMono, color: inkMuted, fontSize: Math.round(12 * uiFontScale * 10) / 10},
        // Kein eigener yAxis-Titel ("Datensätze"/"Größe") mehr — stand bei
        // wenig Platz über der Achse (Zeitraum-Auswahl direkt darüber) und war
        // ohnehin redundant zur Legende unten, die dieselbe Zuordnung schon
        // per Farbe herstellt.
        grid: {left: 10, right: 10, top: 16, bottom: 40, containLabel: true},
        xAxis: {type: 'time'},
        yAxis: [
          {type: 'value', scale: true, axisLabel: {formatter: v => v.toLocaleString(NumberFormat.LOCALE)}},
          {type: 'value', scale: true, axisLabel: {formatter: fmtSize}, splitLine: {show: false}},
        ],
        legend: {bottom: 0, textStyle: {color: inkMuted}},
        tooltip: {
          trigger: 'axis',
          formatter: params => {
            const d = new Date(params[0].value[0]);
            const pad = n => String(n).padStart(2, '0');
            const time = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
            return `${time}<br>` + params.map(p =>
              `${p.marker}${p.seriesName}: <strong>${p.seriesIndex === 0 ? p.value[1].toLocaleString(NumberFormat.LOCALE) : fmtSize(p.value[1])}</strong>`
            ).join('<br>');
          },
        },
        series: [
          {
            name: 'Datensätze', type: 'line', yAxisIndex: 0, data: rowsData,
            itemStyle: {color: getComputedStyle(document.body).getPropertyValue('--chart-line')},
            lineStyle: {width: 1.5}, showSymbol: false,
          },
          {
            name: 'Größe', type: 'line', yAxisIndex: 1, data: sizeData,
            itemStyle: {color: getComputedStyle(document.body).getPropertyValue('--chart-bar')},
            lineStyle: {width: 1.5}, showSymbol: false,
          },
        ],
      });
      growthChart.resize();
    }

    let growthRangeSeq = 0;
    async function setGrowthRange(range) {
      document.querySelectorAll('#growth-range-seg button').forEach(b => b.classList.toggle('active', b.dataset.range === range));
      const requestId = ++growthRangeSeq;
      const res = await fetch(`api/stats-snapshots?range=${range}`);
      const data = await res.json();
      if (requestId !== growthRangeSeq) return;
      renderGrowth(data.points || []);
    }

    if (GROWTH_POINTS.length >= 2) {
      renderGrowth(GROWTH_POINTS);
      window.addEventListener('resize', () => growthChart && growthChart.resize());
      new ResizeObserver(() => growthChart && growthChart.resize()).observe(document.getElementById('growth-chart'));
    }

    if (STORAGE_BREAKDOWN.some(row => row.bytes > 0)) {
      document.querySelectorAll('.storage-dot').forEach(el => {
        el.style.background = STORAGE_COLORS[el.dataset.key] || 'var(--ink-faint)';
      });
      const pie = echarts.init(document.getElementById('storage-pie'));
      const inkMuted = getComputedStyle(document.body).getPropertyValue('--ink-muted');
      const fontMono = getComputedStyle(document.body).getPropertyValue('--font-mono');
      const surface = getComputedStyle(document.body).getPropertyValue('--surface');
      const uiFontScale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--font-scale')) || 1;
      // Kein eigenes Legende-Element — die Tabelle daneben zeigt bereits jede
      // Kategorie mit demselben Farbpunkt, vollständiger Größe und Anteil auf
      // einen Blick, ohne die Umbruch-/Scroll-Klemme, in die eine ECharts-
      // Legende bei sechs sehr ungleich großen Kategorien sonst gerät. Der
      // Donut bleibt damit rein visuell (Proportionen + Tooltip), die Tabelle
      // übernimmt die Rolle der Legende vollständig.
      pie.setOption({
        textStyle: {fontFamily: fontMono, color: inkMuted, fontSize: Math.round(12 * uiFontScale * 10) / 10},
        tooltip: {
          trigger: 'item',
          formatter: p => `${p.marker}${p.name}: <strong>${fmtSize(p.value)}</strong> (${p.percent}%)`,
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
          data: STORAGE_BREAKDOWN.filter(row => row.bytes > 0).map(row => ({
            name: row.label, value: row.bytes, itemStyle: {color: STORAGE_COLORS[row.key]},
          })),
        }],
      });
      window.addEventListener('resize', () => pie.resize());
      new ResizeObserver(() => pie.resize()).observe(document.getElementById('storage-pie'));

      // Tabellenzeile übernimmt die Funktion einer Legende: Hover markiert das
      // zugehörige Segment im Donut (ECharts' highlight/downplay-Aktionen),
      // Klick öffnet den Tooltip an derselben Stelle — Tabelle und Chart
      // bleiben dadurch spürbar verbunden statt zwei getrennte Ansichten.
      document.querySelectorAll('.storage-dot').forEach(dot => {
        const row = dot.closest('tr');
        if (!row) return;
        const label = row.dataset.storageLabel;
        row.style.cursor = 'default';
        row.addEventListener('mouseenter', () => {
          pie.dispatchAction({type: 'highlight', seriesIndex: 0, name: label});
          pie.dispatchAction({type: 'showTip', seriesIndex: 0, name: label});
        });
        row.addEventListener('mouseleave', () => {
          pie.dispatchAction({type: 'downplay', seriesIndex: 0, name: label});
          pie.dispatchAction({type: 'hideTip'});
        });
      });
    }
