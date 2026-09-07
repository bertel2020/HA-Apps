"""Ausreißer-Erkennung: Zähler und Schalter bekommen sie gar nicht erst.

Der Grund liegt in der Kennzahl selbst. `analyze_raw_rows_page()`
(storage/cleanup.py) misst den Sprung zum Vorwert am MITTELWERT DER BETRÄGE im
Zeitraum, nicht am Vorwert:

    jump_percent = abs(value - previous_value) / baseline * 100

Daraus folgen zwei strukturelle Fälle, die keine Fehlbedienung sind, sondern
Eigenschaften des Typs — und die deshalb wie bei 3.1 gar nicht erst angeboten
werden statt sie zu melden.
"""

from __future__ import annotations

import re

from app.storage.cleanup import analyze_raw_rows_page
from app.storage.index import effective_outlier_threshold, outlier_detection_applies

TZ_NAME = "Europe/Berlin"


def _quote(werte, schwelle, modus="standard"):
    """Anteil markierter Werte an allen — dieselbe Rechnung wie die App."""
    from zoneinfo import ZoneInfo

    reihen = [(float(i * 60), float(v)) for i, v in enumerate(werte)]
    ergebnis = analyze_raw_rows_page(
        lambda: iter(reihen), filter_="all", page=1, page_size=1,
        gap_threshold_minutes=None, outlier_threshold_percent=float(schwelle),
        tz=ZoneInfo(TZ_NAME), decimals="auto", outlier_mode=modus,
    )
    c = ergebnis["counts"]
    return c["outliers"] / c["all"] * 100


def test_a_switch_would_mark_most_of_its_own_values(client) -> None:
    """Warum die Sperre für Schalter bleibt, auch nach der Umstellung auf den
    Schnitt der letzten fünf Werte.

    Bei 0/1-Werten ist dieser Schnitt der Anteil der Einsen im Fenster, und
    jeder Wert liegt zwangsläufig weit daneben — gemessen an einer Reihe, die
    im Takt wechselt, und an einer, die selten an ist. Es gibt keine
    Einstellung, bei der das nützlich würde.
    """
    wechsel = [i % 2 for i in range(200)]
    selten_an = ([0] * 19 + [1]) * 10

    for schwelle in (5, 10, 25, 50):
        assert _quote(wechsel, schwelle) > 90, f"Wechsel, Schwelle {schwelle}"
        assert _quote(selten_an, schwelle) > 20, f"selten an, Schwelle {schwelle}"

    # Selbst bei der höchsten wählbaren Schwelle bleibt es die Hälfte.
    assert _quote(wechsel, 100) > 45


def test_a_counter_ignores_normal_growth_but_catches_real_faults() -> None:
    """Zähler standen zunächst auf derselben Sperrliste wie Schalter — das war
    ein Fehlschluss, und dieser Test hält fest, warum.

    Gemessen war nur, dass NORMALE Zuwächse nichts auslösen. Das ist erwünscht,
    nicht blind: genau so soll ein Zähler sich verhalten. Die Fehler, die
    Zähler tatsächlich haben, löst die Erkennung sehr wohl aus.
    """
    stand = [45000.0 + i * 12 for i in range(200)]
    for schwelle in (5, 10, 25, 50, 100):
        assert _quote(stand, schwelle) == 0.0, f"normaler Zuwachs, Schwelle {schwelle}"

    faktor_zehn = list(stand)
    faktor_zehn[100] *= 10
    reset = [45000.0 + i * 12 for i in range(100)] + [i * 12.0 for i in range(100)]
    for schwelle in (5, 10, 25, 50, 100):
        assert _quote(faktor_zehn, schwelle) > 0, f"Faktor-10-Fehlmessung, Schwelle {schwelle}"
        assert _quote(reset, schwelle) > 0, f"Rücksprung auf 0, Schwelle {schwelle}"


