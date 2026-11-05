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


RECORDS = {
    "unit": "kWh", "since": "2025-01-01", "days": 400, "span": 400, "enough": True,
    "records": [{"key": "erzeugung", "label": "Stärkster Erzeugungstag", "value": 61.2, "date": "2026-06-14", "entity_id": "sensor.pv", "offset": -144}],
    "kenntage": {"available": ["fast_autark"], "groups": {"fast_autark": "eigenversorgung"}, "thresholds": {"autark": 90}, "today": "2026-11-05",
                 "years": [{"year": 2026, "fields": {}}, {"year": 2025, "fields": {}}, {"year": 2024, "fields": {}}]},
}


class _Langzeit:
    def __init__(self) -> None:
        self.bodies: list = []
        self.changes: list = []

    def compute_records(self, config: dict) -> dict:
        return RECORDS

    def compute_changes(self, config: dict, body, limit: int = 8) -> dict:
        self.changes.append((body.mode, body.at, limit))
        return {"unit": "kWh", "items": []}

    def compute_months(self, config: dict) -> dict:
        return MONTHS

    def compute_table(self, config: dict, body) -> dict:
        self.bodies.append(body)
        return {"columns": [], "rows": [], "vorjahr": body.vorjahr}


class _Energie:
    def __init__(self) -> None:
        self.langzeit = _Langzeit()
        self.flows: list = []
        self.tiles: list = []
        self.heatmaps: list = []

    def compute_flow(self, config: dict, range_key: str, offset: int, **_kwargs) -> dict:
        self.flows.append((range_key, offset))
        return {
            "verbraucher_breakdown": [
                {"name": "Zähler", "value": 9.0, "kind": "meter"}, {"name": "Herd", "value": 4.0, "kind": "entity"},
                {"name": "Grundlast", "value": 5.0},
            ],
            "versorgung_breakdown": [{"name": "PV", "value": 8.0}, {"name": "Netz", "value": 3.0}, {"name": "Speicher", "value": -1.0}],
            "window_start": 1, "window_end": 2,
        }

    def compute_period_comparison(self, config: dict, range_key: str, offset: int, current: dict, read_cache) -> tuple[dict, dict, dict]:
        kpi = {"erzeugung": 21.3, "verbrauch": 30.0, "netzbezug": 16.0, "einspeisung": 4.1, "autarkie": 46.7, "eigenverbrauch": 80.8,
               "speicher_netto": 1.5, "speicher_laden": 4.0, "speicher_entladen": 2.5, "speicher_soc": 55.0, "speicher_soc_now": 72.0}
        before = {**kpi, "autarkie": 44.2, "eigenverbrauch": None}
        compare = {"erzeugung": {"pct": 12.0, "abs": 2.3}, "verbrauch": {"pct": None, "abs": None}, "netzbezug": {"pct": -7.0, "abs": -1.2}, "einspeisung": {"pct": 31.0, "abs": 1.0}, "speicher_netto": {"pct": None, "abs": 0.8}}
        return compare, kpi, before

    def compute_tile(self, config: dict, key: str, range_key: str, offset: int, read_cache) -> dict:
        self.tiles.append(key)
        if key in ("netzbezug", "einspeisung"):
            return {"series": [{"name": key, "values": [1.0, None, 3.0]}]}
        return {"series": [{"values": [1.0, 2.0, None]}, {"values": [0.5, None, None]}]}

    def compute_heatmap(self, config: dict) -> dict:
        self.heatmaps.append("week")
        return {"rows": [{"label": "Mo", "hours": [1.0] * 24}], "max_value": 1.0}

    def compute_heatmap_weekday(self, config: dict, range_key: str, offset: int) -> dict:
        self.heatmaps.append((range_key, offset))
        return {"rows": [{"label": "Mo", "hours": [2.0] * 24}], "max_value": 2.0}


class _Index:
    snapshot: dict | None = None

    def get_heatmap_weekday_snapshot(self, range_key: str):
        return self.snapshot


@pytest.fixture(autouse=True)
def _weather_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.wetter_routes._load_config", lambda index: {})


