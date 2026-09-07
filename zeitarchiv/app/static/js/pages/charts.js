    // Diese Seite lädt (anders als die Entitäten-Übersicht) nicht per htmx —
    // ein einfacher Reload nach dem Umschalten reicht, damit Favoriten
    // (Sortierung: is_favorite zuerst, siehe list_saved_charts()) an ihre
    // neue Position wandern, statt nur lokal die Stern-Farbe zu drehen.
    document.querySelector('.settings-panel').addEventListener('favorite-changed', () => {
      window.location.reload();
    });

    async function deleteChart(id, btn) {
      if (!await appConfirm('Dieses Chart wirklich löschen?', {danger: true})) return;
      btn.disabled = true;
      fetch(`charts/${id}/delete`, {method: 'POST'}).then(r => {
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
    // damit Entitätenzahl/Zeitraum und Sortierung der neuen Karte korrekt sind.
    async function duplicateChart(id) {
      try {
        const res = await fetch(`charts/${id}/duplicate`, {method: 'POST'});
        if (res.ok) {
          window.location.reload();
        } else {
          appAlert('Duplizieren fehlgeschlagen.');
        }
      } catch (e) {
        appAlert('Duplizieren fehlgeschlagen.');
      }
    }
