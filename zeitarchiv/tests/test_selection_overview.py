"""„Auswahl & Verlauf“: Tabelle, Herkunft, Verlauf und Rückgängig (ROADMAP 1.33, Phase 1)."""

from __future__ import annotations

from app import selection_overview


def _row(entity_id, **fields):
    base = {
        "entity_id": entity_id, "domain": entity_id.split(".", 1)[0], "name": entity_id, "selected": True,
        "excluded": False, "drifted": False, "origin": "manual", "via": [], "excluded_by": [], "topic": None,
        "added_ts": None, "text": False,
    }
    return {**base, **fields}


ASSISTANT = _row("sensor.netz", name="Netzbezug", origin="assistant", topic="energie", added_ts=100.0,
                 via=[{"type": "label", "value": "Zeitarchiv", "level": "entity"},
                      {"type": "assistant", "topic": "energie", "mode": "label"}])
MANUAL_ID = _row("sensor.hand", via=[{"type": "entity"}])
PATTERN = _row("sensor.muster", via=[{"type": "pattern", "value": "sensor.*_temp"}])
AREA = _row("sensor.bereich", via=[{"type": "area", "value": "Küche"}])
DEVICE = _row("sensor.geraet", via=[{"type": "device", "value": "Shelly"}])
LABEL = _row("sensor.label", via=[{"type": "label", "value": "Wichtig", "level": "device"}])
EXCLUDED = _row("sensor.aus", selected=False, origin=None, excluded=True, excluded_by=[{"type": "entity"}])
DRIFTED = _row("sensor.weg", selected=False, origin=None, drifted=True, topic="energie")
UNSELECTED = _row("sensor.nichts", selected=False, origin=None)
CATALOG = [ASSISTANT, MANUAL_ID, PATTERN, AREA, DEVICE, LABEL, EXCLUDED, DRIFTED, UNSELECTED]


def _ids(table):
    return {row["entity_id"] for row in table["rows"]}


def test_table_lists_selection_exclusions_and_drift_but_not_unselected() -> None:
    table = selection_overview.table(CATALOG, archived={"sensor.netz"})
    assert _ids(table) == {row["entity_id"] for row in CATALOG} - {"sensor.nichts"}
    assert table["counts"]["alle"] == 8


def test_filter_chips() -> None:
    expected = {
        "zeitarchiv": {"sensor.netz"},
        "manuell": {"sensor.hand", "sensor.muster", "sensor.bereich", "sensor.geraet", "sensor.label"},
        "muster": {"sensor.muster"},
        "bereich": {"sensor.bereich"},
        "geraet": {"sensor.geraet"},
        "label": {"sensor.netz", "sensor.label"},
        "ausgeschlossen": {"sensor.aus"},
        "geaendert": {"sensor.weg"},
    }
    for key, ids in expected.items():
        table = selection_overview.table(CATALOG, set(), filter_key=key)
        assert _ids(table) == ids, key
        assert table["counts"][key] == len(ids)
    assert selection_overview.table(CATALOG, set(), filter_key="quatsch")["filter"] == "alle"


def test_search_and_paging() -> None:
    assert _ids(selection_overview.table(CATALOG, set(), query="NETZBEZ")) == {"sensor.netz"}
    many = [_row(f"sensor.s{i:03d}", via=[{"type": "entity"}]) for i in range(120)]
    page3 = selection_overview.table(many, set(), page=3)
    assert page3["pages"] == 3 and len(page3["rows"]) == 20
    assert selection_overview.table(many, set(), page=99)["page"] == 3


def test_removable_only_for_assistant_entries_and_manual_single_ids() -> None:
    assert selection_overview.removable(ASSISTANT) and not selection_overview.manual_single_id(ASSISTANT)
    assert selection_overview.removable(MANUAL_ID) and selection_overview.manual_single_id(MANUAL_ID)
    assert selection_overview.removable(DRIFTED)
    for row in (PATTERN, AREA, DEVICE, LABEL, EXCLUDED):
        assert not selection_overview.removable(row), row["entity_id"]
    both = _row("sensor.beides", via=[{"type": "entity"}, {"type": "pattern", "value": "sensor.*"}])
    assert selection_overview.removable(both)


def test_the_assistants_own_label_is_not_shown_twice() -> None:
    assert [b["text"] for b in selection_overview.badges(ASSISTANT, "Zeitarchiv")] == ["Zeitarchiv · Energie"]
    assert [b["text"] for b in selection_overview.badges(ASSISTANT)] == ["Zeitarchiv · Energie", "Label: Zeitarchiv"]
    assert selection_overview.explain(ASSISTANT, "Zeitarchiv") == ["Vom Zeitarchiv-Assistenten über das Label hinzugefügt."]
    table = selection_overview.table(CATALOG, set(), filter_key="label", managed_label="Zeitarchiv")
    assert _ids(table) == {"sensor.label"}
    ids_mode = _row("sensor.ids", origin="assistant", via=[{"type": "entity"}, {"type": "assistant", "topic": "wetter", "mode": "ids"}])
    assert [b["text"] for b in selection_overview.badges(ids_mode)] == ["Zeitarchiv · Wetter"]
    assert not selection_overview.manual_single_id(ids_mode)


