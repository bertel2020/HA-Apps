"""Lovelace-Karte: /api/card/items und /api/card/chart (Token-Port)."""

from __future__ import annotations

import json
import re
import threading

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from _paths import APP, template_text
from app.api_routes import API_CAPABILITIES
from app.card_routes import MAX_CONCURRENT_QUERIES, MAX_SERIES_ENTITIES, PERIODS, SPARK_MAX_POINTS, SPARK_RANGES, SPEC_VERSION, STYLES, ad_hoc_chart, chart_spec, create_card_router, spark_points, table_query_body, tile_compare


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

    tables: dict = {}

    def list_saved_tables(self) -> list[dict]:
        return [{"id": t["id"], "name": t["name"], "column_count": len(t["columns"]), "row_count": len(t["rows"])} for t in self.tables.values()]

    def get_saved_table(self, table_id: int):
        return self.tables.get(table_id)

    def get_entity(self, entity_id: str):
        if entity_id not in self.types:
            return None
        return {"entity_id": entity_id, "aggregation_type": self.types[entity_id], "unit": "kWh"}


TYPES = {"sensor.a": "standard", "sensor.b": "counter", "switch.c": "switch", "select.d": "state"}


def _client(charts: list[dict], query_multi=None, app_link=lambda: None, tables=None, query_table=None) -> tuple[TestClient, list[dict]]:
    calls: list[dict] = []

    def default_query(**kwargs) -> dict:
        calls.append(kwargs)
        return {"series": [], "window_start": 1, "window_end": 2}

    def check_auth(authorization, request_id="-", integration_version=None) -> None:
        if authorization != "Bearer geheim":
            raise HTTPException(status_code=401, detail="nein")

    app = FastAPI()
    index = _FakeIndex(charts, TYPES)
    index.tables = {t["id"]: t for t in (tables or [])}
    app.include_router(create_card_router(index, "Europe/Berlin", check_auth, query_multi or default_query, app_link, query_table))
    return TestClient(app), calls


AUTH = {"Authorization": "Bearer geheim"}


def test_the_capabilities_are_announced() -> None:
    assert {"cards", "card_series", "card_spark"} <= set(API_CAPABILITIES)


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
    assert supported == {1: True, 2: True, 3: True, 4: True, 5: True, 6: False}
    assert body["items"][0] == {"id": 1, "name": "Chart 1", "kind": "chart", "chart_type": "auto", "supported": True}


def test_an_unknown_chart_is_a_404() -> None:
    client, _ = _client([_chart(1, ["sensor.a"])])
    assert client.get("/api/card/chart", params={"id": 99}, headers=AUTH).status_code == 404
    assert client.get("/api/card/chart", params={"id": "x"}, headers=AUTH).status_code == 422


