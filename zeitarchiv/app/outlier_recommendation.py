"""Empfehlung für die Schwelle der Ausreißer-Erkennung (Housekeeping, Konfigurationsseite).

Anders als bei den Lücken genügt hier nicht „möglichst wenige Meldungen“: Echte Fehler (verrutschte Ziffern, Aussetzer)
liegen um Größenordnungen über dem Üblichen und werden auf JEDER Stufe gefunden, auch bei 100×. Eine Empfehlung, die nur
die Gesamtquote drückt, würde von ihnen nach oben getrieben und könnte sie bei einer groben Stufe verstecken. Gezählt
werden deshalb nur die **Grenzfälle**: Treffer der Stufe, die bei 100× verschwinden, also normales Verhalten, das fälschlich
markiert wird. Je 30 Tage statt in Prozent, weil 1 % bei einem 10-Sekunden-Sensor rund 2.600 Meldungen am Tag wären und
bei einem Stundenwert sieben im Monat.

Empfohlen wird die kleinste Stufe, ab der ALLE gröberen höchstens ``TARGET_BORDERLINE_PER_30_DAYS`` Grenzfälle je 30 Tage
haben. Die Zählung je Stufe ist wegen der Fortsetzungsregel nicht monoton (eine gröbere Stufe kann mehr markieren), ein
Blick auf die nächste Stufe allein genügt also nicht.

* Treffer, die auch bei 100× bleiben, sind wahrscheinlich echte Fehler und werden getrennt ausgewiesen.
* Bleibt die Quote selbst bei 100× hoch, ist das keine Frage der Stufe: Status „nicht geeignet“, keine Empfehlung. „Aus“
  wird nie empfohlen, es versteckte genau die Fehler, um die es geht.
* Überspringt die Erkennung die Hälfte der Werte oder mehr (Streuung null, z. B. grobe Sensoren), greift sie kaum;
  eine Stufe zu empfehlen wäre eine falsche Entwarnung.
* Entitäten mit ausgeschalteter Erkennung bekommen kein Profil (Kosten, bewusste Wahl) und keine Empfehlung.

Eine Empfehlung ändert nie von selbst etwas; übernommen wird nur auf Klick. Die Messgrundlage (Profil je Stufe) liefert
``cleanup.analyze_raw_rows_page`` in demselben Durchlauf wie die Gesamtzählung.
"""

from __future__ import annotations

from .formatting import OUTLIER_THRESHOLD_LABELS, format_value
from .i18n import tr
from .storage.cleanup import OUTLIER_RULE_VERSION
from .storage.index import effective_outlier_threshold, entity_last_seen

#: Stufen in der Reihenfolge fein → grob; die gröbste dient als Maßstab für echte Fehler.
TIERS = (10, 20, 50, 100)
#: Zeitraum (Tage rückwärts), dessen Treffer je Stufe gezählt werden.
WINDOW_DAYS = 60
#: Ziel: höchstens so viele Grenzfälle je 30 Tage (etwa einer pro Woche).
TARGET_BORDERLINE_PER_30_DAYS = 4
#: Liefert die aktuelle Stufe mehr als dreimal so viele Grenzfälle wie das Ziel, ist sie zu empfindlich.
TOO_SENSITIVE_FACTOR = 3
#: Mindest-Beobachtungsdauer und Mindestzahl beurteilter Werte für eine Empfehlung.
MIN_DAYS = 14
MIN_JUDGED = 500
#: Ab diesem Anteil übersprungener Werte greift die Erkennung kaum; ab dem kleineren gibt es einen Hinweis.
BLIND_SHARE_HIGH = 0.5
BLIND_SHARE_NOTE = 0.2
#: Entitäten ohne Wert seit so vielen Tagen gelten als stumm (die Zahl 0 wäre eine falsche Entwarnung).
SILENT_AFTER_DAYS = 7

STATUS_INSUFFICIENT = "insufficient"
STATUS_TOO_SENSITIVE = "too_sensitive"
STATUS_OK = "ok"
STATUS_UNSUITABLE = "unsuitable"
STATUS_BLIND = "blind"
STATUS_DISABLED = "disabled"
#: Stati, die „Alle übernehmen“ erfasst: hier korrigiert die Empfehlung eine gestörte Einstellung.
ACTIONABLE = (STATUS_TOO_SENSITIVE,)


