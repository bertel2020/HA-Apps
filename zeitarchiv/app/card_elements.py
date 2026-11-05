"""Dashboard-Bausteine für die Lovelace-Karte (Roadmap 1.48).

Ein Baustein ist ein fertiger, benannter Ausschnitt eines Dashboards (Schlüssel wie ``energie.zeitraeume``).
Die Karte kennt nur den Schlüssel und wenige Optionen; Rollen, Formeln und Entitäten bleiben in der App
eingerichtet. Die Zahlen rechnen dieselben Funktionen wie die Dashboards, hier gibt es keine zweite
Berechnung. Gezeichnet wird in der Karte (element-table-render.js).

Die Dienste der Dashboards entstehen nach dem API-Router; ``bind()`` reicht sie aus main.py nach.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .i18n import tr
from .storage.index import Index

#: Alle Spaltenschlüssel der Zeitraum-Tabellen (Energie kennt davon nur die letzten drei).
COLUMN_KEYS = ("heute", "gestern", "monat", "vormonat", "jahr")
MODES = ("bis_jetzt", "komplett")


class ElementUnavailable(Exception):
    """Der Baustein ist in der App nicht eingerichtet (Antwort 409 mit dem Grund)."""


@dataclass
class Options:
    """Die Optionen einer Anfrage; was ein Baustein nicht kennt, ignoriert er."""

    columns: list[str] | None = None
    vorjahr: bool = True
    mode: str = "bis_jetzt"
    at: str | None = None


@dataclass
class Context:
    index: Index | None = None
    energie: Any = None
    wetter: Any = None


CONTEXT = Context()


def bind(index: Index, energie: Any = None, wetter: Any = None) -> None:
    """Meldet die Dienste der Dashboards an (einmal beim Start, aus main.py)."""
    CONTEXT.index, CONTEXT.energie, CONTEXT.wetter = index, energie, wetter


@dataclass(frozen=True)
class Element:
    key: str
    group: str
    kind: str
    title: Callable[[], str]
    available: Callable[[Context], bool]
    build: Callable[[Context, Options], dict]
    #: Die Spalten, aus denen der Baustein wählen lässt.
    columns: tuple[str, ...] = field(default_factory=tuple)


def _energie_config(ctx: Context) -> dict | None:
    if ctx.energie is None or ctx.index is None:
        return None
    # Spät importiert: energiedashboard_routes zieht fast die ganze App nach sich.
    from . import energie_langzeit
    from .energiedashboard_routes import _load_config

    config = _load_config(ctx.index)
    return config if energie_langzeit.is_configured(config) else None


def _energie_zeitraeume_available(ctx: Context) -> bool:
    return _energie_config(ctx) is not None


def _energie_zeitraeume(ctx: Context, options: Options) -> dict:
    from . import energie_langzeit

    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    keys = [key for key in (options.columns or energie_langzeit.COLUMN_KEYS) if key in energie_langzeit.COLUMN_KEYS]
    body = energie_langzeit.TableRequest(
        columns=keys, vorjahr=options.vorjahr, mode=options.mode if options.mode in MODES else "bis_jetzt", at=options.at,
    )
    return {"renderer": "energie.zeitraeume", "table": ctx.energie.langzeit.compute_table(config, body)}


ELEMENTS: dict[str, Element] = {
    element.key: element
    for element in (
        Element(
            key="energie.zeitraeume", group="energie", kind="table",
            title=lambda: tr("Energie: Zeiträume im Vergleich"),
            available=_energie_zeitraeume_available, build=_energie_zeitraeume,
            columns=("monat", "vormonat", "jahr"),
        ),
    )
}


def listing(ctx: Context | None = None) -> list[dict]:
    """Die Bausteine, die in dieser App eingerichtet sind (für die Auswahl im Editor der Karte)."""
    ctx = ctx or CONTEXT
    return [
        {"key": element.key, "group": element.group, "kind": element.kind, "title": element.title(), "columns": list(element.columns)}
        for element in ELEMENTS.values()
        if element.available(ctx)
    ]


def build(key: str, options: Options, ctx: Context | None = None) -> dict | None:
    """Antwort für einen Baustein oder None, wenn es den Schlüssel nicht gibt. Wirft ``ElementUnavailable``."""
    element = ELEMENTS.get(key)
    if element is None:
        return None
    ctx = ctx or CONTEXT
    return {"key": element.key, "kind": element.kind, "title": element.title(), **element.build(ctx, options)}