def test_a_chart_without_entities_comes_without_data_and_without_a_query() -> None:
    client, calls = _client([_chart(2, []), _chart(3, ["sensor.a"], hidden_entity_ids=["sensor.a"])])
    for chart_id in (2, 3):
        body = client.get("/api/card/chart", params={"id": chart_id}, headers=AUTH).json()
        assert body["data"] is None
    assert client.get("/api/card/chart", params={"id": 2}, headers=AUTH).json()["supported"] is False
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
        "year_over_year": False, "compare": True, "compare_mode": "previous", "raw": False,
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
    assert json.loads(spec["compareSettings"]) == {"compare": True}
    assert chart_spec(_chart(2, ["sensor.a"], compare_settings={}), "auto")["compareSettings"] == ""
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

    # Eine gespeicherte Tabelle: Aufbau und die Antwort von /api/query-table für ihre Spalten.
    table_id = index.create_saved_table(
        "Karte-Tabelle",
        [{"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False},
         {"label": "Gestern", "range_key": "day", "offset": -1, "year_over_year": False}],
        [{"label": "Probe", "row_type": "entity", "entity_ids": ["sensor.card_probe"], "formula": "", "bold": False}],
    )
    tables = client.get("/api/card/items", headers=headers).json()["tables"]
    assert {"id": table_id, "name": "Karte-Tabelle", "kind": "table", "column_count": 2, "row_count": 1} in tables
    table = client.get("/api/card/table", params={"id": table_id}, headers=headers).json()
    assert [c["label"] for c in table["table"]["columns"]] == ["Heute", "Gestern"]
    assert len(table["query"]["columns"]) == 2
    assert table["query"]["columns"][0]["series"][0]["entity_id"] == "sensor.card_probe"


def test_the_app_reports_its_page_in_home_assistant() -> None:
    client, _ = _client([_chart(1, ["sensor.a"])], app_link=lambda: "/hassio/ingress/abc_zeitarchiv")
    assert client.get("/api/card/items", headers=AUTH).json()["app_link"] == "/hassio/ingress/abc_zeitarchiv"
    assert client.get("/api/card/chart", params={"id": 1}, headers=AUTH).json()["app_link"] == "/hassio/ingress/abc_zeitarchiv"
    # Ohne Supervisor gibt es keinen Link, das Feld bleibt trotzdem da.
    client, _ = _client([_chart(1, ["sensor.a"])])
    assert client.get("/api/card/items", headers=AUTH).json()["app_link"] is None


# --- Karte ohne gespeichertes Chart ----------------------------------------------------------


def test_the_entity_list_names_every_archived_entity() -> None:
    client, _ = _client([])
    assert client.get("/api/card/entities").status_code == 401
    entities = client.get("/api/card/entities", headers=AUTH).json()["entities"]
    assert {item["entity_id"] for item in entities} == {"sensor.a", "sensor.b", "switch.c", "select.d"}
    assert {"entity_id": "sensor.b", "aggregation_type": "counter"} in entities


def test_a_series_is_built_for_the_chosen_entities_and_period() -> None:
    client, calls = _client([])
    body = client.get(
        "/api/card/series", params={"entity_ids": "sensor.a,sensor.b,sensor.a", "period": "7d", "style": "percent"}, headers=AUTH,
    ).json()
    assert body["supported"] is True and body["id"] is None and body["name"] is None
    assert json.loads(body["spec"]["entityIds"]) == ["sensor.a", "sensor.b"]  # doppelte fallen weg
    assert (body["spec"]["range"], body["spec"]["continuous"]) == ("week", "true")
    assert (body["spec"]["stacked"], body["spec"]["normalize"]) == ("true", "true")
    assert calls[0]["entity_ids"] == "sensor.a,sensor.b" and calls[0]["range"] == "week" and calls[0]["continuous"] is True


def test_every_period_and_style_has_a_meaning() -> None:
    for period in PERIODS:
        for style in STYLES:
            chart = ad_hoc_chart(["sensor.a"], period, style)
            assert chart["range_key"] in {"day", "week", "month", "year"}
            assert chart["stacked"] is (style != "auto") and chart["normalize"] is (style == "percent")
    # Alles, was chart_spec liest, steht im Ad-hoc-Chart.
    assert set(chart_spec(ad_hoc_chart(["sensor.a"], "month", "auto"), "auto"))


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"entity_ids": ""}, 400),
        ({"entity_ids": ",,"}, 400),
        ({"entity_ids": ",".join(f"sensor.x{n}" for n in range(MAX_SERIES_ENTITIES + 1))}, 400),
        ({"entity_ids": "sensor.a/../b"}, 400),
        ({"entity_ids": "sensor.a", "period": "jahrzehnt"}, 400),
        ({"entity_ids": "sensor.a", "style": "bunt"}, 400),
        ({"entity_ids": "sensor.unbekannt"}, 404),
        ({}, 422),
    ],
)
def test_a_series_asks_for_known_entities_and_known_words_only(params: dict, status: int) -> None:
    client, calls = _client([])
    assert client.get("/api/card/series", params=params, headers=AUTH).status_code == status
    assert calls == []


def test_a_series_with_a_switch_is_drawn_like_any_other() -> None:
    client, calls = _client([])
    body = client.get("/api/card/series", params={"entity_ids": "sensor.a,switch.c"}, headers=AUTH).json()
    assert body["supported"] is True and body["data"] is not None
    assert len(calls) == 1


