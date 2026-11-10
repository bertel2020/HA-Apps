"""Auswahl-Kacheln der Übersichtsseite und die Einstellung für ihre Statistik-Kacheln.

Die Übersicht zeigt oben eine Reihe von Kacheln, die zu den Seiten der App führen (Dashboards, Charts,
Tabellen, …). Welche das sind und in welcher Reihenfolge, steht in der Einstellung ``overview_tiles``
(JSON-Liste der aktiven Schlüssel). Ohne gespeicherte Auswahl gilt eine Standardliste.

Die vier Statistik-Kacheln (Entitäten, Datensätze, Größe, Status) sind optional: ``overview_stats`` kennt
„alert“ (nur ein schmaler Hinweisstreifen, wenn etwas offen ist), „always“ (wie früher) und „off“.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from zoneinfo import ZoneInfo

from . import cleanup_tile
from .energiedashboard_routes import energiedashboard_role_count, is_energiedashboard_configured
from .formatting import format_int
from .i18n import N_, tr
from .storage.index import Index
from .wetter_routes import is_wetter_configured, wetter_role_count

#: Obergrenze der aktiven Kacheln: Es soll eine Auswahl bleiben, keine zweite Navigationsleiste.
MAX_TILES = 10

STATS_MODE_LABELS = {
    "alert": N_("Nur bei Auffälligkeit"),
    "always": N_("Immer"),
    "off": N_("Aus"),
}

SETTING_STATS = "overview_stats"
SETTING_TILES = "overview_tiles"

DASHBOARD_PREFIX = "dashboard:"

#: Feste Ziele: Schlüssel -> (Titel, Seite, Symbol, Farbe 1, Farbe 2). Die Seite ist zugleich der relative Link.
_FIXED: dict[str, tuple[str, str, str, str, str]] = {
    "energiedashboard": (N_("Energiedashboard"), "energiedashboard", "bolt", "var(--energy-solar)", "var(--energy-export)"),
    "wetterdashboard": (N_("Wetterdashboard"), "wetterdashboard", "cloud", "var(--chart-3)", "var(--chart-1)"),
    "charts": (N_("Charts"), "charts", "charts", "var(--chart-1)", "var(--chart-2)"),
    "tables": (N_("Tabellen"), "tables", "tables", "var(--chart-2)", "var(--chart-5)"),
    "entities": (N_("Entitäten"), "entities", "entities", "var(--chart-5)", "var(--chart-4)"),
    "statistik": (N_("Statistik"), "statistik", "statistik", "var(--chart-8)", "var(--chart-3)"),
    "housekeeping": (N_("Housekeeping"), "housekeeping", "housekeeping", "var(--chart-7)", "var(--chart-6)"),
    "import": (N_("Import"), "import", "import", "var(--chart-6)", "var(--chart-1)"),
    "export": (N_("Export"), "export", "export", "var(--chart-4)", "var(--chart-2)"),
}

#: Reihenfolge der festen Ziele im Katalog; die eigenen Dashboards stehen nach den beiden Dashboard-Seiten.
_FIXED_ORDER = ("energiedashboard", "wetterdashboard")
_FIXED_AFTER = ("charts", "tables", "entities", "statistik", "housekeeping", "import", "export")
#: In der Standardliste reserviert: nach den Dashboards immer diese vier.
_DEFAULT_TAIL = ("charts", "tables", "entities", "statistik")

#: Farbpaare der eigenen Dashboards, reihum nach Position.
_DASHBOARD_COLORS = (
    ("var(--chart-4)", "var(--chart-3)"), ("var(--chart-6)", "var(--chart-7)"),
    ("var(--chart-1)", "var(--chart-5)"), ("var(--chart-2)", "var(--chart-4)"),
)


def _plural(count: int, one: str, many: str) -> str:
    return tr(one if count == 1 else many, count=format_int(count))


def _catalog(index: Index) -> list[dict]:
    """Alle möglichen Kacheln in Katalogreihenfolge. ``available`` ist falsch, wenn das Ziel ausgeschaltet ist
    (Energie- und Wetterdashboard sind abschaltbar); solche Einträge bleiben wählbar, erscheinen aber erst,
    wenn das Dashboard wieder an ist."""
    available = {
        "energiedashboard": index.get_setting("energiedashboard_enabled", "0") == "1",
        "wetterdashboard": index.get_setting("wetterdashboard_enabled", "0") == "1",
    }
    rows: list[dict] = []
    for key in _FIXED_ORDER:
        rows.append({"key": key, "label": tr(_FIXED[key][0]), "available": available[key], "note": tr("nur wenn aktiviert")})
    for position, dash in enumerate(d for d in index.list_dashboards() if not d["is_default"]):
        rows.append({"key": f"{DASHBOARD_PREFIX}{dash['id']}", "label": dash["name"], "available": True,
                     "note": tr("eigenes Dashboard"), "position": position, "id": dash["id"]})
    for key in _FIXED_AFTER:
        rows.append({"key": key, "label": tr(_FIXED[key][0]), "available": True, "note": ""})
    return rows


def _default_keys(catalog: list[dict]) -> list[str]:
    """Energie, Wetter (nur wenn an), dann so viele eigene Dashboards, wie neben den vier festen Seiten
    am Ende noch in die Obergrenze passen."""
    head = [row["key"] for row in catalog if row["key"] in _FIXED_ORDER and row["available"]]
    dashboards = [row["key"] for row in catalog if row["key"].startswith(DASHBOARD_PREFIX)]
    room = max(0, MAX_TILES - len(head) - len(_DEFAULT_TAIL))
    return head + dashboards[:room] + list(_DEFAULT_TAIL)


def selected_keys(index: Index, catalog: list[dict] | None = None) -> list[str]:
    """Die aktiven Schlüssel in Anzeigereihenfolge. Unbekannte (gelöschte Dashboards) und doppelte
    Einträge fallen weg, eine unlesbare Einstellung gilt als nicht gesetzt."""
    catalog = _catalog(index) if catalog is None else catalog
    known = {row["key"] for row in catalog}
    raw = index.get_setting(SETTING_TILES)
    if raw is None:
        return _default_keys(catalog)
    try:
        stored = json.loads(raw)
    except ValueError:
        return _default_keys(catalog)
    if not isinstance(stored, list):
        return _default_keys(catalog)
    keys: list[str] = []
    for key in stored:
        if isinstance(key, str) and key in known and key not in keys:
            keys.append(key)
    return keys[:MAX_TILES]


def _save(index: Index, keys: list[str]) -> None:
    index.set_setting(SETTING_TILES, json.dumps(keys))


def settings_rows(index: Index) -> list[dict]:
    """Zeilen der Liste in Einstellungen → Darstellung: zuerst die aktiven in ihrer Reihenfolge, danach
    die übrigen in Katalogreihenfolge."""
    catalog = _catalog(index)
    by_key = {row["key"]: row for row in catalog}
    on = selected_keys(index, catalog)
    rows = [dict(by_key[key], on=True) for key in on]
    rows += [dict(row, on=False) for row in catalog if row["key"] not in on]
    for position, row in enumerate(rows):
        row["can_up"] = row["on"] and position > 0
        row["can_down"] = row["on"] and position < len(on) - 1
        row["color"] = _colors(row["key"], row.get("position", 0))[0]
    return rows


def settings_context(index: Index) -> dict:
    rows = settings_rows(index)
    return {
        "overview_stats": stats_mode(index),
        "overview_stats_options": list(STATS_MODE_LABELS.items()),
        "overview_tile_rows": rows,
        "overview_tile_count": sum(1 for row in rows if row["on"]),
        "overview_tile_max": MAX_TILES,
    }


def apply_form(index: Index, form: Mapping[str, str]) -> None:
    """Verarbeitet die drei Felder des Darstellungsformulars. Wirft ``ValueError`` mit einem anzeigbaren
    Text bei ungültiger Eingabe; was nicht im Formular steht, bleibt unberührt."""
    mode = form.get(SETTING_STATS)
    if mode is not None:
        if mode not in STATS_MODE_LABELS:
            raise ValueError(tr("Ungültige Einstellung für die Statistik-Kacheln"))
        index.set_setting(SETTING_STATS, mode)

    catalog = _catalog(index)
    known = {row["key"] for row in catalog}
    toggle = form.get("overview_tile_toggle")
    if toggle is not None:
        if toggle not in known:
            raise ValueError(tr("Ungültige Kachel"))
        keys = selected_keys(index, catalog)
        if toggle in keys:
            keys.remove(toggle)
        elif len(keys) >= MAX_TILES:
            raise ValueError(tr("Höchstens {count} Kacheln", count=MAX_TILES))
        else:
            keys.append(toggle)
        _save(index, keys)

    move = form.get("overview_tile_move")
    if move is not None:
        key, _, direction = move.rpartition(":")
        keys = selected_keys(index, catalog)
        if key not in keys or direction not in ("up", "down"):
            raise ValueError(tr("Ungültige Kachel"))
        at = keys.index(key)
        to = at - 1 if direction == "up" else at + 1
        if 0 <= to < len(keys):
            keys[at], keys[to] = keys[to], keys[at]
            _save(index, keys)


def stats_mode(index: Index) -> str:
    mode = index.get_setting(SETTING_STATS, "alert")
    return mode if mode in STATS_MODE_LABELS else "alert"


def ensure_defaults(index: Index) -> None:
    """Beim Start: Eine Installation, die schon Entitäten hat, bevor es die Einstellung gab, behält ihre
    Statistik-Kacheln („always“). Eine neue Installation (noch keine Entität) bekommt „alert“. Der Wert wird
    gespeichert, damit er sich nicht mehr ändert, wenn später Entitäten dazukommen."""
    if index.get_setting(SETTING_STATS) is not None:
        return
    index.set_setting(SETTING_STATS, "always" if index.get_overview()["entity_count"] else "alert")


def _colors(key: str, position: int) -> tuple[str, str]:
    if key in _FIXED:
        return _FIXED[key][3], _FIXED[key][4]
    return _DASHBOARD_COLORS[position % len(_DASHBOARD_COLORS)]


def _meta(index: Index, key: str, dash_id: int | None, total_rows: str, total_size: str) -> str:
    if key == "energiedashboard":
        count = energiedashboard_role_count(index)
        if is_energiedashboard_configured(index):
            return _plural(count, "{count} Rolle zugeordnet", "{count} Rollen zugeordnet")
        return tr("Noch nicht eingerichtet")
    if key == "wetterdashboard":
        if is_wetter_configured(index):
            return _plural(wetter_role_count(index), "{count} Rolle zugeordnet", "{count} Rollen zugeordnet")
        return tr("Noch nicht eingerichtet")
    if dash_id is not None:
        return _plural(index.count_dashboard_pins(dash_id), "{count} Kachel", "{count} Kacheln")
    if key == "charts":
        return tr("{count} gespeichert", count=format_int(index.count_saved_charts()))
    if key == "tables":
        return tr("{count} gespeichert", count=format_int(index.count_saved_tables()))
    if key == "entities":
        return _plural(index.get_overview()["entity_count"], "{count} Entität", "{count} Entitäten")
    if key == "statistik":
        return tr("{rows} Datensätze · {size}", rows=total_rows, size=total_size)
    return {
        "housekeeping": tr("Aufräumen und prüfen"),
        "import": tr("Daten übernehmen"),
        "export": tr("Daten als CSV"),
    }[key]


def page_tiles(index: Index, total_rows: str, total_size: str) -> list[dict]:
    """Die Kacheln der Übersicht in Anzeigereihenfolge. Ausgeschaltete Dashboards fehlen."""
    catalog = _catalog(index)
    by_key = {row["key"]: row for row in catalog}
    tiles = []
    for key in selected_keys(index, catalog):
        row = by_key[key]
        if not row["available"]:
            continue
        dash_id = row.get("id")
        c1, c2 = _colors(key, row.get("position", 0))
        if dash_id is not None:
            href, icon = f"dashboards/{dash_id}", "dashboards"
        else:
            href, icon = _FIXED[key][1], _FIXED[key][2]
        tiles.append({
            "key": key, "label": row["label"], "href": href, "icon": icon, "color": c1, "color2": c2,
            "meta": _meta(index, key, dash_id, total_rows, total_size),
        })
    return tiles


def alert(index: Index, tz: ZoneInfo) -> dict | None:
    """Der Hinweisstreifen: nur, wenn etwas offen ist. Inaktive Entitäten, Zählerrückgänge und doppelte
    Zeitstempel lösen ihn aus; markierte Werte stehen nur dabei (sie können Absicht sein und warten
    auf die automatische Löschung)."""
    decreases, duplicates, marked = (row["count"] for row in cleanup_tile.items(index, tz))
    inactive = cleanup_tile.inactive_entities(index)
    if not (inactive or decreases or duplicates):
        return None
    parts = []
    if inactive:
        parts.append(_plural(inactive, "{count} inaktive Entität", "{count} inaktive Entitäten"))
    if decreases:
        parts.append(_plural(decreases, "{count} Zählerrückgang", "{count} Zählerrückgänge"))
    if duplicates:
        parts.append(_plural(duplicates, "{count} doppelter Zeitstempel", "{count} doppelte Zeitstempel"))
    if marked:
        parts.append(_plural(marked, "{count} markierter Wert", "{count} markierte Werte"))
    return {"parts": parts, "link": "housekeeping"}


def page_context(index: Index, tz: ZoneInfo, total_rows: str, total_size: str) -> dict:
    mode = stats_mode(index)
    return {
        "overview_stats": mode,
        "overview_alert": alert(index, tz) if mode == "alert" else None,
        "overview_tiles": page_tiles(index, total_rows, total_size),
    }
