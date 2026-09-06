"""Verhaltenstests für compute_flow()/compute_trends() im Energiedashboard.

Die übrigen test_energiedashboard_*.py prüfen ausschließlich Quelltext-Strings
(Template-Attribute, JS-Ausdrücke). Die eigentliche Flussberechnung — der Kern
der Seite — lief bis hierher ungetestet, obwohl an ihr zuletzt erheblich
umgebaut wurde: die vier Ring-Trends wanderten in einen eigenen Endpunkt, die
Serienabfragen bekamen eine Memoisierung, und Verbrauchergruppen mit nur einem
Mitglied werden aufgelöst. Dieser Test setzt deshalb echte Zählerdaten auf und
prüft das Ergebnis, statt Zeichenketten zu vergleichen.

Aufbau wie in test_query.py: eigenes tmp-Verzeichnis, Index, Werte über
hotbuffer.append(). Alle Rollen sind Zähler (state_class "total_increasing"),
weil compute_flow Perioden-Deltas daraus bildet — ein "standard"-Gauge würde
hier gemittelt statt summiert und die Datenqualitäts-Prüfung anschlagen.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from _paths import APP


try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app import energiedashboard_routes as ed
    from app.storage import hotbuffer, rollup
    from app.storage import query as query_mod
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne pyarrow
    _PYARROW_AVAILABLE = False

import pytest

pytestmark = pytest.mark.skipif(not _PYARROW_AVAILABLE, reason="pyarrow fehlt")

TZ = ZoneInfo("Europe/Berlin")
# Fest verankerter "Jetzt"-Zeitpunkt: der 15.03.2024 ist ein abgeschlossener
# Tag, das Fenster für range="day"/offset=-1 liegt damit vollständig in der
# Vergangenheit und kann sich zwischen zwei Testläufen nicht mehr ändern.
NOW = _dt.datetime(2024, 3, 16, 12, 0, 0, tzinfo=TZ)
TAG = (2024, 3, 15)


def _ts(stunde: float) -> float:
    return _dt.datetime(*TAG, tzinfo=TZ).timestamp() + stunde * 3600


class _FesteUhr(_dt.datetime):
    """compute_flow() ruft datetime.now(tz) selbst auf — ohne feste Uhr hinge
    das Ergebnis am Kalendertag des Testlaufs."""

    @classmethod
    def now(cls, tz=None):  # noqa: A003 — bewusst dieselbe Signatur
        return NOW.astimezone(tz) if tz else NOW


class _Anlage:
    """Kleine, vollständige Beispielanlage: Netzbezug, ein Erzeuger, eine
    Einspeisung und drei Verbraucher — davon zwei in einer gemeinsamen Gruppe
    und einer allein in einer eigenen (der Fall, den compute_flow auflösen
    soll)."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.index = Index(tmp / "index.sqlite")
        self.service = ed.EnergieDashboardService(
            ed.EnergieDashboardDependencies(
                data_dir=tmp, index=self.index, tz=TZ, templates=None, app_root_context=None,
            )
        )

    def zaehler(self, entity_id: str, *werte: tuple[float, float]) -> None:
        """Zählerstände als (Stunde, Stand) — die Differenz über den Tag ist
        der Perioden-Verbrauch. Ein Startwert um 0:00 gehört dazu, sonst fehlt
        compute_flow der Bezugspunkt für das erste Delta."""
        self.index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
        for stunde, stand in werte:
            hotbuffer.append(self.tmp, entity_id, _ts(stunde), stand, TZ)
            self.index.record_write(entity_id, _ts(stunde))

    def close(self) -> None:
        self.index.close()


def _beispielanlage(tmp: Path) -> tuple[_Anlage, dict]:
    a = _Anlage(tmp)
    # Über den Tag: Netzbezug +6, Erzeugung +10, Einspeisung +3,
    # Waschmaschine +1, Trockner +2, Wallbox +4.
    a.zaehler("sensor.netz", (0, 100.0), (12, 103.0), (23, 106.0))
    a.zaehler("sensor.pv", (0, 500.0), (12, 506.0), (23, 510.0))
    a.zaehler("sensor.einspeisung", (0, 50.0), (12, 51.5), (23, 53.0))
    a.zaehler("sensor.waschmaschine", (0, 10.0), (12, 10.5), (23, 11.0))
    a.zaehler("sensor.trockner", (0, 20.0), (12, 21.0), (23, 22.0))
    a.zaehler("sensor.wallbox", (0, 30.0), (12, 32.0), (23, 34.0))

    config = ed._empty_config()
    config.update({
        "netzbezug": "sensor.netz",
        "einspeisung": "sensor.einspeisung",
        "erzeuger": [{"entity_id": "sensor.pv", "name": "Dach-PV"}],
        "verbraucher": [
            {"entity_id": "sensor.waschmaschine", "name": "Waschmaschine", "gruppe": "Haushalt"},
            {"entity_id": "sensor.trockner", "name": "Trockner", "gruppe": "Haushalt"},
            # Einzige Mitgliedschaft in "Mobilität" -> Gruppe wird aufgelöst.
            {"entity_id": "sensor.wallbox", "name": "Wallbox", "gruppe": "Mobilität"},
        ],
        "hub_name": "Haus",
    })
    return a, config


def _flow(monkeypatch, tmp: Path, **kwargs) -> tuple[dict, _Anlage, dict]:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    return a.service.compute_flow(config, "day", -1, **kwargs), a, config


@pytest.fixture()
def tmp(tmp_path_factory) -> Path:
    d = Path(tempfile.mkdtemp(prefix="zeitarchiv-edash-flow-", dir=tmp_path_factory.mktemp("edash")))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_kpi_summen_entsprechen_den_zaehlerdifferenzen(monkeypatch, tmp: Path) -> None:
    flow, a, _ = _flow(monkeypatch, tmp)
    try:
        kpi = flow["kpi"]
        assert kpi["netzbezug"] == pytest.approx(6.0)
        assert kpi["erzeugung"] == pytest.approx(10.0)
        assert kpi["einspeisung"] == pytest.approx(3.0)
        # Verbrauch = alles, was in den Bus fließt, minus Einspeisung
        # (kein Speicher konfiguriert): 6 + 10 - 3 = 13.
        assert kpi["verbrauch"] == pytest.approx(13.0)
    finally:
        a.close()