def test_the_new_routes_need_the_token() -> None:
    client, calls = _client([])
    assert client.get("/api/card/series", params={"entity_ids": "sensor.a"}).status_code == 401
    assert calls == []


# --- Sparkline ---------------------------------------------------------------------------------


def _points(count: int, start: int = 1000) -> list[dict]:
    return [{"ts": start + 60 * n, "value": float(n), "min": None, "max": None} for n in range(count)]


def test_a_short_series_is_kept_as_it_is() -> None:
    assert spark_points(_points(5), "standard") == [[1000 + 60 * n, float(n)] for n in range(5)]
    assert spark_points([{"ts": 1, "value": None}, {"ts": 2, "value": 3.0}], "standard") == [[2, 3.0]]


def test_a_long_series_is_reduced_to_the_limit() -> None:
    points = _points(500)
    reduced = spark_points(points, "standard")
    assert 0 < len(reduced) <= SPARK_MAX_POINTS
    assert reduced[0][0] == 1000                                   # Zeitstempel des ersten Punkts je Gruppe
    assert [ts for ts, _ in reduced] == sorted(ts for ts, _ in reduced)
    # Mittelwert für Messwerte, Summe für Zähler: die Summe aller Punkte bleibt erhalten.
    group = -(-500 // SPARK_MAX_POINTS)
    assert reduced[0][1] == sum(range(group)) / group
    assert sum(value for _, value in spark_points(points, "counter")) == sum(range(500))


def _spark_query(**kwargs) -> dict:
    _spark_query.calls.append(kwargs)
    return {"series": [{"entity_id": kwargs["entity_ids"], "decimals": 1, "points": _points(200)}]}


def _spark_client():
    _spark_query.calls = []
    client, _ = _client([], query_multi=_spark_query, app_link=lambda: "/hassio/ingress/x_zeitarchiv")
    return client, _spark_query.calls


def test_a_spark_asks_the_same_query_as_the_chart_for_one_entity() -> None:
    client, calls = _spark_client()
    body = client.get("/api/card/spark", params={"entity_id": "sensor.a", "range": "7d"}, headers=AUTH).json()
    assert body["supported"] is True and body["unit"] == "kWh" and body["decimals"] == 1
    assert body["app_link"] == "/hassio/ingress/x_zeitarchiv"
    assert 0 < len(body["points"]) <= SPARK_MAX_POINTS
    assert (calls[0]["entity_ids"], calls[0]["range"], calls[0]["continuous"]) == ("sensor.a", "week", True)
    assert {key: SPARK_RANGES[key] for key in SPARK_RANGES} == {"24h": ("day", True), "7d": ("week", True), "30d": ("month", True)}


def test_a_spark_of_a_switch_comes_without_points() -> None:
    client, calls = _spark_client()
    body = client.get("/api/card/spark", params={"entity_id": "switch.c"}, headers=AUTH).json()
    assert body["supported"] is False and body["points"] == []
    assert calls == []


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"entity_id": "sensor.a/../b"}, 400),
        ({"entity_id": "sensor.a,sensor.b"}, 400),
        ({"entity_id": "sensor.a", "range": "1y"}, 400),
        ({"entity_id": "sensor.unbekannt"}, 404),
        ({}, 422),
    ],
)
def test_a_spark_takes_one_known_entity_and_a_known_range(params: dict, status: int) -> None:
    client, calls = _spark_client()
    assert client.get("/api/card/spark", params=params, headers=AUTH).status_code == status
    assert calls == []


def test_a_spark_needs_the_token() -> None:
    client, calls = _spark_client()
    assert client.get("/api/card/spark", params={"entity_id": "sensor.a"}).status_code == 401
    assert calls == []


# --- Vergleich ---------------------------------------------------------------------------------


def _js_tile_compare(cases: list[dict]) -> list:
    """tileCompare() aus chart-tile-render.js, für viele Fälle in einem node-Lauf."""
    import subprocess

    source = (APP / "static/js/chart-tile-render.js").read_text(encoding="utf-8")
    start = source.index("const COMPARE_YEAR_RANGES")
    end = source.index("  // Durchschnitt der gezeichneten Werte")
    code = f"{source[start:end]}; const cases = JSON.parse(require('fs').readFileSync(0, 'utf8')); console.log(JSON.stringify(cases.map(c => tileCompare(c))));"
    run = subprocess.run(["node", "-e", code], input=json.dumps(cases), check=True, capture_output=True, text=True)
    return json.loads(run.stdout)


