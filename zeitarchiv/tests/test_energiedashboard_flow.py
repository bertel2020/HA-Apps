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
import shutil
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyarrow  # noqa: F401 — nur Verfügbarkeitsprüfung, wie in test_query.py

    from app import energiedashboard_routes as ed
    from app.storage import hotbuffer
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
        hinein = sum(l["value"] for l in flow["links"] if l["target"] == hub)
        heraus = sum(l["value"] for l in flow["links"] if l["source"] == hub)
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
            {"source": l["source"], "target": l["target"], "value": pytest.approx(l["value"])}
            for l in flow["links"]
        ]
        # Die Gruppe bleibt zweistufig: Bus -> Haushalt -> Gerät.
        assert any(l["source"] == hub and l["target"] == "Haushalt" for l in flow["links"])
        assert any(l["source"] == "Haushalt" and l["target"] == "Trockner" for l in flow["links"])
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
