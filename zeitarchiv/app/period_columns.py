"""Zeitfenster für Vergleichstabellen mit Vorjahr (Wetter- und Energiedashboard).

Ein Spaltenschlüssel (Heute, Monat, Jahr, …) wird zu einem Zeitfenster und dem Fenster des
Vorjahrespartners aufgelöst. „Bis jetzt“ vergleicht einen laufenden Zeitraum mit demselben Stück
des Vorjahres, „Komplett“ mit dem ganzen Vorjahreszeitraum; abgeschlossene Zeiträume sind in
beiden Modi gleich. Ein Stichtag verschiebt „jetzt“ auf das Ende dieses Tages.

Hier steht nur die Fensterrechnung, ohne Daten und ohne Zustand. Welche Spalten es gibt, legt
das jeweilige Dashboard fest (Spaltenschlüssel -> Zeitraum und Versatz).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .i18n import tr
from .storage import query as query_mod

MODES = ("bis_jetzt", "komplett")


def parse_stichtag(raw: str | None, today: date) -> date | None:
    """Gültiger Stichtag vor heute oder None (heute und Zukunft gelten als „jetzt“); ungültig: 400."""
    if not raw:
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr("Ungültiges Datum: {value}", value=raw)) from None
    return day if day < today else None


def stichtag_anchor(stichtag: date | None, tz: ZoneInfo) -> datetime | None:
    """Ein Zeitpunkt am Stichtag (Mittag), nach dem sich die Zeiträume richten."""
    if stichtag is None:
        return None
    return datetime(stichtag.year, stichtag.month, stichtag.day, 12, tzinfo=tz)


def column_windows(
    windows: dict[str, tuple[str, int]], key: str, now_local: datetime, mode: str,
    anchor: datetime | None = None,
) -> dict:
    """Zeitfenster einer Spalte und ihres Vorjahrespartners.

    windows: Spaltenschlüssel -> (Zeitraum für query_mod._window, Versatz).

    anchor: ein Zeitpunkt am gewählten Stichtag (Mittag). Die Zeiträume richten sich dann nach diesem
    Tag, und „bis jetzt“ heißt „bis zum Ende des Stichtags“. Ohne Stichtag gilt now_local."""
    range_key, offset = windows[key]
    start, _capped, natural_end = query_mod._window(range_key, anchor or now_local, offset)  # noqa: SLF001
    cut = (anchor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)) if anchor else now_local
    capped_end = min(natural_end, cut)
    partial = capped_end < natural_end
    vj_start = query_mod._shift_year(start, -1)  # noqa: SLF001
    vj_end = query_mod._shift_year(  # noqa: SLF001
        capped_end if (partial and mode == "bis_jetzt") else natural_end, -1
    )
    return {
        "key": key, "range": range_key, "partial": partial,
        "start": start, "end": capped_end, "natural_end": natural_end,
        "vj_start": vj_start, "vj_end": vj_end,
    }