def test_the_counter_sensitivity_is_coarse_and_that_is_the_honest_limit() -> None:
    """Was von der Sache übrig bleibt: der Prozentsatz bezieht sich auf den
    mittleren ZÄHLERSTAND, nicht auf den Zuwachs. 5 % von 45.000 sind 2.250 —
    ein Fehlwert darunter bleibt unmarkiert. Das gehört in den Hilfetext (dort
    steht es), ist aber kein Grund, die Einstellung zu entziehen."""
    klein = [45000.0 + i * 12 for i in range(200)]
    klein[100] += 2250          # +5 % des Stands
    assert _quote(klein, 2) > 0
    assert _quote(klein, 5) == 0.0


def test_a_normal_sensor_keeps_the_setting() -> None:
    """Gegenprobe: für `standard` bleibt die Erkennung, was sie war — sonst
    hätte der Guard das Feature abgeschafft statt es zu begrenzen."""
    assert outlier_detection_applies("standard") is True
    assert effective_outlier_threshold("standard", "25") == "25"
    assert effective_outlier_threshold("standard", "off") == "off"


def test_the_stored_value_no_longer_takes_effect_for_switches() -> None:
    """Nicht nur das Formularfeld: die Schwelle gilt für Schalter als „aus",
    auch wenn im Index noch ein alter Wert steht. Sonst würde ein Bestand aus
    der Zeit vor dem Guard weiter jeden Zustandswechsel markieren."""
    assert outlier_detection_applies("switch") is False
    assert effective_outlier_threshold("switch", "5") == "off"
    assert effective_outlier_threshold("switch", "25") == "off"


def test_counters_keep_the_setting() -> None:
    """Die Gegenprobe zur Korrektur: Zähler behalten ihre Schwelle."""
    assert outlier_detection_applies("counter") is True
    assert effective_outlier_threshold("counter", "25") == "25"


def test_the_form_says_why_instead_of_greying_out_silently(client) -> None:
    """Ein deaktiviertes Feld ohne Begründung ist nur die halbe Auskunft."""
    from app.formatting import OUTLIER_BLOCKED_REASONS
    from app.main import index

    index.get_or_create_entity("binary_sensor.guard", "binary_sensor", "measurement", None)
    html = client.get("/entities/binary_sensor.guard/config").text
    assert OUTLIER_BLOCKED_REASONS["switch"] in html
    # Der Auswahlknopf ist weg, nicht nur überschrieben.
    assert 'id="outlier-threshold-btn"' not in html
    assert 'name="outlier_threshold"' not in html

    index.get_or_create_entity("sensor.guard_normal", "sensor", "measurement", "°C")
    html = client.get("/entities/sensor.guard_normal/config").text
    assert 'id="outlier-threshold-btn"' in html
    assert OUTLIER_BLOCKED_REASONS["switch"] not in html


def test_a_posted_value_for_a_blocked_type_is_ignored_not_rejected(client) -> None:
    """Das Feld ist deaktiviert, aber darauf allein darf sich der Server nicht
    verlassen. Verworfen statt abgelehnt: die Einstellung wirkt für diesen Typ
    ohnehin nicht, ein HTTP 400 würde ein Problem behaupten, wo keines ist."""
    from app.main import index

    index.get_or_create_entity("binary_sensor.guard_post", "binary_sensor", "measurement", None)
    vorher = index.get_entity("binary_sensor.guard_post")["outlier_threshold"]
    antwort = client.post(
        "/entities/binary_sensor.guard_post/config", data={"outlier_threshold": "5"}
    )
    assert antwort.status_code == 200
    assert index.get_entity("binary_sensor.guard_post")["outlier_threshold"] == vorher


def test_the_entity_table_shows_that_it_does_not_apply(client) -> None:
    """Eine Zahl, die nichts bewirkt, neben Zahlen, die etwas bewirken, ist
    irreführender als ein sichtbares „gilt hier nicht"."""
    from app.main import index

    index.get_or_create_entity("binary_sensor.guard_tbl", "binary_sensor", "measurement", None)
    index.get_or_create_entity("sensor.guard_tbl", "sensor", "measurement", "°C")

    vorher = index.get_setting("entities_columns", "")
    index.set_setting("entities_columns", "outlier_threshold")
    try:
        html = client.get("/entities-table", params={"search": "guard_tbl"}).text
        zellen = re.findall(
            r'<td[^>]*class="[^"]*col-outlier-threshold[^"]*"[^>]*>(.*?)</td>', html
        )
    finally:
        index.set_setting("entities_columns", vorher)

    # Sortiert nach entity_id: erst der Schalter, dann der normale Sensor.
    assert [z.strip() for z in zellen] == ["—", "25 %"]


