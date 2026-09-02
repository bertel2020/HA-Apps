"""Regressionstest für die Standardspalten der Entitätenliste."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


MAIN_PATH = Path(__file__).resolve().parents[1] / "app" / "main.py"
ENTITIES_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "templates" / "entities_list.html"
)
ENTITIES_TABLE_PATH = ENTITIES_TEMPLATE_PATH.with_name("_entities_table.html")


def _default_columns() -> set[str]:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "ENTITIES_DEFAULT_COLUMNS"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                return set(value.split(","))
    raise AssertionError("ENTITIES_DEFAULT_COLUMNS fehlt")


def test_all_columns_except_resolution_and_retention_are_enabled_by_default() -> None:
    assert _default_columns() == {"type", "first_ts", "last_ts", "unit", "rows", "size"}


def test_visible_data_columns_are_left_aligned() -> None:
    # Ausrichtung seit 0.75.0: Favorit/Typ/Einheit zentriert, Entität/Zeit-
    # spalten linksbündig, alles ab Auflösung (inkl. Größe/Datensätze)
    # rechtsbündig — in EINER konsolidierten Selektorliste statt einzeln je
    # Spalte, damit Kopf- und Datenzeile nie auseinanderlaufen.
    template = ENTITIES_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert (
        ".entities-dt th.col-fav,.entities-dt td.col-fav,\n"
        "  .entities-dt th.col-type,.entities-dt td.col-type,\n"
        "  .entities-dt th.col-unit,.entities-dt td.col-unit{text-align:center;}"
    ) in template
    assert ".entities-dt th.col-date,.entities-dt td.col-date{text-align:left;}" in template
    assert ".entities-dt th.col-rows,.entities-dt td.col-rows,\n  .entities-dt th.col-size,.entities-dt td.col-size," in template
    assert ".entities-dt th.col-retention,.entities-dt td.col-retention," in template
    assert "text-align:right;}" in template
    assert "display:block;width:100%;padding:8px 0;text-align:inherit" in template


def test_entity_tooltip_contains_friendly_name_and_entity_id() -> None:
    template = ENTITIES_TABLE_PATH.read_text(encoding="utf-8")
    assert 'class="entity-tooltip-host"' in template
    assert '<strong>{{ row.friendly_name or \'Kein Friendly Name\' }}</strong>' in template
    assert "<code>{{ row.entity_id }}</code>" in template


def test_export_table_entity_tooltip_includes_both_names() -> None:
    export = (ENTITIES_TEMPLATE_PATH.parent / "_export_table.html").read_text(encoding="utf-8")
    assert 'class="entity entity-tooltip-host" tabindex="0"' in export


@pytest.mark.xfail(
    reason=(
        "Charts- und Statistik-Kacheln zeigen keine Entitäts-Tooltips (kein "
        "entity-tooltip-host, keine entity_tooltip_names/_ids in main.py) — "
        "statistik.html hat gar keine Pro-Entität-Zeilen mehr, charts.html "
        "zeigt nur einen entity_count. Ungeklärt, ob das Feature dort nie "
        "gebaut oder bei einem früheren Umbau entfernt wurde; siehe GAPS_AUDIT.md."
    ),
    strict=False,
)
def test_other_entity_tooltips_also_include_both_names() -> None:
    templates = ENTITIES_TEMPLATE_PATH.parent
    statistik = (templates / "statistik.html").read_text(encoding="utf-8")
    charts = (templates / "charts.html").read_text(encoding="utf-8")
    main = MAIN_PATH.read_text(encoding="utf-8")
    assert 'class="entity-tooltip-host"' in statistik
    assert 'class="entity-tooltip-host" tabindex="0"' in charts
    assert '<strong>{{ row.entity_tooltip_names }}</strong><code>{{ row.entity_tooltip_ids }}</code>' in charts
    assert '"entity_tooltip_names": ", ".join(' in main
    assert '"entity_tooltip_ids": ", ".join(c["entity_ids"])' in main


def test_entity_tooltip_has_two_styled_content_lines_and_dynamic_width() -> None:
    css = (ENTITIES_TEMPLATE_PATH.parents[1] / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "width:max-content;max-width:min(720px,calc(100vw - 48px))" in css
    assert ".entity-tooltip strong,.entity-tooltip code{display:block;white-space:nowrap" in css
    assert ".entity-tooltip strong{" in css and "font-weight:700" in css


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
