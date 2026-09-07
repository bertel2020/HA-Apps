"""Ausreißer-Erkennung: die Regel selbst, die Sperre für Schalter und die
Quote am Formularfeld.

Die Regel misst, um welches VIELFACHE des für die Entität Üblichen ein Wert
danebenliegt (app/storage/cleanup.py, OutlierDetector) — bei Zählern gegen den
üblichen Zuwachs, sonst gegen die übliche Schwankung. Vorher war es ein
Prozentsatz eines Werts; woran das scheiterte, halten die Tests unten fest.
"""

from __future__ import annotations

import re

from app.storage.cleanup import analyze_raw_rows_page
from app.storage.index import effective_outlier_threshold, outlier_detection_applies

TZ_NAME = "Europe/Berlin"


def _quote(werte, schwelle=50, modus="standard"):
    """Anteil markierter Werte an allen — dieselbe Rechnung wie die App."""
    from zoneinfo import ZoneInfo

    reihen = [(float(i * 60), float(v)) for i, v in enumerate(werte)]
    ergebnis = analyze_raw_rows_page(
        lambda: iter(reihen), filter_="all", page=1, page_size=1,
        gap_threshold_minutes=None, outlier_factor=float(schwelle),
        tz=ZoneInfo(TZ_NAME), decimals="auto", outlier_mode=modus,
    )
    c = ergebnis["counts"]
    return c["outliers"] / c["all"] * 100


def test_a_switch_can_never_be_measured_at_all(client) -> None:
    """Warum die Sperre für Schalter bleibt — mit umgekehrter Begründung.

    Unter der Prozentregel markierte ein Schalter fast alle eigenen Werte.
    Unter der Vielfachen-Regel markiert er GAR KEINE, und zwar zwangsläufig:
    der Median eines 0/1-Fensters ist die Mehrheitsklasse, deren Mitglieder
    haben Abweichung 0, und weil die Mehrheit über der Hälfte liegt, ist der
    Median der Abweichungen immer 0. Ein Vielfaches von null gibt es nicht.

    Die Einstellung wäre also folgenlos statt schädlich. Angeboten wird sie
    trotzdem nicht: ein Regler, der nachweislich nie etwas tut, ist eine
    Falschauskunft.
    """
    wechsel = [i % 2 for i in range(200)]
    selten_an = ([0] * 19 + [1]) * 10
    dauer_an = [1] * 150 + [0] + [1] * 49

    for schwelle in (10, 20, 50, 100):
        for name, reihe in (("Wechsel", wechsel), ("selten an", selten_an), ("dauernd an", dauer_an)):
            assert _quote(reihe, schwelle) == 0.0, f"{name}, Schwelle {schwelle}"


def test_a_normal_sensor_keeps_the_setting() -> None:
    """Gegenprobe: für `standard` bleibt die Erkennung, was sie war — sonst
    hätte der Guard das Feature abgeschafft statt es zu begrenzen."""
    assert outlier_detection_applies("standard") is True
    assert effective_outlier_threshold("standard", "50") == "50"
    assert effective_outlier_threshold("standard", "off") == "off"


def test_the_stored_value_no_longer_takes_effect_for_switches() -> None:
    """Nicht nur das Formularfeld: die Schwelle gilt für Schalter als „aus",
    auch wenn im Index noch ein alter Wert steht. Sonst würde ein Bestand aus
    der Zeit vor dem Guard weiter jeden Zustandswechsel markieren."""
    assert outlier_detection_applies("switch") is False
    assert effective_outlier_threshold("switch", "10") == "off"
    assert effective_outlier_threshold("switch", "50") == "off"


def test_counters_keep_the_setting() -> None:
    """Die Gegenprobe zur Korrektur: Zähler behalten ihre Schwelle."""
    assert outlier_detection_applies("counter") is True
    assert effective_outlier_threshold("counter", "50") == "50"


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
        "/entities/binary_sensor.guard_post/config", data={"outlier_threshold": "10"}
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
    assert [z.strip() for z in zellen] == ["—", "50×"]


# --- Stufe 2: die Quote dort zeigen, wo die Schwelle eingestellt wird --------


