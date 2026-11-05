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
#: Kennzahlen der Energie-Kachelreihe (Schlüssel der KPIs in compute_flow) und wie viele eine Karte zeigt.
KPI_VALUES = ("erzeugung", "verbrauch", "netzbezug", "einspeisung", "autarkie", "eigenverbrauch", "speicher")
#: Was eine Karte ohne Auswahl zeigt: die vier Energiemengen; Quoten und Speicher wählt man dazu.
KPI_DEFAULT = KPI_VALUES[:4]
#: Quoten in Prozent (die Veränderung steht in Prozentpunkten, nicht in Prozent).
KPI_QUOTAS = ("autarkie", "eigenverbrauch")
#: Ob ein größerer Wert günstig ist (up) oder ungünstig (down): mehr Erzeugung und Einspeisung ja, mehr Bezug und Verbrauch nein.
KPI_BETTER = {"erzeugung": "up", "einspeisung": "up", "verbrauch": "down", "netzbezug": "down", "autarkie": "up", "eigenverbrauch": "up", "speicher": None}
MAX_VALUES = 8
#: Verbraucher der Veränderungsliste: Vorgabe (energie_langzeit.CHANGES_LIMIT) und Höchstzahl.
CHANGES_DEFAULT = 8
MAX_LIMIT = 20


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
    values: list[str] | None = None
    sparkline: bool = True
    limit: int = CHANGES_DEFAULT


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
    #: Die Werte, aus denen der Baustein wählen lässt: Schlüssel (Energie, fest) oder dazu Beschriftung (Wetter, nach Einrichtung).
    values: Callable[[Context], list[dict]] | None = None
    #: Ob der Baustein die Wahl „bis heute / komplett“ (mode) und die Zahl der Jahre (years) anbietet.
    modes: bool = False
    year_limit: bool = False
    #: Ob der Baustein die Zahl der Zeilen (limit) wählen lässt.
    limit: bool = False


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


def _energie_records(ctx: Context, options: Options) -> dict:
    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    data = ctx.energie.langzeit.compute_records(config)
    records = [{key: record[key] for key in ("key", "label", "value", "date")} for record in data["records"]]
    return {"renderer": "energie.rekorde", "records": records, "enough": data["enough"], "since": data["since"], "unit": data["unit"]}


def _energie_kenntage(ctx: Context, options: Options) -> dict:
    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    kenntage = ctx.energie.langzeit.compute_records(config)["kenntage"]
    # Nur die gewählten Jahre gehen an die Karte (neueste zuerst; 0 = alle).
    years = kenntage["years"][: options.years] if options.years else kenntage["years"]
    return {"renderer": "energie.kenntage", "kenntage": {**kenntage, "years": years}, "ytd": options.mode != "komplett"}


def _energie_changes(ctx: Context, options: Options) -> dict:
    from . import energie_langzeit

    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    body = energie_langzeit.TableRequest(mode=options.mode if options.mode in MODES else "bis_jetzt", at=options.at)
    limit = max(1, min(options.limit, MAX_LIMIT))
    return {"renderer": "energie.veraenderungen", "changes": ctx.energie.langzeit.compute_changes(config, body, limit=limit)}


def _sparkline(tile: dict) -> list[float | None]:
    """Der Verlauf einer Kennzahl aus den Kachel-Daten des Dashboards: bei Netzbezug und Einspeisung die eine Serie, sonst
    die Summe aller Zeilen je Achsenposition (fehlende Buckets bleiben eine Lücke, wenn keine Zeile einen Wert hat)."""
    series = [item["values"] for item in tile.get("series") or [] if item.get("values")]
    if not series:
        return []
    columns = zip(*series, strict=False)
    return [None if all(value is None for value in column) else round(sum(value or 0.0 for value in column), 3) for column in columns]


def _quota_item(key: str, label: str, current: dict, previous: dict) -> dict:
    """Autarkie und Eigenverbrauch: Prozent, die Veränderung in Prozentpunkten zur Vorperiode."""
    value, before = current.get(key), previous.get(key)
    change = round(value - before, 1) if value is not None and before is not None else None
    return {"key": key, "label": label, "unit": "%", "value": value, "pct": None, "abs": change, "delta_unit": tr("PP"),
            "better": KPI_BETTER[key], "spark": []}


