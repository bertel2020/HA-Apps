"""Mehrere Stapel je Chart (saved_charts.stack_groups).

Ohne Eintrag gilt wie bisher ein Stapel je Achse. Mit Stapeln bekommt jede Gruppe einen
eigenen stack-Schlüssel, Balken ohne Gruppe stehen einzeln daneben.
"""

from _paths import APP, template_text

from app.chart_types import stack_groups_clean

EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
EDITOR_TEMPLATE = template_text("chart_editor.html")
TILE_JS = (APP / "static/js/chart-tile-render.js").read_text(encoding="utf-8")
IDS = ["sensor.a", "sensor.b", "sensor.c"]


def test_clean_keeps_valid_groups_and_drops_the_rest() -> None:
    raw = {
        "groups": [
            {"id": "g1", "name": " Geräte "},
            {"id": "g2", "name": "leer"},
            {"id": "x", "name": "kaputt"},
            {"id": "g1", "name": "doppelt"},
        ],
        "members": {"sensor.a": "g1", "sensor.b": "g1", "sensor.fremd": "g1", "sensor.c": "g9"},
    }
    assert stack_groups_clean(raw, IDS) == {
        "groups": [{"id": "g1", "name": "Geräte"}],
        "members": {"sensor.a": "g1", "sensor.b": "g1"},
    }


def test_clean_returns_empty_without_a_usable_group() -> None:
    assert stack_groups_clean(None, IDS) == {}
    assert stack_groups_clean({"groups": [{"id": "g1", "name": "x"}], "members": {}}, IDS) == {}
    assert stack_groups_clean({"groups": "kaputt", "members": []}, IDS) == {}


def test_stack_groups_survive_save_open_and_duplicate(client) -> None:
    from app.main import index

    for eid in IDS:
        index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
    stack = {"groups": [{"id": "g1", "name": "Stapel 1"}], "members": {"sensor.a": "g1", "sensor.b": "g1"}}
    body = {"name": "Stapel-Probe", "entity_ids": IDS, "range_key": "day", "stacked": True, "stack_groups": stack}
    chart_id = client.post("/charts", json=body).json()["id"]
    assert index.get_saved_chart(chart_id)["stack_groups"] == stack
    assert '"g1"' in client.get(f"/charts/{chart_id}").text

    kopie = client.post(f"/charts/{chart_id}/duplicate").json()["id"]
    assert index.get_saved_chart(kopie)["stack_groups"] == stack

    client.post(f"/charts/{chart_id}", json={**body, "stack_groups": {}})
    assert index.get_saved_chart(chart_id)["stack_groups"] == {}


def test_renderers_use_one_stack_key_per_group() -> None:
    # Editor und Kachel müssen dieselbe Regel haben (Parität, siehe chart-tile-render.js).
    for source in (EDITOR_JS, TILE_JS):
        assert "'bar-' + axisKey(s) + '-' + gid" in source
        assert "stack: stackKeys[i] || undefined" in source
        assert "axisTotals.get(stackKeys[i])" in source


def test_editor_offers_the_stack_select_and_saves_it() -> None:
    assert "setStack(id, $event.target.value)" in EDITOR_TEMPLATE
    assert "stack_groups: this.stackGroups" in EDITOR_JS
    assert "const STACK_GROUPS" in EDITOR_TEMPLATE


def test_tile_dataset_carries_the_groups() -> None:
    assert "ds.stackGroups" in TILE_JS
    assert "data-stack-groups" in template_text("_dashboard_tiles.html")


def test_editor_has_stack_containers_with_drop_and_dissolve() -> None:
    for call in ("addStack()", "dissolveStack(g.id)", "stackDrop($event, g.id)", "renameStack(g.id, $event.target.value)"):
        assert call in EDITOR_TEMPLATE
    for fn in ("addStack()", "dissolveStack(gid)", "stackDrop(event, gid)", "stackMembersOf(gid)"):
        assert fn in EDITOR_JS
    # Die Reihenfolge der Liste bleibt die einzige Farbreihenfolge: Ziehen auf einen Stapel gibt die Zeile frei.
    assert "component.draggingEntityId = draggedId;" in EDITOR_JS


def test_empty_stacks_do_not_unstack_the_chart() -> None:
    # Ein frisch angelegter, leerer Stapel darf die Balken nicht plötzlich einzeln zeichnen.
    for source in (EDITOR_JS, TILE_JS):
        assert "const hasStackGroups = Object.keys(stackMembers).length > 0;" in source


def test_tooltips_sum_each_stack_in_both_renderers() -> None:
    for source in (EDITOR_JS, TILE_JS):
        assert "stackNameByKey" in source
        assert "const sumRows = " in source
        assert "e.n >= 2" in source


def test_legend_names_the_stack_in_editor_tile_and_card() -> None:
    legend = (APP / "static/js/legend-render.js").read_text(encoding="utf-8")
    assert "stackLabels" in TILE_JS and "stackLabels(series, spec)" in legend
    assert "stackTag(item)" in legend
    assert 'x-text="stat.stack"' in EDITOR_TEMPLATE
    assert "stackLabelFor(id)" in EDITOR_JS


def test_editor_explains_why_percent_does_not_apply() -> None:
    assert "x-show=\"stackPercentBlocked\"" in EDITOR_TEMPLATE
    assert "get stackPercentBlocked()" in EDITOR_JS


def test_full_resolution_gives_every_stack_its_own_category() -> None:
    # Bei „Voll“ teilen sich Mitglieder eines Stapels eine Kategorie (Stapelname), übrige Serien haben je eine.
    for source in (EDITOR_JS, TILE_JS):
        assert "const slotOf = [];" in source
        assert "data: slotLabels," in source
    assert "[slotOf[i], aggregate," in TILE_JS
    assert "singleBucket ? slotOf[i] : p.ts * 1000" in EDITOR_JS
    # Stapeln gilt bei „Voll“ nur mit eigenen Stapeln, sonst bleibt es der Ranking-Vergleich.
    assert "(!singleBucket || hasStackGroups)" in TILE_JS
    assert "(!this.singleBucket || this.hasStackMembers)" in EDITOR_JS


def test_assigning_an_entry_to_a_stack_switches_stacking_on() -> None:
    assert "if (value !== 'none' && !this.stacked && this.canStack && !this.compare) this.stacked = true;" in EDITOR_JS


def test_adding_a_stack_switches_stacking_on_and_dissolving_the_last_one_off() -> None:
    assert "if (!this.stacked && this.canStack && !this.compare) this.stacked = true;" in EDITOR_JS
    assert "if (!groups.length) this.stacked = false;" in EDITOR_JS


def test_dropping_on_a_stack_keeps_the_order_of_the_list() -> None:
    # Der Weg zu einem Stapel führt über andere Zeilen; deren Umsortieren beim Ziehen wird beim Ablegen zurückgenommen.
    assert "component.dragOrderSnapshot = [...component.selectedEntityIds];" in EDITOR_JS
    assert "this.selectedEntityIds = [...this.dragOrderSnapshot];" in EDITOR_JS


def test_chips_move_inside_a_stack_and_keep_their_colors() -> None:
    assert "moveInStack(mid, -1)" in EDITOR_TEMPLATE and "moveInStack(mid, 1)" in EDITOR_TEMPLATE
    assert "moveInStack(entityId, delta)" in EDITOR_JS
    # Die bisherige Palettenfarbe wird festgeschrieben, damit sich beim Tauschen keine Farbe ändert.
    assert "pinned[id] = 'palette:' + (this.colorIndexFor(id) % PALETTE.length);" in EDITOR_JS
