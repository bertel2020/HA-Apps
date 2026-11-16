"""Housekeeping → Ausreißer: Beurteilung je Entität und Sammelaktion „Empfehlung übernehmen“.

Geändert wird nur die Schwelle der Ausreißer-Erkennung, nie Daten. Der Server berechnet die Empfehlung selbst; der Browser
schickt nur Entitäts-IDs (oder „alle“) und beim Rückgängig die vorherigen Stufen."""

from __future__ import annotations

import time

import pytest

from app import data_quality
from app.storage.cleanup import OUTLIER_RULE_VERSION

NOISY = {"10": 900, "20": 900, "50": 900, "100": 0}
QUIET = {"10": 0, "20": 0, "50": 0, "100": 0}
LOUD = {"10": 900, "20": 800, "50": 700, "100": 600}
IDS = ("sensor.oo_test_eng", "sensor.oo_test_ruhig", "sensor.oo_test_laut", "sensor.oo_test_aus")


def _profile(index, entity_id, tiers, *, schwelle):
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    index.set_config(entity_id, outlier_threshold=schwelle)
    index.record_write(entity_id, time.time())
    if tiers is not None:
        index.set_cleanup_alltime_stats(
            entity_id, {"all": 5000}, schwelle, OUTLIER_RULE_VERSION, gap_threshold="r15", daily={},
            outlier_profile={"rule": OUTLIER_RULE_VERSION, "days": 60.0, "judged": 5000, "blind": 0, "tiers": tiers},
        )


@pytest.fixture()
def entities(client):
    from app.main import index

    _profile(index, IDS[0], NOISY, schwelle="20")
    _profile(index, IDS[1], QUIET, schwelle="50")
    _profile(index, IDS[2], LOUD, schwelle="50")
    _profile(index, IDS[3], None, schwelle="off")
    yield index
    for entity_id in IDS:
        index.set_config(entity_id, outlier_threshold="50")


@pytest.fixture()
def remeasured(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(data_quality, "request_remeasure", lambda idx, ids=None: calls.append(list(ids or [])) or len(ids or []))
    return calls


def _row(html: str, entity_id: str) -> str:
    start = html.index(f"entities/{entity_id}/config")
    return html[start:html.index("</tr>", start)]


def test_each_assessment_is_shown(client, entities) -> None:
    html = client.get("/housekeeping/ausreisser").text
    assert "Zu empfindlich" in _row(html, IDS[0]) and "50×" in _row(html, IDS[0])
    assert "Passt" in _row(html, IDS[1])
    assert "Nicht geeignet" in _row(html, IDS[2]) and "Bereinigung öffnen" in _row(html, IDS[2])
    assert "Ausgeschaltet" in _row(html, IDS[3])


def test_only_too_sensitive_rows_can_be_selected_and_applied(client, entities) -> None:
    html = client.get("/housekeeping/ausreisser").text
    assert f'value="{IDS[0]}"' in html
    for entity_id in IDS[1:]:
        assert f'value="{entity_id}"' not in html


def test_the_filter_keeps_only_the_chosen_assessment(client, entities) -> None:
    html = client.get("/housekeeping/ausreisser?status=unsuitable").text
    assert IDS[2] in html and IDS[0] not in html
    assert client.get("/housekeeping/ausreisser?status=bogus").status_code == 200


def test_state_and_switch_entities_are_not_listed(client, entities) -> None:
    entities.get_or_create_entity("binary_sensor.oo_test_schalter", "binary_sensor", "measurement", None)
    assert "oo_test_schalter" not in client.get("/housekeeping/ausreisser").text


def test_preview_names_old_and_new_and_writes_nothing(client, entities) -> None:
    r = client.post("/housekeeping/ausreisser/recommend/preview", data={"all": "1"})
    assert r.status_code == 200
    assert "1 &times; 20× &rarr; 100×" in r.text or "1 &times; 20× &rarr; 50×" in r.text
    assert entities.get_entity(IDS[0])["outlier_threshold"] == "20"


def test_apply_all_only_touches_the_too_sensitive_ones(client, entities, remeasured) -> None:
    r = client.post("/housekeeping/ausreisser/recommend/apply", data={"all": "1"})
    assert r.status_code == 200
    assert entities.get_entity(IDS[0])["outlier_threshold"] in ("50", "100")
    assert entities.get_entity(IDS[1])["outlier_threshold"] == "50"
    assert entities.get_entity(IDS[2])["outlier_threshold"] == "50"
    assert remeasured == [[IDS[0]]]
    assert f'value="{IDS[0]}|20"' in r.text  # Rückgängig trägt die alte Stufe


def test_a_selection_only_applies_where_the_recommendation_differs(client, entities, remeasured) -> None:
    r = client.post("/housekeeping/ausreisser/recommend/apply", data={"entity_id": [IDS[2], IDS[3]]})
    assert "Nichts zu ändern." in r.text
    assert entities.get_entity(IDS[2])["outlier_threshold"] == "50" and remeasured == []


def test_undo_restores_the_previous_threshold(client, entities, remeasured) -> None:
    client.post("/housekeeping/ausreisser/recommend/apply", data={"all": "1"})
    r = client.post("/housekeeping/ausreisser/undo", data={"undo": f"{IDS[0]}|20"})
    assert entities.get_entity(IDS[0])["outlier_threshold"] == "20"
    assert "1 Änderung(en) zurückgenommen" in r.text


@pytest.mark.parametrize("value", ["bogus|x", f"{IDS[0]}|7", "sensor.gibt_es_nicht|20", "ohne-trenner"])
def test_undo_ignores_invalid_values(client, entities, remeasured, value) -> None:
    client.post("/housekeeping/ausreisser/undo", data={"undo": value})
    assert entities.get_entity(IDS[0])["outlier_threshold"] == "20" and remeasured == []
