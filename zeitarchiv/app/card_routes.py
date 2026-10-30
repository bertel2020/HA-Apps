"""Lovelace-Karte: gespeicherte Charts über den Token-Port (Phase 1).

Lesepfade für die Zeitarchiv-Integration, die sie per WebSocket an die Karte in
Home Assistant weiterreicht:

``GET /api/card/items`` listet die Charts, die in der Karte wählbar sind.
``GET /api/card/chart?id=N`` liefert Einstellungen (``spec``) und Daten (``data``)
eines gespeicherten Charts.
``GET /api/card/entities`` listet die archivierten Entitäten, die die Karte ohne
gespeichertes Chart zeigen kann (Auswahl im Editor).
``GET /api/card/series?entity_ids=…&period=…&style=…`` baut dasselbe wie ``chart`` für
frei gewählte Entitäten, ohne dass ein Chart gespeichert sein muss. ``spec`` trägt dieselben Werte wie die
``data-*``-Attribute einer Dashboard-Kachel (``_dashboard_tiles.html``), nur als
Wörterbuch, damit die Karte ``ChartTileRender.readSpec`` unverändert nutzen kann.

Anders als ``/api/query-multi`` prüfen diese Routen das Token: der Port 8127 ist
von außen erreichbar, die Abfrage-Routen sind es bewusst nicht (nginx.conf). Die
Chart-ID ist ein Query-Parameter, damit nginx je Route ein exaktes ``location =``
behält. Die Abfrage selbst läuft über denselben Handler wie die Oberfläche
(``query_multi``), eine zweite Berechnung gibt es nicht.

Der Umfang der ersten Version ist bewusst klein: Zahlen-Charts ohne Vergleich.
Zustände, Zeitstrahl, Donut, Anteile, Wechsel und Heatmap kommen mit
``supported: false`` und ohne Daten, die Karte zeigt dafür einen Platzhalter.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request

from .chart_types import effective_chart_type
from .i18n import tr
from .storage.index import Index
from .storage.paths import validate_entity_id

#: Fassung des Vertrags zwischen App, Integration und Karte. Die Karte zeigt bei
#: einer unbekannten Fassung eine Meldung statt falsch zu zeichnen.
SPEC_VERSION = 1

#: Darstellungen, die die Karte kann. "bar" ist die ausdrückliche Wahl "kein
#: Zeitstrahl" eines Charts aus Zahlen (chart_types.effective_chart_type).
SUPPORTED_CHART_TYPES = ("auto", "bar")

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

#: Arten, die die Karte nicht zeichnet.
UNSUPPORTED_TYPES = ("state", "switch")

#: Gleichzeitige Abfragen der Karte. Viele Karten auf einem Dashboard fragen
#: beim Laden gemeinsam an; ohne Grenze hielten sie die Index-Sperren der
#: Oberfläche. Wer darüber liegt, bekommt 429 und fragt beim nächsten Takt neu.
MAX_CONCURRENT_QUERIES = 4


def _flag(value: object) -> str:
    return "true" if value else "false"


def chart_spec(chart: dict, chart_type: str) -> dict[str, str]:
    """Die Einstellungen eines Charts so, wie ``el.dataset`` sie für eine Kachel
    liefert (alles Zeichenketten, Listen und Wörterbücher als JSON).

    Nicht enthalten: Legende und Kachelgröße, die gibt es in der Karte nicht.
    ``compareSettings`` ist leer, der Vergleich kommt nicht mit (siehe oben)."""
    return {
        "entityIds": json.dumps(chart["entity_ids"]),
        "entityNames": json.dumps(chart["entity_names"]),
        "hiddenEntityIds": json.dumps(chart["hidden_entity_ids"]),
        "seriesColors": json.dumps(chart["series_colors"]),
        "heatmapStates": json.dumps(chart["heatmap_states"]),
        "compareSettings": "",
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


def is_supported(chart: dict, chart_type: str, aggregation_types: dict[str, str]) -> bool:
    """Nur Zahlen-Charts in einer Darstellung, die die Karte zeichnet."""
    if chart_type not in SUPPORTED_CHART_TYPES or not chart["entity_ids"]:
        return False
    return all(aggregation_types.get(entity_id) not in UNSUPPORTED_TYPES for entity_id in chart["entity_ids"])


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


def create_card_router(
    index: Index,
    time_zone: str,
    check_auth: Callable[..., None],
    query_multi: Callable[..., dict],
    app_link: Callable[[], str | None] = lambda: None,
) -> APIRouter:
    router = APIRouter()
    slots = threading.BoundedSemaphore(MAX_CONCURRENT_QUERIES)

    def aggregation_types() -> dict[str, str]:
        return {row["entity_id"]: row["aggregation_type"] for row in index.list_entities()}

    def answer(chart: dict, at: str | None) -> dict:
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
            result["data"] = query_multi(
                entity_ids=",".join(visible), range=chart["range_key"], offset=0,
                continuous=chart["continuous"], year_over_year=False, compare=False,
                compare_mode="previous", raw=False, previous_years=1,
                # Ein fester Zeitraum bleibt fest, der Stichtag gilt nur für die übrigen.
                at=chart["period_anchor"] or at, heatmap=False, columns=False, px=0,
            )
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
        return {"spec_version": SPEC_VERSION, "items": items, "app_link": app_link()}

    @router.get("/api/card/chart")
    def card_chart(
        request: Request,
        id: int,
        at: str | None = None,
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
        return answer(chart, at)

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
            if row["aggregation_type"] not in UNSUPPORTED_TYPES
        ]
        return {"spec_version": SPEC_VERSION, "entities": entities}

    @router.get("/api/card/series")
    def card_series(
        request: Request,
        entity_ids: str,
        period: str = "month",
        style: str = "auto",
        at: str | None = None,
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
        return answer(ad_hoc_chart(ids, period, style), at)

    return router
