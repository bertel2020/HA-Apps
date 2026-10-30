"""Kontext der Dashboard-Kacheln: Zeitraum- und Kennzahlen-Beschriftung einer Werte-Kachel und
das Template-Dictionary für eine ganze Dashboard-Seite (Kacheln, Sektionen, Auswahllisten).

Aus main.py ausgelagert (Zeilenbudget in test_route_modules.py). Index und Zeitzone kommen als
Argumente, damit das Modul keinen Prozesszustand der App importiert."""

from __future__ import annotations

import time
from zoneinfo import ZoneInfo

from . import cleanup_tile, entity_usage, i18n
from .chart_period import calendar_bounds
from .chart_types import effective_chart_type
from .formatting import decimals_to_int, entity_display_name, format_uptime, format_value
from .i18n import N_, tr
from .staleness import staleness as staleness_for, stale_thresholds
from .storage import query as query_mod


# Der Zeitraum-Text einer Werte-Kachel — je Zeitraum eine kalendarische und
# eine rollierende Beschriftung. Der Text ist die einzige Auskunft darüber,
# welche der beiden Varianten läuft; ein zusätzliches Symbol braucht es damit
# nicht. Die rollierenden Namen sind bewusst das TATSÄCHLICHE Fenster aus
# _window() in storage/query.py und nicht "Monat rollierend": dort sind es
# genau 30 Tage, kein Kalendermonat (Begründung steht als Kommentar an der
# Stelle). "30 Tage" macht diese Vereinfachung sichtbar, statt sie unter dem
# Wort "Monat" zu verstecken.
#
# Dieselbe Tabelle steht ein zweites Mal in static/js/dashboard-tiles.js —
# der Server beschriftet die erste Anzeige, der Browser beschriftet nach einer
# Änderung im Kachelmenü neu, ohne die Seite neu zu laden. Ein Test hält beide
# Kopien deckungsgleich (test_dashboard_value_tile_settings.py).
TILE_RANGE_LABELS = {
    "hour": (N_("Std."), N_("60 Min.")),
    "day": (N_("Tag"), N_("24 Std.")),
    "week": (N_("Woche"), N_("7 Tage")),
    "month": (N_("Monat"), N_("30 Tage")),
    "year": (N_("Jahr"), N_("12 Monate")),
}
# Kennzeichen vor der großen Zahl, sobald diese nicht der aktuelle Wert ist —
# ohne das wäre "16,8 °C" nicht von einem Momentanwert zu unterscheiden.
# "last" trägt bewusst keins: der aktuelle Wert ist der Normalfall und braucht
# keine Erklärung.
TILE_METRIC_LABELS = {"last": "", "min": N_("Min"), "avg": "Ø", "max": N_("Max"), "sum": "Σ"}



def tile_metric_labels(aggregation_type: str | None) -> dict[str, str]:
    """Die Kürzel für einen Entitätstyp — genau eines hängt davon ab.

    Bei einem Zähler ist die Summe über den Zeitraum der Zuwachs des
    Zählerstands: „Σ 6,496 kWh" ist rechnerisch richtig und beschreibt einen
    Tagesertrag trotzdem falsch. Beim Schalter bleibt es Σ — dort summiert die
    Kennzahl Einschaltdauer, das ist kein Zuwachs, und „+2h 15m" wäre keine
    bessere Beschreibung. Bei Messwerten ist die Summe gar nicht erst wählbar
    (siehe tile_available_metrics()).
    """
    if aggregation_type == "counter":
        return {**TILE_METRIC_LABELS, "sum": "+"}
    return TILE_METRIC_LABELS


def tile_available_metrics(aggregation_type: str | None) -> list[str]:
    """Welche Kennzahlen für diesen Entitätstyp überhaupt eine Aussage sind.

    Dieselbe Regel wie in der Chart-Legende (``hasSum`` in entity_detail.html)
    und in _tile_aggregates() in api_routes.py, das die nicht angebotenen
    Werte gar nicht erst mitschickt:

    * Summe nur bei Zählern und Schaltern — 20 °C + 21 °C + … ist keine
      Temperatur.
    * Min/Max nicht bei Schaltern — deren Bucket-Werte sind Einschaltsekunden,
      "kleinster Wert" hieße dort "kürzeste Stunde".

    Das Menü zeigt die übrigen durchgestrichen und unklickbar an, statt sie
    wegzulassen: eine Reihe, die je nach Entität mal drei und mal vier Knöpfe
    hat, wirkt wie ein Fehler, ein durchgestrichener Knopf erklärt sich.
    """
    if aggregation_type == "switch":
        return ["sum"]
    if aggregation_type == "counter":
        return ["min", "avg", "max", "sum"]
    if aggregation_type == "state":
        # Zustände: keine Kennzahl über Indizes — die Kachel zeigt Zustand,
        # "seit" und Anteile (ROADMAP 1.29).
        return []
    return ["min", "avg", "max"]