def test_the_python_compare_rule_equals_the_one_of_the_renderer() -> None:
    """Server und Karte müssen denselben Vergleich meinen: Der Server fragt ihn ab, die Karte
    zeichnet ihn. Beide Regeln laufen hier über dieselbe Fälle-Matrix."""
    import itertools

    cases, charts = [], []
    ids_options = (["sensor.a"], ["sensor.a", "sensor.b"])
    settings_options = (
        {}, {"compare": False},
        {"compare": True}, {"compare": True, "mode": "year"}, {"compare": True, "mode": "longterm"},
        {"compare": True, "mode": "longterm", "years": 0, "average": False},
        {"compare": True, "mode": "previous", "cumsum": True}, {"compare": True, "target": "sensor.a"},
        {"compare": True, "target": "sensor.fremd"}, {"compare": True, "mode": "previous", "target": "sensor.b", "cumsum": True},
    )
    for ids, settings, range_key, continuous, chart_type, preset, stacked in itertools.product(
        ids_options, settings_options, ("hour", "day", "week", "month", "year", "decade"), (False, True),
        ("auto", "bar", "donut", "timeline", "heatmap"), ("auto", "full"), (False, True),
    ):
        chart = _chart(1, ids, compare_settings=settings, range_key=range_key, continuous=continuous,
                       resolution_preset=preset, stacked=stacked)
        charts.append((chart, chart_type, ids))
        cases.append({
            "compareSettings": json.dumps(settings) if settings else "", "range": range_key, "continuous": continuous,
            "entityIds": ids, "chartType": chart_type, "resolutionPreset": preset, "stacked": stacked,
        })
    expected = _js_tile_compare(cases)
    assert len(expected) == len(charts) > 1000
    assert any(item is None for item in expected) and any(item and item["longterm"] for item in expected)
    assert any(item and item["yearsCompare"] for item in expected)
    for (chart, chart_type, ids), js in zip(charts, expected, strict=True):
        assert tile_compare(chart, chart_type, ids) == js, (chart["compare_settings"], chart["range_key"], chart_type)


def test_a_chart_with_a_comparison_asks_the_query_for_it() -> None:
    chart = _chart(1, ["sensor.a"], range_key="year", compare_settings={"compare": True, "mode": "longterm", "years": 3})
    client, calls = _client([chart])
    client.get("/api/card/chart", params={"id": 1}, headers=AUTH)
    assert (calls[0]["compare"], calls[0]["compare_mode"], calls[0]["previous_years"]) == (True, "longterm", 3)
    plain = _chart(2, ["sensor.a"], compare_settings={})
    client, calls = _client([plain])
    client.get("/api/card/chart", params={"id": 2}, headers=AUTH)
    assert (calls[0]["compare"], calls[0]["compare_mode"], calls[0]["previous_years"]) == (False, "previous", 1)


# --- Zeitstrahl, Heatmap, Spalten ------------------------------------------------------------


