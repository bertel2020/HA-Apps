"""Verhaltenstests für _detail_bucket_series() — die neue Datengrundlage für
die Energiebericht-Erweiterungen (Detail-Charts, Stärkster/schwächster Tag).

Prüft insbesondere die Bilanz-Identität für Verbrauch (dieselbe wie in
_monatsverlauf_for_year()) und die Unterscheidung Eigenverbrauch (Erzeugung
minus Einspeisung) vs. Eigenversorgung (Verbrauch minus Netzbezug) — beide
sind nur ohne Speicher identisch, mit Speicher unterscheiden sie sich um den
Netto-Speicher-Fluss (Entladung minus Ladung). Aufbau angelehnt an
test_energiedashboard_flow.py (eigene, kleinere Anlage: mehrere Tage statt
mehrerer Stunden EINES Tages, da _detail_bucket_series bei range="month"
Tages-Buckets liefert)."""

from __future__ import annotations

import datetime as _dt
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import _paths  # noqa: F401


try:
    import pyarrow as pa  # noqa: F401

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
# Mitten im April 2024 — range="month"/offset=0 deckt dann den 1.-15. April
# vollständig über den Hot Buffer ab (kein Rollup/Archiv nötig, siehe
# _query_completed_plus_live_fine: current_month_start == window_start).
NOW = _dt.datetime(2024, 4, 15, 12, 0, 0, tzinfo=TZ)


