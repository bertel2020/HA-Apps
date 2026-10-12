"""Export-Bereich (Einstellungen-Bereich, analog zum Import): ein Assistent in vier
Schritten — Entitäten, Zeitraum, Auflösung, Prüfen. Eine Entität ergibt eine CSV,
mehrere ergeben automatisch ein ZIP mit je einer CSV. Die Dateilogik steht in
export_data.py.

Der Download je Zeile der Entitätenliste bleibt: ohne Zeitraum und Auflösung
(``/export/download?entity_id=…``) kommt die komplette Rohdaten-Historie, Hot
Buffer und Archiv zusammengeführt, zur Löschung markierte Datensätze
ausgeschlossen — dieselbe Sicht wie die Bereinigungs-Seite (list_raw_rows())."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import export_data
from .formatting import entity_display_name, format_type
from .i18n import tr
from .limits import MAX_EXPORT_ROWS
from .storage.coordinator import StorageCoordinator
from .storage.index import Index
from .storage.paths import validate_entity_id

logger = logging.getLogger(__name__)

# Obergrenze der Entitäten je Anfrage — nur Schutz vor absurden Query-Strings.
MAX_EXPORT_ENTITIES = 5000


@dataclass(frozen=True)
class ExportDependencies:
    index: Index
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    templates: Jinja2Templates
    visible_row_count: Callable[[object], int]
    paginate_meta: Callable[[int, int, int], dict]


class EstimateRequest(BaseModel):
    entity_ids: list[str] = Field(default_factory=list)
    start: str = ""
    end: str = ""


def create_export_router(deps: ExportDependencies) -> APIRouter:
    router = APIRouter()
    index = deps.index

    def entities_or_error(entity_ids: list[str]) -> list:
        if not entity_ids:
            raise HTTPException(status_code=400, detail=tr("Keine Entität ausgewählt"))
        if len(entity_ids) > MAX_EXPORT_ENTITIES:
            raise HTTPException(status_code=400, detail=tr("Zu viele Entitäten ausgewählt"))
        found = []
        for entity_id in dict.fromkeys(entity_ids):
            try:
                validate_entity_id(entity_id)
            except ValueError as err:
                raise HTTPException(status_code=400, detail=tr("Ungültige Entitäts-ID")) from err
            entity = index.get_entity(entity_id)
            if entity is None:
                raise HTTPException(status_code=404, detail=tr("Entität nicht gefunden"))
            found.append(entity)
        return found

    def period_or_400(start: str, end: str) -> tuple[float | None, float | None]:
        try:
            return export_data.parse_period(start or None, end or None, deps.tz)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    @router.get("/export", response_class=HTMLResponse)
    def export_page(request: Request) -> HTMLResponse:
        """Seitenrahmen mit dem Assistenten. Die Entitätenliste (Schritt 1) lädt wie
        bei der Entitäten-Übersicht per htmx aus /export-table, damit
        Suche/Filter/Sortierung ohne Reload greifen."""
        units = index.list_distinct_units()
        unit_options = [
            {"value": "__none__" if u is None else u, "label": tr("Ohne Einheit") if u is None else u}
            for u in units
        ]
        return deps.templates.TemplateResponse(
            request,
            "export.html",
            {"unit_options": unit_options, "max_rows": MAX_EXPORT_ROWS},
        )

    def table_response(
        request: Request,
        search: str,
        type_filter: list[str],
        unit_filter: str,
        sort: str,
        direction: str,
        page: int,
        page_size: int,
    ) -> HTMLResponse:
        total = index.count_entities(search=search or None, type_filter=type_filter, unit_filter=unit_filter)
        pagination = deps.paginate_meta(total, page, page_size)
        page_matched = index.list_entities(
            search=search or None, type_filter=type_filter, unit_filter=unit_filter, sort=sort,
            direction=direction, limit=pagination["page_size"],
            offset=(pagination["page"] - 1) * pagination["page_size"],
        )
        rows = [
            {
                "entity_id": row["entity_id"],
                "friendly_name": row["friendly_name"],
                "display_name": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
                "has_custom_name": bool(row["custom_name"]),
                "aggregation_type": row["aggregation_type"],
                "type_label": format_type(row["aggregation_type"]),
                "unit": row["unit"],
                "row_count": deps.visible_row_count(row),
            }
            for row in page_matched
        ]

        def next_dir(column: str) -> str:
            return "desc" if sort == column and direction == "asc" else "asc"

        columns = [("entity_id", tr("Entität")), ("type", tr("Typ")), ("unit", tr("Einheit")), ("rows", tr("Datensätze"))]
        header_links = [
            {
                "key": key,
                "label": label,
                "next_dir": next_dir(key),
                "active": sort == key,
                "arrow": ("↓" if direction == "asc" else "↑") if sort == key else "",
            }
            for key, label in columns
        ]
        return deps.templates.TemplateResponse(
            request,
            "_export_table.html",
            {
                "rows": rows,
                "search": search,
                "type": type_filter,
                "unit": unit_filter,
                "columns": header_links,
                "pagination": pagination,
                "max_rows": MAX_EXPORT_ROWS,
            },
        )

    @router.get("/export-table", response_class=HTMLResponse)
    def export_table(
        request: Request,
        search: str = "",
        type: list[str] = Query(["all"]),
        unit: str = "all",
        sort: str = "entity_id",
        dir: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> HTMLResponse:
        return table_response(request, search, type, unit, sort, dir, page, page_size)

    @router.get("/export/entity-ids")
    def export_entity_ids(
        search: str = "",
        type: list[str] = Query(["all"]),
        unit: str = "all",
    ) -> JSONResponse:
        """Alle Entitäten zu den Filtern der Liste (nicht nur die sichtbare Seite) —
        für „Alle Entitäten wählen"."""
        rows = index.list_entities(search=search or None, type_filter=type, unit_filter=unit)
        return JSONResponse({"entity_ids": [row["entity_id"] for row in rows]})

    @router.post("/export/estimate")
    def export_estimate(body: EstimateRequest) -> JSONResponse:
        """Zeilen je Entität und Auflösung im Zeitraum (siehe export_data.py) samt
        Datenspanne der Entität — Grundlage für die gesperrten Monate, die
        Auflösungs-Kacheln und die Prüfen-Seite des Assistenten. POST, weil die
        Auswahl bei „Alle Entitäten“ Hunderte IDs lang sein kann."""
        entities = entities_or_error(body.entity_ids)
        start_ts, end_ts = period_or_400(body.start, body.end)
        files = []
        for entity in entities:
            first_ts, last_ts = entity["first_ts"], entity["last_ts"]
            files.append({
                "entity_id": entity["entity_id"],
                "name": entity_display_name(entity["entity_id"], entity["friendly_name"], entity["custom_name"]),
                "type": entity["aggregation_type"],
                "first": datetime.fromtimestamp(first_ts, deps.tz).strftime("%Y-%m-%d") if first_ts is not None else None,
                "last": datetime.fromtimestamp(last_ts, deps.tz).strftime("%Y-%m-%d") if last_ts is not None else None,
                "rows": export_data.estimate_rows(deps.data_dir, entity, start_ts, end_ts, deps.tz),
            })
        return JSONResponse({"limit": MAX_EXPORT_ROWS, "files": files})

    def download_response(entity_ids: list[str], start: str, end: str, resolution: str) -> StreamingResponse:
        """Eine Entität → CSV, mehrere → ZIP mit je einer CSV."""
        if resolution not in export_data.RESOLUTIONS:
            raise HTTPException(status_code=400, detail=tr("Unbekannte Auflösung"))
        entities = entities_or_error(entity_ids)
        start_ts, end_ts = period_or_400(start, end)

        complete_raw = start_ts is None and end_ts is None and resolution == "raw"
        for entity in entities:
            if complete_raw:
                rows = deps.visible_row_count(entity)
            else:
                rows = export_data.estimate_rows(deps.data_dir, entity, start_ts, end_ts, deps.tz)[resolution]
            if rows > MAX_EXPORT_ROWS:
                raise HTTPException(
                    status_code=413,
                    detail=tr("CSV-Export ist auf {MAX_EXPORT_ROWS} Zeilen begrenzt", MAX_EXPORT_ROWS=MAX_EXPORT_ROWS),
                )

        suffix = export_data.file_suffix(start or None, end or None, resolution)

        def chunks_for(entity):
            return lambda: export_data.csv_chunks(
                deps.data_dir, index, entity, start_ts, end_ts, resolution, deps.tz,
                deps.coordinator, MAX_EXPORT_ROWS,
            )

        if len(entities) == 1:
            entity = entities[0]
            filename = f"{entity['entity_id']}{suffix}.csv"
            return StreamingResponse(
                chunks_for(entity)(),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        files = [(f"{entity['entity_id']}{suffix}.csv", chunks_for(entity)) for entity in entities]
        filename = f"zeitarchiv-export_{len(entities)}-entitaeten{suffix}.zip"
        return StreamingResponse(
            export_data.zip_stream(files, datetime.now(deps.tz)),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/export/download")
    def export_download(
        entity_id: Annotated[list[str], Query()],
        start: str = "",
        end: str = "",
        resolution: str = "raw",
    ) -> StreamingResponse:
        """Download je Zeile der Entitätenliste: ohne Zeitraum und Auflösung die
        komplette Rohdaten-Historie einer Entität."""
        return download_response(entity_id, start, end, resolution)

    @router.post("/export/download")
    def export_download_form(
        entity_id: Annotated[list[str], Form()],
        start: str = Form(""),
        end: str = Form(""),
        resolution: str = Form("raw"),
    ) -> StreamingResponse:
        """Download aus dem Assistenten als Formular-POST: die Auswahl kann für eine
        Query-Zeile zu lang sein."""
        return download_response(entity_id, start, end, resolution)

    return router
