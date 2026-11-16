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
    index.set_cleanup_alltime_stats("sensor.go_test_a", {"gaps": 7, "all": 100}, "off", 1, gap_threshold="15", gap_profile={"tiers": {"1": 0}})
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
    index.set_cleanup_alltime_stats("sensor.go_test_b", {"gaps": 500, "all": 1000}, "off", 1, gap_threshold="15", gap_profile={"tiers": {"1": 0}})
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


# --- Empfehlung -------------------------------------------------------------------------------------------------

import time  # noqa: E402


@pytest.fixture()
def profiled(client, entities):
    """a: gespeicherte Werte, Profil mit 150 Pausen je 30 Tage über 15 Minuten (zu empfindlich, empfohlen 30 Minuten);
    c: Empfangs-Methode mit 20-Minuten-Pausen (empfohlen 30 Minuten); b: ohne Profil."""
    index, ids = entities
    now = time.time()
    index.set_cleanup_alltime_stats(
        ids[0], {"gaps": 120, "all": 5000}, "off", 1, gap_threshold="15",
        gap_profile={"method": "stored", "days": 30.0, "events": 4000,
                     "tiers": {"1": 800, "5": 400, "15": 150, "30": 3, "60": 1, "180": 0, "360": 0, "720": 0, "1440": 0}},
    )
    index._conn.execute(
        "UPDATE entities SET receive_gaps_since = ?, last_seen_ts = ?, last_ts = ? WHERE entity_id = ?",
        (now - 40 * 86400, now - 60, now - 60, ids[2]),
    )
    index._conn.execute("UPDATE entities SET last_seen_ts = ?, last_ts = ? WHERE entity_id = ?", (now - 60, now - 60, ids[0]))
    index._conn.executemany(
        "INSERT OR IGNORE INTO receive_gaps (entity_id, start_ts, end_ts) VALUES (?, ?, ?)",
        [(ids[2], now - 39 * 86400 + i * 4000, now - 39 * 86400 + i * 4000 + 1200) for i in range(200)],
    )
    index._conn.commit()
    yield index, ids
    index._conn.execute("DELETE FROM receive_gaps WHERE entity_id = ?", (ids[2],))
    index._conn.execute("UPDATE entities SET last_seen_ts = NULL, last_ts = NULL, receive_gaps_since = NULL WHERE entity_id IN (?, ?, ?)", ids)
    index._conn.commit()


def _row(html: str, entity_id: str) -> str:
    i = html.index(f'value="{entity_id}"')
    return html[i:html.index("</tr>", i)]


def test_the_table_shows_recommendation_and_judgement(client, profiled) -> None:
    html = client.get("/housekeeping/luecken-erkennung").text
    assert "Zu empfindlich" in _row(html, "sensor.go_test_a") and "30 Minuten" in _row(html, "sensor.go_test_a")
    assert "Zu empfindlich" in _row(html, "sensor.go_test_c")
    assert "Noch keine Empfehlung" in _row(html, "sensor.go_test_b")


def test_the_judgement_comes_before_the_gap_count_in_the_order(client, profiled) -> None:
    html = client.get("/housekeeping/luecken-erkennung").text
    assert html.index('value="sensor.go_test_a"') < html.index('value="sensor.go_test_b"')


def test_recommend_preview_lists_old_and_new_and_writes_nothing(client, profiled) -> None:
    index, _ids = profiled
    r = client.post("/housekeeping/luecken-erkennung/recommend/preview", data={"entity_id": ["sensor.go_test_a"]})
    assert "1 &times; 15 Minuten &rarr; 30 Minuten" in r.text
    assert index.get_entity("sensor.go_test_a")["gap_threshold"] == "15"


def test_recommend_all_covers_only_the_disturbed_ones(client, profiled, remeasured) -> None:
    index, _ids = profiled
    index.set_config("sensor.go_test_b", gap_threshold="15")
    r = client.post("/housekeeping/luecken-erkennung/recommend/apply", data={"all": "1"})
    assert r.status_code == 200
    assert index.get_entity("sensor.go_test_a")["gap_threshold"] == "30"
    assert index.get_entity("sensor.go_test_c")["gap_threshold"] == "30"
    assert index.get_entity("sensor.go_test_b")["gap_threshold"] == "15"  # keine Empfehlung
    assert "2 Entitäten auf 30 Minuten gesetzt." in r.text and "Die Lücken werden neu gemessen." in r.text
    assert 'value="sensor.go_test_a|15"' in r.text
    assert sorted(remeasured[-1]) == ["sensor.go_test_a", "sensor.go_test_c"]


def test_recommend_for_a_selection_ignores_entities_without_a_recommendation(client, profiled, remeasured) -> None:
    index, _ids = profiled
    r = client.post("/housekeeping/luecken-erkennung/recommend/apply",
                    data={"entity_id": ["sensor.go_test_b", "sensor.gibt_es_nicht"]})
    assert "Nichts zu ändern." in r.text
    assert index.get_entity("sensor.go_test_b")["gap_threshold"] == "15"
    assert remeasured == []


