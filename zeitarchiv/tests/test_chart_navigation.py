"""Zeitraum-Navigation im Chart-Editor (‹ Zeitraum ›, Kalender, „Zurück zu
jetzt“) — nur beim Anpassen, dieselben Bausteine wie auf der Entitätsseite —
und der feste Zeitraum eines gespeicherten Charts (saved_charts.period_anchor)."""

from __future__ import annotations

from _paths import APP, TEMPLATES

EDITOR_JS = (APP / "static" / "js" / "pages" / "chart_editor.js").read_text(encoding="utf-8")
EDITOR_TEMPLATE = (TEMPLATES / "chart_editor.html").read_text(encoding="utf-8")
CALENDAR_JS = (APP / "static" / "js" / "calendar-picker.js").read_text(encoding="utf-8")


def test_navigation_only_while_customizing_and_plain_label_when_viewing() -> None:
    """Beim Ansehen steht wie bisher nur die Zeitraumanzeige; ‹ ›, Kalender und
    „Zurück zu jetzt“ gibt es nur im Modus „Anpassen“."""
    row = EDITOR_TEMPLATE.split('<div class="chart-title-row chart-title-row-end">', 1)[1].split("<template x-if", 1)[0]
    assert '<span class="period-label" x-show="!editing" x-text="periodLabel"></span>' in row
    assert '<span class="nav chart-nav" x-show="editing">' in row
    assert '@click="goBack()"' in row
    assert ':disabled="!canGoForward" @click="goForward()"' in row
    assert 'x-data=\'calendarPicker(null, {{ app_root | tojson }}, {{ calendar_first_date | tojson }}, {{ calendar_last_date | tojson }})\'' in row
    assert '{% include "_calendar_popover.html" %}' in row
    assert 'x-show="offset !== 0"' in row and '@click="goToNow()"' in row


def test_saving_keeps_the_shown_period_without_jumping_back() -> None:
    """Gespeichert wird die angezeigte Periode; nach dem Speichern bleibt die
    Ansicht dort, statt zur laufenden Periode zu springen."""
    save = EDITOR_JS.split("async save() {", 1)[1].split("\n        },", 1)[0]
    assert "period_anchor: this.periodAnchorForSave," in save
    assert "goToNow" not in save
    assert "save-offset-note" not in EDITOR_TEMPLATE


def test_navigation_scripts_are_loaded_before_the_editor() -> None:
    nav = EDITOR_TEMPLATE.index("js/period-navigation.js")
    cal = EDITOR_TEMPLATE.index("js/calendar-picker.js")
    editor = EDITOR_TEMPLATE.index("js/pages/chart_editor.js")
    assert nav < editor and cal < editor


def test_saved_period_is_a_date_not_an_offset() -> None:
    """Der Versatz geht in die Abfrage, gespeichert wird ein Tag in der
    angezeigten Periode — "ein Jahr zurück" würde sonst weiterwandern. In der
    laufenden Periode bleibt der Chart "immer aktuell" (null)."""
    assert "offset: String(this.offset)" in EDITOR_JS
    body = EDITOR_JS.split("async save() {", 1)[1].split("const body = {", 1)[1].split("};", 1)[0]
    assert "offset:" not in body
    getter = EDITOR_JS.split("get periodAnchorForSave() {", 1)[1].split("\n        },", 1)[0]
    assert "if (this.offset === 0" in getter and "return null;" in getter
    assert "msToAnchorDate(PeriodNavigation.anchorForWindow(this.windowStart, this.windowEnd, false))" in getter


def test_a_chart_with_fixed_period_starts_there() -> None:
    assert "const PERIOD_ANCHOR = {{ period_anchor | tojson }};" in EDITOR_TEMPLATE
    assert "offset: PERIOD_ANCHOR ? PeriodNavigation.offsetForRange(RANGE_KEY, anchorDateToMs(PERIOD_ANCHOR)) : 0," in EDITOR_JS
    assert "_rangeAnchorMs: PERIOD_ANCHOR ? anchorDateToMs(PERIOD_ANCHOR) : null," in EDITOR_JS


def test_entity_page_passes_a_past_period_to_save_as_chart() -> None:
    """Nur „Als Chart speichern“ nimmt die Periode mit; die Entitätsseite
    selbst merkt sich nichts (kein offset in ihren gespeicherten Optionen)."""
    js = (APP / "static" / "js" / "pages" / "entity_detail.js").read_text(encoding="utf-8")
    getter = js.split("get saveAsChartUrl() {", 1)[1].split("\n        },", 1)[0]
    assert "if (this.offset !== 0" in getter
    assert "params.set('anchor'," in getter
    options = js.split("async saveChartOptions() {", 1)[1].split("\n        },", 1)[0]
    assert "offset" not in options