def _storage_item(label: str, entry: dict, current: dict, offset: int) -> dict:
    """Speicher: der Saldo (Ladung minus Entladung) mit seiner Veränderung, darunter Ladestand, Ladung und Entladung.
    Der Ladestand ist der jetzige in der laufenden Periode, sonst der Durchschnitt der Periode."""
    now = offset == 0
    soc = current.get("speicher_soc_now") if now else current.get("speicher_soc")
    lines = []
    if soc is not None:
        lines.append({"label": tr("Ladestand") if now else tr("Ø Ladestand"), "value": soc, "unit": "%"})
    for text, key in ((tr("Ladung"), "speicher_laden"), (tr("Entladung"), "speicher_entladen")):
        if current.get(key) is not None:
            lines.append({"label": text, "value": current[key], "unit": "kWh"})
    return {"key": "speicher", "label": label, "unit": "kWh", "value": current.get("speicher_netto"), "pct": None,
            "abs": entry.get("abs"), "better": None, "spark": [], "lines": lines}


def _energie_storage_available(ctx: Context) -> bool:
    config = _energie_config(ctx)
    return config is not None and bool(config.get("speicher"))


def _energie_storage(ctx: Context, options: Options) -> dict:
    """Die größere Speicher-Karte: Saldo, Ladestand, Ladung und Entladung wie die Kennzahl, dazu bei einem Tag die Kurve
    des Ladestands (nur dort ist sie ein Verlauf; bei Monat und Jahr wäre es ein grober Mittelwert je Bucket)."""
    from .energiedashboard_routes import COMPARE_LABELS
    from .storage import query as query_mod

    config = _energie_config(ctx)
    if config is None or not config.get("speicher"):
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    range_key = options.range if options.range in ("day", "month", "year") else "day"
    offset = min(0, options.offset)
    energie = ctx.energie
    read_cache = query_mod.QueryReadCache()
    flow = energie.compute_flow(config, range_key, offset, skip_quality=True, read_cache=read_cache)
    compare, current, _previous = energie.compute_period_comparison(config, range_key, offset, flow, read_cache)
    item = _storage_item(tr("Speicher"), compare.get("speicher_netto") or {}, current, offset)
    soc = []
    if range_key == "day" and options.sparkline:
        soc = (energie.compute_tile(config, "speicher", range_key, offset, read_cache).get("soc")) or []
    now = offset == 0
    level = current.get("speicher_soc_now") if now else current.get("speicher_soc")
    return {"renderer": "energie.speicher", "item": item, "level": level, "soc": soc, "range": range_key,
            "compare_label": tr(COMPARE_LABELS[range_key])}


def _energie_kpis(ctx: Context, options: Options) -> dict:
    from .energiedashboard_routes import COMPARE_LABELS
    from .storage import query as query_mod

    config = _energie_config(ctx)
    if config is None:
        raise ElementUnavailable(tr("Im Energiedashboard nicht eingerichtet"))
    range_key = options.range if options.range in ("day", "month", "year") else "day"
    offset = min(0, options.offset)
    keys = [key for key in dict.fromkeys(options.values or KPI_DEFAULT) if key in KPI_VALUES][:MAX_VALUES] or list(KPI_DEFAULT)
    if not config.get("speicher"):
        keys = [key for key in keys if key != "speicher"] or list(KPI_DEFAULT)
    energie = ctx.energie
    read_cache = query_mod.QueryReadCache()
    flow = energie.compute_flow(config, range_key, offset, skip_quality=True, read_cache=read_cache)
    compare, current, previous = energie.compute_period_comparison(config, range_key, offset, flow, read_cache)
    labels = {
        "erzeugung": tr("Erzeugung"), "verbrauch": tr("Verbrauch"), "netzbezug": tr("Netzbezug"), "einspeisung": tr("Einspeisung"),
        "autarkie": tr("Autarkie"), "eigenverbrauch": tr("Eigenverbrauch"), "speicher": tr("Speicher"),
    }
    items = []
    for key in keys:
        if key in KPI_QUOTAS:
            items.append(_quota_item(key, labels[key], current, previous))
        elif key == "speicher":
            items.append(_storage_item(labels[key], compare.get("speicher_netto") or {}, current, offset))
        else:
            entry = compare.get(key) or {}
            item = {
                "key": key, "label": labels[key], "unit": "kWh", "value": current.get(key), "pct": entry.get("pct"),
                "abs": entry.get("abs"), "better": KPI_BETTER[key], "spark": [],
            }
            if options.sparkline:
                item["spark"] = _sparkline(energie.compute_tile(config, key, range_key, offset, read_cache))
            items.append(item)
    return {"renderer": "energie.kennzahlen", "items": items, "range": range_key,
            "compare_label": tr(COMPARE_LABELS[range_key])}


