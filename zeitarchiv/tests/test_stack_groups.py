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
