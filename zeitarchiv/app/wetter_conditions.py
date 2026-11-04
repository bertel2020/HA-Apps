"""Wetterlage des Wetterdashboards: Himmelsklasse, Windstärke und Regen als Satz (ROADMAP 1.43).

Reine Funktionen ohne Zugriff auf den Speicher; ``wetter_routes.py`` liest die letzten Minuten der Sensoren und
reicht sie hierher. Die Klassen sind Faustwerte aus den Daten einer Eltako-Station, kein Messstandard.
"""

from __future__ import annotations

NIGHT = "night"
DUSK = "dusk"
DAY = "day"  # nur der Dämmerungssensor, ohne Helligkeit für eine Himmelsklasse
OVERCAST = "overcast"  # trüb
CLOUDY = "cloudy"  # bewölkt
FAIR = "fair"  # heiter
SUNNY = "sunny"  # sonnig

#: Untergrenzen von bewölkt, heiter, sonnig als Vielfaches der Sonnen-Schwelle (Standard 25.000 lx): 15.000, 25.000
#: und 70.000 lx. „Heiter“ beginnt genau dort, wo die Sonnenscheindauer zu zählen anfängt.
CLASS_FACTORS_LX = (0.6, 1.0, 2.8)
#: Dieselben Klassen für eine Strahlung in W/m². Aus lx umgerechnet (etwa 110 lx je W/m²), **nicht an Messungen
#: geprüft**: Der Nutzer, an dessen Daten die lx-Grenzen entstanden, hat keinen Strahlungssensor.
CLASS_CUTS_WM2 = (150.0, 250.0, 650.0)
#: Eine Strahlung unter diesem Wert gilt als Nacht (es gibt dort keinen Dämmerungssensor, der es sagen könnte).
NIGHT_WM2 = 5.0

#: Obergrenzen (exklusiv, km/h) der Beaufort-Stufen 0 bis 11; darüber Stufe 12.
BEAUFORT_LIMITS_KMH = (1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118)

_TO_KMH = {"km/h": 1.0, "m/s": 3.6, "mph": 1.609344, "kn": 1.852}

#: Regen, der länger zurückliegt, gilt als „seit langem trocken“; so weit wird der Schalter zurückgelesen.
RAIN_LOOKBACK_HOURS = 48


def sky_class(value: float, cuts: tuple[float, float, float]) -> str:
    """Trüb, bewölkt, heiter oder sonnig nach den Untergrenzen ``cuts`` (bewölkt, heiter, sonnig)."""
    if value >= cuts[2]:
        return SUNNY
    if value >= cuts[1]:
        return FAIR
    return CLOUDY if value >= cuts[0] else OVERCAST


def lx_cuts(sun_threshold_lx: float) -> tuple[float, float, float]:
    return tuple(sun_threshold_lx * factor for factor in CLASS_FACTORS_LX)  # type: ignore[return-value]


def to_kmh(value: float, unit: str | None) -> float | None:
    """Windgeschwindigkeit in km/h; None bei einer unbekannten Einheit (dann keine Stärke, kein Raten)."""
    factor = _TO_KMH.get((unit or "").strip().lower() or "km/h")
    return None if factor is None else value * factor


def beaufort(kmh: float) -> int:
    for stage, limit in enumerate(BEAUFORT_LIMITS_KMH):
        if kmh < limit:
            return stage
    return 12


def rain_state(timeline: list[tuple[float, bool]], now: float) -> dict | None:
    """Zustand des Regensensors aus seinen Wechseln ``(Zeit, an)``; der erste Eintrag ist der Zustand am Anfang.

    ``since``: Beginn des laufenden Regens (None, wenn er schon am Anfang des Fensters lief); ``last``: Ende des
    letzten Regens, solange es trocken ist (None, wenn im Fenster keiner war)."""
    if not timeline:
        return None
    ordered = sorted(timeline)
    raining = ordered[-1][1]
    if raining:
        since = None
        for index in range(len(ordered) - 1, 0, -1):
            if not ordered[index - 1][1]:
                since = ordered[index][0]
                break
        return {"raining": True, "since": since, "last": None, "minutes": None if since is None else max(0.0, (now - since) / 60)}
    last = None
    for index in range(len(ordered) - 1, 0, -1):
        if ordered[index - 1][1] and not ordered[index][1]:
            last = ordered[index][0]
            break
    return {"raining": False, "since": None, "last": last, "minutes": None}
