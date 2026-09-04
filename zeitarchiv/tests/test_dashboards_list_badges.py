"""Tests für zwei visuelle Markierungen auf /dashboards (Nutzerwunsch):
- das Standard-Dashboard bekommt denselben Farbverlauf-Kopfstreifen wie die
  feste Energiedashboard-Kachel.
- die Energiedashboard-Kachel bekommt ein Startseiten-Abzeichen, wenn die
  Einstellung "Startseite" (main.py STARTSEITE_LABELS) auf Energiedashboard
  steht."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMPLATE = (ROOT / "app/templates/dashboards.html").read_text(encoding="utf-8")
MAIN_SOURCE = (ROOT / "app/main.py").read_text(encoding="utf-8")


def test_default_dashboard_card_gets_the_accent_class() -> None:
    assert "class=\"card dash-card{{ ' is-default-home' if d.is_default else '' }}\"" in TEMPLATE


def test_accent_bar_css_uses_the_app_own_two_tone_accent() -> None:
    assert ".dash-card.is-default-home::before{" in TEMPLATE
    block = TEMPLATE.split(".dash-card.is-default-home::before{", 1)[1][:300]
    assert "var(--accent-line)" in block
    assert "var(--accent-bar)" in block


def test_home_badge_only_rendered_when_energiedashboard_is_the_start_page() -> None:
    assert "{% if startseite == 'energiedashboard' %}" in TEMPLATE
    assert 'class="edash-pin-home-badge"' in TEMPLATE
    assert 'data-tooltip="Aktuelle Startseite"' in TEMPLATE


def test_dashboards_list_route_passes_startseite_into_the_context() -> None:
    assert '"startseite": index.get_setting("startseite", "uebersicht"),' in MAIN_SOURCE
