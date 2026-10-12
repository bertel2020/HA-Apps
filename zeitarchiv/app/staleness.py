"""Wann eine Werte-Kachel als veraltet gilt (gelber/roter Rahmen).

Eine feste Stufenauswahl je Kachel statt einer aus dem Meldeintervall
abgeleiteten Heuristik: Die Schwellen bleiben für den Nutzer nachvollziehbar.
Dieselbe Tabelle speist den Server (Erstanzeige) und, über data-Attribute der
Kachel, den Client (Live-Aktualisierung) — es gibt nur eine Quelle.
"""

from __future__ import annotations

# Stufe -> (gelb ab Sekunden, rot ab Sekunden); None = nie markieren.
# "standard" ist das bisherige, einzige Verhalten (15 Minuten / 1 Stunde).
STALE_THRESHOLDS: dict[str, tuple[int, int] | None] = {
    "standard": (15 * 60, 60 * 60),
    "daily": (26 * 3600, 48 * 3600),
    "rare": (3 * 86400, 7 * 86400),
    "off": None,
}
DEFAULT_STALE_MODE = "standard"
STALE_MODES = tuple(STALE_THRESHOLDS)


def stale_thresholds(mode: str) -> tuple[int, int] | None:
    """Schwellen der Stufe; eine unbekannte Stufe fällt auf den Standard zurück."""
    return STALE_THRESHOLDS.get(mode, STALE_THRESHOLDS[DEFAULT_STALE_MODE])


def staleness(seconds_ago: float | None, mode: str) -> str:
    """'fresh', 'warn' oder 'stale'. Ohne letzten Wert oder bei 'off' immer 'fresh'."""
    thresholds = stale_thresholds(mode)
    if seconds_ago is None or thresholds is None:
        return "fresh"
    warn_after, stale_after = thresholds
    if seconds_ago > stale_after:
        return "stale"
    if seconds_ago > warn_after:
        return "warn"
    return "fresh"
