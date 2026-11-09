"""Automatisches Aktualisieren der Tabellen-Seite: Einstellung ``page_refresh`` und Chip im Tabelleneditor."""

from __future__ import annotations

import time

from _paths import APP

TEMPLATES = APP / "templates"
OFF_ATTR = 'data-page-refresh="off"'


def test_base_template_sets_the_attribute_only_when_off() -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "{% if page_refresh == 'off' %} data-page-refresh=\"off\"{% endif %}" in base


def test_setting_is_validated_stored_and_rendered(client) -> None:
    from app.main import index as app_index

    saved = app_index.get_setting("page_refresh")
    try:
        assert client.post("/settings/darstellung", data={"page_refresh": "nope"}).status_code == 400
        assert client.post("/settings/darstellung", data={"page_refresh": "off"}).status_code == 200
        assert app_index.get_setting("page_refresh") == "off"
        assert OFF_ATTR in client.get("/uebersicht").text
        form = client.post("/settings/darstellung", data={"page_refresh": "on"})
        assert form.status_code == 200 and 'id="page-refresh-input"' in form.text
        assert OFF_ATTR not in client.get("/uebersicht").text
    finally:
        if saved is not None:
            app_index.set_setting("page_refresh", saved)
        else:
            app_index._conn.execute("DELETE FROM settings WHERE key = 'page_refresh'")
            app_index._conn.commit()


def test_default_is_on_without_attribute(client) -> None:
    from app.main import index as app_index

    app_index._conn.execute("DELETE FROM settings WHERE key = 'page_refresh'")
    app_index._conn.commit()
    assert OFF_ATTR not in client.get("/uebersicht").text


def test_setting_is_part_of_the_selective_restore() -> None:
    from app.storage.restore_selective import GENERAL_SETTINGS

    assert "page_refresh" in dict(GENERAL_SETTINGS)


def test_table_page_has_chip_menu_and_pause_logic() -> None:
    page = (TEMPLATES / "table_editor.html").read_text(encoding="utf-8")
    script = (APP / "static/js/pages/table_editor.js").read_text(encoding="utf-8")
    # Chip nur auf einer gespeicherten Tabelle, mit Menü und Schalter für diese Seite.
    assert "{% if table_id %}" in page and 'class="refresh-chip"' in page
    assert "refreshNow()" in page and "refreshOn = !refreshOn" in page
    assert "'tbl-flash': flashed[" in page
    # Takt wie die Dashboard-Kacheln, Pausen für Editor, vergangene Zeiträume und Ausschalter.
    assert "const REFRESH_INTERVAL_MS = 60000;" in script
    assert "if (!this.refreshOn) return 'off';" in script
    assert "if (this.editing) return 'edit';" in script
    assert "return 'past';" in script
    # Nur bei sichtbarem Tab, nicht während eines Ladens oder Speicherns.
    assert "document.visibilityState === 'visible'" in script
    assert "!this.loading && !this.saving" in script
    # Hintergrund-Laden zeigt keine "Lädt …"-Zeile und keinen Fehler.
    assert "async load({silent = false} = {})" in script
    assert "if (requestId === this._loadSeq && !silent)" in script


def test_chart_page_has_chip_menu_and_quiet_refresh() -> None:
    page = (TEMPLATES / "chart_editor.html").read_text(encoding="utf-8")
    script = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
    # Chip nur auf einem gespeicherten Chart, mit Menü und Schalter für diese Seite.
    assert "{% if chart_id %}" in page and 'class="refresh-chip"' in page
    assert "refreshNow()" in page and "refreshOn = !refreshOn" in page
    # Takt, Pausen und Bedingungen wie bei der Tabellen-Seite; ein vergangener Zeitraum pausiert.
    assert "const REFRESH_INTERVAL_MS = 60000;" in script
    assert "if (!this.refreshOn) return 'off';" in script
    assert "if (this.editing) return 'edit';" in script
    assert "if (!this.isCurrent) return 'past';" in script
    assert "document.visibilityState === 'visible'" in script
    assert "!this.loading && !this.saving" in script
    # Stilles Nachladen: keine "Lädt …"-Anzeige, kein Fehlerzustand, gezeichnet ohne Animation.
    assert "async load({silent = false} = {})" in script
    assert script.count("!silent") >= 3
    assert "{...option, animation: false}" in script
    assert "quietRender = silent;" in script
    # Alle Zeichenpfade legen den Chart über denselben Wrapper an, sonst liefe die Animation weiter.
    assert "echarts.init(" not in script.replace("const instance = echarts.init(", "")


def test_refresh_chip_styles_are_shared_between_table_and_chart_page() -> None:
    app_css = (APP / "static/css/app.css").read_text(encoding="utf-8")
    table_css = (APP / "static/css/pages/table_editor.css").read_text(encoding="utf-8")
    assert ".refresh-chip{" in app_css and ".refresh-menu{" in app_css
    assert ".refresh-chip" not in table_css


def test_entity_page_has_chip_menu_and_quiet_refresh() -> None:
    page = (TEMPLATES / "entity_detail.html").read_text(encoding="utf-8")
    script = (APP / "static/js/pages/entity_detail.js").read_text(encoding="utf-8")
    # Chip in der Titelzeile, mit Menü und Schalter für diese Seite.
    assert 'class="refresh-chip"' in page and "refreshNow()" in page and "refreshOn = !refreshOn" in page
    # Takt, Pausen (aus, Ausschnitt gewählt, abgeschlossener Zeitraum) und Bedingungen.
    assert "const REFRESH_INTERVAL_MS = 60000;" in script
    assert "if (!this.refreshOn) return 'off';" in script
    assert "if (this.zoomRange) return 'zoom';" in script
    assert "if (!this.isCurrent) return 'past';" in script
    assert "document.visibilityState === 'visible'" in script
    # Stilles Nachladen ohne "Lädt …", ohne Fehlerzustand und ohne Animation.
    assert "async load({silent = false} = {})" in script
    assert script.count("!silent") >= 2
    assert "{...option, animation: false}" in script
    assert "quietRender = silent;" in script
    assert "echarts.init(" not in script.replace("const instance = echarts.init(", "")


def test_entity_facts_follow_the_refresh(client) -> None:
    from app.main import index as app_index

    entity_id = "sensor.page_refresh_test"
    app_index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    app_index.record_write(entity_id, time.time() - 600, 21.5)
    page = client.get(f"/entities/{entity_id}")
    assert page.status_code == 200
    for key in ("last", "count", "extra"):
        assert f'data-fact="{key}"' in page.text
    data = client.get(f"/entities/{entity_id}/facts").json()
    assert set(data) == {"last_ts", "last_ts_time", "last_ago", "row_count", "size", "extra"}
    assert data["last_ts"] and data["row_count"]
    assert data["extra"] is None or {"text", "note", "pending"} <= set(data["extra"])
    assert client.get("/entities/gibt.es_nicht/facts").status_code == 404
    script = (APP / "static/js/pages/entity_detail.js").read_text(encoding="utf-8")
    assert "if (silent) this.refreshFacts();" in script
    assert "/facts`" in script
