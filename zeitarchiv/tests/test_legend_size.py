"""Legende: Größe (Normal/Klein) und „Aus“ (ROADMAP 1.52).

Die Größe ist je Chart leer (folgt der globalen Einstellung) oder „normal“/„small“; „off“ ist ein
dritter Wert des Legendenstils. Geprüft werden Speichern, Prüfung, Einstellung und die Stellen,
an denen die Vorlagen den Wert an das Stylesheet weiterreichen."""

from __future__ import annotations

from _paths import APP, template_text

CHART = {
    "name": "Legendentest", "entity_ids": ["sensor.legende_a"], "range_key": "month",
}


def _chart_body(**overrides) -> dict:
    return {**CHART, **overrides}


def _entity(index) -> None:
    index.get_or_create_entity("sensor.legende_a", "sensor", "measurement", "kWh")


def test_a_chart_keeps_its_legend_size_and_the_off_style(client) -> None:
    from app.main import index

    _entity(index)
    antwort = client.post("/charts", json=_chart_body(name="Legende klein und aus", legend_style="off", legend_size="small"))
    assert antwort.status_code == 200, antwort.text
    chart = index.get_saved_chart(antwort.json()["id"])
    assert chart["legend_style"] == "off" and chart["legend_size"] == "small"

    # Ohne Angabe bleibt die Größe leer: der Chart folgt dann der globalen Einstellung.
    standard = client.post("/charts", json=_chart_body(name="Legende ohne Größe"))
    assert index.get_saved_chart(standard.json()["id"])["legend_size"] == ""

    geaendert = client.post(f"/charts/{antwort.json()['id']}", json=_chart_body(name="Legende klein und aus", legend_size="normal"))
    assert geaendert.status_code == 200, geaendert.text
    assert index.get_saved_chart(antwort.json()["id"])["legend_size"] == "normal"


def test_a_chart_rejects_an_unknown_size_or_style(client) -> None:
    from app.main import index

    _entity(index)
    assert client.post("/charts", json=_chart_body(name="Legende kaputt", legend_size="huge")).status_code == 400
    assert client.post("/charts", json=_chart_body(name="Legende kaputt 2", legend_style="grid")).status_code == 400


def test_the_entity_chart_options_take_size_and_off(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.legende_opt", "sensor", "measurement", "kWh")
    body = {
        "continuous": False, "raw": False, "chart_type": "auto", "show_points": False,
        "show_values": False, "dynamic_y_axis": True, "chart_stats": True,
        "legend_metrics": ["sum"], "legend_style": "off", "legend_size": "small", "decimals": "auto",
    }
    assert client.post("/entities/sensor.legende_opt/chart-options", json=body).status_code == 200
    assert client.post("/entities/sensor.legende_opt/chart-options", json={**body, "legend_size": "huge"}).status_code == 400


def test_the_global_setting_reaches_every_page(client) -> None:
    antwort = client.post("/settings/darstellung", data={"legend_size": "small"})
    assert antwort.status_code == 200, antwort.text
    try:
        assert 'data-legend-size="small"' in client.get("/").text
    finally:
        client.post("/settings/darstellung", data={"legend_size": "normal"})
    assert 'data-legend-size="normal"' in client.get("/").text
    assert client.post("/settings/darstellung", data={"legend_size": "huge"}).status_code == 400


def test_the_stylesheet_scales_the_legend_with_one_variable() -> None:
    css = (APP / "static/css/app.css").read_text(encoding="utf-8")
    assert '[data-legend-size="small"]{--legend-scale:.85;}' in css
    assert '[data-legend-size="normal"]{--legend-scale:1;}' in css
    # Die Tabellen-Legende hat eigene Regeln, damit table.dt für alle anderen Tabellen unberührt bleibt.
    assert "table.dt.compact.chart-legend-table th" in css


def test_both_chart_pages_offer_the_off_style_and_the_size() -> None:
    for name in ("chart_editor.html", "entity_detail.html"):
        vorlage = template_text(name)
        assert "legendStyle === 'off'" in vorlage, name
        assert "legendSizeShown === 'small'" in vorlage, name
        assert ':data-legend-size="legendSize || null"' in vorlage, name


def test_the_editor_hides_every_legend_variant_for_off() -> None:
    """Die Chips-Varianten des Editors sind „chips ODER Statistik aus“ — ohne die Prüfung auf
    „off“ blieben sie dort stehen, wo der Stil nur „table“ lautet."""
    vorlage = template_text("chart_editor.html")
    assert vorlage.count("legendStyle !== 'off' && ") >= 3


def test_the_tile_hands_the_chart_size_to_the_stylesheet() -> None:
    kachel = template_text("_dashboard_tiles.html")
    assert 'data-legend-size="{{ tile.legend_size }}"' in kachel
    js = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
    assert "legendStyle !== 'off'" in js
