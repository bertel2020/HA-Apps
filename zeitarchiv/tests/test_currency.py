"""Währung (EUR, GBP, USD, CHF): Einstellung, Home-Assistant-Standard und Schreibweise der Beträge."""

from __future__ import annotations

import json

import pytest

from _paths import APP
from app import currency, formats


@pytest.fixture
def money():
    """Setzt Währung und Format der „Anfrage“ und stellt beides danach wieder zurück."""
    tokens = []

    def use(code: str, locale: str) -> None:
        tokens.append((currency.current_currency, currency.current_currency.set(code)))
        tokens.append((formats.current_format, formats.current_format.set(locale)))

    yield use
    for var, token in reversed(tokens):
        var.reset(token)


@pytest.mark.parametrize(
    ("code", "german", "english"),
    [
        ("EUR", "2,52 €", "€2.52"),
        ("GBP", "2,52 £", "£2.52"),
        ("USD", "2,52 $", "$2.52"),
        ("CHF", "2,52 CHF", "CHF 2.52"),
    ],
)
def test_the_symbol_stands_after_the_amount_in_german_and_before_it_in_english(money, code, german, english) -> None:
    money(code, "de-DE")
    assert currency.format_money(2.516) == german
    money(code, "en-GB")
    assert currency.format_money(2.516) == english
    money(code, "en-US")
    assert currency.format_money(2.516) == english


def test_negative_amounts_carry_the_minus_in_front_of_everything(money) -> None:
    money("GBP", "en-GB")
    assert currency.format_money(-1234.5) == "-£1,234.50"
    money("EUR", "de-DE")
    assert currency.format_money(-1234.5) == "-1.234,50 €"


def test_an_amount_that_rounds_to_zero_has_no_sign_and_none_is_a_dash(money) -> None:
    money("EUR", "de-DE")
    assert currency.format_money(-0.004) == "0,00 €"
    assert currency.format_money(None) == "—"


@pytest.fixture
def home_assistant(monkeypatch):
    """Ersetzt die Abfrage der HA-Konfiguration und leert den Cache."""
    answers: list = []
    calls: list = []

    def fetch():
        calls.append(1)
        return answers[0] if answers else None

    monkeypatch.setattr(currency, "_fetch_ha_currency", fetch)
    monkeypatch.setattr(currency, "_ha_cache", None)
    return answers, calls


def test_automatic_takes_the_currency_from_home_assistant(home_assistant) -> None:
    answers, _ = home_assistant
    answers.append("GBP")
    assert currency.resolve_currency("auto") == "GBP"
    assert currency.resolve_currency(None) == "GBP"


def test_a_fixed_choice_does_not_ask_home_assistant(home_assistant) -> None:
    answers, calls = home_assistant
    answers.append("GBP")
    assert currency.resolve_currency("CHF") == "CHF"
    assert calls == []


def test_without_an_answer_it_stays_euro_and_does_not_ask_again_at_once(home_assistant) -> None:
    _, calls = home_assistant
    assert currency.resolve_currency("auto") == "EUR"
    assert currency.resolve_currency("auto") == "EUR"
    assert len(calls) == 1


def test_the_answer_is_cached(home_assistant) -> None:
    answers, calls = home_assistant
    answers.append("USD")
    currency.resolve_currency("auto")
    currency.resolve_currency("auto")
    assert len(calls) == 1


def test_unsupported_home_assistant_currencies_are_ignored(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"currency": "SEK"}).encode()

    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    monkeypatch.setattr(currency.urllib.request, "urlopen", lambda *a, **k: Response())
    assert currency._fetch_ha_currency() is None


def test_without_a_supervisor_nothing_is_requested(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert currency._fetch_ha_currency() is None


def test_every_choice_is_a_known_currency_or_auto() -> None:
    assert set(currency.CURRENCY_CHOICES) == {"auto", *currency.CURRENCIES}


# --- Seiten --------------------------------------------------------------------------------------


@pytest.fixture
def auto_again(client, home_assistant):
    yield
    client.post("/settings/currency", data={"currency": "auto"})


def _data_currency(html: str) -> dict:
    marker = "data-currency='"
    start = html.index(marker) + len(marker)
    return json.loads(html[start : html.index("'", start)])


def test_the_page_carries_symbol_and_position(client, auto_again) -> None:
    client.post("/settings/currency", data={"currency": "GBP"})
    info = _data_currency(client.get("/uebersicht", headers={"Accept-Language": "en-GB"}).text)
    assert info == {"code": "GBP", "symbol": "£", "spaced": False, "position": "before", "short": "p"}
    info = _data_currency(client.get("/uebersicht", headers={"Accept-Language": "de-DE"}).text)
    assert info["position"] == "after"


def test_automatic_without_home_assistant_is_euro(client, auto_again) -> None:
    client.post("/settings/currency", data={"currency": "auto"})
    assert _data_currency(client.get("/uebersicht").text)["code"] == "EUR"


def test_the_currency_choice_is_stored_and_reloads_the_page(client, auto_again) -> None:
    response = client.post("/settings/currency", data={"currency": "CHF"})
    assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"


def test_unknown_currency_is_rejected(client) -> None:
    assert client.post("/settings/currency", data={"currency": "SEK"}).status_code == 400


def test_settings_page_offers_the_currency_choice(client, auto_again) -> None:
    html = client.get("/settings", headers={"Accept-Language": "en"}).text
    assert "currency-input" in html and "Swiss franc (CHF)" in html


def test_the_price_fields_name_the_subunit_of_the_currency(client, auto_again) -> None:
    client.post("/settings/currency", data={"currency": "GBP"})
    html = client.get("/energiedashboard/setup", headers={"Accept-Language": "en"}).text
    assert "p/kWh" in html and "Ct/kWh" not in html


def test_no_fixed_euro_sign_is_left_in_scripts_and_the_report() -> None:
    for path in (APP / "static" / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "' €'" not in text and '" €"' not in text, f"{path.name} schreibt das Euro-Zeichen fest"
    report = (APP / "templates" / "_energiedashboard_report.html").read_text(encoding="utf-8")
    assert " €" not in report
