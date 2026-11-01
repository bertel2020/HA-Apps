"""Energiedashboard: Bucket-Serien für die Kachel-Popups auf eine feste Achse bringen.

Die Serien aus ``EnergiedashboardService._entity_series`` taugen nicht direkt für ein
Diagramm, in dem zwei Zeiträume übereinander liegen:

* Beim laufenden Zeitraum (``offset == 0``) ersetzt ``_entity_series`` den letzten Bucket
  durch die feinere Aufschlüsselung (Monat: Stunden für heute, Jahr: Tage für den aktuellen
  Monat). Die Serie mischt dann Bucket-Breiten.
* Ein Index taugt nicht als Achse: der 25.10.2026 hat 25 Stunden-Buckets, Monate sind
  unterschiedlich lang, und die Vorperiode hat ein anderes Fenster als die aktuelle.

Hier steht deshalb ein Kalenderschlüssel (lokale Stunde, Tag des Monats, Monat, …), auf den
beide Serien gefaltet werden. Alle Werte sind Perioden-Deltas (Summen): Falten heißt addieren,
das ergibt für grobe wie für feine Buckets dasselbe. Reine Funktionen ohne Zugriff auf App oder
Index; die Eingaben werden nie verändert (``_entity_series`` teilt seine Ergebnisse über den
Request-Memo).
"""

from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo


def bucket_key(ts: float, range_key: str, tz: ZoneInfo) -> int:
    """Kalenderschlüssel eines Buckets: Fünf-Minuten-Raster (Stunde), lokale Stunde (Tag), Wochentag
    0–6 (Woche), Tag des Monats (Monat), Monat 1–12 (Jahr), letzte Ziffer des Jahres (Dekade).
    Zähler-Buckets der Stunde liegen ohnehin auf dem Fünf-Minuten-Raster; feinere Messreihen
    (Ladestand, Minuten-Buckets) werden darauf abgerundet."""
    local = datetime.fromtimestamp(ts, tz)
    if range_key == "hour":
        return local.minute // 5 * 5
    if range_key == "day":
        return local.hour
    if range_key == "week":
        return local.weekday()
    if range_key == "month":
        return local.day
    if range_key == "year":
        return local.month
    if range_key == "decade":
        return local.year % 10
    raise ValueError(range_key)


def axis_keys(range_key: str, window_start: datetime) -> list[int]:
    """Alle Schlüssel des Zeitraums in Anzeigereihenfolge, auch die noch nicht erreichten
    (laufender Zeitraum) und die ohne Daten. ``window_start`` ist der lokale Fensteranfang."""
    if range_key == "hour":
        return list(range(0, 60, 5))
    if range_key == "day":
        return list(range(24))
    if range_key == "week":
        return list(range(7))
    if range_key == "month":
        return list(range(1, calendar.monthrange(window_start.year, window_start.month)[1] + 1))
    if range_key == "year":
        return list(range(1, 13))
    if range_key == "decade":
        return list(range(10))
    raise ValueError(range_key)


def fold_series(series: dict[float, float], range_key: str, tz: ZoneInfo) -> dict[int, float]:
    """Summiert die Buckets je Kalenderschlüssel. Faltet damit den feinen Rest des laufenden
    Zeitraums zurück in seinen Tages-/Monats-Bucket und fasst die doppelte Stunde am Ende der
    Sommerzeit (25-Stunden-Tag) zusammen; die fehlende Stunde bei Beginn der Sommerzeit bleibt
    ohne Schlüssel."""
    folded: dict[int, float] = {}
    for ts, value in series.items():
        key = bucket_key(ts, range_key, tz)
        folded[key] = folded.get(key, 0.0) + (value or 0.0)
    return folded


def fold_mean(points: dict[float, float], range_key: str, tz: ZoneInfo) -> dict[int, float]:
    """Mittelwert je Kalenderschlüssel, für Messreihen (Ladestand in %) statt Summen. Die Buckets einer
    Messreihe sind gleich breit, ein einfacher Mittelwert entspricht daher dem zeitgewichteten."""
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for ts, value in points.items():
        key = bucket_key(ts, range_key, tz)
        sums[key] = sums.get(key, 0.0) + value
        counts[key] = counts.get(key, 0) + 1
    return {key: sums[key] / counts[key] for key in sums}


def align(
    current: dict[float, float], previous: dict[float, float], range_key: str,
    window_start: datetime, tz: ZoneInfo,
) -> dict:
    """Aktuelle Serie und Vorperiode auf dieselbe Achse legen.

    ``window_start`` ist der lokale Anfang des AKTUELLEN Fensters (bestimmt die Länge des
    Monats). Schlüssel ohne Wert sind ``None`` und nicht ``0``: ein noch nicht erreichter oder
    nicht vorhandener Bucket soll im Diagramm leer bleiben, ein gemessenes ``0`` dagegen als
    Nullbalken erscheinen. Hat die Vorperiode mehr Tage als die aktuelle (31 gegen 30), gehen die
    überzähligen Schlüssel der Vorperiode bewusst nicht in die Achse ein."""
    keys = axis_keys(range_key, window_start)
    cur = fold_series(current, range_key, tz)
    prev = fold_series(previous, range_key, tz)
    return {
        "keys": keys,
        "current": [round(cur[k], 3) if k in cur else None for k in keys],
        "previous": [round(prev[k], 3) if k in prev else None for k in keys],
    }
