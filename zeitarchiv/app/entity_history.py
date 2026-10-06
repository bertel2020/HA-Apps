"""Reiter „Verlauf“ im Bearbeitungsbereich einer Entität: die Einträge aus
``entity_actions`` (dieselbe Tabelle wie Housekeeping → Aktivität), gefiltert auf
eine Entität, mit den Einzelheiten, die ein Protokoll erst brauchbar machen —
bei einer Korrektur der alte und der neue Wert, beim Zurücknehmen welcher Art.

Älteste Einträge kennen diese Einzelheiten nicht (das Markieren und Zurücknehmen
wurde gar nicht protokolliert, bei Korrekturen fehlten die Werte); sie erscheinen
mit den Angaben, die es damals gab, statt gar nicht."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .formatting import format_compact_target, format_int, format_time, format_timestamp, format_value
from .i18n import N_, tr

# Filter-Chips → Aktionen in entity_actions. "marked" fasst Markieren und
# Zurücknehmen zusammen: zusammen erzählen sie erst, was mit einem Wert geschah.
FILTERS: dict[str, tuple[str, ...]] = {
    "all": (),
    "marked": ("mark", "undo"),
    "correct": ("correct",),
    "add": ("add",),
    "purge": ("purge",),
    "compact": ("compact",),
}
FILTER_LABELS = {
    "all": N_("Alle"),
    "marked": N_("Markiert / Rückgängig"),
    "correct": N_("Korrigiert"),
    "add": N_("Hinzugefügt"),
    "purge": N_("Entfernt"),
    "compact": N_("Verdichtet"),
}
DAYS_OPTIONS = (30, 90, 365, 0)
DEFAULT_DAYS = 90
LIMIT = 300

# aktion → (CSS-Klasse der Marke, Beschriftung)
_TAGS = {
    "mark": ("mark", N_("Markiert")),
    "undo": ("undo", N_("Rückgängig")),
    "correct": ("fix", N_("Korrigiert")),
    "add": ("fix", N_("Hinzugefügt")),
    "purge": ("purge", N_("Entfernt")),
    "compact": ("misc", N_("Verdichtet")),
}


def _detail(row) -> dict:
    try:
        value = json.loads(row["detail"]) if row["detail"] else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _when(ts: float, tz: ZoneInfo) -> str:
    return f"{format_timestamp(ts, tz)[:6]} {format_time(ts, tz)[:5]}"


def _value_label(value, decimals: int | None, unit: str | None) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{format_value(value, decimals)} {unit}".strip() if unit else format_value(value, decimals)


def _event(row, tz: ZoneInfo, decimals: int | None, unit: str | None) -> dict:
    action = row["action"]
    detail = _detail(row)
    count = int(row["rows_affected"] or 0)
    css, label = _TAGS.get(action, ("misc", action))
    event = {
        "time": format_time(row["created_at"], tz)[:5],
        "css": css,
        "label": tr(label) if action in _TAGS else label,
        "title": "",
        "sub": "",
        "old": None,
        "new": None,
        "link_marked": action in ("mark", "undo"),
        "failed": row["status"] in ("failed", "interrupted"),
    }
    if action == "mark":
        event["title"] = tr("1 Wert zum Entfernen markiert") if count == 1 else tr("{count} Werte zum Entfernen markiert", count=count)
    elif action == "undo":
        event["title"] = tr("1 Wert wieder aktiv") if count == 1 else tr("{count} Werte wieder aktiv", count=count)
        event["sub"] = {
            "selection": tr("Ausgewählte Markierungen zurückgenommen"),
            "batch": tr("Eine Charge zurückgenommen"),
            "all": tr("Alle Markierungen zurückgenommen"),
            "last": tr("Letzte Löschung zurückgenommen"),
        }.get(detail.get("mode"), "")
    elif action == "correct":
        if "ts" in detail:
            event["title"] = tr("1 Wert am {when}", when=_when(detail["ts"], tz))
            event["old"] = _value_label(detail.get("old"), decimals, unit)
            event["new"] = _value_label(detail.get("new"), decimals, unit)
        else:
            event["title"] = tr("1 Wert korrigiert")
    elif action == "add":
        if "ts" in detail:
            event["title"] = tr("1 Wert am {when} hinzugefügt", when=_when(detail["ts"], tz))
            event["sub"] = _value_label(detail.get("value"), decimals, unit)
        else:
            event["title"] = tr("1 Wert hinzugefügt")
    elif action == "purge":
        event["title"] = tr("1 Wert endgültig entfernt") if count == 1 else tr("{count} Werte endgültig entfernt", count=count)
    elif action == "compact":
        event["title"] = tr("Rohwerte verdichtet")
        parts = []
        if detail.get("target_resolution"):
            parts.append(tr("Zielauflösung: {target}", target=tr(format_compact_target(detail["target_resolution"]))))
        before, after = detail.get("rows_before"), detail.get("rows_after")
        if before is not None and after is not None:
            parts.append(tr("Zeilen: {before} → {after}", before=format_int(before), after=format_int(after)))
        event["sub"] = " · ".join(parts)
    else:
        event["title"] = label
    if row["trigger"] == "automatic":
        event["sub"] = " · ".join(part for part in (event["sub"], tr("automatisch")) if part)
    if event["failed"]:
        event["sub"] = row["error"] or tr("fehlgeschlagen")
    return event


def build_days(rows, tz: ZoneInfo, decimals: int | None, unit: str | None, now: datetime | None = None) -> list[dict]:
    """Gruppiert die (neueste zuerst sortierten) Zeilen nach lokalem Kalendertag."""
    today = (now or datetime.now(tz)).date()
    days: list[dict] = []
    for row in rows:
        day = datetime.fromtimestamp(row["created_at"], tz).date()
        if not days or days[-1]["date"] != day:
            delta = (today - day).days
            if delta == 0:
                label = tr("Heute · {date}", date=format_timestamp(row["created_at"], tz))
            elif delta == 1:
                label = tr("Gestern · {date}", date=format_timestamp(row["created_at"], tz))
            else:
                label = format_timestamp(row["created_at"], tz)
            days.append({"date": day, "label": label, "events": []})
        days[-1]["events"].append(_event(row, tz, decimals, unit))
    return days
