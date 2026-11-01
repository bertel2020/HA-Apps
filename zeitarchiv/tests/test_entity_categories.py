"""Kategorien für Entitäten (ROADMAP 1.42, Phase 1): Speicher, Routen, Liste."""

from __future__ import annotations

import pytest

from _paths import APP
from app.storage.index import (
    MAX_CATEGORIES,
    MAX_CATEGORY_NAME_LENGTH,
    DuplicateNameError,
    InvalidNameError,
    NameTooLongError,
)


@pytest.fixture()
def entities(client):
    from app import main

    ids = ["sensor.cat_a", "sensor.cat_b", "sensor.cat_c"]
    for entity_id in ids:
        main.index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    yield ids
    for entity_id in ids:
        if main.index.get_entity(entity_id) is not None:
            main.index.delete_entity(entity_id)
    for category in main.index.list_entity_categories():
        main.index.delete_entity_category(category["id"])
    main.index.set_setting("entities_columns", "type,first_ts,last_ts,unit,rows,size")
    main.index._conn.execute("DELETE FROM settings WHERE key = 'entities_columns'")
    main.index._conn.commit()


def test_new_categories_get_position_and_rotating_colors(client, entities) -> None:
    from app import main

    first = main.index.create_entity_category("  Solar ")
    second = main.index.create_entity_category("Wärmepumpe")

    listed = main.index.list_entity_categories()
    assert [(c["id"], c["name"], c["position"]) for c in listed] == [(first, "Solar", 1), (second, "Wärmepumpe", 2)]
    assert [c["color_slot"] for c in listed] == [1, 2]


def test_names_are_unique_ignoring_case_and_length_limited(client, entities) -> None:
    from app import main

    main.index.create_entity_category("Wetter")
    with pytest.raises(DuplicateNameError):
        main.index.create_entity_category("WETTER")
    with pytest.raises(NameTooLongError):
        main.index.create_entity_category("x" * (MAX_CATEGORY_NAME_LENGTH + 1))
    with pytest.raises(InvalidNameError):
        main.index.create_entity_category("   ")


def test_category_count_is_limited(client, entities) -> None:
    from app import main

    for number in range(MAX_CATEGORIES):
        main.index.create_entity_category(f"K{number}")
    with pytest.raises(InvalidNameError):
        main.index.create_entity_category("zu viel")


def test_suggestions_are_created_all_or_nothing(client, entities) -> None:
    from app import main

    main.index.create_entity_category("Solar")
    with pytest.raises(DuplicateNameError):
        main.index.create_entity_categories(["Wetter", "solar"])
    assert [c["name"] for c in main.index.list_entity_categories()] == ["Solar"]


def test_assign_move_and_release(client, entities) -> None:
    from app import main

    solar = main.index.create_entity_category("Solar")
    wetter = main.index.create_entity_category("Wetter")

    assert main.index.set_entities_category(entities[:2], solar) == 2
    assert main.index.set_entities_category(entities[:2], solar) == 0  # nichts zu ändern
    assert main.index.set_entities_category([entities[0], "sensor.gibt_es_nicht"], wetter) == 1
    assert main.index.category_map_for(entities) == {entities[0]: wetter, entities[1]: solar}
    assert {c["name"]: c["entity_count"] for c in main.index.list_entity_categories()} == {"Solar": 1, "Wetter": 1}

    assert main.index.set_entities_category([entities[0]], None) == 1
    assert main.index.get_entity_category_id(entities[0]) is None
    with pytest.raises(ValueError):
        main.index.set_entities_category([entities[0]], 9999)


def test_delete_category_releases_entities_but_keeps_them(client, entities) -> None:
    from app import main

    solar = main.index.create_entity_category("Solar")
    main.index.set_entities_category(entities, solar)

    assert main.index.delete_entity_category(solar) == 3
    assert main.index.delete_entity_category(solar) == -1
    assert main.index.category_map_for(entities) == {}
    assert all(main.index.get_entity(entity_id) is not None for entity_id in entities)


def test_deleting_an_entity_drops_its_assignment(client, entities) -> None:
    from app import main

    solar = main.index.create_entity_category("Solar")
    main.index.set_entities_category(entities, solar)
    main.index.delete_entity(entities[0])

    assert main.index.list_entity_categories()[0]["entity_count"] == 2


def test_reorder_ignores_unknown_ids_and_keeps_unlisted_at_the_end(client, entities) -> None:
    from app import main

    a = main.index.create_entity_category("A")
    b = main.index.create_entity_category("B")
    c = main.index.create_entity_category("C")

    main.index.reorder_entity_categories([c, 9999, c, a])

    listed = main.index.list_entity_categories()
    assert [x["id"] for x in listed] == [c, a, b]
    assert [x["position"] for x in listed] == [1, 2, 3]


