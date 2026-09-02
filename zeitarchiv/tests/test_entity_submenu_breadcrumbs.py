"""Regressionstests für den Rücksprung-Link der Entitäts-Unterseiten.

Die frühere Breadcrumb (class="crumb", Link zurück zur Entitätenliste) wurde
entfernt — der bereits vorhandene "← zurück"-Link genügt (redundant sonst).
Beide Unterseiten (Bereinigung/Konfiguration) verlinken jetzt zurück zum
Verlauf DERSELBEN Entität, nicht zur gesamten Liste."""

from __future__ import annotations

from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"
BACK_LINK = '<a href="{{ base }}/entities/{{ entity_id }}">← zurück zum Verlauf</a>'


def _source(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def test_cleanup_has_no_redundant_breadcrumb_and_links_back_to_entity() -> None:
    source = _source("cleanup.html")
    assert 'class="crumb"' not in source
    assert BACK_LINK in source


def test_configuration_has_no_redundant_breadcrumb_and_links_back_to_entity() -> None:
    source = _source("entity_config.html")
    assert 'class="crumb"' not in source
    assert BACK_LINK in source


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
