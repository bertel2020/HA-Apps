"""Import → Home Assistant: Die Vorschau weist auf eine abweichende Einheit der
Langzeitstatistik hin (ROADMAP 1.3). Es wird nichts umgerechnet."""

from __future__ import annotations

import time

import pytest

from _paths import APP  # noqa: F401  (legt den App-Pfad auf sys.path)

ENTITY = "sensor.unit_hint_energy"


@pytest.fixture()
def entity(client):
    from app import main

    main.index.get_or_create_entity(ENTITY, "sensor", "total_increasing", "kWh")
    main._import_service._ha_dry_run_status.clear()
    main._import_service._ha_availability_cache.entries.clear()
    yield ENTITY
    main._import_service._ha_dry_run_status.clear()
    main._import_service._ha_availability_cache.entries.clear()
    progress = main._import_service._ha_progress
    progress.started = False
    progress.kind = ""
    progress.result = None
    if main.index.get_entity(ENTITY) is not None:
        main.index.delete_entity(ENTITY)


def _run_dry_run(client, form) -> str:
    client.post("/import/ha/dry-run", data=form)
    for _ in range(100):
        polled = client.get("/import/ha/progress?watch=dry-run")
        if 'id="ha-section"' in polled.text:
            return polled.text
        time.sleep(0.05)
    raise AssertionError("Vorschau wurde nicht fertig")


def _patch_statistics(monkeypatch, unit: str | None) -> None:
    from app.storage import ha_import, ha_statistics

    now = time.time()
    monkeypatch.setattr(
        ha_statistics, "fetch_statistic_meta",
        lambda ids: {i: ha_statistics.StatisticMeta(i, has_sum=True, unit=unit) for i in ids},
    )
    monkeypatch.setattr(
        ha_statistics, "fetch_statistics_rows",
        lambda *a, **k: ha_import.HistoryFetchResult(rows=[(now - 7200, 1000.0), (now - 3600, 2000.0)]),
    )


FORM = {"entity_ids": [ENTITY], "history_source": "stats", "period": "hour", "range_preset": "max"}


def test_statistics_unit_is_reported_in_the_dry_run(client, entity, monkeypatch) -> None:
    _patch_statistics(monkeypatch, "Wh")
    html = _run_dry_run(client, FORM)
    assert "Die Quelle nennt die Einheit" in html and "Wh" in html and "0,001" in html.replace(".", ",")


def test_same_unit_gives_no_hint(client, entity, monkeypatch) -> None:
    _patch_statistics(monkeypatch, "kWh")
    assert "Die Quelle nennt die Einheit" not in _run_dry_run(client, FORM)


def test_missing_statistics_unit_gives_no_hint(client, entity, monkeypatch) -> None:
    _patch_statistics(monkeypatch, None)
    assert "Die Quelle nennt die Einheit" not in _run_dry_run(client, FORM)


VM_ENTITY = "sensor.unit_hint_vm"


@pytest.fixture()
def vm_entity(client):
    from app import main

    main.index.get_or_create_entity(VM_ENTITY, "sensor", "total_increasing", "kWh")
    yield VM_ENTITY
    if main.index.get_entity(VM_ENTITY) is not None:
        main.index.delete_entity(VM_ENTITY)


def _vm_dry_run(client, monkeypatch, units: list[str]) -> str:
    from app import import_routes
    from app.storage import ha_import

    now = time.time()

    def fake_fetch(self, entity_ids, start, end, base_url, on_entity=None):
        history = ha_import.HistoryFetchResult(rows=[(now - 7200, 1000.0), (now - 3600, 2000.0)], source_units=units)
        return {VM_ENTITY: history}, []

    monkeypatch.setattr(import_routes.ImportService, "_fetch_vm_history", fake_fetch)
    return client.post("/import/vm/dry-run", data={"entity_ids": [VM_ENTITY], "range_preset": "max"}).text


def test_victoriametrics_unit_tag_is_reported_in_the_dry_run(client, vm_entity, monkeypatch) -> None:
    html = _vm_dry_run(client, monkeypatch, ["Wh", "kWh"])
    assert html.count("Die Quelle nennt die Einheit") == 1  # kWh = gespeicherte Einheit, kein Hinweis
    assert "Wh" in html


def test_victoriametrics_without_unit_tag_gives_no_hint(client, vm_entity, monkeypatch) -> None:
    assert "Die Quelle nennt die Einheit" not in _vm_dry_run(client, monkeypatch, [])
