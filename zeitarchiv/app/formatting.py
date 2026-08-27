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
                return f"{format_int(int(value))} {unit}"
            return f"{_localize_number_text(f'{value:.1f}')} {unit}"
        value /= 1024
    return f"{_localize_number_text(f'{value:.1f}')} TB"  # unreachable, beruhigt nur Type-Checker


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


def format_uptime(seconds: float) -> str:
    """Kompakte Laufzeit-Anzeige ("3 Tage 4 Std.", "45 Min.", "12 Sek.") für
    die Prozess-Laufzeit in "Über Zeitarchiv" — höchstens zwei Einheiten,
    dieselbe Kompakt-Idee wie NumberFormat.fmtDuration() auf der JS-Seite
    (static/js/number-format.js, dort für Schalter-Einschaltdauer), hier
    zusätzlich mit einer Tage-Stufe für mehrtägige Laufzeiten."""
    total = max(0, round(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        suffix = f" {hours} Std." if hours else ""
        return f"{days} Tag{'e' if days != 1 else ''}{suffix}"
    if hours:
        suffix = f" {minutes} Min." if minutes else ""
        return f"{hours} Std.{suffix}"
    if minutes:
        return f"{minutes} Min."
    return f"{secs} Sek."


# Zentrale Stelle für das Zahlenformat der Oberfläche — aktuell nur Deutsch
# (Komma als Dezimal-, Punkt als Tausendertrennzeichen). Eine künftige
# Sprachumschaltung (z. B. Englisch, NUMBER_LOCALE="en-US") ändert nur diese
# eine Konstante bzw. wählt den Eintrag dynamisch (z. B. aus einer
# Nutzereinstellung) — format_value()/format_int() selbst kennen kein
# hartcodiertes Trennzeichen mehr. Dasselbe Prinzip wie auf der JS-Seite
# (static/js/number-format.js, window.NumberFormat.LOCALE).
NUMBER_SEPARATORS = {
    "de-DE": {"decimal": ",", "thousands": "."},
    "en-US": {"decimal": ".", "thousands": ","},
}
NUMBER_LOCALE = "de-DE"


def _group_thousands(int_text: str, sep: str) -> str:
    negative = int_text.startswith("-")
    digits = int_text[1:] if negative else int_text
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    grouped = sep.join(groups)
    return f"-{grouped}" if negative else grouped


def _localize_number_text(text: str, locale: str = NUMBER_LOCALE) -> str:
    """Wandelt einen Punkt-Dezimal-String (z. B. "1234.5") ins Oberflächen-
    format des angegebenen locale um, inkl. Tausendergruppierung."""
    seps = NUMBER_SEPARATORS[locale]
    int_part, _, frac_part = text.partition(".")
    grouped = _group_thousands(int_part, seps["thousands"])
    return f"{grouped}{seps['decimal']}{frac_part}" if frac_part else grouped


def format_int(value: int, signed: bool = False) -> str:
    """Tausendergruppierte Ganzzahl im aktuellen Oberflächenformat
    (NUMBER_LOCALE) — für Zeilen-/Datensatzzähler u. Ä. in der GUI.
    signed=True erzwingt ein führendes "+" bei positiven Werten (z. B. für
    eine Differenzanzeige "+42" / "-15"), wie Pythons eigenes "{:+}"."""
    value = int(value)
    text = _localize_number_text(str(value))
    if signed and value >= 0:
        text = f"+{text}"
    return text


def format_value(value: float, decimals: int | None = None) -> str:
    """decimals=None (Standard/"Automatisch"): bis zu 3 Nachkommastellen, aber
    überflüssige Nullen abgeschnitten — ein ganzzahliger Rohwert (z. B. 4 W) zeigt
    "4" statt "4.000", während ein echter Nachkommawert (z. B. 21.437 °C oder ein
    Zähler-Rohwert wie 6403.06) lesbar bleibt statt auf eine feste
    Nachkommastellenzahl aufgefüllt zu werden. Mit explizitem decimals (pro
    Entität konfigurierbar, Konzept Abschnitt 03) wird stattdessen immer genau
    auf diese Anzahl gerundet/aufgefüllt — auch wenn das Nullen anhängt.
    Ergebnis im Oberflächenformat (NUMBER_LOCALE), inkl. Tausendergruppierung."""
    if decimals is not None:
        text = f"{value:.{decimals}f}"
    else:
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        if not text or text in ("-", ""):
            text = "0"
    return _localize_number_text(text)


def parse_localized_number(text: str, locale: str = NUMBER_LOCALE) -> float:
    """Gegenstück zu format_value()/format_int() für Freitext-Zahleneingaben im
    Oberflächenformat (z. B. ein vom Nutzer getippter Umrechnungsfaktor beim
    Symcon-Import) — Dezimal-/Tausendertrennzeichen kommen aus NUMBER_SEPARATORS
    statt hartcodiert zu sein, damit eine künftige Sprachumschaltung automatisch
    mitzieht. Dasselbe Prinzip wie NumberFormat.parse() auf der JS-Seite
    (static/js/number-format.js)."""
    seps = NUMBER_SEPARATORS[locale]
    normalized = text.strip()
    if seps["thousands"]:
        normalized = normalized.replace(seps["thousands"], "")
    if seps["decimal"] != ".":
        normalized = normalized.replace(seps["decimal"], ".")
    return float(normalized)


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
VALUE_FILTER_LABELS = {
    "off": "Aus",
    "decimals": "Gleiche gerundete Werte filtern",
}
GAP_THRESHOLD_LABELS = {
    "1": "1 Minute",
    "5": "5 Minuten",
    "15": "15 Minuten",
    "30": "30 Minuten",
    "60": "1 Stunde",
    "1440": "1 Tag",
    "off": "Aus",
}
DISPLAY_MODE_LABELS = {
    "onoff": "AN/AUS (Rohwert)",
    "time": "Zeit (Dauer)",
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
