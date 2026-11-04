"""Tageslichtkurve des Wetterdashboards: Nacht, Dämmerung und Tag aus dem Dämmerungssensor (ROADMAP 1.43).

Reine Funktionen ohne Zugriff auf den Speicher; ``wetter_routes.py`` liest die Werte eines Tages und reicht
sie hierher. Der Sensor misst von 0 bis 1.000 lx und zeigt, wann es *für ihn* hell wird, nicht den Sonnenstand:
dichte Wolken verschieben „Tag ab 400 lx“ nach hinten, Ausrichtung und Verschattung wirken ebenfalls.

Zwei Auflösungen:

- **fein** (Rohdaten in 5-Minuten-Mitteln): Die Zeiten ergeben sich aus den Schwellenüberschreitungen,
  linear zwischen zwei Messpunkten interpoliert.
- **Stunden** (nur Stundenzeilen, etwa bei älteren Tagen): Eine Stunde zählt als Tag, wenn ihr Mittel die
  Tag-Schwelle erreicht, als Dämmerung, wenn ihr Maximum die Nacht-Schwelle erreicht. Die Zeiten sind dann auf
  eine Stunde genau und mit ``approx`` gekennzeichnet.
"""

from __future__ import annotations

NIGHT = "night"
DUSK = "dusk"
DAY = "day"

#: Zwei Messpunkte weiter auseinander als das gelten als Lücke: Der Speicher legt nur bei neuen Werten Zeilen an,
#: ein Sensor, der mittags auf 1.000 lx steht, liefert stundenlang keine.
MAX_STEP_SECONDS = 900


def classify(value: float, night_lx: float, day_lx: float) -> str:
    if value >= day_lx:
        return DAY
    return DUSK if value >= night_lx else NIGHT


def hold_points(
    points: list[tuple[float, float]], day_start: float, until: float, night_lx: float,
) -> list[tuple[float, float]]:
    """Ergänzt gehaltene Werte: Wo keine Zeile liegt, gilt der letzte Wert weiter.

    Vor der ersten Zeile hält die Reihe ihren Wert nur, wenn er unter der Nacht-Schwelle liegt (die Nacht davor);
    ein heller erster Wert sagt nichts über die Stunden davor. Nach der letzten Zeile gilt der Wert bis ``until``
    (jetzt oder Tagesende)."""
    if not points:
        return []
    ordered = sorted(points)
    out: list[tuple[float, float]] = []
    if ordered[0][1] < night_lx and ordered[0][0] > day_start:
        out.append((day_start, ordered[0][1]))
    for timestamp, value in ordered:
        if out and timestamp - out[-1][0] > MAX_STEP_SECONDS:
            out.append((timestamp - 1, out[-1][1]))
        out.append((timestamp, value))
    if until > out[-1][0]:
        out.append((until, out[-1][1]))
    return out


def _crossing(a: tuple[float, float], b: tuple[float, float], threshold: float) -> float:
    """Zeit, zu der die Gerade von ``a`` nach ``b`` die Schwelle schneidet."""
    if b[1] == a[1]:
        return b[0]
    return a[0] + (threshold - a[1]) / (b[1] - a[1]) * (b[0] - a[0])


def crossings(points: list[tuple[float, float]], threshold: float) -> tuple[float | None, float | None]:
    """(erster Anstieg über die Schwelle, letzter Abfall darunter); None, wenn es keinen gibt."""
    first_up = last_down = None
    for a, b in zip(points, points[1:], strict=False):
        if a[1] < threshold <= b[1] and first_up is None:
            first_up = _crossing(a, b, threshold)
        if a[1] >= threshold > b[1]:
            last_down = _crossing(a, b, threshold)
    return first_up, last_down


