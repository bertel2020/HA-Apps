"""Stichtag der Zeitraum-Navigation eines Dashboards (chart_period.stichtag_window())."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.chart_period import stichtag_or_400, stichtag_window

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 21, 14, 20, tzinfo=TZ)  # Mittwoch


def test_ohne_oder_mit_heutigem_stichtag_bleibt_alles_wie_es_ist():
    assert stichtag_window("month", False, None, NOW) == (NOW, 0)
    assert stichtag_window("month", False, date(2026, 10, 21), NOW) == (NOW, 0)
    assert stichtag_window("day", True, date(2026, 10, 22), NOW) == (NOW, 0)


@pytest.mark.parametrize(("range_key", "stichtag", "shift"), [
    ("day", date(2026, 10, 20), -1),
    ("day", date(2025, 10, 21), -365),
    # Kalenderwochen: Montag 19.10. ist dieselbe Woche, Sonntag 18.10. die davor.
    ("week", date(2026, 10, 19), 0),
    ("week", date(2026, 10, 18), -1),
    ("month", date(2026, 10, 1), 0),
    ("month", date(2025, 12, 31), -10),
    ("year", date(2025, 10, 21), -1),
    ("decade", date(2019, 12, 31), -1),
    ("hour", date(2026, 10, 20), -26),  # Mittag des Vortags
])
def test_kalendarische_zeitraeume_bekommen_die_periode_die_den_stichtag_enthaelt(range_key, stichtag, shift):
    assert stichtag_window(range_key, False, stichtag, NOW) == (NOW, shift)


def test_rollierende_fenster_enden_am_ende_des_stichtags():
    jetzt, versatz = stichtag_window("week", True, date(2025, 3, 30), NOW)
    assert versatz == 0
    assert jetzt == datetime(2025, 3, 31, 0, 0, tzinfo=TZ)


def test_stichtag_wird_streng_geprueft():
    assert stichtag_or_400(None) is None
    assert stichtag_or_400("2025-10-21") == date(2025, 10, 21)
    with pytest.raises(HTTPException):
        stichtag_or_400("21.10.2025")
