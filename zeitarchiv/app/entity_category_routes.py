"""Kategorien für Entitäten (ROADMAP 1.42, Phase 1): Verwaltung und Zuweisen.

Kategorien sind eine eigene Ordnung der App (Thema einer Entität, zum Beispiel
Wärmepumpe oder Solar), unabhängig vom Favoriten-Stern. Jede Entität hat
höchstens eine; die Reihenfolge der Kategorien bestimmt die Sortierung in der
Entitätenliste. Die Anzeige in der Liste liegt in main.py (Spalte „Kategorie“),
hier stehen die Seite zum Verwalten und die schreibenden Routen.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .i18n import N_, tr
from .storage.index import (
    CATEGORY_COLOR_SLOTS,
    MAX_CATEGORIES,
    MAX_CATEGORY_NAME_LENGTH,
    Index,
)

#: Namen der Vorschläge im Leer-Zustand; die Reihenfolge ist die spätere
#: Sortierung. Übernommen werden sie zu ganz normalen, eigenen Kategorien.
SUGGESTED_CATEGORIES = [
    N_("Solar"), N_("Wärmepumpe"), N_("Batterie"), N_("E-Auto"),
    N_("Wetter"), N_("Verbraucher"), N_("Netz"),
]


class _NameBody(BaseModel):
    name: str = Field(max_length=200)


class _NamesBody(BaseModel):
    names: list[str] = Field(max_length=MAX_CATEGORIES)


class _ColorBody(BaseModel):
    slot: int


class _OrderBody(BaseModel):
    ids: list[int] = Field(max_length=MAX_CATEGORIES)


def make_router(get_index: Callable[[], Index], templates: Jinja2Templates, set_columns: Callable[[], None]) -> APIRouter:
    """``set_columns`` nimmt die Kategorie-Spalte in eine schon gespeicherte
    Spaltenauswahl der Liste auf, sobald die erste Kategorie entsteht (ohne
    gespeicherte Auswahl erscheint sie von selbst)."""
    router = APIRouter()

    @router.get("/entities/categories", response_class=HTMLResponse)
    def categories_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "entity_categories.html", {
            "categories": get_index().list_entity_categories(),
            "suggestions": [tr(name) for name in SUGGESTED_CATEGORIES],
            "color_slots": range(1, CATEGORY_COLOR_SLOTS + 1),
            "max_categories": MAX_CATEGORIES,
            "max_name_length": MAX_CATEGORY_NAME_LENGTH,
        })

    def _created(was_empty: bool) -> None:
        if was_empty:
            set_columns()

    @router.post("/entities/categories")
    def create_category(body: _NameBody) -> dict:
        index = get_index()
        was_empty = not index.list_entity_categories()
        category_id = index.create_entity_category(body.name)
        _created(was_empty)
        return {"id": category_id}

    @router.post("/entities/categories/suggestions")
    def create_suggested(body: _NamesBody) -> dict:
        """Mehrere Kategorien auf einmal, alles oder nichts."""
        index = get_index()
        was_empty = not index.list_entity_categories()
        ids = index.create_entity_categories(body.names)
        _created(was_empty and bool(ids))
        return {"ids": ids}

    @router.post("/entities/categories/reorder")
    def reorder_categories(body: _OrderBody) -> dict:
        get_index().reorder_entity_categories(body.ids)
        return {"ok": True}

    @router.post("/entities/categories/{category_id}/rename")
    def rename_category(category_id: int, body: _NameBody) -> dict:
        if not get_index().rename_entity_category(category_id, body.name):
            raise HTTPException(status_code=404, detail=tr("Kategorie nicht gefunden"))
        return {"ok": True}

    @router.post("/entities/categories/{category_id}/color")
    def color_category(category_id: int, body: _ColorBody) -> dict:
        if not 1 <= body.slot <= CATEGORY_COLOR_SLOTS:
            raise HTTPException(status_code=400, detail=tr("Ungültige Farbe"))
        if not get_index().set_entity_category_color(category_id, body.slot):
            raise HTTPException(status_code=404, detail=tr("Kategorie nicht gefunden"))
        return {"ok": True}

    @router.post("/entities/categories/{category_id}/delete")
    def delete_category(category_id: int) -> dict:
        released = get_index().delete_entity_category(category_id)
        if released < 0:
            raise HTTPException(status_code=404, detail=tr("Kategorie nicht gefunden"))
        return {"released": released}

    @router.post("/entities/set-category")
    def set_category(entity_id: list[str] = Form(default=[]), category_id: str = Form(default="")) -> dict:
        """Ordnet die Entitäten einer Kategorie zu; leere ``category_id`` löst die
        Zuordnung. Wie bei „Löschen …“ kommen die IDs als Formularfelder."""
        if not entity_id:
            raise HTTPException(status_code=400, detail=tr("Keine Entitäten gewählt"))
        try:
            target = int(category_id) if category_id.strip() else None
        except ValueError as err:
            raise HTTPException(status_code=400, detail=tr("Kategorie nicht gefunden")) from err
        try:
            changed = get_index().set_entities_category(entity_id, target)
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        return {"changed": changed}

    return router
