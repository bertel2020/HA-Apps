"""Chartoptionen einer Entität: ein abgelehntes Speichern wird gemeldet.

Die Optionen werden bei jeder Änderung im Menü sofort gesendet. Vorher blieb
ein 4xx/5xx stumm (fetch() wirft dabei nicht) — die Einstellung sah
gespeichert aus und war beim nächsten Besuch weg.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from _paths import APP_JS, template_text


SCRIPT = APP_JS / "pages" / "entity_detail.js"

_RUNNER = r"""
const [method, outcome] = process.argv.slice(1);
const vm = require('vm');
const alerts = [];
let reloaded = false;
const ctx = {
  BASE: '', ENTITY_ID: 'sensor.a',
  t: (s) => s,
  appAlert: async (m) => { alerts.push(m); return true; },
  location: {reload: () => { reloaded = true; }},
  fetch: async () => {
    if (outcome === 'network') throw new TypeError('Failed to fetch');
    return {ok: outcome === 'ok', status: outcome === 'ok' ? 200 : 500};
  },
};
vm.createContext(ctx);
vm.runInContext('const component = {' + method + '};', ctx);
vm.runInContext('component.' + method.match(/async (\w+)/)[1] + '()', ctx).then(() => {
  console.log(JSON.stringify({alerts, reloaded}));
});
"""


def _run(name: str, outcome: str) -> dict:
    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^( *)async {name}\(\) \{{.*?^\1\}}", source, re.M | re.S)
    assert match, name
    result = subprocess.run(
        ["node", "-e", _RUNNER, match.group(0).strip(), outcome],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("outcome", ["http_error", "network"])
def test_failed_save_is_reported(outcome) -> None:
    assert _run("saveChartOptions", outcome)["alerts"] == [
        "Chart-Optionen konnten nicht gespeichert werden"
    ]


def test_successful_save_stays_quiet() -> None:
    assert _run("saveChartOptions", "ok")["alerts"] == []


@pytest.mark.parametrize("outcome", ["http_error", "network"])
def test_failed_reset_is_reported_before_the_reload(outcome) -> None:
    assert _run("resetChartOptions", outcome) == {
        "alerts": ["Chart-Optionen konnten nicht zurückgesetzt werden"],
        "reloaded": True,
    }


def test_successful_reset_just_reloads() -> None:
    assert _run("resetChartOptions", "ok") == {"alerts": [], "reloaded": True}


def test_entity_page_loads_the_dialog_that_shows_the_message() -> None:
    page = template_text("entity_detail.html")
    assert page.index("js/confirm-dialog.js") < page.index("js/pages/entity_detail.js")