def _wetter_kpi_values(ctx: Context) -> list[dict]:
    if not _wetter_available(ctx):
        return []
    from . import wetter_routes

    config = wetter_routes._load_config(ctx.index)  # noqa: SLF001 - wie die Seite
    return [{"key": spec["key"], "label": spec["label"]} for spec in ctx.wetter._row_specs(config)]  # noqa: SLF001


def _wetter_kpis(ctx: Context, options: Options) -> dict:
    if not _wetter_available(ctx):
        raise ElementUnavailable(tr("Im Wetterdashboard nicht eingerichtet"))
    tiles = ctx.wetter.compute_tiles(at=options.at)
    wanted = [key for key in dict.fromkeys(options.values or []) if key][:MAX_VALUES]
    if wanted:
        by_key = {tile["key"]: tile for tile in tiles}
        tiles = [by_key[key] for key in wanted if key in by_key]
    return {"renderer": "wetter.kennzahlen", "tiles": tiles}


def _energie_kpi_values(ctx: Context) -> list[dict]:
    config = _energie_config(ctx) or {}
    labels = {
        "erzeugung": tr("Erzeugung"), "verbrauch": tr("Verbrauch"), "netzbezug": tr("Netzbezug"), "einspeisung": tr("Einspeisung"),
        "autarkie": tr("Autarkie"), "eigenverbrauch": tr("Eigenverbrauch"), "speicher": tr("Speicher"),
    }
    # "default": was die Karte ohne Auswahl zeigt; der Speicher nur, wenn in der App einer eingerichtet ist.
    return [{"key": key, "label": labels[key], "default": key in KPI_DEFAULT} for key in KPI_VALUES if key != "speicher" or config.get("speicher")]


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
            key="energie.kennzahlen", group="energie", kind="kpi",
            title=lambda: tr("Energie: Kennzahlen"),
            available=_energie_zeitraeume_available, build=_energie_kpis, ranges=("day", "month", "year"), values=_energie_kpi_values,
        ),
        Element(
            key="energie.speicher", group="energie", kind="kpi",
            title=lambda: tr("Energie: Speicher"),
            available=_energie_storage_available, build=_energie_storage, ranges=("day", "month", "year"),
        ),
        Element(
            key="energie.rekorde", group="energie", kind="kpi",
            title=lambda: tr("Energie: Rekorde"),
            available=_energie_zeitraeume_available, build=_energie_records,
        ),
        Element(
            key="energie.kenntage", group="energie", kind="table",
            title=lambda: tr("Energie: Kenntage"),
            available=_energie_zeitraeume_available, build=_energie_kenntage, modes=True, year_limit=True,
        ),
        Element(
            key="energie.veraenderungen", group="energie", kind="table",
            title=lambda: tr("Energie: Was hat sich verändert?"),
            available=_energie_zeitraeume_available, build=_energie_changes, modes=True, limit=True,
        ),
        Element(
            key="wetter.kennzahlen", group="wetter", kind="kpi",
            title=lambda: tr("Wetter: Kennzahlen"),
            available=_wetter_available, build=_wetter_kpis, values=_wetter_kpi_values,
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
        "ranges": list(element.ranges), "sides": list(element.sides),
        "values": element.values(ctx) if element.values else [],
        "modes": element.modes, "year_limit": element.year_limit, "limit": element.limit,
    }
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
