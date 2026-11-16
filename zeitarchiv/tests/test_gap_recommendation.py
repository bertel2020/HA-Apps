"""Empfehlungslogik der Lücken-Erkennung: kleinste Stufe, die bei der Entität nur selten anschlägt (höchstens
4 Pausen je 30 Tage), mit Mindest-Beobachtung, Beurteilung der aktuellen Einstellung und „Aus“ für Entitäten, bei denen
selbst 24 Stunden zu oft anschlagen."""

from __future__ import annotations

import pytest

from app import gap_recommendation as rec

NOW = 1_800_000_000.0


def _entity(**kw):
    base = {"entity_id": "sensor.x", "gap_threshold": "15", "value_filter": "off", "resolution": "raw",
            "last_seen_ts": NOW - 3600, "last_ts": NOW - 3600}
    return {**base, **kw}


def _profile(per30: dict[int, float] | None = None, *, method="stored", days=30.0, events=500) -> dict:
    """Profil mit den gewünschten Pausen je 30 Tage (bei ``days`` Tagen Beobachtung hochgerechnet zurückgerechnet)."""
    given = per30 or {}
    # Pausen-Zähler sind kumulativ: bei einer kleineren Stufe sind es mindestens so viele wie bei jeder größeren.
    tiers = {t: round(max([v for g, v in given.items() if g >= t] or [0]) * days / 30) for t in rec.STORED_TIERS}
    return {"method": method, "days": days, "events": events, "tiers": tiers}


def test_the_smallest_quiet_tier_is_recommended() -> None:
    profile = _profile({5: 300, 15: 40, 30: 12, 60: 3, 180: 1})
    result = rec.recommend(_entity(gap_threshold="60"), profile, NOW)
    assert result["recommended"] == "60"
    assert result["status"] == rec.STATUS_OK
    assert result["per30"] == pytest.approx(3)


def test_a_quiet_entity_gets_the_smallest_tier() -> None:
    result = rec.recommend(_entity(gap_threshold="5"), _profile({5: 2}), NOW)
    assert result["recommended"] == "1"  # gespeicherte Werte: auch „1 Minute“ ist eine Stufe
    assert result["status"] == rec.STATUS_OK  # 5 ist nur eine Stufe über der empfohlenen
    receive = rec.recommend(_entity(gap_threshold="15", value_filter="decimals"), _profile({5: 2}, method="receive"), NOW)
    assert receive["recommended"] == "5" and receive["status"] == rec.STATUS_OK


def test_a_threshold_that_fires_too_often_is_too_sensitive() -> None:
    profile = _profile({5: 600, 15: 90, 30: 30, 60: 14, 180: 6, 360: 4})
    result = rec.recommend(_entity(gap_threshold="30"), profile, NOW)
    assert result["status"] == rec.STATUS_TOO_SENSITIVE
    assert result["recommended"] == "360"
    assert rec.differs(_entity(gap_threshold="30"), result)


def test_between_target_and_three_times_the_target_still_fits() -> None:
    result = rec.recommend(_entity(gap_threshold="60"), _profile({60: 10, 180: 3}), NOW)
    assert result["status"] == rec.STATUS_OK and result["recommended"] == "180"


def test_an_entity_that_never_stays_quiet_is_unsuitable_and_gets_off() -> None:
    profile = _profile({5: 900, 15: 400, 60: 200, 360: 60, 720: 30, 1440: 12})
    result = rec.recommend(_entity(gap_threshold="60"), profile, NOW)
    assert result["recommended"] == "off"
    assert result["status"] == rec.STATUS_UNSUITABLE
    assert rec.differs(_entity(gap_threshold="60"), result)


def test_a_coarse_threshold_could_be_finer() -> None:
    result = rec.recommend(_entity(gap_threshold="720"), _profile({5: 1}), NOW)
    assert result["status"] == rec.STATUS_FINER and result["recommended"] == "1"


def test_a_switched_off_detection_gets_a_recommendation_but_no_judgement() -> None:
    result = rec.recommend(_entity(gap_threshold="off"), _profile({5: 2}), NOW)
    assert result["status"] == rec.STATUS_DISABLED and result["recommended"] == "1"
    assert rec.differs(_entity(gap_threshold="off"), result)
    unsuitable = rec.recommend(_entity(gap_threshold="off"), _profile({1440: 50}), NOW)
    assert unsuitable["recommended"] == "off" and not rec.differs(_entity(gap_threshold="off"), unsuitable)