def recommend(entity, profile: dict | None, now: float) -> dict:
    """Beurteilt die aktuelle Schwelle einer Entität und nennt die empfohlene.

    ``profile``: ``{"rule", "days", "judged", "blind", "tiers": {"10": n, …}}`` (siehe ``analyze_raw_rows_page``) oder
    ``None``. Ergebnis: ``status`` (``STATUS_*``), ``recommended`` (Stufe als Text oder ``None``), ``borderline`` (Grenzfälle
    je 30 Tage bei der aktuellen Stufe), ``tier_borderline`` (dasselbe je Stufe), ``errors`` (Treffer je 30 Tage, die auch bei 100× bleiben), ``blind_share``,
    ``days`` und ``reason`` (``unmeasured``, ``few_days``, ``few_judged``, ``silent``)."""
    result = {
        "status": STATUS_INSUFFICIENT, "recommended": None, "borderline": None, "errors": None,
        "blind_share": None, "days": None, "reason": None, "tier_borderline": None,
    }
    if effective_outlier_threshold(entity["aggregation_type"], entity["outlier_threshold"]) == "off":
        result["status"] = STATUS_DISABLED
        return result
    if profile is None or profile.get("rule") != OUTLIER_RULE_VERSION:
        result["reason"] = "unmeasured"
        return result
    days = float(profile.get("days") or 0.0)
    judged = int(profile.get("judged") or 0)
    blind = int(profile.get("blind") or 0)
    result["days"] = days
    if days < MIN_DAYS:
        result["reason"] = "few_days"
        return result
    if judged + blind < MIN_JUDGED:
        result["reason"] = "few_judged"
        return result
    seen = entity_last_seen(entity)
    if seen is None or now - seen > SILENT_AFTER_DAYS * 86400:
        result["reason"] = "silent"
        return result
    share = blind / (judged + blind)
    result["blind_share"] = share
    if share >= BLIND_SHARE_HIGH:
        result["status"] = STATUS_BLIND
        return result
    counts = {int(k): int(v) for k, v in (profile.get("tiers") or {}).items()}
    if not all(tier in counts for tier in TIERS):
        result["reason"] = "unmeasured"
        return result
    errors = counts[TIERS[-1]]
    borderline = {tier: max(0, counts[tier] - errors) * 30.0 / days for tier in TIERS}
    result["errors"] = errors * 30.0 / days
    result["tier_borderline"] = borderline
    if result["errors"] > TARGET_BORDERLINE_PER_30_DAYS:
        # Selbst die gröbste Stufe meldet oft: kein Thema der Stufe (echte Fehler oder passt nicht zur Entität).
        result["status"] = STATUS_UNSUITABLE
        return result
    result["recommended"] = str(next(
        tier for tier in TIERS if all(borderline[later] <= TARGET_BORDERLINE_PER_30_DAYS for later in TIERS if later >= tier)
    ))
    current = int(float(entity["outlier_threshold"]))
    result["borderline"] = borderline.get(current, 0.0)
    result["status"] = (
        STATUS_TOO_SENSITIVE
        if result["borderline"] > TARGET_BORDERLINE_PER_30_DAYS * TOO_SENSITIVE_FACTOR else STATUS_OK
    )
    return result


def differs(entity, result: dict) -> bool:
    """Ob die Empfehlung von der aktuellen Einstellung abweicht (und damit etwas zu übernehmen wäre)."""
    recommended = result["recommended"]
    return recommended is not None and recommended != entity["outlier_threshold"]


def recommendations(entities, stats: dict[str, dict], now: float) -> dict[str, dict]:
    """Empfehlungen für viele Entitäten; ``stats`` sind die gecachten Gesamt-Zählungen
    (``Index.list_cleanup_alltime_stats``), aus denen das Profil kommt."""
    return {
        entity["entity_id"]: recommend(entity, (stats.get(entity["entity_id"]) or {}).get("outlier_profile"), now)
        for entity in entities
    }


def recommendation_for(index, entity, now: float) -> dict:
    """Empfehlung einer einzelnen Entität (Konfigurationsseite)."""
    entity_id = entity["entity_id"]
    return recommend(entity, (index.get_cleanup_alltime_stats(entity_id) or {}).get("outlier_profile"), now)


