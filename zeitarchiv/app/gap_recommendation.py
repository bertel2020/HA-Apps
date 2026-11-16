"""Empfehlung für die Schwelle der Lücken-Erkennung (Housekeeping → Lücken-Erkennung, Konfigurationsseite).

Eine Meldung soll etwas Seltenes sein, sonst wird sie ignoriert. Empfohlen wird deshalb die kleinste Stufe, bei der
die Entität in den letzten ``WINDOW_DAYS`` Tagen nur selten (hochgerechnet höchstens ``TARGET_PER_30_DAYS`` je 30 Tage,
etwa einmal pro Woche) eine Pause länger als die Stufe hatte. Die Messlatte liefert die Entität selbst, es gibt keine
pauschale Zahl. Bleibt selbst die größte Stufe darüber, taugt die Erkennung für diese Entität nicht (typisch: Sensoren,
die nur bei Änderung senden), empfohlen wird „Aus“.

Grundlage sind Pausen-Zähler je Stufe (``tiers``: Stufe in Minuten → Zahl der Pausen, die länger waren):
* Entitäten mit Filter oder Auflösung: die Empfangs-Pausen aus ``receive_gaps`` (Methode „receive“). Gespeicherte Werte
  taugen dort nicht: das Lebenszeichen des Filters erzeugt Abstände, die mit der Lieferung nichts zu tun haben (an
  67 echten Entitäten gemessen: rund 20 bis 30 solche Abstände über 6 Stunden je 30 Tage).
* Alle übrigen: die gespeicherten Abstände aus dem Durchlauf der Gesamtzählung (Methode „stored“).

Eine Empfehlung ändert nie von selbst etwas; übernommen wird nur auf Klick.
"""

from __future__ import annotations

from .formatting import GAP_THRESHOLD_LABELS, format_value
from .i18n import tr
from .storage.index import MIN_RECEIVE_GAP_SECONDS, entity_last_seen, gap_method

#: Stufen der Lücken-Erkennung in Minuten, aufsteigend (ohne „Aus“). Die Stufe „1 Minute“ fehlt: Pausen unter
#: 5 Minuten werden nicht festgehalten, die Stufe gilt dort wie „5 Minuten“.
TIERS = (5, 15, 30, 60, 180, 360, 720, 1440)
#: Zeitraum, über den die Pausen gezählt werden.
WINDOW_DAYS = 60
#: Ziel: höchstens so viele Meldungen je 30 Tage (etwa eine pro Woche).
TARGET_PER_30_DAYS = 4
#: Schlägt die aktuelle Schwelle mehr als dreimal so oft an wie das Ziel, ist sie zu empfindlich.
TOO_SENSITIVE_FACTOR = 3
#: Mindest-Beobachtungsdauer und, bei gespeicherten Werten, Mindestzahl an Abständen für eine Empfehlung.
MIN_DAYS = 14
MIN_EVENTS = 30
#: Liegt die aktuelle Schwelle so viele Stufen über der empfohlenen, wäre eine feinere möglich.
FINER_BY_TIERS = 2
#: Entitäten ohne Wert seit so vielen Tagen gelten als stumm: ohne Wert entsteht keine Pause, die Zahl 0 wäre
#: sonst eine falsche Entwarnung.
SILENT_AFTER_DAYS = 7

STATUS_INSUFFICIENT = "insufficient"
STATUS_TOO_SENSITIVE = "too_sensitive"
STATUS_OK = "ok"
STATUS_FINER = "finer"
STATUS_UNSUITABLE = "unsuitable"
STATUS_DISABLED = "disabled"
#: Stati, die „Alle übernehmen“ erfasst: hier ändert die Empfehlung etwas, das gestört ist.
ACTIONABLE = (STATUS_TOO_SENSITIVE, STATUS_UNSUITABLE)


def tier_index(threshold: str) -> int:
    """Position der Stufe in ``TIERS``; „1“ gilt wie „5“, größere Werte als die letzte Stufe wie diese."""
    minutes = float(threshold)
    for position, tier in enumerate(TIERS):
        if minutes <= tier:
            return position
    return len(TIERS) - 1


def empty_tiers() -> dict[int, int]:
    return dict.fromkeys(TIERS, 0)


