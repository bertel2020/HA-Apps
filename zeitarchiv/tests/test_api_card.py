"""Lovelace-Karte: /api/card/items und /api/card/chart (Token-Port)."""

from __future__ import annotations

import json
import re
import threading

from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from _paths import APP, template_text
from app.api_routes import API_CAPABILITIES
from app.card_routes import MAX_CONCURRENT_QUERIES, SPEC_VERSION, chart_spec, create_card_router


def _chart(chart_id: int, entity_ids: list[str], **overrides) -> dict:
    chart = {
        "id": chart_id, "name": f"Chart {chart_id}", "entity_ids": entity_ids, "entity_names": {},
        "hidden_entity_ids": [], "series_colors": {}, "heatmap_states": {}, "period_anchor": None,
        "range_key": "month", "continuous": False, "resolution_preset": "auto", "dynamic_y_axis": True,
        "chart_type": "auto", "donut_aggregation": "sum", "show_values": False, "decimals": "auto",
        "average_line": False, "average_style": "flat", "area_fill": True, "show_points": False,
        "stacked": False, "normalize": False, "horizontal": False, "compare_settings": {"compare": True},
    }
    chart.update(overrides)
    return chart


class _FakeIndex:
    def __init__(self, charts: list[dict], types: dict[str, str]) -> None:
        self.charts = {chart["id"]: chart for chart in charts}
        self.types = types

    def list_entities(self) -> list[dict]:
        return [{"entity_id": entity_id, "aggregation_type": kind} for entity_id, kind in self.types.items()]

    def list_saved_charts(self) -> list[dict]:
        return list(self.charts.values())

    def get_saved_chart(self, chart_id: int) -> dict | None:
        return self.charts.get(chart_id)


TYPES = {"sensor.a": "standard", "sensor.b": "counter", "switch.c": "switch", "select.d": "state"}


def _client(charts: list[dict], query_multi=None) -> tuple[TestClient, list[dict]]:
    calls: list[dict] = []

    def default_query(**kwargs) -> dict:
        calls.append(kwargs)
        return {"series": [], "window_start": 1, "window_end": 2}

    def check_auth(authorization, request_id="-", integration_version=None) -> None:
        if authorization != "Bearer geheim":
            raise HTTPException(status_code=401, detail="nein")

    app = FastAPI()
    app.include_router(create_card_router(_FakeIndex(charts, TYPES), "Europe/Berlin", check_auth, query_multi or default_query))
    return TestClient(app), calls


AUTH = {"Authorization": "Bearer geheim"}


def test_the_capability_is_announced() -> None:
    assert "cards" in API_CAPABILITIES


