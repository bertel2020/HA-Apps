"""Zahlen-, Datums- und Uhrzeitformat (de-DE / en-GB / en-US), getrennt von der Sprache."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import formats
from app.formatting import format_int, format_size, format_time, format_timestamp, format_value, parse_localized_number

MOMENT = datetime(2026, 10, 7, 8, 41, 7, tzinfo=ZoneInfo("Europe/Berlin"))
AFTERNOON = datetime(2026, 10, 7, 15, 5, 9, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture
def locale():
    """Setzt das Format der „Anfrage“ und stellt es danach wieder zurück."""
    token = None

    def use(name: str) -> None:
        nonlocal token
        token = formats.current_format.set(name)

    yield use
    if token is not None:
        formats.current_format.reset(token)


@pytest.mark.parametrize(
    ("setting", "header", "language", "expected"),
    [
        # feste Auswahl gewinnt immer
        ("en-US", "de-DE,de;q=0.9", "de", "en-US"),
        ("de-DE", "en-GB", "en", "de-DE"),
        # auto: Region im Header
        ("auto", "en-GB,en;q=0.9", "en", "en-GB"),
        ("auto", "en-US,en;q=0.9", "en", "en-US"),
        ("auto", "en-AU", "en", "en-GB"),
        ("auto", "en-CA", "en", "en-US"),
        ("auto", "de-AT,de;q=0.9", "de", "de-DE"),
        # auto: nur Sprache → britisch, weil Tag vor Monat und 24-Stunden-Uhr zum Rest der App passen
        ("auto", "en", "en", "en-GB"),
        # auto: unbekannte Sprache im Header → nach Oberflächensprache
        ("auto", "fr-FR,fr;q=0.9", "en", "en-GB"),
        ("auto", "fr-FR", "de", "de-DE"),
        ("auto", "", "de", "de-DE"),
        (None, "en-US", "en", "en-US"),
        ("unbekannt", "en-GB", "en", "en-GB"),
    ],
)
def test_the_format_follows_the_setting_then_the_browser_then_the_language(setting, header, language, expected) -> None:
    assert formats.resolve_format(setting, header, language) == expected


def test_every_choice_is_a_known_format_or_auto() -> None:
    assert set(formats.FORMAT_CHOICES) == {"auto", *formats.FORMATS}


@pytest.mark.parametrize(
    ("name", "integer", "decimal", "size"),
    [("de-DE", "5.570.121", "1.234,5", "1,5 MB"), ("en-GB", "5,570,121", "1,234.5", "1.5 MB"), ("en-US", "5,570,121", "1,234.5", "1.5 MB")],
)
def test_numbers_use_the_separators_of_the_format(locale, name, integer, decimal, size) -> None:
    locale(name)
    assert format_int(5570121) == integer
    assert format_value(1234.5) == decimal
    assert format_size(int(1.5 * 1024 * 1024)) == size


@pytest.mark.parametrize("name", ["de-DE", "en-GB", "en-US"])
def test_a_formatted_number_parses_back_in_the_same_format(locale, name) -> None:
    locale(name)
    assert parse_localized_number(format_value(1234.5)) == 1234.5


@pytest.mark.parametrize(
    ("name", "date", "day_month", "clock", "evening"),
    [
        ("de-DE", "07.10.2026", "07.10.", "08:41:07", "15:05:09"),
        ("en-GB", "07/10/2026", "07/10", "08:41:07", "15:05:09"),
        ("en-US", "10/07/2026", "10/07", "8:41:07 AM", "3:05:09 PM"),
    ],
)
def test_dates_and_times_use_the_pattern_of_the_format(locale, name, date, day_month, clock, evening) -> None:
    locale(name)
    tz = ZoneInfo("Europe/Berlin")
    assert format_timestamp(MOMENT.timestamp(), tz) == date
    assert formats.format_day_month(MOMENT) == day_month
    assert format_time(MOMENT.timestamp(), tz) == clock
    assert format_time(AFTERNOON.timestamp(), tz) == evening
    assert formats.format_datetime(MOMENT) == f"{date} {clock}"


def test_the_short_datetime_has_a_comma_and_no_seconds(locale) -> None:
    locale("de-DE")
    assert formats.format_datetime(MOMENT, seconds=False, comma=True) == "07.10.2026, 08:41"
    locale("en-US")
    assert formats.format_datetime(MOMENT, seconds=False, comma=True) == "10/07/2026, 8:41 AM"


def test_midnight_and_noon_read_as_twelve_in_a_twelve_hour_format(locale) -> None:
    locale("en-US")
    assert formats.format_clock(datetime(2026, 1, 1, 0, 5)) == "12:05 AM"
    assert formats.format_clock(datetime(2026, 1, 1, 12, 5)) == "12:05 PM"


@pytest.mark.parametrize(("name", "label"), [("de-DE", "7. Okt"), ("en-GB", "7 Okt"), ("en-US", "Okt 7")])
def test_the_compact_day_label_orders_day_and_month_per_format(locale, name, label) -> None:
    locale(name)
    assert formats.day_month_label(7, "Okt") == label


# --- Seiten: Einstellung, Browser-Header und <html data-format> ----------------------------------


@pytest.fixture
def auto_again(client):
    yield
    client.post("/settings/regional-format", data={"regional_format": "auto"})


def test_automatic_format_follows_the_browser(client, auto_again) -> None:
    client.post("/settings/regional-format", data={"regional_format": "auto"})
    assert 'data-format="en-GB"' in client.get("/uebersicht", headers={"Accept-Language": "en-GB,en;q=0.9"}).text
    assert 'data-format="en-US"' in client.get("/uebersicht", headers={"Accept-Language": "en-US,en;q=0.9"}).text
    assert 'data-format="de-DE"' in client.get("/uebersicht", headers={"Accept-Language": "de-DE,de;q=0.9"}).text


def test_a_fixed_format_overrides_the_browser(client, auto_again) -> None:
    response = client.post("/settings/regional-format", data={"regional_format": "en-US"})
    assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"
    assert 'data-format="en-US"' in client.get("/uebersicht", headers={"Accept-Language": "de-DE"}).text


def test_unknown_format_is_rejected(client) -> None:
    assert client.post("/settings/regional-format", data={"regional_format": "fr-FR"}).status_code == 400


def test_settings_page_offers_the_format_choice(client, auto_again) -> None:
    html = client.get("/settings").text
    assert "regional-format-input" in html and "English UK (1,234.5 · 07/10/2026)" in html


def test_scripts_read_the_format_instead_of_a_fixed_locale() -> None:
    from _paths import APP

    for path in (APP / "static" / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if path.name in {"i18n.js", "import.js"}:
            continue
        assert "'de-DE'" not in text, f"{path.name} formatiert mit festem de-DE"


@pytest.mark.parametrize(
    ("seconds", "german", "english"),
    [
        (12, "12 Sek.", "12 sec"),
        (45 * 60, "45 Min.", "45 min"),
        (3600, "1 Std.", "1 hr"),
        (3600 + 5 * 60, "1 Std. 5 Min.", "1 hr 5 min"),
        (86400, "1 Tag", "1 day"),
        (2 * 86400 + 3 * 3600, "2 Tage 3 Std.", "2 days 3 hr"),
    ],
)
def test_durations_are_translated(seconds, german, english) -> None:
    from app import i18n
    from app.formatting import format_uptime

    for language, expected in (("de", german), ("en", english)):
        token = i18n.current_language.set(language)
        try:
            assert format_uptime(seconds) == expected
        finally:
            i18n.current_language.reset(token)
