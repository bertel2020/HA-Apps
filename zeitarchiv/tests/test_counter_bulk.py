"""Sammelaktion „Fehlwerte markieren“ in Housekeeping → Zählerrückgänge und die Einordnung
der Rückgänge auf der Bereinigungsseite einer Entität (counter_bulk.py)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app import counter_bulk
from app.storage import cleanup, hotbuffer
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _seed(tmp_path):
    """Zwei Zähler: z1 fällt kurz auf 0 und kehrt zurück (Fehlwert), z2 fällt und bleibt niedrig."""
    index = Index(tmp_path / "index.sqlite")
    now = datetime.now(TZ)
    base = now.timestamp() - 6 * 3600
    for entity_id, values in (
        ("sensor.z1", [100.0, 101.0, 0.0, 101.5, 102.0]),
        ("sensor.z2", [500.0, 501.0, 1.0] + [1.0 + i * 0.1 for i in range(1, 14)]),
    ):
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
        for i, value in enumerate(values):
            hotbuffer.append(tmp_path, entity_id, base + i * 300, value, TZ)
    return index, now, base


def test_only_the_returning_decreases_are_marked_and_the_snapshot_follows(tmp_path) -> None:
    index, now, base = _seed(tmp_path)
    index.set_counter_decrease_snapshot(cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now))

    outcome = counter_bulk.mark_returning(tmp_path, index, TZ, now=now)

    assert outcome["entities"] == {"sensor.z1": 1}
    assert outcome["values"] == 1
    # Genau der Fehlwert ist markiert, der Wert davor und danach nicht.
    assert index.get_deleted_counts("sensor.z1", base, base + 3600) == {base + 2 * 300: 1}
    assert index.get_deleted_counts("sensor.z2", base, base + 6 * 3600) == {}
    # Der Schnappschuss kennt nur noch den Zähler, der niedrig bleibt.
    rows = index.get_counter_decrease_snapshot()["rows"]
    assert [row["entity_id"] for row in rows] == ["sensor.z2"]
    index.close()


def test_the_bulk_marking_can_be_undone_as_the_same_batch(tmp_path) -> None:
    index, now, base = _seed(tmp_path)
    index.set_counter_decrease_snapshot(cleanup.scan_counter_decreases(tmp_path, index, TZ, now=now))
    outcome = counter_bulk.mark_returning(tmp_path, index, TZ, now=now)

    restored = counter_bulk.undo_batch(tmp_path, index, TZ, outcome["batch_at"], list(outcome["entities"]))

    assert restored == 1
    assert index.get_deleted_counts("sensor.z1", base, base + 3600) == {}
    assert {row["entity_id"] for row in index.get_counter_decrease_snapshot()["rows"]} == {"sensor.z1", "sensor.z2"}
    actions = [row["action"] for row in index.list_entity_actions_for_entity("sensor.z1", limit=10)]
    assert "mark" in actions and "undo" in actions
    index.close()


def test_without_a_snapshot_nothing_is_marked(tmp_path) -> None:
    index, now, _ = _seed(tmp_path)
    assert counter_bulk.mark_returning(tmp_path, index, TZ, now=now)["entities"] == {}
    index.close()


def test_the_cleanup_page_gets_a_verdict_for_each_flagged_decrease(tmp_path) -> None:
    index, now, base = _seed(tmp_path)
    label = "Zählerrückgang"
    rows = [
        {"ts": base + 2 * 300, "flags": [{"label": label, "reason": "x"}]},
        {"ts": base + 1 * 300, "flags": []},
    ]
    assert counter_bulk.verdicts_for_rows(tmp_path, index, TZ, "sensor.z1", rows, now=now) == {base + 2 * 300: True}
    assert counter_bulk.verdicts_for_rows(tmp_path, index, TZ, "sensor.z2", [rows[0]], now=now) == {base + 2 * 300: False}
    assert counter_bulk.verdicts_for_rows(tmp_path, index, TZ, "sensor.z1", [rows[1]], now=now) == {}
    index.close()


def _seed_live(index, data_dir, now):
    # Nur 40 Minuten zurück: die Ansicht „Tag“ beginnt um Mitternacht, mehr Abstand ließe den
    # Test in den ersten Stunden des Tages ins Leere laufen.
    base = now.timestamp() - 2400
    index.get_or_create_entity("sensor.pytest_bulk", "sensor", "total_increasing", "kWh")
    for i, value in enumerate([100.0, 101.0, 0.0, 101.5, 102.0]):
        hotbuffer.append(data_dir, "sensor.pytest_bulk", base + i * 300, value, TZ)
    return base


def test_the_routes_mark_show_the_undo_hint_and_take_it_back(client) -> None:
    from app.main import DATA_DIR, index

    now = datetime.now(TZ)
    base = _seed_live(index, DATA_DIR, now)
    try:
        index.set_counter_decrease_snapshot(cleanup.scan_counter_decreases(DATA_DIR, index, TZ, now=now))
        page = client.get("/housekeeping").text
        assert "housekeeping/counter-decreases/mark-returning" in page
        assert "1 Fehlwert" in page

        marked = client.post("/housekeeping/counter-decreases/mark-returning")
        assert marked.status_code == 200
        assert "housekeeping/counter-decreases/undo" in marked.text
        assert index.get_deleted_counts("sensor.pytest_bulk", base, base + 3600) == {base + 2 * 300: 1}
        assert "counter-bulk" not in marked.text  # nichts Offenes mehr

        vals = re.search(r"hx-vals='([^']+)'", marked.text).group(1).replace("&#34;", '"').replace("&quot;", '"')
        undone = client.post("/housekeeping/counter-decreases/undo", data=json.loads(vals))
        assert undone.status_code == 200
        assert index.get_deleted_counts("sensor.pytest_bulk", base, base + 3600) == {}
    finally:
        index.undo_all_deleted("sensor.pytest_bulk")
        index.set_counter_decrease_snapshot([])


def test_the_cleanup_page_shows_the_verdict_in_the_badge(client) -> None:
    from app.main import DATA_DIR, index

    now = datetime.now(TZ)
    _seed_live(index, DATA_DIR, now)
    try:
        html = client.get("/entities/sensor.pytest_bulk/rows?filter=counter_decreases&range=day").text
        assert "Zählerrückgang" in html
        assert "kehrt zurück" in html
    finally:
        index.set_counter_decrease_snapshot([])
