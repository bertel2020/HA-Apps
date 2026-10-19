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
    „Zurück zu jetzt“ gibt es nur im Modus „Anpassen“ — in der Zeitraum-Leiste
    rechts neben der Zeitraumauswahl, wie auf der Entitätsseite."""
    row = EDITOR_TEMPLATE.split('<div class="chart-title-row chart-title-row-end">', 1)[1].split("<template x-if", 1)[0]
    assert '<span class="period-label" x-show="!editing" x-text="periodLabel"></span>' in row
    assert "chart-nav" not in row
    toolbar = EDITOR_TEMPLATE.split('<div class="toolbar" x-show="editing">', 1)[1].split('<div class="toolbar-right">', 1)[0]
    assert toolbar.index('<div class="seg">') < toolbar.index('<span class="nav chart-nav">')
    assert '@click="goBack()"' in toolbar
    assert ':disabled="!canGoForward" @click="goForward()"' in toolbar
    assert 'x-data=\'calendarPicker(null, {{ app_root | tojson }}, {{ calendar_first_date | tojson }}, {{ calendar_last_date | tojson }})\'' in toolbar
    assert '{% include "_calendar_popover.html" %}' in toolbar
    assert 'x-show="offset !== 0"' in toolbar and '@click="goToNow()"' in toolbar


def test_entity_page_has_the_same_back_to_now_chip() -> None:
    tpl = (TEMPLATES / "entity_detail.html").read_text(encoding="utf-8")
    nav = tpl.split('<div class="nav">', 1)[1]
    nav = nav[:nav.index('Zurück zu jetzt') + 40]
    assert '<button type="button" class="chip now-chip" x-show="offset !== 0" x-cloak @click="goToNow()">' in nav
    assert nav.index("goForward()") < nav.index("now-chip")


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


def test_comparison_option_fields_are_right_aligned() -> None:
    """Laufsumme/Soll (Vorjahr) und Jahre/Ø-Linie (Langzeit) stehen rechtsbündig,
    auch beim Anpassen, wenn links keine Zeitraumanzeige steht."""
    for marker in ("compareMode === 'previous' && range === 'year'", "compareMode === 'longterm'"):
        block = EDITOR_TEMPLATE.split(f'x-if="compare && {marker}">', 1)[1]
        assert block.lstrip().startswith('<span style="display:flex;gap:16px;margin-left:auto;">'), marker


def test_comparison_is_saved_and_restored_with_the_chart(client) -> None:
    """Vorjahr/Langzeit samt Jahren, Ø-Linie, Laufsumme und Soll gehen mit dem
    Chart; beim Öffnen stehen sie wieder so da."""
    from app import main

    settings = {"compare": True, "mode": "longterm", "years": 10, "average": False, "cumsum": False, "target": None}
    payload = {"name": "Vergleich gespeichert Test", "entity_ids": ["sensor.gibt_es_nicht"], "range_key": "year",
               "compare_settings": settings}
    chart_id = client.post("/charts", json=payload).json()["id"]
    try:
        assert main.index.get_saved_chart(chart_id)["compare_settings"] == settings
        page = client.get(f"/charts/{chart_id}").text
        assert "const COMPARE = true;" in page
        assert 'const COMPARE_MODE = "longterm";' in page
        assert '"years": 10' in page

        copy_id = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
        assert main.index.get_saved_chart(copy_id)["compare_settings"] == settings
        main.index.delete_saved_chart(copy_id)

        for bad in ({"mode": "irgendwas"}, {"years": 3}, {"target": "kein gueltiger name"}):
            response = client.post(f"/charts/{chart_id}", json={**payload, "compare_settings": {**settings, **bad}})
            assert response.status_code == 400, bad
    finally:
        main.index.delete_saved_chart(chart_id)


def test_editor_restores_and_saves_the_comparison() -> None:
    assert "const COMPARE_SETTINGS = {{ compare_settings | tojson }};" in EDITOR_TEMPLATE
    assert "multiYearAverage: COMPARE_SETTINGS.average ?? true," in EDITOR_JS
    assert "compareCumsum: !!COMPARE_SETTINGS.cumsum," in EDITOR_JS
    assert "SELECTED_ENTITY_IDS.includes(COMPARE_SETTINGS.target)" in EDITOR_JS
    body = EDITOR_JS.split("async save() {", 1)[1].split("const body = {", 1)[1].split("\n          };", 1)[0]
    assert "compare_settings: {" in body
    for key in ("compare: this.compare", "mode: this.compareMode", "years: this.compareYears",
                "average: this.multiYearAverage", "cumsum: this.compareCumsum", "target: this.compareSollEntityId || null"):
        assert key in body, key