def test_dashboard_tile_shows_the_fixed_period() -> None:
    tiles_tpl = (TEMPLATES / "_dashboard_tiles.html").read_text(encoding="utf-8")
    assert 'data-period-anchor="{{ tile.period_anchor or \'\' }}"' in tiles_tpl
    tiles_js = (APP / "static" / "js" / "dashboard-tiles.js").read_text(encoding="utf-8")
    assert "offset = PeriodNavigation.offsetForRange(range, new Date(y, m - 1, d, 12).getTime());" in tiles_js
    assert "offset: String(offset)" in tiles_js
    for page in ("dashboard_detail.html", "entities.html"):
        tpl = (TEMPLATES / page).read_text(encoding="utf-8")
        assert tpl.index("js/period-navigation.js") < tpl.index("js/dashboard-tiles.js"), page


def test_range_change_keeps_the_shown_point_in_time_and_reclick_returns_to_now() -> None:
    fn = EDITOR_JS.split("        setRange(key) {", 1)[1].split("\n        },", 1)[0]
    assert "if (key === this.range) {" in fn
    assert "if (this.offset !== 0) this.goToNow();" in fn
    assert "PeriodNavigation.anchorForWindow(" in fn
    assert "this.offset = PeriodNavigation.offsetForRange(key, anchorMs);" in fn


def test_calendar_jump_translates_a_date_into_the_offset() -> None:
    fn = EDITOR_JS.split("function jumpToDate(dateStr) {", 1)[1].split("\n    }", 1)[0]
    assert "PeriodNavigation.offsetForRange(root.range, root._rangeAnchorMs)" in fn
    assert "root.load();" in fn
    assert "window.jumpToDate = jumpToDate;" in EDITOR_JS


def test_calendar_without_entity_skips_the_day_query_and_allows_every_day_in_range() -> None:
    """Ein Chart hat mehrere Entitäten: ohne entityId keine /data-days-Abfrage
    (die gäbe es für "null" nicht), jeder Tag im Bereich ist wählbar. Auf der
    Entitätsseite und in der Bereinigung bleibt alles wie bisher."""
    load = CALENDAR_JS.split("async loadMonth() {", 1)[1].split("\n    },", 1)[0]
    assert load.strip().startswith("if (!entityId) return;")
    assert "hasData: !entityId || this.dataDays.includes(d)" in CALENDAR_JS


def test_chart_editor_page_renders_with_calendar_bounds(client) -> None:
    response = client.get("/charts/new")
    assert response.status_code == 200
    assert "calendarPicker(null," in response.text


def test_period_anchor_is_stored_copied_and_validated(client) -> None:
    from app import main

    payload = {"name": "Fester Zeitraum Test", "entity_ids": ["sensor.gibt_es_nicht"], "range_key": "year",
               "period_anchor": "2025-07-02"}
    chart_id = client.post("/charts", json=payload).json()["id"]
    try:
        assert main.index.get_saved_chart(chart_id)["period_anchor"] == "2025-07-02"
        page = client.get(f"/charts/{chart_id}").text
        assert 'const PERIOD_ANCHOR = "2025-07-02";' in page

        client.post(f"/charts/{chart_id}", json={**payload, "period_anchor": None})
        assert main.index.get_saved_chart(chart_id)["period_anchor"] is None

        client.post(f"/charts/{chart_id}", json=payload)
        copy_id = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
        assert main.index.get_saved_chart(copy_id)["period_anchor"] == "2025-07-02"
        main.index.delete_saved_chart(copy_id)

        bad = client.post(f"/charts/{chart_id}", json={**payload, "period_anchor": "2025-13-40"})
        assert bad.status_code == 400
    finally:
        main.index.delete_saved_chart(chart_id)


def test_save_as_chart_prefill_carries_the_period(client) -> None:
    page = client.get("/charts/new?entity_id=sensor.gibt_es_nicht&range=year&anchor=2025-07-02").text
    assert 'const PERIOD_ANCHOR = "2025-07-02";' in page
    plain = client.get("/charts/new?entity_id=sensor.gibt_es_nicht&range=year").text
    assert "const PERIOD_ANCHOR = null;" in plain


def test_choosing_a_comparison_returns_to_the_current_period() -> None:
    """Vorjahr, Langzeit usw. beziehen sich auf die laufende Periode: wer vorher
    zurückgeblättert hatte, landet beim Wählen eines Vergleichs wieder bei
    "jetzt". setLongterm() läuft über setCompareMode() und ist damit erfasst."""
    fn = EDITOR_JS.split("setCompareMode(mode) {", 1)[1].split("\n        },", 1)[0]
    assert "this.offset = 0;" in fn
    assert "this._rangeAnchorMs = null;" in fn
    assert fn.index("this.offset = 0;") < fn.index("this.load();")
    assert "this.setCompareMode('longterm');" in EDITOR_JS.split("setLongterm() {", 1)[1].split("\n        },", 1)[0]
