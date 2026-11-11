"""Wetterdashboard: Sonnenschein und Himmelsklasse nach Sonnenstand (Anteil an der Klarhimmel-Helligkeit)."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import wetter_conditions as cond
from app import wetter_routes
from app.storage import hotbuffer, rollup as rollup_mod
from app.storage.index import Index
from app.wetter_routes import WetterDependencies, WetterService

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=TZ)


def _ts(hour: int, minute: int = 0, day: int = 20) -> float:
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ).timestamp()


@pytest.fixture()
def service():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-sun-test-"))
    index = Index(tmp / "index.sqlite")
    yield WetterService(WetterDependencies(
        data_dir=tmp, index=index, tz=TZ, templates=None, app_root_context=lambda request: {},
    ))
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _add(service, entity_id, state_class, unit, value):
    deps = service.deps
    deps.index.get_or_create_entity(entity_id, "sensor", state_class, unit)
    for minute in range(52, 60):
        ts = _ts(11, minute)
        hotbuffer.append(deps.data_dir, entity_id, ts, value, TZ)
        deps.index.record_write(entity_id, ts, value)


def _configure(service, **extra):
    config = {**wetter_routes._empty_config(), "illuminance": "sensor.lux", "sun_elevation": "sensor.elev", **extra}
    wetter_routes._save_config(service.deps.index, config)
    return config


def test_clear_sky_follows_the_sun_elevation() -> None:
    assert cond.clear_sky_lx(2.9) is None
    assert cond.clear_sky_lx(3.0) > 0
    # Novembermittag (rund 19 Grad) gegen Junimittag (rund 60 Grad): Der klare Himmel ist im Sommer fast dreimal so hell.
    assert cond.clear_sky_lx(19.24) == pytest.approx(38652.0, rel=0.01)
    assert cond.clear_sky_lx(60.0) == pytest.approx(113071.0, rel=0.01)
    assert cond.clear_sky_lx(19.24, 0.6) == pytest.approx(cond.clear_sky_lx(19.24) * 0.6)
    assert cond.share_of_clear_pct(19300.0, 19.24) == pytest.approx(50.0, abs=0.2)
    assert cond.share_of_clear_pct(5000.0, 1.0) is None


def test_relative_cuts_are_ascending_and_below_full_sky() -> None:
    assert list(cond.CLASS_CUTS_PCT) == sorted(cond.CLASS_CUTS_PCT) and cond.CLASS_CUTS_PCT[-1] < 100.0


def test_sky_class_is_the_same_share_in_winter_and_summer(service) -> None:
    _configure(service, sun_relative=True)
    _add(service, "sensor.lux", "measurement", "lx", 30000.0)
    _add(service, "sensor.elev", "measurement", "°", 19.24)  # 30.000 lx sind dort gut 77 % des klaren Himmels
    sky = service.compute_conditions(NOW)["sky"]
    assert sky["class"] == cond.SUNNY and sky["unit"] == "%" and sky["value"] == pytest.approx(77.6, abs=0.5)


def test_summer_haze_is_not_sunny_by_share(service) -> None:
    _configure(service, sun_relative=True)
    _add(service, "sensor.lux", "measurement", "lx", 30000.0)
    _add(service, "sensor.elev", "measurement", "°", 60.0)  # 30.000 lx sind bei 60 Grad nur gut 26 %
    assert service.compute_conditions(NOW)["sky"]["class"] == cond.OVERCAST


def test_switch_off_keeps_the_fixed_lux_cuts(service) -> None:
    _configure(service, sun_relative=False)
    _add(service, "sensor.lux", "measurement", "lx", 30000.0)
    _add(service, "sensor.elev", "measurement", "°", 60.0)
    sky = service.compute_conditions(NOW)["sky"]
    assert sky["class"] == cond.FAIR and sky["unit"] == "lx"  # 25.000 bis 70.000 lx wie bisher


def test_without_the_elevation_role_the_fixed_cuts_apply(service) -> None:
    config = _configure(service, sun_relative=True, sun_elevation="")
    _add(service, "sensor.lux", "measurement", "lx", 30000.0)
    assert not service._sun_relative(config)
    assert service.compute_conditions(NOW)["sky"]["unit"] == "lx"


def _patch_rows(service, monkeypatch, elevation_by_hour: dict[int, float]):
    rows = [rollup_mod.FineRow(bucket_start=_ts(hour), value=value) for hour, value in elevation_by_hour.items()]
    monkeypatch.setattr(service, "_rows", lambda *args, **kwargs: rows)


def _lux(values: dict[int, float]) -> list[rollup_mod.FineRow]:
    return [rollup_mod.FineRow(bucket_start=_ts(hour), value=value) for hour, value in values.items()]


def test_sunshine_counts_by_share_not_by_fixed_lux(service, monkeypatch) -> None:
    config = _configure(service, sun_relative=True)
    _add(service, "sensor.elev", "measurement", "°", 19.0)
    # Winter: 11 bis 13 Uhr bei 17 bis 19 Grad Sonnenhöhe, 22.000 bis 26.000 lx sind dort 65 bis 68 % des klaren Himmels.
    _patch_rows(service, monkeypatch, {11: 17.0, 12: 19.0, 13: 17.0})
    winter = _lux({11: 22000.0, 12: 26000.0, 13: 22000.0})
    assert service.summarize("sunshine", winter, config)["hours"] == pytest.approx(3.0)
    assert service.summarize("sunshine", winter, {**config, "sun_relative": False})["hours"] == pytest.approx(1.0)  # bisher nur 12 Uhr
    # Sommer: 40.000 lx bei 60 Grad sind gut 35 %, kein Sonnenschein, obwohl über 25.000 lx.
    _patch_rows(service, monkeypatch, {11: 60.0, 12: 60.0})
    assert service.summarize("sunshine", _lux({11: 40000.0, 12: 40000.0}), config)["hours"] == pytest.approx(0.0)


def test_hours_with_the_sun_too_low_or_unknown_do_not_count(service, monkeypatch) -> None:
    config = _configure(service, sun_relative=True)
    _add(service, "sensor.elev", "measurement", "°", 19.0)
    _patch_rows(service, monkeypatch, {11: 2.0})  # nur 11 Uhr hat eine Sonnenhöhe, und die liegt unter 3 Grad
    summary = service.summarize("sunshine", _lux({11: 5000.0, 12: 50000.0}), config)
    assert summary["hours"] == pytest.approx(0.0)  # 12 Uhr ohne Sonnenhöhe: nichts geraten
    _patch_rows(service, monkeypatch, {})
    assert service.summarize("sunshine", _lux({12: 50000.0}), config) is None


def test_calibration_scales_the_clear_sky(service, monkeypatch) -> None:
    config = _configure(service, sun_relative=True, sun_scale_pct=60.0)
    _add(service, "sensor.elev", "measurement", "°", 19.24)
    _patch_rows(service, monkeypatch, {12: 19.24})
    # Der Sensor erreicht im November nur rund 60 % der Idealkurve (23.900 lx); mit Abgleich 60 % ist das ein klarer Himmel.
    assert service.summarize("sunshine", _lux({12: 23000.0}), config)["hours"] == pytest.approx(1.0)
    assert service.summarize("sunshine", _lux({12: 23000.0}), {**config, "sun_scale_pct": 100.0})["hours"] == pytest.approx(0.0)  # ohne Abgleich knapp unter 60 %
    assert service.summarize("sunshine", _lux({12: 15000.0}), {**config, "sun_scale_pct": 100.0})["hours"] == pytest.approx(0.0)
    assert service.summarize("sunshine", _lux({12: 15000.0}), config)["hours"] == pytest.approx(1.0)


def test_config_roundtrip_and_bounds(service) -> None:
    index = service.deps.index
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "sun_relative": True, "sun_share_pct": 55.0, "sun_scale_pct": 80.0})
    config = wetter_routes._load_config(index)
    assert config["sun_relative"] is True and config["sun_share_pct"] == 55.0 and config["sun_scale_pct"] == 80.0
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "sun_share_pct": 5.0, "sun_scale_pct": 900.0})
    config = wetter_routes._load_config(index)
    assert config["sun_relative"] is False and config["sun_share_pct"] == 60.0 and config["sun_scale_pct"] == 100.0
