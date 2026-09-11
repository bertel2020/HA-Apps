"""Durchschnittslinie im Chart (Optionen-Menü, „Darstellung").

Eine waagerechte `markLine` beim Durchschnitt der gezeichneten Werte — auf der
Entitäts-Chart-Seite und im Chart-Editor. Bewusst eine EIGENE Menüzeile und
nicht an „Statistik in Legende" gekoppelt: die Legende nennt die Zahl, die
Linie zeigt, wo sie im Bild liegt.
"""

from _paths import APP

DETAIL_TEMPLATE = (APP / "templates/entity_detail.html").read_text(encoding="utf-8")
EDITOR_TEMPLATE = (APP / "templates/chart_editor.html").read_text(encoding="utf-8")
DETAIL_JS = (APP / "static/js/pages/entity_detail.js").read_text(encoding="utf-8")
EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")


def _nutzt_echarts_average(js: str) -> bool:
    """ECharts' eingebauter Durchschnitt, in beiden üblichen Schreibweisen —
    ohne Leerzeichen wäre die Zusage sonst nur zufällig erfüllt."""
    return "type: 'average'" in js or "type:'average'" in js


def test_both_pages_offer_the_row_independently_of_the_legend() -> None:
    """Die Kennzahlen-Chips hängen an chartStats und verschwinden mit ihm. Die
    Durchschnittslinie darf das nicht: wer die Legende ausgeschaltet hat, käme
    sonst gar nicht mehr an die Linie heran.

    Der Chart-Editor blendet die Zeile seit der Donut-Darstellungsart
    zusätzlich per x-show="!donut" aus (chart_editor.html) — eine Linie über
    der Zeit ergibt ohne Zeitachse keinen Sinn. Das ist keine Kopplung an die
    Legende, deshalb prüft der Test gezielt auf chartStats/legend statt auf
    x-show generell."""
    for name, vorlage in (("entity_detail", DETAIL_TEMPLATE), ("chart_editor", EDITOR_TEMPLATE)):
        zeilen = [z for z in vorlage.splitlines() if "Durchschnittslinie" in z]
        assert len(zeilen) == 1, name
        block = vorlage.split("Durchschnittslinie")[0].rsplit("<label", 1)[1]
        assert "chartStats" not in block and "legend" not in block.lower(), (
            f"{name}: Zeile hängt an der Legende"
        )
        assert 'averageLine = !averageLine' in vorlage, name


def test_the_average_is_computed_here_not_by_echarts() -> None:
    """`markLine: {type: 'average'}` rechnet über die Daten, die die Serie
    gerade führt — bei dataZoom mit filterMode 'filter' also über den
    sichtbaren Ausschnitt. Die Linie änderte damit ihre Bedeutung beim Zoomen,
    abhängig von einer ganz anderen Option („Dynamische Y-Achse", die den
    filterMode bestimmt)."""
    for name, js in (("entity_detail", DETAIL_JS), ("chart_editor", EDITOR_JS)):
        assert "function averageOf(values)" in js, name
        assert not _nutzt_echarts_average(js), name
        # chart_editor.js kennt seit der Ranking-Vergleich/horizontale-Balken-
        # Erweiterung zusätzlich {xAxis: durchschnitt} (Achsen vertauscht,
        # wenn horizontal aktiv ist) — {yAxis: durchschnitt} bleibt in beiden
        # Dateien der vertikale/Normalfall.
        assert "{yAxis: durchschnitt}" in js, name


def test_each_page_averages_exactly_what_it_draws() -> None:
    """Die Entitätsseite zeichnet ihre Punkte unverändert — dort ist der
    Durchschnitt derselbe wie in der Legende. Der Editor zeichnet
    resamplePoints(), und die fassen Zähler/Schalter per SUMME zusammen: ein
    Durchschnitt über die Rohpunkte läge dort um den Faktor der Bucketbreite
    unter den gezeichneten Balken."""
    assert "averageOf(this.points.map(p => p.value))" in DETAIL_JS
    assert "averageOf(mainPoints.map(p => p.value))" in EDITOR_JS
    assert "averageOf(s.points" not in EDITOR_JS


def test_the_line_sits_on_the_main_series_only() -> None:
    """Sonst zeichnete der Vergleichsmodus eine zweite Linie für die
    Vorperiode — eine Aussage, die in der Legende nirgends steht."""
    assert "series[0].markLine" in DETAIL_JS
    assert "main.markLine" in EDITOR_JS
    assert "compareSeries.markLine" not in DETAIL_JS
    assert "cmp.markLine" not in EDITOR_JS