def tile_metric_context(pin, aggregation_type: str | None = None) -> dict:
    """Zeitraum und Kennzahlen einer Werte-Kachel für das Template.

    Der als Hauptwert gewählte Eintrag fällt hier aus der Kennzahlen-Zeile
    heraus — derselbe Wert zweimal auf einer Kachel wäre nur Rauschen. Die
    Nachschläge sind absichtlich fehlertolerant: ein von Hand verbogener
    Datenbankwert soll die Dashboard-Seite nicht mit einem KeyError
    abschießen, sondern auf den Standard zurückfallen.
    """
    range_key = pin["range_key"] if pin["range_key"] in TILE_RANGE_LABELS else "day"
    primary = pin["primary_metric"] if pin["primary_metric"] in TILE_METRIC_LABELS else "last"
    verfuegbar = tile_available_metrics(aggregation_type)
    labels = tile_metric_labels(aggregation_type)
    # Auch gegen den Entitätstyp gefiltert, nicht nur gegen den Hauptwert: ein
    # Wechsel der Entität (dashboard/entity/{id}) lässt die gespeicherten
    # Kennzahlen stehen, und ein Σ, das für einen Messwert gespeichert wurde,
    # soll danach nicht als "–" auf der Kachel kleben bleiben.
    metrics = [
        m for m in (pin["stats_metrics"] or "").split(",")
        if m and m != primary and m in verfuegbar
    ]
    # Eigener Ein-/Ausschalter (Konzept "laufendes Jahr" — Nutzer-Wunsch nach
    # einer Kachel, deren "Jahr"-Etikett ohne Datumszusatz einfach ganz weg
    # soll statt es zu erklären), unabhängig von der Herleitung unten.
    # "in pin.keys()" statt direkter Indizierung: pin ist hier teils ein von
    # Hand gebautes Test-/Vorschau-Dict ohne jede Spalte (siehe Docstring
    # oben, "fehlertolerant") — fehlt die Spalte, gilt der DB-Default (an).
    show_period = bool(pin["show_period"]) if "show_period" in pin.keys() else True
    return {
        "range_key": range_key,
        "continuous": bool(pin["continuous"]),
        "range_label": TILE_RANGE_LABELS[range_key][1 if pin["continuous"] else 0],
        "primary_metric": primary,
        "primary_label": labels[primary],
        # Auch die Kennzahlen-Zeile und das Menü beschriften sich hieraus,
        # damit dasselbe Σ/+ nicht an vier Stellen einzeln entschieden wird.
        "metric_labels": labels,
        "stats_metrics": metrics,
        "show_period": show_period,
        # Der Zeitraum steht genau einmal auf der Kachel: in der
        # Kennzahlen-Zeile, wenn es sie gibt, sonst im Wert-Bereich an der
        # Stelle des Alters. Bei einem Hauptwert, der kein Momentanwert ist,
        # sagt das Alter des letzten Rohpunkts ohnehin nichts über einen
        # Monatsdurchschnitt aus — dort tritt der Zeitraum an seine Stelle.
        "show_period_in_value_row": show_period and not metrics and primary != "last",
        # Fürs Kachelmenü: nicht anwendbare Kennzahlen werden durchgestrichen
        # gezeigt statt weggelassen (siehe tile_available_metrics()).
        "available_metrics": verfuegbar,
    }


