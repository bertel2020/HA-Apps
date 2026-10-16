"""Selektiver Restore (storage/restore_selective.py): Entitäten und Einstellungen
einzeln aus einem Backup zurückholen, mit Rollback und Rückgängig."""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import _paths  # noqa: F401
from app.storage import backup, reconcile, restore_selective as rs
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
TS = 1_704_067_200.0  # 01.01.2024 00:00 UTC


def _entity(index: Index, data_dir: Path, entity_id: str, values: list[float]) -> Path:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W", entity_id)
    directory = data_dir / "archive" / entity_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "2024-01.parquet"
    pq.write_table(pa.table({"ts": [TS + 60 * i for i in range(len(values))], "value": values}), path)
    reconcile.audit_storage_metadata(data_dir, index, TZ, entity_ids=[entity_id], repair=True)
    return path


def _backup(data_dir: Path, tmp_path: Path, name: str = "backup.zip") -> Path:
    dest = tmp_path / "backups" / name
    backup.create_backup(data_dir, dest, consistent_sqlite=True)
    return dest


def _chart(index: Index, name: str, entities: list[str], chart_type: str = "line") -> int:
    return index.create_saved_chart(name, entities, "day", False, chart_type=chart_type)


def _table(index: Index, name: str, entity_id: str) -> int:
    return index.create_saved_table(
        name,
        [{"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False}],
        [{"label": "Zeile", "row_type": "entity", "entity_ids": [entity_id], "formula": "", "bold": False}],
    )


@pytest.fixture()
def live(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    yield tmp_path, index, StorageCoordinator()
    index.close()


# --------------------------------------------------------------------------- Entitäten


def test_restore_entities_swaps_files_updates_stats_and_can_be_undone(live) -> None:
    data_dir, index, coordinator = live
    path = _entity(index, data_dir, "sensor.a", [1.0, 2.0, 3.0])
    original_bytes = path.read_bytes()
    zip_path = _backup(data_dir, data_dir)
    # Danach ändert sich der Bestand: weniger Zeilen, dazu ein Löschvermerk.
    pq.write_table(pa.table({"ts": [TS], "value": [9.0]}), path)
    index.mark_deleted("sensor.a", [TS])
    reconcile.audit_storage_metadata(data_dir, index, TZ, entity_ids=["sensor.a"], repair=True)
    assert index.get_entity("sensor.a")["row_count"] == 1

    with rs.BackupSource(zip_path) as source:
        diff = {r["entity_id"]: r for r in rs.list_entity_diff(source, index, TZ)}
        assert diff["sensor.a"]["status"] == "diff"
        result = rs.restore_entities(data_dir, index, coordinator, source, ["sensor.a"], TZ, source_label="backup.zip")

    assert path.read_bytes() == original_bytes
    entity = index.get_entity("sensor.a")
    assert entity["row_count"] == 3
    assert entity["deleted_count"] == 0
    rollback = data_dir / result["rollback"]
    assert (rollback / "archive" / "sensor.a" / "2024-01.parquet").is_file()
    assert rs.list_rollbacks(data_dir)[0]["name"] == result["rollback"]

    rs.undo(data_dir, index, coordinator, result["rollback"], TZ)
    assert pq.read_table(path).num_rows == 1
    assert index.get_entity("sensor.a")["row_count"] == 1
    assert index.get_entity("sensor.a")["deleted_count"] == 1
    assert not rollback.exists()


def test_restore_entities_recreates_a_missing_entity_and_undo_removes_it(live) -> None:
    data_dir, index, coordinator = live
    _entity(index, data_dir, "sensor.gone", [1.0, 2.0])
    zip_path = _backup(data_dir, data_dir)
    index.delete_entity("sensor.gone")
    import shutil
    shutil.rmtree(data_dir / "archive" / "sensor.gone")

    with rs.BackupSource(zip_path) as source:
        assert {r["entity_id"]: r["status"] for r in rs.list_entity_diff(source, index, TZ)} == {"sensor.gone": "new"}
        result = rs.restore_entities(data_dir, index, coordinator, source, ["sensor.gone"], TZ, source_label="b")
    assert result["created"] == ["sensor.gone"]
    assert index.get_entity("sensor.gone")["row_count"] == 2
    assert (data_dir / "archive" / "sensor.gone" / "2024-01.parquet").is_file()

    rs.undo(data_dir, index, coordinator, result["rollback"], TZ)
    assert index.get_entity("sensor.gone") is None
    assert not (data_dir / "archive" / "sensor.gone").exists()


def test_restore_entities_keeps_local_entity_settings(live) -> None:
    data_dir, index, coordinator = live
    _entity(index, data_dir, "sensor.keep", [1.0])
    zip_path = _backup(data_dir, data_dir)
    with index._lock, index._conn:
        index._conn.execute("UPDATE entities SET custom_name = 'Mein Name', retention = '90d' WHERE entity_id = 'sensor.keep'")
    with rs.BackupSource(zip_path) as source:
        rs.restore_entities(data_dir, index, coordinator, source, ["sensor.keep"], TZ, source_label="b")
    entity = index.get_entity("sensor.keep")
    assert entity["custom_name"] == "Mein Name"
    assert entity["retention"] == "90d"


def test_restore_entities_rejects_wrong_checksum_and_leaves_state_untouched(live) -> None:
    data_dir, index, coordinator = live
    path = _entity(index, data_dir, "sensor.a", [1.0, 2.0])
    zip_path = _backup(data_dir, data_dir)
    before = path.read_bytes()
    with rs.BackupSource(zip_path) as source:
        source._sums["archive/sensor.a/2024-01.parquet"]["sha256"] = "0" * 64
        with pytest.raises(ValueError):
            rs.restore_entities(data_dir, index, coordinator, source, ["sensor.a"], TZ, source_label="b")
    assert path.read_bytes() == before
    assert rs.list_rollbacks(data_dir) == []
    assert not list(data_dir.glob(".zeitarchiv-selective-staging-*"))


def test_restore_entities_rejects_unknown_entity(live) -> None:
    data_dir, index, coordinator = live
    _entity(index, data_dir, "sensor.a", [1.0])
    zip_path = _backup(data_dir, data_dir)
    with rs.BackupSource(zip_path) as source:
        with pytest.raises(ValueError):
            rs.restore_entities(data_dir, index, coordinator, source, ["sensor.other"], TZ, source_label="b")
        with pytest.raises(ValueError):
            rs.restore_entities(data_dir, index, coordinator, source, ["../etc"], TZ, source_label="b")


def test_selective_rollback_is_not_a_full_restore_rollback(live) -> None:
    """apply_pending_restore() darf ein Teil-Rollback nie als vollständigen Stand einspielen."""
    data_dir, index, coordinator = live
    _entity(index, data_dir, "sensor.a", [1.0])
    zip_path = _backup(data_dir, data_dir)
    with rs.BackupSource(zip_path) as source:
        result = rs.restore_entities(data_dir, index, coordinator, source, ["sensor.a"], TZ, source_label="b")
    assert not backup.RESTORE_ROLLBACK_RE.fullmatch(result["rollback"])
    assert backup.list_restore_rollbacks(data_dir) == []


def test_rollbacks_are_pruned_to_the_newest_three(live) -> None:
    data_dir, index, coordinator = live
    _entity(index, data_dir, "sensor.a", [1.0])
    zip_path = _backup(data_dir, data_dir)
    names = []
    with rs.BackupSource(zip_path) as source:
        for _ in range(5):
            names.append(rs.restore_entities(data_dir, index, coordinator, source, ["sensor.a"], TZ, source_label="b")["rollback"])
    assert [r["name"] for r in rs.list_rollbacks(data_dir)] == sorted(names, reverse=True)[:3]


# --------------------------------------------------------------------------- Einstellungen


def _settings_fixture(index: Index, data_dir: Path) -> dict:
    _entity(index, data_dir, "sensor.a", [1.0])
    ids = {
        "chart_a": _chart(index, "Chart A", ["sensor.a"]),
        "chart_b": _chart(index, "Chart B", ["sensor.a"]),
        "table": _table(index, "Tabelle 1", "sensor.a"),
        "dash": index.create_dashboard("Energie"),
    }
    index.pin_item_to_dashboard(ids["dash"], "chart", ids["chart_a"])
    index.pin_item_to_dashboard(ids["dash"], "chart", ids["chart_b"])
    index.pin_item_to_dashboard(ids["dash"], "table", ids["table"])
    index.pin_entity_to_dashboard(ids["dash"], "sensor.a")
    return ids


def _status(overview: dict, kind: str) -> dict[str, str]:
    return {i["name"]: i["st"] for i in overview[kind]}


def test_settings_overview_compares_by_name(live) -> None:
    data_dir, index, _ = live
    ids = _settings_fixture(index, data_dir)
    zip_path = _backup(data_dir, data_dir)
    index.delete_saved_chart(ids["chart_b"])
    index.update_saved_chart(ids["chart_a"], "Chart A", ["sensor.a"], "day", False, chart_type="bar")
    index.delete_dashboard(ids["dash"])

    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
    assert _status(overview, "charts") == {"Chart A": "diff", "Chart B": "new"}
    assert _status(overview, "tables") == {"Tabelle 1": "same"}
    assert _status(overview, "dashboards") == {"Übersicht": "same", "Energie": "new"}  # „Übersicht“ legt der Index selbst an
    dash = next(d for d in overview["dashboards"] if d["name"] == "Energie")
    assert len(dash["needs"]) == 3
    assert dash["dep"] == {"same": 1, "new": 1, "diff": 1}


def test_dashboard_pulls_in_its_dependencies_and_remaps_pins(live) -> None:
    data_dir, index, _ = live
    ids = _settings_fixture(index, data_dir)
    zip_path = _backup(data_dir, data_dir)
    index.delete_saved_chart(ids["chart_b"])
    index.update_saved_chart(ids["chart_a"], "Chart A", ["sensor.a"], "day", False, chart_type="bar")
    index.delete_dashboard(ids["dash"])

    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        dash_key = next(d["key"] for d in overview["dashboards"] if d["name"] == "Energie")
        plan = rs.make_plan(overview, {dash_key}, {})
        assert plan["total"] == 3  # Dashboard + Chart A (weicht ab, Kopie) + Chart B
        assert {i["name"] for i in plan["auto_charts"]} == {"Chart A", "Chart B"}
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")

    names = {c["name"]: c["id"] for c in index.list_saved_charts()}
    assert set(names) == {"Chart A", "Chart B", "Chart A (Kopie)"}
    dash = next(d for d in index.list_dashboards() if d["name"] == "Energie")
    pins = index.list_dashboard_pins(dash["id"])
    chart_pins = {p["item_id"] for p in pins if p["item_type"] == "chart"}
    assert chart_pins == {names["Chart A (Kopie)"], names["Chart B"]}
    assert any(p["item_type"] == "table" and p["item_id"] == ids["table"] for p in pins)
    entity_pin = next(p for p in pins if p["item_type"] == "entity")
    assert entity_pin["item_id"] == entity_pin["id"]
    assert result["skipped_pins"] == 0

    # Original unverändert, Rückgängig entfernt alles Neue wieder.
    assert index.get_saved_chart(ids["chart_a"])["chart_type"] == "bar"
    rs.undo(data_dir, index, StorageCoordinator(), result["rollback"], TZ)
    assert {c["name"] for c in index.list_saved_charts()} == {"Chart A"}
    assert all(d["name"] != "Energie" for d in index.list_dashboards())
    assert not (data_dir / result["rollback"]).exists()


def test_overwrite_updates_in_place_and_undo_restores_the_old_row(live) -> None:
    data_dir, index, _ = live
    ids = _settings_fixture(index, data_dir)
    zip_path = _backup(data_dir, data_dir)
    index.update_saved_chart(ids["chart_a"], "Chart A", ["sensor.a"], "day", False, chart_type="bar")

    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        key = next(i["key"] for i in overview["charts"] if i["name"] == "Chart A")
        plan = rs.make_plan(overview, {key}, {key: "over"})
        assert [i["name"] for i in plan["charts_over"]] == ["Chart A"]
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")
    assert index.get_saved_chart(ids["chart_a"])["chart_type"] == "line"
    assert len(index.list_saved_charts()) == 2

    rs.undo(data_dir, index, StorageCoordinator(), result["rollback"], TZ)
    assert index.get_saved_chart(ids["chart_a"])["chart_type"] == "bar"


def test_table_overwrite_replaces_columns_and_rows(live) -> None:
    data_dir, index, _ = live
    ids = _settings_fixture(index, data_dir)
    zip_path = _backup(data_dir, data_dir)
    index.update_saved_table(
        ids["table"], "Tabelle 1",
        [{"label": "A", "range_key": "day", "offset": 0, "year_over_year": False},
         {"label": "B", "range_key": "month", "offset": 0, "year_over_year": False}],
        [], None,
    )
    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        item = overview["tables"][0]
        assert item["st"] == "diff"
        plan = rs.make_plan(overview, {item["key"]}, {item["key"]: "over"})
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")
    with index._lock:
        cols = index._conn.execute("SELECT label FROM table_columns WHERE table_id = ?", (ids["table"],)).fetchall()
        rows = index._conn.execute("SELECT label FROM table_rows WHERE table_id = ?", (ids["table"],)).fetchall()
    assert [c[0] for c in cols] == ["Heute"] and [r[0] for r in rows] == ["Zeile"]
    rs.undo(data_dir, index, StorageCoordinator(), result["rollback"], TZ)
    with index._lock:
        cols = index._conn.execute("SELECT label FROM table_columns WHERE table_id = ?", (ids["table"],)).fetchall()
    assert [c[0] for c in cols] == ["A", "B"]


def test_entity_config_groups_and_retention_warning(live) -> None:
    data_dir, index, _ = live
    _entity(index, data_dir, "sensor.a", [1.0])
    with index._lock, index._conn:
        index._conn.execute("UPDATE entities SET retention = '5y', custom_name = 'Alt', decimals = '1' WHERE entity_id = 'sensor.a'")
    zip_path = _backup(data_dir, data_dir)
    with index._lock, index._conn:
        index._conn.execute("UPDATE entities SET retention = 'unlimited', custom_name = 'Neu', decimals = '2' WHERE entity_id = 'sensor.a'")

    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        entity = overview["entities"][0]
        assert set(entity["groups"]) == {"name", "ret", "disp"}
        assert entity["groups"]["ret"]["short"] is True
        assert entity["groups"]["name"]["short"] is False
        plan = rs.make_plan(overview, {"e:sensor.a|name", "e:sensor.a|ret"}, {})
        assert plan["entity_count"] == 1 and plan["total"] == 2
        assert len(plan["warnings"]) == 1
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")

    row = index.get_entity("sensor.a")
    assert (row["custom_name"], row["retention"], row["decimals"]) == ("Alt", "5y", "2")  # decimals nicht gewählt
    rs.undo(data_dir, index, StorageCoordinator(), result["rollback"], TZ)
    row = index.get_entity("sensor.a")
    assert (row["custom_name"], row["retention"]) == ("Neu", "unlimited")


def test_general_settings_use_a_whitelist_and_reset_schedule_state(live) -> None:
    data_dir, index, _ = live
    index.set_setting("backup_schedule", "daily")
    index.set_setting("api_token", "backup-token")
    index.set_setting("retention_last_success", "111")
    zip_path = _backup(data_dir, data_dir)
    index.set_setting("backup_schedule", "off")
    index.set_setting("api_token", "live-token")
    index.set_setting("backup_schedule_next_run", "999")

    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        names = {g["name"] for g in overview["general"]}
        assert names == {"backup_schedule"}  # weder api_token noch retention_last_success
        plan = rs.make_plan(overview, {"g:backup_schedule"}, {})
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")
    assert index.get_setting("backup_schedule") == "daily"
    assert index.get_setting("api_token") == "live-token"
    assert index.get_setting("backup_schedule_next_run") == ""

    rs.undo(data_dir, index, StorageCoordinator(), result["rollback"], TZ)
    assert index.get_setting("backup_schedule") == "off"
    assert index.get_setting("backup_schedule_next_run") == "999"


def test_symcon_names_are_restored_and_undone(live) -> None:
    data_dir, index, _ = live
    (data_dir / "symcon_names.json").write_text(json.dumps({"1": {"name": "alt", "parent": None}}), encoding="utf-8")
    zip_path = _backup(data_dir, data_dir)
    (data_dir / "symcon_names.json").write_text(json.dumps({"1": {"name": "neu", "parent": None}}), encoding="utf-8")
    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        assert [(x["key"], x["st"]) for x in overview["extra"]] == [("x:symcon", "diff")]
        plan = rs.make_plan(overview, {"x:symcon"}, {})
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")
    assert json.loads((data_dir / "symcon_names.json").read_text())["1"]["name"] == "alt"
    rs.undo(data_dir, index, StorageCoordinator(), result["rollback"], TZ)
    assert json.loads((data_dir / "symcon_names.json").read_text())["1"]["name"] == "neu"


def test_identical_objects_are_never_selectable_and_empty_plan_is_rejected(live) -> None:
    data_dir, index, _ = live
    _settings_fixture(index, data_dir)
    zip_path = _backup(data_dir, data_dir)
    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        assert all(i["st"] == "same" for i in overview["charts"] + overview["tables"] + overview["dashboards"])
        plan = rs.make_plan(overview, {i["key"] for i in overview["charts"]}, {})
        assert plan["total"] == 0
        with pytest.raises(ValueError):
            rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")
    assert rs.list_rollbacks(data_dir) == []


def test_pins_to_missing_entities_are_skipped(live) -> None:
    data_dir, index, _ = live
    ids = _settings_fixture(index, data_dir)
    zip_path = _backup(data_dir, data_dir)
    index.delete_dashboard(ids["dash"])
    index.delete_entity("sensor.a")
    with rs.BackupSource(zip_path) as source:
        overview = rs.load_settings_overview(source, index, data_dir)
        dash = next(d for d in overview["dashboards"] if d["name"] == "Energie")
        assert dash["missing_entities"] == 1
        plan = rs.make_plan(overview, {dash["key"]}, {})
        assert any("Entitäten" in w for w in plan["warnings"])
        result = rs.apply_settings(data_dir, index, source, overview, plan, source_label="b")
    assert result["skipped_pins"] == 1


def test_undo_rejects_foreign_names(live) -> None:
    data_dir, index, coordinator = live
    for name in ("../x", ".zeitarchiv-restore-rollback-20260101T000000Z", "nope"):
        with pytest.raises(ValueError):
            rs.undo(data_dir, index, coordinator, name, TZ)


# --------------------------------------------------------------------------- Routen


@pytest.fixture()
def wizard(client):
    """Ein echtes Backup im Backup-Verzeichnis der Test-App."""
    from app import main

    index = main.index
    entity_id = "sensor.wizard_test"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W", "Wizard Test")
    directory = main.DATA_DIR / "archive" / entity_id
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"ts": [TS, TS + 60], "value": [1.0, 2.0]}), directory / "2024-01.parquet")
    reconcile.audit_storage_metadata(main.DATA_DIR, index, TZ, entity_ids=[entity_id], repair=True)
    _chart(index, "Wizard Chart", [entity_id])
    filename = "zeitarchiv-backup-2026-10-16-120000.zip"
    backup.create_backup(main.DATA_DIR, main.BACKUPS_DIR / filename, consistent_sqlite=True)
    yield filename, entity_id
    for chart in index.list_saved_charts():
        if chart["name"] in ("Wizard Chart", "Nur im Live"):
            index.delete_saved_chart(chart["id"])
    index.delete_entity(entity_id)
    import shutil
    shutil.rmtree(directory, ignore_errors=True)
    (main.BACKUPS_DIR / filename).unlink(missing_ok=True)
    for r in rs.list_rollbacks(main.DATA_DIR):
        rs.delete_rollback(main.DATA_DIR, r["name"])