def test_badges_and_explanations() -> None:
    assert [b["text"] for b in selection_overview.badges(MANUAL_ID)] == ["Manuell in HA"]
    assert [b["kind"] for b in selection_overview.badges(EXCLUDED)] == ["excluded"]
    assert [b["text"] for b in selection_overview.badges(DRIFTED)] == ["Von Hand geändert"]
    assert selection_overview.explain(LABEL) == ["Das Gerät trägt das Label „Wichtig“."]
    assert selection_overview.explain(PATTERN) == ["Passt auf das Muster „sensor.*_temp“."]
    table = selection_overview.table(CATALOG, {"sensor.netz"})
    rows = {row["entity_id"]: row for row in table["rows"]}
    assert rows["sensor.netz"]["status"] == "archived"
    assert rows["sensor.hand"]["status"] == "waiting"
    assert rows["sensor.aus"]["status"] == "excluded"
    assert rows["sensor.weg"]["status"] == "drifted"
    assert rows["sensor.muster"]["only_in_ha"] and not rows["sensor.hand"]["only_in_ha"]


# ---- Verlauf ------------------------------------------------------------------------------

def _log(entry_id, **fields):
    base = {"id": entry_id, "ts": 100.0, "source": "assistant", "action": "add", "mode": "label", "topic": "energie",
            "entity_ids": ["sensor.a", "sensor.b"], "count": 2, "status": "applied",
            "results": {"sensor.a": {"result": "ok", "mode": "label"}, "sensor.b": {"result": "already", "mode": None}},
            "undo_of": None, "undone_by": None, "ha_detail": None}
    return {**base, **fields}


def _job(job_id, status="pending", **fields):
    base = {"id": job_id, "created_ts": 200.0, "expires_ts": 200.0 + 86400, "source": "assistant", "action": "add",
            "mode": "ids", "topic": None, "entity_ids": ["sensor.c"], "undo_of": None, "force_ids": [],
            "status": status, "results": {}, "message": None, "finished_ts": None}
    return {**base, **fields}


def test_timeline_merges_log_and_open_jobs_newest_first() -> None:
    catalog = {"received_ts": 150.0, "log": [_log("l1"), _log("l0", ts=50.0, source="manual_ha", action="change",
                                                             ha_detail={"summary": "Muster hinzugefügt, +34"})]}
    jobs = [_job("j1"), _job("j2", status="expired", created_ts=120.0),
            _job("j3", status="applied", finished_ts=140.0),  # vor dem letzten Katalog: steht im Log
            _job("j4", status="applied", finished_ts=160.0, created_ts=155.0)]
    items = selection_overview.timeline(catalog, jobs)
    assert [item["id"] for item in items] == ["j1", "j4", "j2", "l1", "l0"]
    by_id = {item["id"]: item for item in items}
    assert by_id["j1"]["status_text"] == "wartet auf die Integration"
    assert by_id["j2"]["status_text"] == "abgelaufen"
    assert by_id["l0"]["summary"] == "Muster hinzugefügt, +34" and by_id["l0"]["source_text"] == "In HA geändert"
    assert by_id["l1"]["summary"] == "2 hinzugefügt (Label)"
    assert by_id["l1"]["details"][1] == {"entity_id": "sensor.b", "result": "war schon so"}


def test_undo_rules() -> None:
    catalog = {"received_ts": 0, "log": [
        _log("ok"), _log("ha", source="manual_ha", action="change"), _log("failed", status="failed"),
        _log("done", undone_by="x"), _log("nothing", results={"sensor.a": {"result": "already", "mode": None}}),
        _log("queued"),
    ]}
    items = {item["id"]: item for item in selection_overview.timeline(catalog, [_job("u", undo_of="queued", source="undo")])}
    assert items["ok"]["undoable"] is True
    for entry_id in ("ha", "failed", "done", "nothing", "queued"):
        assert items[entry_id]["undoable"] is False, entry_id
    assert items["u"]["undoable"] is False  # offen


def test_undo_plan_reverses_the_action_on_the_same_way() -> None:
    add_plan = selection_overview.undo_plan(_log("a"))
    assert add_plan == {"action": "remove", "mode": "auto", "entity_ids": ["sensor.a"], "force_ids": [], "topic": "energie",
                        "patterns": []}
    removed = _log("r", action="remove", mode=None, source="manual_ui", results={
        "sensor.a": {"result": "ok", "mode": "label"}, "sensor.b": {"result": "ok", "mode": "ids"}})
    plan = selection_overview.undo_plan(removed)
    assert plan["action"] == "add" and plan["mode"] == "label" and plan["force_ids"] == ["sensor.b"]
    only_ids = selection_overview.undo_plan(_log("r2", action="remove", results={"sensor.a": {"result": "ok", "mode": "ids"}}))
    assert only_ids["mode"] == "ids"