def count_tiers(delta_seconds: float, tiers: dict[int, int]) -> None:
    """Zählt eine Pause bei jeder Stufe mit, die sie überschreitet."""
    if delta_seconds <= MIN_RECEIVE_GAP_SECONDS:
        return
    for tier in TIERS:
        if delta_seconds > tier * 60:
            tiers[tier] += 1
        else:
            break


def recommend(entity, profile: dict | None, now: float) -> dict:
    """Beurteilt die aktuelle Schwelle einer Entität und nennt die empfohlene.

    ``profile``: ``{"method": "receive" | "stored", "days": Beobachtungsdauer, "events": Zahl der Abstände (nur
    „stored“), "tiers": {Stufe: Zahl}}`` oder ``None``, wenn nichts gemessen ist. Ergebnis: ``status`` (siehe
    ``STATUS_*``), ``recommended`` (Stufe als Text, ``"off"`` oder ``None``), ``per30`` (Pausen je 30 Tage bei der
    aktuellen Schwelle, ``None`` ohne Messung), ``days`` und ``reason`` (Schlüssel für den Grund einer fehlenden
    Empfehlung: ``unmeasured``, ``not_started``, ``few_days``, ``few_events``, ``silent``)."""
    result = {"status": STATUS_INSUFFICIENT, "recommended": None, "per30": None, "days": None, "reason": None}
    method = gap_method(entity)
    if profile is None or profile.get("method") != method:
        result["reason"] = "unmeasured"
        return result
    days = float(profile.get("days") or 0.0)
    result["days"] = days
    if days < MIN_DAYS:
        result["reason"] = "few_days"
        return result
    if method == "stored" and int(profile.get("events") or 0) < MIN_EVENTS:
        result["reason"] = "few_events"
        return result
    seen = entity_last_seen(entity)
    if seen is None or now - seen > SILENT_AFTER_DAYS * 86400:
        result["reason"] = "silent"
        return result
    tiers = {int(k): int(v) for k, v in (profile.get("tiers") or {}).items()}
    per30 = {tier: tiers.get(tier, 0) * 30.0 / days for tier in TIERS}
    recommended = next((tier for tier in TIERS if per30[tier] <= TARGET_PER_30_DAYS), None)
    result["recommended"] = str(recommended) if recommended is not None else "off"
    threshold = entity["gap_threshold"]
    if threshold == "off":
        result["status"] = STATUS_DISABLED
        return result
    position = tier_index(threshold)
    current = per30[TIERS[position]]
    result["per30"] = current
    if current > TARGET_PER_30_DAYS * TOO_SENSITIVE_FACTOR:
        result["status"] = STATUS_UNSUITABLE if recommended is None else STATUS_TOO_SENSITIVE
    elif recommended is not None and position - TIERS.index(recommended) >= FINER_BY_TIERS:
        result["status"] = STATUS_FINER
    else:
        result["status"] = STATUS_OK
    return result


def differs(entity, result: dict) -> bool:
    """Ob die Empfehlung von der aktuellen Einstellung abweicht (und damit etwas zu übernehmen wäre)."""
    recommended = result["recommended"]
    if recommended is None:
        return False
    current = entity["gap_threshold"]
    if recommended == "off" or current == "off" or gap_method(entity) == "stored":
        return recommended != current
    # Empfangs-Methode: „1“ gilt wie „5 Minuten“, die Empfehlung beginnt ohnehin bei 5.
    return str(TIERS[tier_index(current)]) != recommended


def receive_profile(entity, counts: dict[int, int] | None, now: float) -> dict | None:
    """Profil einer Entität mit Empfangs-Methode aus den Pausen-Zählern der Tabelle ``receive_gaps``; ``None``, solange
    die Aufzeichnung nicht begonnen hat."""
    since = entity["receive_gaps_since"]
    if since is None:
        return None
    cutoff = now - WINDOW_DAYS * 86400
    return {"method": "receive", "days": (now - max(cutoff, since)) / 86400, "tiers": counts or {}}


