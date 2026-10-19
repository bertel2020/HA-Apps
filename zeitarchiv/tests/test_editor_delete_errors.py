"""Löschen im Chart- und Tabellen-Editor: ein HTTP-Fehler ist kein Erfolg.

fetch() wirft bei 4xx/5xx nicht. Vorher führte ein fehlgeschlagenes Löschen
kommentarlos zur Liste, in der das Objekt weiter stand.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from _paths import APP_JS


_RUNNER = r"""
const [method, outcome] = process.argv.slice(1);
const vm = require('vm');
const alerts = [];
const ctx = {
  BASE: '/base', TABLE_ID: 7, CHART_ID: 7,
  t: (s) => s,
  appConfirm: async () => true,
  appAlert: (m) => alerts.push(m),
  window: {location: {href: '/base/editor'}},
  fetch: async () => {
    if (outcome === 'network') throw new TypeError('Failed to fetch');
    return {ok: outcome === 'ok', status: outcome === 'ok' ? 200 : 500};
  },
};
vm.createContext(ctx);
vm.runInContext('const editor = {' + method + '};', ctx);
vm.runInContext('editor.' + method.match(/async (\w+)/)[1] + '()', ctx).then(() => {
  console.log(JSON.stringify({alerts, href: ctx.window.location.href}));
});
"""


def _method(file: str, name: str) -> str:
    source = (APP_JS / "pages" / file).read_text(encoding="utf-8")
    match = re.search(rf"^( *)async {name}\(\) \{{.*?^\1\}}", source, re.M | re.S)
    assert match, name
    return match.group(0).strip()


def _run(file: str, name: str, outcome: str) -> dict:
    result = subprocess.run(
        ["node", "-e", _RUNNER, _method(file, name), outcome],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


CASES = [("table_editor.js", "deleteTable", "/base/tables"), ("chart_editor.js", "deleteChart", "/base/charts")]


@pytest.mark.parametrize(("file", "name", "_list"), CASES)
@pytest.mark.parametrize("outcome", ["http_error", "network"])
def test_failed_delete_stays_on_the_page_and_says_so(file, name, _list, outcome) -> None:
    result = _run(file, name, outcome)
    assert result == {"alerts": ["Löschen fehlgeschlagen."], "href": "/base/editor"}


@pytest.mark.parametrize(("file", "name", "list_url"), CASES)
def test_successful_delete_returns_to_the_list(file, name, list_url) -> None:
    assert _run(file, name, "ok") == {"alerts": [], "href": list_url}
