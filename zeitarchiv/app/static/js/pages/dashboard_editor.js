    function dashboardEditor() {
      return {
        dashboardId: DASHBOARD_ID,
        name: DASHBOARD.name,
        locked: DASHBOARD.locked,
        preciseMode: DASHBOARD.preciseMode,
        fillGaps: DASHBOARD.fillGaps,
        saving: false,
        savedMessage: '',

        async save() {
          if (!this.name.trim()) { appAlert('Bitte einen Namen für das Dashboard angeben.'); return; }
          this.saving = true;
          this.savedMessage = '';
          try {
            const url = this.dashboardId ? `${BASE}/dashboards/${this.dashboardId}/rename` : `${BASE}/dashboards`;
            const res = await fetch(url, {
              method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: this.name.trim()}),
            });
            if (!res.ok) {
              const err = await res.json().catch(() => ({}));
              appAlert(err.detail || 'Speichern fehlgeschlagen.');
              return;
            }
            const data = await res.json();
            if (this.dashboardId) {
              const lockRes = await fetch(`${BASE}/dashboards/${this.dashboardId}/lock`, {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({locked: this.locked}),
              });
              if (!lockRes.ok) { appAlert('Speichern der Fixierung fehlgeschlagen.'); return; }
              const preciseRes = await fetch(`${BASE}/dashboards/${this.dashboardId}/precise-mode`, {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({precise_mode: this.preciseMode}),
              });
              if (!preciseRes.ok) { appAlert('Speichern des Präzisen Modus fehlgeschlagen.'); return; }
              const fillGapsRes = await fetch(`${BASE}/dashboards/${this.dashboardId}/fill-gaps`, {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fill_gaps: this.fillGaps}),
              });
              if (!fillGapsRes.ok) { appAlert('Speichern von "Lücken auffüllen" fehlgeschlagen.'); return; }
            }
            window.location.href = this.dashboardId ? `${BASE}/dashboards/${this.dashboardId}` : `${BASE}/dashboards/${data.id}`;
          } finally {
            this.saving = false;
          }
        },

        async deleteDashboard() {
          if (!await appConfirm('Dieses Dashboard wirklich löschen? Die Kachel-Anordnung geht verloren — die zugrunde liegenden Charts und Tabellen bleiben erhalten und lassen sich jederzeit auf einem anderen Dashboard neu anheften.', {danger: true})) return;
          const res = await fetch(`${BASE}/dashboards/${this.dashboardId}/delete`, {method: 'POST'});
          if (res.ok) {
            window.location.href = `${BASE}/dashboards`;
          } else {
            appAlert('Löschen fehlgeschlagen.');
          }
        },
      };
    }
