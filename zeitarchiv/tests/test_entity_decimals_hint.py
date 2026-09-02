"""Der Hinweis zur Nachkommastellen-Auswahl beschreibt beide Modi eindeutig."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/templates/_entity_config_form.html").read_text(encoding="utf-8")


def test_decimals_hint_explains_automatic_and_fixed_formatting() -> None:
    assert "bis zu drei Nachkommastellen" in SOURCE
    assert "entfernt Nullen am Ende" in SOURCE
    assert "genau diese Stellenzahl gerundet" in SOURCE
    assert "4,00 bei 2 Stellen" in SOURCE
