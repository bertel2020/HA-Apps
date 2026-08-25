"""Menschenlesbare Darstellung für die Entitäten-Tabelle (Konzept Abschnitt 03)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # unreachable, beruhigt nur Type-Checker


def format_timestamp(ts: float | None, tz: ZoneInfo) -> str:
    """tz explizit statt UTC (time.gmtime) — sonst kann das angezeigte Datum bei
    Zeitstempeln nahe Mitternacht vom tatsächlichen lokalen Kalendertag abweichen
    (z. B. 00:32 Europe/Berlin ist noch der Vortag in UTC), inkonsistent zu allen
    anderen Kalendergrenzen in der App (Konzept Abschnitt 05), die konsequent die
    konfigurierte Zeitzone verwenden."""
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y")


def format_time(ts: float | None, tz: ZoneInfo) -> str:
    """Uhrzeit-Gegenstück zu format_timestamp — für die Entitäten-Übersicht
    (Konzept Abschnitt 03), die Datum und Uhrzeit getrennt darstellt (Uhrzeit
    kleiner, unter dem Datum) statt beides in einer Zeile."""
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S")


def format_value(value: float, decimals: int | None = None) -> str:
    """decimals=None (Standard/"Automatisch"): bis zu 3 Nachkommastellen, aber
    überflüssige Nullen abgeschnitten — ein ganzzahliger Rohwert (z. B. 4 W) zeigt
    "4" statt "4.000", während ein echter Nachkommawert (z. B. 21.437 °C oder ein
    Zähler-Rohwert wie 6403.06) lesbar bleibt statt auf eine feste
    Nachkommastellenzahl aufgefüllt zu werden. Mit explizitem decimals (pro
    Entität konfigurierbar, Konzept Abschnitt 03) wird stattdessen immer genau
    auf diese Anzahl gerundet/aufgefüllt — auch wenn das Nullen anhängt."""
    if decimals is not None:
        return f"{value:.{decimals}f}"
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text and text not in ("-", "") else "0"


def decimals_to_int(value: str | None) -> int | None:
    """Wandelt den gespeicherten decimals-String ("auto" oder eine Ziffer) in den
    Parameter für format_value um."""
    if value is None or value == "auto":
        return None
    try:
        return int(value)
    except ValueError:
        return None


# Anzeige-Übersetzungen für die intern (englisch) gespeicherten Werte — die
# gespeicherten Werte selbst bleiben stabile Schlüssel (Sortierung, Filter-URLs,
# interne Logik in rollup.py/query.py), nur die Darstellung wird eingedeutscht.
TYPE_LABELS = {"standard": "Standard", "counter": "Zähler", "switch": "Schalter"}
RESOLUTION_LABELS = {
    "raw": "Rohdaten",
    "30s": "30 Sek.",
    "1min": "1 Min.",
    "5min": "5 Min.",
    "15min": "15 Min.",
    "1h": "1 Std.",
}
RETENTION_LABELS = {
    "unlimited": "Unbegrenzt",
    "30d": "30 Tage",
    "90d": "90 Tage",
    "365d": "365 Tage",
    "2y": "2 Jahre",
    "5y": "5 Jahre",
}
DECIMALS_LABELS = {
    "auto": "Automatisch",
    "0": "0 Nachkommastellen",
    "1": "1 Nachkommastelle",
    "2": "2 Nachkommastellen",
    "3": "3 Nachkommastellen",
}
GAP_THRESHOLD_LABELS = {
    "1": "1 Minute",
    "5": "5 Minuten",
    "15": "15 Minuten",
    "30": "30 Minuten",
    "60": "60 Minuten",
    "off": "Aus",
}
OUTLIER_THRESHOLD_LABELS = {
    "5": "5 %",
    "10": "10 %",
    "25": "25 %",
    "50": "50 %",
    "100": "100 %",
    "off": "Aus",
}
BACKUP_SCHEDULE_LABELS = {
    "off": "Aus",
    "daily": "Täglich",
    "weekly": "Wöchentlich",
}
BACKUP_KEEP_COUNT_LABELS = {
    "unlimited": "Unbegrenzt",
    "3": "3",
    "5": "5",
    "10": "10",
    "20": "20",
}
# Die drei bisherigen Schlüssel bleiben absichtlich erhalten: dadurch werden
# bestehende Installationen ohne Datenmigration exakt auf Kleiner/Klein/Normal
# umbenannt. "3" und "4" ergänzen die zwei neuen größeren Stufen.
FONT_SCALE_LABELS = {
    "0": "Kleiner",
    "1": "Klein",
    "2": "Normal",
    "3": "Groß",
    "4": "Größer",
}


def format_type(value: str) -> str:
    return TYPE_LABELS.get(value, value)


def format_resolution(value: str) -> str:
    return RESOLUTION_LABELS.get(value, value)


def format_retention(value: str) -> str:
    return RETENTION_LABELS.get(value, value)