#: Reihenfolge der Beurteilungen in Listen: zuerst, was Aufmerksamkeit braucht.
STATUS_RANK = {
    STATUS_TOO_SENSITIVE: 0, STATUS_UNSUITABLE: 1, STATUS_BLIND: 2, STATUS_OK: 3, STATUS_DISABLED: 4, STATUS_INSUFFICIENT: 5,
}


def _label(threshold: str | None) -> str:
    if threshold is None:
        return "–"
    return tr(str(OUTLIER_THRESHOLD_LABELS.get(threshold, threshold)))


def describe(entity, result: dict) -> dict:
    """Beschriftungen zu einer Empfehlung: ``recommended_label``, ``status_label``, ``status_tip`` (Begründung), ``tone``
    (``warn``, ``err``, ``ok``, ``muted``) und ``rank`` (Sortierung)."""
    status = result["status"]
    current = _label(entity["outlier_threshold"])
    recommended = _label(result["recommended"])
    target = TARGET_BORDERLINE_PER_30_DAYS
    borderline = format_value(result["borderline"], 1) if result["borderline"] is not None else ""
    errors = format_value(result["errors"], 1) if result["errors"] is not None else ""
    if status == STATUS_INSUFFICIENT:
        label, tone = tr("Noch keine Empfehlung"), "muted"
        tip = {
            "few_days": tr("Erst {days} Tage beobachtet, für eine Empfehlung sind mindestens {minimum} nötig.",
                           days=format_value(result["days"] or 0, 0), minimum=MIN_DAYS),
            "few_judged": tr("Zu wenige beurteilte Werte, nötig sind mindestens {minimum}.", minimum=MIN_JUDGED),
            "silent": tr("Seit mehr als {days} Tagen kein Wert empfangen.", days=SILENT_AFTER_DAYS),
        }.get(result["reason"], tr("Noch nicht gemessen; die Messung läuft im Hintergrund."))
    elif status == STATUS_TOO_SENSITIVE:
        label, tone = tr("Zu empfindlich"), "warn"
        tip = tr("Bei {current} {borderline} Grenzfälle je 30 Tage, die bei gröberer Einstellung verschwinden; das Ziel sind höchstens {target}.", current=current, borderline=borderline, target=target)
    elif status == STATUS_UNSUITABLE:
        label, tone = tr("Nicht geeignet"), "err"
        tip = tr("Auch bei 100× {errors} Treffer je 30 Tage: entweder echte Fehler (Bereinigung prüfen) oder die Erkennung passt nicht zu dieser Entität.", errors=errors)
    elif status == STATUS_BLIND:
        label, tone = tr("Greift kaum"), "muted"
        tip = tr("Die Erkennung kann nur {share} % der Werte prüfen: Bei den übrigen fehlt die übliche Streuung als Maßstab.", share=format_value(100 - (result["blind_share"] or 0) * 100, 0))
    elif status == STATUS_DISABLED:
        label, tone = tr("Ausgeschaltet"), "muted"
        tip = tr("Die Erkennung ist aus.")
    else:
        label, tone = tr("Passt"), "ok"
        tip = tr("Bei {current} {borderline} Grenzfälle je 30 Tage (Ziel: höchstens {target}).", current=current, borderline=borderline, target=target)
    if status in (STATUS_OK, STATUS_TOO_SENSITIVE) and (result["blind_share"] or 0) >= BLIND_SHARE_NOTE:
        tip += " " + tr("{share} % der Werte kann die Erkennung nicht prüfen.", share=format_value((result["blind_share"] or 0) * 100, 0))
    return {
        "recommended_label": recommended, "status_label": label, "status_tip": tip, "tone": tone,
        "rank": STATUS_RANK[status],
    }


def config_context(index, entity, now: float) -> dict:
    """Beurteilung samt Beschriftungen für die Konfigurationsseite der Entität. ``differs`` ist nur wahr, wenn die aktuelle
    Schwelle zu empfindlich ist: nur dann wird eine Änderung angeboten."""
    result = recommendation_for(index, entity, now)
    return {
        **describe(entity, result), "status": result["status"], "recommended": result["recommended"],
        "differs": differs(entity, result) and result["status"] in ACTIONABLE,
    }
