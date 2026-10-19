"""escapeHtml() in dashboard-tiles.js muss auch für Attributwerte taugen.

Die Tabellen-Kacheln setzen damit u. a. data-tooltip-fixed="…" aus frei
wählbaren Spaltenbeschriftungen zusammen. Die frühere Variante über
textContent/innerHTML maskierte keine Anführungszeichen.
"""

from __future__ import annotations

import json
import re
import subprocess

from _paths import APP_JS


SCRIPT = APP_JS / "dashboard-tiles.js"


def _escape_html(value: str) -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    table = re.search(r"^  const HTML_ESCAPES = .*?;$", source, re.M)
    func = re.search(r"^  function escapeHtml\(s\) \{.*?^  \}$", source, re.M | re.S)
    assert table and func, "escapeHtml() nicht gefunden"
    code = f"{table.group(0)}\n{func.group(0)}\nconsole.log(JSON.stringify(escapeHtml({json.dumps(value)})));"
    result = subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_escape_html_covers_attribute_delimiters() -> None:
    assert _escape_html('a"b\'c<d>&e') == "a&quot;b&#39;c&lt;d&gt;&amp;e"


def test_escaped_label_cannot_leave_the_attribute() -> None:
    label = 'x" onmouseover="alert(1)'
    assert '"' not in _escape_html(label)
