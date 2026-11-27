"""Einstellungen: Felder, die nur mit ihrer Voraussetzung wirken, stehen auch nur dann im Formular.

* Backup-Zeitplan: Die Uhrzeit gilt nur für einen automatischen Lauf. Bei "Aus" bleibt sie als verstecktes Feld im Formular, weil
  /backup/schedule die Uhrzeit bei jedem Speichern schreibt und sonst den Vorgabewert eintrüge.
* Archivierung: Das Standard-Lebenszeichen gehört zum Wertänderungsfilter und steht nur bei aktivem Filter (wie im Formular einer
  Entität).
* Automatische Bereinigung: Das Mindestalter der Markierung liest nur der tägliche Lauf, und der nur bei eingeschalteter Automatik."""

from __future__ import annotations

import re

import pytest

from app.main import index

TIME = "07:45"


@pytest.fixture()
def restore_settings():
    """Stellt die Einstellungen der Testdatenbank wieder her. Was vorher nicht gesetzt war, bekommt den Wert, den die App dann liest."""
    from app.main import BACKUP_DEFAULT_TIME, BACKUP_DEFAULT_WEEKDAY  # noqa: PLC0415
    from app.storage.index import DEFAULT_VALUE_FILTER, DEFAULT_VALUE_FILTER_HEARTBEAT  # noqa: PLC0415

    from app.formatting import DEFAULT_PURGE_AUTO_ENABLED, DEFAULT_PURGE_MIN_AGE_DAYS  # noqa: PLC0415

    fallback = {
        "backup_schedule": "off", "backup_schedule_time": BACKUP_DEFAULT_TIME, "backup_schedule_weekday": str(BACKUP_DEFAULT_WEEKDAY),
        "default_value_filter": DEFAULT_VALUE_FILTER, "default_value_filter_heartbeat": DEFAULT_VALUE_FILTER_HEARTBEAT,
        "purge_auto_enabled": DEFAULT_PURGE_AUTO_ENABLED, "purge_min_age_days": DEFAULT_PURGE_MIN_AGE_DAYS,
    }
    before = {key: index.get_setting(key, None) for key in fallback}
    yield
    for key, value in before.items():
        index.set_setting(key, value if value is not None else fallback[key])


def _time_picker(html: str) -> bool:
    return 'id="backup-time-btn"' in html


def _time_inputs(html: str) -> list[str]:
    return re.findall(r'<input type="hidden" name="backup_schedule_time"[^>]*value="([^"]*)"', html)


@pytest.mark.parametrize(("schedule", "picker"), [("off", False), ("daily", True), ("weekly", True)])
def test_the_backup_time_picker_follows_the_schedule(client, restore_settings, schedule: str, picker: bool) -> None:
    html = client.post("/backup/schedule", data={"backup_schedule": schedule, "backup_schedule_time": TIME}).text
    assert _time_picker(html) is picker
    # Das Feld mit der gespeicherten Uhrzeit steht in jedem Fall im Formular.
    assert _time_inputs(html) == [TIME]


def test_switching_the_schedule_off_and_on_keeps_the_time(client, restore_settings) -> None:
    client.post("/backup/schedule", data={"backup_schedule": "daily", "backup_schedule_time": TIME})
    off = client.post("/backup/schedule", data={"backup_schedule": "off", "backup_schedule_time": TIME}).text
    # Der Browser schickt das versteckte Feld mit: die Uhrzeit bleibt erhalten, auch ohne sichtbares Auswahlfeld.
    assert index.get_setting("backup_schedule_time", "") == TIME
    again = client.post("/backup/schedule", data={"backup_schedule": "daily", "backup_schedule_time": _time_inputs(off)[0]}).text
    assert _time_inputs(again) == [TIME]


@pytest.mark.parametrize(("value_filter", "shown"), [("off", False), ("decimals", True)])
def test_the_default_heartbeat_follows_the_value_filter(client, restore_settings, value_filter: str, shown: bool) -> None:
    html = client.post("/settings/archivierung", data={"default_value_filter": value_filter}).text
    assert ('id="default-value-filter-heartbeat-btn"' in html) is shown
    # Der Filter selbst bleibt immer wählbar.
    assert 'id="default-value-filter-btn"' in html


def test_the_stored_default_heartbeat_survives_a_hidden_row(client, restore_settings) -> None:
    client.post("/settings/archivierung", data={"default_value_filter": "decimals"})
    client.post("/settings/archivierung", data={"default_value_filter_heartbeat": "60"})
    kept = index.get_setting("default_value_filter_heartbeat", "")
    client.post("/settings/archivierung", data={"default_value_filter": "off"})  # Zeile weg, Wert bleibt
    assert index.get_setting("default_value_filter_heartbeat", "") == kept


@pytest.mark.parametrize(("enabled", "shown"), [("off", False), ("on", True)])
def test_the_minimum_age_follows_the_automatic_purge(client, restore_settings, enabled: str, shown: bool) -> None:
    html = client.post("/settings/purge-auto", data={"purge_auto_enabled": enabled}).text
    assert ('id="purge-min-age-days-btn"' in html) is shown
    # Der Schalter selbst bleibt immer wählbar.
    assert 'id="purge-auto-enabled-btn"' in html


def test_the_stored_minimum_age_survives_a_hidden_row(client, restore_settings) -> None:
    client.post("/settings/purge-auto", data={"purge_auto_enabled": "on"})
    client.post("/settings/purge-auto", data={"purge_min_age_days": "90"})
    kept = index.get_setting("purge_min_age_days", "")
    client.post("/settings/purge-auto", data={"purge_auto_enabled": "off"})  # Zeile weg, Wert bleibt
    assert index.get_setting("purge_min_age_days", "") == kept
    # Wieder eingeschaltet, steht der gespeicherte Wert in der Zeile.
    html = client.post("/settings/purge-auto", data={"purge_auto_enabled": "on"}).text
    assert f'name="purge_min_age_days" id="purge-min-age-days-input" value="{kept}"' in html
