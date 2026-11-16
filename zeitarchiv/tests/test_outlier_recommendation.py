"""Empfehlung der Ausreißer-Schwelle: Grenzfälle (Treffer, die bei 100× verschwinden) je 30 Tage, kleinste Stufe, ab der
alle gröberen im Ziel liegen; echte Fehler, blinde Werte und ausgeschaltete Erkennung werden getrennt behandelt."""

from __future__ import annotations

import pytest

from app import outlier_recommendation as rec
from app.storage.cleanup import OUTLIER_RULE_VERSION

NOW = 1_800_000_000.0


def _entity(**kw):
    base = {"entity_id": "sensor.x", "aggregation_type": "average", "outlier_threshold": "50",
            "last_seen_ts": NOW - 3600, "last_ts": NOW - 3600}
    return {**base, **kw}


def _profile(per30: dict[int, float] | None = None, *, days=30.0, judged=5000, blind=0, rule=OUTLIER_RULE_VERSION) -> dict:
    """Profil mit den gewünschten Treffern je 30 Tage (Stufe → Treffer); fehlende Stufen sind 0."""
    given = per30 or {}
    tiers = {str(t): round(given.get(t, 0) * days / 30) for t in (10, 20, 50, 100)}
    return {"rule": rule, "days": days, "judged": judged, "blind": blind, "tiers": tiers}


def test_smallest_tier_with_all_coarser_ones_quiet_is_recommended() -> None:
    # Grenzfälle je 30 Tage: 10× → 40, 20× → 10, 50× → 3, 100× → 0 (nach Abzug der echten Fehler)
    profile = _profile({10: 42, 20: 12, 50: 5, 100: 2})
    result = rec.recommend(_entity(outlier_threshold="50"), profile, NOW)
    assert result["recommended"] == "50"
    assert result["status"] == rec.STATUS_OK
    assert result["borderline"] == pytest.approx(3)
    assert result["errors"] == pytest.approx(2)


def test_real_errors_do_not_raise_the_recommendation() -> None:
    quiet = rec.recommend(_entity(), _profile({10: 3, 20: 3, 50: 3, 100: 3}), NOW)
    assert quiet["recommended"] == "10"  # drei echte Fehler je 30 Tage sind auf jeder Stufe gleich und kein Grenzfall


def test_non_monotonic_counts_need_all_coarser_tiers_quiet() -> None:
    # 20× meldet mehr als 10× (Fortsetzungsregel): 10× darf deshalb nicht „ruhig“ heißen, solange 20× laut ist
    profile = {"rule": OUTLIER_RULE_VERSION, "days": 30.0, "judged": 5000, "blind": 0,
               "tiers": {"10": 2, "20": 20, "50": 1, "100": 0}}
    assert rec.recommend(_entity(), profile, NOW)["recommended"] == "50"


def test_too_sensitive_current_tier() -> None:
    result = rec.recommend(_entity(outlier_threshold="20"), _profile({10: 400, 20: 300, 50: 3, 100: 0}), NOW)
    assert result["status"] == rec.STATUS_TOO_SENSITIVE
    assert result["recommended"] == "50"
    assert rec.differs(_entity(outlier_threshold="20"), result)


def test_current_tier_equal_to_recommendation_is_not_a_change() -> None:
    entity = _entity(outlier_threshold="50")
    result = rec.recommend(entity, _profile({10: 100, 20: 40, 50: 3, 100: 0}), NOW)
    assert result["recommended"] == "50" and not rec.differs(entity, result)


def test_noisy_even_at_the_coarsest_tier_is_unsuitable_without_recommendation() -> None:
    entity = _entity(outlier_threshold="50")
    result = rec.recommend(entity, _profile({10: 500, 20: 400, 50: 300, 100: 200}), NOW)
    assert result["status"] == rec.STATUS_UNSUITABLE
    assert result["recommended"] is None and not rec.differs(entity, result)


def test_off_is_never_recommended() -> None:
    for per30 in ({10: 900, 20: 900, 50: 900, 100: 900}, {10: 0}):
        assert rec.recommend(_entity(), _profile(per30), NOW)["recommended"] != "off"


def test_mostly_blind_values_give_no_recommendation() -> None:
    result = rec.recommend(_entity(), _profile({10: 0}, judged=2000, blind=3000), NOW)
    assert result["status"] == rec.STATUS_BLIND
    assert result["recommended"] is None


def test_some_blind_values_add_a_note_but_still_judge() -> None:
    entity = _entity()
    result = rec.recommend(entity, _profile({10: 0}, judged=3500, blind=1500), NOW)
    assert result["status"] == rec.STATUS_OK
    assert "30" in rec.describe(entity, result)["status_tip"]


def test_switched_off_entity_has_no_recommendation() -> None:
    result = rec.recommend(_entity(outlier_threshold="off"), None, NOW)
    assert result["status"] == rec.STATUS_DISABLED and result["recommended"] is None


def test_structurally_blind_types_count_as_switched_off() -> None:
    assert rec.recommend(_entity(aggregation_type="switch"), None, NOW)["status"] == rec.STATUS_DISABLED


@pytest.mark.parametrize("kwargs, reason", [
    ({"days": 5.0}, "few_days"),
    ({"judged": 100, "blind": 50}, "few_judged"),
    ({"rule": OUTLIER_RULE_VERSION - 1}, "unmeasured"),
])
def test_not_enough_data_gives_a_reason(kwargs, reason) -> None:
    result = rec.recommend(_entity(), _profile({10: 0}, **kwargs), NOW)
    assert result["status"] == rec.STATUS_INSUFFICIENT and result["reason"] == reason


def test_missing_profile_and_silent_entity() -> None:
    assert rec.recommend(_entity(), None, NOW)["reason"] == "unmeasured"
    silent = _entity(last_seen_ts=NOW - 30 * 86400, last_ts=NOW - 30 * 86400)
    assert rec.recommend(silent, _profile({10: 0}), NOW)["reason"] == "silent"


def test_profile_missing_a_tier_counts_as_unmeasured() -> None:
    profile = _profile({10: 0})
    del profile["tiers"]["20"]
    assert rec.recommend(_entity(), profile, NOW)["reason"] == "unmeasured"


def test_describe_returns_labels_for_every_status() -> None:
    entity = _entity()
    cases = [
        rec.recommend(entity, None, NOW),
        rec.recommend(_entity(outlier_threshold="20"), _profile({10: 400, 20: 300, 50: 3, 100: 0}), NOW),
        rec.recommend(entity, _profile({10: 500, 20: 400, 50: 300, 100: 200}), NOW),
        rec.recommend(entity, _profile({10: 0}, judged=1000, blind=4000), NOW),
        rec.recommend(_entity(outlier_threshold="off"), None, NOW),
        rec.recommend(entity, _profile({10: 0}), NOW),
    ]
    assert {c["status"] for c in cases} == {
        rec.STATUS_INSUFFICIENT, rec.STATUS_TOO_SENSITIVE, rec.STATUS_UNSUITABLE, rec.STATUS_BLIND,
        rec.STATUS_DISABLED, rec.STATUS_OK,
    }
    for result in cases:
        text = rec.describe(entity, result)
        assert text["status_label"] and text["status_tip"] and text["rank"] >= 0


def test_recommendations_reads_the_profile_from_the_cached_stats() -> None:
    entity = _entity()
    out = rec.recommendations([entity], {"sensor.x": {"outlier_profile": _profile({10: 0})}}, NOW)
    assert out["sensor.x"]["recommended"] == "10"
    assert rec.recommendations([entity], {}, NOW)["sensor.x"]["reason"] == "unmeasured"