def test_grundlast_ist_der_nicht_gemessene_rest(monkeypatch, tmp: Path) -> None:
    """Grundlast hat keinen eigenen Sensor, sondern ist der algebraische Rest.
    Gemessen sind 1 + 2 + 4 = 7 kWh, der Bus führt 13 kWh an Verbrauch —
    bleiben 6 kWh Grundlast."""
    flow, a, _ = _flow(monkeypatch, tmp)
    try:
        grundlast = next(n for n in flow["nodes"] if n["name"] == "Grundlast")
        assert grundlast["value"] == pytest.approx(6.0)
        anteile = {i["name"]: i["value"] for i in flow["verbraucher_breakdown"]}
        assert sum(anteile.values()) == pytest.approx(flow["kpi"]["verbrauch"])
    finally:
        a.close()


def test_energiebilanz_geht_am_bus_auf(monkeypatch, tmp: Path) -> None:
    """Die Erhaltungs-Identität, auf der das ganze Diagramm beruht: was in den
    Sammelknoten hineinfließt, fließt auch wieder heraus."""
    flow, a, config = _flow(monkeypatch, tmp)
    try:
        hub = config["hub_name"]
        hinein = sum(link["value"] for link in flow["links"] if link["target"] == hub)
        heraus = sum(link["value"] for link in flow["links"] if link["source"] == hub)
        assert hinein == pytest.approx(heraus)
        bus = next(n for n in flow["nodes"] if n["role"] == "bus")
        assert bus["value"] == pytest.approx(hinein)
        assert flow["quality"]["plausible"] is True
    finally:
        a.close()


def test_gruppe_mit_einem_mitglied_wird_aufgeloest(monkeypatch, tmp: Path) -> None:
    """"Mobilität" hat nur die Wallbox — der Gruppenknoten wäre eine reine
    Umbenennung und kostet im Diagramm eine ganze Ebene. "Haushalt" hat zwei
    Mitglieder und bleibt deshalb erhalten."""
    flow, a, config = _flow(monkeypatch, tmp)
    try:
        namen = [n["name"] for n in flow["nodes"]]
        assert "Mobilität" not in namen
        assert "Haushalt" in namen
        hub = config["hub_name"]
        assert {"source": hub, "target": "Wallbox", "value": pytest.approx(4.0)} in [
            {"source": link["source"], "target": link["target"], "value": pytest.approx(link["value"])}
            for link in flow["links"]
        ]
        # Die Gruppe bleibt zweistufig: Bus -> Haushalt -> Gerät.
        assert any(link["source"] == hub and link["target"] == "Haushalt" for link in flow["links"])
        assert any(link["source"] == "Haushalt" and link["target"] == "Trockner" for link in flow["links"])
        # Der aufgelöste Verbraucher zählt trotzdem voll mit.
        assert flow["kpi"]["verbrauch"] == pytest.approx(13.0)
    finally:
        a.close()


def test_gruppe_bleibt_wenn_ein_mitglied_in_dieser_periode_still_war(monkeypatch, tmp: Path) -> None:
    """Gezählt wird über die Konfiguration, nicht über die Werte der Periode —
    sonst würde eine Gruppe je nach Zeitraum erscheinen und verschwinden."""
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        # Trockner ohne jeden Verbrauch an diesem Tag: Gruppe hat weiter zwei
        # konfigurierte Mitglieder und muss bestehen bleiben.
        config["verbraucher"][1]["entity_id"] = "sensor.trockner_still"
        a.zaehler("sensor.trockner_still", (0, 5.0), (23, 5.0))
        flow = a.service.compute_flow(config, "day", -1)
        assert "Haushalt" in [n["name"] for n in flow["nodes"]]
    finally:
        a.close()


def test_trends_stecken_nicht_mehr_in_der_flussantwort(monkeypatch, tmp: Path) -> None:
    """Die vier Ring-Trends liegen unter /energiedashboard/trends. Rutschen sie
    versehentlich in compute_flow() zurück, kostet das bei jedem Zeitraum-
    Wechsel wieder drei Kalenderjahre Abfragen."""
    flow, a, config = _flow(monkeypatch, tmp)
    try:
        for key in ("speicher_efficiency_trend", "speicher_soc_trend",
                    "autarkie_trend", "eigenverbrauch_trend"):
            assert key not in flow
        trends = a.service.compute_trends(config)
        assert set(trends) == {"speicher_efficiency_trend", "speicher_soc_trend",
                               "autarkie_trend", "eigenverbrauch_trend"}
        assert all(isinstance(v, list) for v in trends.values())
    finally:
        a.close()


def test_gleiche_serienabfrage_laeuft_pro_request_nur_einmal(monkeypatch, tmp: Path) -> None:
    """Die Memoisierung in _entity_series() darf nicht verlorengehen: ohne sie
    fragen mehrere Bausteine dieselbe Entität für denselben Zeitraum erneut ab,
    und der Lese-Cache allein spart nur das Parsen der Dateien."""
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        aufrufe: list[tuple] = []
        echt = query_mod.query_series

        def zaehlend(data_dir, index, entity_id, range_key, tz, now, **kw):
            aufrufe.append((entity_id, range_key, kw.get("offset", 0), kw.get("continuous", False)))
            return echt(data_dir, index, entity_id, range_key, tz, now, **kw)

        monkeypatch.setattr(query_mod, "query_series", zaehlend)
        read_cache = query_mod.QueryReadCache()
        a.service.compute_flow(config, "day", -1, read_cache=read_cache)
        a.service.compute_flow(config, "day", -1, read_cache=read_cache)
        assert len(aufrufe) == len(set(aufrufe)), "identische Abfrage mehrfach ausgeführt"
    finally:
        a.close()


