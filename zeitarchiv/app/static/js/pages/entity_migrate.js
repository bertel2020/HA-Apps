function goToStep(n) {
  document.querySelectorAll('#migrate-stepper .migrate-step').forEach((el) => {
    const step = parseInt(el.dataset.step, 10);
    el.classList.toggle('active', step === n);
    el.classList.toggle('done', step < n);
  });
  document.getElementById('migrate-step-1').hidden = n !== 1;
  // Schritt 3 hat keine eigene Panel — appConfirm() (migrateExecute()) legt
  // sich als Dialog über Schritt 2, der wie im Mockup sichtbar (nur vom
  // halbtransparenten <dialog>-Backdrop gedimmt) darunter stehen bleibt.
  // n !== 2 hätte Schritt 2 bei n===3 mitversteckt — dann bliebe hinter dem
  // Dialog nur die leere Seite, der Backdrop wirkte wie eine deckende Fläche
  // statt einer Überlagerung.
  document.getElementById('migrate-step-2').hidden = n === 1;
}

// Der Umrechnungsfaktor lebt erst in Schritt 2 (siehe migrate-factor-input in
// _entity_migrate_preview.html, nur bei plan.unit_mismatch gerendert) — ein
// migrate-factor-Feld existiert deshalb nicht immer. Überall dort lesen statt
// direkt document.getElementById('migrate-factor').value, sonst wirft ein
// Klick ohne Einheiten-Konflikt (kein Feld im DOM) einen Fehler.
function _currentFactor() {
  const el = document.getElementById('migrate-factor');
  return el ? (parseFloat(el.value) || 1) : 1;
}

async function _loadPreview(target, factor, overlapResolution) {
  const response = await fetch(`${BASE}/entities/${ENTITY_ID}/migrate/preview`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({target_entity_id: target, factor, overlap_resolution: overlapResolution}),
  });
  document.getElementById('migrate-preview').innerHTML = await response.text();
}

async function migratePreview() {
  const target = document.getElementById('migrate-target-input').value;
  if (!target) {
    appAlert(t('Bitte zuerst eine Ziel-Entität wählen.'));
    return;
  }
  const button = document.getElementById('migrate-preview-btn');
  button.disabled = true;
  try {
    // Erste Vorschau: noch kein Faktor-Feld gesehen, Standard 1 — ein
    // Einheiten-Unterschied zeigt sich erst hier und bringt das Feld dann
    // mit (migrateRecalculate() unten liest/setzt ihn danach).
    await _loadPreview(target, 1, 'target');
    goToStep(2);
  } catch (error) {
    appAlert(t('Die Vorschau konnte nicht geladen werden.'));
  } finally {
    button.disabled = false;
  }
}

async function migrateRecalculate() {
  const target = document.getElementById('migrate-target-input').value;
  const overlapInput = document.querySelector('input[name="overlap_resolution"]:checked');
  const overlapResolution = overlapInput ? overlapInput.value : 'target';
  const button = document.getElementById('migrate-recalculate-btn');
  if (button) button.disabled = true;
  try {
    await _loadPreview(target, _currentFactor(), overlapResolution);
  } catch (error) {
    appAlert(t('Die Vorschau konnte nicht neu berechnet werden.'));
  } finally {
    // Bei Erfolg hat _loadPreview() den Knopf längst durch ein frisches
    // Fragment ersetzt — dieses disabled=false trifft dann ins Leere,
    // schadet aber nicht. Bei einem Fehler bleibt der alte Knopf im DOM und
    // muss wieder bedienbar werden.
    if (button) button.disabled = false;
  }
}

function _migrateConfirmMessage(postAction, overlapResolution, targetEntityId, rowsToTransfer) {
  const base = t('{rows} Datenpunkte werden von {source} nach {target} übertragen.', {rows: rowsToTransfer, source: ENTITY_ID, target: targetEntityId}) + ' ';
  const overlapNote = overlapResolution === 'source'
    ? t('Bei überschneidenden Zeitstempeln gewinnt dabei die Quelle.') + ' '
    : '';
  if (postAction === 'delete') {
    return base + overlapNote + t('Anschließend wird {source} vollständig aus Zeitarchiv entfernt. Dies kann nicht rückgängig gemacht werden.', {source: ENTITY_ID});
  }
  if (postAction === 'clear') {
    return base + overlapNote + t('Anschließend werden die Datenpunkte von {source} gelöscht (Konfiguration bleibt erhalten). Dies kann nicht rückgängig gemacht werden.', {source: ENTITY_ID});
  }
  return base + overlapNote + t('{source} bleibt dabei unverändert erhalten.', {source: ENTITY_ID});
}