def _fuelle(entity_id: str, n: int = 200, sprung_jede: int = 50) -> None:
    """Ruhige Kurve mit SELTENEN, sehr großen Sprüngen — die Regel misst
    Vielfache der üblichen Schwankung, ein regelmäßiger Sprung wäre also
    selbst das Übliche und fiele zu Recht nicht auf."""
    import math
    import time as _time

    from app.main import ingestion_service
    from app.storage.ingestion import IngestEvent

    basis = _time.time() - n * 300
    for i in range(n):
        wert = 20 + math.sin(i / 5) * 2 + (200 if i and i % sprung_jede == 0 else 0)
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

    index.set_config(eid, outlier_threshold="10")
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
    index.set_cleanup_alltime_stats("sensor.rate_leer", {"all": 0, "outliers": 0}, "50")
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


def test_a_counter_measures_the_increment_against_the_usual_increment() -> None:
    """Der Kern der Zähler-Regel: Bezug ist der übliche Zuwachs, nicht der
    Stand — und auch nicht der eine vorherige Zuwachs.

    Beide Vorgängerfassungen scheiterten hier. Am mittleren STAND gemessen lag
    der größte reale Sprung einer echten Entität bei 0,0003 % des Bezugs; es
    gab keine wählbare Schwelle, die je ausgelöst hätte. Am VORHERIGEN Zuwachs
    gemessen wurde es umgekehrt: normaler, schwankender Verbrauch erzeugte in
    einer Messung 272 von 400 Markierungen. Der Median der letzten 50 Zuwächse
    ist gegen beides robust.
    """
    normal = [45000.0 + i * 12 for i in range(200)]
    spitze = [45000.0 + i * 12 for i in range(100)]
    spitze += [spitze[-1] + 500 + i * 12 for i in range(1, 101)]   # ein 40-facher Zuwachs

    for schwelle in (10, 20, 50, 100):
        assert _quote(normal, schwelle, "counter") == 0.0, f"normaler Zuwachs, {schwelle}"
    assert _quote(spitze, 20, "counter") > 0

    # Auch unregelmäßiger, aber plausibler Verbrauch bleibt still — das ist
    # der Fall, an dem die Vorgängerfassung zerbrach.
    import random
    zufall = random.Random(4)
    schwankend = [45000.0]
    for _ in range(400):
        schwankend.append(schwankend[-1] + zufall.uniform(0.5, 25))
    assert _quote(schwankend, 50, "counter") == 0.0


def test_the_counter_rule_means_the_same_on_a_new_and_on_an_old_meter() -> None:
    """Der Grund für die ganze Umstellung.

    Ein Prozentsatz des Stands ist nicht linear: 5 % sind bei Stand 12 ganze
    0,6 und bei Stand 1.200.000 volle 60.000 — dieselbe Einstellung toleriert
    beim frischen Zähler nichts und beim alten alles, obwohl beide denselben
    Verbrauch messen. Gegen den üblichen Zuwachs gemessen verschwindet dieser
    Unterschied vollständig.
    """
    def reihe(start: float, ziffernfehler: bool) -> list[float]:
        werte = [start + i * 12 for i in range(200)]
        if ziffernfehler:
            versatz = werte[100] * 9        # 10.123 -> 101.230
            werte = werte[:100] + [w + versatz for w in werte[100:]]
        return werte

    for stand in (12.0, 10123.0, 1_200_000.0):
        assert _quote(reihe(stand, False), 50, "counter") == 0.0, f"gesund, Stand {stand}"
        assert _quote(reihe(stand, True), 50, "counter") > 0, f"Ziffernfehler, Stand {stand}"

    # Und zwar identisch, nicht bloß "auch irgendwas": gleiche Anzahl.
    mengen = {_quote(reihe(stand, True), 50, "counter") for stand in (12.0, 10123.0, 1_200_000.0)}
    assert len(mengen) == 1, mengen


def test_a_counter_decrease_is_left_to_its_own_marking() -> None:
    """Negative Zuwächse gehen weder in den Bezug ein noch werden sie als
    Ausreißer markiert — dafür gibt es die Markierung "Zählerrückgang". Sonst
    stünden an einem Zählerwechsel zwei Markierungen für dieselbe Ursache."""
    reset = [45000.0 + i * 12 for i in range(100)] + [i * 12.0 for i in range(100)]
    assert _quote(reset, 50, "counter") == 0.0