def recommendations(index, entities, stats: dict[str, dict], now: float, only: str | None = None) -> dict[str, dict]:
    """Empfehlungen für viele Entitäten auf einmal (eine Abfrage für alle Empfangs-Zähler); ``stats`` sind die gecachten
    Gesamt-Zählungen (``Index.list_cleanup_alltime_stats``), aus denen das Profil der gespeicherten Werte kommt."""
    counts = index.receive_gap_tier_counts(TIERS, now - WINDOW_DAYS * 86400, only)
    out = {}
    for entity in entities:
        entity_id = entity["entity_id"]
        if gap_method(entity) == "receive":
            profile = receive_profile(entity, counts.get(entity_id), now)
            result = recommend(entity, profile, now)
            if profile is None:
                result["reason"] = "not_started"
        else:
            result = recommend(entity, (stats.get(entity_id) or {}).get("gap_profile"), now)
        out[entity_id] = result
    return out


def recommendation_for(index, entity, now: float) -> dict:
    """Empfehlung einer einzelnen Entität (Konfigurationsseite)."""
    entity_id = entity["entity_id"]
    return recommendations(index, [entity], {entity_id: index.get_cleanup_alltime_stats(entity_id) or {}}, now, only=entity_id)[entity_id]


#: Reihenfolge der Beurteilungen in Listen: zuerst, was Aufmerksamkeit braucht.
STATUS_RANK = {
    STATUS_UNSUITABLE: 0, STATUS_TOO_SENSITIVE: 1, STATUS_FINER: 2, STATUS_DISABLED: 3, STATUS_OK: 4, STATUS_INSUFFICIENT: 5,
}


def _label(threshold: str | None) -> str:
    if threshold is None:
        return "–"
    if threshold == "off":
        return tr("Aus")
    return tr(str(GAP_THRESHOLD_LABELS.get(threshold, threshold)))


def describe(entity, result: dict) -> dict:
    """Beschriftungen zu einer Empfehlung: ``recommended_label``, ``status_label``, ``status_tip`` (Begründung), ``tone``
    (``warn``, ``ok``, ``info``, ``muted`` für die Darstellung) und ``rank`` (Sortierung)."""
    status = result["status"]
    per30 = format_value(result["per30"], 1) if result["per30"] is not None else ""
    current = _label(entity["gap_threshold"])
    recommended = _label(result["recommended"])
    target = TARGET_PER_30_DAYS
    if status == STATUS_INSUFFICIENT:
        label, tone = tr("Noch keine Empfehlung"), "muted"
        tip = {
            "not_started": tr("Die Aufzeichnung der Pausen zwischen empfangenen Werten hat noch nicht begonnen."),
            "few_days": tr("Erst {days} Tage beobachtet, für eine Empfehlung sind mindestens {minimum} nötig.",
                           days=format_value(result["days"] or 0, 0), minimum=MIN_DAYS),
            "few_events": tr("Zu wenige Messwerte, nötig sind mindestens {minimum} Abstände.", minimum=MIN_EVENTS),
            "silent": tr("Seit mehr als {days} Tagen kein Wert empfangen.", days=SILENT_AFTER_DAYS),
        }.get(result["reason"], tr("Noch nicht gemessen; die Messung läuft im Hintergrund."))
    elif status == STATUS_TOO_SENSITIVE:
        label, tone = tr("Zu empfindlich"), "warn"
        tip = tr("Bei {current} {per30} Meldungen je 30 Tage, das Ziel sind höchstens {target}.", current=current, per30=per30, target=target)
    elif status == STATUS_UNSUITABLE:
        label, tone = tr("Nicht geeignet"), "warn"
        tip = tr("Schon bei {current} {per30} Meldungen je 30 Tage, selbst bei 24 Stunden mehr als {target}: Die Entität sendet wohl nur bei Änderung.", current=current, per30=per30, target=target)
    elif status == STATUS_FINER:
        label, tone = tr("Könnte feiner sein"), "info"
        tip = tr("Bei {current} nur {per30} Meldungen je 30 Tage; schon {recommended} läge im Ziel.", current=current, per30=per30, recommended=recommended)
    elif status == STATUS_DISABLED:
        label, tone = tr("Ausgeschaltet"), "muted"
        tip = tr("Die Erkennung ist aus; empfohlen wäre {recommended}.", recommended=recommended)
    else:
        label, tone = tr("Passt"), "ok"
        tip = tr("Bei {current} {per30} Meldungen je 30 Tage (Ziel: höchstens {target}).", current=current, per30=per30, target=target)
    return {
        "recommended_label": recommended, "status_label": label, "status_tip": tip, "tone": tone,
        "rank": STATUS_RANK[status],
    }
