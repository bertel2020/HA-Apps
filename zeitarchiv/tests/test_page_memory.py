"""Seitengedächtnis (page-memory.js) in node: woher man auf die jetzige Seite kam, wenn der Referrer fehlt.

Unter HA-Ingress fehlt document.referrer oft; dynamic-back-link.js nimmt dann window.zaPrevPage. Geprüft werden
Weitergabe der Herkunft, Neuladen/Filterwechsel auf demselben Pfad und die Zurück-Taste, bei der nichts erfunden
werden darf."""

from __future__ import annotations

import json
import subprocess

import pytest

from _paths import APP

SCRIPT = APP / "static/js/page-memory.js"

SHELL = r"""
const fs = require('fs');
const code = fs.readFileSync(process.argv[1], 'utf8');
const store = {};
const visit = (url, type, sessionOk = true) => {
  const u = new URL(url);
  const sandbox = {
    location: {pathname: u.pathname, href: u.href},
    performance: {getEntriesByType: () => [{type}]},
    sessionStorage: sessionOk
      ? {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }}
      : {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }},
    window: {},
  };
  new Function('location', 'performance', 'sessionStorage', 'window', code)(
    sandbox.location, sandbox.performance, sandbox.sessionStorage, sandbox.window);
  return sandbox.window.zaPrevPage;
};
const out = {};
out.first = visit('http://h/housekeeping?x=1', 'navigate');
out.toCleanup = visit('http://h/entities/a/cleanup?filter=gaps', 'navigate');
out.reload = visit('http://h/entities/a/cleanup?filter=gaps', 'reload');
out.otherQuery = visit('http://h/entities/a/cleanup?filter=outliers', 'navigate');
out.onward = visit('http://h/entities/a', 'navigate');
out.back = visit('http://h/entities/a/cleanup?filter=outliers', 'back_forward');
out.afterBack = visit('http://h/housekeeping', 'navigate');
out.noStorage = visit('http://h/charts', 'navigate', false);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result() -> dict:
    run = subprocess.run(["node", "-e", SHELL, str(SCRIPT)], capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def test_the_origin_is_the_previous_page_with_its_query(result) -> None:
    assert result["first"] is None
    assert result["toCleanup"] == "http://h/housekeeping?x=1"


def test_reload_and_a_query_change_on_the_same_path_keep_the_origin(result) -> None:
    assert result["reload"] == "http://h/housekeeping?x=1"
    assert result["otherQuery"] == "http://h/housekeeping?x=1"


def test_the_next_page_gets_the_page_before_it(result) -> None:
    assert result["onward"] == "http://h/entities/a/cleanup?filter=outliers"


def test_the_back_button_invents_no_origin(result) -> None:
    assert result["back"] is None


def test_without_storage_the_script_does_not_fail(result) -> None:
    assert result["noStorage"] is None