def test_backup_page_links_to_the_wizard(client, wizard) -> None:
    filename, _ = wizard
    html = client.get("/backup").text
    assert f"/backup/restore/{filename}" in html
    assert f'hx-post="backup/restore/{filename}"' not in html


def test_wizard_page_offers_the_three_modes(client, wizard) -> None:
    filename, _ = wizard
    response = client.get(f"/backup/restore/{filename}")
    assert response.status_code == 200
    html = response.text
    for value in ("all", "entities", "settings"):
        assert f'name="mode" value="{value}"' in html
    assert "backup-restore.js" in html


def test_wizard_page_for_unknown_backup_is_404(client) -> None:
    assert client.get("/backup/restore/nope.zip").status_code == 404
    assert client.get("/backup/restore/nope.zip/options?mode=entities").status_code == 404


def test_wizard_options_list_entities_and_settings(client, wizard) -> None:
    filename, entity_id = wizard
    entities = client.get(f"/backup/restore/{filename}/options?mode=entities")
    assert entities.status_code == 200 and entity_id in entities.text and 'name="ent"' in entities.text
    settings = client.get(f"/backup/restore/{filename}/options?mode=settings")
    assert settings.status_code == 200 and "Wizard Chart" in settings.text and 'data-tab="d"' in settings.text
    assert client.get(f"/backup/restore/{filename}/options?mode=all").status_code == 400


