"""Lovelace-Karte: Dashboard-Bausteine (/api/card/elements und /api/card/element, Roadmap 1.48)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app import card_elements
from app.api_routes import API_CAPABILITIES
from app.card_routes import SPEC_VERSION, create_card_router

AUTH = {"Authorization": "Bearer geheim"}


class _Langzeit:
    def __init__(self) -> None:
        self.bodies: list = []

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
    assert [item["key"] for item in body["elements"]] == ["energie.zeitraeume"]
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
