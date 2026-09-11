"""Demo-Modus-Banner (base.html) — Nutzerbeobachtung: es gab bislang gar
keinen sichtbaren Hinweis irgendwo in der App, dass gerade synthetische
Vorführdaten statt echter Werte gezeigt werden (nur zwei Stellen kannten
demo_mode_active überhaupt: Housekeeping und Einstellungen). Jetzt global
über den bestehenden _font_scale_context-Kontextprozessor auf JEDER Seite.
"""

from __future__ import annotations


def test_banner_hidden_by_default(client) -> None:
    response = client.get("/uebersicht")
    assert "demo-banner" not in response.text


def test_banner_shown_when_demo_mode_is_active(client, monkeypatch) -> None:
    monkeypatch.setattr("app.main.DEMO_MODE", True)
    response = client.get("/uebersicht")
    assert "demo-banner" in response.text
    assert "Demo-Modus aktiv" in response.text


def test_banner_appears_on_pages_without_their_own_demo_context(client, monkeypatch) -> None:
    """Vorher kannten nur Housekeeping und Einstellungen demo_mode_active —
    eine beliebige dritte Seite (hier: Entitäten) durfte davon bisher nichts
    wissen. Jetzt läuft es global mit, ohne dass die Route es selbst in
    ihren Kontext aufnehmen muss."""
    monkeypatch.setattr("app.main.DEMO_MODE", True)
    response = client.get("/entities")
    assert "demo-banner" in response.text
