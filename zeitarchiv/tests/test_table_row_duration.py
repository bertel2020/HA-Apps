"""Zeilenformat „Dauer (TT hh:mm)“ (display_format): reine Anzeige von Stunden."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pydantic import ValidationError

from app.main import _TableRowBody

from _paths import APP

COMPUTE = APP / "static/js/table-compute.js"

SCRIPT = r"""
const fs = require('fs');
global.window = {NumberFormat: {fmt: (v, d) => String(v), DECIMAL_SEP: ','}};
global.t = s => s;
global.document = {documentElement: {lang: 'de'}};
require('vm').runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const TC = window.TableCompute;
const dur = {display_format: 'duration'};
const text = (v, unit = 'h', row = dur) => {
  const c = TC.durationCell({value: v, unit}, row);
  return [TC.cellNumberParts(c, 'auto'), TC.cellUnit(c)];
};
console.log(JSON.stringify({
  texts: [0, 0.177, 51.067, 2.5, 480].map(v => TC.durationText(v)),
  cell: text(51.067),
  kwh: text(5, 'kWh'),
  auto: text(5, 'h', {display_format: 'auto'}),
  negative: text(-1),
  missing: TC.durationCell({value: null, unit: 'h'}, dur),
  comparison: TC.comparisonValueText({comparisonValue: 11.35, unit: 'h'}, 'auto', dur),
  comparisonAuto: TC.comparisonValueText({comparisonValue: 11.35, unit: 'h'}, 'auto', {}),
}));
"""


def test_display_formats_are_validated() -> None:
    base = {"label": "x", "row_type": "entity"}
    assert _TableRowBody(**base).display_format == "auto"
    assert _TableRowBody(**base, display_format="duration").display_format == "duration"
    with pytest.raises(ValidationError):
        _TableRowBody(**base, display_format="bogus")


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_duration_display() -> None:
    out = subprocess.run(["node", "-e", SCRIPT, str(COMPUTE)], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    # Beispiele aus dem Issue; volle Minuten werden abgeschnitten, nicht gerundet.
    assert result["texts"] == ["00 00:00", "00 00:10", "02 03:04", "00 02:30", "20 00:00"]
    # Dauer ersetzt Zahl und Einheit.
    assert result["cell"] == [{"whole": "02 03:04", "separator": "", "fraction": ""}, ""]
    # Nur Einheit h, nicht negativ, nur wenn gewählt.
    assert result["kwh"][1] == "kWh"
    assert result["auto"][1] == "h"
    assert result["negative"][1] == "h"
    assert result["missing"] == {"value": None, "unit": "h"}
    # Tooltip-Vergleichswert folgt dem Zeilenformat.
    assert result["comparison"] == "00 11:21"
    assert result["comparisonAuto"] == "11.35 h"
