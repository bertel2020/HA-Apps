"""Flächenfüllung unter Linien-Charts (Optionen-Menü, „Darstellung").

War auf der Dashboard-Kachel (dashboard-tiles.js) für jede Linien-Serie fest
an, auf der eigenen Chart-Seite (chart_editor.js) dagegen bislang komplett
ohne Entsprechung — dieselbe Inkonsistenz, die schon show_values vor seiner
Persistierung hatte. Jetzt ein gespeichertes Chart-Feld wie average_line/
show_values, Default AN (nicht 0 wie bei jenen): das entspricht dem bisherigen,
unveränderten Kachel-Verhalten.
"""

from _paths import APP

EDITOR_TEMPLATE = (APP / "templates/chart_editor.html").read_text(encoding="utf-8")
EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
TILES_JS = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
TILES_TEMPLATE = (APP / "templates/_dashboard_tiles.html").read_text(encoding="utf-8")


def test_the_option_row_exists_and_toggles_areafill() -> None:
    zeilen = [z for z in EDITOR_TEMPLATE.splitlines() if "Fläche</span>" in z]
    assert len(zeilen) == 1
    assert "areaFill = !areaFill" in EDITOR_TEMPLATE


def test_new_charts_default_to_area_fill_enabled(client) -> None:
    """Default AN — ein frisch erstelltes Chart (kein area_fill im Body) soll
    sich auf seiner eigenen Seite genauso zeigen wie bisher jede Dashboard-
    Kachel (dort war die Fläche schon immer fest an)."""
    from app.main import index

    index.get_or_create_entity("sensor.areafill_default", "sensor", "measurement", "°C")
    body = {"name": "Fläche-Default", "entity_ids": ["sensor.areafill_default"], "range_key": "day"}
    chart_id = client.post("/charts", json=body).json()["id"]
    assert index.get_saved_chart(chart_id)["area_fill"] is True
    assert "const AREA_FILL = true;" in client.get(f"/charts/{chart_id}").text


def test_the_saved_chart_keeps_the_option(client) -> None:
    """Speichern, Wiederöffnen und Duplizieren müssen die Fläche mitnehmen —
    sonst wäre sie eine Einstellung, die jeder Seitenaufruf vergisst."""
    from app.main import index

    index.get_or_create_entity("sensor.areafill", "sensor", "measurement", "°C")
    body = {"name": "Fläche-Probe", "entity_ids": ["sensor.areafill"], "range_key": "day",
            "area_fill": False}
    chart_id = client.post("/charts", json=body).json()["id"]
    assert index.get_saved_chart(chart_id)["area_fill"] is False
    assert "const AREA_FILL = false;" in client.get(f"/charts/{chart_id}").text

    kopie = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
    assert index.get_saved_chart(kopie)["area_fill"] is False

    client.post(f"/charts/{chart_id}", json={**body, "area_fill": True})
    assert index.get_saved_chart(chart_id)["area_fill"] is True


def _tile_markup(html: str, chart_id: int) -> str:
    """Grenzt die Kachel dieses einen Charts ein.

    dashboard_id=1 ist die geteilte "Übersicht" — über die ganze Testsitzung
    pinnen andere Tests dort eigene Charts an, die (anders als average_line/
    show_values) mit area_fill=True als Default ebenfalls "true" tragen. Eine
    Prüfung auf der ganzen Seite wäre also von der Testreihenfolge abhängig;
    nur die eigene Kachel (data-item-id bis zum schließenden </a> der
    dtile-body) ist es nicht.
    """
    start = html.index(f'data-item-id="{chart_id}"')
    return html[start:html.index("</a>", start)]


def test_the_dashboard_tile_gets_the_option_from_its_chart(client) -> None:
    """Derselbe Vertrag wie bei average_line: ein angeheftetes Chart soll nicht
    anders aussehen als dasselbe Chart auf seiner eigenen Seite."""
    from app.main import index

    index.get_or_create_entity("sensor.areafill_tile", "sensor", "measurement", "°C")
    body = {"name": "Fläche-Kachel", "entity_ids": ["sensor.areafill_tile"], "range_key": "day"}
    chart_id = client.post("/charts", json=body).json()["id"]
    html = client.post(f"/charts/{chart_id}/pin?dashboard_id=1").text
    assert 'data-area-fill="true"' in _tile_markup(html, chart_id)

    client.post(f"/charts/{chart_id}", json={**body, "area_fill": False})
    html = client.get("/dashboards/1").text
    tile = _tile_markup(html, chart_id)
    assert 'data-area-fill="true"' not in tile
    assert 'data-area-fill="false"' in tile


def test_chart_editor_gates_areastyle_on_the_option() -> None:
    assert "if (this.areaFill) main.areaStyle" in EDITOR_JS
    assert "if (this.areaFill) cmp.areaStyle" in EDITOR_JS


def test_dashboard_tile_gates_areastyle_on_the_option() -> None:
    assert "el.dataset.areaFill !== 'false'" in TILES_JS
    assert "if (areaFill) cfg.areaStyle" in TILES_JS
    assert 'data-area-fill="{{' in TILES_TEMPLATE
