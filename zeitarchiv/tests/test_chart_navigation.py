"""Zeitraum-Navigation im Chart-Editor (‹ Zeitraum ›, Kalender, „Zurück zu
jetzt“) — nur beim Anpassen, dieselben Bausteine wie auf der Entitätsseite,
nie gespeichert."""

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


def test_leaving_customize_mode_returns_to_the_current_period() -> None:
    """Nach dem Speichern zeigt die Ansicht wieder die laufende Periode;
    „Abbrechen“ lädt die Seite ohnehin neu."""
    save = EDITOR_JS.split("async save() {", 1)[1].split("\n        },", 1)[0]
    assert "this.editing = false;" in save
    assert save.index("this.editing = false;") < save.index("if (this.offset !== 0) this.goToNow();")
    cancel = EDITOR_JS.split("cancelEdit() {", 1)[1].split("\n        },", 1)[0]
    assert "window.location.reload();" in cancel


def test_navigation_scripts_are_loaded_before_the_editor() -> None:
    nav = EDITOR_TEMPLATE.index("js/period-navigation.js")
    cal = EDITOR_TEMPLATE.index("js/calendar-picker.js")
    editor = EDITOR_TEMPLATE.index("js/pages/chart_editor.js")
    assert nav < editor and cal < editor


def test_offset_goes_into_the_query_but_never_into_the_saved_chart() -> None:
    assert "offset: String(this.offset)" in EDITOR_JS
    assert "offset: '0'" not in EDITOR_JS
    body = EDITOR_JS.split("async save() {", 1)[1].split("const body = {", 1)[1].split("};", 1)[0]
    assert "offset" not in body
    assert 'x-show="editing && offset !== 0"' in EDITOR_TEMPLATE  # Hinweis neben „Speichern“


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