async function migrateExecute() {
  const card = document.getElementById('migrate-preview-card');
  const target = card.dataset.targetEntityId;
  const rowsToTransfer = card.dataset.rowsToTransfer;
  const postAction = document.querySelector('input[name="post_action"]:checked').value;
  const overlapInput = document.querySelector('input[name="overlap_resolution"]:checked');
  const overlapResolution = overlapInput ? overlapInput.value : 'target';
  const factor = _currentFactor();

  // Stepper zeigt "3 Bestätigung" bereits, während appConfirm() noch offen
  // ist (wie im Mockup: Schritt 2 bleibt sichtbar, aber gedimmt, darüber der
  // Bestätigungsdialog) — appConfirm() selbst übernimmt das Dimmen/den
  // Dialog, hier nur der Stepper-Fortschritt.
  goToStep(3);
  const confirmed = await appConfirm(
    _migrateConfirmMessage(postAction, overlapResolution, target, rowsToTransfer),
    {danger: postAction !== 'keep' || overlapResolution === 'source', confirmLabel: t('Übertragung starten')}
  );
  if (!confirmed) {
    goToStep(2);
    return;
  }

  const button = document.getElementById('migrate-execute-btn');
  button.disabled = true;
  try {
    const response = await fetch(`${BASE}/entities/${ENTITY_ID}/migrate`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        target_entity_id: target, factor, post_action: postAction, overlap_resolution: overlapResolution,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || 'migrate failed');
    }
    showResult(await response.json());
  } catch (error) {
    button.disabled = false;
    goToStep(2);
    appAlert(t('Die Migration konnte nicht ausgeführt werden.'));
  }
}

function _escapeHtml(value) {
  const map = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
  return String(value).replace(/[&<>"']/g, (c) => map[c]);
}

const _POST_ACTION_RESULT_LABELS = {delete: t('Quelle entfernt'), clear: t('Quelle geleert'), keep: t('Quelle erhalten')};

function showResult(result) {
  document.getElementById('migrate-stepper').hidden = true;
  document.getElementById('migrate-step-1').hidden = true;
  document.getElementById('migrate-step-2').hidden = true;

  // post_action/overlap_resolution kommen validiert vom Server zurück (siehe
  // POST_ACTIONS/OVERLAP_RESOLUTIONS in entity_migration.py) — nur die
  // Dashboard-Namen unten sind echte Nutzereingaben und werden deshalb
  // separat escaped, statt hier pauschal alles zu escapen.
  const overlapLabel = result.overlap_resolution === 'source' ? t('Quelle übernommen') : t('Ziel behalten');
  document.getElementById('migrate-result-stats').innerHTML = `
    <div class="stat"><div class="label">${t('Übertragen')}</div><div class="value">${t('{count} Datenpunkte', {count: result.rows_transferred})}</div></div>
    <div class="stat"><div class="label">${t('Überschneidend')}</div><div class="value">${result.duplicate_rows} (${overlapLabel})</div></div>
    <div class="stat"><div class="label">${t('Aktion danach')}</div><div class="value">${_POST_ACTION_RESULT_LABELS[result.post_action] || result.post_action}</div></div>
  `;

  document.getElementById('migrate-result-summary').textContent =
    t('{target} hat jetzt den vollständigen Verlauf.', {target: result.target_entity_id});

  const notes = [];
  if (result.repointed_dashboards.length) {
    notes.push(`<div class="migrate-banner positive">✓ ${t('{count} Dashboard-Kachel(n) automatisch auf die neue Entität umgehängt — Titel, Rundung und Sparkline-Einstellungen bleiben dabei erhalten.', {count: result.repointed_dashboards.length})}</div>`);
    notes.push('<div class="migrate-result-list">' + result.repointed_dashboards.map((name) =>
      `<div class="migrate-result-row"><span>${_escapeHtml(name)}</span><span class="tag">→ ${_escapeHtml(result.target_entity_id)}</span></div>`
    ).join('') + '</div>');
  }
  document.getElementById('migrate-result-dashboards').innerHTML = notes.join('');

  document.getElementById('migrate-result-done-btn').href = `${BASE}/entities/${result.target_entity_id}/config`;
  document.getElementById('migrate-result').hidden = false;
}
