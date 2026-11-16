"""Housekeeping → Lücken-Erkennung: Empfehlungen übernehmen (einzeln, gewählt, alle), Rückgängig, Neumessung.

Übernommen wird nur die Schwelle der Lücken-Erkennung, nie Daten. Die Empfehlung berechnet der Server beim Übernehmen
neu aus dem aktuellen Stand; was der Browser schickt, sind nur die gewählten Entitäten (und beim Rückgängig die
vorherigen Stufen, die gegen die Leiter geprüft werden)."""

from __future__ import annotations

import pytest

from app import data_quality


@pytest.fixture()
def entities(client):
    from app.main import index

    ids = ("sensor.gc_test_a", "sensor.gc_test_b")
    for entity_id in ids:
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, resolution="1h", gap_threshold="15", value_filter="off")
    yield index, ids
    for entity_id in ids:
        index.set_config(entity_id, resolution="raw", gap_threshold="15")


@pytest.fixture()
def remeasured(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(data_quality, "request_remeasure", lambda idx, ids=None: calls.append(list(ids or [])) or len(ids or []))
    return calls


def test_the_section_is_named_after_the_detection_and_lists_the_conflicts(client, entities) -> None:
    html = client.get("/housekeeping").text
    assert 'id="luecken-erkennung"' in html and 'id="konfiguration"' not in html
    assert "sensor.gc_test_a" in html
    assert "Gefundene Lücken" in html and "Lebenszeichen" in html


def test_preview_groups_the_selected_entities_by_recommended_step(client, entities) -> None:
    r = client.post("/housekeeping/luecken-erkennung/preview", data={"entity_id": ["sensor.gc_test_a", "sensor.gc_test_b"]})
    assert r.status_code == 200
    assert "2 &times; auf 1 Stunde" in r.text
    # nichts geschrieben
    assert entities[0].get_entity("sensor.gc_test_a")["gap_threshold"] == "15"


def test_preview_for_all_lists_every_current_conflict(client, entities) -> None:
    r = client.post("/housekeeping/luecken-erkennung/preview", data={"all": "1"})
    assert 'name="all"' in r.text
    assert "&times; auf" in r.text


def test_apply_sets_the_recommendation_and_requests_the_remeasurement(client, entities, remeasured) -> None:
    index, _ids = entities
    r = client.post("/housekeeping/luecken-erkennung/apply", data={"entity_id": ["sensor.gc_test_a"]})
    assert r.status_code == 200
    assert index.get_entity("sensor.gc_test_a")["gap_threshold"] == "60"
    assert index.get_entity("sensor.gc_test_b")["gap_threshold"] == "15"  # nicht gewählt
    assert "Lücken-Erkennung von 15 Minuten auf 1 Stunde gesetzt" in r.text
    assert "Die Lücken werden neu gemessen." in r.text
    assert 'value="sensor.gc_test_a|15"' in r.text  # Rückgängig trägt die alte Stufe
    assert remeasured == [["sensor.gc_test_a"]]


def test_apply_ignores_ids_that_are_no_conflict(client, entities, remeasured) -> None:
    r = client.post("/housekeeping/luecken-erkennung/apply", data={"entity_id": ["sensor.gibt_es_nicht"]})
    assert r.status_code == 200
    assert "Nichts zu übernehmen." in r.text
    assert remeasured == []


def test_undo_restores_the_previous_step_and_remeasures(client, entities, remeasured) -> None:
    index, _ids = entities
    client.post("/housekeeping/luecken-erkennung/apply", data={"entity_id": ["sensor.gc_test_a", "sensor.gc_test_b"]})
    assert index.get_entity("sensor.gc_test_b")["gap_threshold"] == "60"
    r = client.post("/housekeeping/luecken-erkennung/undo", data={"undo": ["sensor.gc_test_a|15", "sensor.gc_test_b|15"]})
    assert "2 Änderung(en) zurückgenommen" in r.text
    assert index.get_entity("sensor.gc_test_a")["gap_threshold"] == "15"
    assert index.get_entity("sensor.gc_test_b")["gap_threshold"] == "15"
    assert remeasured[-1] == ["sensor.gc_test_a", "sensor.gc_test_b"]


def test_undo_rejects_unknown_steps_and_entities(client, entities, remeasured) -> None:
    index, _ids = entities
    r = client.post("/housekeeping/luecken-erkennung/undo", data={"undo": ["sensor.gc_test_a|bogus", "sensor.gibt_es_nicht|15", "kaputt"]})
    assert "0 Änderung(en) zurückgenommen" in r.text
    assert index.get_entity("sensor.gc_test_a")["gap_threshold"] == "15"
    assert remeasured == []


def test_a_heartbeat_only_conflict_is_recommended_the_heartbeat_step(client, entities, remeasured) -> None:
    index, _ids = entities
    index.set_config("sensor.gc_test_a", resolution="raw", value_filter="decimals", value_filter_heartbeat="720")
    r = client.post("/housekeeping/luecken-erkennung/apply", data={"entity_id": ["sensor.gc_test_a"]})
    assert index.get_entity("sensor.gc_test_a")["gap_threshold"] == "720"
    assert "auf 12 Stunden gesetzt" in r.text
    index.set_config("sensor.gc_test_a", value_filter="off", value_filter_heartbeat="360")
