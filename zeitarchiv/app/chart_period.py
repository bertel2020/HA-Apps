"""Fester Zeitraum gespeicherter Charts und Grenzen des Kalender-Sprungs.

Aus main.py herausgelöst (dessen Längengrenze, siehe test_route_modules.py):
reine Regeln ohne Zugriff auf App oder Index. Der feste Zeitraum ist ein Tag
(JJJJ-MM-TT) in der gespeicherten Periode, siehe saved_charts.period_anchor —
ein Datum statt eines Versatzes, weil "ein Jahr zurück" mit der Zeit
weiterwandern würde.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .i18n import tr


def period_anchor_or_400(value: str | None) -> str | None:
    """JJJJ-MM-TT oder None (laufende Periode). Alles andere ist ein Fehler des
    Aufrufers, kein Grund, still auf die laufende Periode zurückzufallen."""
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=tr("Ungültiger Zeitraum")) from None


def calendar_bounds(entity_rows, tz: ZoneInfo) -> dict:
    """Grenzen des Kalender-Sprungs im Chart-Editor: das ganze Archiv statt der
    gerade gewählten Entitäten — die Auswahl ändert sich im Editor laufend, der
    Kalender wird aber nur einmal aufgebaut."""
    firsts = [row["first_ts"] for row in entity_rows if row["first_ts"]]
    lasts = [row["last_ts"] for row in entity_rows if row["last_ts"]]
    day = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")  # noqa: E731
    return {
        "calendar_first_date": day(min(firsts)) if firsts else None,
        "calendar_last_date": day(max(lasts)) if lasts else None,
    }