def test_the_configuration_page_offers_the_recommendation(client, profiled) -> None:
    html = client.get("/entities/sensor.go_test_a/config").text
    assert "Empfehlung" in html and "Zu empfindlich" in html
    assert "gap_threshold" in html and "&#34;30&#34;" in html  # Knopf mit dem empfohlenen Wert
    quiet = client.get("/entities/sensor.go_test_b/config").text
    assert "Noch keine Empfehlung" in quiet and "Empfehlung übernehmen" not in quiet


# --- Energiedashboard und Bereinigungs-Hinweis ---------------------------------------------------------------------

def _energy_uses(monkeypatch, *entity_ids: str) -> None:
    from app import entity_usage

    monkeypatch.setattr(entity_usage, "energy_entity_ids", lambda index: set(entity_ids))


def test_off_is_never_part_of_apply_all_for_entities_in_the_energy_dashboard(client, profiled, remeasured, monkeypatch) -> None:
    index, ids = profiled
    index._conn.execute("UPDATE entities SET resolution = 'raw' WHERE entity_id = ?", (ids[0],))
    index.set_cleanup_alltime_stats(
        ids[0], {"gaps": 900, "all": 5000}, "off", 1, gap_threshold="15",
        gap_profile={"method": "stored", "days": 30.0, "events": 4000,
                     "tiers": {"1": 950, "5": 900, "15": 800, "30": 700, "60": 600, "180": 500, "360": 400, "720": 300, "1440": 200}},
    )
    _energy_uses(monkeypatch, ids[0])
    client.post("/housekeeping/luecken-erkennung/recommend/apply", data={"all": "1"})
    assert index.get_entity(ids[0])["gap_threshold"] == "15"  # nicht geeignet → Aus, aber im Energiedashboard: nicht in „alle“
    assert index.get_entity(ids[2])["gap_threshold"] == "30"  # die übrigen werden übernommen
    assert "Wird im Energiedashboard verwendet" in client.get("/housekeeping/luecken-erkennung").text


def test_the_confirmation_names_energy_entities_that_would_be_switched_off(client, profiled, monkeypatch) -> None:
    index, ids = profiled
    _energy_uses(monkeypatch, ids[1])
    r = client.post("/housekeeping/luecken-erkennung/preview", data={"threshold": "off", "entity_id": [ids[0], ids[1]]})
    assert "Wird im Energiedashboard verwendet: sensor.go_test_b" in r.text
    r = client.post("/housekeeping/luecken-erkennung/preview", data={"threshold": "60", "entity_id": [ids[0], ids[1]]})
    assert "Energiedashboard" not in r.text


def test_the_cleanup_page_says_how_gaps_are_counted_for_receive_entities(client, profiled) -> None:
    index, ids = profiled
    html = client.get(f"/entities/{ids[2]}/rows?range=all").text
    assert "Gezählt werden Pausen zwischen empfangenen Werten, ab" in html
    plain = client.get(f"/entities/{ids[0]}/rows?range=all").text
    assert "Gezählt werden Pausen" not in plain


# --- Bestätigung als Popup ---------------------------------------------------------------------------------------

def test_the_confirmation_is_a_modal_dialog_with_the_affected_entities(client, profiled) -> None:
    r = client.post("/housekeeping/luecken-erkennung/recommend/preview", data={"entity_id": ["sensor.go_test_a", "sensor.go_test_c"]})
    assert "<dialog" in r.text and "data-gc-modal" in r.text
    assert "Betroffene Entitäten (2)" in r.text
    for entity_id in ("sensor.go_test_a", "sensor.go_test_c"):
        assert entity_id in r.text.split("<details")[1].split("</details>")[0]
    assert "15 Minuten &rarr; 30 Minuten" in r.text
    own = client.post("/housekeeping/luecken-erkennung/preview", data={"threshold": "60", "entity_id": ["sensor.go_test_a"]})
    assert "<dialog" in own.text and "15 Minuten &rarr; 1 Stunde" in own.text
    empty = client.post("/housekeeping/luecken-erkennung/preview", data={"threshold": "15", "entity_id": ["sensor.go_test_a"]})
    assert "<dialog" in empty.text and "Nichts zu ändern." in empty.text and "data-gc-cancel" in empty.text


def test_the_toast_after_applying_stays_in_the_page_and_not_in_a_dialog(client, profiled, remeasured) -> None:
    r = client.post("/housekeeping/luecken-erkennung/apply", data={"threshold": "60", "entity_id": ["sensor.go_test_a"]})
    assert "<dialog" not in r.text and "gc-toast" in r.text and "Rückgängig" in r.text
