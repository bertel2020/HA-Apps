"""Lovelace-Karte: Dashboard-Bausteine (/api/card/elements und /api/card/element, Roadmap 1.48)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app import card_elements
from app.api_routes import API_CAPABILITIES
from app.card_routes import SPEC_VERSION, create_card_router

AUTH = {"Authorization": "Bearer geheim"}


MONTHS = {
    "current": {"year": 2026, "month": 11, "day": 5}, "years": [2026, 2025, 2024], "unit": "kWh",
    "roles": [
        {"key": "erzeugung", "label": "Erzeugung", "better": "up", "entity_id": None, "partial_start": None, "flags": {}, "unchecked": 0,
         "values": {"2026": [1] * 12, "2025": [2] * 12, "2024": [3] * 12}},
        {"key": "netzbezug", "label": "Netzbezug", "better": "down", "entity_id": None, "partial_start": None, "flags": {}, "unchecked": 0,
         "values": {"2026": [4] * 12, "2025": [5] * 12, "2024": [6] * 12}},
    ],
}


class _Langzeit:
    def __init__(self) -> None:
        self.bodies: list = []

    def compute_months(self, config: dict) -> dict:
        return MONTHS

    def compute_table(self, config: dict, body) -> dict:
        self.bodies.append(body)
        return {"columns": [], "rows": [], "vorjahr": body.vorjahr}


class _Energie:
    def __init__(self) -> None:
        self.langzeit = _Langzeit()


@pytest.fixture
def energie(monkeypatch: pytest.MonkeyPatch) -> _Energie:
    service = _Energie()
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=object(), energie=service))
    monkeypatch.setattr(card_elements, "_energie_config", lambda ctx: {"erzeuger": []})
    return service


def _client() -> TestClient:
    def check_auth(authorization, request_id="-", integration_version=None) -> None:
        if authorization != "Bearer geheim":
            raise HTTPException(status_code=401, detail="nein")

    app = FastAPI()
    app.include_router(create_card_router(object(), "Europe/Berlin", check_auth, lambda **kw: {}))
    return TestClient(app)


def test_the_capability_is_announced() -> None:
    assert "card_elements" in API_CAPABILITIES


def test_both_routes_need_the_token() -> None:
    client = _client()
    assert client.get("/api/card/elements").status_code == 401
    assert client.get("/api/card/element", params={"key": "energie.zeitraeume"}).status_code == 401


def test_only_set_up_elements_are_listed(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    body = client.get("/api/card/elements", headers=AUTH).json()
    assert body["spec_version"] == SPEC_VERSION
    assert [item["key"] for item in body["elements"]] == ["energie.zeitraeume", "energie.matrix"]
    assert body["elements"][0]["columns"] == ["monat", "vormonat", "jahr"]
    monkeypatch.setattr(card_elements, "_energie_config", lambda ctx: None)
    assert client.get("/api/card/elements", headers=AUTH).json()["elements"] == []


def test_an_element_gives_the_numbers_of_the_dashboard(energie: _Energie) -> None:
    client = _client()
    response = client.get(
        "/api/card/element", headers=AUTH,
        params={"key": "energie.zeitraeume", "columns": "monat,jahr", "vorjahr": "false", "mode": "komplett", "at": "2026-11-01"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["spec_version"] == SPEC_VERSION
    assert body["renderer"] == "energie.zeitraeume" and body["kind"] == "table"
    assert body["table"]["vorjahr"] is False
    assert body["env"] == {"timezone": "Europe/Berlin"}
    sent = energie.langzeit.bodies[0]
    assert (sent.columns, sent.vorjahr, sent.mode, sent.at) == (["monat", "jahr"], False, "komplett", "2026-11-01")


def test_an_element_that_is_not_set_up_says_why(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_elements, "_energie_config", lambda ctx: None)
    response = _client().get("/api/card/element", headers=AUTH, params={"key": "energie.zeitraeume"})
    assert response.status_code == 409
    assert "Energiedashboard" in response.json()["detail"]


def test_unknown_keys_and_bad_options_are_refused(energie: _Energie) -> None:
    client = _client()
    assert client.get("/api/card/element", headers=AUTH, params={"key": "gibt.es.nicht"}).status_code == 404
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.zeitraeume", "columns": "woche"}).status_code == 400
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.zeitraeume", "mode": "x"}).status_code == 400
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.zeitraeume", "at": "gestern"}).status_code == 400
    assert energie.langzeit.bodies == []


def test_the_matrix_sends_only_the_chosen_role_and_years(energie: _Energie) -> None:
    client = _client()
    body = client.get("/api/card/element", headers=AUTH, params={"key": "energie.matrix", "role": "netzbezug", "years": 2}).json()
    assert body["renderer"] == "energie.matrix" and body["role"] == "netzbezug"
    months = body["months"]
    assert months["years"] == [2026, 2025]
    assert [role["key"] for role in months["roles"]] == ["netzbezug"]
    assert sorted(months["roles"][0]["values"]) == ["2025", "2026"]
    # Ohne Angabe die erste Rolle und höchstens 10 Jahre; 0 heißt alle.
    plain = client.get("/api/card/element", headers=AUTH, params={"key": "energie.matrix", "years": 0}).json()
    assert plain["role"] == "erzeugung" and plain["months"]["years"] == [2026, 2025, 2024]
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.matrix", "role": "gas"}).status_code == 400
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.matrix", "years": 99}).status_code == 422


class _Wetter:
    def __init__(self) -> None:
        self.bodies: list = []

    def compute_table(self, body) -> dict:
        self.bodies.append(body)
        return {"columns": [], "rows": [], "vorjahr": body.vorjahr}


def test_the_weather_table_is_listed_and_built_with_its_group(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    wetter = _Wetter()
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=object(), energie=energie, wetter=wetter))
    monkeypatch.setattr("app.wetter_routes.is_wetter_configured", lambda index: True)
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["wetter.zeitraeume"]["groups"] == ["basis", "wind", "klima"]
    body = client.get("/api/card/element", headers=AUTH, params={"key": "wetter.zeitraeume", "columns": "heute,jahr", "group": "wind"}).json()
    assert body["renderer"] == "wetter.zeitraeume" and body["group"] == "wind"
    assert wetter.bodies[0].columns == ["heute", "jahr"]
    assert client.get("/api/card/element", headers=AUTH, params={"key": "wetter.zeitraeume", "group": "x"}).status_code == 400


def test_the_weather_table_is_not_offered_without_a_weather_dashboard(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=object(), energie=energie, wetter=_Wetter()))
    monkeypatch.setattr("app.wetter_routes.is_wetter_configured", lambda index: False)
    client = _client()
    assert "wetter.zeitraeume" not in [item["key"] for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]]
    response = client.get("/api/card/element", headers=AUTH, params={"key": "wetter.zeitraeume"})
    assert response.status_code == 409 and "Wetterdashboard" in response.json()["detail"]
