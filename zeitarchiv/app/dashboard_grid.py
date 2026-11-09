"""Kacheln pro Zeile eines Dashboards (Roadmap 1.40, Phase 6): 3 oder 4, im Präzisen Modus 6 oder 8 Spalten.

Die Einstellung wirkt in der Anzeige nur bei eingeschalteter breiter Darstellung und ab 1500 px
Fensterbreite; sonst und darunter zeigt das Raster wie bisher 3 (6) Spalten, und zu breite Kacheln werden
dort nur schmaler dargestellt (CSS, ``data-columns`` am Raster). Gespeichert wird die Kachelgröße ungekürzt.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .i18n import tr
from .storage.index import Index


def size_limits(dashboard: dict | None) -> tuple[int, int]:
    """Größte wählbare Kachel (Spalten, Zeilen) des Dashboards."""
    precise = 2 if dashboard and dashboard["precise_mode"] else 1
    columns = int(dashboard["grid_columns"]) if dashboard else 3
    return columns * precise, 3 * precise


class _GridColumnsBody(BaseModel):
    grid_columns: int


def make_router(get_index: Callable[[], Index]) -> APIRouter:
    router = APIRouter()

    @router.post("/dashboards/{dashboard_id}/grid-columns")
    def dashboards_grid_columns(dashboard_id: int, body: _GridColumnsBody) -> dict:
        try:
            found = get_index().set_dashboard_grid_columns(dashboard_id, body.grid_columns)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail=tr("Dashboard nicht gefunden"))
        return {"ok": True, "grid_columns": body.grid_columns}

    return router