@pytest.fixture
def energie(monkeypatch: pytest.MonkeyPatch) -> _Energie:
    service = _Energie()
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=_Index(), energie=service))
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
    assert [item["key"] for item in body["elements"]] == [
        "energie.zeitraeume", "energie.matrix", "energie.monatsvergleich", "energie.anteile", "energie.kennzahlen", "energie.rekorde",
        "energie.kenntage", "energie.veraenderungen", "energie.heatmap",
    ]
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

    def _row_specs(self, config: dict) -> list[dict]:
        return [{"key": "temperature", "label": "Temperatur außen"}, {"key": "precip_mm", "label": "Niederschlag"}]

    def compute_tiles(self, now_local=None, at=None) -> list[dict]:
        return [
            {"key": "temperature", "label": "Temperatur", "unit": "°C", "decimals": 1, "kind": "standard", "last": 8.5, "today": {"max": 10.0, "min": 2.0}, "yesterday": None},
            {"key": "precip_mm", "label": "Niederschlag", "unit": "mm", "decimals": 1, "kind": "counter", "last": 3.0, "today": {"sum": 3.0}, "yesterday": {"sum": 1.0}},
        ]

    def compute_table(self, body) -> dict:
        self.bodies.append(body)
        return {"columns": [], "rows": [], "vorjahr": body.vorjahr}

    def compute_matrix(self, metric: str, mode: str = "mm"):
        return None if metric == "precip" else {"metric": metric, "years": [2026], "min": 0, "max": 1, "cells": [], "missing": []}

    def compute_monthly(self, metric: str, mode: str = "mm"):
        return None if metric == "precip" else {"metric": metric, "unit": "°C", "years": {}, "avg": [], "counts": []}


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


def test_the_monthly_comparisons_are_charts(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=object(), energie=energie, wetter=_Wetter()))
    monkeypatch.setattr("app.wetter_routes.is_wetter_configured", lambda index: True)
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["energie.monatsvergleich"]["kind"] == "chart" and listed["wetter.monatsvergleich"]["metrics"] == ["temperature", "precip", "sun"]
    energy = client.get("/api/card/element", headers=AUTH, params={"key": "energie.monatsvergleich", "role": "netzbezug", "years": 2}).json()
    assert energy["renderer"] == "energie.monatsvergleich" and energy["kind"] == "chart" and energy["months"]["years"] == [2026, 2025]
    weather = client.get("/api/card/element", headers=AUTH, params={"key": "wetter.monatsvergleich", "metric": "sun"}).json()
    assert weather["renderer"] == "wetter.monatsvergleich" and weather["monthly"]["metric"] == "sun"
    # Ohne zugeordneten Niederschlag gibt es keinen Regen-Vergleich: 409 mit Grund.
    assert client.get("/api/card/element", headers=AUTH, params={"key": "wetter.monatsvergleich", "metric": "precip"}).status_code == 409
    assert client.get("/api/card/element", headers=AUTH, params={"key": "wetter.monatsvergleich", "metric": "x"}).status_code == 400


def test_the_weather_year_overview_is_a_chart_with_its_own_metrics(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=object(), energie=energie, wetter=_Wetter()))
    monkeypatch.setattr("app.wetter_routes.is_wetter_configured", lambda index: True)
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["wetter.matrix"]["metrics"] == ["avg", "max", "min", "precip", "sun"]
    body = client.get("/api/card/element", headers=AUTH, params={"key": "wetter.matrix", "metric": "max"}).json()
    assert body["renderer"] == "wetter.matrix" and body["kind"] == "chart" and body["matrix"]["metric"] == "max"
    # Eine Kennzahl, die nur der Monatsvergleich kennt, fällt auf die erste der Jahresübersicht zurück.
    assert client.get("/api/card/element", headers=AUTH, params={"key": "wetter.matrix", "metric": "temperature"}).json()["metric"] == "avg"
    assert client.get("/api/card/element", headers=AUTH, params={"key": "wetter.matrix", "metric": "precip"}).status_code == 409


def test_the_share_ring_sends_only_the_leaves_or_the_positive_supply(energie: _Energie) -> None:
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["energie.anteile"]["ranges"] == ["day", "month", "year"] and listed["energie.anteile"]["sides"] == ["verbrauch", "versorgung"]
    use = client.get("/api/card/element", headers=AUTH, params={"key": "energie.anteile", "range": "year", "offset": -1}).json()
    assert use["renderer"] == "energie.anteile" and use["side"] == "verbrauch"
    assert [item["name"] for item in use["items"]] == ["Herd", "Grundlast"]  # ohne die Zählersumme
    assert energie.flows[-1] == ("year", -1)
    supply = client.get("/api/card/element", headers=AUTH, params={"key": "energie.anteile", "side": "versorgung"}).json()
    assert [item["name"] for item in supply["items"]] == ["PV", "Netz"]  # ohne den negativen Speicher
    # Die Woche gibt es beim Ring nicht: sie fällt auf den Monat zurück.
    client.get("/api/card/element", headers=AUTH, params={"key": "energie.anteile", "range": "week"})
    assert energie.flows[-1] == ("month", 0)


