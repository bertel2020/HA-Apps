"""Vorschläge für die Rollen des Wetterdashboards (Knopf „Vorschlagen“ in der Einrichtung).

Die Vorschläge füllen nur leere Rollen und werden erst mit „Speichern“ übernommen. Grundlage sind
die archivierten Entitäten des Index, angereichert um die Metadaten des Katalogs der Integration
(Geräteklasse, Plattform, Gerät, Bereich), wenn er vorliegt; ohne Katalog bleiben Einheit und Name.
Passende Entitäten, die noch nicht archiviert sind, kommen getrennt zurück („missing“), damit die
Oberfläche auf den Auswahl-Assistenten verweisen kann.

Bewertung statt harter Regeln: jede Rolle sammelt Punkte (passende Geräteklasse oder Einheit,
Außen-Hinweis im Namen, Wetter-Integration, dasselbe Gerät wie die Außentemperatur). Ein klarer
Vorsprung gilt als „ok“, ein knapper als „prüfen“.
"""

from __future__ import annotations

import re
from typing import Any

from .i18n import tr
from .selection_topics import WEATHER_PLATFORMS, _DEVICE_TEMP, is_outdoor

#: Rollen, für die Vorschläge berechnet werden (Reihenfolge wie in der Einrichtung).
ROLE_KEYS = ("temperature", "precip_mm", "precip_dur", "humidity", "pressure", "wind", "gust", "radiation", "illuminance")

_TEMP_UNITS = {"°c", "°f", "k"}
_PRESSURE_UNITS = {"hpa", "mbar", "kpa", "pa", "inhg"}
_WIND_UNITS = {"km/h", "m/s", "mph", "kn"}
_COUNTER_UNITS = {"kwh", "wh", "mwh"}
_GUST = re.compile(r"(b[öo]e|gust|spitze|peak|max)", re.IGNORECASE)
_RAIN = re.compile(r"(regen|rain)", re.IGNORECASE)
_RATE = re.compile(r"(rate|intensit|st[äa]rke|pro stunde|per hour|/h)", re.IGNORECASE)
_TOTAL = re.compile(r"(total|gesamt|summe|jahr|year|lifetime)", re.IGNORECASE)
_HUMIDITY = re.compile(r"(feucht|humid)", re.IGNORECASE)
_RELATIVE = re.compile(r"(relativ|relative|meeresspiegel|sea)", re.IGNORECASE)
_ABSOLUTE = re.compile(r"(absolut|absolute)", re.IGNORECASE)
#: Mehrere Helligkeitssensoren an einer Station (Süd, Ost, West, Dämmerung): die Sonnenscheindauer braucht Süd.
_SOUTH = re.compile(r"(s[üu]d|south|central|zentral)", re.IGNORECASE)
_MOON = re.compile(r"(moon|mond)", re.IGNORECASE)
_HEATING = re.compile(r"(heiz|w[äa]rmepumpe|waermepumpe|heat ?pump|heatpump|konvektor|heizstab|therme|fu(ß|ss)boden)", re.IGNORECASE)
_HINT_FIELDS = ("name", "device", "area")

#: Mindestpunkte, damit ein Kandidat überhaupt in Frage kommt.
MIN_SCORE = 3
#: Vorsprung zum Zweitplatzierten, ab dem ein Vorschlag als eindeutig gilt.
CLEAR_LEAD = 2
MAX_CANDIDATES = 5
#: Höchstzahl vorgeschlagener weiterer Sensoren (Temperatur oder Feuchte, auch innen).
MAX_EXTRAS = 4


def _text(row: dict[str, Any]) -> str:
    object_id = row["entity_id"].split(".", 1)[1].replace("_", " ")
    return " ".join(str(row[field]) for field in _HINT_FIELDS if row.get(field)) + " " + object_id


def _unit(row: dict[str, Any]) -> str:
    return (row.get("unit") or "").strip().lower()


def _weather_source(row: dict[str, Any]) -> bool:
    return row.get("platform") in WEATHER_PLATFORMS


def _outdoor_or_station(row: dict[str, Any]) -> bool:
    return is_outdoor(row) or _weather_source(row)


