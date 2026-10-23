"""Diagrammtypen gespeicherter Charts und der Entitätsseite.

Aus main.py herausgelöst: reine Regeln ohne Zugriff auf die App oder den Index —
sie bekommen die Aggregationstypen der Entitäten übergeben. Gespeichert ist je
Chart ein Typ aus CHART_EDITOR_CHART_TYPES, gezeichnet wird, was
effective_chart_type() daraus macht.
"""

from __future__ import annotations

from .i18n import N_

# "timeline" nur clientseitig erzwingbar, wenn tatsächlich alle Serien
# Schalter oder Zustände sind (siehe allSwitch-Getter in chart_editor.js) — hier nur
# generell als gültiger Wert zugelassen, dieselbe Konvention wie
# ENTITY_CHART_TYPES unten. "donut" ist die dritte, chart-weite
# Darstellungsart (Optionen-Menü, "Darstellungsart") — ein Anteil je Serie
# statt eines Zeitverlaufs, siehe chart_editor.js renderDonut().
# "shares"/"changes": Anteile bzw. Wechsel je Zustands-Entität als gestapelte
# Balken (nur Charts aus lauter Zuständen), siehe state-entities.js barPanels().
# "heatmap": Zeit je Zustand nach Wochentag und Stunde, ebenfalls nur für
# Zustände und erst ab dem Zeitraum Monat, siehe state-entities.js heatmapPanels().
CHART_EDITOR_CHART_TYPES = {"auto", "bar", "timeline", "donut", "shares", "changes", "heatmap"}

# Anteile, Wechsel und Heatmap: die Darstellungen, die es nur für Zustände gibt.
STATE_VIEW_CHART_TYPES = ("shares", "changes", "heatmap")

# Entitätsseite (entity_detail.html): zusätzlich "line"/"bar" als ausdrückliche Wahl.
ENTITY_CHART_TYPES = {"auto", "line", "bar", "timeline"}

# Kennung -> Beschriftung. Die Kennung ist die Grundform, nicht das Label: sie
# trägt auf /charts zusätzlich die Kachel-Klasse für den Kopfstreifen je Typ
# (siehe .is-typ-* in pages/charts.css). Aus dem zusammengesetzten Label
# "Linie + Balken" im Template eine Klasse abzuleiten wäre der umgekehrte,
# brüchige Weg.
CHART_TYPE_LABELS = {
    "zeitstrahl": N_("Zeitstrahl"),
    "linie": N_("Linie"),
    "balken": N_("Balken"),
    "gemischt": N_("Linie + Balken"),
    "donut": N_("Donut"),
    "anteile": N_("Anteile"),
    "wechsel": N_("Wechsel"),
    "heatmap": N_("Heatmap"),
}


def effective_chart_type(chart: dict, aggregation_types: dict[str, str]) -> str:
    """Löst "auto" zum tatsächlich gezeichneten Typ auf: Besteht das Chart nur
    aus Schaltern und Zuständen, ist der Zeitstrahl der Standard. "bar" ist die gespeicherte
    ausdrückliche Wahl "kein Zeitstrahl" (der Editor schreibt es, sobald ein
    reines Schalter-Chart den Zeitstrahl abwählt) und bleibt hier unverändert —
    sonst würde "auto" diese Wahl beim nächsten Laden wieder überstimmen."""
    stored = chart["chart_type"]
    # Anteile/Wechsel gibt es nur für Charts aus lauter Zuständen; ein Chart, in
    # das später etwas anderes kam, wird wie "auto" behandelt, statt Zahlen als
    # Zustandsbalken zu zeichnen.
    if stored in STATE_VIEW_CHART_TYPES and not all(aggregation_types.get(e) == "state" for e in chart["entity_ids"]):
        stored = "auto"
    ids = chart["entity_ids"]
    # Nur Zustände und nichts gewählt: "auto" bleibt, die Ansicht hängt vom
    # Zeitraum ab (Zeitstrahl bis zur Woche, danach Anteile — Editor und Kachel
    # entscheiden das selbst, state-entities.js defaultMode()).
    if stored == "auto" and ids and all(aggregation_types.get(e) == "state" for e in ids):
        return "auto"
    if stored == "auto" and ids and all(
        aggregation_types.get(e) in ("switch", "state") for e in ids
    ):
        return "timeline"
    # Zustände gibt es sonst nur als Zeitstrahl oder Donut (chart_editor.js load()).
    if stored not in ("donut", *STATE_VIEW_CHART_TYPES) and any(aggregation_types.get(e) == "state" for e in chart["entity_ids"]) and all(
        aggregation_types.get(e) in ("switch", "state") for e in chart["entity_ids"]
    ):
        return "timeline"
    return stored


