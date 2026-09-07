    document.querySelector('.settings-panel').addEventListener('favorite-changed', () => {
      window.location.reload();
    });

    async function deleteTable(id, btn) {
      if (!await appConfirm('Diese Tabelle wirklich löschen?', {danger: true})) return;
      btn.disabled = true;
      fetch(`tables/${id}/delete`, {method: 'POST'}).then(r => {
        if (r.ok) {
          btn.closest('.chart-card').remove();
        } else {
          btn.disabled = false;
          appAlert('Löschen fehlgeschlagen.');
        }
      }).catch(() => { btn.disabled = false; appAlert('Löschen fehlgeschlagen.'); });
    }

    // Kopie bleibt auf der Liste sichtbar (kein Sprung in den Editor) — ein
    // Reload reicht, dieselbe Konvention wie beim Favoriten-Umschalten oben,
    // damit Zeilen-/Spaltenzahl und Sortierung der neuen Karte korrekt sind.
    async function duplicateTable(id) {
      try {
        const res = await fetch(`tables/${id}/duplicate`, {method: 'POST'});
        if (res.ok) {
          window.location.reload();
        } else {
          appAlert('Duplizieren fehlgeschlagen.');
        }
      } catch (e) {
        appAlert('Duplizieren fehlgeschlagen.');
      }
    }
