"""Der Hinweis zur Nachkommastellen-Auswahl beschreibt beide Modi eindeutig."""


from _paths import APP



SOURCE = (APP / "templates/_entity_config_form.html").read_text(encoding="utf-8")


def test_decimals_hint_explains_automatic_and_fixed_formatting() -> None:
    assert "bis zu drei Stellen" in SOURCE
    assert "lässt Nullen am Ende weg" in SOURCE
    assert "rundet und füllt auf" in SOURCE
    assert "4,00 bei 2 Stellen" in SOURCE