def _score(role: str, row: dict[str, Any], anchor: dict[str, Any] | None) -> tuple[int, list[str]]:
    """Punkte und Begründungen einer Entität für eine Rolle; 0 Punkte = passt nicht."""
    dc, unit, kind = row.get("device_class"), _unit(row), row["aggregation_type"]
    text = _text(row)
    points, reasons = 0, []

    def add(count: int, reason: str) -> None:
        nonlocal points
        points += count
        reasons.append(reason)

    if role == "temperature":
        if kind != "standard" or row["domain"] != "sensor" or _DEVICE_TEMP.search(text):
            return 0, []
        if dc == "temperature":
            add(2, tr("Geräteklasse Temperatur"))
        if unit in _TEMP_UNITS:
            add(1, tr("Einheit {unit}", unit=row.get("unit")))
        if points == 0:
            return 0, []
        if is_outdoor(row):
            add(3, tr("„Außen“ im Namen"))
        if _weather_source(row):
            add(3, tr("Wetter-Integration"))
        if not _outdoor_or_station(row):
            return 0, []  # eine Innentemperatur ist keine Außentemperatur
    elif role in ("humidity", "pressure", "wind", "gust", "radiation", "illuminance"):
        if kind != "standard" or row["domain"] != "sensor":
            return 0, []
        if role == "humidity":
            if dc == "humidity" or (unit == "%" and _HUMIDITY.search(text)):
                add(2, tr("Geräteklasse Feuchte") if dc == "humidity" else tr("„Feuchte“ im Namen"))
            if points and _outdoor_or_station(row):
                add(2, tr("Außen oder Wetter-Integration"))
        elif role == "pressure":
            if dc == "atmospheric_pressure" or unit in _PRESSURE_UNITS:
                add(3, tr("Luftdruck"))
            if points and _RELATIVE.search(text):
                add(1, tr("relativer Druck"))
            if points and _ABSOLUTE.search(text):
                points -= 1
        elif role in ("wind", "gust"):
            if dc == "wind_speed" or unit in _WIND_UNITS:
                add(3, tr("Windgeschwindigkeit"))
            if points:
                gusty = bool(_GUST.search(text))
                if (role == "gust") != gusty:
                    return 0, []
                if gusty:
                    add(1, tr("„Böe“ im Namen"))
        elif role == "radiation":
            if dc == "irradiance" or unit == "w/m²":
                add(3, tr("Strahlung"))
        else:
            if _MOON.search(text):
                return 0, []  # die berechnete Mondhelligkeit ist kein Sensor
            if dc == "illuminance" or unit == "lx":
                add(3, tr("Helligkeit"))
            if points and _SOUTH.search(text):
                add(2, tr("Süd-Sensor"))
    elif role == "precip_mm":
        if kind != "counter" or row["domain"] != "sensor":
            return 0, []
        if dc == "precipitation" or unit in {"mm", "in"}:
            add(3, tr("Niederschlag, Zähler"))
        if points and _RATE.search(text):
            return 0, []
        if points and row.get("state_class") == "total_increasing":
            add(1, tr("laufende Summe"))
        if points and _TOTAL.search(text):
            add(1, tr("„Gesamt“ im Namen"))
        if points and _outdoor_or_station(row):
            add(1, tr("Wetter-Integration") if _weather_source(row) else tr("„Außen“ im Namen"))
    elif role == "precip_dur":
        if kind != "switch" or row["domain"] != "binary_sensor" or not _RAIN.search(text):
            return 0, []
        add(3, tr("„Regen“ im Namen"))
        if dc == "moisture":
            add(1, tr("Geräteklasse Feuchtigkeit"))
        if _weather_source(row):
            add(1, tr("Wetter-Integration"))
    if points and anchor is not None and row["entity_id"] != anchor["entity_id"]:
        if row.get("device") and row.get("device") == anchor.get("device"):
            add(3, tr("gleiches Gerät wie die Außentemperatur"))
        elif row.get("platform") and row.get("platform") == anchor.get("platform") and _weather_source(row):
            add(1, tr("gleiche Integration"))
    return points, reasons


def _rank(role: str, rows: list[dict[str, Any]], anchor: dict[str, Any] | None) -> list[tuple[int, dict[str, Any], list[str]]]:
    scored = []
    for row in rows:
        points, reasons = _score(role, row, anchor)
        if points >= MIN_SCORE:
            scored.append((points, row, reasons))
    scored.sort(key=lambda item: (-item[0], item[1]["entity_id"]))
    return scored


def _entry(ranked: list[tuple[int, dict[str, Any], list[str]]], labels: dict[str, str]) -> dict[str, Any]:
    points, row, reasons = ranked[0]
    lead = points - ranked[1][0] if len(ranked) > 1 else CLEAR_LEAD
    return {
        "entity_id": row["entity_id"],
        "label": labels.get(row["entity_id"], row["entity_id"]),
        "reason": ", ".join(([row["device"]] if row.get("device") else []) + [r for r in reasons if r]),
        "confidence": "ok" if lead >= CLEAR_LEAD else "check",
        "candidates": [
            {"entity_id": other["entity_id"], "label": labels.get(other["entity_id"], other["entity_id"])}
            for _p, other, _r in ranked[1:1 + MAX_CANDIDATES]
        ],
    }