def test_the_standard_rule_is_scale_and_offset_invariant() -> None:
    """Was die Umstellung für Standard-Sensoren bringt: dieselbe Kurve mit
    demselben Fehler ergibt dasselbe Ergebnis, egal auf welcher Skala und um
    welchen Nullpunkt sie liegt.

    Die Prozentfassung konnte das nicht — ein Sprung von 20 auf 60 sind in
    Grad Celsius 200 % des Niveaus, in Kelvin (293 auf 333) nur 13,6 %. Eine
    Schwelle, die für einen Sensor passte, passte für den nächsten nicht.
    """
    import math

    sinus = [math.sin(i / 4) * 1.5 for i in range(200)]
    celsius = [20 + v for v in sinus]
    mit_sprung = list(celsius)
    mit_sprung[100] = 60.0

    varianten = {
        "Celsius": (celsius, mit_sprung),
        "Kelvin": ([273.15 + v for v in celsius], [273.15 + v for v in mit_sprung]),
        "um null": ([v - 20 for v in celsius], [v - 20 for v in mit_sprung]),
        "verzehnfacht": ([v * 10 for v in celsius], [v * 10 for v in mit_sprung]),
    }
    gesund = {name: _quote(a, 20) for name, (a, _b) in varianten.items()}
    krank = {name: _quote(b, 20) for name, (_a, b) in varianten.items()}

    assert set(gesund.values()) == {0.0}, gesund
    assert len(set(krank.values())) == 1, krank
    assert next(iter(krank.values())) > 0


def test_the_windows_are_the_recent_past_not_the_whole_period() -> None:
    """Ein langsam driftendes Signal soll nicht dadurch auffällig werden, dass
    es sich vom Mittel eines Jahres entfernt hat."""
    from app.storage.cleanup import (
        COUNTER_OUTLIER_WINDOW,
        OUTLIER_MIN_VALUES,
        OUTLIER_WINDOW,
    )

    assert (OUTLIER_WINDOW, OUTLIER_MIN_VALUES, COUNTER_OUTLIER_WINDOW) == (15, 5, 50)

    drift = [20 + i * 0.5 for i in range(200)]
    assert _quote(drift, 20) == 0.0, "gleichmäßige Drift darf nicht auffallen"


def test_a_constant_signal_is_skipped_instead_of_guessed() -> None:
    """Die ehrliche Grenze der Regel: sind alle Werte im Fenster gleich, gibt
    es keine übliche Schwankung, an der sich ein Vielfaches messen ließe. Dann
    wird übersprungen statt geraten — auch wenn danach ein Sprung kommt.

    Der Fall ist selten (der Wertänderungsfilter fasst konstante Reihen ohnehin
    zusammen) und steht so im Handbuch."""
    konstant = [20.0] * 40 + [200.0] + [20.0] * 40
    assert _quote(konstant, 10) == 0.0


def test_the_mode_comes_from_one_place_only() -> None:
    """Zwei Kopien dieser Zuordnung, die auseinanderlaufen, waren genau der
    Fehler, den die Vereinheitlichung beseitigt hat."""
    from app import cleanup_stats

    assert cleanup_stats.outlier_mode({"aggregation_type": "counter"}) == "counter"
    assert cleanup_stats.outlier_mode({"aggregation_type": "standard"}) == "standard"
    assert cleanup_stats.outlier_mode({"aggregation_type": "switch"}) == "standard"


def test_a_counter_that_stands_still_does_not_crash_the_analysis() -> None:
    """Ein Zähler ohne Verbrauch im Intervall liefert Zuwachs 0. Wäre 0 der
    Bezug, wäre das eine Division durch null — also ein Absturz der ganzen
    Bereinigungsseite, nicht bloß eine falsche Zahl.

    Der Fall ist der Normalfall, nicht die Ausnahme: nachts verbraucht die
    Wärmepumpe nichts, die PV-Anlage liefert nichts.
    """
    stillstand = [45000.0] * 5 + [45000.0 + i * 12 for i in range(1, 40)]
    stillstand += [stillstand[-1]] * 5
    stillstand += [stillstand[-1] + i * 12 for i in range(1, 20)]

    for schwelle in (10, 20, 50, 100):
        assert _quote(stillstand, schwelle, "counter") == 0.0, schwelle


def test_the_ladder_is_multiples_not_percent() -> None:
    """Die Beschriftung ist Teil der Zusage: "50 %" und "50×" bedeuten
    Verschiedenes, und die Oberfläche darf das nicht verwechseln."""
    from app.formatting import OUTLIER_THRESHOLD_LABELS

    assert list(OUTLIER_THRESHOLD_LABELS) == ["10", "20", "50", "100", "off"]
    assert [OUTLIER_THRESHOLD_LABELS[k] for k in ("10", "20", "50", "100")] == [
        "10×", "20×", "50×", "100×"
    ]
    assert not any("%" in label for label in OUTLIER_THRESHOLD_LABELS.values())


