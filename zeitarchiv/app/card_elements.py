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
#: Rollen der Monatswerte (energie_langzeit.MONTH_ROLES) und die Zahl der Jahre der Matrix (0 = alle).
ROLES = ("erzeugung", "einspeisung", "netzbezug", "verbrauch")
MAX_YEARS = 50
#: Gruppen der Wetter-Tabelle (Reiter Übersicht, Wind und Sonne, Temperatur und Klima).
GROUPS = ("basis", "wind", "klima")
#: Kennzahlen des Wetter-Monatsvergleichs (wetter_routes: temperature, precip, sun).
METRICS = ("temperature", "precip", "sun")
#: Kennzahlen der Wetter-Jahresübersicht (wetter_routes.compute_matrix).
MATRIX_METRICS = ("avg", "max", "min", "precip", "sun")
ALL_METRICS = tuple(dict.fromkeys(METRICS + MATRIX_METRICS))
#: Zeiträume der Bausteine, die einen brauchen (Ring: Tag, Monat, Jahr; Lastprofil: Woche, Monat, Jahr), und die Seite
#: des Rings. offset zählt in Perioden zurück (0 = laufende, -1 = die davor), wie in den Dashboards.
RANGES = ("day", "week", "month", "year")
SIDES = ("verbrauch", "versorgung")
MAX_OFFSET = 60


class ElementUnavailable(Exception):
    """Der Baustein ist in der App nicht eingerichtet (Antwort 409 mit dem Grund)."""


@dataclass
class Options:
    """Die Optionen einer Anfrage; was ein Baustein nicht kennt, ignoriert er."""

    columns: list[str] | None = None
    vorjahr: bool = True
    mode: str = "bis_jetzt"
    at: str | None = None
    role: str | None = None
    years: int = 10
    group: str = "basis"
    metric: str = "temperature"
    range: str = "month"
    offset: int = 0
    side: str = "verbrauch"


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
    #: Die Rollen, aus denen der Baustein wählen lässt (leer: keine Auswahl).
    roles: tuple[str, ...] = field(default_factory=tuple)
    groups: tuple[str, ...] = field(default_factory=tuple)
    metrics: tuple[str, ...] = field(default_factory=tuple)
    #: Zeiträume, aus denen der Baustein wählen lässt, und die Seiten des Rings (leer: keine Auswahl).
    ranges: tuple[str, ...] = field(default_factory=tuple)
    sides: tuple[str, ...] = field(default_factory=tuple)


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


def _energie_matrix(ctx: Context, options: Options) -> dict:
    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    months = ctx.energie.langzeit.compute_months(config)
    roles = months["roles"]
    # Nur die gewählte Rolle und die gewählten Jahre gehen an die Karte (die Seite schickt alle Rollen und Jahre).
    role = next((item for item in roles if item["key"] == options.role), roles[0] if roles else None)
    if role is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    years = months["years"][: options.years] if options.years else months["years"]
    trimmed = {**role, "values": {str(year): role["values"][str(year)] for year in years}}
    return {"renderer": "energie.matrix", "months": {**months, "years": years, "roles": [trimmed]}, "role": role["key"]}


def _energie_monthly(ctx: Context, options: Options) -> dict:
    result = _energie_matrix(ctx, options)
    return {**result, "renderer": "energie.monatsvergleich", "kind": "chart"}


def _energie_shares(ctx: Context, options: Options) -> dict:
    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    range_key = options.range if options.range in ("day", "month", "year") else "month"
    flow = ctx.energie.compute_flow(config, range_key, min(0, options.offset))
    side = options.side if options.side in SIDES else SIDES[0]
    if side == "verbrauch":
        # Wie der Ring der Seite: nur die Blätter; Zähler- und Gruppensummen stünden neben ihren Mitgliedern doppelt.
        items = [item for item in flow.get("verbraucher_breakdown") or [] if item.get("kind") not in ("meter", "group")]
    else:
        items = [item for item in flow.get("versorgung_breakdown") or [] if item["value"] > 0]
    return {
        "renderer": "energie.anteile", "side": side, "range": range_key,
        "items": [{"name": item["name"], "value": item["value"]} for item in items],
        "window": {key: flow.get(key) for key in ("window_start", "window_end", "label") if key in flow},
    }


def _energie_heatmap(ctx: Context, options: Options) -> dict:
    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    range_key = options.range if options.range in ("week", "month", "year") else "week"
    if range_key == "week":
        grid = ctx.energie.compute_heatmap(config)
    else:
        offset = min(0, options.offset)
        cached = ctx.index.get_heatmap_weekday_snapshot(range_key) if offset == 0 else None
        grid = cached["grid"] if cached is not None else ctx.energie.compute_heatmap_weekday(config, range_key, offset)
    return {"renderer": "energie.heatmap", "heatmap": grid, "range": range_key}


