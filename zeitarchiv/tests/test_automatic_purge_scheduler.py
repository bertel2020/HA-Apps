"""Automatische Bereinigung (Housekeeping → Speicherplatz, background.py) —
der Wartungsplaner-Teil. Die eigentliche Alters-Filterung deckt bereits
test_cleanup.py (purge_hot_buffer/purge_archived_months) und test_index.py
(get_deleted_counts_for_entity/remove_deleted_points) ab; hier geht es nur um
das, was NUR _run_automatic_purge_if_due beisteuert: Schalter, Mindestalter
und Protokollierung in Aktivität."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

try:
    from app.background import BackgroundDependencies, BackgroundService
    from app.storage import hotbuffer
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index

    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

# background.py importiert cleanup.py, das pyarrow für den Archiv-Purge
# braucht — derselbe Grund für den optionalen Import wie in test_cleanup.py.
pytestmark = pytest.mark.skipif(not _DEPS_AVAILABLE, reason="pyarrow nicht installiert")

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h) -> float:
    return datetime(y, m, d, h, tzinfo=TZ).timestamp()


@pytest.fixture()
def dienst(tmp_path):
    index = Index(tmp_path / "index.sqlite")
    service = BackgroundService(BackgroundDependencies(
        data_dir=tmp_path, tz=TZ, index=index, coordinator=StorageCoordinator(),
        base_dir=tmp_path, demo_mode_active=False,
        backups_dir=tmp_path / "backups", symcon_import_dir=tmp_path / "symcon",
        csv_import_dir=tmp_path / "csv", backup_default_time="03:00", backup_default_weekday=6,
        retention_default_time="04:00", retention_default_weekday=6,
        count_stale_entities=lambda: 0,
    ))
    try:
        yield service, tmp_path
    finally:
        index.close()


def _mark_deleted_hot_row(service, tmp_path, entity_id, now, *, age_days):
    ts = _ts(2024, 8, 10, 8)
    hotbuffer.append(tmp_path, entity_id, ts, 100.0, TZ)
    service.index.record_write(entity_id, ts)
    service.index.mark_deleted(entity_id, [ts], deleted_at=now.timestamp() - age_days * 86400)
    return ts


def test_disabled_by_default_leaves_marked_rows_untouched(dienst) -> None:
    service, tmp_path = dienst
    service.index.get_or_create_entity("sensor.a", "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    _mark_deleted_hot_row(service, tmp_path, "sensor.a", now, age_days=90)

    service._run_automatic_purge_if_due(now)

    assert service.index.get_deleted_points_count() == 1
    assert service.index.list_entity_actions() == []


def test_enabled_purges_only_marks_past_the_configured_min_age(dienst) -> None:
    service, tmp_path = dienst
    service.index.set_setting("purge_auto_enabled", "on")
    service.index.set_setting("purge_min_age_days", "30")
    service.index.get_or_create_entity("sensor.a", "sensor", "measurement", "W")
    service.index.get_or_create_entity("sensor.b", "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    old_ts = _mark_deleted_hot_row(service, tmp_path, "sensor.a", now, age_days=40)
    fresh_ts = _mark_deleted_hot_row(service, tmp_path, "sensor.b", now, age_days=5)

    service._run_automatic_purge_if_due(now)

    remaining_a = hotbuffer.read_rows(hotbuffer.hot_path(tmp_path, "sensor.a", now.timestamp(), TZ))
    assert old_ts not in [ts for ts, _ in remaining_a]
    remaining_b = hotbuffer.read_rows(hotbuffer.hot_path(tmp_path, "sensor.b", now.timestamp(), TZ))
    assert fresh_ts in [ts for ts, _ in remaining_b]
    assert service.index.get_deleted_points_count() == 1  # nur sensor.b bleibt markiert

    actions = service.index.list_entity_actions()
    assert len(actions) == 1
    assert (actions[0]["action"], actions[0]["trigger"], actions[0]["status"]) == ("purge", "automatic", "success")
    assert actions[0]["rows_affected"] == 1
    assert '"min_age_days": 30' in actions[0]["detail"]


def test_enabled_but_nothing_old_enough_logs_nothing(dienst) -> None:
    service, tmp_path = dienst
    service.index.set_setting("purge_auto_enabled", "on")
    service.index.get_or_create_entity("sensor.a", "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    _mark_deleted_hot_row(service, tmp_path, "sensor.a", now, age_days=1)

    service._run_automatic_purge_if_due(now)

    assert service.index.get_deleted_points_count() == 1
    assert service.index.list_entity_actions() == []


def test_a_second_call_within_a_day_is_a_noop_even_when_something_just_aged_in(dienst) -> None:
    """Höchstens einmal täglich, wie die Verdichten-Automatik — ein zweiter
    Tick Sekunden später darf nicht erneut die globale Sperre ziehen."""
    service, tmp_path = dienst
    service.index.set_setting("purge_auto_enabled", "on")
    service.index.get_or_create_entity("sensor.a", "sensor", "measurement", "W")
    now = datetime(2024, 8, 15, 12, tzinfo=TZ)
    service._run_automatic_purge_if_due(now)

    _mark_deleted_hot_row(service, tmp_path, "sensor.a", now, age_days=90)
    service._run_automatic_purge_if_due(datetime(2024, 8, 15, 12, 0, 5, tzinfo=TZ))

    assert service.index.get_deleted_points_count() == 1
    assert service.index.list_entity_actions() == []
