"""„Werte ignorieren“: pro Entität abschaltbare Liste von Werten, die beim
Erfassen verworfen werden (z. B. die 0, die manche Sensoren beim HA-Start
melden, bevor die Integration einen echten Wert kennt)."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.storage.index import (
    Index,
    TooManyIgnoreValues,
    normalize_ignore_values,
    parse_ignore_values,
    should_ignore_value,
)
from app.storage.ingestion import IngestEvent, IngestionService

TZ = ZoneInfo("UTC")


def test_parse_accepts_decimal_comma_and_dedupes() -> None:
    assert parse_ignore_values("0; -1 ;0,5; 0") == [0.0, -1.0, 0.5]
    assert parse_ignore_values("") == []
    assert parse_ignore_values(" ; ;") == []


@pytest.mark.parametrize("text", ["abc", "0; x", "nan", "inf", "1;-inf"])
def test_parse_rejects_non_numbers(text: str) -> None:
    with pytest.raises(ValueError):
        parse_ignore_values(text)


def test_parse_limits_the_count() -> None:
    assert len(parse_ignore_values(";".join(str(i) for i in range(10)))) == 10
    with pytest.raises(TooManyIgnoreValues):
        parse_ignore_values(";".join(str(i) for i in range(11)))


def test_normalize_is_stable_and_has_no_negative_zero() -> None:
    assert normalize_ignore_values([0.0, -1.0, 0.5]) == "0;-1;0.5"
    assert normalize_ignore_values([-0.0]) == "0"
    assert parse_ignore_values(normalize_ignore_values([0.0, -1.0, 0.5])) == [0.0, -1.0, 0.5]


def test_should_ignore_needs_switch_and_exact_match() -> None:
    assert should_ignore_value(1, "0;-1", 0.0)
    assert should_ignore_value(1, "0;-1", -1.0)
    assert not should_ignore_value(1, "0;-1", 0.001)
    assert not should_ignore_value(0, "0;-1", 0.0)
    assert not should_ignore_value(1, "", 0.0)
    # Ein kaputter Eintrag in der Datenbank bricht den Schreibpfad nicht.
    assert should_ignore_value(1, "x;0", 0.0)


def _event(event_id: str, ts: float, value: float, entity_id: str = "sensor.volt") -> IngestEvent:
    return IngestEvent(
        event_id=event_id, entity_id=entity_id, domain="sensor", ts=ts, value=value,
        state_class="measurement", unit="V",
    )


def test_ingest_discards_listed_values_only_when_switched_on(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, TZ)
        assert service.ingest(_event("e1", 1722470400.0, 3.7)) == "written"
        # Standard: aus, die 0 wird wie jeder andere Wert gespeichert.
        assert service.ingest(_event("e2", 1722470460.0, 0.0)) == "written"
        index.set_config("sensor.volt", ignore_values_enabled=True, ignore_values="0")
        assert service.ingest(_event("e3", 1722470520.0, 0.0)) == "filtered"
        entity = index.get_entity("sensor.volt")
        # Verworfen: weder last_value noch last_ts noch row_count ändern sich.
        assert entity["last_value"] == 0.0 and entity["last_ts"] == 1722470460.0
        assert entity["row_count"] == 2
        assert service.ingest(_event("e4", 1722470580.0, 3.8)) == "written"
        # Ausschalten behält die Liste, wirkt aber nicht mehr.
        index.set_config("sensor.volt", ignore_values_enabled=False)
        assert index.get_entity("sensor.volt")["ignore_values"] == "0"
        assert service.ingest(_event("e5", 1722470640.0, 0.0)) == "written"
    finally:
        index.close()


def test_ignored_event_is_idempotent_on_retry(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        service = IngestionService(tmp_path, index, TZ)
        service.ingest(_event("e1", 1722470400.0, 3.7))
        index.set_config("sensor.volt", ignore_values_enabled=True, ignore_values="0")
        assert service.ingest(_event("e2", 1722470460.0, 0.0)) == "filtered"
        assert service.ingest(_event("e2", 1722470460.0, 0.0)) == "duplicate"
    finally:
        index.close()


def test_new_columns_default_to_off(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.neu", "sensor", "measurement", "V")
        entity = index.get_entity("sensor.neu")
        assert entity["ignore_values_enabled"] == 0 and entity["ignore_values"] == ""
    finally:
        index.close()


def _config(client, entity_id: str, **data):
    return client.post(f"/entities/{entity_id}/config", data=data)


def test_route_validation_and_defaults(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.pytest_ign", "sensor", "measurement", "V")
    try:
        # Einschalten ohne Liste: "0" wird vorbelegt.
        assert _config(client, "sensor.pytest_ign", ignore_values_mode="on").status_code == 200
        row = index.get_entity("sensor.pytest_ign")
        assert row["ignore_values_enabled"] == 1 and row["ignore_values"] == "0"
        # Liste speichern, Dezimalkomma wird normalisiert.
        assert _config(client, "sensor.pytest_ign", ignore_values="0; -1; 0,5").status_code == 200
        assert index.get_entity("sensor.pytest_ign")["ignore_values"] == "0;-1;0.5"
        assert "0; -1; 0.5" in client.get("/entities/sensor.pytest_ign/config").text
        # Ungültig / zu viele / leer bei eingeschaltetem Schalter → 400, nichts geändert.
        assert _config(client, "sensor.pytest_ign", ignore_values="0; abc").status_code == 400
        assert _config(client, "sensor.pytest_ign", ignore_values=";".join(map(str, range(11)))).status_code == 400
        assert _config(client, "sensor.pytest_ign", ignore_values="").status_code == 400
        assert _config(client, "sensor.pytest_ign", ignore_values_mode="x").status_code == 400
        assert index.get_entity("sensor.pytest_ign")["ignore_values"] == "0;-1;0.5"
        # Ausschalten behält die Liste; bei ausgeschaltetem Schalter darf sie leer werden.
        assert _config(client, "sensor.pytest_ign", ignore_values_mode="off").status_code == 200
        row = index.get_entity("sensor.pytest_ign")
        assert row["ignore_values_enabled"] == 0 and row["ignore_values"] == "0;-1;0.5"
        assert _config(client, "sensor.pytest_ign", ignore_values="").status_code == 200
        assert index.get_entity("sensor.pytest_ign")["ignore_values"] == ""
    finally:
        index.set_config("sensor.pytest_ign", ignore_values_enabled=False, ignore_values="")


def test_switch_entities_are_blocked(client) -> None:
    from app.main import index

    index.get_or_create_entity("binary_sensor.pytest_ign", "binary_sensor", None, None)
    assert index.get_entity("binary_sensor.pytest_ign")["aggregation_type"] == "switch"
    response = _config(client, "binary_sensor.pytest_ign", ignore_values_mode="on", ignore_values="0")
    assert response.status_code == 200
    row = index.get_entity("binary_sensor.pytest_ign")
    assert row["ignore_values_enabled"] == 0 and row["ignore_values"] == ""
    page = client.get("/entities/binary_sensor.pytest_ign/config").text
    assert 'name="ignore_values_mode"' not in page