def test_too_little_observation_gives_no_recommendation() -> None:
    few_days = rec.recommend(_entity(), _profile({5: 1}, days=10.0), NOW)
    assert few_days["status"] == rec.STATUS_INSUFFICIENT and few_days["reason"] == "few_days"
    few_events = rec.recommend(_entity(), _profile({5: 1}, events=10), NOW)
    assert few_events["reason"] == "few_events"
    assert rec.recommend(_entity(), None, NOW)["reason"] == "unmeasured"
    for result in (few_days, few_events):
        assert result["recommended"] is None and not rec.differs(_entity(), result)


def test_a_profile_of_the_other_method_does_not_count() -> None:
    receive = _entity(value_filter="decimals")
    assert rec.recommend(receive, _profile({5: 1}, method="stored"), NOW)["reason"] == "unmeasured"
    assert rec.recommend(_entity(), _profile({5: 1}, method="receive"), NOW)["reason"] == "unmeasured"


def test_a_silent_receive_entity_gets_no_zero_as_all_clear() -> None:
    """Ohne Wert entsteht keine Pause: Null Pausen bei einer seit Tagen stummen Entität wäre eine falsche Entwarnung."""
    silent = _entity(value_filter="decimals", last_seen_ts=NOW - 10 * 86400, last_ts=NOW - 10 * 86400)
    result = rec.recommend(silent, _profile({}, method="receive", events=0), NOW)
    assert result["reason"] == "silent" and result["recommended"] is None
    alive = _entity(value_filter="decimals")
    assert rec.recommend(alive, _profile({}, method="receive", events=0), NOW)["recommended"] == "5"


def test_receive_needs_no_event_count() -> None:
    assert rec.recommend(_entity(value_filter="decimals"), {"method": "receive", "days": 20, "tiers": {}}, NOW)["recommended"] == "5"


def test_count_tiers_counts_each_exceeded_tier() -> None:
    tiers = rec.empty_tiers()
    rec.count_tiers(100, tiers)   # unter 5 Minuten: nichts
    rec.count_tiers(20 * 60, tiers)
    rec.count_tiers(7 * 3600, tiers)
    assert tiers[5] == 2 and tiers[15] == 2 and tiers[60] == 1 and tiers[360] == 1 and tiers[720] == 0


def test_the_stored_method_compares_the_exact_step() -> None:
    entity = _entity(gap_threshold="1")  # stored: „1 Minute“ ist eine echte Stufe
    assert rec.differs(entity, {"recommended": "5"})
    receive = _entity(gap_threshold="1", value_filter="decimals")  # receive: „1“ gilt wie „5“
    assert not rec.differs(receive, {"recommended": "5"})


def test_the_constants_match_the_documented_target() -> None:
    assert rec.TARGET_PER_30_DAYS == 4 and rec.MIN_DAYS == 14 and rec.WINDOW_DAYS == 60 and rec.MIN_EVENTS == 30


def test_stored_one_minute_is_a_real_step_with_its_own_count() -> None:
    """Bei gespeicherten Werten wirkt „1 Minute“ wirklich als 1 Minute: Ein Sensor im 2-Minuten-Takt schlägt mit
    Schwelle „1“ ständig an, auch wenn es bei 5 Minuten ruhig wäre (früher „Passt“, weil „1“ wie „5“ gezählt wurde)."""
    entity = _entity(gap_threshold="1")
    profile = _profile({1: 20000}, days=30.0)  # 20.000 Pausen über 1 Minute, keine über 5
    result = rec.recommend(entity, profile, NOW)
    assert result["status"] == rec.STATUS_TOO_SENSITIVE
    assert result["recommended"] == "5"
    assert rec.differs(entity, result)
    tiers = rec.empty_tiers("stored")
    rec.count_tiers(120, tiers)
    rec.count_tiers(400, tiers)
    assert tiers[1] == 2 and tiers[5] == 1
    receive = rec.empty_tiers("receive")
    rec.count_tiers(120, receive)  # Empfangs-Methode: kleinere Pausen werden nicht festgehalten
    assert 1 not in receive and receive[5] == 0


def test_an_old_profile_without_the_one_minute_counter_counts_as_unmeasured() -> None:
    """Ein Pausen-Profil aus der Zeit vor der Stufe „1 Minute“ hat keinen Zähler dafür; ohne Prüfung wäre „1“ mit Null
    Pausen die ruhigste Stufe und würde empfohlen, obwohl nichts gemessen ist."""
    old = {"method": "stored", "days": 40.0, "events": 500, "tiers": {"5": 2888, "15": 2869, "30": 0, "60": 0}}
    result = rec.recommend(_entity(gap_threshold="15"), old, NOW)
    assert result["status"] == rec.STATUS_INSUFFICIENT and result["reason"] == "unmeasured" and result["recommended"] is None