def tiles_context(
    index, tz: ZoneInfo, dashboard_id: int, auto_open_pin_id: int | None = None
) -> dict:
    """Für die Dashboard-Kacheln einer Dashboard-Seite (Konzept "Offene
    Punkte", erweitert um Vergleichstabellen UND um mehrere unabhängige
    Dashboards) — sowohl vom initialen Laden von "/"/"/dashboards/{id}" als
    auch von pin/unpin/reorder genutzt (alle geben dasselbe Fragment zurück,
    damit eine Änderung nicht per Extra-Request neu geladen werden muss).
    dashboard_pins kennt zwei item_type-Werte ('chart'/'table'), hier gegen
    die jeweilige Tabelle aufgelöst — ein verwaister Pin (Chart/Tabelle
    zwischenzeitlich gelöscht, sollte durch die Bereinigung in
    delete_saved_chart()/delete_saved_table() praktisch nie vorkommen) wird
    dabei still übersprungen statt einen Fehler zu werfen.

    URLs im Fragment laufen über app_root (Context-Processor, aus dem
    X-Ingress-Path-Header). Vorher stand hier ein relativer Rückweg, den der
    Aufrufer als "base" mitgeben musste — "." auf "/", ".." auf
    "/dashboards/{id}" —, weil dasselbe Fragment auf zwei Seitentiefen
    eingehängt wird und der Server die Tiefe sonst nicht kennt. Bei pin/unpin
    reiste der Wert sogar als Query-Parameter durch die URL. Mit app_root
    entfällt beides: der Präfix ist absolut und tiefenunabhängig (ZG-03)."""
    pins = index.list_dashboard_pins(dashboard_id)
    aggregation_types = {row["entity_id"]: row["aggregation_type"] for row in index.list_entities()}
    existing_ids = entity_usage.existing_entity_ids(index)
    tiles = []
    # Sektionen (item_type='section') gruppieren die Kacheln rein über ihre
    # Position in derselben Liste — keine Kachel trägt eine section_id. Die
    # erste, kopflose Gruppe sammelt alles vor dem ersten Trenner ("ohne
    # Sektion"); jeder weitere Trenner eröffnet eine neue Gruppe mit eigenem
    # Mini-Raster (siehe _dashboard_tiles.html) statt eines gemeinsamen, vollen
    # Rasters — das hält Sektionsköpfe im präzisen Modus kompakt (kein
    # Rasterelement mit fester grid-auto-rows-Höhe) und "Lücken auffüllen" pro
    # Sektion begrenzt.
    groups: list[dict] = [{"title": None, "section_id": None, "tiles": []}]
    for p in pins:
        if p["item_type"] == "section":
            groups.append({"title": p["title"] or "", "section_id": p["id"], "tiles": []})
            continue
        if p["item_type"] == "chart":
            c = index.get_saved_chart(p["item_id"])
            if c is None:
                continue
            tiles.append({
                "kind": "chart", "id": c["id"], "name": c["name"],
                "missing_ids": entity_usage.missing_entity_ids(c["entity_ids"], existing_ids),
                "entity_ids": c["entity_ids"], "range_key": c["range_key"],
                "continuous": c["continuous"], "entity_names": c["entity_names"],
                # Ausgeblendete Serien reichen bis in die Kachel: sie sollen
                # dort weder gezeichnet noch in der Legende gezählt werden.
                # Die Liste (statt einer schon gefilterten entity_ids) geht
                # mit, weil die Farbe an der Position in der VOLLEN Auswahl
                # hängt — sonst wären dieselben Serien auf Chart-Seite und
                # Kachel unterschiedlich eingefärbt.
                "hidden_entity_ids": c["hidden_entity_ids"],
                "resolution_preset": c["resolution_preset"],
                "dynamic_y_axis": c["dynamic_y_axis"],
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
                "show_legend": bool(p["show_legend"]),
                # Kachel-Legende übernimmt Aussehen UND Inhalte 1:1 von der
                # zugrundeliegenden Chart-Seite (chart_editor.html) — welche
                # Kennzahlen (Min/Max/Ø/Summe/Aktuell), ob überhaupt welche
                # gezeigt werden, und Chips vs. Tabelle sind dort konfiguriert,
                # nicht hier erneut.
                "chart_stats": c["chart_stats"], "legend_metrics": c["legend_metrics"],
                "legend_style": c["legend_style"], "chart_type": effective_chart_type(c, aggregation_types),
                "show_values": c["show_values"], "decimals": c["decimals"],
                "average_line": c["average_line"], "area_fill": c["area_fill"], "show_points": c["show_points"],
                "stacked": c["stacked"], "normalize": c["normalize"], "average_style": c["average_style"],
                "horizontal": c["horizontal"], "donut_aggregation": c["donut_aggregation"], "period_anchor": c["period_anchor"],
                "compare_settings": c["compare_settings"],  # Vergleich wie auf der Chart-Seite
                "heatmap_states": c["heatmap_states"],  # gezeichneter Zustand je Entität der Heatmap
                "series_colors": c["series_colors"],  # eigene Serienfarbe wie auf der Chart-Seite
            })
            groups[-1]["tiles"].append(tiles[-1])
        elif p["item_type"] == "table":
            t = index.get_saved_table(p["item_id"])
            if t is None:
                continue
            tiles.append({
                "kind": "table", "id": t["id"], "name": t["name"],
                "missing_ids": entity_usage.missing_entity_ids(entity_usage.table_entity_ids(t), existing_ids),
                "columns": t["columns"], "rows": t["rows"], "style": t["style"],
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
            })
            groups[-1]["tiles"].append(tiles[-1])
        elif p["item_type"] == "cleanup":
            tiles.append(cleanup_tile.build(index, tz, p))
            groups[-1]["tiles"].append(tiles[-1])
        elif p["item_type"] == "entity":
            e = index.get_entity(p["item_entity_id"])
            if e is None:
                continue
            is_switch = e["aggregation_type"] == "switch"
            is_state = e["aggregation_type"] == "state"
            # Kachel-Override hat Vorrang vor der entity-eigenen Einstellung,
            # "auto" (Feld-Default) bedeutet ausdrücklich "entity-eigenen Wert
            # übernehmen" statt selbst "auto" an format_value() zu reichen.
            effective_decimals = p["decimals"] if p["decimals"] != "auto" else (e["decimals"] or "auto")
            metric_context = tile_metric_context(p, e["aggregation_type"])
            if metric_context["primary_metric"] != "last":
                # Der Hauptwert ist eine Aggregation über den Zeitraum, die
                # der Server hier ohne eigene Abfrage nicht kennt. Platzhalter
                # statt des aktuellen Werts: sonst stünde für den Bruchteil
                # einer Sekunde ein sichtbar falscher Wert auf der Kachel
                # (Momentanwert statt Monatsdurchschnitt), bis der erste
                # Fetch ihn ersetzt.
                value_text = "–"
            elif e["last_value"] is None:
                value_text = "–"
            elif is_state:
                # Zustands-Entität: der Text des aktuellen Zustands, wie überall
                # die HA-Übersetzung vor dem Rohwert (ROADMAP 1.29).
                value_text = query_mod.state_text(index, e["entity_id"], e["last_value"])
            elif is_switch:
                # Momentaner Zustand — anders als der chart-eigene
                # display_mode='time' (Summe der Einschaltdauer über einen
                # Zeitraum, siehe dashboard-tiles.js isDuration) geht es hier
                # um "gerade an oder aus", das display_mode nicht berührt.
                value_text = "An" if e["last_value"] else "Aus"
            else:
                value_text = format_value(e["last_value"], decimals_to_int(effective_decimals))
            seconds_ago = (time.time() - e["last_ts"]) if e["last_ts"] is not None else None
            # Zwei Schwellen je Kachel (Stufe stale_mode, siehe staleness.py);
            # Standard ist 15 Minuten (gelb) / eine Stunde (rot), unabhängig
            # vom Auflösungsintervall der Entität. Bewusst feste, für den
            # Nutzer nachvollziehbare Stufen statt einer datenabhängigen
            # Heuristik. Nur der Kartenrahmen zeigt das an (siehe
            # .dtile-entity.is-warn/is-stale), der Wert selbst bleibt immer
            # schwarz.
            # Zustände werden nur beim Wechsel gespeichert — ein seit Tagen
            # unveränderter Zustand ist kein veralteter Wert.
            stale_mode = "off" if is_state else p["stale_mode"]
            staleness = staleness_for(seconds_ago, stale_mode)
            stale_limits = stale_thresholds(stale_mode)
            tiles.append({
                "kind": "entity", "entity_id": e["entity_id"],
                # Eigene Zeilen-ID der Kachel (nicht der Entität!) — macht
                # mehrere Kacheln derselben Entität einzeln identifizierbar
                # (Einstellungen, Umsortieren, Entfernen), siehe
                # pin_entity_to_dashboard() in index.py.
                "pin_id": p["item_id"],
                "name": p["title"] or entity_display_name(e["entity_id"], e["friendly_name"], e["custom_name"]),
                # Roher Override fürs Titel-Eingabefeld im Kachelmenü — anders
                # als "name" oben (mit friendly_name-Fallback) soll das Feld
                # leer bleiben, solange kein eigener Titel gesetzt ist.
                "custom_title": p["title"] or "",
                "value_text": value_text, "unit": "" if is_switch or is_state else (e["unit"] or ""),
                "is_switch": is_switch, "is_state": is_state, "decimals": effective_decimals,
                "state_view": p["state_view"] if is_state else "strip",
                "age_text": tr("vor {format_uptime}", format_uptime=format_uptime(seconds_ago)) if seconds_ago is not None else tr("nie"),
                "staleness": staleness,
                "grid_cols": p["grid_cols"], "grid_rows": p["grid_rows"],
                "show_sparkline": bool(p["show_sparkline"]), "show_age": bool(p["show_age"]),
                "stale_mode": stale_mode,
                "stale_warn_after": stale_limits[0] if stale_limits else "",
                "stale_stale_after": stale_limits[1] if stale_limits else "",
                "sparkline_resolution": p["sparkline_resolution"],
                **metric_context,
            })
            groups[-1]["tiles"].append(tiles[-1])
    # Eine leere kopflose Erstgruppe (alle Kacheln liegen bereits hinter einem
    # Trenner) wird nicht mitgerendert — sonst stünde ein leeres, unbenanntes
    # Mini-Raster über der ersten echten Sektion. Bleibt sie die einzige
    # Gruppe (frisches Dashboard ganz ohne Sektionen/Kacheln), muss sie
    # stehen bleiben, sie trägt dann die "+"-Kachel.
    if not groups[0]["tiles"] and len(groups) > 1:
        groups.pop(0)
    pinned_chart_ids = {p["item_id"] for p in pins if p["item_type"] == "chart"}
    pinned_table_ids = {p["item_id"] for p in pins if p["item_type"] == "table"}
    pinned_entity_ids = {p["item_entity_id"] for p in pins if p["item_type"] == "entity"}
    dashboard = index.get_dashboard(dashboard_id)
    dashboard_locked = bool(dashboard["locked"]) if dashboard else False
    dashboard_precise = bool(dashboard["precise_mode"]) if dashboard else False
    dashboard_fill_gaps = bool(dashboard["fill_gaps"]) if dashboard else False
    entity_rows = index.list_entities()
    all_entities = [
        {
            "entity_id": row["entity_id"],
            "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
            "ha_name": row["friendly_name"] or row["entity_id"],
            "is_custom": bool(row["custom_name"]),
        }
        for row in entity_rows
    ]
    return {
        "dashboard_id": dashboard_id,
        "dashboard_name": (i18n.retranslate(dashboard["name"]) if dashboard["is_default"] else dashboard["name"]) if dashboard else "Dashboard",
        "dashboard_locked": dashboard_locked,
        # Präziser Modus: Gitter/Zeilenhöhe halbiert (.dashboard-grid.is-precise),
        # Größen-Picker geht dann bis 6x6 statt 3x3 (siehe _dashboard_tile_menu.html).
        "dashboard_precise": dashboard_precise,
        # Lücken auffüllen: grid-auto-flow: dense (.dashboard-grid.is-dense) —
        # unabhängig vom Präzisen Modus, beide lassen sich frei kombinieren.
        "dashboard_fill_gaps": dashboard_fill_gaps,
        # Zeitraum-Navigation: Stichtag-Leiste über den Kacheln (_dashboard_period_nav.html),
        # der Kalender reicht über das ganze Archiv wie im Chart-Editor.
        "dashboard_period_nav": bool(dashboard["period_nav"]) if dashboard else False, **calendar_bounds(entity_rows, tz),
        "groups": groups,
        "auto_open_pin_id": auto_open_pin_id,
        # "pinned" ist rein informativ (Hinweis im Picker) — eine bereits
        # angeheftete Entität lässt sich seit dem Mehrfach-Anheften-Feature
        # trotzdem erneut wählen, statt ausgeschlossen zu werden.
        "entity_pin_options": [
            {**row, "pinned": row["entity_id"] in pinned_entity_ids}
            for row in all_entities
        ],
        # Fixiertes Dashboard: keine neuen Kacheln anheften, siehe dashboard_locked
        # in _dashboard_tiles.html/_dashboard_tile_menu.html für die restlichen
        # Layout-Aktionen (Größe/Entfernen/Umsortieren).
        "can_add_tile": len(tiles) < index.DASHBOARD_TILE_LIMIT and not dashboard_locked,
        "unpinned_charts": [c for c in index.list_saved_charts() if c["id"] not in pinned_chart_ids],
        "unpinned_tables": [t for t in index.list_saved_tables() if t["id"] not in pinned_table_ids],
    }
