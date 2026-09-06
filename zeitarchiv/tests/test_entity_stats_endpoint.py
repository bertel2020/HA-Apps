"""Verhaltenstests für /api/entity-stats — die Datenquelle der Werte-Kacheln.

Die Kacheln holten bisher je Kachel die kompletten Rohpunkte des Tages und
rechneten aktuellen Wert, Alter und Sparkline im Browser. Dieser Endpunkt
bündelt das für mehrere Kacheln, dünnt die Sparkline serverseitig aus und
liefert zusätzlich die Kennzahlen.

Aufbau wie test_energiedashboard_flow.py: eigenes tmp-Verzeichnis, echter
Index, Werte über hotbuffer.append(), feste Uhr. Der Router wird in eine
eigene FastAPI-App gehängt statt die ganze app.main zu starten — so hängt
der Test an nichts, was diese Routen nicht selbst brauchen.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import api_routes
    from app.storage import hotbuffer
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index
    from app.storage.ingestion import IngestionService

    _AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne fastapi/starlette
    _AVAILABLE = False

import pytest

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="fastapi/starlette fehlt")

TZ = ZoneInfo("Europe/Berlin")
# Fester "Jetzt"-Zeitpunkt mitten am Tag: der Kalendertag ist damit angefangen
# (nicht leer, nicht abgeschlossen), sonst hinge das Ergebnis am Testlauf.
NOW = _dt.datetime(2024, 3, 15, 12, 0, 0, tzinfo=TZ)


def _ts(stunde: float) -> float:
    return _dt.datetime(2024, 3, 15, tzinfo=TZ).timestamp() + stunde * 3600


class _FesteUhr(_dt.datetime):
    """Die Route ruft datetime.now(tz) selbst auf."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW.astimezone(tz) if tz else NOW