def test_wizard_review_and_apply_for_entities(client, wizard) -> None:
    filename, entity_id = wizard
    review = client.post(f"/backup/restore/{filename}/review", data={"mode": "entities", "ent": [entity_id]})
    assert review.status_code == 200 and "Prüfen und bestätigen" in review.text and entity_id in review.text
    empty = client.post(f"/backup/restore/{filename}/review", data={"mode": "entities"})
    assert "rw-error" in empty.text
    # Ohne Bestätigung passiert nichts.
    refused = client.post(f"/backup/restore/{filename}/apply", data={"mode": "entities", "ent": [entity_id]})
    assert "rw-error" in refused.text and rs.list_rollbacks(__import__("app.main", fromlist=["x"]).DATA_DIR) == []
    done = client.post(f"/backup/restore/{filename}/apply", data={"mode": "entities", "ent": [entity_id], "confirm": "1"})
    assert done.status_code == 200 and "wiederhergestellt" in done.text and "/backup/selective/undo/" in done.text
    listing = client.get("/backup/selective/list")
    assert "Selektive Wiederherstellungen" in listing.text
    name = rs.list_rollbacks(__import__("app.main", fromlist=["x"]).DATA_DIR)[0]["name"]
    undone = client.post(f"/backup/selective/undo/{name}")
    assert "Rückgängig gemacht" in undone.text


