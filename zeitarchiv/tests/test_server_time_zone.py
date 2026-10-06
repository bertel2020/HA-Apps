"""Datumsangaben der Oberfläche entstehen in der Serverzone, nicht der des Browsers.

Auslöser: Server in Deutschland, Browser in Portugal (eine Stunde dahinter). Das
Monatsfenster beginnt am 01.10. 00:00 Berlin = 30.09. 23:00 im Browser, das
Jahresfenster am 01.01. 00:00 Berlin = 31.12. 23:00. Die Oberfläche zeigte
„September" und das Vorjahr, obwohl die Werte stimmten.
"""
import json
import subprocess

import pytest

from _paths import ADDON

JS = ADDON / "app" / "static" / "js"
OKT_BERLIN = 1790805600  # 2026-10-01 00:00 Europe/Berlin
JAN_BERLIN = 1767222000  # 2026-01-01 00:00 Europe/Berlin


def _node(tz: str, body: str, lang: str = "de"):
    skript = f"""
    global.window = global;
    global.document = {{documentElement: {{lang: '{lang}', dataset: {{tz: 'Europe/Berlin'}}}}}};
    {(JS / "server-time.js").read_text()}
    {(JS / "i18n.js").read_text()}
    {(JS / "table-compute.js").read_text()}
    console.log(JSON.stringify({body}));
    """
    ergebnis = subprocess.run(
        ["node", "-e", skript], env={"TZ": tz, "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin"},
        capture_output=True, text=True, check=True,
    )
    return json.loads(ergebnis.stdout)


@pytest.mark.parametrize("browser_tz", ["Europe/Berlin", "Europe/Lisbon", "America/New_York", "Asia/Tokyo"])
def test_labels_zeigen_den_zeitraum_des_servers_in_jeder_browserzone(browser_tz):
    jahr = _node(browser_tz, f"TableCompute.resolveLabel('{{jahr}}', {JAN_BERLIN})")
    monat = _node(browser_tz, f"TableCompute.resolveLabel('{{monat}} {{tag}}.{{monat_nr}}.', {OKT_BERLIN})")
    assert jahr == "2026"
    assert monat == "Oktober 01.10."


def test_englische_platzhalter_loesen_wie_die_deutschen_auf():
    paare = [("year", "jahr"), ("year_short", "jahr_kurz"), ("quarter", "quartal"), ("month", "monat"),
             ("month_short", "monat_kurz"), ("month_no", "monat_nr"), ("week", "woche"), ("day", "tag"),
             ("decade", "dekade")]
    englisch = " ".join("{" + en + "}" for en, _ in paare)
    deutsch = " ".join("{" + de + "}" for _, de in paare)
    assert _node("UTC", f"TableCompute.resolveLabel('{englisch}', {OKT_BERLIN})") == \
        _node("UTC", f"TableCompute.resolveLabel('{deutsch}', {OKT_BERLIN})")
    # Tippfehler bleiben sichtbar stehen
    assert _node("UTC", f"TableCompute.resolveLabel('{{yaer}}', {OKT_BERLIN})") == "{yaer}"


def test_einfuegehilfe_setzt_den_platzhalter_in_der_sprache_der_oberflaeche():
    token = "TableCompute.LABEL_VARIABLES.map(v => v[0])"
    assert _node("UTC", token)[:2] == ["jahr", "jahr_kurz"]
    assert _node("UTC", token, lang="en")[:2] == ["year", "year_short"]


def test_cutoff_texte_nutzen_die_serverzone():
    text = _node("Europe/Lisbon", f"TableCompute.comparisonElapsedTimeText({OKT_BERLIN}, 0, 'month')")
    assert text == "01.10., 00:00 Uhr"


def test_parts_liefert_serverzone_und_wochentag():
    p = _node("Europe/Lisbon", f"ServerTime.parts({OKT_BERLIN})")
    assert p == {"year": 2026, "month": 10, "day": 1, "hour": 0, "minute": 0, "weekday": 4}


def test_unbekannte_zone_faellt_auf_browser_zurueck():
    skript = f"""
    global.window = global;
    global.document = {{documentElement: {{dataset: {{tz: 'Nicht/Existent'}}}}}};
    {(JS / "server-time.js").read_text()}
    console.log(JSON.stringify([ServerTime.zone === undefined, ServerTime.parts(0).year]));
    """
    out = subprocess.run(["node", "-e", skript], env={"TZ": "UTC", "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin"},
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [True, 1970]


def test_seiten_tragen_die_serverzone_und_laden_den_helfer(client):
    html = client.get("/").text
    assert 'data-tz="' in html
    assert "js/server-time.js" in html


def test_kalenderauswahl_zerlegt_keine_serverzeitstempel_in_der_browserzone():
    quelle = (JS / "calendar-picker.js").read_text()
    for muster in ("new Date().getFullYear", "today.getFullYear", "d.getFullYear"):
        assert muster not in quelle, muster
