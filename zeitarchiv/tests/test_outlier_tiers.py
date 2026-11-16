"""Mitzählen mehrerer Schwellen-Stufen in einem Durchlauf (Grundlage der Empfehlung).

Die Zählung je Stufe muss exakt dem Ergebnis eines eigenen Detektors mit genau dieser
Stufe als Schwelle entsprechen — auch wo die Fortsetzungsregel eine gröbere Stufe mehr
markieren lässt als eine feinere (nicht monoton).
"""

import random
from zoneinfo import ZoneInfo

import pytest

from app.storage.cleanup import OutlierDetector

TZ = ZoneInfo("Europe/Berlin")
TIERS = (10.0, 20.0, 50.0, 100.0)


def _run(series, mode, **kw):
    d = OutlierDetector(decimals="auto", tz=TZ, reasons=False, mode=mode, **kw)
    out = []
    for ts, v in series:
        grund = d.check(ts, v)
        out.append((grund is not None, dict(d.tier_hits), d.judged))
    return out


def _check(series, mode):
    multi = _run(series, mode, factor=None, tiers=TIERS)
    for t in TIERS:
        single = _run(series, mode, factor=t)
        for i, ((hit, _, _), (_, hits, _)) in enumerate(zip(single, multi, strict=True)):
            assert hit == hits.get(t, False), (mode, t, i)


def test_non_monotonic_case_matches_separate_runs():
    values = [32, 40, 33, 41, 32, 42, 33, 40, 32, 41] * 6 + [95, 96, 32, 33, 95, 33]
    series = [(i * 60.0, float(v)) for i, v in enumerate(values)]
    _check(series, "standard")


@pytest.mark.parametrize("seed", range(8))
def test_random_standard_series(seed):
    rnd = random.Random(seed)
    series, level = [], 20.0
    for i in range(1500):
        if rnd.random() < 0.01:
            level += rnd.choice([-1, 1]) * rnd.uniform(5, 80)
        v = level + rnd.gauss(0, 1.5)
        if rnd.random() < 0.01:
            v *= rnd.choice([10, 100, 1000])
        series.append((i * 60.0, round(v, 1)))
    _check(series, "standard")


@pytest.mark.parametrize("seed", range(8))
def test_random_counter_series(seed):
    rnd = random.Random(seed)
    series, ts, stand = [], 0.0, 1000.0
    for _ in range(1500):
        ts += rnd.choice([60.0] * 8 + [900.0, 3600.0])
        stand += rnd.expovariate(1 / 0.3) * (ts and 1)
        v = stand
        if rnd.random() < 0.01:
            v = stand * rnd.choice([10, 100, 1000])
        if rnd.random() < 0.005:
            v = 0.0
        series.append((ts, v))
    _check(series, "counter")


def test_judged_reports_blind_values():
    series = [(i * 60.0, 5.0) for i in range(30)]
    multi = _run(series, "standard", factor=None, tiers=TIERS)
    assert multi[2][2] is None       # vor OUTLIER_MIN_VALUES: nicht zur Prüfung
    assert all(j is False for _, _, j in multi[6:])   # MAD 0: blind
    assert not any(any(h.values()) for _, h, _ in multi)


def test_tiers_do_not_change_own_threshold_result():
    rnd = random.Random(1)
    series = [(i * 60.0, rnd.gauss(20, 1) * (50 if rnd.random() < 0.02 else 1)) for i in range(800)]
    assert [h for h, _, _ in _run(series, "standard", factor=50.0)] == [
        h for h, _, _ in _run(series, "standard", factor=50.0, tiers=TIERS)
    ]
