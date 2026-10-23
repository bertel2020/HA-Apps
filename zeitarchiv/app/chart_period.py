"""Fester Zeitraum und Vergleich gespeicherter Charts, Grenzen des Kalender-Sprungs.

Aus main.py herausgelöst (dessen Längengrenze, siehe test_route_modules.py):
reine Regeln ohne Zugriff auf App oder Index. Der feste Zeitraum ist ein Tag
(JJJJ-MM-TT) in der gespeicherten Periode, siehe saved_charts.period_anchor —
ein Datum statt eines Versatzes, weil "ein Jahr zurück" mit der Zeit
weiterwandern würde.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .i18n import tr
from .storage.paths import validate_entity_id

# Vergleich eines gespeicherten Charts (saved_charts.compare_settings, JSON):
# an/aus, Art ("previous" = Vorperiode/Vorjahr, "year" = Vorjahres…,
# "longterm" = Langzeit), bei Langzeit Zahl der Vorjahre (0 = alle) und
# Ø-Linie, bei Vorjahr die Laufsumme und welche Entität die Soll-Linie ist.
COMPARE_MODES = ("previous", "year", "longterm")
LONGTERM_YEARS = (0, 2, 5, 10)


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


def compare_settings_or_400(value: dict | None) -> dict:
    """Prüft und normalisiert den gespeicherten Vergleich. Unbekannte Schlüssel
    fallen weg, fehlende bekommen den Ausgangswert des Editors."""
    value = value or {}
    mode = value.get("mode", "previous")
    years = value.get("years", 5)
    target = value.get("target") or None
    if mode not in COMPARE_MODES or years not in LONGTERM_YEARS:
        raise HTTPException(status_code=400, detail=tr("Ungültiger Vergleich"))
    if target is not None:
        try:
            target = validate_entity_id(target)
        except ValueError:
            raise HTTPException(status_code=400, detail=tr("Ungültiger Vergleich")) from None
    return {
        "compare": bool(value.get("compare", False)), "mode": mode, "years": years,
        "average": bool(value.get("average", True)), "cumsum": bool(value.get("cumsum", False)), "target": target,
    }


def stichtag_or_400(value: str | None) -> date | None:
    """Stichtag der Zeitraum-Navigation eines Dashboards (JJJJ-MM-TT) oder None
    (heute). Dieselbe Strenge wie period_anchor_or_400()."""
    anchor = period_anchor_or_400(value)
    return date.fromisoformat(anchor) if anchor else None


def stichtag_window(range_key: str, continuous: bool, stichtag: date | None, now: datetime) -> tuple[datetime, int]:
    """Übersetzt den Stichtag eines Dashboards in das, was query_series()
    versteht: (Bezugszeitpunkt, zusätzlicher Perioden-Versatz).

    Kalendarische Zeiträume bekommen die Periode, die den Stichtag enthält —
    vollständig, nicht am Stichtag gekappt: wer auf den Oktober 2025 blickt,
    will den ganzen Monat sehen. Das ist dieselbe Umrechnung wie
    PeriodNavigation.offsetForRange() für einen festen Zeitraum (Mittag als
    Anker), nur serverseitig, damit Charts, Tabellen und Werte-Kacheln
    dieselbe Regel teilen. Rollierende Fenster ("letzte 24 h") haben keine
    Periode, die den Tag enthalten könnte — sie enden am Ende des Stichtags.
    Ein Stichtag heute oder in der Zukunft ändert nichts."""
    if stichtag is None or stichtag >= now.date():
        return now, 0
    if continuous:
        return datetime.combine(stichtag + timedelta(days=1), time.min, tzinfo=now.tzinfo), 0
    today = now.date()
    if range_key == "hour":
        hour_now = now.replace(tzinfo=None, minute=0, second=0, microsecond=0)
        shift = (datetime.combine(stichtag, time(12)) - hour_now) // timedelta(hours=1)
    elif range_key == "day":
        shift = (stichtag - today).days
    elif range_key == "week":
        shift = ((stichtag - timedelta(days=stichtag.weekday())) - (today - timedelta(days=today.weekday()))).days // 7
    elif range_key == "month":
        shift = (stichtag.year * 12 + stichtag.month) - (today.year * 12 + today.month)
    elif range_key == "year":
        shift = stichtag.year - today.year
    elif range_key == "decade":
        shift = stichtag.year // 10 - today.year // 10
    else:
        raise HTTPException(status_code=400, detail=tr("Ungültiger Zeitraum"))
    return now, min(shift, 0)
