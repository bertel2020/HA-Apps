"""Lovelace-Karte: gespeicherte Charts über den Token-Port (Phase 1).

Lesepfade für die Zeitarchiv-Integration, die sie per WebSocket an die Karte in
Home Assistant weiterreicht:

``GET /api/card/items`` listet die Charts, die in der Karte wählbar sind.
``GET /api/card/chart?id=N`` liefert Einstellungen (``spec``) und Daten (``data``)
eines gespeicherten Charts.
``GET /api/card/entities`` listet die archivierten Entitäten, die die Karte ohne
gespeichertes Chart zeigen kann (Auswahl im Editor).
``GET /api/card/series?entity_ids=…&period=…&style=…`` baut dasselbe wie ``chart`` für
frei gewählte Entitäten, ohne dass ein Chart gespeichert sein muss.
``GET /api/card/table?id=N`` liefert Aufbau (``table``) und fertige Abfrage (``query``) einer
gespeicherten Vergleichstabelle; die Karte rechnet daraus mit table-compute.js dieselben Zellen wie
die Kachel.
``GET /api/card/spark?entity_id=…&range=…`` liefert eine kurze Reihe für die Sparkline unter
einer Kachelkarte (höchstens ``SPARK_MAX_POINTS`` Punkte). ``spec`` trägt dieselben Werte wie die
``data-*``-Attribute einer Dashboard-Kachel (``_dashboard_tiles.html``), nur als
Wörterbuch, damit die Karte ``ChartTileRender.readSpec`` unverändert nutzen kann.

Anders als ``/api/query-multi`` prüfen diese Routen das Token: der Port 8127 ist
von außen erreichbar, die Abfrage-Routen sind es bewusst nicht (nginx.conf). Die
Chart-ID ist ein Query-Parameter, damit nginx je Route ein exaktes ``location =``
behält. Die Abfrage selbst läuft über denselben Handler wie die Oberfläche
(``query_multi``), eine zweite Berechnung gibt es nicht.

Was die Karte nicht zeichnet, kommt mit ``supported: false`` und ohne Daten, sie zeigt
dafür einen Platzhalter.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Query, Request

from .chart_types import effective_chart_type
from .i18n import tr
from .storage import query as query_mod
from .storage.index import Index
from .storage.paths import validate_entity_id

#: Fassung des Vertrags zwischen App, Integration und Karte. Die Karte zeigt bei
#: einer unbekannten Fassung eine Meldung statt falsch zu zeichnen.
SPEC_VERSION = 1

#: Darstellungen, die die Karte kann: alle, die ein gespeichertes Chart hat. "bar" ist die
#: ausdrückliche Wahl "kein Zeitstrahl" eines Charts aus Zahlen.
SUPPORTED_CHART_TYPES = ("auto", "bar", "timeline", "donut", "shares", "changes", "heatmap")

#: Zeiträume der Karte ohne gespeichertes Chart: Schlüssel -> (Zeitraum, fortlaufend).
#: "day" ist der heutige Tag, "24h" die letzten 24 Stunden (so wie im Chart-Editor).
PERIODS = {
    "day": ("day", False), "week": ("week", False), "month": ("month", False), "year": ("year", False),
    "24h": ("day", True), "7d": ("week", True), "30d": ("month", True),
}

#: Darstellung ohne gespeichertes Chart: "auto" je Entität nach ihrer Art, "stacked"
#: gestapelte Balken, "percent" gestapelt als Anteile in Prozent.
STYLES = ("auto", "stacked", "percent")

#: Entitäten je Karte ohne gespeichertes Chart (wie im Chart-Editor, MAX_MULTI_QUERY_ENTITIES).
MAX_SERIES_ENTITIES = 10

#: Zeiträume der Sparkline (fortlaufend, wie im Chart-Editor) und die Höchstzahl ihrer Punkte.
SPARK_RANGES = {"24h": ("day", True), "7d": ("week", True), "30d": ("month", True)}
SPARK_MAX_POINTS = 60

#: Arten, die die Sparkline nicht zeichnet (sie zeigt nur Zahlenverläufe).
SPARK_UNSUPPORTED_TYPES = ("state", "switch")

#: Breite der Zeichenfläche in Bildpunkten, für die Spalten des Zeitstrahls in Jahr und Dekade.
MAX_PX = 4000

#: Gleichzeitige Abfragen der Karte. Viele Karten auf einem Dashboard fragen
#: beim Laden gemeinsam an; ohne Grenze hielten sie die Index-Sperren der
#: Oberfläche. Wer darüber liegt, bekommt 429 und fragt beim nächsten Takt neu.
MAX_CONCURRENT_QUERIES = 4


def _flag(value: object) -> str:
    return "true" if value else "false"


def chart_spec(chart: dict, chart_type: str) -> dict[str, str]:
    """Die Einstellungen eines Charts so, wie ``el.dataset`` sie für eine Kachel
    liefert (alles Zeichenketten, Listen und Wörterbücher als JSON).

    Nicht enthalten: Legende und Kachelgröße, die gibt es in der Karte nicht."""
    return {
        "entityIds": json.dumps(chart["entity_ids"]),
        "entityNames": json.dumps(chart["entity_names"]),
        "hiddenEntityIds": json.dumps(chart["hidden_entity_ids"]),
        "seriesColors": json.dumps(chart["series_colors"]),
        "heatmapStates": json.dumps(chart["heatmap_states"]),
        "compareSettings": json.dumps(chart["compare_settings"]) if chart["compare_settings"] else "",
        "periodAnchor": chart["period_anchor"] or "",
        "range": chart["range_key"],
        "continuous": _flag(chart["continuous"]),
        "resolutionPreset": chart["resolution_preset"],
        "dynamicYAxis": _flag(chart["dynamic_y_axis"]),
        "chartType": chart_type,
        "donutAggregation": chart["donut_aggregation"],
        "showValues": _flag(chart["show_values"]),
        "decimals": str(chart["decimals"]),
        "averageLine": _flag(chart["average_line"]),
        "averageStyle": chart["average_style"],
        "areaFill": _flag(chart["area_fill"]),
        "showPoints": _flag(chart["show_points"]),
        "stacked": _flag(chart["stacked"]),
        "normalize": _flag(chart["normalize"]),
        "horizontal": _flag(chart["horizontal"]),
        "animation": "false",
    }


#: Darstellungen, in denen ein Vergleich nicht gilt (wie tileCompare() in chart-tile-render.js).
NO_COMPARE_TYPES = ("timeline", "donut", "shares", "changes", "heatmap")
COMPARE_YEAR_RANGES = ("hour", "day", "week", "month")


def tile_compare(chart: dict, chart_type: str, entity_ids: list[str]) -> dict | None:
    """Welcher Vergleich für dieses Chart gilt, oder None: dieselbe Regel wie ``tileCompare()`` in
    chart-tile-render.js (ein Test hält beide gleich). ``entity_ids`` sind die sichtbaren Entitäten."""
    settings = chart["compare_settings"] or {}
    if (
        not settings.get("compare") or chart_type in NO_COMPARE_TYPES or chart["resolution_preset"] == "full"
        or (chart["stacked"] and len(entity_ids) >= 2)
    ):
        return None
    mode = settings.get("mode") or "previous"
    range_key, continuous = chart["range_key"], chart["continuous"]
    longterm = mode == "longterm" and range_key == "year" and not continuous and len(entity_ids) == 1
    if mode == "longterm" and not longterm:
        mode = "previous"
    if mode == "year" and range_key not in COMPARE_YEAR_RANGES:
        mode = "previous"
    target = settings.get("target") if settings.get("target") in entity_ids else ""
    cumsum = bool(settings.get("cumsum"))
    years = settings.get("years")
    return {
        "mode": mode, "longterm": longterm, "target": target, "cumsum": cumsum,
        "years": 5 if years is None else years,
        "average": True if settings.get("average") is None else settings["average"],
        "yearsCompare": mode == "previous" and range_key == "year" and (cumsum or bool(target)),
    }


def is_supported(chart: dict, chart_type: str, aggregation_types: dict[str, str]) -> bool:
    """Ob die Karte dieses Chart zeichnet (aggregation_types bleibt für spätere Einschränkungen)."""
    return chart_type in SUPPORTED_CHART_TYPES and bool(chart["entity_ids"])


def ad_hoc_chart(entity_ids: list[str], period: str, style: str) -> dict:
    """Ein Chart nur für diese Abfrage, mit den Vorgaben eines neu angelegten gespeicherten
    Charts. So laufen Spec und Abfrage durch dieselben Funktionen wie bei gespeicherten."""
    range_key, continuous = PERIODS[period]
    return {
        "id": None, "name": None, "entity_ids": entity_ids, "entity_names": {}, "hidden_entity_ids": [],
        "series_colors": {}, "heatmap_states": {}, "compare_settings": {}, "period_anchor": None,
        "range_key": range_key, "continuous": continuous, "resolution_preset": "auto", "dynamic_y_axis": True,
        "chart_type": "auto", "donut_aggregation": "sum", "show_values": False, "decimals": "auto",
        "average_line": False, "average_style": "flat", "area_fill": True, "show_points": False,
        "stacked": style in ("stacked", "percent"), "normalize": style == "percent", "horizontal": False,
    }


def spark_points(points: list[dict], aggregation_type: str, limit: int = SPARK_MAX_POINTS) -> list[list[float]]:
    """Höchstens ``limit`` Punkte [Zeitstempel, Wert]: aufeinanderfolgende Punkte werden zu einem
    zusammengefasst (Zähler als Summe, sonst Mittelwert), der Zeitstempel ist der des ersten."""
    values = [(p["ts"], p["value"]) for p in points if isinstance(p.get("value"), (int, float))]
    if len(values) <= limit:
        return [[ts, value] for ts, value in values]
    size = -(-len(values) // limit)  # aufgerundet
    out = []
    for start in range(0, len(values), size):
        chunk = values[start:start + size]
        total = sum(value for _, value in chunk)
        out.append([chunk[0][0], total if aggregation_type == "counter" else total / len(chunk)])
    return out


def table_query_body(columns: list[dict], rows: list[dict], at: str | None = None) -> dict | None:
    """Der Körper der Abfrage an /api/query-table für eine Tabelle: dieselbe Regel wie ``queryBody()``
    in table-compute.js (ein Test hält beide gleich). ``columns`` sind die sichtbaren Spalten; ohne
    Entität-/Gruppen-Zeile gibt es nichts zu fragen (None)."""
    entity_ids = list(dict.fromkeys(
        entity_id for row in rows if row["row_type"] in ("entity", "group") for entity_id in row["entity_ids"]
    ))
    if not entity_ids:
        return None

    def base_column(other: dict) -> bool:
        return not other.get("year_over_year") and (other.get("offset") or 0) == 0

    def column(col: dict) -> dict:
        offset = col.get("offset") or 0
        same = not at and offset < 0 and not col.get("year_over_year") and any(
            other["range_key"] == col["range_key"] and base_column(other) for other in columns
        )
        return {
            "range_key": col["range_key"], "offset": offset, "year_over_year": bool(col.get("year_over_year")),
            "same_elapsed": same,
        }

    return {"entity_ids": entity_ids, "at": at, "columns": [column(col) for col in columns]}


def create_card_router(
    index: Index,
    time_zone: str,
    check_auth: Callable[..., None],
    query_multi: Callable[..., dict],
    app_link: Callable[[], str | None] = lambda: None,
    query_table: Callable[[dict], dict] | None = None,
) -> APIRouter:
    router = APIRouter()
    slots = threading.BoundedSemaphore(MAX_CONCURRENT_QUERIES)

    def aggregation_types() -> dict[str, str]:
        return {row["entity_id"]: row["aggregation_type"] for row in index.list_entities()}

    def run_queries(chart: dict, chart_type: str, visible: list[str], at: str | None, px: int) -> dict:
        """Die Abfragen, die eine Kachel für dieses Chart stellt (fetchChartData() in
        chart-tile-render.js): die Reihen, bei der Heatmap zusätzlich die Zellen, beim Zeitstrahl in
        Jahr und Dekade zusätzlich die Spalten in der Breite der Zeichenfläche."""
        compare = tile_compare(chart, chart_type, visible)
        range_key = chart["range_key"]
        base = {
            "entity_ids": ",".join(visible), "range": range_key, "offset": 0, "continuous": chart["continuous"],
            "year_over_year": False,
            # Ein fester Zeitraum bleibt fest, der Stichtag gilt nur für die übrigen.
            "at": chart["period_anchor"] or at,
        }
        timeline = chart_type == "timeline"
        data = query_multi(
            **base, compare=compare is not None, compare_mode=compare["mode"] if compare else "previous",
            raw=timeline, previous_years=compare["years"] if compare and compare["longterm"] else 1,
            heatmap=False, columns=False, px=0,
        )
        plain = {"compare": False, "compare_mode": "previous", "raw": False, "previous_years": 1}
        if chart_type == "heatmap" and range_key in query_mod.STATE_HEATMAP_RANGES:
            heat = query_multi(**base, **plain, heatmap=True, columns=False, px=0)
            cells = {item["entity_id"]: item.get("cells") for item in heat.get("series", [])}
            for entry in data.get("series", []):
                if cells.get(entry["entity_id"]):
                    entry["cells"] = cells[entry["entity_id"]]
        if timeline and range_key in query_mod.STATE_COLUMNS_RANGES:
            columns = query_multi(**{**base}, compare=False, compare_mode="previous", raw=False, previous_years=1,
                                  heatmap=False, columns=True, px=max(60, px - 10))
            by_id = {item["entity_id"]: item for item in columns.get("series", []) if item.get("columns")}
            for entry in data.get("series", []):
                if entry["entity_id"] in by_id:
                    entry["columns"] = by_id[entry["entity_id"]]["columns"]
                    entry["dt"] = by_id[entry["entity_id"]].get("dt")
        return data

    def answer(chart: dict, at: str | None, px: int = 0) -> dict:
        """Antwort für ein gespeichertes oder ad hoc gebautes Chart: Spec immer, Daten nur für
        Darstellbares."""
        types = aggregation_types()
        chart_type = effective_chart_type(chart, types)
        supported = is_supported(chart, chart_type, types)
        result = {
            "spec_version": SPEC_VERSION,
            "id": chart["id"],
            "name": chart["name"],
            "chart_type": chart_type,
            "supported": supported,
            "spec": chart_spec(chart, chart_type),
            "env": {"timezone": time_zone},
            # Pfad der Zeitarchiv-Seite in Home Assistant, für den Link der Karte; None ohne Supervisor.
            "app_link": app_link(),
            "data": None,
        }
        visible = [entity_id for entity_id in chart["entity_ids"] if entity_id not in chart["hidden_entity_ids"]]
        if not supported or not visible:
            return result
        if not slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail=tr("Zu viele gleichzeitige Abfragen"))
        try:
            result["data"] = run_queries(chart, chart_type, visible, at, px)
        finally:
            slots.release()
        return result

    @router.get("/api/card/items")
    def card_items(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Gespeicherte Charts für die Auswahl im Karten-Editor."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        types = aggregation_types()
        items = []
        for chart in index.list_saved_charts():
            chart_type = effective_chart_type(chart, types)
            items.append({
                "id": chart["id"],
                "name": chart["name"],
                "kind": "chart",
                "chart_type": chart_type,
                "supported": is_supported(chart, chart_type, types),
            })
        tables = [
            {"id": row["id"], "name": row["name"], "kind": "table", "column_count": row["column_count"], "row_count": row["row_count"]}
            for row in index.list_saved_tables()
        ]
        return {"spec_version": SPEC_VERSION, "items": items, "tables": tables, "app_link": app_link()}

    @router.get("/api/card/chart")
    def card_chart(
        request: Request,
        id: int,
        at: str | None = None,
        px: int = Query(0, ge=0, le=MAX_PX),
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Einstellungen und Daten eines gespeicherten Charts.

        ``at``: Stichtag (JJJJ-MM-TT) einer Zeitraum-Navigation, gilt wie bei den
        Kacheln nur für Charts ohne festen Zeitraum."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        chart = index.get_saved_chart(id)
        if chart is None:
            raise HTTPException(status_code=404, detail=tr("Chart nicht gefunden"))
        return answer(chart, at, px)

    @router.get("/api/card/table")
    def card_table(
        request: Request,
        id: int,
        at: str | None = None,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Aufbau und fertige Abfrage einer gespeicherten Vergleichstabelle."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        table = index.get_saved_table(id)
        if table is None:
            raise HTTPException(status_code=404, detail=tr("Tabelle nicht gefunden"))
        result = {
            "spec_version": SPEC_VERSION, "id": table["id"], "name": table["name"],
            "table": {"columns": table["columns"], "rows": table["rows"], "style": table["style"]},
            "env": {"timezone": time_zone}, "app_link": app_link(), "query": {"columns": []},
        }
        visible = [col for col in table["columns"] if not col.get("hidden")]
        body = table_query_body(visible, table["rows"], at)
        if body is None or query_table is None:
            return result
        if not slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail=tr("Zu viele gleichzeitige Abfragen"))
        try:
            result["query"] = query_table(body)
        finally:
            slots.release()
        return result

    @router.get("/api/card/entities")
    def card_entities(
        request: Request,
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Archivierte Entitäten, die die Karte ohne gespeichertes Chart zeigen kann."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        entities = [
            {"entity_id": row["entity_id"], "aggregation_type": row["aggregation_type"]}
            for row in index.list_entities()
        ]
        return {"spec_version": SPEC_VERSION, "entities": entities}

    @router.get("/api/card/spark")
    def card_spark(
        request: Request,
        entity_id: str,
        range: str = "24h",  # noqa: A002 - Name des Query-Parameters
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Kurze Reihe einer Entität für die Sparkline unter einer Kachelkarte."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        try:
            validate_entity_id(entity_id)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from None
        if range not in SPARK_RANGES:
            raise HTTPException(status_code=400, detail=tr("Ungültiger Zeitraum"))
        entity = index.get_entity(entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail=tr("Entität nicht gefunden"))
        result = {
            "spec_version": SPEC_VERSION, "entity_id": entity_id, "range": range,
            "supported": entity["aggregation_type"] not in SPARK_UNSUPPORTED_TYPES, "unit": entity["unit"],
            "aggregation_type": entity["aggregation_type"], "points": [], "app_link": app_link(),
        }
        if not result["supported"]:
            return result
        if not slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail=tr("Zu viele gleichzeitige Abfragen"))
        try:
            range_key, continuous = SPARK_RANGES[range]
            data = query_multi(
                entity_ids=entity_id, range=range_key, offset=0, continuous=continuous, year_over_year=False,
                compare=False, compare_mode="previous", raw=False, previous_years=1, at=None,
                heatmap=False, columns=False, px=0,
            )
        finally:
            slots.release()
        series = (data.get("series") or [{}])[0]
        result["points"] = spark_points(series.get("points") or [], entity["aggregation_type"])
        result["decimals"] = series.get("decimals")
        return result

    @router.get("/api/card/series")
    def card_series(
        request: Request,
        entity_ids: str,
        period: str = "month",
        style: str = "auto",
        at: str | None = None,
        px: int = Query(0, ge=0, le=MAX_PX),
        authorization: str | None = Header(default=None),
        x_zeitarchiv_integration_version: str | None = Header(default=None),
    ) -> dict:
        """Einstellungen und Daten für frei gewählte Entitäten (die Karte ohne gespeichertes Chart)."""
        check_auth(authorization, request.headers.get("x-request-id", "-"), x_zeitarchiv_integration_version)
        ids = list(dict.fromkeys(item.strip() for item in entity_ids.split(",") if item.strip()))
        if not ids or len(ids) > MAX_SERIES_ENTITIES:
            raise HTTPException(
                status_code=400,
                detail=tr("Maximal {MAX_MULTI_QUERY_ENTITIES} Entitäten pro Abfrage", MAX_MULTI_QUERY_ENTITIES=MAX_SERIES_ENTITIES),
            )
        for entity_id in ids:
            try:
                validate_entity_id(entity_id)
            except ValueError as err:
                raise HTTPException(status_code=400, detail=str(err)) from None
            if index.get_entity(entity_id) is None:
                raise HTTPException(status_code=404, detail=tr("Entität nicht gefunden"))
        if period not in PERIODS:
            raise HTTPException(status_code=400, detail=tr("Ungültiger Zeitraum"))
        if style not in STYLES:
            raise HTTPException(status_code=400, detail=tr("Ungültige Darstellung"))
        return answer(ad_hoc_chart(ids, period, style), at, px)

    return router
