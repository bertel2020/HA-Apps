"""Ein NaN darf nicht ins Archiv — und darf auch keinen Batch mitreißen.

Hintergrund (ZG-24 in CODE_ANALYSE.md): `float("nan")` ist in Python ein
gültiger Aufruf, kein Fehler. Ein Home-Assistant-Sensor, dessen Zustand als
"nan" oder "inf" rendert, passiert deshalb den Filter der Integration
unbeschadet, und `json.dumps` schreibt `NaN` klaglos in den Batch. Gespeichert
wurde er bis September 2026 genauso klaglos.

Der Schaden entsteht erst danach, und er ist dauerhaft: ein NaN zieht jeden
Aggregat-Eimer mit, in den es fällt — aus einem gültigen 10,0 daneben wird
NaN, nicht 10,0 — und Starlette rendert JSON mit `allow_nan=False`. Aus einem
einzelnen Messwert wird damit ein HTTP 500 auf jede Chart-, Tabellen- und
Dashboardabfrage, die seinen Zeitraum berührt, bis der Punkt von Hand gelöscht
ist.

Diese Datei prüft beide Hälften der Lösung:

1. Solche Events werden **einzeln** aussortiert (`skipped`), nicht als 422
   zurückgewiesen. Der Queue-Writer der Integration wiederholt einen Batch
   "bis zum Erfolg oder bis zum expliziten Stopp" und unterscheidet dabei
   nicht zwischen 4xx und 5xx — eine Ablehnung des ganzen Batches hielte
   deshalb dauerhaft auch die Messwerte aller anderen Entitäten darin fest.
2. Was danach im Archiv liegt, ist als JSON darstellbar. Diese Zusage prüft
   die Wirkung statt der Schreibweise und bleibt damit auch gültig, wenn der
   Filter einmal anders gebaut wird.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from starlette.responses import JSONResponse

import _paths  # noqa: F401
from app.limits import MAX_EVENT_TEXT_LENGTH, MAX_EVENT_TS, MIN_EVENT_TS
from app.storage import query
from app.storage.index import Index
from app.storage.ingestion import IngestEvent, IngestionService, _is_storable_measurement

TZ = ZoneInfo("Europe/Berlin")
BASE_TS = 1_757_000_000.0

#: Jeder Fall einmal, mit dem Grund, aus dem er nicht speicherbar ist.
UNSTORABLE = [
    ("value=NaN", BASE_TS, float("nan")),
    ("value=Infinity", BASE_TS, float("inf")),
    ("value=-Infinity", BASE_TS, float("-inf")),
    ("ts=NaN", float("nan"), 1.0),
    ("ts=Infinity", float("inf"), 1.0),
    ("ts im Jahr 10000", 253_402_300_800.0, 1.0),
    ("ts im Jahr 1", -62_135_596_800.0, 1.0),
    ("ts in Millisekunden statt Sekunden", BASE_TS * 1000, 1.0),
    ("ts=0", 0.0, 1.0),
]

STORABLE = [
    ("normaler Messwert", BASE_TS, 21.5),
    ("negativer Messwert", BASE_TS, -273.15),
    ("Null", BASE_TS, 0.0),
    ("sehr großer endlicher Wert", BASE_TS, 1e300),
    ("untere Fenstergrenze", MIN_EVENT_TS, 1.0),
    ("obere Fenstergrenze", MAX_EVENT_TS, 1.0),
]


def _event(ts: float, value: float, entity_id: str = "sensor.zg24_probe") -> IngestEvent:
    return IngestEvent(
        event_id=uuid.uuid4().hex,
        entity_id=entity_id,
        domain="sensor",
        ts=ts,
        value=value,
        state_class="measurement",
    )


@pytest.fixture
def service(tmp_path: Path) -> tuple[IngestionService, Index, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    index = Index(data_dir / "index.sqlite")
    return IngestionService(data_dir, index, TZ), index, data_dir


@pytest.mark.parametrize(("label", "ts", "value"), UNSTORABLE, ids=[c[0] for c in UNSTORABLE])
def test_unstorable_measurements_are_recognized(label: str, ts: float, value: float) -> None:
    assert not _is_storable_measurement(_event(ts, value)), label


@pytest.mark.parametrize(("label", "ts", "value"), STORABLE, ids=[c[0] for c in STORABLE])
def test_ordinary_measurements_stay_storable(label: str, ts: float, value: float) -> None:
    """Gegenprobe — der Filter darf nicht mehr aussortieren als er soll."""
    assert _is_storable_measurement(_event(ts, value)), label


@pytest.mark.parametrize(("label", "ts", "value"), UNSTORABLE, ids=[c[0] for c in UNSTORABLE])
def test_an_unstorable_event_is_skipped_without_side_effects(
    service, label: str, ts: float, value: float
) -> None:
    """Weder Entität noch Ledger-Eintrag — und damit folgenlos wiederholbar.

    Das ist der Grund, warum der Filter vor `get_or_create_entity()` und vor
    dem Claim steht: ein Sensor, der ausschließlich NaN liefert, soll in der
    Entitätenliste gar nicht erst auftauchen.
    """
    ingestion, index, data_dir = service
    assert ingestion.ingest(_event(ts, value)) == "skipped", label
    assert index.get_entity("sensor.zg24_probe") is None
    ledger = sqlite3.connect(data_dir / "index.sqlite")
    assert ledger.execute("SELECT COUNT(*) FROM ingested_events").fetchone()[0] == 0
    assert not list(data_dir.rglob("*.parquet"))
    assert not list(data_dir.rglob("*.csv"))


def test_one_bad_event_does_not_take_the_rest_of_the_batch_with_it(service) -> None:
    """Die eigentliche Zusage. Vorher riss ein einziges NaN im ts den ganzen
    Batch mit (`except Exception: … raise`, HTTP 500) — und weil der
    Queue-Writer der Integration jeden Fehler unbegrenzt wiederholt, blieb er
    dauerhaft hängen, samt der Messwerte aller anderen Entitäten darin."""
    ingestion, index, _ = service
    batch = [
        _event(BASE_TS + 0, 10.0, "sensor.zg24_gut"),
        _event(BASE_TS + 60, float("nan"), "sensor.zg24_kaputt"),
        _event(BASE_TS + 120, 30.0, "sensor.zg24_gut"),
        _event(float("inf"), 1.0, "sensor.zg24_kaputt"),
        _event(BASE_TS + 180, 40.0, "sensor.zg24_gut"),
    ]
    results = [ingestion.ingest(event) for event in batch]
    assert results == ["written", "skipped", "written", "skipped", "written"]
    assert index.get_entity("sensor.zg24_gut")["row_count"] == 3
    assert index.get_entity("sensor.zg24_kaputt") is None


def test_the_archive_stays_renderable_as_json(service) -> None:
    """Wirkungsprüfung statt Mechanikprüfung.

    Starlette rendert mit `allow_nan=False` und wirft bei einem NaN
    `ValueError: Out of range float values are not JSON compliant` — genau der
    500er, um den es hier geht. Diese Zusage bliebe deshalb auch dann gültig,
    wenn der Filter einmal ganz woanders säße.
    """
    ingestion, index, data_dir = service
    entity_id = "sensor.zg24_json"
    ingestion.ingest(_event(BASE_TS + 0, 10.0, entity_id))
    ingestion.ingest(_event(BASE_TS + 60, float("nan"), entity_id))
    ingestion.ingest(_event(BASE_TS + 120, 30.0, entity_id))

    now = datetime.fromtimestamp(BASE_TS + 300, TZ)
    series = query.query_series(data_dir, index, entity_id, "day", TZ, now)
    JSONResponse(series).render(series)  # wirft, sobald ein NaN darin steckt

    werte = [point["value"] for point in series["points"] if point["value"] is not None]
    assert werte, "keine Punkte — dann prüft der Test nichts"
    assert all(math.isfinite(value) for value in werte)
    assert 10.0 in werte, "der gültige Nachbarwert im selben Eimer darf nicht verschwinden"


def test_the_import_parsers_reject_the_same_values() -> None:
    """CSV und Symcon schreiben am Live-Weg vorbei direkt ins Archiv.

    `float("nan")` ist auch dort ein gültiger Aufruf, und `"1e400"` läuft ohne
    Ausnahme nach `inf` über. None heißt in beiden Parsern „nicht lesbar" und
    zählt als übersprungene Zeile — im Dry Run sichtbar.
    """
    from app.storage.csv_import import _parse_timestamp, _parse_value
    from app.storage.symcon_import import _parse_value as symcon_value

    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "1e400"):
        assert _parse_value(raw) is None, f"csv_import akzeptiert {raw!r}"
        assert symcon_value(raw) is None, f"symcon_import akzeptiert {raw!r}"
        assert _parse_timestamp(raw, "unix_s", "", TZ) is None, f"csv-ts akzeptiert {raw!r}"

    # Gegenprobe: die gewöhnlichen Formen beider Parser bleiben unberührt.
    assert _parse_value("21,5") == 21.5
    assert _parse_value("-7") == -7.0
    assert symcon_value("true") == 1.0
    assert _parse_timestamp("1757000000", "unix_s", "", TZ) == BASE_TS


def test_a_malformed_message_is_still_rejected_outright() -> None:
    """Die andere Hälfte der Trennlinie.

    Ein überlanges Metadatenfeld ist kein Datenzustand, sondern ein
    fehlerhafter Client — dafür bleibt 422 richtig. Ohne diese Zusage könnte
    jemand die Grenze aus Symmetrie-Gründen später ebenfalls in ein „skipped"
    verwandeln und damit ein 100.000 Zeichen langes Feld in Index und Logs
    durchlassen.
    """
    from app.api_routes import EventIn

    gemeinsam = {"entity_id": "sensor.zg24_probe", "domain": "sensor", "ts": BASE_TS, "value": 1.0}
    EventIn(**gemeinsam | {"friendly_name": "f" * MAX_EVENT_TEXT_LENGTH})
    with pytest.raises(ValidationError):
        EventIn(**gemeinsam | {"friendly_name": "f" * (MAX_EVENT_TEXT_LENGTH + 1)})

    # ts/value dagegen NICHT — sie gehen durchs Modell und werden erst im
    # Ingest je Event aussortiert (siehe Modulkommentar).
    assert math.isnan(EventIn(**gemeinsam | {"value": float("nan")}).value)


def test_the_write_endpoint_answers_200_and_reports_the_skip(client) -> None:
    """Ende zu Ende über HTTP: der Batch geht durch, die Quote ist sichtbar."""
    from app.main import index
    from app.security import ensure_api_token

    entity_id = "sensor.zg24_http"
    headers = {"Authorization": f"Bearer {ensure_api_token(index)}"}
    body = json.dumps({
        "events": [
            {"entity_id": entity_id, "domain": "sensor", "ts": BASE_TS + 600, "value": 1.0},
            {"entity_id": entity_id, "domain": "sensor", "ts": BASE_TS + 660, "value": float("nan")},
            {"entity_id": entity_id, "domain": "sensor", "ts": BASE_TS + 720, "value": 3.0},
        ]
    })
    response = client.post(
        "/api/write", content=body, headers=headers | {"Content-Type": "application/json"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["written"] == 2
    assert response.json()["skipped"] == 1
