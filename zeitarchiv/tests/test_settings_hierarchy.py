"""Regression tests for the visual and semantic settings hierarchy."""

from pathlib import Path

import sys

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.formatting import format_int, format_value

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
CSS = (ROOT / "app" / "static" / "css" / "app.css").read_text(
    encoding="utf-8"
)


def test_settings_main_areas_are_second_level_sections() -> None:
    # rotation/speicherplatz/aufbewahrung zogen mit 0.75.0 nach Housekeeping;
    # protokollierung ist seit der Einbettung in logs.html keine eigene
    # settings.html-Sektion mehr (siehe test_logs_template.py). meldungen kam
    # mit den Tipps/Meldungen-Einstellungen neu dazu.
    source = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    section_ids = (
        "darstellung",
        "archivierung",
        "meldungen",
        "verbindung",
        "diagnose",
        "ueber",
    )
    for section_id in section_ids:
        assert f'<section id="{section_id}" class="settings-section">' in source
    assert source.count('class="settings-section-title"') == len(section_ids)
    assert 'style="margin-top:28px;padding-top:22px' not in source


def test_storage_and_retention_use_distinct_sublevels() -> None:
    # Speicherplatz/Endgültige Bereinigung lebt seit 0.75.0 in housekeeping.html.
    housekeeping = (TEMPLATES / "housekeeping.html").read_text(encoding="utf-8")
    storage = (TEMPLATES / "_settings_storage_index_form.html").read_text(
        encoding="utf-8"
    )
    purge = (TEMPLATES / "_settings_purge_form.html").read_text(encoding="utf-8")
    retention = (TEMPLATES / "_settings_retention_form.html").read_text(
        encoding="utf-8"
    )

    assert '<h2 class="settings-section-title">Speicherplatz</h2>' in housekeeping
    assert '<h3 class="settings-subsection-title">Indexkonsistenz</h3>' in storage
    assert '<h3 class="settings-subsection-title">Endgültige Bereinigung</h3>' in housekeeping
    assert '<h4 class="settings-minor-title">Vorschau der Bereinigung</h4>' in purge
    assert '<h3 class="settings-subsection-title">Automatische Durchsetzung</h3>' in retention
    assert retention.index("Automatische Durchsetzung") < retention.index(
        "Bestand und Fälligkeit"
    )


def test_settings_hierarchy_has_shared_spacing_and_typography() -> None:
    for selector in (
        ".settings-section + .settings-section",
        ".settings-panel .settings-section-title",
        ".settings-subsection + .settings-subsection",
        ".settings-panel .settings-subsection-title",
        ".settings-panel .settings-minor-title",
    ):
        assert selector in CSS


def test_changed_settings_templates_compile() -> None:
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.filters["format_int"] = format_int
    environment.filters["format_value"] = format_value
    for template in (
        "settings.html",
        "_settings_storage_index_form.html",
        "_settings_purge_form.html",
        "_settings_retention_form.html",
    ):
        environment.get_template(template)
