"""Umrechnung von Energieeinheiten für das Energiedashboard.

Zähler liegen im Archiv in der Einheit ihrer Entität. Das Dashboard rechnet durchgehend in kWh und
skaliert deshalb beim Lesen. Bewusst nur die drei Energieeinheiten: Die Lesewege des Dashboards
bedienen auch Ladezustand (%) und Preise, deren Werte unverändert bleiben müssen."""

from __future__ import annotations

#: Faktor nach kWh.
_KWH_FACTOR: dict[str, float] = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}

#: Einheiten, die das Dashboard als Energiezähler annimmt.
ENERGY_UNITS: tuple[str, ...] = tuple(_KWH_FACTOR)


def energy_unit_scale(unit: str | None) -> float:
    """Faktor, der einen Wert in ``unit`` nach kWh umrechnet; 1,0 für kWh und für jede andere Einheit."""
    return _KWH_FACTOR.get(unit or "", 1.0)