def test_list_sorts_by_category_order_with_uncategorized_last(client, entities) -> None:
    from app import main

    first = main.index.create_entity_category("Zuerst")
    second = main.index.create_entity_category("Danach")
    main.index.set_entities_category([entities[2]], first)
    main.index.set_entities_category([entities[0]], second)

    ascending = [r["entity_id"] for r in main.index.list_entities(search="sensor.cat_", sort="category")]
    descending = [r["entity_id"] for r in main.index.list_entities(search="sensor.cat_", sort="category", direction="desc")]

    assert ascending == [entities[2], entities[0], entities[1]]
    assert descending == [entities[1], entities[0], entities[2]]


def test_routes_manage_categories_and_assignment(client, entities) -> None:
    from app import main

    created = client.post("/entities/categories", json={"name": "Solar"})
    assert created.status_code == 200
    category_id = created.json()["id"]
    assert client.post("/entities/categories", json={"name": "solar"}).status_code == 409
    assert client.post("/entities/categories", json={"name": "x" * 41}).status_code == 400

    assert client.post(f"/entities/categories/{category_id}/rename", json={"name": "PV"}).status_code == 200
    assert client.post(f"/entities/categories/{category_id}/color", json={"slot": 5}).status_code == 200
    assert client.post(f"/entities/categories/{category_id}/color", json={"slot": 9}).status_code == 400
    assert client.post("/entities/categories/9999/rename", json={"name": "Nichts"}).status_code == 404
    assert main.index.list_entity_categories()[0]["name"] == "PV"
    assert main.index.list_entity_categories()[0]["color_slot"] == 5

    assigned = client.post("/entities/set-category", data={"entity_id": entities[:2], "category_id": str(category_id)})
    assert assigned.json() == {"changed": 2}
    assert client.post("/entities/set-category", data={"category_id": str(category_id)}).status_code == 400
    assert client.post("/entities/set-category", data={"entity_id": entities, "category_id": "9999"}).status_code == 404
    released = client.post("/entities/set-category", data={"entity_id": entities[:1], "category_id": ""})
    assert released.json() == {"changed": 1}

    assert client.post(f"/entities/categories/{category_id}/delete").json() == {"released": 1}
    assert client.post(f"/entities/categories/{category_id}/delete").status_code == 404


def test_suggestions_route_and_page(client, entities) -> None:
    empty = client.get("/entities/categories").text
    assert "Noch keine Kategorien" in empty and 'id="cat-suggest"' in empty

    response = client.post("/entities/categories/suggestions", json={"names": ["Solar", "Wetter"]})
    assert len(response.json()["ids"]) == 2
    filled = client.get("/entities/categories").text
    assert "Noch keine Kategorien" not in filled and 'data-action="rename"' in filled


def test_category_column_shows_up_with_the_first_category_only(client, entities) -> None:
    from app import main

    assert "Kategorie" not in client.get("/entities-table").text
    assert "category" not in main._entities_visible_columns()

    category_id = client.post("/entities/categories", json={"name": "Wärmepumpe"}).json()["id"]
    client.post("/entities/set-category", data={"entity_id": entities[0], "category_id": str(category_id)})

    html = client.get("/entities-table", params={"search": "sensor.cat_", "sort": "category"}).text
    assert 'class="cat-pill"' in html and "Wärmepumpe" in html and 'col-category' in html
    assert html.index("sensor.cat_a") < html.index("sensor.cat_b")


def test_saved_column_choice_takes_the_category_column_with_the_first_category(client, entities) -> None:
    from app import main

    main.index.set_setting("entities_columns", "type,unit")
    client.post("/entities/categories", json={"name": "Solar"})
    assert "category" in main._entities_visible_columns()

    main.index.set_setting("entities_columns", "type,unit")  # Nutzer blendet sie wieder aus
    assert "category" not in main._entities_visible_columns()


def test_config_page_offers_the_category(client, entities) -> None:
    category_id = client.post("/entities/categories", json={"name": "Wetter"}).json()["id"]
    client.post("/entities/set-category", data={"entity_id": entities[0], "category_id": str(category_id)})

    html = client.get(f"/entities/{entities[0]}/config").text
    assert 'id="category-btn"' in html and "Wetter" in html


def test_selection_bar_and_scripts_are_wired() -> None:
    template = (APP / "templates/entities_list.html").read_text(encoding="utf-8")
    script = (APP / "static/js/pages/entities_list.js").read_text(encoding="utf-8")
    assert 'id="sel-category-popover"' in template
    assert "entities/set-category" in script


# ---- Phase 2: Filter ---------------------------------------------------------------------

