"""Messwert-Linien in Stunde/Tag: jeder Rohwert gilt bis zum nächsten (Issue 12, Wasserpumpe).

Eine Pumpe meldet beim Laufen oft ~265 W und beim Abschalten einmal 0 W, in der Pause nichts mehr. Der Mittelwert
nach Anzahl ergab 195 W statt 0, und die leeren Buckets der Pause fehlten, sodass die Linie schräg zum nächsten
Lauf stieg.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

try:
    from app.storage import rollup

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="pyarrow fehlt")

TZ = ZoneInfo("Europe/Berlin")
BUCKET = 300


def _ts(h: int, mi: int = 0, s: int = 0) -> float:
    return datetime(2026, 11, 16, h, mi, s, tzinfo=TZ).timestamp()


def _held(rows, boundary, start, end):
    return rollup.compute_held_standard_rollup(
        rows, boundary, rollup.seconds_bucket_key(TZ, BUCKET), rollup.seconds_bucket_next(BUCKET), start, end
    )


def _by_start(fine) -> dict[float, rollup.FineRow]:
    return {row.bucket_start: row for row in fine}


def test_pause_bleibt_null_statt_schraeger_bruecke():
    # 265 W bis kurz nach 01:00, dann 0 W bis 04:00, dann wieder 270 W.
    rows = [(_ts(0, 59), 265.0), (_ts(1, 2, 30), 0.0), (_ts(4, 0, 20), 270.0)]
    fine = _by_start(_held(rows, 265.0, _ts(0), _ts(5)))
    assert fine[_ts(2, 30)].value == 0.0
    assert fine[_ts(3, 55)].value == 0.0
    assert fine[_ts(4, 0)].value == pytest.approx((0.0 * 20 + 270.0 * 280) / 300)
    # jeder Bucket des Fensters hat einen Wert, nichts fehlt
    assert len(fine) == 5 * 12


def test_mischbucket_ist_zeitgewichtet():
    # Im Bucket 01:00–01:05 läuft die Pumpe 150 s, dann 150 s aus: Mittel 132,5, nicht (265+0)/2 nach Anzahl.
    rows = [(_ts(0, 59), 265.0), (_ts(1, 2, 30), 0.0)]
    fine = _by_start(_held(rows, None, _ts(0), _ts(2)))
    assert fine[_ts(1, 0)].value == pytest.approx(132.5)
    assert fine[_ts(1, 0)].min_value == 0.0
    assert fine[_ts(1, 0)].max_value == 265.0


def test_randwert_gilt_ab_fensteranfang():
    fine = _by_start(_held([(_ts(0, 30), 10.0)], 4.0, _ts(0), _ts(1)))
    assert fine[_ts(0, 0)].value == 4.0
    assert fine[_ts(0, 30)].value == 10.0


def test_nichts_ueber_das_haltende_ende_hinaus():
    # "jetzt" liegt um 00:20: es gibt keine Buckets danach.
    fine = _held([(_ts(0, 5), 7.0)], None, _ts(0), _ts(0, 20))
    assert max(row.bucket_start for row in fine) < _ts(0, 20)


def test_ohne_daten_leer():
    assert _held([], None, _ts(0), _ts(1)) == []
