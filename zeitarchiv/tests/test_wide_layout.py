"""Breite Darstellung (Roadmap 1.40, Phase 1): Schalter, Attribut, Rahmen-Token.

Mit dem Schalter AUS muss die Oberfläche byte-genau wie vorher aussehen: kein
Attribut am <html>-Tag, Rahmenbreite weiter 1120px. Alle Regeln, die erst die
breite Darstellung bringt, hängen später an `:root[data-page-layout="wide"]`
und stehen in einem `@media (min-width:1200px)`-Block (Quelltext-Test unten).
"""

from __future__ import annotations

import re

from _paths import APP, APP_CSS, PAGE_CSS, STATIC, TEMPLATES

WIDE_ATTR = 'data-page-layout="wide"'


def test_frame_width_exists_only_as_a_token() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")
    assert ":root{--page-max:1120px;--page-narrow:1120px;}" in app_css
    assert app_css.count("1120px") >= 1
    assert "max-width:1120px" not in app_css
    backup = (PAGE_CSS / "backup_restore.css").read_text(encoding="utf-8")
    assert "max-width:1120px" not in backup
    assert ".rw-bar-in{max-width:var(--page-max);" in backup
    assert ".topnav-bar{max-width:var(--page-max);" in app_css
    assert ".page{max-width:var(--page-max);" in app_css


def test_wide_rules_live_only_inside_min_width_1200_media_blocks() -> None:
    for css in STATIC.glob("css/**/*.css"):
        text = css.read_text(encoding="utf-8")
        for match in re.finditer(r"\[data-page-layout=[\"']wide[\"']\]", text):
            before = text[: match.start()]
            media = before.rfind("@media")
            assert media != -1, f"{css.name}: Wide-Regel ohne @media"
            assert "min-width:1200px" in before[media: media + 60], f"{css.name}: Wide-Regel außerhalb von min-width:1200px"
            assert before.count("{") - before.count("}") >= 1, f"{css.name}: Wide-Regel nicht im @media-Block"


def test_no_container_type_on_dashboard_groups_or_grid() -> None:
    for css in STATIC.glob("css/**/*.css"):
        for rule in re.finditer(r"([^{}]+)\{([^{}]*container-type[^{}]*)\}", css.read_text(encoding="utf-8")):
            assert ".dgroup" not in rule.group(1) and ".dashboard-grid" not in rule.group(1), css.name


def test_base_template_sets_the_attribute_only_when_wide() -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "{% if page_layout == 'wide' %} data-page-layout=\"wide\"{% endif %}" in base


def test_setting_is_validated_stored_and_rendered(client) -> None:
    from app.main import index as app_index

    saved = app_index.get_setting("page_layout")
    try:
        assert client.post("/settings/darstellung", data={"page_layout": "nope"}).status_code == 400
        assert client.post("/settings/darstellung", data={"page_layout": "wide"}).status_code == 200
        assert app_index.get_setting("page_layout") == "wide"
        assert WIDE_ATTR in client.get("/uebersicht").text
        form = client.post("/settings/darstellung", data={"page_layout": "standard"})
        assert form.status_code == 200 and 'id="page-layout-input"' in form.text
        assert WIDE_ATTR not in client.get("/uebersicht").text
    finally:
        if saved is not None:
            app_index.set_setting("page_layout", saved)
        else:
            app_index._conn.execute("DELETE FROM settings WHERE key = 'page_layout'")
            app_index._conn.commit()


def test_default_is_standard_without_attribute(client) -> None:
    from app.main import index as app_index

    app_index._conn.execute("DELETE FROM settings WHERE key = 'page_layout'")
    app_index._conn.commit()
    assert WIDE_ATTR not in client.get("/uebersicht").text


def test_setting_is_part_of_the_selective_restore() -> None:
    from app.storage.restore_selective import GENERAL_SETTINGS

    assert "page_layout" in dict(GENERAL_SETTINGS)
    assert (APP / "main.py").read_text(encoding="utf-8").count("page_layout.") >= 3