# --- Stufe 2: die Quote dort zeigen, wo die Schwelle eingestellt wird --------


def _fuelle(entity_id: str, n: int = 200, sprung_jede: int = 20) -> None:
    """Werte mit regelmäßigen, deutlichen Sprüngen — genug, dass die Erkennung
    etwas findet, ohne von der Größe der Testdaten abzuhängen."""
    import math
    import time as _time

    from app.main import ingestion_service
    from app.storage.ingestion import IngestEvent

    basis = _time.time() - n * 300
    for i in range(n):
        wert = 20 + math.sin(i / 5) * 2 + (12 if i % sprung_jede == 0 else 0)
        ingestion_service.ingest(IngestEvent(
            entity_id=entity_id, ts=basis + i * 300, value=round(wert, 2),
            domain="sensor", state_class="measurement", unit="°C",
            friendly_name=None, event_id=f"{entity_id}-{i}",
        ))


def _rate(entity_id: str):
    from app import cleanup_stats
    from app.main import index

    return cleanup_stats.outlier_rate(index, index.get_entity(entity_id))


def test_a_rate_computed_for_another_threshold_is_not_shown(client) -> None:
    """Die wichtigste Zusage dieser Stufe. Der Cache der Bereinigungsseite
    kennt nur EINE Zahl je Entität — die zur Schwelle gehört, mit der zuletzt
    gezählt wurde. Sie neben einer inzwischen geänderten Schwelle anzuzeigen
    wäre schlimmer als gar keine Quote: die Zahl sähe aus, als beschriebe sie
    die aktuelle Einstellung."""
    from app import cleanup_stats
    from app.main import DATA_DIR, TZ, index
    from datetime import datetime

    eid = "sensor.rate_wechsel"
    _fuelle(eid)
    cleanup_stats.alltime_counts(DATA_DIR, index, TZ, index.get_entity(eid), datetime.now(TZ))
    assert _rate(eid) is not None

    index.set_config(eid, outlier_threshold="5")
    assert _rate(eid) is None, "Quote der alten Schwelle wird weiter angezeigt"

    cleanup_stats.alltime_counts(
        DATA_DIR, index, TZ, index.get_entity(eid), datetime.now(TZ), force=True
    )
    assert _rate(eid) is not None


def test_an_old_cache_entry_without_a_threshold_counts_as_unknown(client) -> None:
    """Einträge aus der Zeit vor dieser Änderung tragen das Feld nicht. Sie
    gelten als unbekannt statt als passend — sonst zeigte die erste Anzeige
    nach dem Update eine Zahl an, deren Schwelle niemand kennt."""
    from app.main import index

    eid = "sensor.rate_altbestand"
    _fuelle(eid, n=50)
    index.set_cleanup_alltime_stats(eid, {"all": 50, "outliers": 3})
    assert _rate(eid) is None


def test_no_rate_where_the_detection_does_not_apply(client) -> None:
    """Für Zähler und Schalter gibt es keine Schwelle — also auch keine Quote
    und keinen Knopf, der eine berechnen will."""
    from app.main import index

    index.get_or_create_entity("binary_sensor.rate_aus", "binary_sensor", "measurement", None)
    # Der Cache-Eintrag wird bewusst mitgeliefert: ohne ihn wäre die Zusage
    # schon dadurch erfüllt, dass noch nie jemand gerechnet hat — geprüft
    # werden soll aber, dass die Sperre selbst greift.
    index.set_cleanup_alltime_stats(
        "binary_sensor.rate_aus", {"all": 100, "outliers": 90}, "off"
    )
    assert _rate("binary_sensor.rate_aus") is None

    html = client.get("/entities/binary_sensor.rate_aus/config").text
    assert "Jetzt prüfen" not in html

    # Gegenprobe mit einem normalen Sensor, dessen Schwelle auf "Aus" steht:
    # auch dort gibt es nichts anzuzeigen.
    index.get_or_create_entity("sensor.rate_aus", "sensor", "measurement", "°C")
    index.set_config("sensor.rate_aus", outlier_threshold="off")
    index.set_cleanup_alltime_stats("sensor.rate_aus", {"all": 100, "outliers": 90}, "off")
    assert _rate("sensor.rate_aus") is None