def test_veraltete_sensoren_und_falsche_einheit_werden_gemeldet(monkeypatch, tmp: Path) -> None:
    """Die Datenqualitäts-Prüfungen sind der einzige Schutz davor, dass ein
    scheinbar sauber aufgehendes Diagramm auf falsch zugeordneten Rollen
    beruht."""
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        # Leistung statt Energie zugeordnet — typischer Einrichtungsfehler.
        a.index.get_or_create_entity("sensor.leistung", "sensor", "measurement", "W")
        hotbuffer.append(tmp, "sensor.leistung", _ts(6), 700.0, TZ)
        a.index.record_write("sensor.leistung", _ts(6))
        config["verbraucher"].append({"entity_id": "sensor.leistung", "name": "Falsch"})
        flow = a.service.compute_flow(config, "day", -1)
        pruefungen = {c["label"]: c for c in flow["quality"]["checks"]}
        assert pruefungen["Einheit korrekt (kWh)"]["ok"] is False
        assert "Falsch (W)" in pruefungen["Einheit korrekt (kWh)"]["detail"]
        assert pruefungen["Zähler-Typ korrekt"]["ok"] is False
        assert flow["quality"]["plausible"] is False
    finally:
        a.close()


def test_doppelt_zugeordnete_entitaet_wird_erkannt(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        config["erzeuger"].append({"entity_id": "sensor.pv", "name": "Dach-PV nochmal"})
        flow = a.service.compute_flow(config, "day", -1)
        pruefung = next(c for c in flow["quality"]["checks"]
                        if c["label"] == "Keine doppelt zugeordneten Entitäten")
        assert pruefung["ok"] is False
    finally:
        a.close()


def test_mischton_haengt_am_bus_anschluss_nicht_an_der_gruppierung(monkeypatch, tmp: Path) -> None:
    """Der PV/Netz-Mischton ("wie grün war dieser Verbrauch") gehört an jede
    Bahn, die direkt vom Sammelknoten kommt — Gruppe, Grundlast UND einzelnes
    Gerät. Vorher trugen ihn nur Gruppen und Grundlast, ein ungruppiertes Gerät
    bekam einen bedeutungslosen Grauverlauf. Damit hing die Farbe eines Geräts
    davon ab, ob es zufällig gruppiert ist — eine reine Darstellungsfrage hätte
    die Bedeutung der Farbe bestimmt. Besonders schief seit dem Auflösen der
    Ein-Mitglied-Gruppen: die Wallbox verlor den Mischton allein durch eine
    Layout-Optimierung."""
    flow, a, _ = _flow(monkeypatch, tmp)
    try:
        blend = {n["name"]: bool(n.get("blend")) for n in flow["nodes"] if n.get("role") == "sink"}
        # Direkt am Bus: Gruppe, aufgelöstes Einzelgerät, rechnerischer Rest.
        assert blend["Haushalt"] is True
        assert blend["Wallbox"] is True
        assert blend["Grundlast"] is True
        # Innerhalb der Gruppe nicht — dort trägt ihn schon Bus -> Haushalt.
        assert blend["Waschmaschine"] is False
        assert blend["Trockner"] is False
        # Rollen mit eigener Farbbedeutung bleiben unangetastet.
        assert blend["Einspeisung"] is False
    finally:
        a.close()


# --- Perioden-Vergleich ("vs. Vortag") -------------------------------------
#
# Diese Regel war schon einmal kaputt (CHANGELOG 0.80.x): der Vergleichswert
# kam aus einem rollierenden, an "jetzt" verankerten Fenster statt aus der
# angezeigten Kalenderperiode und konnte "+X %" zeigen, obwohl der Wert
# gegenüber dem Vortag gesunken war. Der Fehlermodus ist still — eine falsche
# Prozentzahl sieht plausibel aus.


def _zwei_tage(tmp: Path, gestern: float, vorgestern: float) -> tuple[_Anlage, dict]:
    """Netzbezug an zwei aufeinanderfolgenden Tagen, sonst nichts. Reicht, um
    das Vorzeichen des Vergleichs zu prüfen, und hält die Erwartung im Kopf
    nachrechenbar."""
    a = _Anlage(tmp)
    # NOW ist der 16.03., TAG der 15.03. — offset=-1 trifft also TAG selbst
    # ("gestern"), offset=-2 den Tag davor ("vorgestern"). Bewusst ausgeschrieben
    # statt über Offset-Arithmetik: die verschob die Tage beim ersten Versuch um
    # eins, sodass am geprüften Tag 0 kWh standen.
    vortag = _dt.datetime(*TAG, tzinfo=TZ) - _dt.timedelta(days=1)   # vorgestern
    haupttag = _dt.datetime(*TAG, tzinfo=TZ)                          # gestern
    folgetag = haupttag + _dt.timedelta(days=1)                       # NOW-Tag
    stand_start = 100.0
    werte = [
        (vortag.timestamp(), stand_start),
        ((vortag + _dt.timedelta(hours=23)).timestamp(), stand_start + vorgestern),
        (haupttag.timestamp(), stand_start + vorgestern),
        ((haupttag + _dt.timedelta(hours=23)).timestamp(), stand_start + vorgestern + gestern),
        # Anker zu Beginn des NOW-Tages, damit der Haupttag sauber abschließt.
        (folgetag.timestamp(), stand_start + vorgestern + gestern),
    ]
    a.index.get_or_create_entity("sensor.netz", "sensor", "total_increasing", "kWh")
    for ts, wert in werte:
        hotbuffer.append(tmp, "sensor.netz", ts, wert, TZ)
        a.index.record_write("sensor.netz", ts)
    config = ed._empty_config()
    config.update({"netzbezug": "sensor.netz", "hub_name": "Haus"})
    return a, config


def test_abgeschlossene_periode_vergleicht_kalendarisch(monkeypatch, tmp: Path) -> None:
    """Der Kern des alten Fehlers: gestern 4 kWh gegen vorgestern 10 kWh ist ein
    RÜCKGANG. Zöge der Vergleich ein an "jetzt" verankertes Fenster heran, käme
    hier ein positiver Wert heraus."""
    a, config = _zwei_tage(tmp, gestern=4.0, vorgestern=10.0)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        rc = query_mod.QueryReadCache()
        current = a.service.compute_flow(config, "day", -1, read_cache=rc)
        vergleich, kpi_jetzt, kpi_vorher = a.service.compute_period_comparison(
            config, "day", -1, current, rc,
        )
        assert current["kpi"]["netzbezug"] == pytest.approx(4.0)
        assert kpi_vorher["netzbezug"] == pytest.approx(10.0)
        # -60 %, und vor allem: negativ, so wie der angezeigte Wert gefallen ist.
        assert vergleich["netzbezug"]["pct"] == pytest.approx(-60.0)
    finally:
        a.close()


def test_abgeschlossene_periode_nutzt_kein_rollierendes_fenster(monkeypatch, tmp: Path) -> None:
    """Für offset<0 muss der Vergleich denselben Wert als Basis nehmen, der auch
    angezeigt wird — sonst driften Zahl und Prozentangabe auseinander."""
    a, config = _zwei_tage(tmp, gestern=4.0, vorgestern=10.0)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        rc = query_mod.QueryReadCache()
        current = a.service.compute_flow(config, "day", -1, read_cache=rc)
        _v, kpi_jetzt, _p = a.service.compute_period_comparison(config, "day", -1, current, rc)
        assert kpi_jetzt["netzbezug"] == pytest.approx(current["kpi"]["netzbezug"])
    finally:
        a.close()


def test_laufende_periode_vergleicht_rollierend(monkeypatch, tmp: Path) -> None:
    """Bei offset=0 dagegen bewusst rollierend: die laufende Periode ist noch
    unvollständig, ein kalendarischer Vergleich gegen einen ganzen Vortag wäre
    unfair. Erkennbar daran, dass die Vergleichsbasis hier NICHT der angezeigte
    (noch wachsende) Periodenwert ist."""
    a, config = _zwei_tage(tmp, gestern=4.0, vorgestern=10.0)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        rc = query_mod.QueryReadCache()
        current = a.service.compute_flow(config, "day", 0, read_cache=rc)
        _v, kpi_jetzt, _p = a.service.compute_period_comparison(config, "day", 0, current, rc)
        assert kpi_jetzt is not current["kpi"]
    finally:
        a.close()


def test_vergleichsregel_steht_nur_noch_an_einer_stelle() -> None:
    """Die Verzweigung stand wortgleich in /energiedashboard/data und im
    Energiebericht — die Konstellation, in der ein Fehler einmal behoben und
    beim zweiten Vorkommen vergessen wird."""
    quelle = (APP / "energiedashboard_routes.py").read_text(
        encoding="utf-8"
    )
    assert quelle.count("continuous=True, read_cache=read_cache") == 2  # beide in der Helfermethode
    assert quelle.count("def compute_period_comparison") == 1
    assert quelle.count("self.compute_period_comparison(") == 2  # Daten-Route und Bericht


# --- Speicher ---------------------------------------------------------------
#
# Der Bereich mit der meisten Eigenlogik und durchweg stillen Fehlern: eine
# falsch gewichtete Prozentzahl oder ein verrutschter Faktor 1000 sehen im
# Diagramm völlig plausibel aus.


def _gauge(a: _Anlage, entity_id: str, einheit: str, *werte: tuple[float, float]) -> None:
    """Messgröße (Ladezustand in %, Kapazität in kWh/Wh) — anders als die
    Zähler kein total_increasing, sonst würde compute_flow Differenzen statt
    Momentanwerten bilden."""
    a.index.get_or_create_entity(entity_id, "sensor", "measurement", einheit)
    for stunde, wert in werte:
        hotbuffer.append(a.tmp, entity_id, _ts(stunde), wert, TZ)
        a.index.record_write(entity_id, _ts(stunde))


def _mit_speicher(tmp: Path, speicher: list[dict]) -> tuple[_Anlage, dict]:
    a, config = _beispielanlage(tmp)
    config["speicher"] = speicher
    return a, config


def test_ladung_und_entladung_werden_als_eigene_knoten_gefuehrt(monkeypatch, tmp: Path) -> None:
    """Entladung speist den Bus (Quelle), Ladung entnimmt ihm (Senke) — die
    beiden "kind"-Werte steuern im Frontend außerdem die Farbe."""
    a, config = _mit_speicher(tmp, [{
        "name": "Heimspeicher",
        "laden_entity_id": "sensor.laden", "entladen_entity_id": "sensor.entladen",
    }])
    a.zaehler("sensor.laden", (0, 200.0), (23, 205.0))      # +5 geladen
    a.zaehler("sensor.entladen", (0, 300.0), (23, 303.0))   # +3 entladen
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        knoten = {n["name"]: n for n in flow["nodes"]}
        assert knoten["Heimspeicher (Entladung)"]["role"] == "source"
        assert knoten["Heimspeicher (Entladung)"]["kind"] == "storage_out"
        assert knoten["Heimspeicher (Ladung)"]["role"] == "sink"
        assert knoten["Heimspeicher (Ladung)"]["kind"] == "storage_in"
        # Netto = Ladung minus Entladung, hier also +2 kWh in den Speicher.
        assert flow["kpi"]["speicher_netto"] == pytest.approx(2.0)
        assert flow["kpi"]["speicher_laden"] == pytest.approx(5.0)
        assert flow["kpi"]["speicher_entladen"] == pytest.approx(3.0)
    finally:
        a.close()


def test_ohne_speicherrolle_bleibt_netto_none_statt_null(monkeypatch, tmp: Path) -> None:
    """None und 0.0 bedeuten Verschiedenes: "kein Speicher konfiguriert" gegen
    "Speicher da, aber diese Periode ohne Bewegung". Das Frontend blendet die
    Kachel nur im ersten Fall aus."""
    flow, a, _ = _flow(monkeypatch, tmp)
    try:
        assert flow["kpi"]["speicher_netto"] is None
        assert flow["kpi"]["speicher_laden"] is None
    finally:
        a.close()


def test_ladezustand_wird_kapazitaetsgewichtet_gemittelt(monkeypatch, tmp: Path) -> None:
    """Der Fall, den der Code-Kommentar als Grund nennt: ein kleiner Speicher
    voll, ein großer leer. Ein einfacher Mittelwert ergäbe 50 %, tatsächlich
    ist die Anlage aber fast leer — 2 kWh von 12 kWh, also rund 17 %."""
    a, config = _mit_speicher(tmp, [
        {"name": "Klein", "soc_entity_id": "sensor.soc_klein", "capacity_kwh": 2.0},
        {"name": "Gross", "soc_entity_id": "sensor.soc_gross", "capacity_kwh": 10.0},
    ])
    _gauge(a, "sensor.soc_klein", "%", (0, 100.0), (12, 100.0), (23, 100.0))
    _gauge(a, "sensor.soc_gross", "%", (0, 0.0), (12, 0.0), (23, 0.0))
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        # (100*2 + 0*10) / 12 = 16,67 — NICHT 50.
        assert flow["kpi"]["speicher_soc"] == pytest.approx(16.7, abs=0.1)
        # Gesamtkapazität 12 kWh, davon 16,67 % -> 2 kWh.
        assert flow["kpi"]["speicher_soc_kwh"] == pytest.approx(2.0, abs=0.1)
    finally:
        a.close()


def test_kapazitaet_aus_wh_sensor_wird_umgerechnet(monkeypatch, tmp: Path) -> None:
    """BMS-Integrationen melden die Kapazität häufig in Wh. Ein verrutschter
    Faktor 1000 fiele in einer Prozentanzeige nicht auf, in der kWh-Angabe
    daneben aber sehr wohl."""
    a, config = _mit_speicher(tmp, [{
        "name": "BMS", "soc_entity_id": "sensor.soc",
        "capacity_entity_id": "sensor.kapazitaet_wh",
    }])
    _gauge(a, "sensor.soc", "%", (0, 50.0), (23, 50.0))
    _gauge(a, "sensor.kapazitaet_wh", "Wh", (0, 8000.0), (23, 8000.0))
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        # 8000 Wh = 8 kWh, davon 50 % -> 4 kWh (nicht 4000).
        assert flow["kpi"]["speicher_soc_kwh"] == pytest.approx(4.0, abs=0.1)
    finally:
        a.close()


def test_kwh_angabe_entfaellt_wenn_eine_kapazitaet_fehlt(monkeypatch, tmp: Path) -> None:
    """Bei nur teilweise bekannten Kapazitäten wäre die kWh-Summe irreführend
    niedrig — der Speicher ohne Angabe zählt in der Summe nicht mit, ging aber
    mit Gewicht 1 in den Prozentschnitt ein. Dann lieber gar keine kWh-Zahl."""
    a, config = _mit_speicher(tmp, [
        {"name": "Mit", "soc_entity_id": "sensor.soc_a", "capacity_kwh": 10.0},
        {"name": "Ohne", "soc_entity_id": "sensor.soc_b"},
    ])
    _gauge(a, "sensor.soc_a", "%", (0, 80.0), (23, 80.0))
    _gauge(a, "sensor.soc_b", "%", (0, 80.0), (23, 80.0))
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["speicher_soc"] == pytest.approx(80.0, abs=0.5)
        assert flow["kpi"]["speicher_soc_kwh"] is None
    finally:
        a.close()


def test_vertauschte_lade_und_entladerolle_wird_gemeldet(monkeypatch, tmp: Path) -> None:
    """Über einen längeren Zeitraum kann nicht mehr entladen als geladen worden
    sein. Ohne diese Prüfung geht das Diagramm scheinbar sauber auf, obwohl die
    beiden Sensoren vertauscht zugeordnet sind."""
    a, config = _mit_speicher(tmp, [{
        "name": "Verdreht",
        "laden_entity_id": "sensor.wenig", "entladen_entity_id": "sensor.viel",
    }])
    a.zaehler("sensor.wenig", (0, 10.0), (23, 11.0))   # +1 "geladen"
    a.zaehler("sensor.viel", (0, 20.0), (23, 29.0))    # +9 "entladen"
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        pruefung = next(c for c in flow["quality"]["checks"]
                        if c["label"] == "Speicher-Wirkungsgrad plausibel")
        assert pruefung["ok"] is False
        assert "Verdreht" in pruefung["detail"]
        assert flow["quality"]["plausible"] is False
    finally:
        a.close()


def test_mehrere_speicher_werden_bei_energie_summiert(monkeypatch, tmp: Path) -> None:
    """Energiemengen addieren sich über Speicher hinweg — anders als der
    Ladezustand, der gemittelt wird. Die Aufschlüsselung erscheint erst ab zwei
    Speichern, bei einem wäre sie nur eine Wiederholung der Summe."""
    a, config = _mit_speicher(tmp, [
        {"name": "A", "laden_entity_id": "sensor.a_lad", "entladen_entity_id": "sensor.a_ent"},
        {"name": "B", "laden_entity_id": "sensor.b_lad", "entladen_entity_id": "sensor.b_ent"},
    ])
    a.zaehler("sensor.a_lad", (0, 0.0), (23, 4.0))
    a.zaehler("sensor.a_ent", (0, 0.0), (23, 1.0))
    a.zaehler("sensor.b_lad", (0, 0.0), (23, 6.0))
    a.zaehler("sensor.b_ent", (0, 0.0), (23, 2.0))
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["speicher_laden"] == pytest.approx(10.0)
        assert flow["kpi"]["speicher_entladen"] == pytest.approx(3.0)
        aufschluesselung = {s["name"]: s["value"] for s in flow["speicher_breakdown"]}
        assert aufschluesselung == {"A": pytest.approx(3.0), "B": pytest.approx(4.0)}
    finally:
        a.close()


# --- Sankey-Kartenhöhe ------------------------------------------------------
#
# Reine Darstellungslogik im JS, deshalb hier als Quelltext-Prüfung wie in den
# übrigen test_energiedashboard_*.py. Der eigentliche Nachweis lief im Browser:
# bei 16 Verbrauchern wuchs der dünnste Balken von 7,6 px auf 17,8 px.


def _js() -> str:
    return (APP / "static/js/energiedashboard.js").read_text(
        encoding="utf-8"
    )


def test_kartenhoehe_waechst_mit_der_dichtesten_ebene() -> None:
    js = _js()
    assert "Math.min(760, Math.max(420, dichtesteEbene * 52))" in js
    # Untergrenze 420 = bisheriger Wert, damit kleine Anlagen unverändert bleiben.
    assert "wrap.classList.contains('edash-sankey-wrap')" in js


def test_kartenhoehe_nur_horizontal_mobil_bleibt_die_css_regel() -> None:
    """Vertikal wirkt die Knotenzahl auf die BREITE (narrowNodeGap), nicht auf
    die Höhe — dort muss der Inline-Stil geleert werden, sonst überschriebe er
    dauerhaft die @media-Regel."""
    js = _js()
    start = js.index("const neueHoehe = isNarrow")
    assert "? ''" in js[start:start + 120]


def test_hoehenaenderung_loest_ein_resize_aus() -> None:
    """ECharts merkt sich die Größe beim init() — ohne resize() zeichnet es in
    den alten Ausschnitt und der Rest der Karte bleibt leer."""
    js = _js()
    start = js.index("if (wrap.style.height !== neueHoehe)")
    # Die vollständige Anweisung prüfen, nicht nur den Methodennamen: ein
    # ausgehebelter Wächter ("if (false) chartInstance.resize()") enthält den
    # Aufruf ja weiterhin und rutschte durch eine Teilstring-Prüfung durch.
    assert "if (chartInstance) chartInstance.resize();" in js[start:start + 400]


def test_narrow_node_gap_ist_deklariert_bevor_die_serie_ihn_nutzt() -> None:
    """Beim Einbau der Kartenhöhe wurde die Deklaration von narrowNodeGap
    versehentlich mit ersetzt. node --check meldete nichts (kein Syntaxfehler),
    der vertikale Sankey warf erst zur Laufzeit "narrowNodeGap is not defined"
    und blieb leer."""
    js = _js()
    deklaration = js.index("const narrowNodeGap =")
    nutzung = js.index("nodeGap: isNarrow ? narrowNodeGap")
    assert deklaration < nutzung


def test_farbumrechnung_wird_gemerkt() -> None:
    """toRgbTriplet() hängt ein Element in den DOM und ruft getComputedStyle —
    beides erzwingt ein Style-Recalc, mitten im Render-Pfad. Ohne Cache waren
    das bei 30 Verbrauchern 62 Recalcs je Neuzeichnung (zwei pro Farbmischung),
    mit Cache 4 beim ersten Render und danach keine mehr. Der Schlüssel ist die
    bereits aufgelöste Farbe, deshalb ist keine Invalidierung nötig."""
    js = _js()
    assert "const rgbTripletCache = new Map();" in js
    start = js.index("function toRgbTriplet(")
    body = js[start:start + 700]
    assert "rgbTripletCache.get(cssColor)" in body
    assert "if (gemerkt) return gemerkt;" in body
    assert "rgbTripletCache.set(cssColor, triplet);" in body


# --- Wirkungsgrad-Cache -----------------------------------------------------


def _speicheranlage(tmp: Path) -> tuple[_Anlage, dict]:
    a, config = _beispielanlage(tmp)
    config["speicher"] = [{
        "name": "Heimspeicher",
        "laden_entity_id": "sensor.laden", "entladen_entity_id": "sensor.entladen",
    }]
    a.zaehler("sensor.laden", (0, 0.0), (23, 10.0))
    a.zaehler("sensor.entladen", (0, 0.0), (23, 9.0))
    return a, config


def _decade_zaehler(monkeypatch) -> list[int]:
    """Zählt Abfragen über die gesamte Historie — genau die, die der Cache
    einsparen soll."""
    treffer = [0]
    echt = query_mod.query_series

    def wrap(data_dir, index, entity_id, range_key, tz, now, **kw):
        if range_key == "decade":
            treffer[0] += 1
        return echt(data_dir, index, entity_id, range_key, tz, now, **kw)

    monkeypatch.setattr(query_mod, "query_series", wrap)
    return treffer


def test_wirkungsgrad_wird_beim_zweiten_aufruf_nicht_neu_berechnet(monkeypatch, tmp: Path) -> None:
    """Die Berechnung liest die GESAMTE Historie und kostete gemessen 15-19 %
    eines Requests — für einen Wert, den ein weiterer Tag Messdaten nicht
    sichtbar verschiebt."""
    a, config = _speicheranlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    treffer = _decade_zaehler(monkeypatch)
    try:
        erst = a.service.compute_flow(config, "day", -1)["kpi"]["speicher_efficiency"]
        kalt = treffer[0]
        treffer[0] = 0
        zweit = a.service.compute_flow(config, "day", -1)["kpi"]["speicher_efficiency"]
        assert kalt > 0, "erster Aufruf muss tatsächlich rechnen"
        assert treffer[0] == 0, "zweiter Aufruf darf die Historie nicht erneut lesen"
        assert zweit == erst
        assert erst == pytest.approx(90.0)  # 9 entladen / 10 geladen
    finally:
        a.close()


def test_geaenderte_rollenzuordnung_verwirft_den_cache(monkeypatch, tmp: Path) -> None:
    """Die Signatur aus den beteiligten Entitäten macht eine eigene
    Invalidierung überflüssig — sonst zeigte eine korrigierte Zuordnung
    stundenlang den alten Wert."""
    a, config = _speicheranlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    treffer = _decade_zaehler(monkeypatch)
    try:
        a.service.compute_flow(config, "day", -1)
        a.zaehler("sensor.entladen2", (0, 0.0), (23, 5.0))
        config["speicher"][0]["entladen_entity_id"] = "sensor.entladen2"
        treffer[0] = 0
        neu = a.service.compute_flow(config, "day", -1)["kpi"]["speicher_efficiency"]
        assert treffer[0] > 0, "geänderte Zuordnung muss neu rechnen"
        assert neu == pytest.approx(50.0)  # 5 entladen / 10 geladen
    finally:
        a.close()


def test_abgelaufener_cache_wird_neu_berechnet(monkeypatch, tmp: Path) -> None:
    a, config = _speicheranlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    treffer = _decade_zaehler(monkeypatch)
    try:
        a.service.compute_flow(config, "day", -1)
        eintrag = json.loads(a.index.get_setting(ed.SETTING_EFFICIENCY_CACHE, ""))
        eintrag["checked_at"] -= ed.EFFICIENCY_CACHE_TTL_SECONDS + 1
        a.index.set_setting(ed.SETTING_EFFICIENCY_CACHE, json.dumps(eintrag))
        treffer[0] = 0
        a.service.compute_flow(config, "day", -1)
        assert treffer[0] > 0
    finally:
        a.close()


def test_zeitstempel_aus_der_zukunft_gilt_als_abgelaufen(monkeypatch, tmp: Path) -> None:
    """Wird die Systemuhr zurückgestellt, läge checked_at in der Zukunft — ohne
    die Untergrenze bliebe ein falscher Wert bis zum Aufholen der Uhr hängen."""
    a, config = _speicheranlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    treffer = _decade_zaehler(monkeypatch)
    try:
        a.service.compute_flow(config, "day", -1)
        eintrag = json.loads(a.index.get_setting(ed.SETTING_EFFICIENCY_CACHE, ""))
        eintrag["checked_at"] += 99999
        a.index.set_setting(ed.SETTING_EFFICIENCY_CACHE, json.dumps(eintrag))
        treffer[0] = 0
        a.service.compute_flow(config, "day", -1)
        assert treffer[0] > 0
    finally:
        a.close()


# --- Auffälligkeiten-Baseline ----------------------------------------------


def _anomalie_anlage(tmp: Path) -> tuple[_Anlage, dict]:
    """Ein Verbraucher, dessen Verbrauch im ANGEZEIGTEN Monat deutlich über den
    drei Vormonaten liegt — nur dann feuert die Auffälligkeit, nur dann steht
    überhaupt eine Baseline im Ergebnis, und nur dann fällt ein falsch gelesener
    Vergleichswert auf.

    Die Monate sind bewusst so gelegt, dass die drei Vergleichsperioden über
    eine JAHRESGRENZE reichen: angezeigt wird Februar 2024, verglichen wird mit
    Januar 2024 sowie Dezember und November 2023. Die Fensterauswahl muss dafür
    zwei grobe Abfragen anfordern statt einer — sonst fehlen zwei der drei Werte
    unbemerkt.

    Anders als die übrigen Fixtures hier reicht der Hot Buffer nicht: eine
    Monatsabfrage liest abgeschlossene Tage aus dem Rollup. Die Monate werden
    deshalb wie in test_query.py als archivierte Monate samt Rollup angelegt.
    """
    a = _Anlage(tmp)
    a.zaehler("sensor.netz", (0, 0.0), (23, 100.0))
    a.index.get_or_create_entity("sensor.geraet", "sensor", "total_increasing", "kWh")
    # (Jahr, Monat, Zählerstand am Monatsanfang, Verbrauch im Monat)
    monate = [(2023, 11, 0.0, 10.0), (2023, 12, 10.0, 10.0),
              (2024, 1, 20.0, 10.0), (2024, 2, 30.0, 40.0)]
    archiv = tmp / "archive" / "sensor.geraet"
    archiv.mkdir(parents=True, exist_ok=True)
    for jahr, monat, start, menge in monate:
        erster = _dt.datetime(jahr, monat, 1, 6, tzinfo=TZ)
        letzter = _dt.datetime(jahr, monat, 28, 18, tzinfo=TZ)
        tabelle = pa.table({
            "ts": [erster.timestamp(), letzter.timestamp()],
            "value": [start, start + menge],
        })
        pq.write_table(tabelle, archiv / f"{jahr}-{monat:02d}.parquet")
        rollup.append_completed_month(tmp, "sensor.geraet", "counter", tabelle, jahr, monat, TZ)
        a.index.record_write("sensor.geraet", letzter.timestamp())
    config = ed._empty_config()
    config.update({
        "netzbezug": "sensor.netz", "hub_name": "Haus",
        "verbraucher": [{"entity_id": "sensor.geraet", "name": "Gerät"}],
    })
    return a, config


def test_anomalie_feuert_im_testaufbau_ueberhaupt(monkeypatch, tmp: Path) -> None:
    """Absicherung der Absicherung: ohne ausgelöste Auffälligkeit wären die
    Baselines unten auf beiden Wegen None und der Vergleich wertlos."""
    a, config = _anomalie_anlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "month", -1)
        knoten = next(n for n in flow["nodes"] if n["name"] == "Gerät")
        assert knoten.get("anomaly") is True, "Testaufbau löst keine Auffälligkeit aus"
        assert knoten["anomaly_baseline"] == pytest.approx(10.0)
    finally:
        a.close()