@pytest.fixture
def anlage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Zwei Entitäten mit echten Werten über den halben Tag.

    ``sensor.temperatur`` ist ein Messwert (Momentanwerte, Min/Ø/Max sinnvoll,
    Summe nicht), ``sensor.zaehler`` ein Zähler (steigende Zählerstände, erst
    die Bucket-Deltas ergeben eine sinnvolle Summe).
    """
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity("sensor.temperatur", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.zaehler", "sensor", "total_increasing", "kWh")

    # 0..11 Uhr, stündlich. Temperatur schwankt, der Zähler steigt um 2 je Stunde.
    temperaturen = [11.0, 10.0, 10.5, 12.0, 14.0, 16.0, 18.0, 19.5, 21.0, 22.0, 23.0, 20.0]
    for stunde, wert in enumerate(temperaturen):
        hotbuffer.append(tmp_path, "sensor.temperatur", _ts(stunde), wert, TZ)
        hotbuffer.append(tmp_path, "sensor.zaehler", _ts(stunde), 100.0 + stunde * 2, TZ)

    coordinator = StorageCoordinator()
    deps = api_routes.ApiDependencies(
        data_dir=tmp_path,
        index=index,
        tz=TZ,
        coordinator=coordinator,
        ingestion=IngestionService(tmp_path, index, TZ, coordinator),
        api_token=lambda: "test-token",
        app_version="0.0.0-test",
        collect_notices=lambda: [],
    )
    app = FastAPI()
    app.include_router(api_routes.create_api_router(deps, api_routes.ApiState()))
    monkeypatch.setattr(api_routes, "datetime", _FesteUhr)
    with TestClient(app) as client:
        yield client
    index.close()


def _serie(payload: dict, entity_id: str) -> dict:
    return next(s for s in payload["series"] if s["entity_id"] == entity_id)


def test_without_stats_the_endpoint_only_does_what_the_old_raw_fetch_did(anlage) -> None:
    """Eine Kachel in Standardkonfiguration soll den Endpunkt nicht teurer
    machen als den bisherigen Roh-Request: aktueller Wert, Alter, Punkte —
    aber keine Kennzahlen und damit auch keine gebucketete Zweitabfrage."""
    payload = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day"
    ).json()
    serie = _serie(payload, "sensor.temperatur")
    assert serie["last"] == 20.0
    assert serie["last_ts"] == _ts(11)
    assert serie["aggregates"] is None
    assert len(serie["points"]) == 12
    assert payload["sparkline_source"] == "raw"


def test_measurement_gets_min_avg_max_but_no_sum(anlage) -> None:
    """Die Summe von Momentanwerten (20 °C + 21 °C + …) ist keine Temperatur —
    dieselbe Regel wie in der Chart-Legende."""
    payload = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day&stats=true"
    ).json()
    werte = _serie(payload, "sensor.temperatur")["aggregates"]
    assert werte["min"] == 10.0
    assert werte["max"] == 23.0
    assert werte["sum"] is None
    assert 10.0 < werte["avg"] < 23.0


def test_counter_sum_comes_from_bucket_deltas_not_from_raw_readings(anlage) -> None:
    """Der Kern der Aufteilung: Rohwerte eines Zählers sind Zählerstände
    (100, 102, … 122). Ihre Summe wäre über 1300 — eine sinnlose Zahl in der
    Größenordnung "Zählerstand × Messpunkte". Erst query_series() bildet die
    Deltas, deren Summe der Verbrauch der Periode ist: 122 - 100 = 22."""
    payload = anlage.get(
        "/api/entity-stats?entity_ids=sensor.zaehler&range=day&stats=true"
    ).json()
    werte = _serie(payload, "sensor.zaehler")["aggregates"]
    assert werte["sum"] == pytest.approx(22.0)
    # Der aktuelle Wert bleibt der Zählerstand, kein Delta.
    assert _serie(payload, "sensor.zaehler")["last"] == 122.0


def test_resolution_thins_the_sparkline_on_the_server(anlage) -> None:
    """Zwölf Stundenwerte, ein Punkt je Stunde angefordert: die Punktzahl darf
    sich dadurch nicht ändern. Bei "raw" bleiben es ebenfalls zwölf — die
    Ausdünnung ist der einzige Unterschied, nicht das Fenster."""
    fein = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day&resolution=raw"
    ).json()
    grob = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day&resolution=1h"
    ).json()
    assert len(_serie(fein, "sensor.temperatur")["points"]) == 12
    assert len(_serie(grob, "sensor.temperatur")["points"]) == 12
    # Der letzte Punkt je Bucket bleibt erhalten, damit der aktuelle Stand steht.
    assert _serie(grob, "sensor.temperatur")["points"][-1]["value"] == 20.0


def test_several_tiles_come_back_in_one_request(anlage) -> None:
    payload = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur,sensor.zaehler&range=day&stats=true"
    ).json()
    assert [s["entity_id"] for s in payload["series"]] == [
        "sensor.temperatur", "sensor.zaehler",
    ]
    assert all(s["aggregates"] is not None for s in payload["series"])


def test_long_ranges_switch_to_buckets_instead_of_raw_points(anlage) -> None:
    """Ein Jahr an Rohpunkten sprengt MAX_RAW_QUERY_POINTS. Der Endpunkt sagt
    im Antwortfeld, womit die Sparkline gezeichnet wird, damit die
    Kachel-Einstellungen die Auflösungs-Reihe ausgrauen können, statt die
    Regel selbst zu kennen."""
    for range_key, quelle in [("week", "raw"), ("month", "buckets"), ("year", "buckets")]:
        payload = anlage.get(
            f"/api/entity-stats?entity_ids=sensor.zaehler&range={range_key}"
        ).json()
        assert payload["sparkline_source"] == quelle, range_key


def test_current_value_survives_a_range_without_raw_points(anlage) -> None:
    """Bei Monat/Jahr wäre der letzte Bucket eines Zählers ein Delta, kein
    Zählerstand. Der aktuelle Wert muss deshalb auch dort aus Rohwerten
    kommen — sonst zeigte die Kachel plötzlich den Monatsverbrauch als
    "aktuellen Wert"."""
    payload = anlage.get(
        "/api/entity-stats?entity_ids=sensor.zaehler&range=month"
    ).json()
    serie = _serie(payload, "sensor.zaehler")
    assert serie["last"] == 122.0
    assert serie["last_ts"] == _ts(11)


def test_rolling_window_differs_from_the_calendar_one(anlage) -> None:
    """"Tag" ist der Kalendertag ab Mitternacht, rollierend sind es die
    letzten 24 Stunden — zwei verschiedene Fenster, kein Anzeigeunterschied."""
    kalendarisch = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day"
    ).json()
    rollierend = anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day&continuous=true"
    ).json()
    assert kalendarisch["window_start"] == _ts(0)
    assert rollierend["window_start"] == NOW.timestamp() - 24 * 3600
    assert rollierend["window_end"] == NOW.timestamp()


def test_unknown_range_and_resolution_are_rejected(anlage) -> None:
    # "decade" kennt die Abfrage, die Kachel bietet es bewusst nicht an.
    assert anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=decade"
    ).status_code == 400
    assert anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur&range=day&resolution=2min"
    ).status_code == 400


def test_too_many_entities_are_refused_so_the_client_has_to_chunk(anlage) -> None:
    """Der Client gruppiert Kacheln nach (Zeitraum, rollierend, Auflösung) und
    muss dabei bei MAX_MULTI_QUERY_ENTITIES stückeln — ohne diese Grenze
    bliebe das unbemerkt, bis ein großes Dashboard sie überschreitet."""
    zuviele = ",".join(f"sensor.x{i}" for i in range(26))
    assert anlage.get(f"/api/entity-stats?entity_ids={zuviele}&range=day").status_code == 413


def test_a_default_tile_does_not_pay_for_the_bucketed_query(
    anlage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Kennzahlen brauchen eine zweite, gebucketete Abfrage. Eine Kachel
    ohne Kennzahlen darf sie nicht bezahlen — sonst würde der Umbau jedes
    bestehende Dashboard teurer machen, obwohl sich dort nichts ändert."""
    aufrufe: list[str] = []
    echt_series = api_routes.query_mod.query_series
    echt_raw = api_routes.query_mod.query_raw_series

    def zaehle_series(*args, **kwargs):
        aufrufe.append("series")
        return echt_series(*args, **kwargs)

    def zaehle_raw(*args, **kwargs):
        aufrufe.append("raw")
        return echt_raw(*args, **kwargs)

    monkeypatch.setattr(api_routes.query_mod, "query_series", zaehle_series)
    monkeypatch.setattr(api_routes.query_mod, "query_raw_series", zaehle_raw)

    anlage.get("/api/entity-stats?entity_ids=sensor.temperatur&range=day")
    assert aufrufe == ["raw"]

    aufrufe.clear()
    anlage.get("/api/entity-stats?entity_ids=sensor.temperatur&range=day&stats=true")
    assert aufrufe == ["raw", "series"]


def test_entities_of_one_group_share_one_read_cache(
    anlage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Sinn des Bündelns: alle Entitäten einer Gruppe liegen in derselben
    Anfrage und teilen sich denselben request-lokalen Lese-Cache, sodass eine
    gemeinsam genutzte Monats-CSV nur einmal geparst wird. Ohne gemeinsamen
    Cache brächte das Bündeln nur eine HTTP-Runde weniger."""
    caches: list[int] = []
    echt = api_routes.query_mod.query_series

    def merke_cache(*args, **kwargs):
        caches.append(id(kwargs.get("read_cache")))
        return echt(*args, **kwargs)

    monkeypatch.setattr(api_routes.query_mod, "query_series", merke_cache)
    anlage.get(
        "/api/entity-stats?entity_ids=sensor.temperatur,sensor.zaehler&range=day&stats=true"
    )
    assert len(caches) == 2
    assert caches[0] == caches[1]
    assert caches[0] != id(None)
