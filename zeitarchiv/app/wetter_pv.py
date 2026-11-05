"""PV-Ertrag gegen Helligkeit (experimentell): Was hätte die Anlage bei dieser Helligkeit liefern müssen?

Reine Funktionen ohne Archivzugriff. Die Erwartung wird aus den Vorwochen gelernt, nicht vorgegeben: je
Helligkeitsstufe der Median der Stundenleistung in den letzten ``WINDOW_DAYS`` Tagen vor dem betrachteten
Tag. Ein Median statt einer Geraden, weil der Zusammenhang gekrümmt ist (Module legen bei viel Licht schwächer
zu, der Süd-Sensor sättigt bei 98.800 lx, die Anlage steigt dort noch). Verglichen werden nur Stunden, für die
beide Seiten Werte haben und für die es eine gelernte Stufe gibt.

Das ist eine Messung, keine Prognose: Temperatur, Abregelung und ein voller Akku senken die Leistung ohne
Verschlechterung der Anlage, und der Sensor misst nur an einem Punkt.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

#: Untergrenzen der Helligkeitsstufen in lx; die erste Stufe trennt Dämmerung und Trübnis vom Rest.
BIN_EDGES = (0.0, 3000.0, 10000.0, 20000.0, 30000.0, 40000.0, 50000.0, 60000.0, 70000.0, 80000.0, 90000.0)
WINDOW_DAYS = 28
MIN_BIN_SAMPLES = 5
MIN_WINDOW_DAYS = 5  # Tage im Lernfenster, ab denen überhaupt eine Erwartung gebildet wird
MIN_DAY_HOURS = 6  # Stunden mit Erwartung, ab denen ein Tag verglichen wird
DEVIATION_LIMIT = 0.15  # darüber (nach oben oder unten) wird der Tag markiert
MIN_EXPECTED_PEAK_HOURS = 1.5  # Erwartung mindestens so viele Volllaststunden der Anlage, sonst ist die Abweichung zu zufällig
MIN_RATIO_DAYS = 5  # Tage je Uhrzeit für das Verhältnis nach Uhrzeit
MIN_HOUR_EXPECTED_SHARE = 0.05  # Stunden mit weniger Erwartung (Anteil der Spitze) zeigen kein Verhältnis: es wäre Rauschen


def bin_of(lux: float) -> int:
    """Index der Helligkeitsstufe (größte Untergrenze, die nicht über ``lux`` liegt)."""
    index = 0
    for position, edge in enumerate(BIN_EDGES):
        if lux >= edge:
            index = position
    return index


def learn_curve(samples: list[tuple[float, float]]) -> dict[int, float]:
    """Median der Leistung je Helligkeitsstufe aus ``(lux, kW)``-Paaren; Stufen mit zu wenig Stunden fehlen."""
    by_bin: dict[int, list[float]] = defaultdict(list)
    for lux, power in samples:
        by_bin[bin_of(lux)].append(power)
    return {index: median(values) for index, values in by_bin.items() if len(values) >= MIN_BIN_SAMPLES}


def evaluate(pairs: list[tuple[date, int, float, float]], show_days: list[date]) -> dict | None:
    """Tagesvergleich und Verhältnis nach Uhrzeit.

    ``pairs``: ``(Tag, Stunde, Helligkeit in lx, Leistung in kW)`` für jede Stunde mit beiden Werten. ``show_days``:
    die Tage, die ausgegeben werden. Je Tag zählen die ``WINDOW_DAYS`` Tage davor als Lernfenster. Ohne
    auswertbaren Tag ``None``."""
    if not pairs:
        return None
    by_day: dict[date, list[tuple[int, float, float]]] = defaultdict(list)
    for day, hour, lux, power in pairs:
        by_day[day].append((hour, lux, power))
    peak = max(power for _day, _hour, _lux, power in pairs)
    min_expected = peak * MIN_EXPECTED_PEAK_HOURS
    days_out: list[dict] = []
    hour_actual: dict[int, float] = defaultdict(float)
    hour_expected: dict[int, float] = defaultdict(float)
    hour_days: dict[int, int] = defaultdict(int)
    for day in show_days:
        window = [by_day[day - timedelta(days=offset)] for offset in range(1, WINDOW_DAYS + 1) if day - timedelta(days=offset) in by_day]
        if len(window) < MIN_WINDOW_DAYS:
            continue
        training = [(lux, power) for rows in window for _hour, lux, power in rows]
        curve = learn_curve(training)
        actual = expected = 0.0
        counted: list[tuple[int, float, float]] = []
        for hour, lux, power in by_day.get(day, ()):
            reference = curve.get(bin_of(lux))
            if reference is None:
                continue
            actual += power
            expected += reference
            counted.append((hour, power, reference))
        if len(counted) < MIN_DAY_HOURS or expected <= 0:
            continue
        deviation = actual / expected - 1.0
        days_out.append({
            "date": day.isoformat(), "actual": round(actual, 2), "expected": round(expected, 2),
            "deviation": round(deviation * 100, 1),
            "flagged": expected >= min_expected and abs(deviation) > DEVIATION_LIMIT,
            # Verhältnis je Stunde dieses Tages, für die Anzeige des gewählten Tages.
            "hours": [
                {"hour": hour, "ratio": round(power / reference, 3)}
                for hour, power, reference in sorted(counted) if reference >= peak * MIN_HOUR_EXPECTED_SHARE
            ],
        })
        for hour, power, reference in counted:
            hour_actual[hour] += power
            hour_expected[hour] += reference
            hour_days[hour] += 1
    if not days_out:
        return None
    hours_out = [
        {"hour": hour, "ratio": round(hour_actual[hour] / hour_expected[hour], 3), "days": hour_days[hour]}
        for hour in sorted(hour_days)
        if hour_days[hour] >= MIN_RATIO_DAYS and hour_expected[hour] > 0
    ]
    return {"days": days_out, "hours": hours_out, "peak_kw": round(peak, 2), "min_expected": round(min_expected, 2)}
