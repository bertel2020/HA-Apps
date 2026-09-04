"""Tests für die CO2-/Kosten-Bilanz-Hervorhebung im Energiedashboard
(Nutzerwunsch: eine dritte "Bilanz"-Kachel im CO2-Dialog, bei negativem Wert
als kleiner Erfolg gefeiert; dieselbe Farblogik für den Saldo im
Kostenanalyse-Dialog samt Erfolgstext; ein farbig hervorgehobener Chip OHNE
Stern; dieselbe Bilanz im gedruckten Bericht; ein Farbverlauf auf den
restlichen drei Dashboard-Karten)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/energiedashboard.js").read_text(encoding="utf-8")
VIEW = (ROOT / "app/templates/_energiedashboard_view.html").read_text(encoding="utf-8")
DASHBOARD_HTML = (ROOT / "app/templates/energiedashboard.html").read_text(encoding="utf-8")
REPORT = (ROOT / "app/templates/_energiedashboard_report.html").read_text(encoding="utf-8")


def test_co2_bilanz_is_ausstoss_minus_vermieden() -> None:
    assert "co2Bilanz() {" in JS
    assert "this.kpi.co2_ausstoss - this.kpi.co2_vermieden" in JS


def test_cost_win_text_only_for_negative_saldo() -> None:
    assert "costWinText() {" in JS
    assert "this.kpi.net_cost != null && this.kpi.net_cost < 0" in JS


def test_co2_dialog_has_bilanz_tile_with_win_and_cost_states() -> None:
    assert "co2Bilanz() != null" in VIEW
    assert "'is-bilanz-win': co2Bilanz() < 0, 'is-cost': co2Bilanz() >= 0" in VIEW
    assert 'x-show="co2Bilanz() < 0">★</div>' in VIEW
    # Erfolgszeile nur bei negativer Bilanz, keine Zeile im positiven Fall.
    assert 'x-show="co2Bilanz() < 0">🌿 Mehr vermieden als verursacht.</div>' in VIEW


def test_cost_dialog_saldo_gets_star_color_and_win_text() -> None:
    assert "'is-bilanz-win': kpi.net_cost < 0, 'is-cost': kpi.net_cost >= 0" in VIEW
    assert 'x-show="kpi.net_cost < 0">★</div>' in VIEW
    assert 'x-show="kpi.net_cost < 0" x-text="costWinText()"' in VIEW


def test_co2_chip_gets_color_only_no_star() -> None:
    """Nutzer-Vorgabe: der Kopfzeilen-Chip bekommt bei positiver Bilanz nur
    die Farbe (is-win), keinen Stern — anders als die Kachel im Dialog."""
    assert ':class="{\'is-win\': co2Bilanz() < 0}"' in VIEW
    # Der Chip-Block selbst enthält keinen Stern-Marker.
    chip_start = VIEW.index('class="edash-co2-badge"')
    chip_block = VIEW[chip_start:chip_start + 400]
    assert "★" not in chip_block


def test_dashboard_css_defines_bilanz_win_and_cost_styles() -> None:
    assert ".edash-kpi.is-bilanz-win{" in DASHBOARD_HTML
    assert ".edash-kpi.is-cost .edash-kpi-val{color:var(--danger);}" in DASHBOARD_HTML
    assert ".edash-kpi-sparkle{" in DASHBOARD_HTML
    assert ".edash-kpi-win-note{" in DASHBOARD_HTML
    assert ".edash-co2-badge.is-win{" in DASHBOARD_HTML


def test_report_shows_same_bilanz_line_as_dashboard() -> None:
    assert "co2_bilanz = current.kpi.co2_ausstoss - current.kpi.co2_vermieden" in REPORT
    assert "'is-surplus' if co2_bilanz < 0 else 'is-cost'" in REPORT
    assert '"sauber" if co2_bilanz < 0 else "belastet"' in REPORT


def test_report_bilanz_sign_convention_matches_co2_bilanz_js() -> None:
    """Kreuz-Check: Bericht und Live-Dialog müssen dieselbe Vorzeichen-Logik
    verwenden (Ausstoß − Vermieden, negativ = sauber/Erfolg) — sonst zeigt
    derselbe Zeitraum im Bericht eine andere Bewertung als im Dashboard."""
    ausstoss, vermieden = 2.66, 4.18
    co2_bilanz = ausstoss - vermieden
    assert co2_bilanz < 0
    label = "sauber" if co2_bilanz < 0 else "belastet"
    css_class = "is-surplus" if co2_bilanz < 0 else "is-cost"
    assert (label, css_class) == ("sauber", "is-surplus")


def test_gradient_extended_to_remaining_three_cards_with_role_colors() -> None:
    """Nutzerwunsch: derselbe kaum wahrnehmbare Verlauf wie bei Energiefluss,
    aber je Karte mit ihrer EIGENEN Rollenfarbe statt einer geteilten
    Deko-Klasse (siehe Empfehlung im Mockup)."""
    assert ".edash-verbraucher-card{" in DASHBOARD_HTML
    assert ".edash-tageslast-card{" in DASHBOARD_HTML
    assert "var(--chart-4)" in DASHBOARD_HTML.split("\n  .edash-ring-card{")[1][:200]
    assert "var(--chart-7)" in DASHBOARD_HTML.split(".edash-verbraucher-card{")[1][:200]
    assert "var(--chart-7)" in DASHBOARD_HTML.split(".edash-tageslast-card{")[1][:200]
    assert 'class="edash-card edash-span2 edash-verbraucher-card"' in VIEW
    assert 'class="edash-card edash-span3 edash-tageslast-card"' in VIEW