@pytest.mark.parametrize("range_key", ["hour", "day", "month", "year"])
def test_baseline_ueber_groebere_abfrage_ergibt_dieselben_werte(monkeypatch, tmp: Path, range_key) -> None:
    """Der eigentliche Nachweis: die Abkürzung über eine gröbere Abfrage muss
    exakt dieselben Auffälligkeiten liefern wie die Einzelabfragen. Geprüft,
    indem _COARSER_RANGE geleert wird — dann greift der Fallback-Zweig."""
    a, config = _anomalie_anlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        mit = a.service.compute_flow(config, range_key, -1)
        monkeypatch.setattr(ed, "_COARSER_RANGE", {})
        ohne = a.service.compute_flow(config, range_key, -1)
        assert mit["anomalien"] == ohne["anomalien"]
        knoten_mit = {n["name"]: n.get("anomaly_baseline") for n in mit["nodes"]}
        knoten_ohne = {n["name"]: n.get("anomaly_baseline") for n in ohne["nodes"]}
        assert knoten_mit == knoten_ohne
    finally:
        a.close()


def test_groebere_abfrage_nur_wo_sie_nachweislich_hilft() -> None:
    """"day" und "year" sind bewusst NICHT abgebildet: der Aufwand hängt am
    gescannten Datenvolumen, nicht an der Anzahl der Abfragen. Eine
    Monatsabfrage liest den ganzen Monat (gemessen 49,9 ms gegenüber 5,7 ms für
    drei Tagesabfragen), "decade" scannt zehn Jahre. Wer die Abbildung
    "vervollständigt", macht den Tages-Request rund zehnmal langsamer."""
    assert ed._COARSER_RANGE == {"hour": "day", "month": "year"}


