"""Regressionstests für die Speichernutzung auf der Statistikseite."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


class _FakeURL:
    path = "/statistik"


class _FakeRequest:
    """Minimaler Ersatz für Starlettes Request — _topnav.html liest nur
    request.url.path (aktuelle Seite hervorheben), sonst nichts."""

    url = _FakeURL()


def _render_storage_table() -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    rows = [
        {"key": "archive", "label": "Archiv", "size": "1 MB", "percent": 10, "bytes": 1, "href": None},
        {"key": "rollup", "label": "Rollups", "size": "1 MB", "percent": 10, "bytes": 1, "href": None},
        {"key": "hot", "label": "Laufender Monat (Hot Buffer)", "size": "1 MB", "percent": 10, "bytes": 1, "href": None},
        {"key": "index", "label": "Index", "size": "1 MB", "percent": 10, "bytes": 1, "href": "statistik/index", "optimization_recommended": True},
        {"key": "reports", "label": "Import-Reports", "size": "1 MB", "percent": 10, "bytes": 1, "href": "import?tab=reports"},
        {"key": "backups", "label": "Backups", "size": "1 MB", "percent": 10, "bytes": 1, "href": "backup"},
        {"key": "import", "label": "Import-Zwischendateien", "size": "1 MB", "percent": 50, "bytes": 5, "href": "import"},
    ]
    return environment.get_template("statistik.html").render(
        request=_FakeRequest(),
        css_v=1,
        font_scale_value="1",
        app_root="",
        entity_count=0,
        total_rows="0",
        total_size="0 B",
        has_growth_history=False,
        growth_range_options=[],
        growth_points=[],
        storage_breakdown=rows,
        storage_total_size="6 MB",
        by_type=[],
        by_resolution=[],
        retention_due_rows="120",
        retention_due_entities=1,
        retention_due_months=2,
        retention_due_size="1 MB",
        retention_history_30d_rows="50",
        retention_history_30d_size="500 KB",
        retention_history_all_rows="500",
        retention_history_all_size="5 MB",
        retention_jobs=[],
        duplicates_by_entity=[],
        duplicates_total="0",
        generated_at="24.08.2026 15:00",
    )


def test_backup_and_import_are_links_inside_storage_table() -> None:
    html = _render_storage_table()
    assert '<a class="storage-category-link" href="backup">Backups</a>' in html
    assert '<a class="storage-category-link" href="import">Import-Zwischendateien</a>' in html
    assert '<a class="storage-category-link" href="import?tab=reports">Import-Reports</a>' in html
    assert '<a class="storage-category-link" href="statistik/index">Index</a>' in html
    assert html.count('class="storage-optimization-chip">Optimierung empfohlen</span>') == 1
    assert "verwalten/löschen" not in html


def test_index_precedes_the_two_linked_categories() -> None:
    html = _render_storage_table()
    index_pos = html.index('data-storage-label="Index"')
    backup_pos = html.index('data-storage-label="Backups"')
    import_pos = html.index('data-storage-label="Import-Zwischendateien"')
    assert index_pos < backup_pos < import_pos


def test_storage_category_stays_on_one_line_at_normal_font_size() -> None:
    source = (TEMPLATES_DIR / "statistik.html").read_text(encoding="utf-8")
    assert 'class="dt stats-dt storage-usage-table"' in source
    assert "table.storage-usage-table{min-width:600px;}" in source
    assert ".storage-usage-table td:first-child{white-space:nowrap;}" in source
    assert ".stats-col-storage-label{width:55%;}" in source
    assert ".stats-col-storage-size{width:26%;}" in source
    assert ".stats-col-storage-percent{width:19%;}" in source


def test_storage_usage_table_is_sortable_with_numeric_values_and_fixed_total() -> None:
    html = _render_storage_table()
    assert 'class="dt stats-dt storage-usage-table" data-sortable' in html
    assert '<td data-sort="5">1 MB</td>' in html
    assert '<tfoot><tr><td class="label"><strong>Gesamt</strong>' in html


def test_growth_chart_uses_dynamic_y_axes() -> None:
    source = (TEMPLATES_DIR / "statistik.html").read_text(encoding="utf-8")
    assert source.count("{type: 'value', scale: true") == 2


def test_all_statistics_data_tables_use_standard_sorting() -> None:
    source = (TEMPLATES_DIR / "statistik.html").read_text(encoding="utf-8")
    css = (TEMPLATES_DIR.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    # War 4, bevor die Bestand-und-Fälligkeit-Tabelle mit Aufbewahrung/Rotation
    # nach Housekeeping zog (siehe _settings_retention_form.html).
    assert source.count("data-sortable") == 3
    assert 'data-sort="{{ row.entity_count_raw }}"' in source
    assert 'data-sort="{{ row.total_rows_raw }}"' in source
    assert 'data-sort="{{ row.total_size_raw }}"' in source
    assert 'th.dt-sort-asc::after{content:" ↓";}' in css
    assert 'th.dt-sort-desc::after{content:" ↑";}' in css
    assert 'content:" ▲"' not in css and 'content:" ▼"' not in css


def test_retention_overview_separates_due_and_historical_deletions() -> None:
    # Lebt seit 0.75.0 in _settings_retention_form.html (Housekeeping) statt
    # statistik.html — die "not in"-Abgrenzung gegen die Bestand-und-
    # Fälligkeit-Tabelle entfällt, weil beides jetzt bewusst im selben Formular
    # liegt (siehe test_retention_breakdown_is_located_with_retention_settings).
    retention = (TEMPLATES_DIR / "_settings_retention_form.html").read_text(encoding="utf-8")
    assert "Aktuell durch Aufbewahrung fällig" in retention
    assert "Manuell zur Löschung markiert" not in retention
    assert "<h2>Zur Löschung markiert</h2>" not in retention
    assert "Letzte 30 Tage endgültig gelöscht" in retention


def test_reclaimable_storage_tile_uses_short_single_line_caption() -> None:
    retention = (TEMPLATES_DIR / "_settings_retention_form.html").read_text(encoding="utf-8")
    assert '<div class="sub-value">Archivdateien</div>' in retention
    assert "Archivdateien der aktuellen Vorschau" not in retention


def test_retention_status_tiles_use_surface_background() -> None:
    source = (TEMPLATES_DIR / "_settings_retention_form.html").read_text(encoding="utf-8")
    css = (TEMPLATES_DIR.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert source.count('class="status-card"') == 4
    assert ".status-card{" in css and "background:var(--surface)" in css


def test_ui_typography_and_field_colors_follow_shared_semantics() -> None:
    css = (TEMPLATES_DIR.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    backup = (TEMPLATES_DIR / "_settings_backup_schedule_form.html").read_text(encoding="utf-8")
    archive_settings = (TEMPLATES_DIR / "_settings_archivierung_form.html").read_text(encoding="utf-8")
    entity_settings = (TEMPLATES_DIR / "_entity_config_form.html").read_text(encoding="utf-8")
    assert ".settings-panel p{" in css and "max-width:100%" in css
    assert ".settings-panel .hint,.settings-panel p.hint" in css
    assert "max-width:78ch" not in css and "max-width:82ch" not in css
    assert "textarea:not([readonly]):not(:disabled){background:var(--surface)!important;}" in css
    assert "textarea:disabled{background:var(--surface-alt)!important" in css
    assert backup.count('class="status-card"') == 3
    assert "noch keinen Job" not in archive_settings
    assert "noch keinen Job" not in entity_settings


def test_retention_breakdown_is_located_with_retention_settings() -> None:
    settings = (TEMPLATES_DIR / "_settings_retention_form.html").read_text(encoding="utf-8")
    statistics = (TEMPLATES_DIR / "statistik.html").read_text(encoding="utf-8")
    assert "Bestand und Fälligkeit nach Aufbewahrungsfrist" in settings
    assert "retention_preview_generated_at" in settings
    assert "Bestand und Fälligkeit nach Aufbewahrungsfrist" not in statistics


def test_retention_summary_values_align_below_two_line_titles() -> None:
    # Beide Teile zogen mit der Retention-Übersicht nach Housekeeping um: das
    # Markup in _settings_retention_form.html, die CSS-Regel (kein eigener
    # app.css-Eintrag, siehe .seg-Kommentar dort) inline in housekeeping.html.
    retention = (TEMPLATES_DIR / "_settings_retention_form.html").read_text(encoding="utf-8")
    housekeeping = (TEMPLATES_DIR / "housekeeping.html").read_text(encoding="utf-8")
    assert 'class="stat-row retention-summary"' in retention
    assert ".retention-summary .stat .label{min-height:3em;}" in housekeeping


def test_index_details_explain_all_logical_database_areas() -> None:
    source = (TEMPLATES_DIR / "statistik_index.html").read_text(encoding="utf-8")
    main = (TEMPLATES_DIR.parent / "main.py").read_text(encoding="utf-8")
    assert "Entitäten und Archivstatus" in main
    assert "Schreibsicherheit und Bereinigung" in main
    assert "Charts, Tabellen und Dashboards" in main
    assert "Statistikverlauf" in main
    assert "Einstellungen und Wartung" in main
    assert "nicht die eigentlichen Messreihen" in source
    assert "SQLite-Struktur" in source
    assert "Fachdaten inkl. Indizes" in source
    assert "<th>Daten</th><th>Indizes</th><th>Gesamt</th>" in source
    assert "zugehörige Indizes sind jeder Fachtabelle zugerechnet" in source
    assert "Freier/reclaimbarer Speicher" in source
    assert ">Index optimieren</button>" in source
    assert "{% if index_optimization.can_optimize %}" in source
    assert "index-status-stat" in source
    assert "index-status-chip" not in source


def test_index_optimization_is_threshold_based_and_exclusive() -> None:
    main = (TEMPLATES_DIR.parent / "main.py").read_text(encoding="utf-8")
    optimization = (TEMPLATES_DIR.parent / "index_optimization.py").read_text(
        encoding="utf-8"
    )
    assert "INDEX_VACUUM_MIN_FILE_BYTES = 50 * 1024 * 1024" in optimization
    assert "INDEX_VACUUM_MIN_RECLAIMABLE_BYTES = 10 * 1024 * 1024" in optimization
    assert "INDEX_VACUUM_MIN_RECLAIMABLE_RATIO = 0.25" in optimization
    assert '@app.post("/statistik/index/optimize"' in main
    assert "with storage_coordinator.exclusive():" in optimization
    assert "shutil.disk_usage(index_path.parent).free" in optimization


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
