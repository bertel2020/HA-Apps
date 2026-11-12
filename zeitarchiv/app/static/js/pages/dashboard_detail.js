// Schloss neben dem Dashboard-Titel: Klick fragt nach und hebt die Fixierung auf, damit man nicht
// über Dashboards → Bearbeiten gehen muss. Die Route ist dieselbe wie im Editor.
(function () {
  const btn = document.querySelector('.dash-locked-badge[data-unlock-url]');
  if (!btn) return;
  btn.addEventListener('click', async function () {
    if (!await appConfirm(t('Fixierung aufheben und das Dashboard bearbeiten?'), {})) return;
    try {
      const res = await fetch(btn.dataset.unlockUrl, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({locked: false}),
      });
      if (!res.ok) { appAlert(t('Aufheben der Fixierung fehlgeschlagen.')); return; }
      window.location.reload();
    } catch (error) {
      appAlert(t('Aufheben der Fixierung fehlgeschlagen.'));
    }
  });
})();
