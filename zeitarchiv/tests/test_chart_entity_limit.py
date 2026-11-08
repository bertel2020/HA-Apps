"""Ein Chart darf höchstens so viele Entitäten haben, wie die Abfrage /api/query-multi erlaubt.

Vorher ließ sich ein Chart mit 26 Entitäten speichern (HTTP 200), die Datenabfrage lehnte
dieselben IDs aber mit 413 ab: Das gespeicherte Chart war dauerhaft nicht darstellbar.
"""

from _paths import APP, template_text

from app.limits import MAX_MULTI_QUERY_ENTITIES

EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")


def _body(count: int, name: str = "Grenze") -> dict:
    return {
        "name": name,
        "entity_ids": [f"sensor.grenze_{i}" for i in range(count)],
        "range_key": "day",
    }


def test_saving_more_entities_than_the_query_allows_is_refused(client) -> None:
    resp = client.post("/charts", json=_body(MAX_MULTI_QUERY_ENTITIES + 1))
    assert resp.status_code == 400
    assert str(MAX_MULTI_QUERY_ENTITIES) in resp.json()["detail"]


def test_saving_exactly_the_limit_works(client) -> None:
    assert client.post("/charts", json=_body(MAX_MULTI_QUERY_ENTITIES, "Grenze genau")).status_code == 200


def test_updating_to_more_entities_than_allowed_is_refused(client) -> None:
    chart_id = client.post("/charts", json=_body(1, "Grenze Update")).json()["id"]
    resp = client.post(f"/charts/{chart_id}", json=_body(MAX_MULTI_QUERY_ENTITIES + 1, "Grenze Update"))
    assert resp.status_code == 400


def test_the_editor_gets_the_same_limit() -> None:
    assert "const MAX_ENTITIES = " in template_text("chart_editor.html")
    assert "this.selectedEntityIds.length >= MAX_ENTITIES" in EDITOR_JS
    assert "this.selectedEntityIds.length > MAX_ENTITIES" in EDITOR_JS