def test_groebere_abfrage_spart_bei_monat_tatsaechlich_abfragen(monkeypatch, tmp: Path) -> None:
    a, config = _anomalie_anlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    gezaehlt = [0]
    echt = query_mod.query_series

    def wrap(*args, **kw):
        gezaehlt[0] += 1
        return echt(*args, **kw)

    monkeypatch.setattr(query_mod, "query_series", wrap)
    try:
        gezaehlt[0] = 0
        a.service.compute_flow(config, "month", -1)
        mit = gezaehlt[0]
        monkeypatch.setattr(ed, "_COARSER_RANGE", {})
        gezaehlt[0] = 0
        a.service.compute_flow(config, "month", -1)
        assert mit < gezaehlt[0], "gröbere Abfrage muss weniger Abfragen brauchen"
    finally:
        a.close()


def _preis_anlage(tmp: Path, versatz_minuten: int, takt_minuten: int) -> tuple[_Anlage, dict]:
    """Beispielanlage plus Preis-Entität mit einstellbarem Sendezyklus.

    Der Netzbezug ist ein Zähler und bekommt bei Zeitraum "Tag" Stunden-Buckets,
    die Preis-Entität ist ein Messwert und bekommt 5-Minuten-Buckets
    (LIVE_BUCKET_SECONDS in storage/query.py). Ob beide Raster zusammenfinden,
    hängt allein an der Sendephase des Preissensors — genau das prüfen die
    Tests unten.
    """
    a, config = _beispielanlage(tmp)
    a.index.get_or_create_entity("sensor.preis", "sensor", "measurement", "EUR/kWh")
    schritte = int(24 * 60 / takt_minuten)
    for i in range(schritte):
        ts = _ts(0) + i * takt_minuten * 60 + versatz_minuten * 60
        hotbuffer.append(tmp, "sensor.preis", ts, 0.30, TZ)
        a.index.record_write("sensor.preis", ts)
    config["kosten"] = {"preis_netzbezug": "sensor.preis"}
    return a, config


