"""Tests für die Live-Auflösung von Standard-Entitäten (resolution.py):
Zeitfenster im Hot Buffer zu Ø/Min/Max zusammenfassen, Zeitstempel = Bucket-
Ende statt Bucket-Anfang."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from _paths import ADDON  # noqa: F401  (stellt sys.path sicher)

from app.storage import hotbuffer, resolution


def test_bucket_end_rounds_up_to_the_closing_window() -> None:
    # 5-Minuten-Raster (300s): ein Wert exakt auf der Grenze gehört zum
    # SCHLIESSENDEN Fenster, nicht zum neu beginnenden.
    assert resolution.bucket_end(300, 0.0) == 0.0
    assert resolution.bucket_end(300, 0.001) == 300.0
    assert resolution.bucket_end(300, 299.999) == 300.0
    assert resolution.bucket_end(300, 300.0) == 300.0
    assert resolution.bucket_end(300, 300.001) == 600.0


def test_collapse_pending_window_writes_average_min_max_at_bucket_end() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-resolution-test-"))
    try:
        tz = ZoneInfo("UTC")
        path = hotbuffer.hot_path(tmp, "sensor.temp", 1000.0, tz)
        for ts, value in [(603.0, 21.0), (623.0, 21.1), (671.0, 24.8), (745.0, 22.0), (899.0, 21.2)]:
            hotbuffer.append(tmp, "sensor.temp", ts, value, tz)

        resolution.collapse_pending_window(path, 300)

        records = hotbuffer.read_full_rows(path)
        assert len(records) == 1
        ts, value, event_id, min_value, max_value = records[0]
        assert ts == 900.0  # Bucket-Ende, nicht -Anfang (603..899 liegen im Fenster (600, 900])
        # sum()/len() wie in resolution.collapse_pending_window(), statt einer
        # von Hand addierten Kette — sonst driftet die Fließkomma-Rundung
        # minimal auseinander (0+21.0+21.1+… summiert nicht bitgleich zu
        # 21.0+21.1+…).
        assert value == sum([21.0, 21.1, 24.8, 22.0, 21.2]) / 5
        assert min_value == 21.0
        assert max_value == 24.8
        assert event_id is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collapse_pending_window_is_a_noop_without_pending_rows() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-resolution-test-"))
    try:
        tz = ZoneInfo("UTC")
        path = hotbuffer.hot_path(tmp, "sensor.temp", 1000.0, tz)
        hotbuffer.append(tmp, "sensor.temp", 603.0, 21.0, tz)
        resolution.collapse_pending_window(path, 300)
        before = hotbuffer.read_full_rows(path)

        # Zweiter Aufruf ohne neue Rohzeilen dazwischen: nichts zu tun, weil
        # die einzige Zeile schon aufgelöst ist (min_value gesetzt) —
        # relevant für den Sicherheitsnetz-Lauf, der dieselbe Datei mehrfach
        # treffen kann.
        resolution.collapse_pending_window(path, 300)
        after = hotbuffer.read_full_rows(path)
        assert before == after
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pending_bucket_end_reflects_only_unresolved_trailing_rows() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-resolution-test-"))
    try:
        tz = ZoneInfo("UTC")
        path = hotbuffer.hot_path(tmp, "sensor.temp", 1000.0, tz)
        assert resolution.pending_bucket_end(path, 300) is None

        hotbuffer.append(tmp, "sensor.temp", 100.0, 21.0, tz)
        assert resolution.pending_bucket_end(path, 300) == 300.0

        resolution.collapse_pending_window(path, 300)
        assert resolution.pending_bucket_end(path, 300) is None

        hotbuffer.append(tmp, "sensor.temp", 605.0, 22.0, tz)
        assert resolution.pending_bucket_end(path, 300) == 900.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
