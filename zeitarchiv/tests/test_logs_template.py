"""Strukturtests für Protokollseite, Einstellungen und Ingress-Limit."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT  / "app" / "templates"


def test_logs_page_uses_text_content_for_untrusted_log_lines() -> None:
    source = (TEMPLATES / "logs.html").read_text(encoding="utf-8")
    assert "output.textContent" in source
    assert "output.innerHTML" not in source
    assert "api/logs" in source
    assert "logs/download" in source
    assert 'id="log-source-mode"' in source
    assert "15000" in source


def test_logs_search_uses_shared_search_field_style() -> None:
    source = (TEMPLATES / "logs.html").read_text(encoding="utf-8")
    assert 'id="log-search" class="search" type="search"' in source


def test_logs_template_compiles() -> None:
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.get_template("logs.html")


def test_logs_page_embeds_logging_settings_directly() -> None:
    # Umgekehrt zum früheren Stand: Protokollierung wird nicht mehr nur
    # verlinkt, sondern direkt auf der Protokollseite eingebettet — wer sich
    # das Protokoll ansieht, will die Stufe oft im selben Moment anpassen
    # (siehe Kommentar in logs.html).
    source = (TEMPLATES / "logs.html").read_text(encoding="utf-8")
    assert '_settings_logging_form.html' in source
    assert 'id="log-settings-section"' in source
    assert 'href="settings#protokollierung"' not in source


def test_logging_settings_are_not_duplicated_in_settings_nav() -> None:
    # Protokollierung ist seit der Einbettung in logs.html keine eigene
    # settings.html-Sektion mehr — dort würde sie sonst doppelt gepflegt.
    nav = (TEMPLATES / "_settings_nav.html").read_text(encoding="utf-8")
    settings = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    assert "protokollierung" not in nav
    assert '_settings_logging_form.html' not in settings


def test_ingress_accepts_two_gib_zip_plus_multipart_overhead() -> None:
    nginx = (ROOT  / "nginx.conf").read_text(encoding="utf-8")
    addon_config = (ROOT  / "config.yaml").read_text(encoding="utf-8")
    assert "client_max_body_size 2050m;" in nginx
    assert "access_log off;" in nginx
    assert "ingress_stream: true" in addon_config


def test_addon_grants_access_to_supervisor_logs_api() -> None:
    addon_config = (ROOT  / "config.yaml").read_text(encoding="utf-8")
    assert "hassio_api: true" in addon_config


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
