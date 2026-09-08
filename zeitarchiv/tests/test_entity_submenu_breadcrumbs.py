"""Zusagen über die Kopfzeilen der drei Entitätsseiten.

Geschichte dieser Datei in zwei Schritten, weil sie sonst wie eine
Meinungsänderung ohne Grund aussieht:

1. Ursprünglich trugen die Unterseiten eine eigene Breadcrumb
   (``class="crumb"``) zurück zur Entitätenliste — redundant neben dem
   ohnehin vorhandenen "← zurück"-Link. Sie wurde entfernt, und beide
   Unterseiten verlinkten stattdessen zurück zum Verlauf DERSELBEN Entität.
2. Seit die drei Seiten eine gemeinsame Reiterzeile haben
   (``_entity_tabs.html``), ist auch dieser Link redundant: der Verlauf IST
   ein Reiter. An seiner Stelle steht jetzt wieder der Weg eine Ebene höher —
   den bieten die Reiter nicht an, und die Verlaufsseite hat ihn an derselben
   Stelle schon, wodurch alle drei Köpfe gleich aufgebaut sind.

Geprüft wird deshalb beides: dass die redundanten Links weg sind UND dass die
Reiterzeile auf allen drei Seiten steht. Fehlte sie auf einer, wäre sie eine
Einbahnstraße — man käme hin, aber nicht zurück.
"""

from __future__ import annotations


from _paths import TEMPLATES


TEMPLATES_DIR = TEMPLATES
VERLAUF_LINK = '<a href="{{ app_root }}/entities/{{ entity_id }}">← zurück zum Verlauf</a>'
LISTEN_LINK = '{{ app_root }}/entities">← zurück zu Entitäten</a>'


def _source(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def test_all_three_entity_pages_carry_the_tab_row() -> None:
    """Ohne die Zeile auf JEDER der drei Seiten führt sie nur in eine
    Richtung."""
    for datei, aktiv in (
        ("entity_detail.html", "verlauf"),
        ("cleanup.html", "werte"),
        ("entity_config.html", "konfig"),
    ):
        source = _source(datei)
        assert '{% include "_entity_tabs.html" %}' in source, datei
        assert f"{{% with active = '{aktiv}' %}}" in source, datei


def test_the_tab_row_links_to_all_three_pages() -> None:
    """Die Ziele stehen nur an dieser einen Stelle — verrutscht eines, ist der
    Reiter still eine Sackgasse."""
    source = _source("_entity_tabs.html")
    for ziel in (
        '{{ app_root }}/entities/{{ entity_id }}"',
        '{{ app_root }}/entities/{{ entity_id }}/cleanup"',
        '{{ app_root }}/entities/{{ entity_id }}/config"',
    ):
        assert ziel in source, ziel


def test_the_sub_pages_no_longer_repeat_what_the_tabs_offer() -> None:
    """Weder die alte Breadcrumb noch der Rücksprung zum Verlauf: den trägt
    jetzt der Reiter. Stattdessen der Weg eine Ebene höher."""
    for datei in ("cleanup.html", "entity_config.html"):
        source = _source(datei)
        assert 'class="crumb"' not in source, datei
        assert VERLAUF_LINK not in source, datei
        assert LISTEN_LINK in source, datei


def test_the_options_menu_keeps_only_actions() -> None:
    """"Werte bearbeiten" und "Konfiguration" sind Seiten, keine Aktionen —
    sie gehören in die Reiter, nicht ins Menü. "Als Chart speichern" bleibt:
    das tut etwas auf DIESER Seite."""
    source = _source("entity_detail.html")
    menue_zeilen = [z for z in source.splitlines() if 'class="menu-row"' in z]
    assert any("Als Chart speichern" in z for z in menue_zeilen)
    for weg in ("Werte bearbeiten", "Konfiguration"):
        assert not any(weg in z for z in menue_zeilen), weg


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
