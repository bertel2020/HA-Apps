"""Einheiten-Schutz beim Aufnehmen (ROADMAP 1.3, Stufe 1).

Die gespeicherte Einheit einer Entität bleibt maßgeblich. Meldet ein Event eine
andere Einheit derselben Größe (Wh statt kWh, kW statt W), wird der Wert auf
die gespeicherte Einheit umgerechnet, statt die Einheit still zu überschreiben.
Bewusst nur die rein multiplikativen Paare aus ``entity_migration``: keine
Groß-/Kleinschreibung normalisieren (``mW`` und ``MW`` sind verschieden), kein
Präfix-Parser, kein °C/°F."""

from __future__ import annotations

# Bekannte SI-Vorsatz-Stufen für die beiden physikalischen Größen, bei denen
# ein Einheiten-Wechsel zwischen Quelle und Ziel in der Praxis vorkommt (ein
# Gerät wird ersetzt und meldet fortan in Wh statt kWh, oder W statt kW) —
# bewusst NICHT generisch für jede Einheit: "m" wäre sonst z. B. sowohl
# Milli-Präfix als auch die Basiseinheit Meter, "kWh"/"kW" unterscheiden sich
# nur im letzten Buchstaben und dürfen nie gegeneinander vorgeschlagen werden
# (Energie ist keine Leistung) — zwei getrennte, explizite Tabellen statt
# eines Präfix-Parsers vermeiden beide Fallen von vornherein.
_ENERGY_UNIT_SCALE: dict[str, float] = {"Wh": 1.0, "kWh": 1_000.0, "MWh": 1_000_000.0, "GWh": 1_000_000_000.0}
_POWER_UNIT_SCALE: dict[str, float] = {"W": 1.0, "kW": 1_000.0, "MW": 1_000_000.0, "GW": 1_000_000_000.0}


def suggest_factor(source_unit: str, target_unit: str) -> float | None:
    """Schlägt einen Umrechnungsfaktor vor, wenn Quelle und Ziel dieselbe
    physikalische Größe nur in unterschiedlichem SI-Präfix messen (z. B. kWh
    vs. Wh → 1000.0) — None, wenn beide Einheiten gleich sind (kein Faktor
    nötig) oder keine bekannte, rein multiplikative Umrechnung existiert
    (z. B. °C vs. °F, das bräuchte zusätzlich eine additive Verschiebung, die
    das Migrations-Feature nicht unterstützt)."""
    if not source_unit or not target_unit or source_unit == target_unit:
        return None
    for scale in (_ENERGY_UNIT_SCALE, _POWER_UNIT_SCALE):
        if source_unit in scale and target_unit in scale:
            return scale[source_unit] / scale[target_unit]
    return None


def clean_unit(unit: str | None) -> str:
    """Einheit ohne umgebende Leerzeichen; None wird zum leeren Text."""
    return (unit or "").strip()


def conversion_factor(event_unit: str | None, stored_unit: str | None) -> float | None:
    """Faktor, der einen Wert in ``event_unit`` auf ``stored_unit`` umrechnet.

    None, wenn nichts umzurechnen ist: eine Einheit fehlt, beide sind gleich
    oder das Paar ist nicht bekannt (siehe ``is_unit_mismatch``)."""
    event_unit, stored_unit = clean_unit(event_unit), clean_unit(stored_unit)
    if not event_unit or not stored_unit:
        return None
    return suggest_factor(event_unit, stored_unit)


def is_unit_mismatch(event_unit: str | None, stored_unit: str | None) -> bool:
    """Beide Einheiten sind bekannt und verschieden, aber nicht umrechenbar."""
    event_unit, stored_unit = clean_unit(event_unit), clean_unit(stored_unit)
    if not event_unit or not stored_unit or event_unit == stored_unit:
        return False
    return suggest_factor(event_unit, stored_unit) is None