def test_the_load_profile_uses_the_cache_for_the_running_period_only(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    week = client.get("/api/card/element", headers=AUTH, params={"key": "energie.heatmap", "range": "week"}).json()
    assert week["range"] == "week" and energie.heatmaps == ["week"]
    _Index.snapshot = {"grid": {"rows": [], "max_value": 0, "cached": True}}
    try:
        cached = client.get("/api/card/element", headers=AUTH, params={"key": "energie.heatmap", "range": "month"}).json()
        assert cached["heatmap"]["cached"] is True and energie.heatmaps == ["week"]
        previous = client.get("/api/card/element", headers=AUTH, params={"key": "energie.heatmap", "range": "month", "offset": -2}).json()
        assert previous["heatmap"]["max_value"] == 2.0 and energie.heatmaps[-1] == ("month", -2)
    finally:
        _Index.snapshot = None
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.heatmap", "offset": 1}).status_code == 422
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.heatmap", "range": "decade"}).status_code == 400


def test_the_energy_key_figures_carry_value_change_and_a_sparkline(energie: _Energie) -> None:
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["energie.kennzahlen"]["kind"] == "kpi" and [v["key"] for v in listed["energie.kennzahlen"]["values"]] == ["erzeugung", "verbrauch", "netzbezug", "einspeisung", "autarkie", "eigenverbrauch"]
    body = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": "erzeugung,netzbezug,unbekannt"}).json()
    assert body["renderer"] == "energie.kennzahlen" and body["range"] == "month"
    items = {item["key"]: item for item in body["items"]}
    assert list(items) == ["erzeugung", "netzbezug"]  # Unbekanntes fällt weg, die Reihenfolge der Wahl bleibt
    assert items["erzeugung"]["value"] == 21.3 and items["erzeugung"]["pct"] == 12.0 and items["erzeugung"]["better"] == "up"
    assert items["netzbezug"]["pct"] == -7.0 and items["netzbezug"]["better"] == "down"
    assert items["erzeugung"]["spark"] == [1.5, 2.0, None]  # Summe der Zeilen, Lücke bleibt Lücke
    assert items["netzbezug"]["spark"] == [1.0, None, 3.0]
    assert body["compare_label"]


def test_the_sparkline_can_be_left_out_and_costs_nothing_then(energie: _Energie) -> None:
    client = _client()
    body = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "sparkline": "false"}).json()
    assert all(item["spark"] == [] for item in body["items"]) and energie.tiles == []
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": "a b"}).status_code == 400
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": ",".join(["erzeugung"] * 9)}).status_code == 400


def test_the_weather_key_figures_offer_the_set_up_values_and_filter_them(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_elements, "CONTEXT", card_elements.Context(index=_Index(), energie=energie, wetter=_Wetter()))
    monkeypatch.setattr("app.wetter_routes.is_wetter_configured", lambda index: True)
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["wetter.kennzahlen"]["values"] == [{"key": "temperature", "label": "Temperatur außen"}, {"key": "precip_mm", "label": "Niederschlag"}]
    everything = client.get("/api/card/element", headers=AUTH, params={"key": "wetter.kennzahlen"}).json()
    assert [tile["key"] for tile in everything["tiles"]] == ["temperature", "precip_mm"] and everything["renderer"] == "wetter.kennzahlen"
    chosen = client.get("/api/card/element", headers=AUTH, params={"key": "wetter.kennzahlen", "values": "precip_mm"}).json()
    assert [tile["key"] for tile in chosen["tiles"]] == ["precip_mm"]


