"""Reiter „Markiert“ im Bearbeitungsbereich: alle markierten Werte einer Entität,
nach Charge gruppiert, und ihr Zurücknehmen (einzeln, je Charge, alle)."""

from __future__ import annotations

from app.storage.index import Index


def _entity_with_batches(index: Index, entity_id: str) -> tuple[float, float]:
    """Zwei Chargen: eine ältere mit 3 Markierungen (davon ein Duplikat-Zeitstempel),
    eine jüngere mit 2."""
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    older, newer = 1_700_000_000.0, 1_700_000_500.0
    index.mark_deleted(entity_id, [10.0, 20.0, 20.0], deleted_at=older)
    index.mark_deleted(entity_id, [30.0, 40.0], deleted_at=newer)
    return older, newer


def test_undo_by_ids_takes_back_exactly_the_chosen_markings(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    _entity_with_batches(index, "sensor.a")
    rows = index.list_deleted_points_for_entity("sensor.a")["rows"]
    # Zwei Markierungen mit demselben Zeitstempel 20.0: gewählt wird nur EINE.
    duplicate_ids = [row["id"] for row in rows if row["ts"] == 20.0]
    assert len(duplicate_ids) == 2

    assert index.undo_deleted_ids("sensor.a", [duplicate_ids[0]]) == 1
    assert index.get_entity("sensor.a")["deleted_count"] == 4
    assert index.get_deleted_counts("sensor.a", 0.0, 100.0)[20.0] == 1
    index.close()


def test_undo_batch_only_touches_that_batch_not_just_the_latest(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    older, _newer = _entity_with_batches(index, "sensor.a")

    assert index.undo_deleted_batch("sensor.a", older) == 3
    assert index.get_entity("sensor.a")["deleted_count"] == 2  # die jüngere Charge bleibt
    assert sorted(index.get_deleted_counts("sensor.a", 0.0, 100.0)) == [30.0, 40.0]
    index.close()


def test_undo_all_empties_the_entity_and_leaves_others_alone(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    _entity_with_batches(index, "sensor.a")
    _entity_with_batches(index, "sensor.b")

    assert index.undo_all_deleted("sensor.a") == 5
    assert index.get_entity("sensor.a")["deleted_count"] == 0
    assert index.get_entity("sensor.b")["deleted_count"] == 5
    index.close()


def test_count_deleted_by_batch_counts_only_the_asked_batches(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    older, newer = _entity_with_batches(index, "sensor.a")
    assert index.count_deleted_by_batch("sensor.a", [older]) == {older: 3}
    assert index.count_deleted_by_batch("sensor.a", [older, newer]) == {older: 3, newer: 2}
    assert index.count_deleted_by_batch("sensor.a", []) == {}
    index.close()


def test_the_marked_panel_groups_by_batch_and_offers_all_three_ways_to_undo(client) -> None:
    from app.main import index

    older, newer = _entity_with_batches(index, "sensor.pytest_marked_panel")
    html = client.get("/entities/sensor.pytest_marked_panel/marked").text

    assert html.count('class="marked-group"') == 2  # zwei Chargen
    assert "3 Werte" in html and "2 Werte" in html
    assert '"mode": "selection"' in html
    assert '"mode": "batch"' in html
    assert '"mode": "all"' in html
    # Der Reiter-Zähler und der Banner werden mit aktualisiert (hx-swap-oob).
    assert 'id="marked-tab-count" hx-swap-oob="true">5<' in html
    assert 'id="pending-purge-banner" hx-swap-oob="true"' in html
    assert "5 Werte sind zur Löschung markiert" in html
    # Der Banner führt in diesen Reiter (nicht nach Housekeeping): dort lässt sich alles tun.
    assert 'href="/entities/sensor.pytest_marked_panel/cleanup?tab=marked"' in html


def test_confirm_names_the_number_and_changes_nothing(client) -> None:
    from app.main import index

    older, _newer = _entity_with_batches(index, "sensor.pytest_marked_confirm")
    html = client.post(
        "/entities/sensor.pytest_marked_confirm/marked/confirm",
        data={"mode": "batch", "batch": str(older), "page": "1"},
    ).text

    assert "3 Werte" in html
    assert index.get_entity("sensor.pytest_marked_confirm")["deleted_count"] == 5  # unverändert


def test_undo_route_takes_back_a_batch_and_refreshes_the_banner(client) -> None:
    from app.main import index

    older, _newer = _entity_with_batches(index, "sensor.pytest_marked_undo")
    html = client.post(
        "/entities/sensor.pytest_marked_undo/marked/undo",
        data={"mode": "batch", "batch": str(older), "page": "1"},
    ).text

    assert index.get_entity("sensor.pytest_marked_undo")["deleted_count"] == 2
    assert "2 Werte sind zur Löschung markiert" in html


def test_undo_all_leaves_an_empty_state_and_an_empty_banner(client) -> None:
    from app.main import index

    _entity_with_batches(index, "sensor.pytest_marked_all")
    html = client.post(
        "/entities/sensor.pytest_marked_all/marked/undo", data={"mode": "all", "page": "1"},
    ).text

    assert index.get_entity("sensor.pytest_marked_all")["deleted_count"] == 0
    assert "Keine zur Löschung markierten Werte." in html
    assert "entity-purge-banner" not in html


def test_undo_route_rejects_an_unknown_mode(client) -> None:
    from app.main import index

    _entity_with_batches(index, "sensor.pytest_marked_bad")
    response = client.post("/entities/sensor.pytest_marked_bad/marked/undo", data={"mode": "weg"})
    assert response.status_code == 400
    assert index.get_entity("sensor.pytest_marked_bad")["deleted_count"] == 5


# -- Einklappbare Chargen, Blättern -----------------------------------------


def test_batches_are_collapsible_and_only_the_first_starts_open(client) -> None:
    from app.main import index

    _entity_with_batches(index, "sensor.pytest_marked_collapse")
    html = client.get("/entities/sensor.pytest_marked_collapse/marked").text

    assert html.count("x-data=\"{ open: true }\"") == 1
    assert html.count("x-data=\"{ open: false }\"") == 1
    assert html.count('x-show="open"') == 5  # jede Wertzeile hängt an ihrer Charge
    assert 'class="marked-toggle"' in html


def test_the_pager_uses_the_shared_markup_with_thousands_separators(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_marked_pager", "sensor", "measurement", "°C")
    index.mark_deleted("sensor.pytest_marked_pager", [float(i) for i in range(1, 1235)], deleted_at=1_700_000_000.0)
    html = client.get("/entities/sensor.pytest_marked_pager/marked?page=2&page_size=100").text

    assert "101&ndash;200 von 1.234" in html
    assert "/ 13" in html
    for symbol in ("«", "‹", "›", "»"):
        assert symbol in html
    assert 'name="page_size"' in html and 'value="100" selected' in html
    # Das Blättern steht außerhalb des Formulars, sonst ginge sein Seitenfeld bei jedem
    # Zurücknehmen mit.
    assert html.index("</form>") < html.index('class="pager marked-pager"')


# -- Endgültiges Entfernen genau dieser Entität -----------------------------


def test_purge_scoped_to_one_entity_leaves_the_others_marks(tmp_path) -> None:
    import time
    from zoneinfo import ZoneInfo

    from app.storage import cleanup, hotbuffer

    tz = ZoneInfo("Europe/Berlin")
    index = Index(tmp_path / "index.sqlite")
    now_ts = time.time()
    for entity_id in ("sensor.a", "sensor.b"):
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        hotbuffer.append(tmp_path, entity_id, now_ts, 1.0, tz)
        index.record_write(entity_id, now_ts)
        cleanup.soft_delete(index, entity_id, [now_ts])

    preview = cleanup.preview_purge(tmp_path, index, tz, entity_ids=["sensor.a"])
    assert preview["totals"]["removable_rows"] == 1
    assert [row["entity_id"] for row in preview["rows"]] == ["sensor.a"]

    assert cleanup.purge_hot_buffer(tmp_path, index, tz, entity_ids=["sensor.a"]) == 1
    assert index.get_entity("sensor.a")["deleted_count"] == 0
    assert index.get_entity("sensor.b")["deleted_count"] == 1  # unangetastet
    index.close()


def test_purge_preview_route_shows_fresh_numbers_and_a_danger_button(client) -> None:
    import time

    from app.main import DATA_DIR, TZ, index
    from app.storage import hotbuffer

    entity_id = "sensor.pytest_marked_purge_preview"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    now_ts = time.time()
    hotbuffer.append(DATA_DIR, entity_id, now_ts, 1.0, TZ)
    index.record_write(entity_id, now_ts)
    index.mark_deleted(entity_id, [now_ts])

    html = client.post(f"/entities/{entity_id}/marked/purge-preview").text
    assert "Das lässt sich nicht rückgängig machen" in html
    assert "<b>1</b><span>Werte werden entfernt</span>" in html
    assert "btn-danger" in html
    assert "1 Werte endgültig entfernen" in html
    assert index.get_entity(entity_id)["deleted_count"] == 1  # nur eine Vorschau


def test_purge_route_removes_the_marks_in_the_background_and_reports(client) -> None:
    import time

    from app.main import DATA_DIR, TZ, index
    from app.storage import hotbuffer

    entity_id = "sensor.pytest_marked_purge_run"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    now_ts = time.time()
    hotbuffer.append(DATA_DIR, entity_id, now_ts, 1.0, TZ)
    index.record_write(entity_id, now_ts)
    index.mark_deleted(entity_id, [now_ts])

    started = client.post(f"/entities/{entity_id}/marked/purge")
    assert started.status_code == 200

    html = started.text
    for _ in range(50):
        if "hx-trigger=\"every 500ms\"" not in html:
            break
        time.sleep(0.1)
        html = client.get(f"/entities/{entity_id}/marked/purge/progress").text

    assert index.get_entity(entity_id)["deleted_count"] == 0
    assert "1 Zeile physisch entfernt." in html
    assert "Keine zur Löschung markierten Werte." in html
