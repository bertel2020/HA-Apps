    // Reload statt lokalem Umsortieren, dieselbe Konvention wie tables.html/
    // charts.html — sonst müsste hier die is_favorite-Sortierung von
    // list_dashboards() im Frontend nachgebaut werden.
    document.querySelector('.settings-panel').addEventListener('favorite-changed', () => {
      window.location.reload();
    });

    // Reload statt DOM-Update: die Kachel UND das Topnav-Dropdown
    // (nav_energiedashboard_enabled) müssen konsistent umschalten — dieselbe
    // Konvention wie setDefaultDashboard() unten.
    async function toggleEnergieDashboard(btn) {
      const willEnable = !btn.classList.contains('is-on');
      btn.disabled = true;
      try {
        const res = await fetch(`energiedashboard/${willEnable ? 'enable' : 'disable'}`, {method: 'POST'});
        if (res.ok) {
          window.location.reload();
        } else {
          btn.disabled = false;
          appAlert('Konnte nicht umgeschaltet werden.');
        }
      } catch (e) {
        btn.disabled = false;
        appAlert('Konnte nicht umgeschaltet werden.');
      }
    }

    async function deleteDashboard(id, btn) {
      if (!await appConfirm('Dieses Dashboard wirklich löschen? Die Kachel-Anordnung geht verloren — die zugrunde liegenden Charts und Tabellen bleiben erhalten und lassen sich jederzeit auf einem anderen Dashboard neu anheften.', {danger: true})) return;
      btn.disabled = true;
      fetch(`dashboards/${id}/delete`, {method: 'POST'}).then(r => {
        if (r.ok) {
          document.getElementById(`dash-card-${id}`).remove();
        } else {
          btn.disabled = false;
          appAlert('Löschen fehlgeschlagen.');
        }
      }).catch(() => { btn.disabled = false; appAlert('Löschen fehlgeschlagen.'); });
    }

    // Kopie bleibt auf der Liste sichtbar (kein Sprung in den Editor) — ein
    // Reload reicht, dieselbe Konvention wie duplicateTable()/duplicateChart().
    async function duplicateDashboard(id) {
      try {
        const res = await fetch(`dashboards/${id}/duplicate`, {method: 'POST'});
        if (res.ok) {
          window.location.reload();
        } else {
          appAlert('Duplizieren fehlgeschlagen.');
        }
      } catch (e) {
        appAlert('Duplizieren fehlgeschlagen.');
      }
    }

    // Verschiebt is_default auf dieses Dashboard — Reload, damit sowohl die
    // "Standard"-Markierung/Sortierung hier als auch das Topnav-Dropdown
    // (nav_dashboards_list nutzt dieselbe list_dashboards()) konsistent sind.
    async function setDefaultDashboard(id) {
      try {
        const res = await fetch(`dashboards/${id}/set-default`, {method: 'POST'});
        if (res.ok) {
          window.location.reload();
        } else {
          appAlert('Konnte nicht als Standard festgelegt werden.');
        }
      } catch (e) {
        appAlert('Konnte nicht als Standard festgelegt werden.');
      }
    }