def test_wizard_review_and_apply_for_settings(client, wizard) -> None:
    filename, _ = wizard
    from app import main

    _chart(main.index, "Nur im Live", ["sensor.wizard_test"])  # damit der Vergleich nicht leer ist
    main.index.delete_saved_chart(next(c["id"] for c in main.index.list_saved_charts() if c["name"] == "Wizard Chart"))
    options = client.get(f"/backup/restore/{filename}/options?mode=settings").text
    import re
    key = re.search(r'value="(c:\d+)" data-key="c:\d+" aria-label="Wizard Chart"', options).group(1)
    review = client.post(f"/backup/restore/{filename}/review", data={"mode": "settings", "sel": [key]})
    assert "Wird hinzugefügt" in review.text and "Wizard Chart" in review.text
    done = client.post(f"/backup/restore/{filename}/apply", data={"mode": "settings", "sel": [key], "confirm": "1"})
    assert "Änderungen angewendet" in done.text
    assert any(c["name"] == "Wizard Chart" for c in main.index.list_saved_charts())
    main.index.delete_saved_chart(next(c["id"] for c in main.index.list_saved_charts() if c["name"] == "Nur im Live"))


def test_wizard_all_mode_review_without_confirm_does_not_queue_a_restore(client, wizard) -> None:
    filename, _ = wizard
    from app import main

    review = client.post(f"/backup/restore/{filename}/review", data={"mode": "all"})
    assert "Das Add-on muss neu starten" in review.text
    refused = client.post(f"/backup/restore/{filename}/apply", data={"mode": "all"})
    assert "rw-error" in refused.text
    assert not (main.DATA_DIR / backup.RESTORE_REQUEST_NAME).exists()


def test_selective_delete_route(client, wizard) -> None:
    filename, entity_id = wizard
    from app import main

    client.post(f"/backup/restore/{filename}/apply", data={"mode": "entities", "ent": [entity_id], "confirm": "1"})
    name = rs.list_rollbacks(main.DATA_DIR)[0]["name"]
    assert client.post(f"/backup/selective/delete/{name}").status_code == 200
    assert client.post(f"/backup/selective/delete/{name}").status_code == 404