def test_both_routes_need_the_token() -> None:
    client, calls = _client([_chart(1, ["sensor.a"])])
    assert client.get("/api/card/items").status_code == 401
    assert client.get("/api/card/chart", params={"id": 1}).status_code == 401
    assert client.get("/api/card/chart", params={"id": 1}, headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert calls == []


def test_items_mark_what_the_card_can_draw() -> None:
    client, _ = _client([
        _chart(1, ["sensor.a", "sensor.b"]),
        _chart(2, ["switch.c"]),
        _chart(3, ["sensor.a"], chart_type="donut"),
        _chart(4, ["select.d"], chart_type="heatmap"),
        _chart(5, ["sensor.a"], chart_type="bar"),
        _chart(6, []),
    ])
    body = client.get("/api/card/items", headers=AUTH).json()
    assert body["spec_version"] == SPEC_VERSION
    supported = {item["id"]: item["supported"] for item in body["items"]}
    assert supported == {1: True, 2: False, 3: False, 4: False, 5: True, 6: False}
    assert body["items"][0] == {"id": 1, "name": "Chart 1", "kind": "chart", "chart_type": "auto", "supported": True}


def test_an_unknown_chart_is_a_404() -> None:
    client, _ = _client([_chart(1, ["sensor.a"])])
    assert client.get("/api/card/chart", params={"id": 99}, headers=AUTH).status_code == 404
    assert client.get("/api/card/chart", params={"id": "x"}, headers=AUTH).status_code == 422


def test_an_unsupported_chart_comes_without_data_and_without_a_query() -> None:
    client, calls = _client([_chart(2, ["switch.c"]), _chart(3, ["sensor.a"], chart_type="donut")])
    for chart_id in (2, 3):
        body = client.get("/api/card/chart", params={"id": chart_id}, headers=AUTH).json()
        assert body["supported"] is False
        assert body["data"] is None
        assert body["spec"]["entityIds"]
    assert calls == []


def test_a_supported_chart_runs_the_same_query_as_the_tile() -> None:
    chart = _chart(1, ["sensor.a", "sensor.b", "sensor.hidden"], hidden_entity_ids=["sensor.hidden"], continuous=True)
    client, calls = _client([chart])
    body = client.get("/api/card/chart", params={"id": 1, "at": "2026-10-01"}, headers=AUTH).json()
    assert body["supported"] is True
    assert body["data"] == {"series": [], "window_start": 1, "window_end": 2}
    assert body["env"] == {"timezone": "Europe/Berlin"}
    assert calls == [{
        "entity_ids": "sensor.a,sensor.b", "range": "month", "offset": 0, "continuous": True,
        "year_over_year": False, "compare": False, "compare_mode": "previous", "raw": False,
        "previous_years": 1, "at": "2026-10-01", "heatmap": False, "columns": False, "px": 0,
    }]


def test_a_fixed_period_beats_the_requested_day() -> None:
    client, calls = _client([_chart(1, ["sensor.a"], period_anchor="2025-03-01")])
    client.get("/api/card/chart", params={"id": 1, "at": "2026-10-01"}, headers=AUTH)
    assert calls[0]["at"] == "2025-03-01"


def test_the_spec_is_what_a_tile_reads_from_its_dataset() -> None:
    spec = chart_spec(_chart(1, ["sensor.a"], entity_names={"sensor.a": "Strom"}, stacked=True), "auto")
    assert json.loads(spec["entityIds"]) == ["sensor.a"]
    assert json.loads(spec["entityNames"]) == {"sensor.a": "Strom"}
    assert spec["stacked"] == "true" and spec["normalize"] == "false"
    assert spec["decimals"] == "auto" and spec["animation"] == "false"
    # Der Vergleich kommt in dieser Fassung nicht mit, auch wenn das Chart ihn gespeichert hat.
    assert spec["compareSettings"] == ""
    assert all(isinstance(value, str) for value in spec.values())


def test_the_spec_covers_every_setting_the_renderer_reads() -> None:
    """readSpec() liest seine Einstellungen als ds.<name>. Fehlt hier ein Schlüssel,
    zeichnet die Karte mit dem Vorgabewert statt mit dem des Charts."""
    source = (APP / "static/js/chart-tile-render.js").read_text(encoding="utf-8")
    body = source.split("function readSpec(ds) {", 1)[1].split("\n  }\n", 1)[0]
    read = set(re.findall(r"\bds\.([A-Za-z]+)", body))
    assert read, "readSpec liest nichts mehr aus ds"
    assert read <= set(chart_spec(_chart(1, ["sensor.a"]), "auto")), sorted(read - set(chart_spec(_chart(1, ["sensor.a"]), "auto")))


def test_the_spec_covers_the_tile_attributes_except_legend_and_size() -> None:
    """Zieht eine Kachel ein neues data-Attribut für das Zeichnen dazu, muss die
    Karte es auch bekommen. Legende und Kachelgröße gibt es dort nicht."""
    template = template_text("_dashboard_tiles.html")
    block = template.split('data-item-type="chart"', 1)[1].split("</a>", 1)[0]
    attributes = set(re.findall(r"\bdata-([a-z-]+)=", block))
    camel = {re.sub(r"-([a-z])", lambda m: m.group(1).upper(), name) for name in attributes}
    not_in_card = {"itemType", "itemId", "gridCols", "gridRows", "showLegend", "chartStats", "legendMetrics", "legendStyle"}
    assert (camel - not_in_card) <= set(chart_spec(_chart(1, ["sensor.a"]), "auto")), sorted(
        camel - not_in_card - set(chart_spec(_chart(1, ["sensor.a"]), "auto"))
    )


def test_only_a_few_queries_run_at_the_same_time() -> None:
    release = threading.Event()
    running = threading.Semaphore(0)

    def slow_query(**_kwargs) -> dict:
        running.release()
        release.wait(5)
        return {"series": []}

    client, _ = _client([_chart(1, ["sensor.a"])], query_multi=slow_query)
    results: list[int] = []

    def ask() -> None:
        results.append(client.get("/api/card/chart", params={"id": 1}, headers=AUTH).status_code)

    threads = [threading.Thread(target=ask) for _ in range(MAX_CONCURRENT_QUERIES)]
    for thread in threads:
        thread.start()
    for _ in range(MAX_CONCURRENT_QUERIES):
        assert running.acquire(timeout=5)
    assert client.get("/api/card/chart", params={"id": 1}, headers=AUTH).status_code == 429
    release.set()
    for thread in threads:
        thread.join(5)
    assert sorted(results) == [200] * MAX_CONCURRENT_QUERIES
    # Nach dem Lauf sind die Plätze wieder frei.
    assert client.get("/api/card/chart", params={"id": 1}, headers=AUTH).status_code == 200


def test_the_error_text_has_an_english_entry() -> None:
    catalog = json.loads((APP / "i18n/en.json").read_text(encoding="utf-8"))["app"]
    assert "Zu viele gleichzeitige Abfragen" in catalog


def test_the_real_app_serves_a_saved_chart_with_its_token(client) -> None:
    """Durch die ganze App: Chart speichern, mit dem echten Token abfragen."""
    from app.main import _current_api_token, index

    index.get_or_create_entity("sensor.card_probe", "sensor", "measurement", "kW")
    chart_id = client.post(
        "/charts", json={"name": "Karte-Probe", "entity_ids": ["sensor.card_probe"], "range_key": "day"},
    ).json()["id"]
    # Kein Aufruf ohne Token hier: der Fehlschlag landet im ratenbegrenzten Log
    # (api_auth_failure) und ließe test_api_observability danach nichts mehr sehen.
    headers = {"Authorization": f"Bearer {_current_api_token()}"}
    items = client.get("/api/card/items", headers=headers).json()["items"]
    assert {"id": chart_id, "name": "Karte-Probe", "kind": "chart", "chart_type": "auto", "supported": True} in items
    body = client.get("/api/card/chart", params={"id": chart_id}, headers=headers).json()
    assert body["supported"] is True
    assert body["data"]["series"][0]["entity_id"] == "sensor.card_probe"
    assert json.loads(body["spec"]["entityIds"]) == ["sensor.card_probe"]