def test_the_long_term_blocks_are_listed_with_their_options(energie: _Energie) -> None:
    listed = {item["key"]: item for item in _client().get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert listed["energie.rekorde"]["kind"] == "kpi" and not listed["energie.rekorde"]["modes"]
    assert listed["energie.kenntage"]["kind"] == "table" and listed["energie.kenntage"]["modes"] and listed["energie.kenntage"]["year_limit"]
    assert listed["energie.veraenderungen"]["modes"] and listed["energie.veraenderungen"]["limit"] and not listed["energie.veraenderungen"]["year_limit"]


def test_the_records_send_only_what_the_tiles_show(energie: _Energie) -> None:
    body = _client().get("/api/card/element", headers=AUTH, params={"key": "energie.rekorde"}).json()
    assert body["renderer"] == "energie.rekorde" and body["enough"] is True and body["since"] == "2025-01-01"
    assert body["records"] == [{"key": "erzeugung", "label": "Stärkster Erzeugungstag", "value": 61.2, "date": "2026-06-14"}]  # kein Verweis in die App


def test_the_key_days_are_cut_to_the_chosen_years_and_count_until_today_by_default(energie: _Energie) -> None:
    client = _client()
    body = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kenntage", "years": 2}).json()
    assert [entry["year"] for entry in body["kenntage"]["years"]] == [2026, 2025] and body["ytd"] is True
    everything = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kenntage", "years": 0, "mode": "komplett"}).json()
    assert len(everything["kenntage"]["years"]) == 3 and everything["ytd"] is False


def test_the_changes_follow_mode_day_and_the_number_of_rows(energie: _Energie) -> None:
    client = _client()
    client.get("/api/card/element", headers=AUTH, params={"key": "energie.veraenderungen"})
    client.get("/api/card/element", headers=AUTH, params={"key": "energie.veraenderungen", "mode": "komplett", "at": "2026-08-12", "limit": 3})
    assert energie.langzeit.changes == [("bis_jetzt", None, 8), ("komplett", "2026-08-12", 3)]
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.veraenderungen", "limit": 0}).status_code == 422
    assert client.get("/api/card/element", headers=AUTH, params={"key": "energie.veraenderungen", "limit": 21}).status_code == 422


def test_the_quotas_change_in_percentage_points_and_are_not_part_of_the_default(energie: _Energie) -> None:
    client = _client()
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    values = {v["key"]: v for v in listed["energie.kennzahlen"]["values"]}
    assert values["erzeugung"]["default"] is True and values["autarkie"]["default"] is False and "speicher" not in values  # kein Speicher eingerichtet
    default = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen"}).json()
    assert [item["key"] for item in default["items"]] == ["erzeugung", "verbrauch", "netzbezug", "einspeisung"]
    body = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": "eigenverbrauch,autarkie"}).json()
    items = {item["key"]: item for item in body["items"]}
    assert list(items) == ["eigenverbrauch", "autarkie"]  # die Reihenfolge der Wahl bleibt
    assert items["autarkie"]["value"] == 46.7 and items["autarkie"]["unit"] == "%" and items["autarkie"]["abs"] == 2.5 and items["autarkie"]["pct"] is None
    assert items["autarkie"]["delta_unit"] and items["autarkie"]["better"] == "up" and items["autarkie"]["spark"] == []
    assert items["eigenverbrauch"]["abs"] is None  # ohne Vorperiode keine Veränderung


def test_the_storage_is_offered_only_with_a_storage_and_shows_balance_and_charge(energie: _Energie, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    without = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": "speicher"}).json()
    assert [item["key"] for item in without["items"]] == ["erzeugung", "verbrauch", "netzbezug", "einspeisung"]  # unbekannt fällt auf die Vorgabe zurück
    monkeypatch.setattr(card_elements, "_energie_config", lambda ctx: {"erzeuger": [], "speicher": [{"name": "Akku"}]})
    listed = {item["key"]: item for item in client.get("/api/card/elements", headers=AUTH).json()["elements"]}
    assert "speicher" in [v["key"] for v in listed["energie.kennzahlen"]["values"]]
    now = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": "speicher", "range": "day"}).json()["items"][0]
    assert now["value"] == 1.5 and now["abs"] == 0.8 and now["better"] is None and now["spark"] == []
    assert [(line["value"], line["unit"]) for line in now["lines"]] == [(72.0, "%"), (4.0, "kWh"), (2.5, "kWh")]  # Ladestand jetzt
    past = client.get("/api/card/element", headers=AUTH, params={"key": "energie.kennzahlen", "values": "speicher", "range": "day", "offset": -1}).json()["items"][0]
    assert past["lines"][0]["value"] == 55.0  # in einer vergangenen Periode der Durchschnitt
