"""Was die automatische Bereinigung (Housekeeping → Speicherplatz) mit markierten Werten vorhat —
für Banner, Reiter „Markiert“ und die Meldung „Endgültige Bereinigung möglich“.

Die Automatik entfernt Markierungen, die älter als das Mindestalter sind, einmal täglich. Das Alter
zählt ab ``deleted_at`` (dem Zeitpunkt des Markierens), nicht ab dem Zeitstempel des Werts, und ihr
Lauf ist nicht zeitkritisch: ein Datum genügt, eine Uhrzeit wäre ein Implementierungsdetail."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .formatting import (
    DEFAULT_PURGE_AUTO_ENABLED,
    DEFAULT_PURGE_MIN_AGE_DAYS,
    PURGE_MIN_AGE_DAYS_LABELS,
    format_timestamp,
)
from .i18n import tr

# Eine Charge gilt als „bald dran“, wenn sie in höchstens so vielen Kalendertagen entfernt wird.
SOON_DAYS = 3
# Ab so vielen Tagen über dem Fälligkeitsdatum zeigt die Meldung wieder: die Automatik hätte längst
# laufen müssen und hat es nicht getan (ein Lauf pro Tag, dazu etwas Luft).
OVERDUE_GRACE_DAYS = 2


def settings(index) -> dict:
    """{"enabled": bool, "min_age_days": int, "age_label": str}"""
    days = int(index.get_setting("purge_min_age_days", DEFAULT_PURGE_MIN_AGE_DAYS))
    label = PURGE_MIN_AGE_DAYS_LABELS.get(str(days))
    return {
        "enabled": index.get_setting("purge_auto_enabled", DEFAULT_PURGE_AUTO_ENABLED) == "on",
        "min_age_days": days,
        "age_label": tr(label) if label else tr("{count} Tage", count=days),
    }


def batch_due(deleted_at: float, min_age_days: int, tz: ZoneInfo, now: float | None = None) -> dict:
    """Wann eine Charge dran ist: {"label", "soon"}. ``soon`` bei höchstens SOON_DAYS Kalendertagen
    oder wenn die Frist schon verstrichen ist (dann entfernt der nächste tägliche Lauf sie)."""
    now = time.time() if now is None else now
    due_ts = deleted_at + min_age_days * 86400
    if due_ts <= now:
        return {"label": tr("wird beim nächsten Lauf entfernt"), "soon": True}
    days_left = (datetime.fromtimestamp(due_ts, tz).date() - datetime.fromtimestamp(now, tz).date()).days
    date = format_timestamp(due_ts, tz)[:6]
    if days_left <= SOON_DAYS:
        suffix = tr("in 1 Tag") if days_left <= 1 else tr("in {count} Tagen", count=days_left)
        return {"label": tr("wird ab {date} entfernt, {in_days}", date=date, in_days=suffix), "soon": True}
    return {"label": tr("wird ab {date} entfernt", date=date), "soon": False}


def note_for_entity(index, entity_id: str, tz: ZoneInfo, now: float | None = None) -> dict:
    """Für den Banner: Automatik an/aus und, wenn an, wann die älteste Markierung dran ist."""
    state = settings(index)
    note = {"enabled": state["enabled"], "age_label": state["age_label"], "due": None, "overdue": False}
    if not state["enabled"]:
        return note
    oldest = index.oldest_deleted_at(entity_id)
    if oldest is None:
        return note
    now = time.time() if now is None else now
    due_ts = oldest + state["min_age_days"] * 86400
    note["overdue"] = due_ts <= now
    note["due"] = format_timestamp(due_ts, tz)[:6]
    return note


def overdue(index, now: float | None = None) -> bool:
    """Automatik an, und die älteste Markierung ist über das Mindestalter hinaus um mehr als die
    Schonfrist liegen geblieben — der Lauf hat sie nicht entfernt."""
    state = settings(index)
    if not state["enabled"]:
        return False
    oldest = index.oldest_deleted_at()
    if oldest is None:
        return False
    now = time.time() if now is None else now
    return oldest + (state["min_age_days"] + OVERDUE_GRACE_DAYS) * 86400 <= now
