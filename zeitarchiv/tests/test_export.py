"""CSV-/ZIP-Export: Zeitraum, Auflösung, mehrere Entitäten (app/export_data.py,
app/export_routes.py).

Aufbau wie test_entity_migration.py: echte Archivmonate in einem tmp-Verzeichnis
(alle Werte weit vor dem laufenden Monat, damit der Test nicht am Datum hängt),
echter Index. Der Router hängt in einer eigenen FastAPI-App."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import export_data, export_routes
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index

try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def _ts(y: int, m: int, d: int, h: int = 0, minute: int = 0) -> float:
    return datetime(y, m, d, h, minute, tzinfo=TZ).timestamp()


def test_parse_period_end_day_is_inclusive() -> None:
    start, end = export_data.parse_period("2020-01-01", "2020-01-31", TZ)
    assert start == _ts(2020, 1, 1)
    assert end == _ts(2020, 2, 1)
    assert export_data.parse_period("", "", TZ) == (None, None)
    assert export_data.parse_period("2020-03-01", "", TZ) == (_ts(2020, 3, 1), None)


@pytest.mark.parametrize("start,end", [("2020-13-01", ""), ("morgen", ""), ("2020-02-01", "2020-01-01")])
def test_parse_period_rejects_bad_input(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        export_data.parse_period(start, end, TZ)


def test_file_suffix_names_year_month_and_free_ranges() -> None:
    assert export_data.file_suffix(None, None, "raw") == ""
    assert export_data.file_suffix("2025-01-01", "2025-12-31", "raw") == "_2025"
    assert export_data.file_suffix("2024-02-01", "2024-02-29", "hour") == "_2024-02_hour"
    assert export_data.file_suffix("2025-03-05", "2025-04-01", "raw") == "_2025-03-05_bis_2025-04-01"
    assert export_data.file_suffix("2025-03-05", None, "day") == "_2025-03-05_bis_ende_day"


def test_aggregate_rows_uses_mean_for_standard_and_last_value_for_counters() -> None:
    rows = [
        (_ts(2020, 1, 1, 10, 0), 10.0), (_ts(2020, 1, 1, 10, 30), 20.0),
        (_ts(2020, 1, 1, 11, 0), 40.0),
        (_ts(2020, 1, 2, 0, 0), 50.0),
    ]
    assert list(export_data.aggregate_rows(rows, "hour", "standard", TZ)) == [
        (_ts(2020, 1, 1, 10), 15.0), (_ts(2020, 1, 1, 11), 40.0), (_ts(2020, 1, 2, 0), 50.0),
    ]
    assert list(export_data.aggregate_rows(rows, "hour", "counter", TZ)) == [
        (_ts(2020, 1, 1, 10), 20.0), (_ts(2020, 1, 1, 11), 40.0), (_ts(2020, 1, 2, 0), 50.0),
    ]
    # Tage folgen der lokalen Mitternacht, nicht UTC.
    assert [ts for ts, _ in export_data.aggregate_rows(rows, "day", "switch", TZ)] == [_ts(2020, 1, 1), _ts(2020, 1, 2)]
    assert list(export_data.aggregate_rows([], "day", "standard", TZ)) == []


class _Setup:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = Index(root / "index.sqlite")

    def entity(self, entity_id: str, state_class: str, rows: list[tuple[float, float]]) -> None:
        self.index.get_or_create_entity(entity_id, "sensor", state_class, "x")
        by_month: dict[str, list[tuple[float, float]]] = {}
        for ts, value in rows:
            by_month.setdefault(datetime.fromtimestamp(ts, TZ).strftime("%Y-%m"), []).append((ts, value))
        for label, month_rows in by_month.items():
            folder = self.root / "archive" / entity_id
            folder.mkdir(parents=True, exist_ok=True)
            pq.write_table(
                pa.table({"ts": [r[0] for r in month_rows], "value": [r[1] for r in month_rows]}),
                folder / f"{label}.parquet",
            )
        for ts, value in sorted(rows):
            self.index.record_write(entity_id, ts, value)


@pytest.fixture
def setup(tmp_path: Path):
    prepared = _Setup(tmp_path)
    prepared.entity("sensor.a", "measurement", [
        (_ts(2020, 1, 1, 10, 0), 10.0), (_ts(2020, 1, 1, 10, 30), 20.0), (_ts(2020, 1, 1, 11, 0), 40.0),
        (_ts(2020, 2, 3, 8, 0), 5.0), (_ts(2020, 2, 3, 9, 0), 7.0),
    ])
    prepared.entity("sensor.b", "total_increasing", [
        (_ts(2020, 1, 1, 10, 0), 100.0), (_ts(2020, 1, 1, 10, 30), 101.0), (_ts(2020, 2, 3, 8, 0), 102.0),
    ])
    yield prepared
    prepared.index.close()


@pytest.fixture
def client(setup):
    if not _AVAILABLE:
        pytest.skip("fastapi/starlette fehlt")
    app = FastAPI()
    app.include_router(export_routes.create_export_router(export_routes.ExportDependencies(
        index=setup.index, data_dir=setup.root, tz=TZ, coordinator=StorageCoordinator(),
        templates=None,  # type: ignore[arg-type] — nur die Daten-Routen werden gerufen
        visible_row_count=lambda entity: int(entity["row_count"] or 0),
        paginate_meta=lambda total, page, size: {},
    )))
    return TestClient(app)


def _lines(response) -> list[str]:
    return response.text.strip().split("\r\n")


def test_estimate_counts_whole_months_exactly_and_caps_aggregates(client) -> None:
    body = client.post("/export/estimate", json={"entity_ids": ["sensor.a", "sensor.b"], "start": "2020-01-01", "end": "2020-01-31"}).json()
    by_id = {f["entity_id"]: f for f in body["files"]}
    assert by_id["sensor.a"]["rows"]["raw"] == 3
    assert by_id["sensor.b"]["rows"]["raw"] == 2
    assert by_id["sensor.a"]["rows"]["day"] <= 3
    assert by_id["sensor.a"]["first"] == "2020-01-01" and by_id["sensor.a"]["last"] == "2020-02-03"
    assert body["limit"] == export_routes.MAX_EXPORT_ROWS
    empty = client.post("/export/estimate", json={"entity_ids": ["sensor.a"], "start": "2021-01-01", "end": "2021-01-31"}).json()
    assert empty["files"][0]["rows"] == {"raw": 0, "minute": 0, "hour": 0, "day": 0}


def test_single_entity_without_options_is_the_complete_raw_csv(client) -> None:
    response = client.get("/export/download", params={"entity_id": "sensor.a"})
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="sensor.a.csv"' in response.headers["content-disposition"]
    lines = _lines(response)
    assert lines[0] == "timestamp,unix_ts,value"
    assert len(lines) == 1 + 5
    assert lines[1].startswith("2020-01-01 10:00:00,")


def test_period_limits_the_rows_and_names_the_file(client) -> None:
    response = client.get("/export/download", params={"entity_id": "sensor.a", "start": "2020-02-01", "end": "2020-02-29"})
    assert 'filename="sensor.a_2020-02.csv"' in response.headers["content-disposition"]
    assert len(_lines(response)) == 1 + 2


def test_resolution_aggregates_per_interval(client) -> None:
    response = client.get("/export/download", params={"entity_id": "sensor.a", "start": "2020-01-01", "end": "2020-01-31", "resolution": "hour"})
    lines = _lines(response)
    assert 'filename="sensor.a_2020-01_hour.csv"' in response.headers["content-disposition"]
    assert [line.split(",")[0] for line in lines[1:]] == ["2020-01-01 10:00:00", "2020-01-01 11:00:00"]
    assert [line.split(",")[2] for line in lines[1:]] == ["15", "40"]


def test_several_entities_come_as_one_zip_with_one_csv_each(client) -> None:
    response = client.post("/export/download", data={
        "entity_id": ["sensor.a", "sensor.b"], "start": "2020-01-01", "end": "2020-01-31", "resolution": "raw",
    })
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="zeitarchiv-export_2-entitaeten_2020-01.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == ["sensor.a_2020-01.csv", "sensor.b_2020-01.csv"]
        a = archive.read("sensor.a_2020-01.csv").decode().strip().split("\r\n")
        b = archive.read("sensor.b_2020-01.csv").decode().strip().split("\r\n")
    assert len(a) == 1 + 3 and len(b) == 1 + 2
    assert a[0] == b[0] == "timestamp,unix_ts,value"


def test_over_the_limit_is_rejected_before_streaming(client, monkeypatch) -> None:
    monkeypatch.setattr(export_routes, "MAX_EXPORT_ROWS", 2)
    assert client.get("/export/download", params={"entity_id": "sensor.a"}).status_code == 413
    # Ein Monat passt (2 Zeilen), der andere nicht (3).
    assert client.get("/export/download", params={"entity_id": "sensor.a", "start": "2020-02-01", "end": "2020-02-29"}).status_code == 200
    assert client.get("/export/download", params={"entity_id": "sensor.a", "start": "2020-01-01", "end": "2020-01-31"}).status_code == 413


@pytest.mark.parametrize("params,status", [
    ({"entity_id": "sensor.gibt_es_nicht"}, 404),
    ({"entity_id": "../etc/passwd"}, 400),
    ({"entity_id": "sensor.a", "start": "kaputt"}, 400),
    ({"entity_id": "sensor.a", "resolution": "sekunde"}, 400),
    ({"entity_id": "sensor.a", "start": "2020-02-01", "end": "2020-01-01"}, 400),
])
def test_bad_requests_are_rejected(client, params, status) -> None:
    assert client.get("/export/download", params=params).status_code == status


def test_estimate_without_entities_is_a_client_error(client) -> None:
    assert client.post("/export/estimate", json={"entity_ids": []}).status_code == 400
