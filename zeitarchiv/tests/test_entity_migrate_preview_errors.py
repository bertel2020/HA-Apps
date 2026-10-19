"""Migrationsassistent: eine Fehlerantwort ist keine Vorschau.

fetch() wirft bei 4xx/5xx nicht. Vorher landete die Fehlerantwort im
Vorschaufenster und der Assistent sprang zu Schritt 2, als wäre alles gut.
"""

from __future__ import annotations

import json
import subprocess

from _paths import APP_JS


SCRIPT = APP_JS / "pages" / "entity_migrate.js"

_RUNNER = r"""
const [script, status, body] = process.argv.slice(1);
const vm = require('vm');
const elements = {};
const el = (id) => (elements[id] ||= {id, hidden: false, innerHTML: '', value: '', disabled: false});
el('migrate-target-input').value = 'sensor.ziel';
el('migrate-step-1').hidden = false;
el('migrate-step-2').hidden = true;
const alerts = [];
const ctx = {
  BASE: '', ENTITY_ID: 'sensor.quelle',
  t: (s, v) => s,
  appAlert: (m) => alerts.push(m),
  document: {
    getElementById: el,
    querySelector: () => null,
    querySelectorAll: () => [],
  },
  fetch: async () => ({
    ok: Number(status) < 400,
    status: Number(status),
    text: async () => body,
    json: async () => JSON.parse(body),
  }),
};
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(script, 'utf8'), ctx);
vm.runInContext('migratePreview()', ctx).then(() => {
  console.log(JSON.stringify({
    alerts,
    preview: el('migrate-preview').innerHTML,
    step1Hidden: el('migrate-step-1').hidden,
    step2Hidden: el('migrate-step-2').hidden,
  }));
});
"""


def _run(status: int, body: str) -> dict:
    result = subprocess.run(
        ["node", "-e", _RUNNER, str(SCRIPT), str(status), body],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_error_response_stays_in_step_one_and_names_the_reason() -> None:
    result = _run(404, json.dumps({"detail": "Entität nicht gefunden"}))
    assert result["preview"] == ""
    assert result["step1Hidden"] is False and result["step2Hidden"] is True
    assert result["alerts"] == [
        "Die Vorschau konnte nicht geladen werden. Entität nicht gefunden"
    ]


def test_non_json_error_falls_back_to_the_general_message() -> None:
    result = _run(502, "<html>Bad Gateway</html>")
    assert result["preview"] == ""
    assert result["alerts"] == ["Die Vorschau konnte nicht geladen werden."]


def test_success_shows_the_preview_and_moves_to_step_two() -> None:
    result = _run(200, "<div>Vorschau</div>")
    assert result["preview"] == "<div>Vorschau</div>"
    assert result["alerts"] == []
    assert result["step1Hidden"] is True and result["step2Hidden"] is False
