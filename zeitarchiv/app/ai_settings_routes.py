"""Einstellungsbereich "KI-Zugriff" (ROADMAP 1.31, V1).

Der Bereich wird von der Einstellungsseite per htmx nachgeladen (wie
settings/ram) und speichert über einen eigenen POST. Eigenes Modul, damit
main.py nur die Anbindung trägt (Zeilenbudget, siehe test_route_modules.py).

Der Schalter und die Auswahl der freigegebenen Entitäten gehören der Seite, die
die Daten besitzt, nicht der Integration: Es gibt genau eine Stelle, die gilt.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from zoneinfo import ZoneInfo

from . import ai_settings, ai_tools, ai_usage
from .formatting import entity_display_name, format_time, format_timestamp
from .i18n import active_language, tr
from .storage.index import Index


def _example_questions() -> list[str]:
    return [
        tr("Wie viel Strom habe ich im letzten Monat verbraucht?"),
        tr("An welchem Tag im August war es auf dem Dachboden am wärmsten?"),
        tr("Habe ich dieses Jahr mehr oder weniger Strom verbraucht als letztes Jahr?"),
        tr("Wie lange lief die Pumpe gestern?"),
        tr("Was hat mich der Strom 2025 gekostet?"),
    ]


def _instruction_text() -> str:
    """Text zum Kopieren für die Projekt-Anweisung in Claude bzw. die eigenen
    Anweisungen in ChatGPT. Freiwillig; hilft, wenn neben Zeitarchiv auch HAs
    eigene Werkzeuge aktiv sind. Fest Englisch wie alle Texte für das Modell."""
    return (
        "For historical values, comparisons and energy costs of my home, use the Zeitarchiv tools. "
        "For the current state of devices, use the Home Assistant tools. "
        "Start with zeitarchiv_search_entities to find entity IDs."
    )


def _usage(index: Index, tz: ZoneInfo, language: str) -> dict:
    snapshot = ai_usage.USAGE.snapshot(index)
    last = snapshot["last_used"]
    return {
        "last_used": f"{format_timestamp(last, tz)} {format_time(last, tz)}" if last else None,
        "total": snapshot["total"],
        "by_tool": [
            {"title": titles[0 if language == "de" else 1].removeprefix("Zeitarchiv: "), "count": snapshot["calls"].get(name, 0)}
            for name, titles in ai_tools.TOOL_TEXTS.items()
        ],
        "admin": snapshot["by_account"]["admin"],
        "user": snapshot["by_account"]["user"],
        "unknown": snapshot["by_account"]["unknown"],
    }


def make_router(get_index: Callable[[], Index], templates: Jinja2Templates, tz: ZoneInfo) -> APIRouter:
    router = APIRouter()

    def context(request: Request, saved: bool = False) -> dict:
        index = get_index()
        access = ai_settings.load(index)
        language = active_language()
        rows = index.list_entities()
        return {
            "ai_access": access,
            "ai_scopes": ai_settings.SCOPES,
            "ai_saved": saved,
            "ai_entities": [
                {
                    "entity_id": row["entity_id"],
                    "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
                    "unit": row["unit"] or "",
                    "checked": row["entity_id"] in access.entities,
                }
                for row in rows
            ],
            # Auswahl, die es nicht mehr gibt, bleibt sichtbar statt still zu verschwinden.
            "ai_missing": sorted(access.entities - {row["entity_id"] for row in rows}),
            "ai_tools": [
                {
                    "name": name,
                    "title": titles[0 if language == "de" else 1].removeprefix("Zeitarchiv: "),
                    "enabled": name not in access.tools_disabled,
                }
                for name, titles in ai_tools.TOOL_TEXTS.items()
            ],
            "ai_instructions_max": ai_settings.MAX_INSTRUCTIONS_LENGTH,
            "ai_usage": _usage(index, tz, language),
            "ai_api_ids": ai_settings.known_api_ids(index),
            "ai_questions": _example_questions(),
            "ai_instruction": _instruction_text(),
        }

    @router.get("/settings/ai", response_class=HTMLResponse)
    def ai_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_settings_ki_form.html", context(request))

    @router.post("/settings/ai", response_class=HTMLResponse)
    async def ai_save(request: Request) -> HTMLResponse:
        form = await request.form()
        scope = form.get("scope", "all")
        if scope not in ai_settings.SCOPES:
            scope = "all"
        ai_settings.save(
            get_index(),
            enabled=form.get("enabled") == "1",
            scope=scope,
            entities=[str(item) for item in form.getlist("entity")],
            exclude_presence=form.get("exclude_presence") == "1",
            tools_disabled=[name for name in ai_tools.TOOL_TEXTS if name not in set(form.getlist("tool"))],
            instructions=str(form.get("instructions", "")),
        )
        return templates.TemplateResponse(request, "_settings_ki_form.html", context(request, saved=True))

    return router