def test_an_entity_without_values_offers_no_invented_zero(client) -> None:
    """0 von 0 ist keine Quote. Die Oberfläche soll dann nichts behaupten."""
    from app.main import index

    index.get_or_create_entity("sensor.rate_leer", "sensor", "measurement", "°C")
    assert _rate("sensor.rate_leer") is None

    # Auch MIT passendem Cache-Eintrag: ein Lauf über eine leere Entität legt
    # {"all": 0} ab, und 0/0 ist keine Quote, sondern eine Division.
    index.set_cleanup_alltime_stats("sensor.rate_leer", {"all": 0, "outliers": 0}, "25")
    assert _rate("sensor.rate_leer") is None


def test_the_button_computes_once_and_shows_the_numbers(client) -> None:
    """Der Vollscan läuft auf Klick, nicht bei jedem Seitenaufbau — und das
    Ergebnis steht danach direkt am Feld, mit deutschem Dezimalkomma."""
    eid = "sensor.rate_knopf"
    _fuelle(eid)

    html = client.get(f"/entities/{eid}/config").text
    assert "Jetzt prüfen" in html
    assert "outlier-rate-value" not in html

    antwort = client.post(f"/entities/{eid}/outlier-rate")
    assert antwort.status_code == 200
    assert "outlier-rate-value" in antwort.text
    quote = antwort.text.split('class="outlier-rate-value')[1].split(">")[1].split("<")[0]
    assert "," in quote and "." not in quote, quote
    # Der Wertänderungsfilter kann Werte verschlucken — die Gesamtzahl ist
    # deshalb nicht zwangsläufig 200, nur größer als die markierten.
    teilung = antwort.text.split('class="outlier-rate-sub"')[1].split("<")[0]
    markiert, gesamt = (int(x) for x in re.findall(r"\d+", teilung)[:2])
    assert 0 < markiert < gesamt


def test_the_cache_is_not_rebuilt_on_every_page_view(client) -> None:
    """Ein Vollscan je Seitenaufruf wäre bei Entitäten mit Millionen Rohwerten
    genau das, wofür der 15-Minuten-Cache existiert."""
    import time as _time
    from datetime import datetime

    from app import cleanup_stats
    from app.main import DATA_DIR, TZ, index

    eid = "sensor.rate_cache"
    _fuelle(eid, n=50)
    cleanup_stats.alltime_counts(DATA_DIR, index, TZ, index.get_entity(eid), datetime.now(TZ))
    erst = index.get_cleanup_alltime_stats(eid)["computed_at"]
    _time.sleep(1.1)

    client.get(f"/entities/{eid}/config")
    cleanup_stats.alltime_counts(DATA_DIR, index, TZ, index.get_entity(eid), datetime.now(TZ))
    assert index.get_cleanup_alltime_stats(eid)["computed_at"] == erst

    cleanup_stats.alltime_counts(
        DATA_DIR, index, TZ, index.get_entity(eid), datetime.now(TZ), force=True
    )
    assert index.get_cleanup_alltime_stats(eid)["computed_at"] > erst


# --- Die Regeln selbst ------------------------------------------------------


def test_a_counter_now_measures_the_increment_not_the_reading() -> None:
    """Der Kern der Umstellung. Ein Zählerstand steigt immer; interessant ist,
    ob sein ZUWACHS aus der Reihe fällt.

    Der alte Bezug (Mittelwert der Stände) machte das unmöglich: an einer
    echten Entität gemessen lag der größte reale Sprung bei 0,0003 % davon,
    die kleinste wählbare Schwelle bei 5 %. Es gab keinen einstellbaren Wert,
    der je ausgelöst hätte.
    """
    normal = [45000.0 + i * 12 for i in range(200)]
    spitze = list(normal)
    for j in range(100, 200):
        spitze[j] += 500       # ein einziges Intervall mit 40-fachem Verbrauch

    for schwelle in (5, 10, 25, 50, 100):
        assert _quote(normal, schwelle, "counter") == 0.0, f"normaler Zuwachs, {schwelle}"
        assert _quote(spitze, schwelle, "counter") > 0, f"Verbrauchsspitze, {schwelle}"

    # Gegenprobe: mit der alten Bezugsgröße bliebe genau diese Spitze stumm.
    assert _quote(spitze, 5) == 0.0


