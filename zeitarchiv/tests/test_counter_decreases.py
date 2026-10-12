"""Meldung und Housekeeping-Abschnitt zu Zählerrückgängen: Einordnung „kehrt zurück“ /
„bleibt niedrig“, die zählende Meldung und ihr Verschwinden nach dem Markieren."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app import notices
from app.storage import cleanup
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _rows(*values: float, start: float = 1_000_000.0, step: float = 300.0):
    return [(start + i * step, v) for i, v in enumerate(values)]


def test_a_dip_that_comes_back_within_the_window_is_a_bad_value() -> None:
    found = cleanup.classify_counter_decreases(_rows(100, 101, 0, 101.5, 102))
    assert len(found) == 1
    assert found[0]["returns"] is True
    assert found[0]["previous"] == 101 and found[0]["value"] == 0
    assert found[0]["recovered_value"] == 101.5 and found[0]["recovered_after"] == 300


def test_a_reset_that_stays_low_is_not_a_bad_value() -> None:
    # 12 niedrige Folgewerte im 5-Minuten-Takt: beide Grenzen (30 Min., 10 Werte) sind überschritten.
    found = cleanup.classify_counter_decreases(_rows(500, 501, *[1 + i * 0.1 for i in range(14)]))
    assert [item["returns"] for item in found] == [False]


def test_the_assessment_stays_open_while_the_data_is_too_short_to_decide() -> None:
    found = cleanup.classify_counter_decreases(_rows(500, 501, 1, 1.1))
    assert [item["returns"] for item in found] == [None]


def test_either_limit_is_enough_to_count_as_returning() -> None:
    # Dichter Takt (30 s): die Rückkehr kommt nach 9 Werten, also nach 4,5 Minuten.
    dense = _rows(100, 101, 0, *[0.1] * 8, 101.2, step=30.0)
    assert cleanup.classify_counter_decreases(dense)[0]["returns"] is True
    # Dünner Takt (1 h): schon der 2. Folgewert liegt über 30 Minuten, aber unter 10 Werten.
    sparse = _rows(100, 101, 0, 0.1, 101.2, step=3600.0)
    assert cleanup.classify_counter_decreases(sparse)[0]["returns"] is True


def test_rising_values_are_never_flagged_and_the_low_value_becomes_the_new_baseline() -> None:
    assert cleanup.classify_counter_decreases(_rows(1, 2, 3, 4)) == []
    found = cleanup.classify_counter_decreases(_rows(100, 5, 6, 7, 8))
    assert len(found) == 1  # 6, 7, 8 liegen über dem neuen Bezug 5


def _row(entity_id="sensor.zaehler", count=1, returning=1, returns=True) -> dict:
    ts = datetime(2026, 10, 3, 12, 0, tzinfo=TZ).timestamp()
    return {
        "entity_id": entity_id, "friendly_name": "Smart Meter", "unit": "kWh", "decimals": "auto",
        "count": count, "returning": returning,
        "last": {
            "ts": ts, "previous": 15204.3, "value": 0.0, "returns": returns,
            "recovered_value": 15204.6 if returns else None, "recovered_after": 300 if returns else None,
        },
    }


def test_no_notice_without_decreases() -> None:
    assert notices.counter_decrease_notice([], TZ) is None


def test_one_meter_names_the_meter_and_links_to_its_cleanup_page() -> None:
    notice = notices.counter_decrease_notice([_row()], TZ)
    assert notice["title"] == "Zählerrückgang erkannt"
    assert "Smart Meter" in notice["detail"] and "03.10." in notice["detail"]
    assert "Wahrscheinlich ein Fehlwert" in notice["detail"]
    assert notice["link"] == "/entities/sensor.zaehler/cleanup"


def test_a_meter_that_stays_low_is_described_as_a_possible_replacement() -> None:
    notice = notices.counter_decrease_notice([_row(returning=0, returns=False)], TZ)
    assert "Zählerwechsel" in notice["detail"]


def test_several_meters_give_one_counting_notice_linking_to_housekeeping() -> None:
    rows = [_row("sensor.a", count=2, returning=2), _row("sensor.b", count=1, returning=0, returns=False)]
    notice = notices.counter_decrease_notice(rows, TZ)
    assert notice["title"] == "Zählerrückgänge erkannt"
    assert "3 Rückgänge bei 2 Zählern" in notice["detail"]
    assert "Bei 2 davon" in notice["detail"]
    assert notice["link"] == "/housekeeping#zaehlerrueckgaenge"


def test_marking_invalidates_the_snapshot_so_the_notice_ends(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.z", "sensor", "total_increasing", "kWh")
    index.set_counter_decrease_snapshot([_row("sensor.z")])
    assert not index.is_counter_decrease_snapshot_stale()

    cleanup.soft_delete(index, "sensor.z", [1_000_600.0])

    assert index.is_counter_decrease_snapshot_stale()
    assert index.get_counter_decrease_snapshot()["rows"] == []
    index.close()


def test_the_scan_only_looks_at_counters_and_skips_marked_values(tmp_path) -> None:
    from app.storage import hotbuffer

    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.z", "sensor", "total_increasing", "kWh")
    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    now = datetime.now(TZ)
    base = now.timestamp() - 3600
    values = [100.0, 101.0, 0.0, 101.5]
    for entity_id in ("sensor.z", "sensor.t"):
        for i, value in enumerate(values):
            hotbuffer.append(tmp_path, entity_id, base + i * 300, value, TZ)

    result = cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now)
    assert [row["entity_id"] for row in result] == ["sensor.z"]
    assert result[0]["last"]["returns"] is True

    index.mark_deleted("sensor.z", [base + 2 * 300])
    assert cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now) == []
    index.close()


def test_the_housekeeping_page_lists_the_meters_with_their_assessment(client) -> None:
    from app.main import index

    index.set_counter_decrease_snapshot([_row("sensor.pytest_zaehler")])
    html = client.get("/housekeeping").text
    assert 'id="zaehlerrueckgaenge"' in html
    assert "sensor.pytest_zaehler" in html
    assert "kehrt zurück" in html
    assert "housekeeping#zaehlerrueckgaenge" in html  # Eintrag in der Navigation
    index.set_counter_decrease_snapshot([])  # keine Spuren für andere Tests


def test_a_daily_reset_to_zero_is_not_a_decrease_but_a_glitch_at_midnight_with_a_high_value_is() -> None:
    day = datetime(2026, 10, 5, 23, 0, tzinfo=TZ).timestamp()
    reset = [(day, 12.5), (day + 1800, 12.5), (day + 3600 + 1300, 0.0), (day + 3600 + 1600, 0.1)]
    assert cleanup.classify_counter_decreases(reset) != []  # ohne Zeitzone: jeder Rückgang
    assert cleanup.classify_counter_decreases(reset, TZ) == []
    # Fällt der Stand über Mitternacht nur leicht, ist es kein planmäßiges Rücksetzen.
    slight = [(day, 12.5), (day + 3600 + 1300, 9.0)]
    assert len(cleanup.classify_counter_decreases(slight, TZ)) == 1
    # Dasselbe Verhalten mitten am Tag: auch ein Rückgang auf 0 zählt.
    midday = [(day + 40000, 12.5), (day + 40300, 0.0)]
    assert len(cleanup.classify_counter_decreases(midday, TZ)) == 1


def test_a_snapshot_computed_under_older_rules_is_stale_and_not_shown(tmp_path) -> None:
    import json

    index = Index(tmp_path / "index.sqlite")
    index.set_setting(
        "counter_decrease_snapshot_cache", json.dumps({"checked_at": time.time(), "rows": [_row("sensor.alt")]})
    )
    assert index.is_counter_decrease_snapshot_stale()
    assert index.get_counter_decrease_snapshot() is None

    index.set_counter_decrease_snapshot([_row("sensor.neu")])
    assert not index.is_counter_decrease_snapshot_stale()
    assert index.get_counter_decrease_snapshot()["rows"][0]["entity_id"] == "sensor.neu"
    index.close()
