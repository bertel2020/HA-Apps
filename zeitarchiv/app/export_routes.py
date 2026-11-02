"""Export-Bereich (Einstellungen-Bereich, analog zum Import): ein Assistent in vier
Schritten — Entitäten, Zeitraum, Auflösung, Form und Download. Eine Entität ergibt
eine CSV, mehrere ergeben ein ZIP mit je einer CSV oder eine gemeinsame Datei
(nebeneinander oder untereinander). Die Dateilogik steht in export_data.py.

Der Download je Zeile der Entitätenliste bleibt: ohne Zeitraum und Auflösung
(``/export/download?entity_id=…``) kommt die komplette Rohdaten-Historie, Hot
Buffer und Archiv zusammengeführt, zur Löschung markierte Datensätze
ausgeschlossen — dieselbe Sicht wie die Bereinigungs-Seite (list_raw_rows())."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
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
#: Zeilen der Vorschau in Schritt 4, mit der Kopfzeile.
PREVIEW_LINES = 4


@dataclass(frozen=True)
class ExportDependencies:
    index: Index
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    templates: Jinja2Templates
    visible_row_count: Callable[[object], int]
    paginate_meta: Callable[[int, int, int], dict]


@dataclass(frozen=True)
class DownloadPlan:
    """Was ein Download liefert: Dateiname, Typ, der Strom der Bytes und eine Probe (die erste Datei als Text)."""

    filename: str
    media_type: str
    stream: Callable[[], Iterator[bytes]]
    sample_name: str
    sample: Callable[[], Iterator[str]]


def iter_bytes(chunks: Iterator[str]) -> Iterator[bytes]:
    for chunk in chunks:
        yield chunk.encode("utf-8")


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
            {"unit_options": unit_options, "max_rows": MAX_EXPORT_ROWS, "categories": index.list_entity_categories()},
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
        category_filter: list[str] | None = None,
    ) -> HTMLResponse:
        total = index.count_entities(
            search=search or None, type_filter=type_filter, unit_filter=unit_filter, category_filter=category_filter,
        )
        pagination = deps.paginate_meta(total, page, page_size)
        page_matched = index.list_entities(
            search=search or None, type_filter=type_filter, unit_filter=unit_filter, sort=sort,
            direction=direction, limit=pagination["page_size"],
            offset=(pagination["page"] - 1) * pagination["page_size"], category_filter=category_filter,
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
        category: list[str] = Query(["all"]),
    ) -> HTMLResponse:
        return table_response(request, search, type, unit, sort, dir, page, page_size, category)

    @router.get("/export/entity-ids")
    def export_entity_ids(
        search: str = "",
        type: list[str] = Query(["all"]),
        unit: str = "all",
        category: list[str] = Query(["all"]),
    ) -> JSONResponse:
        """Alle Entitäten zu den Filtern der Liste (nicht nur die sichtbare Seite) —
        für „Alle Entitäten wählen"."""
        rows = index.list_entities(search=search or None, type_filter=type, unit_filter=unit, category_filter=category)
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
        return JSONResponse({"limit": MAX_EXPORT_ROWS, "wide_limit": export_data.MAX_WIDE_ENTITIES, "files": files})

    def format_or_400(
        layout: str, sep: str, decimal: str, bom: bool, time: str, unix: bool,
    ) -> export_data.CsvFormat:
        try:
            return export_data.parse_format(layout, sep, decimal, bom, time, unix)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    def too_many_rows() -> HTTPException:
        return HTTPException(
            status_code=413,
            detail=tr("CSV-Export ist auf {MAX_EXPORT_ROWS} Zeilen begrenzt", MAX_EXPORT_ROWS=MAX_EXPORT_ROWS),
        )

    def joint_plan(
        entities: list, start: str, end: str, resolution: str, start_ts: float | None, end_ts: float | None,
        fmt: export_data.CsvFormat,
    ) -> DownloadPlan:
        """Eine gemeinsame CSV für mehrere Entitäten (nebeneinander oder untereinander)."""
        if any(entity["aggregation_type"] == "state" for entity in entities):
            raise HTTPException(
                status_code=400, detail=tr("Zustands-Entitäten lassen sich nicht in einer gemeinsamen Datei exportieren"),
            )
        if fmt.layout == "wide":
            if resolution == "raw":
                raise HTTPException(status_code=400, detail=tr("Nebeneinander gibt es erst ab der Auflösung Minute"))
            if len(entities) > export_data.MAX_WIDE_ENTITIES:
                raise HTTPException(
                    status_code=400,
                    detail=tr("Nebeneinander ist auf {MAX_WIDE_ENTITIES} Entitäten begrenzt", MAX_WIDE_ENTITIES=export_data.MAX_WIDE_ENTITIES),
                )
        if start_ts is None and end_ts is None and resolution == "raw":
            rows = sum(deps.visible_row_count(entity) for entity in entities)
        else:
            rows = export_data.estimate_joint_rows(deps.data_dir, entities, start_ts, end_ts, resolution, fmt.layout, deps.tz)
        if rows > MAX_EXPORT_ROWS:
            raise too_many_rows()
        names = {e["entity_id"]: entity_display_name(e["entity_id"], e["friendly_name"], e["custom_name"]) for e in entities}
        make = export_data.wide_chunks if fmt.layout == "wide" else export_data.long_chunks
        suffix = export_data.file_suffix(start or None, end or None, resolution)
        filename = f"zeitarchiv-export_{len(entities)}-entitaeten{suffix}.csv"

        def chunks() -> Iterator[str]:
            return make(
                deps.data_dir, index, entities, names, start_ts, end_ts, resolution, deps.tz, deps.coordinator,
                MAX_EXPORT_ROWS, fmt,
            )

        return DownloadPlan(filename, "text/csv", lambda: iter_bytes(chunks()), filename, chunks)

    def download_plan(
        entity_ids: list[str], start: str, end: str, resolution: str, fmt: export_data.CsvFormat | None = None,
    ) -> DownloadPlan:
        """Eine Entität → CSV, mehrere → ZIP mit je einer CSV oder (fmt.layout) eine gemeinsame CSV."""
        fmt = fmt or export_data.CsvFormat()
        if resolution not in export_data.RESOLUTIONS:
            raise HTTPException(status_code=400, detail=tr("Unbekannte Auflösung"))
        entities = entities_or_error(entity_ids)
        start_ts, end_ts = period_or_400(start, end)
        if fmt.layout != "per_entity" and len(entities) > 1:
            return joint_plan(entities, start, end, resolution, start_ts, end_ts, fmt)

        complete_raw = start_ts is None and end_ts is None and resolution == "raw"
        for entity in entities:
            if complete_raw:
                rows = deps.visible_row_count(entity)
            else:
                rows = export_data.estimate_rows(deps.data_dir, entity, start_ts, end_ts, deps.tz)[resolution]
            if rows > MAX_EXPORT_ROWS:
                raise too_many_rows()

        suffix = export_data.file_suffix(start or None, end or None, resolution)

        def chunks_for(entity):
            return lambda: export_data.csv_chunks(
                deps.data_dir, index, entity, start_ts, end_ts, resolution, deps.tz,
                deps.coordinator, MAX_EXPORT_ROWS, fmt,
            )

        first = entities[0]
        first_name = f"{first['entity_id']}{suffix}.csv"
        if len(entities) == 1:
            return DownloadPlan(first_name, "text/csv", lambda: iter_bytes(chunks_for(first)()), first_name, chunks_for(first))

        files = [(f"{entity['entity_id']}{suffix}.csv", chunks_for(entity)) for entity in entities]
        filename = f"zeitarchiv-export_{len(entities)}-entitaeten{suffix}.zip"
        return DownloadPlan(
            filename, "application/zip", lambda: export_data.zip_stream(files, datetime.now(deps.tz)),
            first_name, chunks_for(first),
        )

    def download_response(plan: DownloadPlan) -> StreamingResponse:
        return StreamingResponse(
            plan.stream(), media_type=plan.media_type,
            headers={"Content-Disposition": f'attachment; filename="{plan.filename}"'},
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
        return download_response(download_plan(entity_id, start, end, resolution))

    @router.post("/export/download")
    def export_download_form(
        entity_id: Annotated[list[str], Form()],
        start: str = Form(""),
        end: str = Form(""),
        resolution: str = Form("raw"),
        layout: str = Form("per_entity"),
        sep: str = Form("comma"),
        decimal: str = Form("point"),
        bom: bool = Form(False),
        time: str = Form("stamp"),
        unix: bool = Form(True),
    ) -> StreamingResponse:
        """Download aus dem Assistenten als Formular-POST: die Auswahl kann für eine
        Query-Zeile zu lang sein. Ohne die Felder zur Form kommt die Datei von früher."""
        fmt = format_or_400(layout, sep, decimal, bom, time, unix)
        return download_response(download_plan(entity_id, start, end, resolution, fmt))

    @router.post("/export/preview")
    def export_preview(
        entity_id: Annotated[list[str], Form()],
        start: str = Form(""),
        end: str = Form(""),
        resolution: str = Form("raw"),
        layout: str = Form("per_entity"),
        sep: str = Form("comma"),
        decimal: str = Form("point"),
        bom: bool = Form(False),
        time: str = Form("stamp"),
        unix: bool = Form(True),
    ) -> JSONResponse:
        """Die ersten Zeilen der Datei, die der Download liefern würde (bei einem ZIP die der ersten Datei),
        für die Vorschau in Schritt 4. Dieselben Prüfungen wie beim Download, Fehler als Statuscode."""
        fmt = format_or_400(layout, sep, decimal, bom, time, unix)
        plan = download_plan(entity_id, start, end, resolution, fmt)
        lines: list[str] = []
        chunks = plan.sample()
        try:
            text = ""
            for chunk in chunks:
                text += chunk
                lines = text.split("\r\n")
                if len(lines) > PREVIEW_LINES:
                    break
        finally:
            # Gibt die Sperren frei, ohne den Rest der Datei zu lesen.
            chunks.close()  # type: ignore[attr-defined]
        shown = [line for line in lines[:PREVIEW_LINES] if line]
        return JSONResponse({"file": plan.sample_name, "text": "\n".join(shown).lstrip("\ufeff")})

    return router
