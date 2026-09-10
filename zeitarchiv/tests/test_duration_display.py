"""Dauer-Anzeige (Schalter, Optionen-Menü "Anzeigemodus: Zeit") auf Kacheln.

chart_editor.js gruppierte Dauer-Serien schon immer auf eine eigene
Achse (axisKey()) und formatierte Achse/Tooltip/Werte-Label mit
NumberFormat.fmtDuration. dashboard-tiles.js kannte den Anzeigemodus bislang
NUR in der selbstgebauten HTML-Legende — die eigentliche ECharts-Achse und
der native Tooltip/Werte-Label gruppierten nur nach s.unit und formatierten
nur mit fmtCompactNumber, eine Dauer-Serie konnte sich also fälschlich eine
Achse mit unitlosen Nicht-Dauer-Serien teilen und zeigte dort rohe Sekunden
statt "1h 30m".
"""

from _paths import APP

TILES_JS = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")


def test_the_tile_groups_duration_series_onto_their_own_axis() -> None:
    """Dieselbe Regel wie chart_editor.js: aggregation_type=switch UND
    display_mode=time bekommen einen synthetischen Achsen-Schlüssel statt
    ihrer echten (meist leeren) unit."""
    assert "isDurationSeries = s => s.aggregation_type === 'switch' && s.display_mode === 'time'" in TILES_JS
    assert "axisKey = s => isDurationSeries(s) ? ' duration' : s.unit" in TILES_JS
    assert "units.indexOf(axisKey(s))" in TILES_JS


def test_the_axis_label_formats_duration_series_as_a_duration() -> None:
    assert "isDuration ? NumberFormat.fmtDuration(v) : fmtCompactNumber(v, decimals)" in TILES_JS


def test_the_tooltip_formats_duration_series_as_a_duration() -> None:
    assert "? NumberFormat.fmtDuration(p.data[1])" in TILES_JS


def test_the_value_label_formats_duration_series_as_a_duration() -> None:
    assert "? NumberFormat.fmtDuration(params.value[1])" in TILES_JS


def test_the_duration_flag_survives_the_hold_point() -> None:
    """Der Linien-Zweig hängt bis window_end einen Halte-Punkt an, der den
    letzten Wert wiederholt (last[1] usw.) — das Dauer-Flag (Index 4) muss
    dabei mitkopiert werden, sonst verliert genau der letzte, oft sichtbarste
    Punkt seine Dauer-Formatierung."""
    assert "lineData.push([data.window_end * 1000, last[1], last[2], last[3], last[4]]);" in TILES_JS


def test_both_pages_use_the_same_duration_predicate() -> None:
    """Beide Dateien müssen dieselbe Definition von "Dauer-Serie" verwenden —
    sonst könnten Kachel und Editor bei einer künftigen Änderung wieder
    auseinanderlaufen."""
    predicate = "s.aggregation_type === 'switch' && s.display_mode === 'time'"
    assert TILES_JS.count(predicate) >= 1
    assert EDITOR_JS.count(predicate) >= 1
