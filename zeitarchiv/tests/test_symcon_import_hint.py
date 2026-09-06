"""Regressionstest für den aktuellen Hinweis zum Symcon-Upload-Ablauf."""


from jinja2 import Environment, FileSystemLoader


from app.formatting import format_int, format_value

from _paths import TEMPLATES


SOURCE = (TEMPLATES / "import.html").read_text(encoding="utf-8")


def test_hint_describes_the_zip_upload_flow_without_bind_mount() -> None:
    assert "Bind-Mount" not in SOURCE
    assert "Den Symcon-<code>db</code>-Ordner als ZIP-Datei hochladen" in SOURCE
    assert "entpackt und scannt den Inhalt automatisch" in SOURCE
    assert "settings.json" in SOURCE


def test_hint_describes_non_destructive_merge_precisely() -> None:
    assert "Bestehende Monatsarchive werden nicht verändert" in SOURCE
    assert "nur noch nicht vorhandene Zeitstempel" in SOURCE
    assert "Rollups automatisch" in SOURCE
    assert "ein neuer ZIP-Upload ersetzt sie vollständig" in SOURCE


def test_import_template_compiles() -> None:
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.filters["format_int"] = format_int
    environment.filters["format_value"] = format_value
    environment.get_template("import.html")
