"""Nutzungsanzeige des KI-Zugriffs (ROADMAP 1.31, V1.1): nur zur Information.

Zählt Aufrufe je Werkzeug seit dem Start und merkt sich, wann zuletzt jemand
zugegriffen hat. Dazu, ob der Zugriff mit einem Administratorkonto kam: Die
Integration schickt die Art des Kontos im Kopf ``X-Zeitarchiv-Ai-User`` mit
(``admin`` oder ``user``). Das ist keine Sperre; nach der empfohlenen Einrichtung
melden sich die meisten mit ihrem eigenen Admin-Konto an, und die Anzeige soll
nur sichtbar machen, was tatsächlich zugreift.

Der Zustand liegt im Prozess, wie die Verbindungsstatistik der App (ApiState).
Nur "zuletzt genutzt" steht zusätzlich in den Einstellungen, damit es einen
Neustart überlebt; geschrieben wird höchstens einmal je Minute.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .storage.index import Index

SETTING_LAST_USED = "ai_last_used"
#: Wie oft "zuletzt genutzt" höchstens in die Datenbank geschrieben wird.
PERSIST_INTERVAL_SECONDS = 60.0
CLIENT_KINDS = ("admin", "user")


@dataclass
class AiUsage:
    lock: threading.Lock = field(default_factory=threading.Lock)
    calls: dict[str, int] = field(default_factory=dict)
    by_account: dict[str, int] = field(default_factory=lambda: {"admin": 0, "user": 0, "unknown": 0})
    last_used: float | None = None
    _persisted_at: float = 0.0

    def record(self, index: Index, tool: str, account: str | None) -> None:
        kind = account if account in CLIENT_KINDS else "unknown"
        now = time.time()
        with self.lock:
            self.calls[tool] = self.calls.get(tool, 0) + 1
            self.by_account[kind] += 1
            self.last_used = now
            due = now - self._persisted_at >= PERSIST_INTERVAL_SECONDS
            if due:
                self._persisted_at = now
        if due:
            index.set_setting(SETTING_LAST_USED, str(now))

    def snapshot(self, index: Index) -> dict:
        with self.lock:
            calls, by_account, last = dict(self.calls), dict(self.by_account), self.last_used
        if last is None:
            try:
                last = float(index.get_setting(SETTING_LAST_USED, "") or 0) or None
            except ValueError:
                last = None
        return {"calls": calls, "total": sum(calls.values()), "by_account": by_account, "last_used": last}

    def reset(self) -> None:
        with self.lock:
            self.calls.clear()
            self.by_account.update(admin=0, user=0, unknown=0)
            self.last_used = None
            self._persisted_at = 0.0


#: Eine Instanz je Prozess; ai_routes schreibt, ai_settings_routes liest.
USAGE = AiUsage()
