"""Räume in der Entitätsliste (Discussion 51, Wunsch 3): Zuordnung aus dem Katalog,
Filter, Spalte und Anzeige in der Kennzahlen-Leiste. Die Räume kommen nur lesend aus
dem Katalog der Integration; ohne Katalog gibt es weder Spalte noch Filter."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _paths import APP  # noqa: F401  (legt den App-Pfad auf sys.path)

IDS = ["sensor.room_a", "sensor.room_b", "sensor.room_c", "sensor.room_d"]


@pytest.fixture()
def data_dir() -> Path:
    return Path(os.environ["ZEITARCHIV_DATA_DIR"])


@pytest.fixture()
def rooms(client, data_dir):
    """Vier Entitäten; a und b im Hobbyraum bzw. Küche, c und d ohne Raum (d fehlt im Katalog)."""
    from app import main, selection_catalog

    for entity_id in IDS:
        main.index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)
    selection_catalog.save(data_dir, [
        {"entity_id": IDS[0], "area": "Hobbyraum", "floor": "Erdgeschoss"},
        {"entity_id": IDS[1], "area": "Küche", "floor": None},
        {"entity_id": IDS[2], "area": None},
    ])
    yield IDS
    (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)
    for entity_id in IDS:
        if main.index.get_entity(entity_id) is not None:
            main.index.delete_entity(entity_id)
    main.index._conn.execute("DELETE FROM settings WHERE key = 'entities_columns'")
    main.index._conn.commit()


def _found(*selected: str) -> list[str]:
    from app import entity_areas, main

    sets = entity_areas.area_filter(Path(os.environ["ZEITARCHIV_DATA_DIR"]), list(selected))
    return [r["entity_id"] for r in main.index.list_entities(search="sensor.room_", area_filter=sets)]


def test_area_map_only_lists_entities_with_a_room(client, rooms, data_dir) -> None:
    from app import entity_areas

    assert entity_areas.area_map(data_dir) == {
        IDS[0]: {"area": "Hobbyraum", "floor": "Erdgeschoss"},
        IDS[1]: {"area": "Küche", "floor": None},
    }
    assert entity_areas.area_names(data_dir) == ["Hobbyraum", "Küche"]


def test_area_map_follows_a_newer_catalog(client, rooms, data_dir) -> None:
    from app import entity_areas, selection_catalog

    assert entity_areas.area_map(data_dir)
    selection_catalog.save(data_dir, [{"entity_id": IDS[3], "area": "Bad"}])
    assert list(entity_areas.area_map(data_dir)) == [IDS[3]]


def test_no_catalog_means_no_rooms(client, data_dir) -> None:
    from app import entity_areas, selection_catalog

    (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)
    assert entity_areas.area_map(data_dir) == {}
    assert entity_areas.area_names(data_dir) == []


def test_filter_by_room_and_by_none(client, rooms) -> None:
    assert _found("Hobbyraum") == [IDS[0]]
    assert _found("Hobbyraum", "Küche") == [IDS[0], IDS[1]]
    assert _found("none") == [IDS[2], IDS[3]]  # c ohne Raum, d unbekannt
    assert _found("Küche", "none") == [IDS[1], IDS[2], IDS[3]]
    assert _found("all") == IDS  # kein Filter
    assert _found("Gibt es nicht") == []


def test_count_matches_the_list(client, rooms, data_dir) -> None:
    from app import entity_areas, main

    sets = entity_areas.area_filter(data_dir, ["Hobbyraum", "none"])
    assert main.index.count_entities(search="sensor.room_", area_filter=sets) == 3


def test_column_filter_and_cell_in_the_list(client, rooms) -> None:
    from app import main

    main.index.set_setting("entities_columns", "type,area")
    page = client.get("/entities").text
    assert 'id="area-filter-popover"' in page and 'value="Hobbyraum"' in page and 'value="none"' in page

    html = client.get("/entities-table", params={"search": "sensor.room_", "area": ["Küche"]}).text
    assert "sensor.room_b" in html and "sensor.room_a" not in html
    assert "col-area" in html

    full = client.get("/entities-table", params={"search": "sensor.room_"}).text
    assert "Hobbyraum" in full and "Erdgeschoss" in full  # Etage als Tooltip


def test_filter_is_ignored_while_the_column_is_hidden(client, rooms) -> None:
    from app import main

    main.index.set_setting("entities_columns", "type")
    html = client.get("/entities-table", params={"search": "sensor.room_", "area": ["Küche"]}).text
    assert "sensor.room_a" in html and "sensor.room_b" in html
    assert "col-area" not in html


def test_room_is_offered_only_with_a_catalog(client, data_dir) -> None:
    from app import main, selection_catalog

    (data_dir / selection_catalog.FILENAME).unlink(missing_ok=True)
    assert "area" not in {key for key, _ in main._entities_column_options()}
    main.index.set_setting("entities_columns", "type,area")
    assert "area" not in main._entities_visible_columns()
    assert 'id="area-filter-popover"' not in client.get("/entities").text
    main.index._conn.execute("DELETE FROM settings WHERE key = 'entities_columns'")
    main.index._conn.commit()


def test_header_of_the_room_column_is_not_a_sort_link(client, rooms) -> None:
    from app import main

    main.index.set_setting("entities_columns", "area")
    html = client.get("/entities-table", params={"search": "sensor.room_"}).text
    assert "sort=area" not in html


def test_entity_pages_show_the_room_read_only_next_to_the_id(client, rooms) -> None:
    for path in (f"/entities/{IDS[0]}/config", f"/entities/{IDS[0]}"):
        html = client.get(path).text
        assert 'class="entity-room"' in html and "Hobbyraum" in html and "Erdgeschoss" in html
        assert html.index(IDS[0]) < html.index('class="entity-room"')
    # ohne Raum: keine Pille, die Kennzahlen-Leiste hat keine eigene Zelle dafür
    assert 'class="entity-room"' not in client.get(f"/entities/{IDS[2]}/config").text
    assert "<dt>Raum</dt>" not in client.get(f"/entities/{IDS[0]}/config").text


def test_import_page_shows_room_and_category_columns(client, rooms) -> None:
    """Import → Home Assistant: Raum und Kategorie als Spalten, nur wenn es sie gibt."""
    from app import main

    html = client.get("/import?tab=ha").text
    assert 'data-sort-key="area"' in html
    assert 'data-sort-key="category"' not in html  # noch keine Kategorie angelegt

    category_id = main.index.create_entity_category("Energie")
    try:
        main.index.set_entities_category([rooms[0]], category_id)
        html = client.get("/import?tab=ha").text
        assert 'data-sort-key="category"' in html
        assert "Hobbyraum" in html and "Energie" in html
    finally:
        main.index.delete_entity_category(category_id)


def test_vm_and_influx_tables_show_room_and_category_columns(client, rooms) -> None:
    """Auch VictoriaMetrics und InfluxDB: Kategorie und Raum hinter der Einheit, nur wenn es sie gibt."""
    from app import main

    html = client.get("/import").text
    assert html.count('data-sort-key="area"') >= 3  # Home Assistant, VictoriaMetrics, InfluxDB
    category_id = main.index.create_entity_category("Verbrauch")
    try:
        main.index.set_entities_category([rooms[0]], category_id)
        html = client.get("/import").text
        for prefix in ("vm", "influx"):
            table = html[html.index(f'id="{prefix}-import-table"'):]
            table = table[: table.index("</table>")]
            assert table.index('data-sort-key="unit"') < table.index('data-sort-key="category"') < table.index('data-sort-key="area"')
            assert "Hobbyraum" in table and "Verbrauch" in table
    finally:
        main.index.delete_entity_category(category_id)


def test_import_filter_menu_lists_categories_rooms_and_types(client, rooms) -> None:
    """Das Menü „Filter“ bietet nur Werte an, die in der Tabelle vorkommen (Home Assistant, VictoriaMetrics, InfluxDB)."""
    from app import main

    category_id = main.index.create_entity_category("Verbrauch")
    try:
        main.index.set_entities_category([rooms[0]], category_id)
        html = client.get("/import").text
        for prefix in ("ha", "vm", "influx"):
            bar = html[html.index(f'id="{prefix}-filter"'):]
            bar = bar[: bar.index('data-filter-tags')]
            assert "data-filter-menu-btn" in bar
            area = bar[bar.index('data-filter-group="area"'):]
            assert "Hobbyraum" in area and "Küche" in area
            assert 'value="Verbrauch"' in bar
            assert 'data-filter-group="type"' in bar
            assert 'data-filter-chip="never"' in bar
    finally:
        main.index.delete_entity_category(category_id)


def test_symcon_filter_chips_are_rendered_when_variables_exist() -> None:
    from _paths import TEMPLATES

    text = (TEMPLATES / "import.html").read_text(encoding="utf-8")
    for chip in ("unmapped", "mapped", "unitdiff", "unnamed", "marked"):
        assert f'("{chip}"' in text
