"""Regressionstests für Import/Export: keine redundante Breadcrumb.

Seit der globalen Menüleiste (0.30.0) gibt es auf diesen Seiten gar keine
Breadcrumb mehr — die Navigation übernimmt vollständig die Kopfzeile."""

from __future__ import annotations

from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


def _source(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def test_import_has_no_redundant_settings_link_or_breadcrumb() -> None:
    source = _source("import.html")
    assert '<a href="settings">Einstellungen</a>' not in source
    assert 'class="crumb"' not in source
    assert "<h1>Import</h1>" in source


def test_export_has_no_redundant_settings_link_or_breadcrumb() -> None:
    source = _source("export.html")
    assert '<a href="settings">Einstellungen</a>' not in source
    assert 'class="crumb"' not in source
    assert "<h1>CSV-Export</h1>" in source


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