def test_the_old_percent_settings_are_carried_over_by_rank(tmp_path) -> None:
    """Gespeicherte Prozentwerte bedeuten auf der neuen Leiter nichts mehr. Sie
    werden nach ihrem PLATZ übernommen (empfindlichste alte Stufe wird
    empfindlichste neue), nicht nach ihrem Zahlenwert — und niemand findet
    beim nächsten Start eine ungültige Auswahl vor."""

    from app.formatting import OUTLIER_THRESHOLD_LABELS
    from app.storage.index import Index

    db = tmp_path / "index.sqlite"
    index = Index(db)
    for eid, alt in (("sensor.a", "5"), ("sensor.b", "10"), ("sensor.c", "25"),
                     ("sensor.d", "50"), ("sensor.e", "100"), ("sensor.f", "off")):
        index.get_or_create_entity(eid, "sensor", "measurement", "°C")
        index.set_config(eid, outlier_threshold=alt)
    index.set_setting("default_outlier_threshold", "25")
    index.close() if hasattr(index, "close") else None

    # Zweiter Start: dieselbe Datei, _migrate() läuft erneut.
    zweiter = Index(db)
    erwartet = {"sensor.a": "10", "sensor.b": "10", "sensor.c": "20",
                "sensor.d": "50", "sensor.e": "100", "sensor.f": "off"}
    for eid, neu in erwartet.items():
        wert = zweiter.get_entity(eid)["outlier_threshold"]
        assert wert == neu, f"{eid}: {wert}"
        assert wert in OUTLIER_THRESHOLD_LABELS
    assert zweiter.get_setting("default_outlier_threshold", "") == "20"

    # Idempotent: ein dritter Start ändert nichts mehr.
    dritter = Index(db)
    assert dritter.get_entity("sensor.c")["outlier_threshold"] == "20"


def test_both_row_paths_agree_on_a_real_looking_series() -> None:
    """Dieselbe Entität darf nicht je nach gewähltem Zeitraum unterschiedlich
    viele Ausreißer zeigen. Genau das war der Fall: kurze Zeiträume liefen über
    detect_outliers() mit einer eigenen Rechnung, "Jahr"/"Gesamt" über
    analyze_raw_rows_page()."""
    import math
    from zoneinfo import ZoneInfo

    from app.storage.cleanup import detect_outliers

    werte = [20 + math.sin(i / 7) * 2 for i in range(300)]
    werte[150] = 90.0
    rows = [(float(i * 60), v) for i, v in enumerate(werte)]

    direkt = detect_outliers(rows, 20, decimals="auto", tz=ZoneInfo(TZ_NAME))
    assert direkt, "der eingebaute Fehler muss gefunden werden"
    assert _quote(werte, 20) == len(direkt) / len(werte) * 100


def test_the_reason_names_the_previous_value_in_both_modes() -> None:
    """Die Kennzahl bezieht sich auf ein Fenster, nicht auf den Vorwert — aber
    die erste Frage vor einer markierten Zeile lautet trotzdem "und was stand
    vorher da?". Ohne ihn müsste man dafür die Markierung wegklicken und in der
    Liste nachsehen."""
    import math
    from zoneinfo import ZoneInfo

    from app.storage.cleanup import detect_outliers

    tz = ZoneInfo(TZ_NAME)
    ruhig = [24.2 + math.sin(i / 4) * 0.1 for i in range(20)]
    ruhig[15] = 21.56
    rows = [(1_786_500_000 + i * 900.0, v) for i, v in enumerate(ruhig)]
    grund = next(iter(detect_outliers(rows, 20, "auto", tz).values()))
    assert "Vorwert" in grund
    # Der Vorwert ist der unmittelbar vorhergehende, nicht der Median.
    assert _format(ruhig[14]) in grund
    assert "12.08.2026" in grund

    stand = [45000.0 + i * 12 for i in range(60)]
    stand = stand[:40] + [w + 91107 for w in stand[40:]]
    rows = [(1_786_500_000 + i * 3600.0, v) for i, v in enumerate(stand)]
    grund = next(iter(detect_outliers(rows, 50, "auto", tz, mode="counter").values()))
    assert f"Vorwert {_format(stand[39])}" in grund


def _format(wert: float) -> str:
    from app.formatting import decimals_to_int, format_value

    return format_value(wert, decimals_to_int("auto"))
