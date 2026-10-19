"""Der gemeinsame HTML-Maskierer (html-escape.js) taugt für Text und Attribute.

Die Tabellen-Kacheln setzen damit u. a. data-tooltip-fixed="…" aus frei
wählbaren Spaltenbeschriftungen zusammen. Früher hatte jede Datei ihren
eigenen Helfer; einer maskierte keine Anführungszeichen, einer kein &.
"""

from __future__ import annotations

import json
import re
import subprocess

from _paths import APP_JS, TEMPLATES


SCRIPT = APP_JS / "html-escape.js"

_RUNNER = r"""
const [script, value] = process.argv.slice(1);
const vm = require('vm');
const ctx = {window: {}};
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(script, 'utf8'), ctx);
console.log(JSON.stringify(ctx.window.escapeHtml(JSON.parse(value))));
"""


def _escape_html(value) -> str:
    result = subprocess.run(
        ["node", "-e", _RUNNER, str(SCRIPT), json.dumps(value)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_escape_html_covers_attribute_delimiters() -> None:
    assert _escape_html('a"b\'c<d>&e') == "a&quot;b&#39;c&lt;d&gt;&amp;e"


def test_escaped_label_cannot_leave_the_attribute() -> None:
    assert '"' not in _escape_html('x" onmouseover="alert(1)')


def test_null_becomes_empty_text() -> None:
    assert _escape_html(None) == ""


def test_no_script_keeps_its_own_escape_helper() -> None:
    own = re.compile(r"function\s+_?esc\w*\s*\(|\bescLegend\b|replace\(/\[&<>")
    for path in sorted(APP_JS.rglob("*.js")):
        if "vendor" in path.parts or path.name == "html-escape.js":
            continue
        assert not own.search(path.read_text(encoding="utf-8")), path.name


def test_every_page_loads_the_shared_helper_early() -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert base.index("js/i18n.js") < base.index("js/html-escape.js") < base.index("{% block content")
