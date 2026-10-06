    async function deleteAllValues() {
      const confirmed = await window.appConfirm(
        t('Wirklich ALLE archivierten Werte von {entity} endgültig löschen? Hot Buffer, Monatsarchive, Rollups und Löschmarkierungen werden entfernt. Die Entität und ihre Konfiguration bleiben bestehen. Dies kann nicht rückgängig gemacht werden.', {entity: ENTITY_ID}),
        {danger: true, confirmLabel: t('Alle Werte löschen')}
      );
      if (!confirmed) return;
      const button = document.getElementById('delete-all-values-btn');
      button.disabled = true;
      try {
        const response = await fetch(`${BASE}/entities/${ENTITY_ID}/values/delete-all`, {method: 'POST'});
        if (!response.ok) throw new Error('delete failed');
        window.location.reload();
      } catch (error) {
        button.disabled = false;
        appAlert(t('Die Werte konnten nicht gelöscht werden.'));
      }
    }

    async function deleteEntity() {
      const confirmed = await window.appConfirm(
        t('Entität {entity} vollständig aus Zeitarchiv entfernen? Alle Werte und ihre individuelle Zeitarchiv-Konfiguration werden endgültig gelöscht. Wenn die Home-Assistant-Integration diese Entität weiter sendet, wird sie automatisch neu angelegt. Dies kann nicht rückgängig gemacht werden.', {entity: ENTITY_ID}),
        {danger: true, confirmLabel: t('Entität entfernen')}
      );
      if (!confirmed) return;
      const button = document.getElementById('delete-entity-btn');
      button.disabled = true;
      try {
        const response = await fetch(`${BASE}/entities/${ENTITY_ID}/delete`, {method: 'POST'});
        if (!response.ok) throw new Error('delete failed');
        window.location.href = `${BASE}/entities`;
      } catch (error) {
        button.disabled = false;
        appAlert(t('Die Entität konnte nicht entfernt werden.'));
      }
    }
