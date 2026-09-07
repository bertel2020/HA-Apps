"""Regressionstests für die reversible Löschbestätigung."""

from __future__ import annotations


from jinja2 import Environment, FileSystemLoader


from app.formatting import format_int, format_value

from _paths import APP, TEMPLATES, page_text




def test_cleanup_delete_requires_reversible_confirmation() -> None:
    rows = (TEMPLATES / "_rows_table.html").read_text(encoding="utf-8")
    assert "hx-confirm=" in rows
    assert "noch nicht endgültig gelöscht" in rows
    assert "Rückgängig (letzte Löschung)" in rows


def test_destructive_entity_actions_have_explicit_confirmations() -> None:
    cleanup = page_text("cleanup.html")
    detail = page_text("entity_detail.html")
    config = page_text("entity_config.html")
    assert "Alle Werte löschen" in config
    assert "Entität entfernen" in config
    assert "Dies kann nicht rückgängig gemacht werden" in config
    assert "automatisch neu angelegt" in config
    assert "static/js/confirm-dialog.js" in config
    assert "Alle Werte löschen" not in cleanup
    assert "Entität entfernen" not in detail


def test_cleanup_reuses_chart_period_anchor_and_shows_hour_date() -> None:
    cleanup = page_text("cleanup.html")
    rows = (TEMPLATES / "_rows_table.html").read_text(encoding="utf-8")
    chart_editor = page_text("chart_editor.html")
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "static/js/period-navigation.js" in cleanup
    assert "PeriodNavigation.anchorForWindow" in cleanup
    assert "PeriodNavigation.offsetForRange" in cleanup
    assert "pageData.rangeAnchorMs = picked.getTime()" in cleanup
    assert "pageData.windowEnd = {{ window_end_ts }}" in rows
    assert "pageData.isCurrent = {{ is_current | tojson }}" in rows
    assert "`${fmtDay(start)} · ${fmtTime(start)}–${fmtTime(end)} Uhr`" in chart_editor
    assert "strftime('%d.%m.%Y')} · {window_start.strftime('%H:%M')" in main


def test_cleanup_templates_compile() -> None:
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.filters["format_int"] = format_int
    environment.filters["format_value"] = format_value
    environment.get_template("cleanup.html")
    environment.get_template("_rows_table.html")
    environment.get_template("entity_config.html")


def test_settings_purge_shows_read_only_preview_permanently() -> None:
    purge = (TEMPLATES / "_settings_purge_form.html").read_text(encoding="utf-8")
    assert 'settings/purge/preview' not in purge
    assert "Vorschau der Bereinigung" in purge
    assert "Diese Vorschau verändert keine Daten." in purge
    assert "Zur Löschung markiert</div>" not in purge
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.filters["format_int"] = format_int
    environment.filters["format_value"] = format_value
    environment.get_template("_settings_purge_form.html")


def test_marked_points_are_loaded_on_demand_in_a_dialog() -> None:
    # Speicherplatz/"Endgültige Bereinigung" (samt Dialog) lebt seit 0.75.0 in
    # Housekeeping statt Einstellungen — die Formular-/Partial-Dateien selbst
    # blieben unverändert, nur eingebunden von einer anderen Seite.
    purge = (TEMPLATES / "_settings_purge_form.html").read_text(encoding="utf-8")
    housekeeping = (TEMPLATES / "housekeeping.html").read_text(encoding="utf-8")
    partial = (TEMPLATES / "_settings_marked_points.html").read_text(encoding="utf-8")
    assert "Markierte Datensätze anzeigen" in purge
    assert 'hx-get="settings/purge/marked"' in purge
    assert '<dialog id="marked-points-dialog"' in housekeeping
    assert "Messzeitpunkt" in partial and "Markiert am" in partial
    assert 'hx-include="#marked-points-search"' in partial
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.get_template("_settings_marked_points.html")


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