def test_list_filters_by_category_and_by_none(client, entities) -> None:
    from app import main

    solar = main.index.create_entity_category("Solar")
    wetter = main.index.create_entity_category("Wetter")
    main.index.set_entities_category([entities[0]], solar)
    main.index.set_entities_category([entities[1]], wetter)

    def found(*selected: str) -> list[str]:
        return [r["entity_id"] for r in main.index.list_entities(search="sensor.cat_", category_filter=list(selected))]

    assert found(str(solar)) == [entities[0]]
    assert found(str(solar), str(wetter)) == [entities[0], entities[1]]
    assert found("none") == [entities[2]]
    assert found(str(wetter), "none") == [entities[1], entities[2]]
    assert found("all") == entities  # kein Filter
    assert main.index.count_entities(search="sensor.cat_", category_filter=[str(solar), "none"]) == 2


def test_table_route_applies_the_category_filter_and_the_page_offers_it(client, entities) -> None:
    solar = client.post("/entities/categories", json={"name": "Solar"}).json()["id"]
    client.post("/entities/set-category", data={"entity_id": entities[0], "category_id": str(solar)})

    html = client.get("/entities-table", params={"search": "sensor.cat_", "category": [str(solar)]}).text
    assert "sensor.cat_a" in html and "sensor.cat_b" not in html

    page = client.get("/entities").text
    assert 'id="category-filter-popover"' in page and 'value="none"' in page


def test_filter_is_hidden_without_categories(client, entities) -> None:
    assert 'id="category-filter-popover"' not in client.get("/entities").text


# ---- Phase 2: Auswahl-Assistent ----------------------------------------------------------

@pytest.fixture()
def assistant_catalog():
    import os
    from pathlib import Path

    from app import selection_catalog, selection_jobs

    data_dir = Path(os.environ["ZEITARCHIV_DATA_DIR"])
    for name in (selection_catalog.FILENAME, selection_jobs.FILENAME):
        (data_dir / name).unlink(missing_ok=True)
    selection_catalog.save(
        data_dir,
        [
            {"entity_id": "weather.cat_home", "name": "Cat Wetter", "text": True},
            {"entity_id": "sensor.cat_aussen", "name": "Cat Aussentemperatur", "device_class": "temperature",
             "state_class": "measurement", "unit": "°C"},
            {"entity_id": "sensor.cat_regen", "name": "Cat Regen", "device_class": "precipitation",
             "state_class": "total_increasing", "unit": "mm"},
        ],
        label={"id": "za", "name": "Zeitarchiv"}, log=[], ledger_count=0, entry_id="EINTRAG", phase1=True,
    )
    yield
    for name in (selection_catalog.FILENAME, selection_jobs.FILENAME):
        (data_dir / name).unlink(missing_ok=True)


def _apply_form(client, **extra):
    from app import main

    preview = client.post("/selection/apply", data={"topic": "wetter", "entity_id": "sensor.cat_aussen"})
    assert preview.status_code == 200
    return preview, main


def test_assistant_confirmation_offers_the_topic_as_a_new_category_only_when_categories_exist(client, entities, assistant_catalog) -> None:
    preview, main = _apply_form(client)
    # Noch keine Kategorien: angeboten, aber nicht vorgewählt, nichts wird aufgedrängt.
    assert 'value="new:Wetter">' in preview.text and 'value="" selected' in preview.text

    main.index.create_entity_category("Solar")
    preview = client.post("/selection/apply", data={"topic": "wetter", "entity_id": "sensor.cat_aussen"})
    assert 'value="new:Wetter" selected' in preview.text and "gibt es noch nicht" in preview.text

    main.index.create_entity_category("wetter")
    preview = client.post("/selection/apply", data={"topic": "wetter", "entity_id": "sensor.cat_aussen"})
    assert "new:Wetter" not in preview.text and " selected>wetter</option>" in preview.text


def test_assistant_apply_creates_the_category_and_assigns_before_the_entity_exists(client, entities, assistant_catalog) -> None:
    from app import main

    main.index.create_entity_category("Solar")
    form = {"topic": "wetter", "entity_id": "sensor.cat_aussen", "confirmed": "1", "category": "new:Wetter"}

    done = client.post("/selection/apply", data=form, follow_redirects=False)

    assert done.status_code == 303
    created = {c["name"]: c["id"] for c in main.index.list_entity_categories()}
    assert set(created) == {"Solar", "Wetter"}
    assert main.index.get_entity_category_id("sensor.cat_aussen") == created["Wetter"]  # Entität gibt es noch nicht
    main.index.delete_entity_category(created["Wetter"])


