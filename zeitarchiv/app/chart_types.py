"""Diagrammtypen gespeicherter Charts und der Entitätsseite.

Aus main.py herausgelöst: reine Regeln ohne Zugriff auf die App oder den Index —
sie bekommen die Aggregationstypen der Entitäten übergeben. Gespeichert ist je
Chart ein Typ aus CHART_EDITOR_CHART_TYPES, gezeichnet wird, was
effective_chart_type() daraus macht.
"""

from __future__ import annotations

from .i18n import N_

# "timeline" nur clientseitig erzwingbar, wenn tatsächlich alle Serien
# Schalter sind (siehe allSwitch-Getter in chart_editor.html) — hier nur
# generell als gültiger Wert zugelassen, dieselbe Konvention wie
# ENTITY_CHART_TYPES unten. "donut" ist die dritte, chart-weite
# Darstellungsart (Optionen-Menü, "Darstellungsart") — ein Anteil je Serie
# statt eines Zeitverlaufs, siehe chart_editor.js renderDonut().
CHART_EDITOR_CHART_TYPES = {"auto", "bar", "timeline", "donut"}

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
}


def effective_chart_type(chart: dict, aggregation_types: dict[str, str]) -> str:
    """Löst "auto" zum tatsächlich gezeichneten Typ auf: Besteht das Chart nur
    aus Schaltern, ist der Zeitstrahl der Standard. "bar" ist die gespeicherte
    ausdrückliche Wahl "kein Zeitstrahl" (der Editor schreibt es, sobald ein
    reines Schalter-Chart den Zeitstrahl abwählt) und bleibt hier unverändert —
    sonst würde "auto" diese Wahl beim nächsten Laden wieder überstimmen."""
    stored = chart["chart_type"]
    if stored == "auto" and chart["entity_ids"] and all(
        aggregation_types.get(e) == "switch" for e in chart["entity_ids"]
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
    if chart_type == "timeline":
        return "zeitstrahl"
    if chart_type == "donut":
        return "donut"
    vorhanden = {
        "balken" if aggregation_types.get(entity_id) in ("counter", "switch") else "linie"
        for entity_id in chart["entity_ids"]
    }
    return "gemischt" if len(vorhanden) > 1 else next(iter(vorhanden), "")


def chart_type_label(chart: dict, aggregation_types: dict[str, str]) -> str:
    return CHART_TYPE_LABELS.get(chart_type_key(chart, aggregation_types), "")
