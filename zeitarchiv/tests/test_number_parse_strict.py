"""NumberFormat.parseStrict (static/js/number-format.js), in node ausgeführt: die Eingabe von
„Rohwert hinzufügen“ darf nicht stillschweigend etwas anderes speichern als geschrieben wurde."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _paths import APP

MODULE = APP / "static/js/number-format.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


def _parse(texts: list[str], locale: str) -> list[float | None]:
    script = f"""
      global.window = {{ZA_FORMAT: {json.dumps(locale)}}};
      require({json.dumps(str(MODULE))});
      const out = {json.dumps(texts)}.map(t => {{ const v = window.NumberFormat.parseStrict(t); return Number.isNaN(v) ? null : v; }});
      console.log(JSON.stringify(out));
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(done.stdout)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("21,5", 21.5),
        ("21.5", 21.5),  # kein Tausenderpunkt: eine einzelne Ziffer danach
        ("-3,25", -3.25),
        ("+7", 7),
        ("0", 0),
        ("1.234", 1234),  # drei Ziffern nach dem Punkt: Tausendergruppe des Formats
        ("1.234,5", 1234.5),
        ("1.234.567", 1234567),
        ("  12  ", 12),
        ("12abc", None),
        ("abc", None),
        ("1,2,3", None),  # Dezimaltrenner mehrfach, keine Tausendergruppen
        ("1.23.456", None),  # falsch gruppiert
        ("1,234.5", 1234.5),  # englische Schreibweise, eindeutig
        ("1,234", 1.234),  # Komma ist hier der Dezimaltrenner des Formats
        ("21,", None),
        ("", None),
        ("1e5", None),
        ("Infinity", None),
    ],
)
def test_parse_strict_german(text: str, expected: float | None) -> None:
    assert _parse([text], "de-DE") == [expected]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("21.5", 21.5), ("1,234", 1234), ("1,234.5", 1234.5), ("21,5", 21.5), ("1.234.5", None), ("12abc", None)],
)
def test_parse_strict_english(text: str, expected: float | None) -> None:
    assert _parse([text], "en-GB") == [expected]
