"""Auswertungen des Wetterdashboards über Tageswerte (ROADMAP 1.43, Phase 2).

Reine Funktionen über ``DailyPoint``-Listen (``storage.query.query_daily_series``), ohne Zugriff
auf den Speicher: Laufsummen des Niederschlags, Monatswerte, Kenntage, Rekorde und der Kopfsatz.
Die Routen in ``wetter_routes.py`` holen die Tagesreihen und reichen sie hierher.

Gemeinsame Regeln:
- Ein Tag trägt das Datum der lokalen Zeitzone (``DailyPoint.day_start`` ist Mitternacht dort).
- Der 29. Februar zählt zum 28. Februar (wie ``_shift_year`` im Vorjahresvergleich).
- Fehlende Tage sind kein 0. Eine Laufsumme hält bei einer Lücke ihren Stand und ist nach dem
  letzten echten Wert ``None``, nie in die Zukunft fortgeschrieben.
- Kenntage, Rekorde und Monatsmittel zählen nur Tage mit ausreichender Abdeckung (``covered``);
  der laufende Tag zählt bei Kenntagen nie, weil sein Min/Max noch wandern kann.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .storage.query import DailyPoint

SLOTS = 365  # Kalendertage eines Nicht-Schaltjahres
#: Ein Jahr gilt als vollständig, wenn so viele Tage Werte haben (Archiv muss dort schon laufen).
COMPLETE_YEAR_MIN_DAYS = 350
#: Ein abgeschlossener Monat braucht für Mittelwerte mindestens so viel Abdeckung.
MONTH_MIN_COVERAGE = 0.7
#: Rekorde erst, wenn das Archiv mindestens so viele belegte Tage hat.
RECORDS_MIN_DAYS = 365

#: DWD-Kenntage: (Schlüssel, Grenzwert, Vergleichsfeld, Richtung)
KENNTAGE = (
    ("frost", 0.0, "min_value", "below"),  # Frosttag: Min unter 0 °C
    ("eis", 0.0, "max_value", "below"),  # Eistag: Max unter 0 °C
    ("sommer", 25.0, "max_value", "atleast"),  # Sommertag: Max ab 25 °C
    ("heiss", 30.0, "max_value", "atleast"),  # Heißer Tag: Max ab 30 °C
)


_FAHRENHEIT = {"°F", "℉"}
_INCH = {"in", "inch", "inches"}


def celsius_to_unit(value: float, unit: str | None) -> float:
    """Ein Grenzwert in °C in der Einheit der Temperatur-Entität. Fahrenheit und Kelvin werden umgerechnet,
    alles andere (°C, keine Einheit) bleibt wie es ist. Ohne das zählte ein °F-Sensor fast jeden Tag als
    Sommertag (ab 25 °F) und einen Frosttag erst bei −18 °C, ein Kelvin-Sensor jeden Tag als heißen Tag."""
    unit = (unit or "").strip()
    if unit in _FAHRENHEIT:
        return value * 9 / 5 + 32
    if unit == "K":
        return value + 273.15
    return value


def mm_to_unit(value: float, unit: str | None) -> float:
    """Eine Menge in mm in der Einheit der Niederschlags-Entität (Zoll werden umgerechnet)."""
    if (unit or "").strip().lower() in _INCH:
        return value / 25.4
    return value


def _kenntage_limits(unit: str | None) -> list[tuple[str, float, str, str]]:
    return [(key, celsius_to_unit(limit, unit), field, direction) for key, limit, field, direction in KENNTAGE]


def slot_of(day: date) -> int:
    """Index 0..364 im Nicht-Schaltjahr; der 29.02. fällt auf den 28.02."""
    capped = day.day if not (day.month == 2 and day.day == 29) else 28
    return date(2025, day.month, capped).timetuple().tm_yday - 1


def day_of(point: DailyPoint, tz: ZoneInfo) -> date:
    return datetime.fromtimestamp(point.day_start, tz).date()


def _cumulative(values: dict[int, float], last_slot: int, hold_to_end: bool = False) -> list[float | None]:
    """Laufsumme über die 365 Plätze. Lücken halten den Stand, nach dem letzten echten Wert (oder
    nach ``last_slot``) steht None; ohne jeden Wert ist die ganze Reihe None."""
    if not values:
        return [None] * SLOTS
    stop = last_slot if hold_to_end else min(max(values), last_slot)
    result: list[float | None] = [None] * SLOTS
    running = 0.0
    for slot in range(stop + 1):
        running += values.get(slot, 0.0)
        result[slot] = round(running, 3)
    return result


def precipitation(
    daily: dict[date, float], today: date, wet_threshold: float, hold_to_end: bool = False,
    archive_start: date | None = None,
) -> dict:
    """Laufsummen je Jahr, Spanne der Vorjahre und Kennzahlen des laufenden Jahres.

    daily: Tageswert (mm oder Minuten) je lokalem Datum, nur Tage mit Zeile.
    today: lokales Datum von „jetzt“; der heutige Tag ist enthalten, aber unvollständig.
    wet_threshold: ab dieser Tagesmenge zählt ein Tag als Regentag (und darunter als trocken).
    hold_to_end: Die Laufsumme hält ihren Stand bis heute (bzw. Jahresende) auch hinter dem letzten Tag
    mit Zeile. Nötig bei einem Regensensor ein/aus: ein Tag ohne Wechsel hat keine Zeile und ist
    trocken, nicht unbekannt. Beim Zähler fehlt ein Tag nur, wenn der Sensor ausgefallen ist.
    Dann gilt auch ein Jahr als vollständig, wenn das Archiv in dessen ersten Tagen begann
    (archive_start), und Tage ohne Zeile zählen in der Trockenphase als trocken."""
    by_year: dict[int, dict[int, float]] = {}
    for day, value in daily.items():
        slot = slot_of(day)
        year_values = by_year.setdefault(day.year, {})
        year_values[slot] = year_values.get(slot, 0.0) + value  # 29.02. zählt zum 28.02.
    current_year = today.year
    today_slot = slot_of(today)
    cumulative = {
        year: _cumulative(values, today_slot if year == current_year else SLOTS - 1, hold_to_end)
        for year, values in by_year.items()
    }
    def is_complete(year: int, values: dict[int, float]) -> bool:
        if year >= current_year:
            return False
        if len(values) >= COMPLETE_YEAR_MIN_DAYS:
            return True
        return bool(hold_to_end and archive_start and archive_start <= date(year, 1, 7))

    complete = sorted(year for year, values in by_year.items() if is_complete(year, values))
    avg: list[float | None] = [None] * SLOTS
    low: list[float | None] = [None] * SLOTS
    high: list[float | None] = [None] * SLOTS
    for slot in range(SLOTS):
        column = [cumulative[year][slot] for year in complete if cumulative[year][slot] is not None]
        if len(column) == len(complete) and column:
            avg[slot] = round(sum(column) / len(column), 3)
            low[slot], high[slot] = min(column), max(column)
    previous = cumulative.get(current_year - 1)
    current_values = by_year.get(current_year, {})
    current_days = {day: value for day, value in daily.items() if day.year == current_year}
    if hold_to_end and archive_start is not None:
        # Tage ohne Wechsel sind trocken, nicht unbekannt: lückenlos von Jahresbeginn (oder Archivbeginn) bis heute.
        first = max(date(current_year, 1, 1), archive_start)
        for offset in range((today - first).days + 1):
            current_days.setdefault(first + timedelta(days=offset), 0.0)
    wettest = max(current_days.items(), key=lambda item: (item[1], -item[0].toordinal()), default=None)

    longest_run, longest_end, running_run = 0, None, 0
    previous_day: date | None = None
    for day in sorted(current_days):
        dry = current_days[day] < wet_threshold
        contiguous = previous_day is not None and (day - previous_day).days == 1
        running_run = running_run + 1 if (dry and contiguous and running_run) else (1 if dry else 0)
        if running_run > longest_run:
            longest_run, longest_end = running_run, day
        previous_day = day
    # Die laufende Trockenphase endet am letzten Tag mit Wert; sie gilt nur, wenn der heute oder gestern ist.
    current_run = running_run if previous_day is not None and (today - previous_day).days <= 1 else 0

    return {
        "year": current_year,
        "today_slot": today_slot,
        "daily": [round(current_values[slot], 3) if slot in current_values else None for slot in range(SLOTS)],
        "cumulative": cumulative.get(current_year, [None] * SLOTS),
        "previous": previous if previous and any(v is not None for v in previous) else None,
        "previous_year": current_year - 1,
        "avg": avg, "low": low, "high": high,
        "avg_years": complete,
        # Stand bis heute: der letzte echte Wert, auch wenn heute noch keine Zeile da ist.
        "total": next((v for v in reversed(cumulative.get(current_year, [None] * SLOTS)[: today_slot + 1]) if v is not None), None),
        "previous_to_date": previous[today_slot] if previous else None,
        "avg_to_date": avg[today_slot] if len(complete) >= 2 else None,
        "wet_days": sum(1 for value in current_days.values() if value >= wet_threshold),
        "wettest": {"value": wettest[1], "date": wettest[0].isoformat()} if wettest and wettest[1] > 0 else None,
        "longest_dry_days": longest_run,
        "longest_dry_end": longest_end.isoformat() if longest_end else None,
        "current_dry_days": current_run,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def monthly(
    points: list[DailyPoint], tz: ZoneInfo, today: date, kind: str, dry_start: date | None = None,
) -> dict:
    """Monatswerte je Jahr (12 Plätze) und Spanne der abgeschlossenen Vorjahre.

    kind ``mean``: Mittel der Tages-Ø (Messwerte), nur abgedeckte Tage. ``sum``: Summe der
    Tageswerte (Niederschlag). Ein abgeschlossener Monat braucht MONTH_MIN_COVERAGE seiner Tage;
    der laufende Monat zeigt, was bisher da ist (Teilwert, deshalb nicht im Mittel der Vorjahre).
    dry_start (nur ``sum``, Regensensor ein/aus): Monate ab diesem Datum ohne Zeile sind 0 statt unbekannt,
    und ein Monat braucht keine Abdeckung, weil Tage ohne Wechsel trocken sind."""
    per_month: dict[tuple[int, int], list[float]] = {}
    for point in points:
        if kind == "mean" and (not point.covered or point.value is None):
            continue
        day = day_of(point, tz)
        per_month.setdefault((day.year, day.month), []).append(point.value or 0.0)
    if dry_start is not None:
        year, month = dry_start.year, dry_start.month
        while (year, month) <= (today.year, today.month):
            if date(year, month, 1) + timedelta(days=7) >= dry_start:
                per_month.setdefault((year, month), [0.0])
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    years: dict[int, list[float | None]] = {}
    for (year, month), values in per_month.items():
        running = (year, month) == (today.year, today.month)
        needed = 1 if (running or dry_start is not None) else calendar.monthrange(year, month)[1] * MONTH_MIN_COVERAGE
        if len(values) < needed:
            continue
        years.setdefault(year, [None] * 12)[month - 1] = round(
            _mean(values) if kind == "mean" else sum(values), 3
        )
    past = sorted(year for year in years if year < today.year)
    avg: list[float | None] = []
    low: list[float | None] = []
    high: list[float | None] = []
    counts: list[int] = []
    for month in range(12):
        column = [years[year][month] for year in past if years[year][month] is not None]
        counts.append(len(column))
        avg.append(round(sum(column) / len(column), 3) if column else None)
        low.append(min(column) if column else None)
        high.append(max(column) if column else None)
    return {
        "years": {str(year): values for year, values in sorted(years.items())},
        "current_year": today.year, "current_month": today.month,
        "avg": avg, "low": low, "high": high, "counts": counts,
    }


def headline(points: list[DailyPoint], tz: ZoneInfo, today: date) -> dict | None:
    """Kopfsatz: laufender Monat bis gestern gegen dasselbe Stück der Vorjahre (gleicher Ausschnitt).

    Am Monatsersten gibt es noch keinen abgeschlossenen Tag, dann zählt der ganze Vormonat.
    Erst ab zwei Vorjahren mit ausreichender Abdeckung; None, wenn die Grundlage fehlt."""
    if today.day == 1:
        month_year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        last_day = calendar.monthrange(month_year, month)[1]
    else:
        month_year, month, last_day = today.year, today.month, today.day - 1
    means: dict[int, float] = {}
    for year in range(month_year - 12, month_year + 1):
        values = [
            point.value for point in points
            if point.covered and point.value is not None
            and (day := day_of(point, tz)).year == year and day.month == month and day.day <= last_day
        ]
        if len(values) >= max(1, last_day * 0.8):
            means[year] = sum(values) / len(values)
    current = means.pop(month_year, None)
    reference = [value for year, value in sorted(means.items()) if year < month_year]
    if current is None or len(reference) < 2:
        return None
    years = sorted(year for year in means if year < month_year)
    return {
        "month": month, "year": month_year, "last_day": last_day,
        "current": round(current, 2), "reference": round(sum(reference) / len(reference), 2),
        "first_year": years[0], "last_year": years[-1], "n_years": len(reference),
    }


def kenntage(points: list[DailyPoint], tz: ZoneInfo, today: date, unit: str | None = None) -> dict:
    """Kenntage je Jahr und Monat. Der laufende Tag zählt nie, Tage ohne Abdeckung auch nicht.

    Rückgabe: {Jahr: {"frost": [12], "eis": [12], "sommer": [12], "heiss": [12], "days": [12]}} mit
    "days" als Zahl der gezählten Tage je Monat (Grundlage für „n von m Tagen“). ``unit`` ist die Einheit der
    Temperatur-Entität; die Grenzwerte sind °C und werden dorthin umgerechnet."""
    limits = _kenntage_limits(unit)
    result: dict[int, dict[str, list[int]]] = {}
    for point in points:
        day = day_of(point, tz)
        if day >= today or not point.covered or point.min_value is None or point.max_value is None:
            continue
        year = result.setdefault(day.year, {key: [0] * 12 for key, *_rest in KENNTAGE} | {"days": [0] * 12})
        year["days"][day.month - 1] += 1
        for key, limit, field, direction in limits:
            value = getattr(point, field)
            if (value < limit) if direction == "below" else (value >= limit):
                year[key][day.month - 1] += 1
    return {str(year): counts for year, counts in sorted(result.items())}


def count_kenntage(
    points: list[DailyPoint], tz: ZoneInfo, window_start: float, window_end: float, today: date,
    unit: str | None = None,
) -> dict | None:
    """Kenntage über ein Fenster (Tabellenzeilen). None ohne belegten Tag. ``unit`` wie bei kenntage()."""
    limits = _kenntage_limits(unit)
    counts = {key: 0 for key, *_rest in KENNTAGE}
    days = 0
    for point in points:
        if not (window_start <= point.day_start < window_end):
            continue
        if day_of(point, tz) >= today or not point.covered or point.min_value is None or point.max_value is None:
            continue
        days += 1
        for key, limit, field, direction in limits:
            value = getattr(point, field)
            if (value < limit) if direction == "below" else (value >= limit):
                counts[key] += 1
    return {**counts, "days": days} if days else None


def records(points: list[DailyPoint], tz: ZoneInfo, today: date) -> dict | None:
    """Wärmster/kältester Tag, höchster/tiefster Wert aus einer Temperaturreihe.

    Erst ab RECORDS_MIN_DAYS belegten Tagen, sonst wäre „seit Archivbeginn“ ein Wochenrekord.
    Gezählt werden nur abgedeckte, abgeschlossene Tage."""
    usable = [
        point for point in points
        if point.covered and point.value is not None and point.min_value is not None
        and point.max_value is not None and day_of(point, tz) < today
    ]
    if len(usable) < RECORDS_MIN_DAYS:
        return None

    def entry(point: DailyPoint, value: float) -> dict:
        return {"value": round(value, 2), "date": day_of(point, tz).isoformat()}

    warmest = max(usable, key=lambda p: (p.value, -p.day_start))
    coldest = min(usable, key=lambda p: (p.value, p.day_start))
    highest = max(usable, key=lambda p: (p.max_value, -p.day_start))
    lowest = min(usable, key=lambda p: (p.min_value, p.day_start))
    return {
        "warmest_day": entry(warmest, warmest.value), "coldest_day": entry(coldest, coldest.value),
        "highest": entry(highest, highest.max_value), "lowest": entry(lowest, lowest.min_value),
        "days": len(usable), "since": day_of(usable[0], tz).isoformat(),
    }


# -- Sonne (Langzeit) -----------------------------------------------------------------------------

#: Tagesklassen der Sonne als Zahl in der Jahr×Tag-Matrix (Farbstufen von trüb bis sonnig).
SUN_CLASS_CODES = {"overcast": 0, "mixed": 1, "sunny": 2}


def sun_points(days: list[dict], tz: ZoneInfo, field: str) -> list[DailyPoint]:
    """Tageswerte aus den Sonnentagen (``WetterService._sun_days``), damit Monatswerte und Matrix dieselben Regeln
    nutzen wie die Temperatur. ``field`` ``hours``: Sonnenstunden jedes Tages mit Werten; ``class``: Klassenzahl,
    nur für Tage mit Klasse (die anderen fehlen in der Matrix)."""
    points = []
    for entry in days:
        value = entry["hours"] if field == "hours" else (
            SUN_CLASS_CODES[entry["class"]] if entry["class"] else None
        )
        if value is None:
            continue
        stamp = datetime(entry["date"].year, entry["date"].month, entry["date"].day, tzinfo=tz).timestamp()
        points.append(DailyPoint(day_start=stamp, value=float(value)))
    return points


def sun_kenntage(days: list[dict]) -> dict:
    """Sonnentage und trübe Tage je Jahr und Monat (Form wie ``kenntage``): ``sunny`` und ``overcast`` zählen Tage der
    Klasse sonnig beziehungsweise trüb, ``days`` die Tage mit Klasse. Wechselhafte Tage zählen nur in ``days``."""
    result: dict[int, dict[str, list[int]]] = {}
    for entry in days:
        if entry["class"] is None:
            continue
        year = result.setdefault(entry["date"].year, {"sunny": [0] * 12, "overcast": [0] * 12, "days": [0] * 12})
        month = entry["date"].month - 1
        year["days"][month] += 1
        if entry["class"] == "sunny":
            year["sunny"][month] += 1
        elif entry["class"] == "overcast":
            year["overcast"][month] += 1
    return {str(year): counts for year, counts in sorted(result.items())}


def _longest_run(days: list[dict], kind: str) -> dict | None:
    """Längste Folge aufeinanderfolgender Tage der Klasse ``kind``; bei Gleichstand die früheste."""
    best: tuple[int, date, date] | None = None
    start: date | None = None
    previous: date | None = None
    length = 0
    for entry in days:
        if entry["class"] != kind:
            start = None
        else:
            if start is None or previous is None or (entry["date"] - previous).days != 1:
                start, length = entry["date"], 0
            length += 1
            if best is None or length > best[0]:
                best = (length, start, entry["date"])
        previous = entry["date"]
    if best is None:
        return None
    return {"value": best[0], "date": best[1].isoformat(), "end": best[2].isoformat()}


def sun_records(days: list[dict], monthly_hours: dict, today: date) -> dict | None:
    """Rekorde der Sonne: sonnigster Tag, sonnigster Monat (nur abgeschlossene Monate aus ``monthly``) und die
    längste Folge sonniger beziehungsweise trüber Tage. Erst ab RECORDS_MIN_DAYS Tagen mit Werten."""
    with_hours = [entry for entry in days if entry["hours"] is not None]
    if len(with_hours) < RECORDS_MIN_DAYS:
        return None
    top = max(with_hours, key=lambda entry: (entry["hours"], -entry["date"].toordinal()))
    out: dict = {
        "sunniest_day": {"value": round(top["hours"], 2), "date": top["date"].isoformat()},
        "since": with_hours[0]["date"].isoformat(), "days": len(with_hours),
    }
    best_month: tuple[float, int, int] | None = None
    for year, values in monthly_hours["years"].items():
        for month, value in enumerate(values, start=1):
            if value is None or (int(year), month) == (today.year, today.month):
                continue
            if best_month is None or value > best_month[0]:
                best_month = (value, int(year), month)
    if best_month is not None:
        out["sunniest_month"] = {"value": round(best_month[0], 1), "date": date(best_month[1], best_month[2], 1).isoformat()}
    for key, kind in (("longest_sunny_streak", "sunny"), ("longest_overcast_streak", "overcast")):
        run = _longest_run(days, kind)
        if run:
            out[key] = run
    return out


LEAP_SLOTS = 366  # Plätze der Jahr×Tag-Matrix: ein Schaltjahr, damit der 29.02. eine eigene Spalte hat


def matrix_slot(day: date) -> int:
    """Spalte 0..365 in der Jahr×Tag-Matrix (Bezugsjahr 2024, der 29.02. hat Platz 59)."""
    return date(2024, day.month, day.day).timetuple().tm_yday - 1


def matrix(
    points: list[DailyPoint], tz: ZoneInfo, today: date, field: str, scale: float = 1.0,
    archive_start: date | None = None, dry_fill: bool = False, require_coverage: bool = True,
) -> dict:
    """Jahr×Tag-Matrix: eine Zeile je Jahr (neuestes oben), eine Spalte je Kalendertag.

    field: Feld des Tageswerts (``value``, ``min_value``, ``max_value``). ``cells`` sind
    ``[Spalte, Zeile, Wert]``. ``missing`` sind Tage zwischen Archivbeginn und gestern ohne Wert
    (grau gezeichnet, nie als 0); Tage vor dem Archiv und in der Zukunft bleiben leer.
    dry_fill (Regensensor ein/aus): ein Tag ohne Zeile ist trocken (0), nicht fehlend.
    Der laufende Tag fehlt immer, sein Wert wandert noch."""
    values: dict[date, float] = {}
    for point in points:
        day = day_of(point, tz)
        raw = getattr(point, field)
        if day >= today or raw is None or (require_coverage and not point.covered):
            continue
        values[day] = raw * scale
    first = archive_start or (min(values) if values else None)
    if first is None:
        return {"years": [], "cells": [], "missing": [], "min": None, "max": None}
    years = list(range(today.year, first.year - 1, -1))
    cells: list[list[float]] = []
    missing: list[list[int]] = []
    for row, year in enumerate(years):
        day = max(date(year, 1, 1), first)
        stop = min(date(year, 12, 31), today - timedelta(days=1))
        while day <= stop:
            value = values.get(day)
            if value is None and dry_fill:
                value = 0.0
            if value is None:
                missing.append([matrix_slot(day), row])
            else:
                cells.append([matrix_slot(day), row, round(value, 2)])
            day += timedelta(days=1)
    numbers = [cell[2] for cell in cells]
    return {
        "years": years, "cells": cells, "missing": missing,
        "min": min(numbers) if numbers else None, "max": max(numbers) if numbers else None,
    }


# -- Heizen (Phase 3) ---------------------------------------------------------------------------

HEATING_MONTHS = (10, 11, 12, 1, 2, 3, 4)  # Heizperiode fest vom 1. Oktober bis 30. April


def heating_days(
    temperature: list[DailyPoint], counters: list[list[DailyPoint]], scales: list[float],
    tz: ZoneInfo, today: date,
) -> list[list]:
    """Tage der Heizperiode für das Streudiagramm und die Gradtagzahl: ``[Datum, Tages-Ø, [kWh je Zähler]]``.

    Gezählt wird nur ein abgeschlossener Tag (nicht heute) in den Monaten Oktober bis April mit
    ausreichender Temperaturabdeckung und mindestens einem Zähler mit Wert. Ein Zähler ohne Zeile an
    einem solchen Tag steuert 0 bei (er hat nichts gemessen); fehlt jeder Zähler, fehlt der Tag.
    ``scales`` rechnet die Zählereinheit in kWh um (Wh: 0,001, MWh: 1000)."""
    kwh: list[dict[date, float]] = [
        {day_of(point, tz): (point.value or 0.0) * scale for point in series}
        for series, scale in zip(counters, scales, strict=True)
    ]
    days: list[list] = []
    for point in temperature:
        day = day_of(point, tz)
        if day >= today or day.month not in HEATING_MONTHS or not point.covered or point.value is None:
            continue
        per_counter = [values.get(day) for values in kwh]
        if all(value is None for value in per_counter):
            continue
        days.append([day.isoformat(), round(point.value, 2), [round(v or 0.0, 3) for v in per_counter]])
    return days
