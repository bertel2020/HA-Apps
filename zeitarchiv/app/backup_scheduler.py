"""Deterministische Kalenderplanung für portable Zeitarchiv-Backups."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_schedule_time(value: str) -> time:
    """Liest eine lokale Uhrzeit im stabilen ``HH:MM``-Format."""
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültige Uhrzeit") from exc
    return parsed.replace(second=0, microsecond=0)


def _valid_local_datetime(day, local_time: time, tz: ZoneInfo) -> datetime:
    """Löst nicht existente DST-Zeiten auf die nächste gültige Minute auf.

    Bei einer doppelt vorkommenden Uhrzeit wird ``fold=0`` verwendet. Da der
    nächste Lauf anschließend als UTC-Zeitstempel persistiert wird, läuft der
    Job in der zweiten Falte nicht ein zweites Mal.
    """
    candidate = datetime.combine(day, local_time, tzinfo=tz)
    for _ in range(181):
        roundtrip = candidate.astimezone(timezone.utc).astimezone(tz)
        if roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("Lokaler Sicherungszeitpunkt ist ungültig")


def next_scheduled_run(
    now: datetime,
    schedule: str,
    time_value: str,
    weekday: int = 6,
) -> datetime | None:
    """Gibt den nächsten Kalendertermin strikt nach ``now`` zurück."""
    if schedule == "off":
        return None
    if schedule not in {"daily", "weekly"}:
        raise ValueError("Ungültiger Zeitplan")
    if now.tzinfo is None:
        raise ValueError("now benötigt eine Zeitzone")
    if weekday not in range(7):
        raise ValueError("Ungültiger Wochentag")

    local_now = now.astimezone(now.tzinfo)
    local_time = parse_schedule_time(time_value)
    if schedule == "daily":
        day = local_now.date()
        candidate = _valid_local_datetime(day, local_time, local_now.tzinfo)
        if candidate <= local_now:
            candidate = _valid_local_datetime(day + timedelta(days=1), local_time, local_now.tzinfo)
        return candidate

    days_ahead = (weekday - local_now.weekday()) % 7
    day = local_now.date() + timedelta(days=days_ahead)
    candidate = _valid_local_datetime(day, local_time, local_now.tzinfo)
    if candidate <= local_now:
        candidate = _valid_local_datetime(day + timedelta(days=7), local_time, local_now.tzinfo)
    return candidate

