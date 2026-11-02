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


def _post(client, **data):
    base = {"entity_id": ["sensor.a", "sensor.b"], "start": "2020-01-01", "end": "2020-01-31"}
    return client.post("/export/download", data={**base, **data})


def test_default_form_fields_give_the_file_from_before(client) -> None:
    with_fields = client.post("/export/download", data={
        "entity_id": ["sensor.a"], "layout": "per_entity", "sep": "comma", "decimal": "point", "bom": "0",
        "time": "stamp", "unix": "1",
    })
    assert with_fields.content == client.get("/export/download", params={"entity_id": "sensor.a"}).content


def test_date_and_time_in_separate_columns_and_unix_time_optional(client) -> None:
    lines = _lines(client.post("/export/download", data={
        "entity_id": ["sensor.a"], "start": "2020-01-01", "end": "2020-01-31", "time": "split", "unix": "0",
    }))
    assert lines[0] == "date,time,value"
    assert lines[1] == "2020-01-01,10:00:00,10.0"
    split_with_unix = _lines(client.post("/export/download", data={"entity_id": ["sensor.a"], "time": "split"}))
    assert split_with_unix[0] == "date,time,unix_ts,value"
    only_stamp = _lines(client.post("/export/download", data={"entity_id": ["sensor.a"], "unix": "0"}))
    assert only_stamp[0] == "timestamp,value"


def test_german_format_uses_semicolon_decimal_comma_and_a_byte_order_mark(client) -> None:
    response = client.post("/export/download", data={
        "entity_id": ["sensor.a"], "start": "2020-01-01", "end": "2020-01-31", "resolution": "hour",
        "sep": "semicolon", "decimal": "comma", "bom": "1", "unix": "0",
    })
    assert response.content.startswith(b"\xef\xbb\xbf")
    lines = response.content.decode("utf-8-sig").strip().split("\r\n")
    assert lines == ["timestamp;value", "2020-01-01 10:00:00;15", "2020-01-01 11:00:00;40"]
    raw = client.post("/export/download", data={
        "entity_id": ["sensor.a"], "start": "2020-01-01", "end": "2020-01-31", "sep": "semicolon", "decimal": "comma",
    })
    assert raw.content.decode().split("\r\n")[1].endswith(";10,0")
    assert "1,0" not in raw.content.decode().split("\r\n")[0]


def test_comma_as_separator_and_as_decimal_at_once_is_rejected(client) -> None:
    assert _post(client, sep="comma", decimal="comma").status_code == 400
    assert _post(client, sep="tab", decimal="comma").status_code == 200
    assert _post(client, layout="diagonal").status_code == 400
    assert _post(client, time="irgendwann").status_code == 400


def test_each_zip_member_carries_the_byte_order_mark(client) -> None:
    response = _post(client, bom="1")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert all(archive.read(name).startswith(b"\xef\xbb\xbf") for name in archive.namelist())


def test_joint_file_side_by_side_has_one_column_per_entity_and_empty_cells(client) -> None:
    response = _post(client, layout="wide", resolution="hour", unix="0")
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="zeitarchiv-export_2-entitaeten_2020-01_hour.csv"' in response.headers["content-disposition"]
    assert _lines(response) == [
        "timestamp,sensor.a [x],sensor.b [x]",
        "2020-01-01 10:00:00,15,101.0",
        "2020-01-01 11:00:00,40,",
    ]


def test_joint_file_one_below_the_other_has_entity_time_value_and_unit(client) -> None:
    lines = _lines(_post(client, layout="long", resolution="hour", time="split", unix="0"))
    assert lines == [
        "entity,date,time,value,unit",
        "sensor.a,2020-01-01,10:00:00,15,x",
        "sensor.a,2020-01-01,11:00:00,40,x",
        "sensor.b,2020-01-01,10:00:00,101.0,x",
    ]
    raw = _lines(_post(client, layout="long"))
    assert raw[0] == "entity,timestamp,unix_ts,value,unit"
    assert len(raw) == 1 + 3 + 2


def test_joint_file_with_german_format_masks_by_the_chosen_separator(client) -> None:
    lines = _post(client, layout="wide", resolution="hour", sep="semicolon", decimal="comma", unix="0", bom="1").content.decode("utf-8-sig").strip().split("\r\n")
    assert lines[0] == "timestamp;sensor.a [x];sensor.b [x]"
    assert lines[1] == "2020-01-01 10:00:00;15;101,0"


def test_side_by_side_needs_a_fixed_resolution(client) -> None:
    assert _post(client, layout="wide", resolution="raw").status_code == 400
    assert _post(client, layout="wide", resolution="day").status_code == 200