@pytest.mark.parametrize(
    "versatz,takt",
    [(0, 60), (0, 15), (7, 15), (2, 5), (13, 30)],
    ids=["stuendlich-auf-null", "15min-ab-00", "15min-ab-07", "5min-ab-02", "30min-ab-13"],
)
def test_grid_costs_do_not_depend_on_the_price_sensors_reporting_phase(
    monkeypatch, tmp: Path, versatz: int, takt: int
) -> None:
    """Der Netzbezug beträgt über den Tag 6 kWh, der Preis konstant 0,30 €/kWh
    — also 1,80 € Kosten, unabhängig davon, WANN der Preissensor sendet.

    Vor der Umstellung suchte die Rechnung den Preis per Schlüsselgleichheit
    (`ts in factor_series`) und traf damit nur, wenn der Preissensor zufällig
    innerhalb der ersten fünf Minuten einer Stunde einen Wert hatte. Ein
    Sensor, der alle 15 Minuten ab :07 sendet, lieferte NULL Treffer und damit
    0,00 € — bei völlig gesunder Anlage und ohne jede Fehlermeldung.
    """
    a, config = _preis_anlage(tmp, versatz, takt)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        ergebnis = a.service.compute_flow(config, "day", -1)
        assert ergebnis["kpi"]["netzbezug_cost"] == pytest.approx(1.80, abs=0.01)
    finally:
        a.close()


