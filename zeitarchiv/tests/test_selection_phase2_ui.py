"""Entitäts-Assistent Phase 2 (ROADMAP 1.33) über die ganze App (``client``): Freitext,
Ausschluss aufheben beim Übernehmen, Muster-Vorschläge, Ausschließen / Wieder aufnehmen,
Massenaktionen, Muster entfernen, Statusseite mit Import-Hinweis, Katalog-Push."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import selection_catalog, selection_jobs

DATA_DIR = Path(os.environ["ZEITARCHIV_DATA_DIR"])

PLUGS = [
    {"entity_id": f"sensor.p2_plug_{i}_leistung", "name": f"P2 Steckdose {i}", "device_class": "power",
     "unit": "W", "device": "P2 Steckdosen"}
    for i in range(5)
]
ENTITIES = [
    *PLUGS,
    {"entity_id": "sensor.p2_plug_9_zaehler", "name": "P2 Zähler fremd", "selected": True, "origin": "manual",
     "via": [{"type": "pattern", "value": "sensor.p2_*"}]},
    {"entity_id": "sensor.p2_aus_id", "name": "P2 Ausgeschlossen ID", "device_class": "power", "excluded": True,
     "excluded_by": [{"type": "entity"}]},
    {"entity_id": "sensor.p2_aus_muster", "name": "P2 Ausgeschlossen Muster", "device_class": "power", "excluded": True,
     "excluded_by": [{"type": "pattern", "value": "*_aus_muster"}]},
    {"entity_id": "sensor.p2_hand", "name": "P2 Handeintrag", "selected": True, "origin": "manual",
     "via": [{"type": "entity"}]},
    {"entity_id": "sensor.p2_za", "name": "P2 Assistent", "selected": True, "origin": "assistant", "topic": "energie",
     "via": [{"type": "assistant", "topic": "energie", "mode": "ids"}, {"type": "entity"}]},
    {"entity_id": "person.p2_anna", "name": "P2 Anna"},
]
PATTERNS = [
    {"pattern": "sensor.p2_za_*", "origin": "assistant", "topic": "energie", "added_ts": 1.0, "count": 1},
    {"pattern": "sensor.p2_*", "origin": "manual", "count": 9},
]
LOG = [
    {"id": "log-ex", "ts": 1000.0, "source": "manual_ui", "action": "exclude", "entity_ids": ["sensor.p2_aus_id"],
     "count": 1, "status": "applied", "results": {"sensor.p2_aus_id": {"result": "ok"}}},
    {"id": "log-pat", "ts": 900.0, "source": "assistant", "action": "add_patterns", "entity_ids": [],
     "patterns": ["sensor.p2_za_*"], "count": 1, "status": "applied", "topic": "energie",
     "results": {"sensor.p2_za_*": {"result": "ok"}}},
]


def _save(**overrides):
    args = {"label": {"id": "za", "name": "Zeitarchiv"}, "log": LOG, "ledger_count": 1, "entry_id": "EINTRAG",
            "phase1": True, "patterns": PATTERNS, "phase2": True, **overrides}
    selection_catalog.save(DATA_DIR, ENTITIES, **args)


@pytest.fixture()
def catalog():
    for name in (selection_catalog.FILENAME, selection_jobs.FILENAME):
        (DATA_DIR / name).unlink(missing_ok=True)
    _save()
    yield
    for name in (selection_catalog.FILENAME, selection_jobs.FILENAME):
        (DATA_DIR / name).unlink(missing_ok=True)


@pytest.fixture()
def german_again(client):
    yield
    client.post("settings/language", data={"language": "de"})


def _jobs():
    return selection_jobs.all_jobs(DATA_DIR)


def _field(html: str, name: str, value: str) -> str:
    return html.split(f'name="{name}" value="{value}"', 1)[1].split(">", 1)[0]


# ---- Freitext ----------------------------------------------------------------------------

def test_a_synonym_shows_the_topic(client, catalog) -> None:
    html = client.get("/selection?q=Strom").text
    assert "Verstanden als: Energie" in html and 'value="Strom"' in html
    assert 'name="topic" value="energie"' in html


def test_fulltext_search_is_a_pseudo_topic_without_preselection(client, catalog) -> None:
    html = client.get("/selection?q=p2 steckdose").text
    assert "Suche: „p2 steckdose“" in html and "Treffer in Name/Gerät/Bereich/Plattform" in html
    assert 'name="topic" value="suche"' in html and 'name="q" value="p2 steckdose"' in html
    assert "checked" not in _field(html, "entity_id", "sensor.p2_plug_0_leistung")
    assert "Anwesenheits-Entitäten" not in html
    assert "Anwesenheits-Entitäten" in client.get("/selection?q=anna").text
    assert "Nichts gefunden" in client.get("/selection?q=gibtsnirgends").text


def test_apply_from_the_search(client, catalog) -> None:
    form = {"topic": "suche", "q": "p2 steckdose", "mode": "ids", "entity_id": ["sensor.p2_plug_1_leistung"]}
    preview = client.post("/selection/apply", data=form)
    assert preview.status_code == 200 and "Suche „p2 steckdose“" in preview.text
    foreign = client.post("/selection/apply", data={**form, "entity_id": "sensor.p2_hand", "confirmed": "1"})
    assert foreign.status_code == 400 and "Kein Treffer dieser Suche" in foreign.text
    done = client.post("/selection/apply", data={**form, "confirmed": "1"}, follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert job["topic"] is None and job["entity_ids"] == ["sensor.p2_plug_1_leistung"] and job["mode"] == "ids"


# ---- Ausschluss aufheben beim Übernehmen --------------------------------------------------

def test_id_exclusions_are_offered_as_their_own_group(client, catalog) -> None:
    html = client.get("/selection?topic=energie").text
    assert "Ausschluss aufheben und übernehmen" in html
    field = _field(html, "unexclude_id", "sensor.p2_aus_id")
    assert "disabled" not in field and "checked" not in field
    assert 'name="entity_id" value="sensor.p2_aus_id"' not in html
    assert "disabled" in _field(html, "entity_id", "sensor.p2_aus_muster")


def test_apply_with_unexclude_names_them_and_sends_unexclude_ids(client, catalog) -> None:
    form = {"topic": "energie", "mode": "label", "entity_id": ["sensor.p2_plug_2_leistung"],
            "unexclude_id": ["sensor.p2_aus_id"]}
    preview = client.post("/selection/apply", data=form).text
    assert "wird der Ausschluss aufgehoben" in preview and "P2 Ausgeschlossen ID" in preview
    assert 'name="unexclude_id" value="sensor.p2_aus_id"' in preview
    assert client.post("/selection/apply", data={**form, "confirmed": "1"}, follow_redirects=False).status_code == 303
    job = _jobs()[0]
    assert job["entity_ids"] == ["sensor.p2_plug_2_leistung", "sensor.p2_aus_id"]
    assert job["unexclude_ids"] == ["sensor.p2_aus_id"]
    assert selection_jobs.public(job)["unexclude_ids"] == ["sensor.p2_aus_id"]


def test_pattern_exclusions_stay_locked(client, catalog) -> None:
    form = {"topic": "energie", "unexclude_id": "sensor.p2_aus_muster", "confirmed": "1"}
    response = client.post("/selection/apply", data=form)
    assert response.status_code == 400 and "über ein Muster ausgeschlossen" in response.text and _jobs() == []


def test_phase1_integration_gets_no_unexclude(client, catalog) -> None:
    _save(patterns=None, phase2=False)
    html = client.get("/selection?topic=energie").text
    assert "Ausschluss aufheben und übernehmen" not in html
    response = client.post("/selection/apply", data={"topic": "energie", "unexclude_id": "sensor.p2_aus_id", "confirmed": "1"})
    assert response.status_code == 400 and _jobs() == []


# ---- Muster-Vorschläge --------------------------------------------------------------------

def test_pattern_suggestions_with_preview_and_confirmation(client, catalog) -> None:
    html = client.get("/selection?q=p2 steckdose").text
    assert "Muster-Vorschläge" in html and "sensor.p2_plug_*" in html
    assert "Textentitäten werden von Mustern nie erfasst" in html and "automatisch archiviert" in html
    assert "Davon nicht in dieser Ansicht: 1" in html  # sensor.p2_plug_9_zaehler
    form = {"pattern": "sensor.p2_plug_*", "topic": "suche", "q": "p2 steckdose"}
    preview = client.post("/selection/patterns", data=form)
    assert preview.status_code == 200 and "Muster übernehmen?" in preview.text and _jobs() == []
    assert "6 passende Entitäten" in preview.text
    done = client.post("/selection/patterns", data={**form, "confirmed": "1"}, follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert (job["action"], job["mode"], job["patterns"], job["entity_ids"]) == ("add_patterns", "ids", ["sensor.p2_plug_*"], [])


@pytest.mark.parametrize("form,text", [
    ({"pattern": "*", "topic": "suche", "q": "p2 steckdose"}, "gehört nicht zu den Vorschlägen"),
    ({"pattern": "sensor.p2_plug_*", "topic": "suche", "q": "anna"}, "gehört nicht zu den Vorschlägen"),
    ({"pattern": "sensor.p2_plug_*", "topic": "quatsch"}, "Unbekanntes Thema"),
])
def test_pattern_add_refuses_what_is_not_a_candidate(client, catalog, form, text) -> None:
    response = client.post("/selection/patterns", data={**form, "confirmed": "1"})
    assert response.status_code == 400 and text in response.text and _jobs() == []


def test_active_patterns_are_shown_as_active(client, catalog) -> None:
    _save(patterns=[{"pattern": "sensor.p2_plug_*", "origin": "manual", "count": 6}])
    html = client.get("/selection?q=p2 steckdose").text
    assert "schon aktiv" in html and 'name="pattern" value="sensor.p2_plug_*"' not in html
    response = client.post("/selection/patterns", data={"pattern": "sensor.p2_plug_*", "topic": "suche",
                                                        "q": "p2 steckdose", "confirmed": "1"})
    assert response.status_code == 400 and "schon aktiv" in response.text


# ---- Ausschließen / Wieder aufnehmen / Massenaktionen ---------------------------------------

def test_overview_offers_exclude_include_and_the_pattern_table(client, catalog) -> None:
    html = client.get("/selection/overview").text
    assert 'formaction="/selection/exclude"' in html and 'formaction="/selection/include"' in html
    assert 'name="ids" value="sensor.p2_plug_9_zaehler"' in html  # nur über Muster: ausschließbar
    assert "Der Ausschluss kommt von diesem Muster" in html and "*_aus_muster" in html
    assert "sensor.p2_za_*" in html and 'action="/selection/patterns/remove"' in html and "in HA ändern" in html
    assert "Ausgeschlossen: 1" in html and "Muster hinzugefügt" in html and "In Zeitarchiv geändert" in html


def test_exclude_a_pattern_selected_row(client, catalog) -> None:
    preview = client.post("/selection/exclude", data={"one": "sensor.p2_plug_9_zaehler"})
    assert preview.status_code == 200 and "Ausschließen?" in preview.text
    assert "über ein Muster, einen Bereich" in preview.text and 'name="confirm_manual"' in preview.text
    missing = client.post("/selection/exclude", data={"one": "sensor.p2_plug_9_zaehler", "confirmed": "1"})
    assert missing.status_code == 400 and "Bitte bestätige" in missing.text and _jobs() == []
    done = client.post("/selection/exclude", data={"one": "sensor.p2_plug_9_zaehler", "confirmed": "1",
                                                   "confirm_manual": "1"}, follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert (job["source"], job["action"], job["mode"], job["entity_ids"]) == (
        "manual_ui", "exclude", "auto", ["sensor.p2_plug_9_zaehler"])


def test_bulk_exclude_skips_rows_that_do_not_fit(client, catalog) -> None:
    ids = ["sensor.p2_za", "sensor.p2_aus_id"]
    preview = client.post("/selection/exclude", data={"ids": ids})
    assert "1 markierte Zeilen passen nicht" in preview.text and 'name="confirm_manual"' not in preview.text
    client.post("/selection/exclude", data={"ids": ids, "confirmed": "1"}, follow_redirects=False)
    assert _jobs()[0]["entity_ids"] == ["sensor.p2_za"]
    none = client.post("/selection/exclude", data={"ids": ["sensor.p2_aus_id"], "confirmed": "1"})
    assert none.status_code == 400 and "Keine der markierten" in none.text


def test_include_lifts_only_id_exclusions(client, catalog) -> None:
    done = client.post("/selection/include", data={"one": "sensor.p2_aus_id", "confirmed": "1"}, follow_redirects=False)
    assert done.status_code == 303 and _jobs()[0]["action"] == "include"
    blocked = client.post("/selection/include", data={"one": "sensor.p2_aus_muster", "confirmed": "1"})
    assert blocked.status_code == 400 and "kommt vom Muster „*_aus_muster“" in blocked.text and len(_jobs()) == 1


def test_bulk_remove_skips_and_more_than_200_are_refused(client, catalog) -> None:
    preview = client.post("/selection/remove", data={"ids": ["sensor.p2_za", "sensor.p2_plug_9_zaehler"]})
    assert preview.status_code == 200 and "1 markierte Zeilen passen nicht" in preview.text
    many = [f"sensor.viel_{i}" for i in range(201)]
    for path in ("/selection/remove", "/selection/exclude", "/selection/include"):
        response = client.post(path, data={"ids": many, "confirmed": "1"})
        assert response.status_code == 400 and "Höchstens 200" in response.text
    assert _jobs() == []


def test_phase1_integration_cannot_exclude(client, catalog) -> None:
    _save(patterns=None, phase2=False)
    assert 'formaction="/selection/exclude"' not in client.get("/selection/overview").text
    response = client.post("/selection/exclude", data={"one": "sensor.p2_hand", "confirmed": "1", "confirm_manual": "1"})
    assert response.status_code == 400 and "neuere Version" in response.text and _jobs() == []


def test_remove_patterns_only_for_assistant_patterns(client, catalog) -> None:
    preview = client.post("/selection/patterns/remove", data={"pattern": "sensor.p2_za_*"})
    assert preview.status_code == 200 and "Muster entfernen?" in preview.text
    done = client.post("/selection/patterns/remove", data={"pattern": "sensor.p2_za_*", "confirmed": "1"},
                       follow_redirects=False)
    assert done.status_code == 303
    job = _jobs()[0]
    assert (job["action"], job["patterns"], job["topic"]) == ("remove_patterns", ["sensor.p2_za_*"], "energie")
    manual = client.post("/selection/patterns/remove", data={"pattern": "sensor.p2_*", "confirmed": "1"})
    assert manual.status_code == 400 and "in Home Assistant eingetragen" in manual.text
    unknown = client.post("/selection/patterns/remove", data={"pattern": "sensor.nix_*", "confirmed": "1"})
    assert unknown.status_code == 400 and len(_jobs()) == 1


def test_undo_of_exclude_and_of_a_pattern(client, catalog) -> None:
    client.post("/selection/undo", data={"target": "log-ex", "confirmed": "1"}, follow_redirects=False)
    client.post("/selection/undo", data={"target": "log-pat", "confirmed": "1"}, follow_redirects=False)
    jobs = {job["undo_of"]: job for job in _jobs()}
    assert (jobs["log-ex"]["action"], jobs["log-ex"]["entity_ids"]) == ("include", ["sensor.p2_aus_id"])
    assert (jobs["log-pat"]["action"], jobs["log-pat"]["patterns"]) == ("remove_patterns", ["sensor.p2_za_*"])
    assert jobs["log-pat"]["source"] == "undo"


# ---- Statusseite ---------------------------------------------------------------------------

def test_status_page_offers_the_history_import_after_an_add(client, catalog) -> None:
    job = selection_jobs.create(DATA_DIR, source="assistant", action="add", mode="label", entity_ids=["sensor.p2_plug_0_leistung"])
    url = f"/selection/jobs/{job['id']}/status"
    assert client.get(url).json() == {"status": "pending"}
    assert client.get(url, headers={"Accept": "application/json"}).json() == {"status": "pending"}
    pending = client.get(url, headers={"Accept": "text/html"}).text
    assert "Auftrag: Hinzufügen" in pending and "/import?tab=ha" not in pending
    selection_jobs.ack(DATA_DIR, job["id"], "applied", {"sensor.p2_plug_0_leistung": {"result": "ok", "mode": "label"}})
    page = client.get(url, headers={"Accept": "text/html", "X-Ingress-Path": "/api/hassio_ingress/abc"}).text
    assert "Verlauf aus Home Assistant nachladen" in page and 'href="/api/hassio_ingress/abc/import?tab=ha"' in page
    overview = client.get(f"/selection/overview?job={job['id']}").text
    assert "Verlauf aus Home Assistant nachladen" not in overview and f"/selection/jobs/{job['id']}/status" in overview


def test_status_page_has_no_import_hint_after_exclude(client, catalog) -> None:
    job = selection_jobs.create(DATA_DIR, source="manual_ui", action="exclude", entity_ids=["sensor.p2_hand"])
    selection_jobs.ack(DATA_DIR, job["id"], "applied", {"sensor.p2_hand": {"result": "ok"}})
    page = client.get(f"/selection/jobs/{job['id']}/status", headers={"Accept": "text/html"}).text
    assert "Auftrag: Ausschließen" in page and "Verlauf aus Home Assistant nachladen" not in page
    assert client.get("/selection/jobs/gibtsnicht/status", headers={"Accept": "text/html"}).status_code == 404


# ---- Sprache und Katalog-Push --------------------------------------------------------------

def test_phase2_pages_render_in_english(client, catalog, german_again) -> None:
    client.post("/settings/language", data={"language": "en"})
    search = client.get("/selection?q=p2 steckdose").text
    assert "What do you want to archive?" in search and "Pattern suggestions" in search
    assert "Understood as: Energy" in client.get("/selection?q=strom").text
    overview = client.get("/selection/overview").text
    assert "Include again …" in overview and "Exclude …" in overview and "Pattern added" in overview
    confirm = client.post("/selection/exclude", data={"one": "sensor.p2_za"}).text
    assert "Exclude?" in confirm


def test_catalog_push_with_patterns_switches_on_phase2(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.selection_routes import create_catalog_router

    app = FastAPI()
    app.include_router(create_catalog_router(tmp_path, lambda *args: None))
    api = TestClient(app)
    body = {"entities": [{"entity_id": "sensor.a", "via": [{"type": "pattern", "value": "sensor.a*", "assistant": True}]}],
            "log": [], "ledger_count": 0, "patterns": [{"pattern": "sensor.a*", "origin": "assistant", "count": 1}]}
    assert api.post("/api/catalog", json=body).status_code == 200
    stored = selection_catalog.load(tmp_path)
    assert stored["phase1"] is True and stored["phase2"] is True
    assert stored["patterns"][0]["pattern"] == "sensor.a*" and stored["entities"][0]["via"][0]["assistant"] is True
    assert api.post("/api/catalog", json={"entities": [], "log": []}).status_code == 200
    assert selection_catalog.load(tmp_path)["phase2"] is False
    assert api.post("/api/catalog", json={"entities": [], "patterns": "kaputt"}).status_code == 422
