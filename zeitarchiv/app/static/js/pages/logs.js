    // Mobil einklappbare Protokollierungs-Karte (CSS: @media(max-width:700px)
    // oben). Die Klasse wird nur gesetzt, wenn der schmale Breakpoint gilt —
    // am Desktop hat sie ohnehin keine Wirkung, so bleibt die Karte dort
    // ohne Zustandslogik immer offen. Beim Wechsel schmal→breit (z. B.
    // Tablet drehen) wird die Klasse entfernt, sonst käme sie beim Zurück-
    // drehen im zuletzt gewählten Zustand wieder — gewollt: Standard zu.
    (function () {
      const section = document.getElementById('log-settings-section');
      const toggle = document.getElementById('log-settings-toggle');
      if (!section || !toggle) return;
      const narrow = window.matchMedia('(max-width:700px)');
      const setCollapsed = (collapsed) => {
        section.classList.toggle('is-collapsed', collapsed);
        toggle.setAttribute('aria-expanded', String(!collapsed));
      };
      const applyMode = () => {
        if (narrow.matches) {
          toggle.setAttribute('role', 'button');
          toggle.setAttribute('tabindex', '0');
          setCollapsed(true);
        } else {
          toggle.removeAttribute('role');
          toggle.removeAttribute('tabindex');
          toggle.removeAttribute('aria-expanded');
          section.classList.remove('is-collapsed');
        }
      };
      toggle.addEventListener('click', () => {
        if (narrow.matches) setCollapsed(!section.classList.contains('is-collapsed'));
      });
      toggle.addEventListener('keydown', (e) => {
        if (!narrow.matches) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle.click(); }
      });
      narrow.addEventListener('change', applyMode);
      applyMode();
    })();
    (() => {
      const output = document.getElementById('log-output');
      const source = document.getElementById('log-source');
      const status = document.getElementById('log-status');
      const sourceMode = document.getElementById('log-source-mode');
      const level = document.getElementById('log-filter-input');
      const search = document.getElementById('log-search');
      const auto = document.getElementById('log-auto');
      const download = document.getElementById('log-download');
      let sequence = 0;
      let searchTimer = null;

      function params() {
        const query = new URLSearchParams({level: level.value, search: search.value, source: sourceMode.value});
        return query;
      }

      async function refreshLogs() {
        const requestId = ++sequence;
        status.textContent = 'Wird aktualisiert …';
        try {
          const response = await fetch(`api/logs?${params()}&limit=500`);
          if (!response.ok) throw new Error('HTTP ' + response.status);
          const data = await response.json();
          if (requestId !== sequence) return;
          const wasNearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 80;
          output.textContent = data.lines.length ? data.lines.join('\n') : 'Keine passenden Protokolleinträge.';
          source.textContent = `Quelle: ${data.source}${data.fallback ? ' (Fallback)' : ''}`;
          status.textContent = `${data.count} Zeilen · ${new Date(data.generated_at * 1000).toLocaleTimeString('de-DE')}`;
          download.href = `logs/download?${params()}`;
          if (wasNearBottom) output.scrollTop = output.scrollHeight;
        } catch (error) {
          if (requestId !== sequence) return;
          status.textContent = 'Aktualisierung fehlgeschlagen';
          output.textContent = 'Protokoll konnte nicht geladen werden.';
        }
      }

      document.getElementById('log-refresh').addEventListener('click', refreshLogs);
      sourceMode.addEventListener('change', refreshLogs);
      level.addEventListener('change', refreshLogs);
      search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(refreshLogs, 250);
      });
      setInterval(() => { if (auto.checked) refreshLogs(); }, 15000);
      refreshLogs();
    })();
