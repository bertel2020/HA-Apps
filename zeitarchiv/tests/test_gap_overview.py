"""Housekeeping → Lücken-Erkennung: Übersicht je Entität und Sammelaktion „Schwelle setzen“.

Geändert wird nur die Einstellung der Lücken-Erkennung, nie Daten. Der Server prüft die Schwelle gegen die Stufen und
liest die Entitäten selbst; was der Browser schickt, sind nur Entitäts-IDs (oder „alle“) und beim Rückgängig die
vorherigen Stufen."""

from __future__ import annotations

import pytest

from app import data_quality
from app.storage.index import gap_method


@pytest.fixture()
def entities(client):
    from app.main import index

    ids = ("sensor.go_test_a", "sensor.go_test_b", "sensor.go_test_c")
    for entity_id in ids:
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, resolution="raw", gap_threshold="15", value_filter="off")
    index.set_config(ids[2], value_filter="decimals")
    yield index, ids
    for entity_id in ids:
        index.set_config(entity_id, resolution="raw", gap_threshold="15", value_filter="off")


@pytest.fixture()
def remeasured(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(data_quality, "request_remeasure", lambda idx, ids=None: calls.append(list(ids or [])) or len(ids or []))
    return calls


def test_the_section_is_in_the_page_and_in_the_navigation(client, entities) -> None:
    html = client.get("/housekeeping").text
    assert 'id="luecken-erkennung"' in html and "housekeeping#luecken-erkennung" in html
    assert "sensor.go_test_a" in html
    assert 'class="page page-wide"' in html


def test_the_table_shows_method_threshold_and_measured_gaps(client, entities) -> None:
    index, _ids = entities
    index.set_cleanup_alltime_stats("sensor.go_test_a", {"gaps": 7, "all": 100}, "off", 1, gap_threshold="15")
    html = client.get("/housekeeping/luecken-erkennung").text
    row_a = html[html.index("sensor.go_test_a"):]
    row_a = row_a[:row_a.index("</tr>")]
    assert "Gespeicherte Werte" in row_a and "15 Minuten" in row_a and ">7<" in row_a
    row_c = html[html.index('value="sensor.go_test_c"'):]
    row_c = row_c[:row_c.index("</tr>")]
    assert "Empfangene Werte" in row_c and "noch nicht begonnen" in row_c and ">–<" in row_c
    assert gap_method(index.get_entity("sensor.go_test_c")) == "receive"


def test_the_most_gaps_come_first(client, entities) -> None:
    index, _ids = entities
    index.set_cleanup_alltime_stats("sensor.go_test_b", {"gaps": 500, "all": 1000}, "off", 1, gap_threshold="15")
    html = client.get("/housekeeping/luecken-erkennung").text
    assert html.index("sensor.go_test_b") < html.index("sensor.go_test_a")


def test_state_entities_are_not_listed(client, entities) -> None:
    index, _ids = entities
    assert index.get_or_create_entity("sensor.go_test_text", "sensor", None, None, state=True) == "state"
    html = client.get("/housekeeping/luecken-erkennung").text
    assert "sensor.go_test_a" in html and "sensor.go_test_text" not in html


def test_preview_groups_by_old_threshold_and_writes_nothing(client, entities) -> None:
    index, _ids = entities
    index.set_config("sensor.go_test_b", gap_threshold="60")
    r = client.post("/housekeeping/luecken-erkennung/preview",
                    data={"threshold": "360", "entity_id": ["sensor.go_test_a", "sensor.go_test_b"]})
    assert r.status_code == 200
    assert "1 &times; von 15 Minuten" in r.text and "1 &times; von 1 Stunde" in r.text
    assert "6 Stunden" in r.text
    assert index.get_entity("sensor.go_test_a")["gap_threshold"] == "15"


def test_preview_skips_entities_that_already_have_the_threshold(client, entities) -> None:
    r = client.post("/housekeeping/luecken-erkennung/preview", data={"threshold": "15", "entity_id": ["sensor.go_test_a"]})
    assert "Nichts zu ändern." in r.text


def test_apply_sets_the_threshold_and_requests_the_remeasurement(client, entities, remeasured) -> None:
    index, _ids = entities
    r = client.post("/housekeeping/luecken-erkennung/apply", data={"threshold": "60", "entity_id": ["sensor.go_test_a"]})
    assert r.status_code == 200
    assert index.get_entity("sensor.go_test_a")["gap_threshold"] == "60"
    assert index.get_entity("sensor.go_test_b")["gap_threshold"] == "15"
    assert "Lücken-Erkennung von 15 Minuten auf 1 Stunde gesetzt" in r.text
    assert "Die Lücken werden neu gemessen." in r.text
    assert 'value="sensor.go_test_a|15"' in r.text
    assert remeasured == [["sensor.go_test_a"]]


def test_apply_can_switch_the_detection_off(client, entities, remeasured) -> None:
    index, _ids = entities
    client.post("/housekeeping/luecken-erkennung/apply", data={"threshold": "off", "entity_id": ["sensor.go_test_b"]})
    assert index.get_entity("sensor.go_test_b")["gap_threshold"] == "off"


def test_apply_rejects_an_unknown_threshold(client, entities, remeasured) -> None:
    for value in ("2", "abc", ""):
        r = client.post("/housekeeping/luecken-erkennung/apply", data={"threshold": value, "entity_id": ["sensor.go_test_a"]})
        assert r.status_code == 400
    assert client.post("/housekeeping/luecken-erkennung/apply", data={"entity_id": ["sensor.go_test_a"]}).status_code == 400
    assert remeasured == []


def test_apply_ignores_ids_that_are_not_listed(client, entities, remeasured) -> None:
    r = client.post("/housekeeping/luecken-erkennung/apply", data={"threshold": "60", "entity_id": ["sensor.gibt_es_nicht"]})
    assert "Nichts zu ändern." in r.text
    assert remeasured == []


def test_undo_restores_the_previous_steps_and_remeasures(client, entities, remeasured) -> None:
    index, _ids = entities
    client.post("/housekeeping/luecken-erkennung/apply", data={"threshold": "60", "entity_id": ["sensor.go_test_a", "sensor.go_test_b"]})
    r = client.post("/housekeeping/luecken-erkennung/undo", data={"undo": ["sensor.go_test_a|15", "sensor.go_test_b|15"]})
    assert "2 Änderung(en) zurückgenommen" in r.text
    assert index.get_entity("sensor.go_test_a")["gap_threshold"] == "15"
    assert remeasured[-1] == ["sensor.go_test_a", "sensor.go_test_b"]


def test_undo_rejects_unknown_steps_and_entities(client, entities, remeasured) -> None:
    index, _ids = entities
    r = client.post("/housekeeping/luecken-erkennung/undo",
                    data={"undo": ["sensor.go_test_a|bogus", "sensor.gibt_es_nicht|15", "kaputt"]})
    assert "0 Änderung(en) zurückgenommen" in r.text
    assert index.get_entity("sensor.go_test_a")["gap_threshold"] == "15"
    assert remeasured == []
