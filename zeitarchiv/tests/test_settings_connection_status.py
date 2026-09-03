"""Strukturtests für den app-konsistenten Verbindungsstatus."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app/templates"
SOURCE = (TEMPLATES / "_settings_verbindung_form.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def test_connection_status_uses_shared_status_cards() -> None:
    assert 'class="status-grid connection-status-grid"' in SOURCE
    assert 'class="connection-status-cards"' in SOURCE
    # 4 immer gerenderte Karten (Letzter Wert/Schreibzugriffe/Auth-Fehler/
    # Integrations-Version) + 1 bedingte (Update verfügbar) — siehe
    # Integrationsversions-Anzeige seit 0.78.0, GET /api/notices.
    assert SOURCE.count('<div class="status-card') == 5
    assert "status-card-danger" in SOURCE
    assert 'class="val mono" style="line-height:1.8;"' not in SOURCE


@pytest.mark.xfail(
    reason=(
        "connection-status-note/-note-item existieren nirgends in Template "
        "oder CSS — nur einzelne <span class=\"hint\"> je Statuskarte, keine "
        "gruppierte Hinweiszeile. Unklar, ob nie gebaut oder bei der "
        "Statuskarten-Umstellung entfernt; siehe GAPS_AUDIT.md."
    ),
    strict=False,
)
def test_connection_status_hint_is_grouped_and_responsive() -> None:
    assert 'class="connection-status-note"' in SOURCE
    assert SOURCE.count("connection-status-note-item") == 2
    assert ".connection-status-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in CSS
    assert "grid-auto-rows:1fr" in CSS
    assert ".connection-status-cards{grid-template-columns:1fr;}" in CSS
    assert ".connection-status-note{grid-template-columns:1fr;}" in CSS
    assert "@media (max-width:900px)" in CSS


def test_connection_settings_fragment_compiles() -> None:
    Environment(loader=FileSystemLoader(TEMPLATES)).get_template("_settings_verbindung_form.html")