def _preis_anlage_mit_ende(tmp: Path, letzte_stunde: float, schwelle: str = "15") -> tuple[_Anlage, dict]:
    """Preissensor, der ab ``letzte_stunde`` schweigt — der tote Sensor."""
    a, config = _beispielanlage(tmp)
    a.index.get_or_create_entity("sensor.preis", "sensor", "measurement", "EUR/kWh")
    a.index.set_config("sensor.preis", gap_threshold=schwelle)
    i = 0
    while i * 15 <= letzte_stunde * 60:
        ts = _ts(0) + i * 15 * 60
        hotbuffer.append(tmp, "sensor.preis", ts, 0.30, TZ)
        a.index.record_write("sensor.preis", ts)
        i += 1
    config["kosten"] = {"preis_netzbezug": "sensor.preis"}
    return a, config


def test_a_dead_price_sensor_is_not_carried_forward_indefinitely(monkeypatch, tmp: Path) -> None:
    """Der letzte bekannte Preis gilt fort — aber nicht beliebig lange. Ein
    Sensor, der um 3 Uhr verstummt, darf seinen Wert nicht über den Rest des
    Tages legen; sonst wäre das Fortschreiben nur eine andere stille Lüge als
    das frühere Verschlucken."""
    a, config = _preis_anlage_mit_ende(tmp, letzte_stunde=3)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["netzbezug_cost"] < 1.80
        pruefung = next(
            p for p in flow["quality"]["checks"] if p["label"] == "Preise und Faktoren vollständig"
        )
        assert pruefung["ok"] is False
        assert "Preis Netzbezug" in pruefung["detail"]
    finally:
        a.close()


