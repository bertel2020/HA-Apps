"""Reiter „Verlauf“: das Protokoll einer Entität mit den Einzelheiten, die es zum
Nachvollziehen braucht (alter/neuer Wert, Art des Zurücknehmens)."""

from __future__ import annotations

import json
import time
from zoneinfo import ZoneInfo

from app import entity_history
from app.formatting import format_value
from app.storage import cleanup
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _log(index: Index, entity_id: str, action: str, count: int = 1, detail: dict | None = None, **kw) -> None:
    now = time.time()
    index.log_entity_action(
        entity_id, action, kw.get("trigger", "manual"), now, now, kw.get("status", "success"),
        rows_affected=count, detail=json.dumps(detail) if detail else None, error=kw.get("error"),
    )


def test_marking_and_undoing_are_logged_with_their_counts(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.a", "sensor", "measurement", "kWh")
    cleanup.soft_delete(index, "sensor.a", [10.0, 20.0, 30.0])
    assert cleanup.undo_last_delete(index, "sensor.a") == 3

    actions = [(row["action"], row["rows_affected"]) for row in index.list_entity_actions_for_entity("sensor.a")]
    assert sorted(actions) == [("mark", 3), ("undo", 3)]
    index.close()


def test_nothing_is_logged_when_nothing_was_marked_or_undone(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.a", "sensor", "measurement", "kWh")
    cleanup.soft_delete(index, "sensor.a", [])
    assert cleanup.undo_last_delete(index, "sensor.a") == 0
    assert index.list_entity_actions_for_entity("sensor.a") == []
    index.close()


def test_the_history_of_one_entity_excludes_the_others_and_filters_by_action(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    _log(index, "sensor.a", "mark", 2)
    _log(index, "sensor.a", "correct")
    _log(index, "sensor.b", "mark", 9)

    assert len(index.list_entity_actions_for_entity("sensor.a")) == 2
    only_marks = index.list_entity_actions_for_entity("sensor.a", actions=("mark", "undo"))
    assert [row["rows_affected"] for row in only_marks] == [2]
    assert index.list_entity_actions_for_entity("sensor.a", since_ts=time.time() + 60) == []
    index.close()


def test_a_correction_shows_old_and_new_value_and_old_entries_still_render(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    ts = 1_759_492_800.0
    _log(index, "sensor.a", "correct", detail={"ts": ts, "old": 142.63, "new": 14263.0})
    _log(index, "sensor.a", "correct")  # Eintrag aus der Zeit vor den Einzelheiten
    rows = index.list_entity_actions_for_entity("sensor.a")

    events = entity_history.build_days(rows, TZ, None, "kWh")[0]["events"]
    detailed = next(e for e in events if e["old"] is not None)
    assert detailed["old"] == f"{format_value(142.63)} kWh" and detailed["new"] == f"{format_value(14263.0)} kWh"
    assert "03.10." in detailed["title"]
    plain = next(e for e in events if e["old"] is None)
    assert plain["title"] == "1 Wert korrigiert"
    index.close()


def test_undo_events_say_what_kind_of_undo_it_was_and_link_to_marked(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    _log(index, "sensor.a", "undo", 4, detail={"mode": "all"})
    event = entity_history.build_days(index.list_entity_actions_for_entity("sensor.a"), TZ, None, None)[0]["events"][0]
    assert event["title"] == "4 Werte wieder aktiv"
    assert event["sub"] == "Alle Markierungen zurückgenommen"
    assert event["link_marked"] is True
    index.close()


def test_a_failed_run_shows_the_error_instead_of_the_detail(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    _log(index, "sensor.a", "compact", status="failed", error="Platte voll")
    event = entity_history.build_days(index.list_entity_actions_for_entity("sensor.a"), TZ, None, None)[0]["events"][0]
    assert event["failed"] is True and event["sub"] == "Platte voll"
    index.close()


def test_the_history_panel_groups_by_day_and_filters(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_history", "sensor", "measurement", "kWh")
    _log(index, "sensor.pytest_history", "mark", 3)
    _log(index, "sensor.pytest_history", "correct", detail={"ts": 1_759_492_800.0, "old": 1.0, "new": 2.0})

    html = client.get("/entities/sensor.pytest_history/history").text
    assert "Heute" in html
    assert "3 Werte zum Entfernen markiert" in html
    assert "history-old" in html

    marked_only = client.get("/entities/sensor.pytest_history/history?filter=marked").text
    assert "3 Werte zum Entfernen markiert" in marked_only
    assert "history-old" not in marked_only


def test_the_history_panel_says_so_when_there_is_nothing(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_history_empty", "sensor", "measurement", "kWh")
    assert "Keine Einträge" in client.get("/entities/sensor.pytest_history_empty/history").text


def test_marking_through_the_route_and_undoing_a_batch_both_land_in_the_history(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_history_flow"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    client.post(f"/entities/{entity_id}/rows/delete", data={"ts": ["10.0", "20.0"]})
    batch = index.list_deleted_points_for_entity(entity_id)["rows"][0]["deleted_at"]
    client.post(f"/entities/{entity_id}/marked/undo", data={"mode": "batch", "batch": str(batch)})

    html = client.get(f"/entities/{entity_id}/history").text
    assert "2 Werte zum Entfernen markiert" in html
    assert "2 Werte wieder aktiv" in html
    assert "Eine Charge zurückgenommen" in html


def test_the_cleanup_page_has_a_history_tab(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_history_tab", "sensor", "measurement", "kWh")
    html = client.get("/entities/sensor.pytest_history_tab/cleanup").text
    assert 'id="history-panel"' in html and "load-history" in html