def test_assistant_keeps_existing_categories_unless_asked(client, entities, assistant_catalog) -> None:
    from app import main

    solar = main.index.create_entity_category("Solar")
    wetter = main.index.create_entity_category("Wetter")
    main.index.set_entities_category(["sensor.cat_aussen"], solar, only_known=False)

    form = {"topic": "wetter", "entity_id": "sensor.cat_aussen", "confirmed": "1", "category": f"id:{wetter}"}
    client.post("/selection/apply", data=form, follow_redirects=False)
    assert main.index.get_entity_category_id("sensor.cat_aussen") == solar

    from app import selection_jobs
    import os
    from pathlib import Path
    (Path(os.environ["ZEITARCHIV_DATA_DIR"]) / selection_jobs.FILENAME).unlink(missing_ok=True)
    client.post("/selection/apply", data={**form, "recategorize": "1"}, follow_redirects=False)
    assert main.index.get_entity_category_id("sensor.cat_aussen") == wetter


def test_assistant_rejects_an_unknown_category_without_creating_a_job(client, entities, assistant_catalog) -> None:
    from app import selection_jobs
    import os
    from pathlib import Path

    form = {"topic": "wetter", "entity_id": "sensor.cat_aussen", "confirmed": "1", "category": "id:9999"}
    response = client.post("/selection/apply", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert selection_jobs.all_jobs(Path(os.environ["ZEITARCHIV_DATA_DIR"])) == []


# ---- Phase 3: Export und Demo ------------------------------------------------------------

def test_export_wizard_filters_by_category(client, entities) -> None:
    solar = client.post("/entities/categories", json={"name": "Solar"}).json()["id"]
    client.post("/entities/set-category", data={"entity_id": entities[0], "category_id": str(solar)})

    assert 'id="export-category-filter-popover"' in client.get("/export").text
    table = client.get("/export-table", params={"search": "sensor.cat_", "category": [str(solar)]}).text
    assert "sensor.cat_a" in table and "sensor.cat_b" not in table
    ids = client.get("/export/entity-ids", params={"search": "sensor.cat_", "category": ["none"]}).json()["entity_ids"]
    assert ids == [entities[1], entities[2]]


def test_export_wizard_has_no_category_filter_without_categories(client, entities) -> None:
    assert 'id="export-category-filter-popover"' not in client.get("/export").text


def test_demo_entities_map_to_the_demo_categories() -> None:
    from app import demo_generation as demo

    expected = {
        "sensor.demo_pv_leistung": "Solar", "sensor.demo_balkonkraftwerk_pv_leistung": "Solar",
        "sensor.demo_heimspeicher_speicher_soc": "Batterie", "sensor.demo_balkonkraftwerk_entladeleistung": "Batterie",
        "sensor.demo_wallbox_energie": "E-Auto", "sensor.demo_stromzaehler_bezug": "Netz",
        "sensor.demo_waschmaschine_energie": "Verbraucher", "sensor.demo_aussentemperatur": "Wetter",
    }
    for entity_id, name in expected.items():
        assert demo.DEMO_CATEGORIES[demo.demo_category_index(entity_id)] == name, entity_id
    for entity_id in ("sensor.demo_heizung", "sensor.demo_wasserzaehler", "device_tracker.demo_smartphone"):
        assert demo.demo_category_index(entity_id) is None, entity_id


def test_demo_categories_keep_own_assignments_and_translate(client, entities) -> None:
    from app import demo_generation as demo
    from app import main

    def local(entity_id: str) -> str:
        return demo.localized_id(entity_id, "en")  # die Demo legt die Entitäten mit übersetzten IDs an

    for entity in demo.DEMO_ENTITIES:
        main.index.get_or_create_entity(local(entity.entity_id), entity.domain, entity.state_class, entity.unit)
    own = main.index.create_entity_category("Eigene")
    main.index.set_entities_category([local("sensor.demo_pv_leistung")], own)
    try:
        demo.assign_demo_categories(main.index, "en")
        names = {c["id"]: c["name"] for c in main.index.list_entity_categories()}
        assert {"Battery", "EV", "Grid", "Consumers", "Weather", "Solar"} <= set(names.values())
        assert main.index.get_entity_category_id(local("sensor.demo_pv_leistung")) == own
        assert names[main.index.get_entity_category_id(local("sensor.demo_aussentemperatur"))] == "Weather"
    finally:
        for entity in demo.DEMO_ENTITIES:
            main.index.delete_entity(local(entity.entity_id))


def test_category_column_follows_type_and_replaces_size_by_default(client, entities) -> None:
    from app import main

    client.post("/entities/categories", json={"name": "Solar"})
    columns = main._entities_visible_columns()
    assert "category" in columns and "size" not in columns

    html = client.get("/entities-table").text
    assert html.index("col-type") < html.index("col-category")
    assert html.index('class="col-type">') < html.index('class="col-category">')