def test_the_standard_rule_follows_the_level_instead_of_the_period_mean() -> None:
    """Was die Umstellung für Standard-Sensoren wirklich ändert — und was
    nicht.

    Sie ÄNDERT: der Bezug ist das aktuelle Niveau statt des Mittelwerts über
    den ganzen Zeitraum. Eine gleichmäßige Drift fällt damit nicht mehr auf,
    sobald sie weit genug vom Zeitraum-Mittel entfernt ist.

    Sie ändert NICHT: der Prozentsatz bleibt relativ zum Niveau. Derselbe
    absolute Sprung zählt auf einer Skala mit hohem Nullpunkt weniger — in
    Kelvin ist ein Sprung von 20 auf 60 Grad nur 13,6 % des Niveaus, in Grad
    Celsius 200 %. Und bei Werten um null wird der Bezug klein und die
    Erkennung empfindlich. Das ist der Preis dieser Rechnung; er steht so auch
    im Hilfetext.
    """
    import math

    sinus = [math.sin(i / 4) * 1.5 for i in range(200)]
    warm = [20 + v for v in sinus]
    warm_mit_sprung = list(warm)
    warm_mit_sprung[100] = 60.0
    assert _quote(warm, 25) == 0.0
    assert _quote(warm_mit_sprung, 25) > 0

    kelvin_mit_sprung = [273.15 + v for v in warm_mit_sprung]
    assert _quote(kelvin_mit_sprung, 25) == 0.0, "Skalenabhängigkeit ist NICHT verschwunden"
    assert _quote(kelvin_mit_sprung, 10) > 0, "eine engere Schwelle findet ihn dort"


def test_the_window_is_the_last_five_values() -> None:
    """Fünf, und zwar die letzten — nicht der ganze Zeitraum. Ein langsam
    driftendes Signal soll nicht dadurch auffällig werden, dass es sich vom
    Mittel eines Jahres entfernt hat."""
    from app.storage.cleanup import OUTLIER_WINDOW

    assert OUTLIER_WINDOW == 5

    drift = [20 + i * 0.5 for i in range(200)]     # gleichmäßiger Anstieg
    assert _quote(drift, 25) == 0.0, "gleichmäßige Drift darf nicht auffallen"


def test_the_mode_comes_from_the_aggregation_type() -> None:
    """Sonst liefe die Zähler-Regel auf Standard-Sensoren oder umgekehrt."""
    from pathlib import Path

    from app import cleanup_stats
    import app.main as main_mod

    erwartet = 'outlier_mode="counter" if entity["aggregation_type"] == "counter" else "standard"'
    for modul in (main_mod, cleanup_stats):
        assert erwartet in Path(modul.__file__).read_text(encoding="utf-8"), modul.__name__


def test_a_counter_that_stands_still_does_not_crash_the_analysis() -> None:
    """Ein Zähler ohne Verbrauch im Intervall liefert Zuwachs 0 — und 0 ist der
    Bezug für den nächsten. Ohne Schutz wäre das eine Division durch null, also
    ein Absturz der ganzen Bereinigungsseite, nicht bloß eine falsche Zahl.

    Der Fall ist der Normalfall, nicht die Ausnahme: nachts verbraucht die
    Wärmepumpe nichts, die PV-Anlage liefert nichts.
    """
    stillstand = [45000.0] * 5 + [45000.0 + i * 12 for i in range(1, 40)]
    stillstand += [stillstand[-1]] * 5          # wieder Stillstand
    stillstand += [stillstand[-1] + i * 12 for i in range(1, 20)]

    for schwelle in (5, 10, 25, 50, 100):
        quote = _quote(stillstand, schwelle, "counter")
        assert 0 <= quote <= 100, schwelle
