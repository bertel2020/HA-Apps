"""Einschaltungen eines Schalters als eigene Serie (ROADMAP 1.74).

/api/query-multi liefert die Anzahl je Zeitfenster als gewöhnliche Zähler-Serie: Wert = Zahl der Einschaltungen,
Einheit "Einschaltungen". Damit funktionieren Chart-Editor, Dashboard-Kacheln und die Lovelace-Karte ohne eigenen
Zeichenpfad — Summen, Achsen je Einheit, Stapel, Vergleich und Tabellen gelten für Zähler schon.
"""

from __future__ import annotations

from .i18n import tr

#: Anzeige einer Schalter-Serie im Chart: die Dauer, nur die Anzahl, oder Dauer (Balken) und Anzahl (Linie) zusammen.
SERIES_METRICS = ("duration", "count", "both")
#: Der Anzeigemodus der Entität (entities.display_mode) als Standard der Serie; "onoff" und "time" zeigen die Dauer.
DISPLAY_MODE_METRICS = {"count": "count", "both": "both"}
#: Anhang der Serien-Kennung für die zusätzliche Anzahl-Serie bei "Beides".
COUNT_SUFFIX = "#count"


def _counted(points: list[dict] | None) -> list[dict] | None:
    if points is None:
        return None
    return [{"ts": p["ts"], "value": p.get("count"), "min": None, "max": None} for p in points]


def default_metric(display_mode: str | None) -> str:
    """Was ein Schalter ohne ausdrückliche Wahl im Chart zeigt."""
    return DISPLAY_MODE_METRICS.get(display_mode or "", "duration")


def switch_count_entries(entry: dict, metric: str) -> list[dict]:
    """Die Serien, die für einen Schalter mit gewählter Anzeige in die Antwort kommen.

    "count": die Dauer-Serie wird durch die Anzahl-Serie ersetzt. "both": die Dauer-Serie bleibt, dahinter kommt die
    Anzahl als Linie unter der Kennung ``<entity_id>#count`` (``base_entity_id`` verweist auf die Entität).
    ``source_type`` nennt den Schalter hinter der Zähler-Serie; die Oberfläche bietet dort die Anzeige weiter an.
    Eine unbekannte Anzahl (Rollup aus der Zeit vor der Zählung) bleibt ``None``, nie 0."""
    counted = dict(
        entry, source_type="switch", aggregation_type="counter", unit=tr("Einschaltungen"), decimals=None, display_mode="onoff",
        points=_counted(entry["points"]),
    )
    for key in ("compare_points", "compare_cut_points"):
        if key in counted:
            counted[key] = _counted(counted[key])
    if counted.get("years"):
        counted["years"] = [
            dict(year, points=_counted(year["points"]), cut_points=_counted(year.get("cut_points")))
            for year in counted["years"]
        ]
    if metric == "count":
        return [counted]
    extra = dict(
        counted, entity_id=entry["entity_id"] + COUNT_SUFFIX, base_entity_id=entry["entity_id"],
        friendly_name=f"{entry['friendly_name']} · {tr('Anzahl')}", base_name=entry["friendly_name"], chart_type="line",
    )
    return [entry, extra]
