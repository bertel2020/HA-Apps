    // Aus-/Einblenden des heutigen Tipps (im "Alle Tipps"-Dialog, siehe
    // _tips_list_body.html) wirkt sich auch auf das Glocken-Menü aus —
    // htmx:afterRequest bubbelt vom auslösenden Button bis hierher hoch,
    // ein einziger Listener auf dem Container reicht deshalb für beide
    // Buttons (Ausblenden/Wieder einblenden).
    document.getElementById('tips-list-body')?.addEventListener('htmx:afterRequest', () => {
      if (window.refreshNoticePanel) refreshNoticePanel();
    });

    async function unmuteNotice(evt) {
      const btn = evt.currentTarget;
      const row = btn.closest('.settings-compact-row');
      const noticeId = row ? row.dataset.noticeId : null;
      if (!noticeId) return;
      btn.disabled = true;
      try {
        const res = await fetch('notices/' + encodeURIComponent(noticeId) + '/unmute', {method: 'POST'});
        if (!res.ok) { btn.disabled = false; return; }
        if (row) row.remove();
        const list = document.getElementById('muted-notices-list');
        if (list && !list.querySelector('.settings-compact-row')) {
          list.outerHTML = '<div class="empty">Keine stummgeschalteten Meldungen.</div>';
        }
        // Die zurückgeholte Meldung erscheint wieder im Glocken-Menü — ohne
        // diesen Refresh (definiert in _topnav.html, global verfügbar) blieb
        // sie dort bis zum manuellen Neuladen unsichtbar.
        refreshNoticePanel();
      } catch (e) {
        btn.disabled = false;
      }
    }
    function toggleTokenVisibility(btn) {
      const el = document.getElementById('token-value');
      const masked = el.dataset.masked === 'true';
      el.textContent = masked ? el.dataset.token : '••••••••••••••••';
      el.dataset.masked = masked ? 'false' : 'true';
      btn.textContent = masked ? 'Verbergen' : 'Anzeigen';
    }
    function copyToken() {
      const el = document.getElementById('token-value');
      const status = document.getElementById('token-copy-status');
      const text = el.dataset.token;
      const showStatus = (msg) => {
        status.textContent = msg;
        setTimeout(() => { status.textContent = ' '; }, 2500);
      };
      // navigator.clipboard existiert nur in sicheren Kontexten (HTTPS oder
      // localhost) — die App wird hier aber typischerweise über einfaches
      // HTTP im lokalen Netz erreicht (Home-Assistant-Add-on ohne eigenes
      // Zertifikat), wo navigator.clipboard schlicht undefined ist. Bisher
      // führte das zu einem stillen TypeError beim Aufruf von .writeText
      // (Button "tat nichts"). Fallback über ein unsichtbares Textfeld +
      // execCommand('copy') funktioniert auch dort noch.
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(
          () => showStatus('In Zwischenablage kopiert.'),
          () => showStatus('Kopieren fehlgeschlagen — Token bitte manuell markieren und kopieren.')
        );
        return;
      }
      const temp = document.createElement('textarea');
      temp.value = text;
      temp.style.position = 'fixed';
      temp.style.opacity = '0';
      document.body.appendChild(temp);
      temp.focus();
      temp.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(temp);
      showStatus(ok ? 'In Zwischenablage kopiert.' : 'Kopieren fehlgeschlagen — Token bitte manuell markieren und kopieren.');
    }