def _extras(rows: list[dict[str, Any]], taken: set[str]) -> list[dict[str, str]]:
    """Weitere Temperatur- und Feuchtesensoren (auch Innenräume), die keiner Rolle zugeordnet sind.

    Außen-Sensoren zuerst, danach nach Entitäts-ID; Gerätetemperaturen (CPU, Akku …) zählen nicht.
    """
    found = []
    for row in rows:
        if row["entity_id"] in taken or row["domain"] != "sensor" or row["aggregation_type"] != "standard":
            continue
        dc, unit = row.get("device_class"), _unit(row)
        temperature = dc == "temperature" or unit in _TEMP_UNITS
        humidity = dc == "humidity" or (unit == "%" and _HUMIDITY.search(_text(row)))
        if not (temperature or humidity) or _DEVICE_TEMP.search(_text(row)):
            continue
        found.append((0 if _outdoor_or_station(row) else 1, row["entity_id"], row))
    found.sort(key=lambda item: item[:2])
    return [row for _o, _e, row in found][:MAX_EXTRAS]


def suggest(
    archived: list[dict[str, Any]], labels: dict[str, str], catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Vorschläge je Rolle.

    archived: Zeilen der archivierten Entitäten (entity_id, aggregation_type, unit, state_class, …),
    labels: Anzeigenamen je Entität, catalog: Zeilen des Integrationskatalogs oder None.
    Rückgabe: {"roles": {rolle: Vorschlag}, "heating": [...], "extras": [...], "missing": [...], "has_catalog": bool}.
    """
    meta = {row["entity_id"]: row for row in (catalog or [])}
    rows = []
    for entity in archived:
        extra = meta.get(entity["entity_id"], {})
        rows.append({
            "entity_id": entity["entity_id"],
            "domain": entity["entity_id"].split(".", 1)[0],
            "aggregation_type": entity["aggregation_type"],
            "unit": entity.get("unit"),
            "state_class": entity.get("state_class"),
            "name": labels.get(entity["entity_id"]) or extra.get("name"),
            "device_class": extra.get("device_class"),
            "platform": extra.get("platform"),
            "device": extra.get("device"),
            "area": extra.get("area"),
        })
    known = {row["entity_id"] for row in rows}
    roles: dict[str, dict[str, Any]] = {}
    anchor: dict[str, Any] | None = None
    for role in ROLE_KEYS:
        ranked = _rank(role, rows, anchor)
        if not ranked:
            continue
        roles[role] = _entry(ranked, labels)
        if role == "temperature":
            anchor = next(row for row in rows if row["entity_id"] == ranked[0][1]["entity_id"])
    taken = {pick["entity_id"] for pick in roles.values()}
    extras = [
        {"entity_id": row["entity_id"], "label": labels.get(row["entity_id"], row["entity_id"])}
        for row in _extras(rows, taken)
    ]
    heating = [
        {"entity_id": row["entity_id"], "name": "", "label": labels.get(row["entity_id"], row["entity_id"])}
        for row in rows
        if row["aggregation_type"] == "counter" and _unit(row) in _COUNTER_UNITS and _HEATING.search(_text(row))
    ][:6]
    missing = []
    if catalog:
        outside = [
            {
                "entity_id": row["entity_id"], "domain": row["entity_id"].split(".", 1)[0],
                "aggregation_type": "switch" if row["entity_id"].startswith("binary_sensor.") else (
                    "counter" if row.get("state_class") == "total_increasing" else "standard"),
                "unit": row.get("unit"), "state_class": row.get("state_class"), "name": row.get("name"),
                "device_class": row.get("device_class"), "platform": row.get("platform"),
                "device": row.get("device"), "area": row.get("area"),
            }
            for row in catalog if row["entity_id"] not in known
        ]
        for role in ROLE_KEYS:
            if role in roles:
                continue
            ranked = _rank(role, outside, anchor)
            if ranked:
                row = ranked[0][1]
                missing.append({"role": role, "entity_id": row["entity_id"], "label": row.get("name") or row["entity_id"]})
    return {"roles": roles, "heating": heating, "extras": extras, "missing": missing, "has_catalog": bool(catalog)}
