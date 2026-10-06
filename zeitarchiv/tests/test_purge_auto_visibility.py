"""Sichtbarkeit der automatischen Bereinigung: Banner, Chargen im Reiter „Markiert“ und die Meldung
„Endgültige Bereinigung möglich“."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import notices, purge_auto
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=TZ).timestamp()
DAY = 86400.0


def _index(tmp_path, enabled: bool, age: str = "7") -> Index:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.a", "sensor", "total_increasing", "kWh")
    index.set_setting("purge_auto_enabled", "on" if enabled else "off")
    index.set_setting("purge_min_age_days", age)
    return index


def test_a_batch_far_from_due_shows_the_date_only() -> None:
    due = purge_auto.batch_due(NOW - 1 * DAY, 30, TZ, NOW)
    assert due["soon"] is False
    assert due["label"] == "wird ab 04.11. entfernt"


def test_a_batch_due_within_three_days_names_the_remaining_days() -> None:
    due = purge_auto.batch_due(NOW - 4 * DAY, 7, TZ, NOW)  # fällig am 09.10.
    assert due["soon"] is True
    assert due["label"] == "wird ab 09.10. entfernt, in 3 Tagen"
    assert purge_auto.batch_due(NOW - 6 * DAY, 7, TZ, NOW)["label"].endswith("in 1 Tag")


def test_a_batch_past_its_age_waits_for_the_next_daily_run() -> None:
    due = purge_auto.batch_due(NOW - 8 * DAY, 7, TZ, NOW)
    assert due == {"label": "wird beim nächsten Lauf entfernt", "soon": True}


def test_the_banner_note_follows_the_automatic_setting(tmp_path) -> None:
    index = _index(tmp_path, enabled=True)
    index.mark_deleted("sensor.a", [1.0], deleted_at=NOW - 1 * DAY)
    note = purge_auto.note_for_entity(index, "sensor.a", TZ, NOW)
    assert note["enabled"] is True and note["due"] == "12.10." and note["overdue"] is False

    index.set_setting("purge_auto_enabled", "off")
    assert purge_auto.note_for_entity(index, "sensor.a", TZ, NOW)["enabled"] is False
    index.close()


def test_the_bell_notice_is_silent_while_the_automatic_cleanup_is_on_schedule(tmp_path) -> None:
    index = _index(tmp_path, enabled=True)
    index.mark_deleted("sensor.a", [1.0], deleted_at=NOW - 3 * DAY)  # Frist 7 Tage: noch nicht dran
    assert purge_auto.overdue(index, NOW) is False
    index.close()


def test_the_bell_notice_returns_when_the_automatic_run_left_marks_lying_around(tmp_path) -> None:
    index = _index(tmp_path, enabled=True)
    index.mark_deleted("sensor.a", [1.0], deleted_at=NOW - 10 * DAY)  # 7 Tage + 2 Schonfrist = 9 < 10
    assert purge_auto.overdue(index, NOW) is True
    index.set_setting("purge_auto_enabled", "off")
    assert purge_auto.overdue(index, NOW) is False  # ohne Automatik gilt die gewöhnliche Meldung
    index.close()


def _notice_ids(index: Index, tmp_path) -> set[str]:
    totals = {"removable_rows": 3, "entities_affected": 1, "archive_rows": 0}
    built = notices.build_notices(
        index, tmp_path / "index.sqlite", TZ, totals, None, 0, 0, 0, False,
        purge_rows=[{"entity_id": "sensor.a", "friendly_name": "A", "removable_rows": 3}],
    )
    return {n["id"] for n in built}


def test_build_notices_hides_the_purge_notice_with_automatic_cleanup_on_schedule(tmp_path) -> None:
    index = _index(tmp_path, enabled=True)
    index.mark_deleted("sensor.a", [1.0], deleted_at=datetime.now(TZ).timestamp() - 1 * DAY)
    assert "housekeeping.purge_available" not in _notice_ids(index, tmp_path)

    index.set_setting("purge_auto_enabled", "off")
    assert "housekeeping.purge_available" in _notice_ids(index, tmp_path)
    index.close()


def test_the_marked_panel_labels_each_batch_when_the_automatic_cleanup_is_on(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_purge_auto"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    index.set_setting("purge_auto_enabled", "on")
    index.set_setting("purge_min_age_days", "7")
    index.mark_deleted(entity_id, [10.0, 20.0], deleted_at=datetime.now(TZ).timestamp() - 5 * DAY)
    try:
        html = client.get(f"/entities/{entity_id}/marked").text
        assert "marked-due soon" in html and "wird ab" in html
        assert "Die automatische Bereinigung entfernt Markierungen nach 1 Woche." in html

        index.set_setting("purge_auto_enabled", "off")
        off = client.get(f"/entities/{entity_id}/marked").text
        assert "marked-due" not in off
        assert "Automatik einschalten" in off
    finally:
        index.set_setting("purge_auto_enabled", "off")


def test_the_banner_says_whether_the_automatic_cleanup_is_on(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_purge_auto_banner"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    index.mark_deleted(entity_id, [10.0], deleted_at=datetime.now(TZ).timestamp() - 1 * DAY)
    index.set_setting("purge_min_age_days", "7")
    try:
        index.set_setting("purge_auto_enabled", "off")
        assert "Automatische Bereinigung ist aus." in client.get(f"/entities/{entity_id}/cleanup").text
        index.set_setting("purge_auto_enabled", "on")
        assert "Die älteste Markierung wird ab dem" in client.get(f"/entities/{entity_id}/cleanup").text
    finally:
        index.set_setting("purge_auto_enabled", "off")