def test_the_saved_chart_keeps_the_option(client) -> None:
    """Speichern, Wiederöffnen und Duplizieren müssen die Linie mitnehmen —
    sonst wäre sie eine Einstellung, die jeder Seitenaufruf vergisst."""
    from app.main import index

    index.get_or_create_entity("sensor.avgline", "sensor", "measurement", "°C")
    body = {"name": "Ø-Probe", "entity_ids": ["sensor.avgline"], "range_key": "day",
            "average_line": True}
    chart_id = client.post("/charts", json=body).json()["id"]
    assert index.get_saved_chart(chart_id)["average_line"] is True
    assert "const AVERAGE_LINE = true;" in client.get(f"/charts/{chart_id}").text

    kopie = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
    assert index.get_saved_chart(kopie)["average_line"] is True

    client.post(f"/charts/{chart_id}", json={**body, "average_line": False})
    assert index.get_saved_chart(chart_id)["average_line"] is False


def test_every_entity_chart_option_survives_the_request_model(client) -> None:
    """Der eigentliche Fund bei dieser Änderung, und ein alter Fehler:
    Pydantic verwirft unbekannte Felder stillschweigend, und
    entity_set_chart_options() speichert genau das, was model_dump() liefert.
    Ein im Modell vergessenes Feld wird gesendet, mit HTTP 200 angenommen und
    NIE gespeichert — beim nächsten Laden steht wieder der globale Default da.

    Genau so ist show_marked durchgefallen, seit es eingeführt wurde. Diese
    Zusage prüft nicht ein Feld, sondern die Regel: jeder Schlüssel der
    Defaults muss im Modell stehen.
    """
    from app.main import _ENTITY_CHART_OPTION_DEFAULTS, _EntityChartOptionsBody

    fehlend = set(_ENTITY_CHART_OPTION_DEFAULTS) - set(_EntityChartOptionsBody.model_fields)
    assert not fehlend, f"nicht im Request-Modell und damit nicht speicherbar: {sorted(fehlend)}"


def test_the_option_is_actually_stored_per_entity(client) -> None:
    """Die Gegenprobe zur Regel oben, einmal durch den echten Weg."""
    from app.main import _resolve_entity_chart_options, index

    index.get_or_create_entity("sensor.avgstore", "sensor", "measurement", "°C")
    schnappschuss = {
        "continuous": False, "raw": False, "chart_type": "line", "show_points": False,
        "show_values": False, "dynamic_y_axis": False, "chart_stats": True,
        "show_marked": True, "average_line": True,
        "legend_metrics": ["min"], "legend_style": "chips", "decimals": "auto",
    }
    assert client.post("/entities/sensor.avgstore/chart-options", json=schnappschuss).status_code == 200
    optionen = _resolve_entity_chart_options(index.get_entity("sensor.avgstore"))
    assert optionen["average_line"] is True
    assert optionen["show_marked"] is True


def test_the_dashboard_tile_gets_the_option_from_its_chart(client) -> None:
    """Ein angeheftetes Chart soll nicht anders aussehen als dasselbe Chart auf
    seiner eigenen Seite. Die Kachel liest die Einstellung aus dem
    data-Attribut, das _dashboard_tiles.html aus dem gespeicherten Chart
    schreibt — fehlt sie im Kachel-Kontext, steht dort still „false" (Jinja
    liefert für einen fehlenden Schlüssel Undefined, und das ist falsy).
    """
    from app.main import index

    index.get_or_create_entity("sensor.avgtile", "sensor", "measurement", "°C")
    body = {"name": "Ø-Kachel", "entity_ids": ["sensor.avgtile"], "range_key": "day",
            "average_line": True}
    chart_id = client.post("/charts", json=body).json()["id"]
    html = client.post(f"/charts/{chart_id}/pin?dashboard_id=1").text
    assert 'data-average-line="true"' in html

    client.post(f"/charts/{chart_id}", json={**body, "average_line": False})
    html = client.get("/dashboards/1").text
    assert 'data-average-line="true"' not in html
    assert 'data-average-line="false"' in html


def test_the_tile_averages_the_drawn_points_without_the_hold_point() -> None:
    """Der Linien-Zweig hängt bis window_end einen Halte-Punkt an, der den
    letzten Wert wiederholt. Über lineData zu mitteln zählte ihn doppelt —
    deshalb eine eigene Werteliste je Zweig."""
    tiles = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
    assert "const averageOf = values =>" in tiles
    assert not _nutzt_echarts_average(tiles)
    assert "el.dataset.averageLine === 'true'" in tiles
    assert "averageOf(prepared[i].averageValues)" in tiles
    assert "averageValues = displayPoints.map(p => p.value);" in tiles
    assert "averageValues: [aggregate]," in tiles
    assert "averageOf(lineData" not in tiles
