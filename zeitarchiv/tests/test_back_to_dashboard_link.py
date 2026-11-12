"""Rücklink zum Dashboard auf Chart-, Tabellen- und Entitätsseite.

Die Kacheln hängen ?from_dashboard=<id> an; die Zielseite trägt den Link fest ein, statt sich auf
document.referrer zu verlassen (dynamic-back-link.js). Unter HA-Ingress fehlt der Referrer oft, dann
gab es keinen Weg zurück zum Dashboard (Forum, 2026-11-12: „Übersicht fehlt“ auf der Chart-Seite).
"""

import itertools
import re

from _paths import APP, template_text, tile_js

COLUMNS = [{"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False}]


def _links(html: str) -> list[tuple[str, str]]:
    """(href, Text) aller Links im „← zurück“-Absatz der Seite."""
    absatz = re.search(r'<p class="sub" data-dynamic-back.*?</p>', html, re.S)
    assert absatz, "Rücklink-Absatz fehlt"
    return re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', absatz.group(0))


_zaehler = itertools.count(1)


def _setup(client):
    from app.main import index

    n = next(_zaehler)  # die Datenbank lebt über die Tests des Moduls hinweg: Namen müssen eindeutig sein
    index.get_or_create_entity("sensor.back_link", "sensor", "measurement", "°C")
    chart_id = client.post(
        "/charts", json={"name": f"Rücklink {n}", "entity_ids": ["sensor.back_link"], "range_key": "day"}
    ).json()["id"]
    table_id = index.create_saved_table(f"Rücklink-Tabelle {n}", COLUMNS, [])
    extra = index.create_dashboard(f"Wohnzimmer-Test {n}")
    return index, chart_id, table_id, index.get_default_dashboard_id(), extra


def test_pages_link_back_to_the_originating_dashboard(client) -> None:
    index, chart_id, table_id, default_id, extra = _setup(client)
    for pfad in (f"/charts/{chart_id}", f"/tables/{table_id}", "/entities/sensor.back_link"):
        eigenes = _links(client.get(f"{pfad}?from_dashboard={extra}").text)
        assert (f"/dashboards/{extra}", f"← zurück zum Dashboard „{index.get_dashboard(extra)['name']}“") in eigenes, pfad
        # Standard-Dashboard = Übersichtsseite
        uebersicht = _links(client.get(f"{pfad}?from_dashboard={default_id}").text)
        assert ("/uebersicht", "← zurück zur Übersicht") in uebersicht, pfad


def test_without_or_with_invalid_origin_there_is_no_extra_link(client) -> None:
    _index, chart_id, _table_id, _default_id, _extra = _setup(client)
    for wert in ("", "?from_dashboard=99999", "?from_dashboard=abc", '?from_dashboard="><script>'):
        seite = client.get(f"/charts/{chart_id}{wert}")
        assert seite.status_code == 200
        assert len(_links(seite.text)) == 1, wert  # nur „zurück zu Charts“


def test_the_dashboard_name_is_escaped(client) -> None:
    from app.main import index

    _index, chart_id, *_ = _setup(client)
    boese = index.create_dashboard(f"<b>x</b>{next(_zaehler)}")
    html = client.get(f"/charts/{chart_id}?from_dashboard={boese}").text
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_tiles_pass_their_dashboard_on() -> None:
    tiles = template_text("_dashboard_tiles.html")
    for ziel in ("charts/{{ tile.id }}", "tables/{{ tile.id }}", "entities/{{ tile.entity_id }}"):
        assert f"{{{{ href_base }}}}{ziel}?from_dashboard={{{{ dashboard_id }}}}" in tiles, ziel


def test_the_referrer_link_does_not_duplicate_a_server_side_dashboard_link() -> None:
    js = (APP / "static/js/dynamic-back-link.js").read_text(encoding="utf-8")
    assert "[data-back-dashboard]" in js
    for name in ("chart_editor.html", "table_editor.html", "entity_detail.html"):
        assert "data-back-dashboard" in template_text(name), name
    assert tile_js()  # Kachel-Skript lädt unverändert


def test_usage_chips_open_the_default_dashboard_as_the_overview_page() -> None:
    """Chips „Verwendet in“ und Rücklink führen für das Standard-Dashboard auf dieselbe Seite wie die Topnav."""
    usage = template_text("_dashboard_usage.html")
    assert usage.count("'/uebersicht' if dashboard.is_default") == 2