def _wetter_available(ctx: Context) -> bool:
    from .wetter_routes import is_wetter_configured

    return ctx.wetter is not None and ctx.index is not None and is_wetter_configured(ctx.index)


def _wetter_zeitraeume(ctx: Context, options: Options) -> dict:
    from . import wetter_routes

    if not _wetter_available(ctx):
        raise ElementUnavailable(tr("Im Wetterdashboard nicht eingerichtet"))
    keys = [key for key in (options.columns or wetter_routes.COLUMN_KEYS) if key in wetter_routes.COLUMN_KEYS]
    body = wetter_routes.TableRequest(
        columns=keys, vorjahr=options.vorjahr, mode=options.mode if options.mode in MODES else "bis_jetzt", at=options.at,
    )
    return {
        "renderer": "wetter.zeitraeume", "table": ctx.wetter.compute_table(body),
        "group": options.group if options.group in GROUPS else "basis",
    }


def _wetter_matrix(ctx: Context, options: Options) -> dict:
    if not _wetter_available(ctx):
        raise ElementUnavailable(tr("Im Wetterdashboard nicht eingerichtet"))
    metric = options.metric if options.metric in MATRIX_METRICS else MATRIX_METRICS[0]
    matrix = ctx.wetter.compute_matrix(metric)
    if matrix is None:
        raise ElementUnavailable(tr("Kein Niederschlag zugeordnet."))
    return {"renderer": "wetter.matrix", "matrix": matrix, "metric": metric}


def _wetter_monthly(ctx: Context, options: Options) -> dict:
    if not _wetter_available(ctx):
        raise ElementUnavailable(tr("Im Wetterdashboard nicht eingerichtet"))
    metric = options.metric if options.metric in METRICS else "temperature"
    monthly = ctx.wetter.compute_monthly(metric)
    if monthly is None:
        raise ElementUnavailable(tr("Kein Niederschlag zugeordnet."))
    return {"renderer": "wetter.monatsvergleich", "monthly": monthly, "metric": metric}


ELEMENTS: dict[str, Element] = {
    element.key: element
    for element in (
        Element(
            key="energie.zeitraeume", group="energie", kind="table",
            title=lambda: tr("Energie: Zeiträume im Vergleich"),
            available=_energie_zeitraeume_available, build=_energie_zeitraeume,
            columns=("monat", "vormonat", "jahr"),
        ),
        Element(
            key="energie.matrix", group="energie", kind="table",
            title=lambda: tr("Energie: Jahre × Monate"),
            available=_energie_zeitraeume_available, build=_energie_matrix, roles=ROLES,
        ),
        Element(
            key="energie.monatsvergleich", group="energie", kind="chart",
            title=lambda: tr("Energie: Monatsvergleich"),
            available=_energie_zeitraeume_available, build=_energie_monthly, roles=ROLES,
        ),
        Element(
            key="energie.anteile", group="energie", kind="chart",
            title=lambda: tr("Energie: Anteile"),
            available=_energie_zeitraeume_available, build=_energie_shares,
            ranges=("day", "month", "year"), sides=SIDES,
        ),
        Element(
            key="energie.heatmap", group="energie", kind="chart",
            title=lambda: tr("Energie: Tageslastprofil"),
            available=_energie_zeitraeume_available, build=_energie_heatmap,
            ranges=("week", "month", "year"),
        ),
        Element(
            key="wetter.monatsvergleich", group="wetter", kind="chart",
            title=lambda: tr("Wetter: Monatsvergleich"),
            available=_wetter_available, build=_wetter_monthly, metrics=METRICS,
        ),
        Element(
            key="wetter.matrix", group="wetter", kind="chart",
            title=lambda: tr("Wetter: Jahresübersicht"),
            available=_wetter_available, build=_wetter_matrix, metrics=MATRIX_METRICS,
        ),
        Element(
            key="wetter.zeitraeume", group="wetter", kind="table",
            title=lambda: tr("Wetter: Zeiträume im Vergleich"),
            available=_wetter_available, build=_wetter_zeitraeume,
            columns=("heute", "gestern", "monat", "vormonat", "jahr"), groups=GROUPS,
        ),
    )
}


def listing(ctx: Context | None = None) -> list[dict]:
    """Die Bausteine, die in dieser App eingerichtet sind (für die Auswahl im Editor der Karte)."""
    ctx = ctx or CONTEXT
    return [
        {"key": element.key, "group": element.group, "kind": element.kind, "title": element.title(), "columns": list(element.columns), "roles": list(element.roles), "groups": list(element.groups), "metrics": list(element.metrics),
        "ranges": list(element.ranges), "sides": list(element.sides)}
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