def chart_type_key(chart: dict, aggregation_types: dict[str, str]) -> str:
    """Wie das Chart tatsächlich gezeichnet wird, für die Übersichtskachel.

    Gespeichert ist "auto", "timeline" oder "donut" — bei "auto" entscheidet
    der Aggregationstyp JEDER Entität einzeln (Zähler/Schalter → Balken, sonst
    Linie, dieselbe Regel wie _resolved_chart_type() in storage/query.py).
    Ein Chart kann deshalb beides zugleich enthalten. Ein Chart ohne
    Entitäten hat keinen Typ — leere Kennung, Kachel ohne Streifen."""
    chart_type = effective_chart_type(chart, aggregation_types)
    if chart_type == "timeline" or (
        chart_type == "auto" and chart["entity_ids"] and all(aggregation_types.get(e) == "state" for e in chart["entity_ids"])
    ):
        return "zeitstrahl"
    if chart_type == "donut":
        return "donut"
    if chart_type in STATE_VIEW_CHART_TYPES:
        return {"shares": "anteile", "changes": "wechsel", "heatmap": "heatmap"}[chart_type]
    vorhanden = {
        "balken" if aggregation_types.get(entity_id) in ("counter", "switch") else "linie"
        for entity_id in chart["entity_ids"]
    }
    return "gemischt" if len(vorhanden) > 1 else next(iter(vorhanden), "")


def chart_type_label(chart: dict, aggregation_types: dict[str, str]) -> str:
    return CHART_TYPE_LABELS.get(chart_type_key(chart, aggregation_types), "")


_FLAGS = ("show_points", "show_values", "dynamic_y_axis", "average_line", "chart_stats", "area_fill")


def editor_display_prefill(params, metrics: set[str], styles: set[str], decimals: set[str]) -> dict:
    """Darstellungsoptionen, die "Als Chart speichern" von der Entitätsseite in
    den Editor mitgibt. Nur gültige Werte kommen durch: ein fehlender oder
    kaputter Parameter lässt den Editor bei seinem Standard, statt die Seite
    mit 400 abzulehnen — der Link ist nur eine Vorbelegung."""
    out: dict = {}
    if params.get("chart_type") in CHART_EDITOR_CHART_TYPES:
        out["chart_type"] = params["chart_type"]
    if params.get("decimals") in decimals:
        out["decimals"] = params["decimals"]
    if params.get("legend_style") in styles:
        out["legend_style"] = params["legend_style"]
    if params.get("average_style") in ("flat", "rolling"):
        out["average_style"] = params["average_style"]
    chosen = [m for m in params.get("legend_metrics", "").split(",") if m in metrics]
    if chosen:
        out["legend_metrics"] = chosen
    for flag in _FLAGS:
        if params.get(flag) in ("true", "false"):
            out[flag] = params[flag] == "true"
    # Gezeichneter Zustand der Heatmap von der Entitätsseite (nur für diese Entität).
    entity_id = params.get("entity_id", "")
    out["heatmap_states"] = heatmap_states_clean({entity_id: params.get("heatmap_state", "")}, [entity_id])
    return out


# Länge eines Zustandstextes, wie ihn Home Assistant liefert (255 Zeichen
# Zustandslänge) — darüber ist es kein Zustand.
_HEATMAP_STATE_MAX_LENGTH = 255


def heatmap_states_clean(value: dict | None, entity_ids: list[str]) -> dict[str, str]:
    """Gezeichneter Zustand je Entität der Heatmap (saved_charts.heatmap_states).

    Nur Einträge zu Entitäten, die im Chart stehen, mit nichtleerem Text: ein
    Eintrag ohne Entsprechung bliebe sonst stumm mitgespeichert, bis die
    Entität irgendwann wieder auftaucht. Kein Fehler bei Unpassendem — die
    Heatmap fällt dann auf ihre Vorbelegung zurück, es geht nichts verloren."""
    allowed = set(entity_ids)
    return {
        entity_id: text
        for entity_id, text in (value or {}).items()
        if entity_id in allowed and isinstance(text, str) and 0 < len(text) <= _HEATMAP_STATE_MAX_LENGTH
    }
