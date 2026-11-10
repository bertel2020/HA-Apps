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
            head = before[media: media + 60]
            assert "min-width:1200px" in head or "min-width:1500px" in head, f"{css.name}: Wide-Regel außerhalb von min-width:1200/1500px"
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


def test_precise_mode_uses_four_columns_on_tablets() -> None:
    """Phase 2: zwischen 521 und 820px nicht sechs schmale Spalten, in beiden Kopien der Rasterregeln."""
    blocks = []
    for name in ("dashboard_detail.css", "entities.css"):
        css = (PAGE_CSS / name).read_text(encoding="utf-8")
        start = css.index("@media (min-width:521px) and (max-width:820px){")
        block = css[start: css.index("\n  }", start)]
        assert ".dashboard-grid.is-precise{grid-template-columns:repeat(4,minmax(0,1fr));}" in block
        spans = [int(n) for n in re.findall(r"grid-column:span (\d+)", block)]
        assert spans and max(spans) <= 4, name
        # Der Block für Telefone (eine Spalte) bleibt unverändert und kommt danach.
        assert css.index("@media (max-width:520px){", start) > start
        blocks.append(block)
    assert blocks[0] == blocks[1]


WIDE_TEMPLATES = {"charts", "tables", "table_editor", "chart_editor", "entities_list", "dashboard_detail", "entities", "dashboards",
                  "energiedashboard", "wetterdashboard"}


def test_only_the_planned_pages_opt_into_the_wide_frame() -> None:
    """Exakt die Seiten aus dem Plan; Formulare und Einstellungen bleiben schmal."""
    found = {
        path.stem for path in TEMPLATES.glob("*.html")
        if re.search(r'class="page page-wide"', path.read_text(encoding="utf-8"))
    }
    assert found == WIDE_TEMPLATES
    for name in ("settings",):
        assert "page-wide" not in (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


def test_wide_frame_rules_apply_only_at_1200px_and_center_narrow_pages() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    block = css[css.index("@media (min-width:1200px){\n  :root[data-page-layout=\"wide\"]{--page-max:1920px;}"):]
    block = block[: block.index("\n}")]
    assert '.page:not(.page-wide)' in block and ".rw-bar-in" in block
    assert "max-width:var(--page-narrow)" in block and "margin-left" not in block
    # Der Chart-Editor bekommt die höhere Fläche nur im Schalter-Block.
    editor = (PAGE_CSS / "chart_editor.css").read_text(encoding="utf-8")
    assert editor.count("#chart{height:max(420px,min(33vw,640px));}") == 1
    assert "#chart{width:100%;height:420px;max-width:100%;}" in editor


def test_dashboard_width_and_row_height_rules_live_in_the_switch_block() -> None:
    css = (PAGE_CSS / "dashboard_detail.css").read_text(encoding="utf-8")
    assert "cqw" not in css  # container-type auf .dgroup/.dashboard-grid prüft der Test oben
    assert css.count("--dash-max:1440px") == 1 and css.count("--dash-row-factor:.58") == 1
    start = css.index("@media (min-width:1200px){\n  :root[data-page-layout=\"wide\"]{--dash-max")
    block = css[start:]
    assert block.count("grid-auto-rows") == 2
    # Außerhalb des Schalter-Blocks bleibt die Zeilenhöhe wie bisher an der Schriftgröße.
    assert "grid-auto-rows:var(--dashboard-row-height)" in css[:start]
    assert "max(var(--dashboard-row-height)" in block


def test_energy_and_weather_pages_cap_their_content_at_1440px_inside_the_switch_block() -> None:
    for name, root in (("energiedashboard", "#energiedashboard-root"), ("wetterdashboard", "#wetter-root")):
        css = (PAGE_CSS / f"{name}.css").read_text(encoding="utf-8")
        rule = f':root[data-page-layout="wide"] {root}{{max-width:1440px;margin-inline:auto;}}'
        assert css.count(rule) == 1, name
        assert css[: css.index(rule)].rstrip().endswith("@media (min-width:1200px){"), name
    energy = (PAGE_CSS / "energiedashboard.css").read_text(encoding="utf-8")
    assert energy.count(':root[data-page-layout="wide"] .edash-soc-now[data-tooltip]::after{left:auto;right:0;}') == 1
    js = (STATIC / "js" / "energiedashboard.js").read_text(encoding="utf-8")
    assert "sankeyWideMinHeight" in js and "isWideLayout() && wrap ? wrap.clientWidth / 3 : 0" in js


def test_chart_heights_grow_only_inside_the_1500px_switch_block() -> None:
    for name, root in (("energiedashboard", "#energiedashboard-root"), ("wetterdashboard", "#wetter-root")):
        css = (PAGE_CSS / f"{name}.css").read_text(encoding="utf-8")
        start = css.index("@media (min-width:1500px){\n  :root[data-page-layout=\"wide\"] " + root + " .wt-chart{height:340px;}")
        block = css[start: css.index("\n}", start)]
        assert block.count("height:") >= 3 and all(f"{root} " in line for line in block.splitlines()[1:])
    assert ':root[data-page-layout="wide"] #wetter-root .wt-table{min-width:100%;}' in (PAGE_CSS / "wetterdashboard.css").read_text(encoding="utf-8")
    assert ':root[data-page-layout="wide"] #wetter-root .wt-days{grid-template-columns:repeat(7,minmax(0,1fr));}' in (PAGE_CSS / "wetterdashboard.css").read_text(encoding="utf-8")


def test_overview_tiles_and_dashboard_list_show_at_most_five_per_row_only_in_the_switch_block() -> None:
    for name, sel, minimum in (("entities.css", ".ov-tiles", 230), ("dashboards.css", ".dash-grid", 240)):
        css = (PAGE_CSS / name).read_text(encoding="utf-8")
        rule = (f':root[data-page-layout="wide"] {sel}{{grid-template-columns:repeat(auto-fill,'
                f'minmax(max({minimum}px, calc((100% - 4 * 14px) / 5)),1fr));}}')
        assert css.count(rule) == 1, name
        assert css[: css.index(rule)].rstrip().endswith("@media (min-width:1500px){"), name


def test_consumer_share_card_stays_stacked_like_the_supply_card_in_the_switch_block() -> None:
    css = (PAGE_CSS / "energiedashboard.css").read_text(encoding="utf-8")
    rule = ':root[data-page-layout="wide"] .edash-verbraucher-card .edash-share-wrap{flex-direction:column;align-items:stretch;gap:14px;}'
    assert css.count(rule) == 1
    assert css[: css.index(rule)].count('#energiedashboard-root{max-width:1440px;margin-inline:auto;}') == 1
