"""Oberfläche des Auswahl-Assistenten (ROADMAP 1.33, Phase 1): Vorschläge übernehmen,
„Auswahl & Verlauf“, Entfernen und Rückgängig — über die ganze App (``client``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import selection_catalog, selection_jobs

DATA_DIR = Path(os.environ["ZEITARCHIV_DATA_DIR"])

ENTITIES = [
    {"entity_id": "sensor.ui_netz", "name": "UI Netzbezug", "device_class": "energy", "state_class": "total_increasing",
     "unit": "kWh", "energy_role": "grid_import", "device": "UI Zähler"},
    {"entity_id": "sensor.ui_wp", "name": "UI Wärmepumpe", "device_class": "energy", "state_class": "total_increasing",
     "unit": "kWh", "energy_role": "consumer", "selected": True, "origin": "assistant", "topic": "energie",
     "via": [{"type": "assistant", "topic": "energie", "mode": "label"}, {"type": "label", "value": "Zeitarchiv"}]},
    {"entity_id": "sensor.ui_aus", "name": "UI Ausgeschlossen", "device_class": "energy",
     "state_class": "total_increasing", "energy_role": "consumer", "excluded": True,
     "excluded_by": [{"type": "entity"}]},
    {"entity_id": "weather.ui_home", "name": "UI Wetter", "text": True},
    {"entity_id": "sensor.ui_hand", "name": "UI Handeintrag", "selected": True, "origin": "manual",
     "via": [{"type": "entity"}]},
    {"entity_id": "sensor.ui_muster", "name": "UI Muster", "selected": True, "origin": "manual",
     "via": [{"type": "pattern", "value": "sensor.ui_*"}]},
]
LOG = [
    {"id": "log-add", "ts": 1000.0, "source": "assistant", "action": "add", "mode": "label", "topic": "energie",
     "entity_ids": ["sensor.ui_wp"], "count": 1, "status": "applied",
     "results": {"sensor.ui_wp": {"result": "ok", "mode": "label"}}},
    {"id": "log-ha", "ts": 900.0, "source": "manual_ha", "action": "change", "entity_ids": [], "count": 3,
     "status": "applied", "ha_detail": {"kind": "options", "summary": "Muster hinzugefügt, +3"}},
]


@pytest.fixture()
def catalog():
    for name in (selection_catalog.FILENAME, selection_jobs.FILENAME):
        (DATA_DIR / name).unlink(missing_ok=True)
    selection_catalog.save(DATA_DIR, ENTITIES, label={"id": "za", "name": "Zeitarchiv"}, log=LOG,
                           ledger_count=1, entry_id="EINTRAG", phase1=True)
    yield
    for name in (selection_catalog.FILENAME, selection_jobs.FILENAME):
        (DATA_DIR / name).unlink(missing_ok=True)


@pytest.fixture()
def german_again(client):
    yield
    client.post("settings/language", data={"language": "de"})


def _jobs():
    return selection_jobs.all_jobs(DATA_DIR)


# ---- Vorschlagen -------------------------------------------------------------------------

def test_suggest_page_shows_tiles_checkboxes_and_footer(client, catalog) -> None:
    html = client.get("/selection?topic=energie").text
    for label in ("Energie", "Wetter", "Temperatur & Klima", "Wasser & Gas", "Netzwerk", "Anwesenheit"):
        assert label in html
    assert 'name="entity_id" value="sensor.ui_netz"' in html
    netz = html.split('value="sensor.ui_netz"', 1)[1].split(">", 1)[0]
    assert "checked" in netz and "disabled" not in netz
    excluded = html.split('value="sensor.ui_aus"', 1)[1].split(">", 1)[0]
    assert "disabled" in excluded and "checked" not in excluded
    assert "Textentitäten gehen immer als Einzel-ID." in html
    assert 'action="/selection/apply"' in html


def test_text_entities_are_marked_on_the_weather_topic(client, catalog) -> None:
    html = client.get("/selection?topic=wetter").text
    assert "UI Wetter" in html and "nur als Einzel-ID" in html


def test_presence_topic_shows_the_sensitive_hint(client, catalog) -> None:
    assert "Anwesenheit ist sensibel" in client.get("/selection?topic=anwesenheit").text


def test_old_integration_keeps_the_page_read_only(client, catalog) -> None:
    selection_catalog.save(DATA_DIR, ENTITIES)
    html = client.get("/selection").text
    assert "braucht es eine neuere Version" in html
    assert 'id="sel-submit" disabled' in html
    response = client.post("/selection/apply", data={"topic": "energie", "entity_id": "sensor.ui_netz", "confirmed": "1"})
    assert response.status_code == 400 and _jobs() == []


def test_apply_asks_for_confirmation_then_creates_a_job(client, catalog) -> None:
    form = {"topic": "wetter", "mode": "label", "entity_id": ["weather.ui_home"]}
    preview = client.post("/selection/apply", data=form)
    assert preview.status_code == 200
    assert "Auswahl übernehmen?" in preview.text and "Textentitäten" in preview.text
    assert _jobs() == []
    done = client.post("/selection/apply", data={**form, "confirmed": "1"}, follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert done.headers["location"] == f"/selection/overview?job={job['id']}"
    assert job["source"] == "assistant" and job["action"] == "add" and job["mode"] == "label"
    assert job["topic"] == "wetter" and job["force_ids"] == ["weather.ui_home"] and job["entry_id"] == "EINTRAG"
    status = client.get(f"/selection/jobs/{job['id']}/status").json()
    assert status == {"status": "pending"}
    overview = client.get(done.headers["location"]).text
    assert "Wartet auf die Integration." in overview and "data-status-url" in overview
    selection_jobs.ack(DATA_DIR, job["id"], "applied", {"weather.ui_home": {"result": "ok", "mode": "ids"}})
    assert "Aktiv." in client.get(done.headers["location"]).text


def test_apply_respects_the_ingress_prefix_in_the_redirect(client, catalog) -> None:
    done = client.post(
        "/selection/apply", data={"topic": "energie", "entity_id": "sensor.ui_netz", "confirmed": "1"},
        headers={"X-Ingress-Path": "/api/hassio_ingress/abc"}, follow_redirects=False,
    )
    assert done.headers["location"].startswith("/api/hassio_ingress/abc/selection/overview?job=")


@pytest.mark.parametrize("form,text", [
    ({"topic": "energie", "confirmed": "1"}, "Keine Entität ausgewählt."),
    ({"topic": "quatsch", "entity_id": "sensor.ui_netz", "confirmed": "1"}, "Unbekanntes Thema."),
    ({"topic": "energie", "mode": "alles", "entity_id": "sensor.ui_netz", "confirmed": "1"}, "Unbekannter Modus"),
    ({"topic": "energie", "entity_id": "sensor.gibtsnicht", "confirmed": "1"}, "Nicht im Katalog"),
    ({"topic": "energie", "entity_id": "weather.ui_home", "confirmed": "1"}, "Gehört nicht zu diesem Thema"),
    ({"topic": "energie", "entity_id": ["sensor.ui_aus", "sensor.ui_wp"], "confirmed": "1"}, "schon archiviert"),
])
def test_apply_rejects_invalid_requests(client, catalog, form, text) -> None:
    response = client.post("/selection/apply", data=form)
    assert response.status_code == 400 and text in response.text
    assert _jobs() == []


def test_apply_rejects_more_than_200(client, catalog) -> None:
    ids = [f"sensor.viel_{i}" for i in range(201)]
    response = client.post("/selection/apply", data={"topic": "energie", "entity_id": ids, "confirmed": "1"})
    assert response.status_code == 400 and "Höchstens 200" in response.text


def test_locked_rows_are_skipped_not_applied(client, catalog) -> None:
    form = {"topic": "energie", "entity_id": ["sensor.ui_netz", "sensor.ui_aus"], "confirmed": "1"}
    assert client.post("/selection/apply", data=form, follow_redirects=False).status_code == 303
    assert _jobs()[0]["entity_ids"] == ["sensor.ui_netz"]


# ---- Auswahl & Verlauf --------------------------------------------------------------------

def test_overview_renders_table_badges_filters_and_timeline(client, catalog) -> None:
    html = client.get("/selection/overview").text
    assert 'class="entity-tabs"' in html and "Auswahl & Verlauf" in html
    assert "UI Wärmepumpe" in html and "Zeitarchiv · Energie" in html and "Muster: sensor.ui_*" in html
    assert "Manuell in HA" in html and "Ausgeschlossen" in html
    # Die Herkunft steht in der Spalte, das Zeilenmenü erklärt sie nicht noch einmal
    assert "Warum ist das drin?" not in html
    assert "In HA geändert" in html and "Muster hinzugefügt, +3" in html
    # Muster-Zeile: kein Kästchen, nur der Hinweis auf HA
    assert 'name="ids" value="sensor.ui_muster"' not in html
    assert 'name="ids" value="sensor.ui_hand"' in html
    # Rückgängig nur für den eigenen Eintrag, nicht für manual_ha
    assert 'name="target" value="log-add"' in html and 'name="target" value="log-ha"' not in html
    filtered = client.get("/selection/overview?filter=muster").text
    assert "UI Muster" in filtered and "UI Handeintrag" not in filtered
    searched = client.get("/selection/overview?q=handeintrag").text
    assert "UI Handeintrag" in searched and "UI Wärmepumpe" not in searched


def test_entities_list_carries_the_tabs(client) -> None:
    html = client.get("/entities").text
    assert 'class="entity-tabs"' in html and 'href="/selection/overview"' in html


def test_overview_without_catalog(client) -> None:
    (DATA_DIR / selection_catalog.FILENAME).unlink(missing_ok=True)
    assert "Noch kein Katalog empfangen" in client.get("/selection/overview").text


def test_remove_an_assistant_entry(client, catalog) -> None:
    preview = client.post("/selection/remove", data={"one": "sensor.ui_wp"})
    assert preview.status_code == 200 and "Aus der Auswahl entfernen?" in preview.text
    assert "confirm_manual" not in preview.text
    done = client.post("/selection/remove", data={"one": "sensor.ui_wp", "confirmed": "1"}, follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert (job["source"], job["action"], job["mode"], job["entity_ids"]) == ("manual_ui", "remove", "auto", ["sensor.ui_wp"])


def test_removing_a_manual_entry_needs_the_extra_confirmation(client, catalog) -> None:
    form = {"ids": ["sensor.ui_hand", "sensor.ui_wp"], "confirmed": "1"}
    preview = client.post("/selection/remove", data={"ids": form["ids"]})
    assert "Von Hand gewählt: 1" in preview.text and 'name="confirm_manual"' in preview.text
    missing = client.post("/selection/remove", data=form)
    assert missing.status_code == 400 and "Bitte bestätige" in missing.text and _jobs() == []
    done = client.post("/selection/remove", data={**form, "confirm_manual": "1"}, follow_redirects=False)
    assert done.status_code == 303 and _jobs()[0]["entity_ids"] == ["sensor.ui_hand", "sensor.ui_wp"]


@pytest.mark.parametrize("entity_id,text", [
    ("sensor.ui_muster", "nur über Muster, Bereich, Gerät oder Label"),
    ("sensor.ui_aus", "nur über Muster, Bereich, Gerät oder Label"),
    ("sensor.unbekannt", "Nicht im Katalog"),
])
def test_remove_refuses_what_only_ha_can_change(client, catalog, entity_id, text) -> None:
    response = client.post("/selection/remove", data={"one": entity_id, "confirmed": "1"})
    assert response.status_code == 400 and text in response.text and _jobs() == []


def test_undo_creates_a_counter_job_once(client, catalog) -> None:
    preview = client.post("/selection/undo", data={"target": "log-add"})
    assert preview.status_code == 200 and "Rückgängig machen?" in preview.text
    done = client.post("/selection/undo", data={"target": "log-add", "confirmed": "1"}, follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert (job["source"], job["action"], job["undo_of"], job["entity_ids"]) == ("undo", "remove", "log-add", ["sensor.ui_wp"])
    again = client.post("/selection/undo", data={"target": "log-add", "confirmed": "1"})
    assert again.status_code == 400 and len(_jobs()) == 1
    ha = client.post("/selection/undo", data={"target": "log-ha", "confirmed": "1"})
    assert ha.status_code == 400 and "Home Assistant" in ha.text


def test_pages_render_in_english(client, catalog, german_again) -> None:
    client.post("/settings/language", data={"language": "en"})
    suggest = client.get("/selection?topic=energie").text
    assert "Selection assistant" in suggest and "Temperature & climate" in suggest
    assert "Text entities are always added as a single ID." in suggest
    overview = client.get("/selection/overview").text
    assert "Selection & history" in overview
    assert "Start assistant" in overview
    assert "Changed in HA" in overview and "Zeitarchiv · Energy" in overview
    confirm = client.post("/selection/remove", data={"one": "sensor.ui_hand"}).text
    assert "Remove from the selection?" in confirm and "Chosen by hand: 1" in confirm


# ---- Bestätigung als Popup (selection_overview.js) -----------------------------------------

def test_confirmation_says_one_entity_in_the_singular(client, catalog) -> None:
    text = client.post("/selection/remove", data={"one": "sensor.ui_wp"}).text
    assert "1 Entität wird nicht mehr archiviert" in text and "1 Entitäten" not in text
    both = client.post("/selection/remove", data={"ids": ["sensor.ui_hand", "sensor.ui_wp"]}).text
    assert "2 Entitäten werden nicht mehr archiviert" in both


def test_manual_confirmation_is_a_real_checkbox_with_a_hidden_warning(client, catalog) -> None:
    text = client.post("/selection/remove", data={"ids": ["sensor.ui_hand", "sensor.ui_wp"]}).text
    assert 'class="confirm-check"' in text and "filter-chip" not in text.split("Von Hand gewählt")[1]
    assert "data-manual-missing hidden" in text  # das Popup zeigt den Hinweis ohne Neuladen
    missing = client.post("/selection/remove", data={"ids": ["sensor.ui_hand", "sensor.ui_wp"], "confirmed": "1"})
    assert missing.status_code == 400 and "data-manual-missing>" in missing.text.replace(" hidden", "")


def test_popup_response_has_no_topnav_but_the_confirmation_page(client, catalog) -> None:
    html = client.post("/selection/remove?popup=1", data={"one": "sensor.ui_wp"}).text
    assert "page-sticky-header" not in html and "Aus der Auswahl entfernen?" in html and 'class="page' in html


def test_overview_has_the_confirm_dialog_and_the_script_handles_the_three_actions() -> None:
    from _paths import APP, template_text

    html = template_text("selection_overview.html")
    assert 'id="sel-confirm"' in html and "page-sel" in html and "page-head-wide" not in html
    js = (APP / "static" / "js" / "pages" / "selection_overview.js").read_text()
    assert "remove|exclude|include" in js and "getAttribute('formaction')" in js and "popup" in js
