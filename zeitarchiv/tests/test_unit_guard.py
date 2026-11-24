"""Einheiten-Schutz beim Aufnehmen (ROADMAP 1.3, Stufe 1)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("pyarrow")

from app.notices import state_rejection_notices  # noqa: E402
from app.storage import hotbuffer  # noqa: E402
from app.storage.index import Index  # noqa: E402
from app.storage.ingestion import IngestEvent, IngestionService  # noqa: E402
from app.storage.units import conversion_factor, is_unit_mismatch  # noqa: E402

BASE = 1722470400.0


def _event(n: int, value: float, unit: str | None, entity: str = "sensor.energy",
           state_class: str = "total_increasing", domain: str = "sensor") -> IngestEvent:
    return IngestEvent(
        event_id=f"e{n}", entity_id=entity, domain=domain, ts=BASE + n * 60,
        value=value, state_class=state_class, unit=unit,
    )


class _Env:
    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-unit-guard-"))
        self.index = Index(self.tmp / "index.sqlite")
        self.service = IngestionService(self.tmp, self.index, ZoneInfo("UTC"))
        return self

    def __exit__(self, *exc):
        self.index.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def values(self, entity: str = "sensor.energy") -> list[float]:
        path = hotbuffer.hot_path(self.tmp, entity, BASE, ZoneInfo("UTC"))
        return [record[1] for record in hotbuffer.read_records(path)]


@pytest.mark.parametrize(
    ("event_unit", "stored", "expected"),
    [
        ("Wh", "kWh", 0.001), ("kWh", "Wh", 1000.0), ("W", "kW", 0.001), ("MWh", "kWh", 1000.0),
        ("kWh", "kWh", None), ("°C", "°F", None), ("kWh", "kW", None), ("mW", "MW", None),
        (None, "kWh", None), ("", "kWh", None), ("Wh", None, None),
    ],
)
def test_conversion_factor(event_unit, stored, expected) -> None:
    result = conversion_factor(event_unit, stored)
    assert result == pytest.approx(expected) if expected is not None else result is None


def test_mismatch_only_for_known_different_units() -> None:
    assert is_unit_mismatch("°C", "°F")
    assert is_unit_mismatch("kWh", "kW")
    assert not is_unit_mismatch("Wh", "kWh")
    assert not is_unit_mismatch("kWh", "kWh")
    assert not is_unit_mismatch(None, "kWh")
    assert not is_unit_mismatch("kWh", None)


def test_wh_event_is_converted_and_stored_unit_stays() -> None:
    with _Env() as env:
        assert env.service.ingest(_event(0, 1234.5, "kWh")) == "written"
        assert env.service.ingest(_event(1, 1234600.0, "Wh")) == "written"
        assert env.service.ingest(_event(2, 1234.7, "kWh")) == "written"
        # Gleicher Wert nach der Umrechnung gilt als unverändert (Wertänderungsfilter).
        assert env.service.ingest(_event(3, 1234700.0, "Wh")) == "filtered"
        assert env.values() == [1234.5, 1234.6, 1234.7]
        assert env.index.get_entity("sensor.energy")["unit"] == "kWh"


def test_conversion_happens_before_the_counter_decrease_check(caplog) -> None:
    with _Env() as env:
        env.service.ingest(_event(0, 1000.0, "kWh"))
        env.service.ingest(_event(1, 1000500.0, "Wh"))
        assert "event=counter_decrease" not in caplog.text


def test_retry_gives_the_same_stored_value() -> None:
    with _Env() as env:
        env.service.ingest(_event(0, 10.0, "kWh"))
        assert env.service.ingest(_event(1, 12000.0, "Wh")) == "written"
        assert env.service.ingest(_event(1, 12000.0, "Wh")) == "duplicate"
        assert env.values() == [10.0, 12.0]


def test_ignore_list_sees_the_converted_value() -> None:
    with _Env() as env:
        env.service.ingest(_event(0, 5.0, "kW", entity="sensor.p", state_class="measurement"))
        env.index.set_config("sensor.p", ignore_values_enabled=True, ignore_values="7")
        assert env.service.ingest(
            _event(1, 7000.0, "W", entity="sensor.p", state_class="measurement")) == "filtered"
        assert env.service.ingest(
            _event(2, 8000.0, "W", entity="sensor.p", state_class="measurement")) == "written"
        assert env.values("sensor.p") == [5.0, 8.0]


def test_unknown_pair_keeps_todays_behaviour_and_is_reported() -> None:
    with _Env() as env:
        env.service.ingest(_event(0, 20.0, "°C", entity="sensor.t", state_class="measurement"))
        env.service.ingest(_event(1, 68.0, "°F", entity="sensor.t", state_class="measurement"))
        assert env.values("sensor.t") == [20.0, 68.0]
        assert env.index.get_entity("sensor.t")["unit"] == "°F"
        ids = {notice["id"] for notice in state_rejection_notices(env.index)}
        assert ids == {"entities.unit_mismatch"}


def test_conversion_is_reported_as_info_notice() -> None:
    with _Env() as env:
        env.service.ingest(_event(0, 1.0, "kWh"))
        env.service.ingest(_event(1, 1500.0, "Wh"))
        notices = state_rejection_notices(env.index)
        assert [(n["id"], n["severity"]) for n in notices] == [("entities.unit_converted", "info")]
        assert "sensor.energy" in notices[0]["detail"]


def test_missing_unit_and_empty_stored_unit() -> None:
    with _Env() as env:
        env.service.ingest(_event(0, 1.0, None))
        assert env.index.get_entity("sensor.energy")["unit"] in (None, "")
        env.service.ingest(_event(1, 2.0, "kWh"))
        assert env.index.get_entity("sensor.energy")["unit"] == "kWh"
        env.service.ingest(_event(2, 3.0, None))
        assert env.index.get_entity("sensor.energy")["unit"] == "kWh"
        assert env.values() == [1.0, 2.0, 3.0]
        assert state_rejection_notices(env.index) == []


def test_state_entities_are_left_alone() -> None:
    with _Env() as env:
        env.service.ingest(IngestEvent(
            event_id="s1", entity_id="sensor.mode", domain="sensor", ts=BASE, value=0.0,
            state_class=None, unit="kWh", state="heating",
        ))
        env.service.ingest(IngestEvent(
            event_id="s2", entity_id="sensor.mode", domain="sensor", ts=BASE + 60, value=0.0,
            state_class=None, unit="Wh", state="idle",
        ))
        assert state_rejection_notices(env.index) == []
