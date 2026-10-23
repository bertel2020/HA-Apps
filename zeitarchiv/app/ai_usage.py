"""Nutzungsanzeige des KI-Zugriffs (ROADMAP 1.31, V1.1): nur zur Information.

Zählt Aufrufe je Tag, Werkzeug und Kontoart sowie abgelehnte Aufrufe (Zugriff
aus, Werkzeug abgeschaltet). Dazu, ob der Zugriff mit einem Administratorkonto
kam: Die Integration schickt die Art des Kontos im Kopf ``X-Zeitarchiv-Ai-User``
mit (``admin`` oder ``user``). Das ist keine Sperre; nach der empfohlenen
Einrichtung melden sich die meisten mit ihrem eigenen Admin-Konto an, und die
Anzeige soll nur sichtbar machen, was tatsächlich zugreift.

Die Tageswerte stehen als kleines JSON in den Einstellungen und überleben so
einen Neustart; älter als ``RETENTION_DAYS`` wird verworfen. Gezählt wird im
Prozess, geschrieben höchstens einmal je Minute: Ein Absturz kann also bis zu
eine Minute Zählung kosten, dafür belastet eine Anfrageflut den Index nicht.
Inhalte der Fragen werden nie gespeichert.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo

from .storage.index import Index

SETTING_LAST_USED = "ai_last_used"
SETTING_DAILY = "ai_usage_daily"
#: Wie oft die Zähler höchstens in die Datenbank geschrieben werden.
PERSIST_INTERVAL_SECONDS = 60.0
CLIENT_KINDS = ("admin", "user")
REJECT_REASONS = ("ai_disabled", "tool_disabled")
RETENTION_DAYS = 90
WINDOW_DAYS = 7


def _empty_day() -> dict:
    return {"calls": {}, "account": {"admin": 0, "user": 0, "unknown": 0}, "rejected": {}}


def _load_days(index: Index) -> dict[str, dict]:
    try:
        raw = json.loads(index.get_setting(SETTING_DAILY, "{}") or "{}")
    except json.JSONDecodeError:
        return {}
    days: dict[str, dict] = {}
    for key, value in raw.items() if isinstance(raw, dict) else []:
        if not isinstance(value, dict):
            continue
        day = _empty_day()
        calls, account, rejected = value.get("calls"), value.get("account"), value.get("rejected")
        if isinstance(calls, dict):
            day["calls"] = {str(k): int(v) for k, v in calls.items() if isinstance(v, int)}
        if isinstance(account, dict):
            for kind in day["account"]:
                if isinstance(account.get(kind), int):
                    day["account"][kind] = account[kind]
        if isinstance(rejected, dict):
            day["rejected"] = {str(k): int(v) for k, v in rejected.items() if isinstance(v, int)}
        days[str(key)] = day
    return days


@dataclass
class AiUsage:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float | None = None
    _days: dict[str, dict] = field(default_factory=dict)
    _index: Index | None = None
    _persisted_at: float = 0.0
    _dirty: bool = False

    def _attach(self, index: Index) -> None:
        """Lädt die gespeicherten Tageswerte, sobald ein anderer Index auftaucht (nur mit Sperre)."""
        if self._index is not index:
            self._index = index
            self._days = _load_days(index)
            self._persisted_at = 0.0
            self._dirty = False

    def _day(self, now: datetime) -> dict:
        key = now.date().isoformat()
        if key not in self._days:
            self._days[key] = _empty_day()
            cutoff = (now.date() - timedelta(days=RETENTION_DAYS)).isoformat()
            for old in [k for k in self._days if k < cutoff]:
                del self._days[old]
        return self._days[key]

    def _flush_due(self, now_ts: float) -> str | None:
        """Der zu schreibende JSON-Text, wenn die Schreibpause um ist (nur mit Sperre)."""
        self._dirty = True
        if now_ts - self._persisted_at < PERSIST_INTERVAL_SECONDS:
            return None
        self._persisted_at = now_ts
        self._dirty = False
        return json.dumps(self._days, separators=(",", ":"))

    def record(self, index: Index, tool: str, account: str | None, tz: tzinfo | None = None) -> None:
        kind = account if account in CLIENT_KINDS else "unknown"
        now_ts = time.time()
        now = datetime.fromtimestamp(now_ts, tz or timezone.utc)
        with self.lock:
            self._attach(index)
            day = self._day(now)
            day["calls"][tool] = day["calls"].get(tool, 0) + 1
            day["account"][kind] += 1
            self.last_used = now_ts
            payload = self._flush_due(now_ts)
        if payload is not None:
            index.set_setting(SETTING_DAILY, payload)
            index.set_setting(SETTING_LAST_USED, str(now_ts))

    def record_rejected(self, index: Index, reason: str, tz: tzinfo | None = None) -> None:
        now_ts = time.time()
        now = datetime.fromtimestamp(now_ts, tz or timezone.utc)
        with self.lock:
            self._attach(index)
            day = self._day(now)
            day["rejected"][reason] = day["rejected"].get(reason, 0) + 1
            payload = self._flush_due(now_ts)
        if payload is not None:
            index.set_setting(SETTING_DAILY, payload)

    def flush(self) -> None:
        """Schreibt ausstehende Zähler sofort (beim Beenden der App und in Tests)."""
        with self.lock:
            index = self._index
            if index is None or not self._dirty:
                return
            payload = json.dumps(self._days, separators=(",", ":"))
            last = self.last_used
            self._dirty = False
            self._persisted_at = time.time()
        index.set_setting(SETTING_DAILY, payload)
        # Sonst zeigt "zuletzt genutzt" nach dem Neustart den Stand der letzten Schreibpause.
        if last is not None:
            index.set_setting(SETTING_LAST_USED, str(last))

    def snapshot(self, index: Index, tz: tzinfo | None = None) -> dict:
        today = datetime.now(tz or timezone.utc).date()
        window_start = (today - timedelta(days=WINDOW_DAYS - 1)).isoformat()
        with self.lock:
            self._attach(index)
            days, last = {k: v for k, v in self._days.items()}, self.last_used
        calls: dict[str, int] = {}
        by_account = {"admin": 0, "user": 0, "unknown": 0}
        rejected: dict[str, int] = {}
        total = 0
        for key, day in days.items():
            total += sum(day["calls"].values())
            if key < window_start:
                continue
            for tool, count in day["calls"].items():
                calls[tool] = calls.get(tool, 0) + count
            for kind, count in day["account"].items():
                by_account[kind] += count
            for reason, count in day["rejected"].items():
                rejected[reason] = rejected.get(reason, 0) + count
        if last is None:
            try:
                last = float(index.get_setting(SETTING_LAST_USED, "") or 0) or None
            except ValueError:
                last = None
        return {
            "calls": calls, "total": total, "by_account": by_account, "last_used": last,
            "today": sum(days.get(today.isoformat(), _empty_day())["calls"].values()),
            "week": sum(calls.values()), "rejected": rejected, "rejected_week": sum(rejected.values()),
        }

    def reset(self) -> None:
        with self.lock:
            self._days = {}
            self._index = None
            self.last_used = None
            self._persisted_at = 0.0
            self._dirty = False


#: Eine Instanz je Prozess; ai_routes schreibt, ai_settings_routes liest.
USAGE = AiUsage()