def test_gap_threshold_off_means_carry_forward_without_limit(monkeypatch, tmp: Path) -> None:
    """"off" ist in dieser App die ausdrückliche Ansage "diese Entität sendet
    unregelmäßig, melde mir keine Lücken". Dann gilt der letzte Preis auch
    über eine lange Pause — und die Kosten sind wieder vollständig."""
    a, config = _preis_anlage_mit_ende(tmp, letzte_stunde=3, schwelle="off")
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["netzbezug_cost"] == pytest.approx(1.80, abs=0.01)
        pruefung = next(
            p for p in flow["quality"]["checks"] if p["label"] == "Preise und Faktoren vollständig"
        )
        assert pruefung["ok"] is True
    finally:
        a.close()


def test_complete_prices_report_no_gap(monkeypatch, tmp: Path) -> None:
    """Gegenprobe: bei lückenlosem Preis meldet die Prüfung nichts — sonst
    stünde dauerhaft eine Warnung da, die niemand abstellen kann."""
    a, config = _preis_anlage(tmp, versatz_minuten=7, takt_minuten=15)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        pruefung = next(
            p for p in flow["quality"]["checks"] if p["label"] == "Preise und Faktoren vollständig"
        )
        assert pruefung["ok"] is True
    finally:
        a.close()