class _FesteUhr(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: A003
        return NOW.astimezone(tz) if tz else NOW


class _Anlage:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.index = Index(tmp / "index.sqlite")
        self.service = ed.EnergieDashboardService(
            ed.EnergieDashboardDependencies(
                data_dir=tmp, index=self.index, tz=TZ, templates=None, app_root_context=None,
            )
        )

    def zaehler(self, entity_id: str, *werte: tuple[_dt.datetime, float]) -> None:
        self.index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
        for zeitpunkt, stand in werte:
            self.index.record_write(entity_id, zeitpunkt.timestamp())
            hotbuffer.append(self.tmp, entity_id, zeitpunkt.timestamp(), stand, TZ)

    def close(self) -> None:
        self.index.close()


def _tag(tag: int, stunde: float) -> _dt.datetime:
    return _dt.datetime(2024, 4, tag, tzinfo=TZ) + _dt.timedelta(hours=stunde)


@pytest.fixture()
def tmp(tmp_path_factory) -> Path:
    d = Path(tempfile.mkdtemp(prefix="zeitarchiv-edash-detail-", dir=tmp_path_factory.mktemp("edash")))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _anlage_mit_speicher(tmp: Path) -> tuple[_Anlage, dict]:
    """Drei Tage mit Daten (1., 5., 10. April) — dazwischen bewusst Lücken,
    damit die Buckets erkennbar EINZELNEN Tagen zugeordnet sind, nicht nur
    einer Monatssumme. Speicher lädt/entlädt an Tag 1 und 5 symmetrisch
    (kein Nettoeffekt), an Tag 10 asymmetrisch (Netto-Entladung 4 kWh) —
    genau dort muss Eigenversorgung von Eigenverbrauch abweichen.

    Zusätzlicher Messpunkt bei NOW (15. April, Zählerstand unverändert seit
    dem 10.) für jede Entität: _entity_series() ersetzt bei offset=0 den
    zeitlich LETZTEN Bucket durch eine frische Tages-Abfrage für "heute"
    (siehe deren Docstring) — ohne einen Messpunkt exakt an NOW wäre der
    10. selbst (der sonst letzte Bucket mit Daten) fälschlich das Ziel
    dieses Ersetzens und ginge dabei verloren, weil der 15. selbst dann
    keine eigene Kontur hätte. Delta 0, ändert an keiner erwarteten Summe
    etwas."""
    a = _Anlage(tmp)
    a.zaehler("sensor.netz", (_tag(1, 0), 100.0), (_tag(1, 23), 104.0),
              (_tag(5, 0), 104.0), (_tag(5, 23), 110.0),
              (_tag(10, 0), 110.0), (_tag(10, 23), 113.0), (NOW, 113.0))
    a.zaehler("sensor.pv", (_tag(1, 0), 500.0), (_tag(1, 23), 520.0),
              (_tag(5, 0), 520.0), (_tag(5, 23), 560.0),
              (_tag(10, 0), 560.0), (_tag(10, 23), 600.0), (NOW, 600.0))
    a.zaehler("sensor.einspeisung", (_tag(1, 0), 50.0), (_tag(1, 23), 58.0),
              (_tag(5, 0), 58.0), (_tag(5, 23), 70.0),
              (_tag(10, 0), 70.0), (_tag(10, 23), 80.0), (NOW, 80.0))
    a.zaehler("sensor.speicher_laden", (_tag(1, 0), 10.0), (_tag(1, 23), 12.0),
              (_tag(5, 0), 12.0), (_tag(5, 23), 14.0),
              (_tag(10, 0), 14.0), (_tag(10, 23), 15.0), (NOW, 15.0))
    a.zaehler("sensor.speicher_entladen", (_tag(1, 0), 5.0), (_tag(1, 23), 7.0),
              (_tag(5, 0), 7.0), (_tag(5, 23), 9.0),
              (_tag(10, 0), 9.0), (_tag(10, 23), 14.0), (NOW, 14.0))

    config = ed._empty_config()
    config.update({
        "netzbezug": "sensor.netz",
        "einspeisung": "sensor.einspeisung",
        "erzeuger": [{"entity_id": "sensor.pv", "name": "Dach-PV"}],
        "speicher": [{
            "name": "Heimspeicher",
            "laden_entity_id": "sensor.speicher_laden",
            "entladen_entity_id": "sensor.speicher_entladen",
        }],
        "hub_name": "Haus",
    })
    return a, config


def _series(monkeypatch, tmp: Path):
    a, config = _anlage_mit_speicher(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    read_cache = query_mod.QueryReadCache()
    result = a.service._detail_bucket_series(  # noqa: SLF001 — Verhaltenstest der internen Hilfsfunktion
        config, "month", 0, NOW, read_cache,
    )
    return result, a


def test_periodensummen_entsprechen_der_bilanz_identitaet(monkeypatch, tmp: Path) -> None:
    """Netzbezug 4+6+3=13, Erzeugung 20+40+40=100, Einspeisung 8+12+10=30,
    Speicherladung 2+2+1=5, Speicherentladung 2+2+5=9 — Verbrauch daraus
    exakt wie in _monatsverlauf_for_year(): netz+erz+entladen-einsp-laden."""
    result, a = _series(monkeypatch, tmp)
    try:
        assert sum(result["netzbezug"].values()) == pytest.approx(13.0)
        assert sum(result["erzeugung"].values()) == pytest.approx(100.0)
        assert sum(result["einspeisung"].values()) == pytest.approx(30.0)
        erwarteter_verbrauch = 13.0 + 100.0 + 9.0 - 30.0 - 5.0
        assert sum(result["verbrauch"].values()) == pytest.approx(erwarteter_verbrauch)
    finally:
        a.close()


def test_eigenverbrauch_und_eigenversorgung_unterscheiden_sich_um_speicher_netto(
    monkeypatch, tmp: Path,
) -> None:
    """Ohne Speicher wären beide identisch (dieselbe physikalische Größe von
    zwei Seiten). Mit Netto-Speicherentladung (hier 9-5=4 kWh) liegt
    Eigenversorgung um genau diese 4 kWh über Eigenverbrauch — sie zählt die
    Speicherentladung als zusätzliche, nicht aus dem Netz stammende
    Versorgung mit, was Eigenverbrauch (reine PV-Größe) nicht tut."""
    result, a = _series(monkeypatch, tmp)
    try:
        eigenverbrauch_total = sum(result["eigenverbrauch"].values())
        eigenversorgung_total = sum(result["eigenversorgung"].values())
        assert eigenverbrauch_total == pytest.approx(70.0)  # erz100 - einsp30
        assert eigenversorgung_total == pytest.approx(74.0)  # verbrauch87 - netz13
        assert eigenversorgung_total - eigenverbrauch_total == pytest.approx(4.0)
    finally:
        a.close()


def test_tagesbuckets_sind_einzelnen_tagen_zugeordnet(monkeypatch, tmp: Path) -> None:
    """Der eigentliche Zweck der Funktion: nicht nur eine Periodensumme,
    sondern ein Bucket je Tag mit Daten. Day 10 hat wegen der asymmetrischen
    Speichernutzung eine andere Eigenverbrauch/Eigenversorgung-Differenz als
    Tag 1/5 (dort 0, an Tag 10 die vollen 4 kWh) — das lässt sich nur auf
    Tagesebene zeigen, nicht an der Periodensumme allein."""
    result, a = _series(monkeypatch, tmp)
    try:
        netzbezug_tage = sorted(result["netzbezug"].items())
        assert len(netzbezug_tage) >= 3, "Erwartet mindestens einen Bucket je beliefertem Tag (1./5./10.)"
        werte = sorted(round(v, 3) for v in result["netzbezug"].values() if v)
        assert werte == [3.0, 4.0, 6.0]

        diff_je_tag = {
            ts: round(result["eigenversorgung"].get(ts, 0.0) - result["eigenverbrauch"].get(ts, 0.0), 3)
            for ts in set(result["eigenverbrauch"]) | set(result["eigenversorgung"])
        }
        # Nur EIN Tag (der 10., mit Netto-Entladung) darf eine Abweichung
        # ungleich 0 zeigen — an Tag 1/5 war Ladung == Entladung.
        abweichende_tage = [v for v in diff_je_tag.values() if abs(v) > 0.01]
        assert abweichende_tage == pytest.approx([4.0])
    finally:
        a.close()


def test_ohne_erzeuger_oder_netzbezug_bleiben_serien_leer(monkeypatch, tmp: Path) -> None:
    """Kein Sonderfall im Code nötig — series() liefert für eine leere
    entity_id einfach {} zurück, die Bilanz-Identität rechnet klaglos mit
    Nullen weiter."""
    a = _Anlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    config = ed._empty_config()
    read_cache = query_mod.QueryReadCache()
    try:
        result = a.service._detail_bucket_series(config, "month", 0, NOW, read_cache)  # noqa: SLF001
        assert result["erzeugung"] == {}
        assert result["netzbezug"] == {}
        assert result["verbrauch"] == {}
    finally:
        a.close()


# ---------------------------------------------------------------------------
# _staerkster_schwaechster_tag() / _is_outage_day() / _strongest_weakest_row()
#
# Nutzt dieselbe Anlage wie oben (Tag 1/5/10 mit Daten, Tag 15 = NOW mit
# Delta 0 überall — das ist zugleich der Ausfalltag-Testfall: Erzeugung UND
# Verbrauch sind dort beide 0, darf also nirgends als Rekord auftauchen).
# Erwartete Tageswerte (Hand gerechnet, siehe Kommentare oben in der Datei):
#   Tag  1: Netzbezug=4, Erzeugung=20, Einspeisung=8,  Verbrauch=16, Eigenverbrauch=12, Eigenversorgung=12
#   Tag  5: Netzbezug=6, Erzeugung=40, Einspeisung=12, Verbrauch=34, Eigenverbrauch=28, Eigenversorgung=28
#   Tag 10: Netzbezug=3, Erzeugung=40, Einspeisung=10, Verbrauch=37, Eigenverbrauch=30, Eigenversorgung=34
# ---------------------------------------------------------------------------

def _rows(monkeypatch, tmp: Path):
    a, config = _anlage_mit_speicher(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    read_cache = query_mod.QueryReadCache()
    rows = a.service._staerkster_schwaechster_tag(config, "month", 0, NOW, read_cache)  # noqa: SLF001
    return {row["label"]: row for row in rows}, a


def test_ausfalltag_taucht_nirgends_als_rekord_auf(monkeypatch, tmp: Path) -> None:
    """Tag 15 (NOW) hat Erzeugung=Verbrauch=0 — ein klassischer Ausfalltag,
    kein echter "bester Verbrauchstag" (0 kWh Verbrauch wäre sonst für
    Verbrauch, wo weniger besser ist, fälschlich der Bestwert)."""
    rows, a = _rows(monkeypatch, tmp)
    try:
        for row in rows.values():
            for seite in ("best", "worst"):
                eintrag = row[seite]
                if eintrag is not None:
                    assert "15." not in eintrag["date_label"], f"{row['label']}/{seite}: {eintrag}"
    finally:
        a.close()


def test_stromertrag_gleichstand_gewinnt_chronologisch_frueherer_tag(monkeypatch, tmp: Path) -> None:
    """Tag 5 und Tag 10 haben beide 40 kWh Erzeugung — Tag 5 muss gewinnen
    (chronologisch früher), nicht per Zufall irgendeiner der beiden."""
    rows, a = _rows(monkeypatch, tmp)
    try:
        stromertrag = rows["Stromertrag"]
        assert stromertrag["best"]["date_label"] == "5. Apr"
        assert stromertrag["best"]["value"] == pytest.approx(40.0)
        assert stromertrag["worst"]["date_label"] == "1. Apr"
        assert stromertrag["worst"]["value"] == pytest.approx(20.0)
        # (20+40+40)/3 — der Bezugspunkt fürs "ggü. Ø X" im Bericht.
        assert stromertrag["avg"] == pytest.approx(33.3, abs=0.1)
    finally:
        a.close()


def test_netzbezug_und_verbrauch_sind_invertiert(monkeypatch, tmp: Path) -> None:
    """Bei Netzbezug/Verbrauch ist "bester Tag" der mit dem NIEDRIGSTEN
    Wert — Tag 10 hat den niedrigsten Netzbezug (3) und sollte dort als
    bester Tag erscheinen, obwohl er bei Erzeugung der (gleich-)höchste ist."""
    rows, a = _rows(monkeypatch, tmp)
    try:
        netzbezug = rows["Netzbezug"]
        assert netzbezug["best"]["date_label"] == "10. Apr"
        assert netzbezug["best"]["value"] == pytest.approx(3.0)
        assert netzbezug["worst"]["date_label"] == "5. Apr"
        assert netzbezug["worst"]["value"] == pytest.approx(6.0)

        verbrauch = rows["Verbrauch"]
        assert verbrauch["best"]["date_label"] == "1. Apr"
        assert verbrauch["best"]["value"] == pytest.approx(16.0)
        assert verbrauch["worst"]["date_label"] == "10. Apr"
        assert verbrauch["worst"]["value"] == pytest.approx(37.0)
    finally:
        a.close()


def test_autarkie_und_eigenverbrauch_als_tagesquote_in_prozent(monkeypatch, tmp: Path) -> None:
    """Autarkie = (Verbrauch-Netzbezug)/Verbrauch, Eigenverbrauch =
    Eigenverbrauch/Erzeugung — beide als Tagesquote in %, mit Delta in
    Prozentpunkten (as_points=True), nicht als relative %-Änderung wie bei
    den kWh-Kennzahlen."""
    rows, a = _rows(monkeypatch, tmp)
    try:
        autarkie = rows["Autarkie"]
        assert autarkie["is_pkt"] is True
        # Tag 10: (37-3)/37*100 = 91.9 % — der höchste der drei Tage.
        assert autarkie["best"]["date_label"] == "10. Apr"
        assert autarkie["best"]["value"] == pytest.approx(91.9, abs=0.1)
        # Tag 1: (16-4)/16*100 = 75.0 % — der niedrigste.
        assert autarkie["worst"]["date_label"] == "1. Apr"
        assert autarkie["worst"]["value"] == pytest.approx(75.0, abs=0.1)

        eigenverbrauch = rows["Eigenverbrauch"]
        # Tag 10: 30/40*100 = 75 %, Tag 1: 12/20*100 = 60 %.
        assert eigenverbrauch["best"]["date_label"] == "10. Apr"
        assert eigenverbrauch["best"]["value"] == pytest.approx(75.0, abs=0.1)
        assert eigenverbrauch["worst"]["date_label"] == "1. Apr"
        assert eigenverbrauch["worst"]["value"] == pytest.approx(60.0, abs=0.1)
    finally:
        a.close()


def test_ohne_gueltigen_tag_bleiben_best_und_worst_none(monkeypatch, tmp: Path) -> None:
    """Eine Anlage ganz ohne Daten (nur der Ausfalltag NOW) darf nicht mit
    einem falschen "Rekord" aus lauter Nullen antworten."""
    a = _Anlage(tmp)
    a.zaehler("sensor.netz", (NOW, 100.0))
    a.zaehler("sensor.pv", (NOW, 500.0))
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    config = ed._empty_config()
    config.update({"netzbezug": "sensor.netz", "erzeuger": [{"entity_id": "sensor.pv", "name": "PV"}], "hub_name": "Haus"})
    read_cache = query_mod.QueryReadCache()
    try:
        rows = {row["label"]: row for row in a.service._staerkster_schwaechster_tag(config, "month", 0, NOW, read_cache)}  # noqa: SLF001
        assert rows["Stromertrag"]["best"] is None
        assert rows["Stromertrag"]["worst"] is None
    finally:
        a.close()


def test_bar_chart_geometry_stapelt_comp_b_auf_comp_a() -> None:
    """Regressionstest für einen echten Bug: comp_a und comp_b wurden zuerst
    beide unabhängig von der Grundlinie aus nach oben gezeichnet (statt
    gestapelt) — der jeweils kleinere Anteil verschwand dadurch komplett
    unter dem größeren, statt als eigenes, farblich erkennbares Segment zu
    erscheinen (in der Optik: der Legenden-Farbton stimmte nicht mit dem
    tatsächlich sichtbaren Balken überein). comp_b muss oberhalb von comp_a
    beginnen, nicht bei derselben y-Koordinate."""
    current = [{"label": "1.–7.", "a": 30.0, "b": 10.0, "day_count": 7}]
    previous = [{"label": "1.–7.", "a": 20.0, "b": 5.0, "day_count": 7}]
    geometry = ed.EnergieDashboardService._bar_chart_geometry(  # noqa: SLF001
        current, previous, "a", "b", current_avg=40.0, previous_avg=25.0,
    )
    bar = geometry["bars"][0]["current"]
    # comp_a reicht von der Grundlinie bis a_y — comp_b schließt GENAU DORT
    # an (b_y + b_h == a_y), überlappt also nicht mit comp_a.
    assert bar["a_y"] + bar["a_h"] == pytest.approx(geometry["baseline_y"])
    assert bar["b_y"] + bar["b_h"] == pytest.approx(bar["a_y"])
    # Und die Summe beider Segmente ergibt exakt den Hintergrundbalken (die
    # tatsächlich gemessene Gesamtsumme comp_a+comp_b).
    assert bar["b_y"] == pytest.approx(bar["backing_y"])