def test_side_by_side_is_limited_in_the_number_of_entities(client, monkeypatch) -> None:
    monkeypatch.setattr(export_data, "MAX_WIDE_ENTITIES", 1)
    assert _post(client, layout="wide", resolution="hour").status_code == 400
    assert _post(client, layout="long", resolution="hour").status_code == 200


def test_joint_file_limit_counts_the_rows_of_the_file(client, monkeypatch) -> None:
    # Vorab zählt die Summe der Entitäten (hier 3 + 2 Rohwerte), das ist die obere Grenze der Zeilen.
    monkeypatch.setattr(export_routes, "MAX_EXPORT_ROWS", 4)
    assert _post(client, layout="long", resolution="raw").status_code == 413
    assert _post(client, layout="wide", resolution="hour").status_code == 413
    monkeypatch.setattr(export_routes, "MAX_EXPORT_ROWS", 5)
    assert _post(client, layout="long", resolution="raw").status_code == 200
    assert _post(client, layout="wide", resolution="hour").status_code == 200


def test_side_by_side_estimate_is_capped_by_the_time_grid_not_the_sum(setup, monkeypatch) -> None:
    """Zwei Entitäten mit je 120 Minutenwerten: untereinander 240 Zeilen, nebeneinander höchstens die
    121 Zeitpunkte des gemeinsamen Fensters."""
    rows = [(_ts(2020, 3, 1, 10, minute), float(minute)) for minute in range(60)] + [
        (_ts(2020, 3, 1, 11, minute), float(minute)) for minute in range(60)
    ]
    setup.entity("sensor.c", "measurement", rows)
    setup.entity("sensor.d", "measurement", rows)
    entities = [setup.index.get_entity("sensor.c"), setup.index.get_entity("sensor.d")]
    # Ein angeschnittener Monat wird anteilig geschätzt; für den Test steht die Zahl je Entität fest.
    monkeypatch.setattr(export_data, "estimate_rows", lambda *args, **kwargs: {"minute": 120})
    long_rows = export_data.estimate_joint_rows(setup.root, entities, None, None, "minute", "long", TZ)
    wide_rows = export_data.estimate_joint_rows(setup.root, entities, None, None, "minute", "wide", TZ)
    assert long_rows == 240
    assert wide_rows < long_rows and wide_rows <= 122


def test_state_entities_cannot_go_into_a_joint_file(client, setup) -> None:
    setup.index.get_or_create_entity("sensor.zustand", "sensor", None, None, state=True)
    response = client.post("/export/download", data={"entity_id": ["sensor.a", "sensor.zustand"], "layout": "long"})
    assert response.status_code == 400


def test_a_single_entity_ignores_the_joint_layout(client) -> None:
    response = client.post("/export/download", data={"entity_id": ["sensor.a"], "layout": "wide", "resolution": "raw"})
    assert response.status_code == 200
    assert _lines(response)[0] == "timestamp,unix_ts,value"


def test_estimate_reports_the_side_by_side_limit(client) -> None:
    body = client.post("/export/estimate", json={"entity_ids": ["sensor.a"]}).json()
    assert body["wide_limit"] == export_data.MAX_WIDE_ENTITIES


def test_preview_shows_the_first_lines_of_the_file_the_download_would_give(client) -> None:
    response = client.post("/export/preview", data={
        "entity_id": ["sensor.a", "sensor.b"], "start": "2020-01-01", "end": "2020-01-31", "resolution": "hour",
        "layout": "wide", "unix": "0",
    })
    body = response.json()
    assert body["file"] == "zeitarchiv-export_2-entitaeten_2020-01_hour.csv"
    assert body["text"].split("\n") == [
        "timestamp,sensor.a [x],sensor.b [x]", "2020-01-01 10:00:00,15,101.0", "2020-01-01 11:00:00,40,",
    ]


def test_preview_of_a_zip_shows_its_first_file_and_keeps_the_bom_out_of_the_text(client) -> None:
    body = client.post("/export/preview", data={
        "entity_id": ["sensor.a", "sensor.b"], "start": "2020-01-01", "end": "2020-01-31", "bom": "1", "unix": "0",
    }).json()
    assert body["file"] == "sensor.a_2020-01.csv"
    assert body["text"].split("\n")[0] == "timestamp,value"


def test_preview_rejects_what_the_download_rejects(client) -> None:
    assert client.post("/export/preview", data={"entity_id": ["sensor.a"], "sep": "comma", "decimal": "comma"}).status_code == 400
    assert client.post("/export/preview", data={"entity_id": ["sensor.a", "sensor.b"], "layout": "wide"}).status_code == 400