class _Recorder:
    """query_multi, das je Art der Abfrage eine eigene Antwort gibt."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        ids = kwargs["entity_ids"].split(",")
        if kwargs["heatmap"]:
            return {"series": [{"entity_id": i, "cells": [[1, 2, 3]]} for i in ids]}
        if kwargs["columns"]:
            return {"series": [{"entity_id": i, "columns": [[1, {"a": 5}]], "dt": 3600} for i in ids]}
        return {"series": [{"entity_id": i, "points": [{"ts": 1, "value": 1}]} for i in ids]}


def _recorded(chart: dict, **params):
    recorder = _Recorder()
    client, _ = _client([chart], query_multi=recorder)
    body = client.get("/api/card/chart", params={"id": chart["id"], **params}, headers=AUTH).json()
    return body, recorder.calls


def test_a_timeline_asks_for_raw_values() -> None:
    body, calls = _recorded(_chart(1, ["switch.c"], range_key="day"))
    assert body["chart_type"] == "timeline" and len(calls) == 1 and calls[0]["raw"] is True


def test_a_timeline_over_a_year_also_asks_for_columns_as_wide_as_the_card() -> None:
    body, calls = _recorded(_chart(1, ["select.d"], chart_type="timeline", range_key="year"), px=500)
    assert [(c["raw"], c["columns"], c["px"]) for c in calls] == [(True, False, 0), (False, True, 490)]
    series = body["data"]["series"][0]
    assert series["columns"] == [[1, {"a": 5}]] and series["dt"] == 3600
    # Eine winzige Breite wird nicht schmaler als 60 Bildpunkte.
    _, calls = _recorded(_chart(1, ["select.d"], chart_type="timeline", range_key="decade"), px=10)
    assert calls[1]["px"] == 60


def test_a_heatmap_gets_its_cells_only_where_the_range_allows_it() -> None:
    body, calls = _recorded(_chart(1, ["select.d"], chart_type="heatmap", range_key="month"))
    assert [c["heatmap"] for c in calls] == [False, True]
    assert body["data"]["series"][0]["cells"] == [[1, 2, 3]]
    _, calls = _recorded(_chart(1, ["select.d"], chart_type="heatmap", range_key="week"))
    assert [c["heatmap"] for c in calls] == [False]


def test_a_plain_chart_needs_one_query() -> None:
    _, calls = _recorded(_chart(1, ["sensor.a"], range_key="year"))
    assert len(calls) == 1 and (calls[0]["raw"], calls[0]["heatmap"], calls[0]["columns"]) == (False, False, False)


def test_the_width_is_bounded() -> None:
    client, _ = _client([_chart(1, ["sensor.a"])])
    assert client.get("/api/card/chart", params={"id": 1, "px": 4001}, headers=AUTH).status_code == 422
    assert client.get("/api/card/chart", params={"id": 1, "px": -1}, headers=AUTH).status_code == 422


def test_the_ranges_of_the_extra_queries_equal_those_of_the_renderer() -> None:
    import re
    import subprocess

    from app.storage import query as query_mod

    source = (APP / "static/js/state-entities.js").read_text(encoding="utf-8")
    share = re.search(r"const SERVER_SHARE_RANGES = (\[[^\]]*\])", source).group(1)
    columns = re.search(r"const COLUMN_RANGES = (\[[^\]]*\])", source).group(1)
    run = subprocess.run(["node", "-e", f"console.log(JSON.stringify([{share}, {columns}]))"], check=True, capture_output=True, text=True)
    assert [tuple(item) for item in json.loads(run.stdout)] == [query_mod.STATE_HEATMAP_RANGES, query_mod.STATE_COLUMNS_RANGES]


# --- Tabellen ----------------------------------------------------------------------------------


def _table(table_id: int = 1, **over) -> dict:
    table = {
        "id": table_id, "name": f"Tabelle {table_id}", "style": {"zebra": True},
        "columns": [
            {"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False, "decimals": "auto", "hidden": False},
            {"label": "Gestern", "range_key": "day", "offset": -1, "year_over_year": False, "decimals": "auto", "hidden": False},
            {"label": "Verborgen", "range_key": "week", "offset": 0, "year_over_year": False, "decimals": "auto", "hidden": True},
        ],
        "rows": [
            {"label": "A", "row_type": "entity", "entity_ids": ["sensor.a"]},
            {"label": "Gruppe", "row_type": "group", "entity_ids": ["sensor.a", "sensor.b"]},
            {"label": "Formel", "row_type": "formula", "entity_ids": [], "formula": "A+B"},
            {"label": "", "row_type": "separator", "entity_ids": []},
        ],
    }
    table.update(over)
    return table


def _js_query_body(cases: list[dict]) -> list:
    import subprocess

    source = (APP / "static/js/table-compute.js").read_text(encoding="utf-8")
    start = source.index("  function queryBody(")
    end = source.index("  // Die Abfrage der Oberfläche")
    code = f"{source[start:end]}; const cases = JSON.parse(require('fs').readFileSync(0, 'utf8')); console.log(JSON.stringify(cases.map(c => queryBody(c.columns, c.rows, c.at))));"
    run = subprocess.run(["node", "-e", code], input=json.dumps(cases), check=True, capture_output=True, text=True)
    return json.loads(run.stdout)


def test_the_python_query_body_equals_the_one_of_table_compute() -> None:
    import itertools

    def column(range_key, offset, yoy):
        return {"label": "x", "range_key": range_key, "offset": offset, "year_over_year": yoy}

    column_sets = [
        [column("day", 0, False), column("day", -1, False)],
        [column("day", -1, False)],
        [column("month", 0, False), column("month", -1, False), column("month", -12, True), column("year", 0, False)],
        [column("week", 0, True), column("week", -1, False)],
        [column("day", 0, True), column("day", -1, False)],
        [],
    ]
    row_sets = [
        [{"row_type": "entity", "entity_ids": ["sensor.a"]}],
        [{"row_type": "group", "entity_ids": ["sensor.a", "sensor.b"]}, {"row_type": "entity", "entity_ids": ["sensor.b", "sensor.c"]}],
        [{"row_type": "formula", "entity_ids": []}, {"row_type": "separator", "entity_ids": []}],
        [{"row_type": "summary", "entity_ids": []}],
        [],
    ]
    cases = [{"columns": c, "rows": r, "at": at} for c, r, at in itertools.product(column_sets, row_sets, (None, "2026-10-01"))]
    expected = _js_query_body(cases)
    assert len(expected) == len(cases) and any(item is None for item in expected) and any(item for item in expected)
    assert any(c["same_elapsed"] for item in expected if item for c in item["columns"])
    for case, js in zip(cases, expected, strict=True):
        assert table_query_body(case["columns"], case["rows"], case["at"]) == js, case


def _table_client(tables, query_table=None):
    calls = []

    def fake(body: dict) -> dict:
        calls.append(body)
        return {"columns": [{"series": [], "window_start": 1} for _ in body["columns"]]}

    client, _ = _client([], tables=tables, query_table=query_table or fake)
    return client, calls


def test_items_list_the_saved_tables_apart_from_the_charts() -> None:
    client, _ = _table_client([_table(1), _table(2)])
    body = client.get("/api/card/items", headers=AUTH).json()
    assert body["items"] == []
    assert body["tables"] == [
        {"id": 1, "name": "Tabelle 1", "kind": "table", "column_count": 3, "row_count": 4},
        {"id": 2, "name": "Tabelle 2", "kind": "table", "column_count": 3, "row_count": 4},
    ]


def test_a_table_comes_with_its_structure_and_the_query_of_its_visible_columns() -> None:
    client, calls = _table_client([_table(1)])
    body = client.get("/api/card/table", params={"id": 1}, headers=AUTH).json()
    assert body["table"]["style"] == {"zebra": True} and len(body["table"]["columns"]) == 3 and len(body["table"]["rows"]) == 4
    # Die ausgeblendete Spalte wird nicht abgefragt (die Kachel fragt sie auch nicht), die Zeilen alle.
    assert [c["range_key"] for c in calls[0]["columns"]] == ["day", "day"]
    assert calls[0]["entity_ids"] == ["sensor.a", "sensor.b"]
    assert calls[0]["columns"][1]["same_elapsed"] is True
    assert len(body["query"]["columns"]) == 2 and body["env"] == {"timezone": "Europe/Berlin"}


def test_a_table_without_entities_asks_nothing() -> None:
    client, calls = _table_client([_table(1, rows=[{"label": "F", "row_type": "formula", "entity_ids": [], "formula": "1"}])])
    body = client.get("/api/card/table", params={"id": 1}, headers=AUTH).json()
    assert body["query"] == {"columns": []} and calls == []


def test_an_unknown_table_is_a_404_and_the_token_is_needed() -> None:
    client, calls = _table_client([_table(1)])
    assert client.get("/api/card/table", params={"id": 9}, headers=AUTH).status_code == 404
    assert client.get("/api/card/table", params={"id": 1}).status_code == 401
    assert client.get("/api/card/table", params={"id": "x"}, headers=AUTH).status_code == 422
    assert calls == []
