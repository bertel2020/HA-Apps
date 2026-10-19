"""Option „Punkte" (Optionen-Menü, „Darstellung") wird mit dem Chart gespeichert.

Der Schalter lebte bisher nur im Editor-Zustand (showPoints: false) und fiel beim
Speichern heraus — nach dem Wiederöffnen war er aus, und die Dashboard-Kachel
zeichnete nie Punkte. Jetzt ein gespeichertes Chart-Feld, Default AUS.
"""

from _paths import APP, template_text

EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
TILES_JS = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
TILES_TEMPLATE = template_text("_dashboard_tiles.html")


def test_the_saved_chart_keeps_the_option(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.showpoints", "sensor", "measurement", "°C")
    body = {"name": "Punkte-Probe", "entity_ids": ["sensor.showpoints"], "range_key": "day"}
    chart_id = client.post("/charts", json=body).json()["id"]
    assert index.get_saved_chart(chart_id)["show_points"] is False
    assert "const SHOW_POINTS = false;" in client.get(f"/charts/{chart_id}").text

    client.post(f"/charts/{chart_id}", json={**body, "show_points": True})
    assert index.get_saved_chart(chart_id)["show_points"] is True
    assert "const SHOW_POINTS = true;" in client.get(f"/charts/{chart_id}").text

    kopie = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
    assert index.get_saved_chart(kopie)["show_points"] is True


def test_the_dashboard_tile_gets_the_option_from_its_chart(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.showpoints_tile", "sensor", "measurement", "°C")
    body = {"name": "Punkte-Kachel", "entity_ids": ["sensor.showpoints_tile"], "range_key": "day",
            "show_points": True}
    chart_id = client.post("/charts", json=body).json()["id"]
    html = client.post(f"/charts/{chart_id}/pin?dashboard_id=1").text
    start = html.index(f'data-item-id="{chart_id}"')
    assert 'data-show-points="true"' in html[start:html.index("</a>", start)]


def test_editor_and_tile_use_the_option() -> None:
    assert "showPoints: SHOW_POINTS" in EDITOR_JS
    assert "show_points: this.showPoints" in EDITOR_JS
    assert "el.dataset.showPoints === 'true'" in TILES_JS
    assert "if (!showPoints) cfg.symbol = 'none'" in TILES_JS
    assert 'data-show-points="{{' in TILES_TEMPLATE