def bands_from_points(points: list[tuple[float, float]], night_lx: float, day_lx: float) -> list[list]:
    """Zusammenhängende Abschnitte [von, bis, Klasse] entlang der Kurve; Wechsel an den Schwellenschnitten."""
    bands: list[list] = []

    def add(start: float, end: float, kind: str) -> None:
        if end <= start:
            return
        if bands and bands[-1][2] == kind and bands[-1][1] == start:
            bands[-1][1] = end
        else:
            bands.append([start, end, kind])

    for a, b in zip(points, points[1:], strict=False):
        cuts = sorted(
            _crossing(a, b, threshold)
            for threshold in (night_lx, day_lx)
            if min(a[1], b[1]) < threshold <= max(a[1], b[1])
        )
        edges = [a[0], *cuts, b[0]]
        for start, end in zip(edges, edges[1:], strict=False):
            middle = (start + end) / 2
            fraction = (middle - a[0]) / (b[0] - a[0]) if b[0] != a[0] else 0.0
            add(start, end, classify(a[1] + fraction * (b[1] - a[1]), night_lx, day_lx))
    return bands


def summarize_fine(
    points: list[tuple[float, float]], night_lx: float, day_lx: float, until: float, live: bool,
) -> dict:
    """Zeiten und Längen aus einer feinen Kurve; ``live``: der Tag läuft noch (``until`` ist dann jetzt)."""
    night_up, night_down = crossings(points, night_lx)
    day_up, day_down = crossings(points, day_lx)
    ongoing = bool(live and points and points[-1][1] >= day_lx and day_up is not None)
    if ongoing:
        day_down = None
    end = until if ongoing else day_down
    return {
        "from": day_up, "to": day_down, "ongoing": ongoing, "approx": False,
        "length": end - day_up if day_up is not None and end is not None else None,
        "morning": day_up - night_up if day_up is not None and night_up is not None else None,
        "evening": night_down - day_down if day_down is not None and night_down is not None else None,
        "dawn": night_up, "dusk": night_down,
        "bands": bands_from_points(points, night_lx, day_lx),
    }


def hour_classes(
    hour_starts: list[float], rows: dict[float, tuple[float, float]], night_lx: float, day_lx: float,
) -> list[str | None]:
    """Klasse je Stunde (Tag bei Mittel ab Tag-Schwelle, Dämmerung bei Maximum ab Nacht-Schwelle).

    Stunden ohne Zeile werden aufgefüllt, wenn davor und danach dieselbe Klasse steht (ein Sensor, der seinen
    Wert hält, legt keine Zeile an), liegen zwei verschiedene Klassen davor und danach, gilt Dämmerung. Vor der
    ersten und nach der letzten Zeile bleibt die Stunde leer (``None``): dort gibt es keine Aussage."""
    known: list[str | None] = []
    for start in hour_starts:
        row = rows.get(start)
        if row is None:
            known.append(None)
            continue
        mean, peak = row
        known.append(DAY if mean >= day_lx else DUSK if peak >= night_lx else NIGHT)
    positions = [i for i, kind in enumerate(known) if kind is not None]
    if not positions:
        return known
    for i in range(positions[0], positions[-1] + 1):
        if known[i] is not None:
            continue
        before = next(known[j] for j in range(i - 1, -1, -1) if known[j] is not None)
        after = next(known[j] for j in range(i + 1, len(known)) if known[j] is not None)
        known[i] = before if before == after else DUSK
    return known


def summarize_hours(hour_starts: list[float], classes: list[str | None]) -> dict:
    """Zeiten und Längen aus den Stundenklassen; auf eine Stunde genau."""
    day_hours = [i for i, kind in enumerate(classes) if kind == DAY]
    bands: list[list] = []
    for start, kind in zip(hour_starts, classes, strict=True):
        if kind is None:
            continue
        if bands and bands[-1][2] == kind and bands[-1][1] == start:
            bands[-1][1] = start + 3600
        else:
            bands.append([start, start + 3600, kind])
    if not day_hours:
        return {
            "from": None, "to": None, "ongoing": False, "approx": True, "length": None, "morning": None,
            "evening": None, "dawn": None, "dusk": None, "bands": bands,
        }
    first, last = day_hours[0], day_hours[-1]
    return {
        "from": hour_starts[first], "to": hour_starts[last] + 3600, "ongoing": False, "approx": True,
        "length": len(day_hours) * 3600.0,
        "morning": sum(1 for kind in classes[:first] if kind == DUSK) * 3600.0,
        "evening": sum(1 for kind in classes[last + 1:] if kind == DUSK) * 3600.0,
        "dawn": None, "dusk": None, "bands": bands,
    }
