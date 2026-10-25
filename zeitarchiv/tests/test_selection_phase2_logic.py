"""Entitäts-Assistent Phase 2 (ROADMAP 1.33), reine Logik der App: Auftragsprüfung der
neuen Aktionen, Katalogfelder ``patterns``/``log.patterns``, Freitext mit Synonymen,
Muster-Vorschläge und die Erweiterungen von „Auswahl & Verlauf“."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from app import selection_catalog, selection_jobs, selection_overview, selection_patterns, selection_search

# ---- Aufträge ----------------------------------------------------------------------------


def _create(tmp_path: Path, **kwargs):
    args = {"source": "manual_ui", "action": "exclude", "entity_ids": ["sensor.a"], **kwargs}
    return selection_jobs.create(tmp_path, **args)


def test_exclude_and_include_jobs_use_mode_auto(tmp_path: Path) -> None:
    for action in ("exclude", "include", "remove"):
        job = _create(tmp_path, action=action, mode="label")
        assert job["mode"] == "auto" and job["patterns"] == [] and job["unexclude_ids"] == []


def test_add_with_unexclude_ids_keeps_them_as_a_subset(tmp_path: Path) -> None:
    job = _create(tmp_path, source="assistant", action="add", mode="ids", entity_ids=["sensor.a", "sensor.b"],
                  unexclude_ids=["sensor.b", "sensor.b"])
    assert job["unexclude_ids"] == ["sensor.b"]
    public = selection_jobs.public(job)
    assert public["unexclude_ids"] == ["sensor.b"] and public["patterns"] == []
    assert "entry_id" not in public and "status" not in public


def test_pattern_jobs_carry_patterns_and_no_ids(tmp_path: Path) -> None:
    job = _create(tmp_path, source="assistant", action="add_patterns", entity_ids=[], patterns=["sensor.shelly_*"],
                  mode="label")
    assert job["mode"] == "ids" and job["entity_ids"] == [] and job["patterns"] == ["sensor.shelly_*"]
    removed = _create(tmp_path, action="remove_patterns", entity_ids=[], patterns=["sensor.shelly_*"])
    assert removed["mode"] == "auto"
    assert len(_create(tmp_path, action="add_patterns", entity_ids=[], patterns=[f"sensor.p{i}_*" for i in range(20)])["patterns"]) == 20


@pytest.mark.parametrize("kwargs", [
    {"action": "add_patterns", "entity_ids": [], "patterns": []},
    {"action": "add_patterns", "entity_ids": ["sensor.a"], "patterns": ["sensor.a*"]},
    {"action": "add_patterns", "entity_ids": [], "patterns": [f"sensor.p{i}_*" for i in range(21)]},
    {"action": "add_patterns", "entity_ids": [], "patterns": ["Sensor.Gross*"]},
    {"action": "add_patterns", "entity_ids": [], "patterns": ["sensor.[ab]*"]},
    {"action": "add_patterns", "entity_ids": [], "patterns": ["s" * 129]},
    {"action": "remove_patterns", "entity_ids": [], "patterns": ["a.b.c"]},
    {"action": "exclude", "patterns": ["sensor.a*"]},
    {"action": "exclude", "entity_ids": []},
    {"action": "exclude", "entity_ids": [f"sensor.s{i}" for i in range(201)]},
    {"action": "include", "unexclude_ids": ["sensor.a"]},
    {"action": "add", "mode": "label", "unexclude_ids": ["sensor.fremd"]},
    {"action": "change"},
])
def test_invalid_phase2_jobs_are_rejected(tmp_path: Path, kwargs) -> None:
    with pytest.raises(selection_jobs.JobError):
        _create(tmp_path, **kwargs)
    assert selection_jobs.rev(tmp_path) == 0


def test_ack_of_a_pattern_job_keys_results_by_pattern(tmp_path: Path) -> None:
    job = _create(tmp_path, action="add_patterns", entity_ids=[], patterns=["sensor.a_*", "sensor.b_*"])
    results = {"sensor.a_*": {"result": "too_many"}, "sensor.b_*": {"result": "neu_in_v9"},
               "sensor.c_*": {"result": "ok"}, "sensor.x": {"result": "ok"}}
    assert selection_jobs.ack(tmp_path, job["id"], "partial", results)
    stored = selection_jobs.get(tmp_path, job["id"])["results"]
    assert stored == {"sensor.a_*": {"result": "too_many", "mode": None}, "sensor.b_*": {"result": "error", "mode": None}}


def test_phase1_jobs_on_disk_are_served_with_empty_new_fields(tmp_path: Path) -> None:
    job = _create(tmp_path)
    job.pop("patterns")
    job.pop("unexclude_ids")
    assert selection_jobs.public(job)["patterns"] == [] and selection_jobs.public(job)["unexclude_ids"] == []


# ---- Katalog -----------------------------------------------------------------------------

def test_catalog_patterns_are_validated_and_limited(tmp_path: Path) -> None:
    raw = [
        {"pattern": "sensor.shelly_*", "origin": "assistant", "topic": "energie", "added_ts": 5.0, "count": 12, "x": 1},
        {"pattern": "sensor.shelly_*", "origin": "manual", "count": 1},  # doppelt
        {"pattern": "*_temp", "origin": "irgendwas", "count": -3, "added_ts": "gestern"},
        {"pattern": "Nicht Gültig"}, {"pattern": 5}, "kaputt",
        *({"pattern": f"sensor.p{i}_*"} for i in range(120)),
    ]
    selection_catalog.save(tmp_path, [], patterns=raw, phase2=True)
    catalog = selection_catalog.load(tmp_path)
    assert catalog["phase2"] is True
    first, second = catalog["patterns"][:2]
    assert first == {"pattern": "sensor.shelly_*", "origin": "assistant", "topic": "energie", "added_ts": 5.0, "count": 12}
    assert second == {"pattern": "*_temp", "origin": "manual", "topic": None, "added_ts": None, "count": 0}
    assert len(catalog["patterns"]) <= selection_catalog.MAX_PATTERNS


def test_catalog_keeps_the_assistant_flag_of_a_pattern_way(tmp_path: Path) -> None:
    selection_catalog.save(tmp_path, [{"entity_id": "sensor.a", "via": [
        {"type": "pattern", "value": "sensor.a*", "assistant": True},
        {"type": "pattern", "value": "*_a", "assistant": "ja"},
    ]}])
    via = selection_catalog.load(tmp_path)["entities"][0]["via"]
    assert via == [{"type": "pattern", "value": "sensor.a*", "assistant": True}, {"type": "pattern", "value": "*_a"}]


def test_log_accepts_the_new_actions_and_their_patterns() -> None:
    log = selection_catalog.clean_log([
        {"id": "p1", "ts": 10.0, "source": "assistant", "action": "add_patterns", "entity_ids": [],
         "patterns": ["sensor.a_*", "sensor.a_*", "UNGÜLTIG", "x" * 129, *(f"sensor.p{i}_*" for i in range(30))],
         "count": 1, "results": {"sensor.a_*": {"result": "ok"}, "sensor.z_*": {"result": "ok"}}, "extra": "weg"},
        {"id": "e1", "ts": 9.0, "source": "manual_ui", "action": "exclude", "entity_ids": ["sensor.b"],
         "results": {"sensor.b": {"result": "invalid"}}},
        {"id": "i1", "ts": 8.0, "source": "undo", "action": "include", "entity_ids": ["sensor.b"]},
        {"id": "r1", "ts": 7.0, "source": "manual_ui", "action": "remove_patterns", "patterns": "sensor.a_*"},
        {"id": "x1", "ts": 6.0, "source": "manual_ui", "action": "explode", "entity_ids": ["sensor.b"]},
    ])
    by_id = {entry["id"]: entry for entry in log}
    assert set(by_id) == {"p1", "e1", "i1", "r1"}
    assert by_id["p1"]["patterns"][0] == "sensor.a_*" and len(by_id["p1"]["patterns"]) == selection_catalog.MAX_LOG_PATTERNS
    assert by_id["p1"]["results"] == {"sensor.a_*": {"result": "ok", "mode": None}}
    assert by_id["p1"]["count"] == 20 and "extra" not in by_id["p1"]
    assert by_id["e1"]["results"]["sensor.b"]["result"] == "invalid"
    assert by_id["r1"]["patterns"] == []


def test_old_catalog_files_get_the_phase2_defaults(tmp_path: Path) -> None:
    selection_catalog.save(tmp_path, [{"entity_id": "sensor.a"}], log=[
        {"id": "l", "ts": 1.0, "source": "assistant", "action": "add", "entity_ids": ["sensor.a"]}], phase1=True)
    catalog = selection_catalog.load(tmp_path)
    assert catalog["patterns"] == [] and catalog["phase2"] is False and catalog["log"][0]["patterns"] == []


# ---- Freitext ----------------------------------------------------------------------------

@pytest.mark.parametrize("query,topic", [
    ("Strom", "energie"), ("  solar   PV ", "energie"), ("Photovoltaik", "energie"), ("heizung", "klima"),
    ("Luftfeuchte", "klima"), ("weather", "wetter"), ("Zähler", "wasser_gas"), ("WLAN", "netzwerk"),
    ("presence", "anwesenheit"), ("Temperatur & Klima", "klima"),
    ("CO2", "luftqualitaet"), ("Feinstaub", "luftqualitaet"), ("Batterie", "batterien"), ("akku", "batterien"),
    ("CPU", "system"), ("Festplatte", "system"), ("Fenster", "tueren_fenster"), ("Tür", "tueren_fenster"),
    ("strom küche", None), ("strom wetter", None), ("stromzähler", None), ("", None),
])
def test_synonyms_pick_a_topic_only_when_every_term_agrees(query, topic) -> None:
    assert selection_search.understood_topic(query) == topic


SEARCH_ROWS = [
    {"entity_id": "sensor.kueche_temp", "domain": "sensor", "name": "Küche Temperatur", "area": "Küche", "platform": "mqtt"},
    {"entity_id": "sensor.bad_temp", "domain": "sensor", "name": "Bad", "device": "Küchenradio", "platform": "zha"},
    {"entity_id": "light.kueche", "domain": "light", "name": "Licht", "area": "Küche"},
    {"entity_id": "sensor.versteckt", "domain": "sensor", "name": "Küche versteckt", "hidden": True},
    {"entity_id": "person.anna", "domain": "person", "name": "Anna", "area": "Küche"},
]


def test_fulltext_needs_every_term_in_name_id_device_area_or_platform() -> None:
    def ids(query):
        return {row["entity_id"] for group in selection_search.search(query, SEARCH_ROWS)["more"] for row in group["rows"]}

    assert ids("küche") == {"sensor.kueche_temp", "sensor.bad_temp", "light.kueche", "person.anna"}
    assert ids("küche temp") == {"sensor.kueche_temp", "sensor.bad_temp"}
    assert ids("ZHA") == {"sensor.bad_temp"}
    assert ids("nirgends") == set()


def test_search_preselects_nothing_and_flags_presence_hits() -> None:
    result = selection_search.search("küche", SEARCH_ROWS, archived={"sensor.bad_temp"})
    rows = [row for group in result["more"] for row in group["rows"]]
    assert result["topic"] == selection_search.SEARCH_TOPIC and result["recommended"] == []
    assert not any(row["checked"] for row in rows)
    assert {row["reason"] for row in rows} == {"Treffer in Name/Gerät/Bereich/Plattform"}
    assert result["presence"] == 1
    assert selection_search.search("temp", SEARCH_ROWS)["presence"] == 0


def test_search_is_limited() -> None:
    many = [{"entity_id": f"sensor.viel_{i:04d}", "domain": "sensor", "name": f"Viel {i}"} for i in range(350)]
    result = selection_search.search("viel", many)
    assert result["counts"]["found"] == selection_search.MAX_RESULTS and result["truncated"] == 50


# ---- Muster ------------------------------------------------------------------------------

@pytest.mark.parametrize("entity_id,pattern", [
    ("sensor.shelly_plug_power", "sensor.shelly_*"), ("sensor.shelly_plug_power", "shelly_*"),
    ("sensor.shelly_plug_power", "*_power"), ("light.shelly_plug", "sensor.shelly_*"),
    ("sensor.ab", "sensor.a?"), ("sensor.abc", "sensor.a?"), ("sensor.x", "sensor*"),
])
def test_matching_is_fnmatchcase_with_object_ids_for_patterns_without_a_dot(entity_id, pattern) -> None:
    target = entity_id if "." in pattern else entity_id.split(".", 1)[1]
    assert selection_patterns.matches(entity_id, pattern) is fnmatchcase(target, pattern)


def _cat(entity_id, **fields):
    return {"entity_id": entity_id, "domain": entity_id.split(".", 1)[0], "name": entity_id, **fields}


PLUGS = [_cat(f"sensor.shelly_plug_{i}_power", device_class="power") for i in range(6)]
CATALOG = [
    *PLUGS,
    _cat("sensor.shelly_plug_9_status", text=True),            # Text: zählt nie
    _cat("sensor.shelly_plug_8_energy", selected=True),         # passt, nicht in der Ansicht
    _cat("sensor.shelly_plug_7_temp", excluded=True),
    _cat("sensor.fritz_a"), _cat("sensor.fritz_b"),
]


def test_candidates_need_five_hits_and_prefer_the_specific_prefix() -> None:
    view = [{"entity_id": row["entity_id"], "text": False} for row in PLUGS]
    found = selection_patterns.candidates(view, CATALOG, archived={"sensor.shelly_plug_0_power"})
    # „sensor.shelly_*“ bringt keine Treffer mehr als „sensor.shelly_plug_*“ und fällt weg.
    assert [item["pattern"] for item in found] == ["sensor.shelly_plug_*"]
    item = found[0]
    assert item["hits"] == 6 and item["total"] == 8
    assert (item["new"], item["archived"], item["excluded"]) == (5, 2, 1)
    assert item["outside"] == 2 and item["text"] == 1 and item["high_rate"] == 6
    assert len(item["examples"]) == 8 and item["active"] is False
    assert selection_patterns.candidates(view[:4], CATALOG, set()) == []


def test_text_hits_do_not_make_a_candidate_and_active_patterns_are_marked() -> None:
    texts = [{"entity_id": f"sensor.txt_{i}_x", "text": True} for i in range(9)]
    assert selection_patterns.candidates(texts, CATALOG, set()) == []
    view = [{"entity_id": row["entity_id"], "text": False} for row in PLUGS]
    assert selection_patterns.candidates(view, CATALOG, set(), active={"sensor.shelly_plug_*"})[0]["active"] is True


def test_candidates_are_capped_and_short_prefixes_skipped() -> None:
    rows = [_cat(f"sensor.g{group:02d}x_{i}") for group in range(12) for i in range(5)]
    rows += [_cat(f"sensor.ab_{i}") for i in range(7)]  # „ab“ ist zu kurz
    view = [{"entity_id": row["entity_id"], "text": False} for row in rows]
    found = selection_patterns.candidates(view, rows, set())
    assert len(found) == selection_patterns.MAX_CANDIDATES
    assert all(not item["pattern"].startswith("sensor.ab_") for item in found)


# ---- Auswahl & Verlauf -------------------------------------------------------------------

def _row(entity_id, **fields):
    base = {"entity_id": entity_id, "domain": "sensor", "name": entity_id, "selected": True, "excluded": False,
            "drifted": False, "origin": "manual", "via": [], "excluded_by": [], "topic": None, "added_ts": None}
    return {**base, **fields}


def test_exclude_and_include_rules() -> None:
    area = _row("sensor.bereich", via=[{"type": "area", "value": "Küche"}])
    by_id = _row("sensor.aus", selected=False, origin=None, excluded=True, excluded_by=[{"type": "entity"}])
    by_pattern = _row("sensor.muster", selected=False, origin=None, excluded=True,
                      excluded_by=[{"type": "pattern", "value": "*_muster"}])
    both = _row("sensor.beides", selected=False, origin=None, excluded=True,
                excluded_by=[{"type": "entity"}, {"type": "pattern", "value": "*_beides"}])
    assert selection_overview.excludable(area) and not selection_overview.excludable(by_id)
    assert selection_overview.includable(by_id) and selection_overview.includable(both)
    assert not selection_overview.includable(by_pattern) and not selection_overview.includable(area)
    assert selection_overview.excluded_patterns(both) == ["*_beides"]
    assert selection_overview.held_by_ha(area) == ["area"]
    rows = {row["entity_id"]: row for row in selection_overview.table([area, by_id, by_pattern], set())["rows"]}
    assert rows["sensor.bereich"]["excludable"] and rows["sensor.aus"]["includable"]
    assert rows["sensor.muster"]["excluded_patterns"] == ["*_muster"] and not rows["sensor.muster"]["includable"]


def test_assistant_patterns_show_as_zeitarchiv() -> None:
    row = _row("sensor.shelly_a", via=[{"type": "pattern", "value": "sensor.shelly_*", "assistant": True}])
    assert [badge["text"] for badge in selection_overview.badges(row)] == ["Zeitarchiv-Muster: sensor.shelly_*"]
    assert selection_overview.matches_filter(row, "zeitarchiv") and selection_overview.matches_filter(row, "muster")
    assert "Zeitarchiv-Assistent" in selection_overview.explain(row)[0]
    assert selection_overview.held_by_ha(row) == []


def _log(entry_id, action, **fields):
    base = {"id": entry_id, "ts": 100.0, "source": "manual_ui", "action": action, "mode": None, "topic": None,
            "entity_ids": ["sensor.a"], "patterns": [], "count": 1, "status": "applied",
            "results": {"sensor.a": {"result": "ok", "mode": None}}, "undo_of": None, "undone_by": None, "ha_detail": None}
    return {**base, **fields}


def test_timeline_labels_and_undo_for_the_new_actions() -> None:
    pattern_results = {"sensor.p_*": {"result": "ok", "mode": None}, "sensor.q_*": {"result": "already", "mode": None}}
    log = [
        _log("ex", "exclude"), _log("in", "include", ts=90.0),
        _log("ap", "add_patterns", source="assistant", entity_ids=[], patterns=["sensor.p_*", "sensor.q_*"],
             results=pattern_results, ts=80.0, topic="energie"),
        _log("rp", "remove_patterns", entity_ids=[], patterns=["sensor.p_*"],
             results={"sensor.p_*": {"result": "ok", "mode": None}}, ts=70.0),
    ]
    items = {item["id"]: item for item in selection_overview.timeline({"received_ts": 0, "log": log}, [])}
    assert items["ex"]["summary"] == "Ausgeschlossen: 1" and items["ex"]["source_text"] == "In Zeitarchiv geändert"
    assert items["in"]["summary"] == "Wieder aufgenommen: 1"
    assert items["ap"]["summary"] == "Muster hinzugefügt" and items["ap"]["source_text"] == "Zeitarchiv-Assistent"
    assert items["ap"]["details"] == [{"entity_id": "sensor.p_*", "result": "übernommen"},
                                      {"entity_id": "sensor.q_*", "result": "war schon so"}]
    assert items["rp"]["summary"] == "Muster entfernt"
    assert all(item["undoable"] for item in items.values())
    assert selection_overview.undo_plan(items["ex"])["action"] == "include"
    assert selection_overview.undo_plan(items["in"])["action"] == "exclude"
    plan = selection_overview.undo_plan(items["ap"])
    assert plan == {"action": "remove_patterns", "mode": "auto", "entity_ids": [], "force_ids": [], "topic": "energie",
                    "patterns": ["sensor.p_*"]}
    back = selection_overview.undo_plan(items["rp"])
    assert back["action"] == "add_patterns" and back["mode"] == "ids" and back["patterns"] == ["sensor.p_*"]


def test_open_pattern_jobs_appear_in_the_timeline_with_their_patterns() -> None:
    job = {"id": "j", "created_ts": 5.0, "source": "assistant", "action": "add_patterns", "mode": "ids",
           "entity_ids": [], "patterns": ["sensor.p_*"], "status": "pending", "results": {}}
    item = selection_overview.timeline(None, [job])[0]
    assert item["patterns"] == ["sensor.p_*"] and item["count"] == 1 and item["summary"] == "Muster hinzugefügt"
