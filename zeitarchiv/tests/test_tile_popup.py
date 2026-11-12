"""Verlaufs-Popup der Dashboard-Kacheln (Roadmap 1.55).

Das Popup ist die bestehende Seite der Kachel in einem iframe (?popup=1): ohne Topnav, Rücklinks und
Reiter, nur lesend. Die Seiten selbst bleiben ein Baustein — Chart-Seite und Popup werden nicht doppelt
gepflegt.
"""

import itertools

from _paths import APP, template_text

_zaehler = itertools.count(1)


def _chart(client) -> int:
    from app.main import index

    n = next(_zaehler)
    index.get_or_create_entity("sensor.tile_popup", "sensor", "measurement", "°C")
    return client.post(
        "/charts", json={"name": f"Popup {n}", "entity_ids": ["sensor.tile_popup"], "range_key": "day"}
    ).json()["id"]


def test_popup_mode_drops_topnav_and_marks_the_page(client) -> None:
    chart_id = _chart(client)
    normal = client.get(f"/charts/{chart_id}").text
    popup = client.get(f"/charts/{chart_id}?popup=1").text
    assert "data-popup" not in normal
    assert "page-sticky-header" in normal
    assert " data-popup" in popup.split("<body", 1)[0]
    assert "page-sticky-header" not in popup
    assert "zaPopupClose" in popup
    assert "vendor/alpine.min.js" in popup  # sonst kommt Alpine nur über die fehlende Topnav


def test_chart_page_shows_period_bar_when_embedded_and_hides_the_edit_button() -> None:
    html = template_text("chart_editor.html")
    assert '<div class="toolbar" x-show="editing || popup">' in html
    assert 'class="btn popup-hide" x-show="!editing" @click="editing = true"' in html


def test_entity_page_hides_header_facts_and_tabs_in_popup() -> None:
    html = template_text("entity_detail.html")
    assert html.count("popup-hide") >= 3


def test_both_dashboard_pages_include_the_dialog_and_load_the_script() -> None:
    dialog = template_text("_tile_popup.html")
    assert '<dialog id="tile-popup"' in dialog and "tile-popup-edit" in dialog
    for name in ("dashboard_detail.html", "entities.html"):  # Übersicht und eigene Dashboards
        html = template_text(name)
        assert '{% include "_tile_popup.html" %}' in html, name
        assert "js/tile-popup.js" in html, name


def test_only_value_chart_and_table_tiles_open_the_popup() -> None:
    js = (APP / "static" / "js" / "tile-popup.js").read_text()
    assert "a.dtile-body[href]" in js
    assert ".dtile-entity" in js and 'data-item-type="chart"' in js and 'data-item-type="table"' in js
    assert "cleanup" not in js
