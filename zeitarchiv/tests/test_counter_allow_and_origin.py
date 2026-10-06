"""Zähler mit erlaubten Rückgängen (Konfiguration „Zählerrückgänge“), Herkunft der Markierungen
und die Meldung bei Fehlwert-Häufung."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import counter_bulk, entity_history, notices
from app.storage import cleanup, hotbuffer
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _counter(tmp_path, entity_id="sensor.z", values=(100.0, 101.0, 0.0, 101.5, 102.0), minutes_ago=40):
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    now = datetime.now(TZ)
    base = now.timestamp() - minutes_ago * 60
    for i, value in enumerate(values):
        hotbuffer.append(tmp_path, entity_id, base + i * 300, value, TZ)
    return index, now, base


# -- Rückgänge erlauben ---------------------------------------------------------------------------

def test_an_allowed_counter_is_left_out_of_the_scan_and_the_flags(tmp_path) -> None:
    from app import cleanup_stats

    index, now, _ = _counter(tmp_path)
    assert [r["entity_id"] for r in cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now)] == ["sensor.z"]
    assert cleanup_stats.counter_decreases_flagged(index.get_entity("sensor.z"))

    index.set_counter_decreases("sensor.z", "allow")

    assert cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now) == []
    assert not cleanup_stats.counter_decreases_flagged(index.get_entity("sensor.z"))
    assert [r["entity_id"] for r in index.list_counter_decreases_allowed()] == ["sensor.z"]
    index.close()


def test_switching_the_mode_updates_the_snapshot_for_that_counter_only(tmp_path) -> None:
    index, now, _ = _counter(tmp_path)
    index.get_or_create_entity("sensor.other", "sensor", "total_increasing", "kWh")
    for i, value in enumerate([5.0, 6.0, 0.0, 6.5]):
        hotbuffer.append(tmp_path, "sensor.other", now.timestamp() - 3000 + i * 300, value, TZ)
    index.set_counter_decrease_snapshot(cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now))

    counter_bulk.set_mode(tmp_path, index, TZ, "sensor.z", "allow")
    assert [r["entity_id"] for r in index.get_counter_decrease_snapshot()["rows"]] == ["sensor.other"]

    counter_bulk.set_mode(tmp_path, index, TZ, "sensor.z", "report")
    assert {r["entity_id"] for r in index.get_counter_decrease_snapshot()["rows"]} == {"sensor.z", "sensor.other"}
    index.close()


def test_the_mode_is_validated(tmp_path) -> None:
    index, _, _ = _counter(tmp_path)
    try:
        index.set_counter_decreases("sensor.z", "nonsense")
    except ValueError:
        pass
    else:
        raise AssertionError("ungültiger Modus muss abgelehnt werden")
    index.close()


def test_the_config_field_and_route_switch_the_mode(client) -> None:
    from app.main import DATA_DIR, index

    now = datetime.now(TZ)
    index.get_or_create_entity("sensor.pytest_allow", "sensor", "total_increasing", "kWh")
    try:
        page = client.get("/entities/sensor.pytest_allow/config").text
        assert 'id="counter-decrease-field"' in page
        changed = client.post("/entities/sensor.pytest_allow/counter-decreases", data={"counter_decreases": "allow"})
        assert changed.status_code == 200
        assert "keine Meldung" in changed.text or "no notification" in changed.text
        assert index.get_entity("sensor.pytest_allow")["counter_decreases"] == "allow"
        assert client.post("/entities/sensor.pytest_allow/counter-decreases", data={"counter_decreases": "x"}).status_code == 400
        # kein Zähler → 404
        index.get_or_create_entity("sensor.pytest_temp", "sensor", "measurement", "°C")
        assert client.post("/entities/sensor.pytest_temp/counter-decreases", data={"counter_decreases": "allow"}).status_code == 404
        assert 'id="counter-decrease-field"' not in client.get("/entities/sensor.pytest_temp/config").text
    finally:
        index.set_counter_decreases("sensor.pytest_allow", "report")
        index.set_counter_decrease_snapshot([])
    assert DATA_DIR and now


def test_housekeeping_offers_allow_for_meters_that_stay_low_and_lists_allowed_ones(client) -> None:
    from app.main import index

    ts = datetime.now(TZ).timestamp()
    row = {
        "entity_id": "sensor.pytest_stays", "friendly_name": "Tageszähler", "unit": "kWh", "decimals": "auto",
        "count": 3, "returning": 0, "returning_days": [],
        "last": {"ts": ts, "previous": 12.8, "value": 0.0, "returns": False, "recovered_value": None, "recovered_after": None},
    }
    index.get_or_create_entity("sensor.pytest_stays", "sensor", "total_increasing", "kWh")
    try:
        index.set_counter_decrease_snapshot([row])
        html = client.get("/housekeeping").text
        assert "entities/sensor.pytest_stays/counter-decreases" in html
        index.set_counter_decreases("sensor.pytest_stays", "allow")
        index.set_counter_decrease_snapshot([])
        html = client.get("/housekeeping").text
        assert "counter-allowed" in html
        assert "sensor.pytest_stays/config" in html
    finally:
        index.set_counter_decreases("sensor.pytest_stays", "report")
        index.set_counter_decrease_snapshot([])


# -- Herkunft der Markierung -----------------------------------------------------------------------

def test_a_mark_records_its_origin_and_the_batch_it_belongs_to(tmp_path) -> None:
    index, _, base = _counter(tmp_path)
    cleanup.soft_delete(index, "sensor.z", [base + 600], deleted_at=1234.5, source="duplicates")
    cleanup.soft_delete(index, "sensor.z", [base + 300], deleted_at=2345.5)
    cleanup.soft_delete(index, "sensor.z", [base], deleted_at=3456.5, source="not-a-source")

    assert entity_history.mark_sources(index, "sensor.z") == {1234.5: "duplicates", 2345.5: "manual", 3456.5: "manual"}
    index.close()


def test_batches_without_an_origin_are_not_guessed(tmp_path) -> None:
    index, _, base = _counter(tmp_path)
    index.mark_deleted("sensor.z", [base], deleted_at=99.0)  # wie vor dieser Änderung: ohne Protokoll
    assert entity_history.mark_sources(index, "sensor.z") == {}
    index.close()


def test_the_bulk_marking_is_labelled_as_such(tmp_path) -> None:
    index, now, _ = _counter(tmp_path)
    index.set_counter_decrease_snapshot(cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now))
    outcome = counter_bulk.mark_returning(tmp_path, index, TZ, now=now)
    assert entity_history.mark_sources(index, "sensor.z") == {outcome["batch_at"]: "counter_bulk"}
    index.close()


def test_the_marked_tab_shows_the_origin_per_batch(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_origin", "sensor", "total_increasing", "kWh")
    try:
        cleanup.soft_delete(index, "sensor.pytest_origin", [1_000_000.0], deleted_at=5000.0, source="duplicates")
        index.mark_deleted("sensor.pytest_origin", [1_000_300.0], deleted_at=4000.0)
        html = client.get("/entities/sensor.pytest_origin/marked").text
        assert "marked-source" in html
        assert "Duplikate" in html or "Duplicates" in html
        assert "unbekannt" in html or "unknown" in html
    finally:
        index.undo_all_deleted("sensor.pytest_origin")


# -- Fehlwert-Häufung --------------------------------------------------------------------------------

def _row(days: list[str], entity_id="sensor.z") -> dict:
    return {
        "entity_id": entity_id, "friendly_name": "Smart Meter", "unit": "kWh", "decimals": "auto",
        "count": len(days), "returning": len(days), "returning_days": days,
        "last": {"ts": 1_000_000.0, "previous": 10.0, "value": 0.0, "returns": True, "recovered_value": 10.2, "recovered_after": 300},
    }


def test_the_scan_collects_the_days_with_a_returning_decrease(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.z", "sensor", "total_increasing", "kWh")
    now = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    for day in (1, 2, 3):
        start = (now - timedelta(days=day)).timestamp()
        for i, value in enumerate([100.0 + day, 101.0 + day, 0.0, 101.5 + day, 102.0 + day]):
            hotbuffer.append(tmp_path, "sensor.z", start + i * 300, value, TZ)
    rows = cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now)
    assert len(rows[0]["returning_days"]) == 3
    index.close()


def test_three_days_make_a_pattern_notice_that_replaces_the_single_one(tmp_path) -> None:
    pattern = notices.counter_pattern_notice(_row(["2026-10-01", "2026-10-03", "2026-10-05"]))
    assert pattern["id"] == "housekeeping.counter_pattern.sensor.z"
    assert "wiederholt Fehlwerte" in pattern["title"] or "repeatedly" in pattern["title"]
    assert pattern["link"] == "/entities/sensor.z/cleanup"


def test_the_pattern_notice_appears_in_the_panel_and_the_single_notice_steps_aside(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    rows = [_row(["2026-10-01", "2026-10-03", "2026-10-05"], "sensor.a"), _row(["2026-10-05"], "sensor.b")]
    index.set_counter_decrease_snapshot(rows)
    found = notices.build_notices(index, tmp_path / "index.sqlite", TZ, {}, None, 0, 0.0, 0.0, False)
    ids = [n["id"] for n in found]
    assert "housekeeping.counter_pattern.sensor.a" in ids
    single = next(n for n in found if n["id"] == "housekeeping.counter_decreases")
    assert "sensor.b" in single["link"] or "Smart Meter" in single["detail"]
    index.close()


# -- Regel „Fehlwerte automatisch markieren“ ---------------------------------------------------------

def _rule_counter(tmp_path, enabled_minutes_ago=60, **kwargs):
    index, now, base = _counter(tmp_path, **kwargs)
    index.set_counter_auto_mark("sensor.z", True, now.timestamp() - enabled_minutes_ago * 60)
    return index, now, base


def _run(tmp_path, index, now):
    from contextlib import nullcontext

    from app import counter_auto

    index.set_setting("counter_auto_mark_last_run", "0")
    return counter_auto.run_if_due(tmp_path, index, TZ, lambda _eid: nullcontext(), now=now)


def test_the_rule_is_off_by_default_and_marks_nothing(tmp_path) -> None:
    index, now, base = _counter(tmp_path)
    assert index.get_entity("sensor.z")["counter_auto_mark"] == 0
    assert index.list_counter_auto_entities() == []
    _run(tmp_path, index, now)
    assert index.get_deleted_counts("sensor.z", base, base + 3600) == {}
    index.close()


def test_the_rule_marks_a_returning_decrease_with_origin_and_trigger(tmp_path) -> None:
    index, now, base = _rule_counter(tmp_path, enabled_minutes_ago=60)
    assert _run(tmp_path, index, now)

    assert index.get_deleted_counts("sensor.z", base, base + 3600) == {base + 2 * 300: 1}
    rows = index.list_entity_actions_for_entity("sensor.z", actions=("mark",))
    assert rows[0]["trigger"] == "automatic"
    assert set(entity_history.mark_sources(index, "sensor.z").values()) == {"counter_rule"}
    index.close()


def test_the_rule_only_looks_at_decreases_after_it_was_switched_on(tmp_path) -> None:
    index, now, base = _rule_counter(tmp_path, enabled_minutes_ago=5)  # Rückgang liegt 30 Minuten zurück
    _run(tmp_path, index, now)
    assert index.get_deleted_counts("sensor.z", base, base + 3600) == {}
    index.close()


def test_the_rule_does_not_mark_a_decrease_that_stays_low(tmp_path) -> None:
    values = [500.0, 501.0, 1.0] + [1.0 + i * 0.1 for i in range(1, 14)]
    index, now, base = _rule_counter(tmp_path, values=values, minutes_ago=90, enabled_minutes_ago=120)
    _run(tmp_path, index, now)
    assert index.get_deleted_counts("sensor.z", base, base + 7200) == {}
    index.close()


def test_a_run_is_limited_to_once_per_hour(tmp_path) -> None:
    from contextlib import nullcontext

    from app import counter_auto

    index, now, _ = _rule_counter(tmp_path)
    assert counter_auto.run_if_due(tmp_path, index, TZ, lambda _e: nullcontext(), now=now) is True
    assert counter_auto.run_if_due(tmp_path, index, TZ, lambda _e: nullcontext(), now=now) is False
    index.close()


def test_an_undone_batch_is_not_marked_again(tmp_path) -> None:
    index, now, base = _rule_counter(tmp_path)
    _run(tmp_path, index, now)
    assert index.undo_last_deleted_batch("sensor.z") == 1
    _run(tmp_path, index, now)
    assert index.get_deleted_counts("sensor.z", base, base + 3600) == {}
    index.close()


def test_undoing_by_batch_and_by_selection_is_remembered_too(tmp_path) -> None:
    index, now, base = _rule_counter(tmp_path)
    _run(tmp_path, index, now)
    (batch,) = entity_history.mark_sources(index, "sensor.z")
    assert index.undo_deleted_batch("sensor.z", batch) == 1
    assert index.list_counter_mark_ignored("sensor.z") == {base + 2 * 300}
    index.close()


def test_the_safety_limit_halts_the_rule_instead_of_marking(tmp_path) -> None:
    from app import counter_auto

    values = [100.0, 99.0] * 60  # 59 Rückgänge, jeder kehrt zurück
    index, now, base = _rule_counter(tmp_path, values=values, minutes_ago=700, enabled_minutes_ago=800)
    assert 59 > counter_auto.MAX_PER_RUN
    _run(tmp_path, index, now)

    assert index.get_deleted_counts("sensor.z", base, base + 86400) == {}
    assert index.get_entity("sensor.z")["counter_auto_halted_at"] is not None
    assert [n["id"] for n in counter_auto.notices(index, TZ)] == ["housekeeping.counter_rule_halted.sensor.z"]
    # Wiedereinschalten hebt das „angehalten“ auf
    index.set_counter_auto_mark("sensor.z", True, now.timestamp())
    assert index.get_entity("sensor.z")["counter_auto_halted_at"] is None
    index.close()


def test_allowing_decreases_switches_the_rule_off(tmp_path) -> None:
    index, _, _ = _rule_counter(tmp_path)
    index.set_counter_decreases("sensor.z", "allow")
    entity = index.get_entity("sensor.z")
    assert entity["counter_auto_mark"] == 0 and entity["counter_auto_since"] is None
    index.set_counter_decreases("sensor.z", "report")
    assert index.get_entity("sensor.z")["counter_auto_mark"] == 0  # bleibt aus
    index.close()


def test_the_bulk_action_leaves_rule_counters_to_the_rule(tmp_path) -> None:
    index, now, base = _rule_counter(tmp_path, enabled_minutes_ago=5)  # die Regel sieht den Rückgang nicht
    index.set_counter_decrease_snapshot(cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now))
    assert counter_bulk.mark_returning(tmp_path, index, TZ, now=now)["entities"] == {}
    index.close()


def test_an_info_notice_needs_marks_on_three_days(tmp_path) -> None:
    import json
    import time

    from app import counter_auto

    index, now, _ = _rule_counter(tmp_path)
    def mark_days_ago(days_ago: int) -> None:
        stamp = time.time() - days_ago * 86400
        index.log_entity_action("sensor.z", "mark", "automatic", stamp, stamp, "success", rows_affected=1, detail=json.dumps({"source": "counter_rule", "batch": stamp}))
        index._conn.execute("UPDATE entity_actions SET created_at = ? WHERE id = (SELECT MAX(id) FROM entity_actions)", (stamp,))
        index._conn.commit()

    mark_days_ago(1)
    mark_days_ago(2)
    assert counter_auto.notices(index, TZ) == []
    mark_days_ago(3)
    found = counter_auto.notices(index, TZ)
    assert [n["id"] for n in found] == ["housekeeping.counter_rule.sensor.z"] and found[0]["severity"] == "info"
    index.close()


def test_the_route_switches_the_rule_only_for_report_counters(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_rule", "sensor", "total_increasing", "kWh")
    try:
        assert client.post("/entities/sensor.pytest_rule/counter-decreases", data={"auto_mark": "on"}).status_code == 200
        entity = index.get_entity("sensor.pytest_rule")
        assert entity["counter_auto_mark"] == 1 and entity["counter_auto_since"] is not None
        assert client.post("/entities/sensor.pytest_rule/counter-decreases", data={"counter_decreases": "allow"}).status_code == 200
        assert index.get_entity("sensor.pytest_rule")["counter_auto_mark"] == 0
        assert client.post("/entities/sensor.pytest_rule/counter-decreases", data={"auto_mark": "on"}).status_code == 409
        assert client.post("/entities/sensor.pytest_rule/counter-decreases", data={"auto_mark": "maybe"}).status_code == 400
    finally:
        index.set_counter_decreases("sensor.pytest_rule", "report")


def test_the_config_page_shows_the_switch_only_for_report(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_rule2", "sensor", "total_increasing", "kWh")
    try:
        assert "counter-auto" in client.get("/entities/sensor.pytest_rule2/config").text
        index.set_counter_decreases("sensor.pytest_rule2", "allow")
        assert 'class="counter-auto"' not in client.get("/entities/sensor.pytest_rule2/config").text
    finally:
        index.set_counter_decreases("sensor.pytest_rule2", "report")


def test_housekeeping_lists_a_rule_counter_even_when_nothing_is_open(client) -> None:
    import json
    import time

    from app.main import index

    index.get_or_create_entity("sensor.pytest_rule3", "sensor", "total_increasing", "kWh")
    index.set_counter_auto_mark("sensor.pytest_rule3", True, time.time() - 3600)
    now = time.time()
    index.log_entity_action("sensor.pytest_rule3", "mark", "automatic", now, now, "success", rows_affected=4, detail=json.dumps({"source": "counter_rule", "batch": now}))
    try:
        index.set_counter_decrease_snapshot([])
        html = client.get("/housekeeping").text
        assert "sensor.pytest_rule3" in html
        assert "4 automatisch markiert" in html or "4 marked automatically" in html
    finally:
        index.set_counter_decreases("sensor.pytest_rule3", "allow")  # schaltet die Regel aus
        index.set_counter_decreases("sensor.pytest_rule3", "report")
