"""Code-Review Phase 10: Härtung und Kleinkram (C1, C4, C12–C15, C17)."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from _paths import ADDON
from app import ai_usage, background
from app.storage import ha_statistics
from app.storage.paths import InvalidEntityIdError, validate_entity_id


# ---------------------------------------------------------------- C1: ungültige Eingaben → 400


def test_an_invalid_entity_id_is_its_own_value_error() -> None:
    with pytest.raises(InvalidEntityIdError):
        validate_entity_id("../etc/passwd")
    assert issubclass(InvalidEntityIdError, ValueError)  # bestehende "except ValueError" bleiben gültig


@pytest.mark.parametrize("path", ["/entities/not%20valid/rows", "/entities/NOPE/rows"])
def test_a_malformed_entity_id_in_the_path_is_answered_with_400_or_404_never_500(client, path: str) -> None:
    assert client.get(path).status_code in (400, 404)


def test_non_numeric_paging_values_in_the_rows_form_are_a_400(client) -> None:
    response = client.post("/entities/sensor.nicht_da/rows/delete", data={"page": "abc"})
    assert response.status_code in (400, 404)  # 404, wenn die Entität zuerst geprüft wird
    from app.main import _rows_form_common

    with pytest.raises(Exception) as caught:
        _rows_form_common({"offset": "x"})
    assert getattr(caught.value, "status_code", None) == 400


# ---------------------------------------------------------------- C4: Basis-Image mit Digest


def test_the_base_image_is_pinned_by_digest() -> None:
    first = (ADDON / "Dockerfile").read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(r"FROM python:3\.12-slim@sha256:[0-9a-f]{64}", first)


# ---------------------------------------------------------------- C12: Tages-Bucket an Zeitumstellungstagen


def _fetch_day_buckets(monkeypatch, entries: list[dict], period: str = "day"):
    monkeypatch.setattr(ha_statistics, "_ws_call", lambda commands: [{
        "id": 1, "type": "result", "success": True, "result": {"sensor.e": entries},
    }])
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return ha_statistics.fetch_statistics_rows("sensor.e", start, start + timedelta(days=60), period)


def test_a_counter_on_a_23_hour_day_stays_inside_that_day(monkeypatch) -> None:
    berlin = ZoneInfo("Europe/Berlin")
    day = datetime(2026, 3, 29, tzinfo=berlin)       # Umstellung auf Sommerzeit: 23 Stunden
    nxt = datetime(2026, 3, 30, tzinfo=berlin)
    ms = lambda moment: int(moment.timestamp() * 1000)  # noqa: E731
    result = _fetch_day_buckets(monkeypatch, [{"start": ms(day), "end": ms(nxt), "state": 5.0}])
    (ts, value), = result.rows
    assert value == 5.0
    assert datetime.fromtimestamp(ts, berlin).date().isoformat() == "2026-03-29"
    assert ts == nxt.timestamp() - 1


def test_without_an_end_field_the_old_period_length_is_the_fallback(monkeypatch) -> None:
    start = datetime(2026, 1, 10, tzinfo=timezone.utc)
    result = _fetch_day_buckets(monkeypatch, [{"start": int(start.timestamp() * 1000), "state": 1.0}])
    assert result.rows[0][0] == start.timestamp() + 86_399


def test_an_implausible_end_field_is_ignored(monkeypatch) -> None:
    start = datetime(2026, 1, 10, tzinfo=timezone.utc)
    bogus = int((start + timedelta(days=30)).timestamp() * 1000)
    result = _fetch_day_buckets(monkeypatch, [{"start": int(start.timestamp() * 1000), "end": bogus, "state": 1.0}])
    assert result.rows[0][0] == start.timestamp() + 86_399


# ---------------------------------------------------------------- C13: halboffene Fenster


def test_a_point_on_the_window_end_belongs_to_the_next_window(tmp_path) -> None:
    from app.storage import hotbuffer, query
    from app.storage.index import Index

    tz = ZoneInfo("Europe/Berlin")
    start = datetime(2024, 7, 1, tzinfo=tz).timestamp()
    end = datetime(2024, 7, 2, tzinfo=tz).timestamp()
    index = Index(tmp_path / "index.sqlite")
    try:
        hot = hotbuffer.hot_path(tmp_path, "sensor.x", start, tz)
        hotbuffer.write_records(hot, [(start, 1.0, "a", None, None), (end - 1, 2.0, "b", None, None), (end, 3.0, "c", None, None)])
        rows = query._read_hot_rows_filtered(tmp_path, "sensor.x", index, start, start, end, tz)
        assert [value for _ts, value in rows] == [1.0, 2.0]  # der Punkt genau auf dem Ende gehört zum nächsten Fenster
    finally:
        index.close()


# ---------------------------------------------------------------- C14: "sauber beendet" nur ohne laufende Threads


class _Settings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_setting(self, key: str, value: str) -> None:
        self.values[key] = value


def _service(alive_thread: threading.Thread | None) -> SimpleNamespace:
    return SimpleNamespace(
        _maintenance_scheduler_stop=threading.Event(), _maintenance_scheduler_thread=None,
        _storage_reconcile_stop=threading.Event(), _storage_reconcile_thread=alive_thread,
        requires_synchronous_reconciliation=True, _storage_reconcile_completed=True, index=_Settings(),
    )


def test_a_clean_shutdown_is_recorded_when_all_threads_have_stopped() -> None:
    service = _service(None)
    background.BackgroundService.stop(service)
    assert service.index.values == {"storage_clean_shutdown": "1"}


def test_a_thread_still_running_after_the_join_prevents_the_clean_flag(monkeypatch) -> None:
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    monkeypatch.setattr(thread, "join", lambda timeout=None: None)  # die Zeitgrenze des join() ist abgelaufen
    service = _service(thread)
    try:
        background.BackgroundService.stop(service)
        assert service.index.values == {}
    finally:
        release.set()
        thread.join()


# ---------------------------------------------------------------- C15: snapshot() liest eine tiefe Kopie


def test_snapshot_does_not_see_counters_changed_after_the_copy(monkeypatch) -> None:
    usage = ai_usage.AiUsage()
    index = SimpleNamespace(get_setting=lambda *a, **k: "", set_setting=lambda *a, **k: None)
    monkeypatch.setattr(ai_usage, "_load_days", lambda _index: {})
    usage.record(index, "get_summary", "admin")
    seen_inner: list[dict] = []
    original = ai_usage.copy.deepcopy

    def spy(value):
        copy = original(value)
        seen_inner.append(copy)
        return copy

    monkeypatch.setattr(ai_usage.copy, "deepcopy", spy)
    usage.snapshot(index)
    assert seen_inner, "snapshot() muss die Tageswerte tief kopieren"
    day = next(iter(usage._days.values()))
    assert next(iter(seen_inner[0].values()))["calls"] is not day["calls"]


# ---------------------------------------------------------------- C17: Friendly-Name statt roher ID


def test_the_longterm_consumer_name_prefers_the_friendly_name_over_the_entity_id() -> None:
    from app.energie_langzeit import EnergieLangzeitService

    entities = {"sensor.wp": {"friendly_name": "Wärmepumpe", "custom_name": None}, "sensor.x": {"friendly_name": None, "custom_name": None}}
    fake = SimpleNamespace(index=SimpleNamespace(get_entity=lambda entity_id: entities.get(entity_id)))
    name = lambda consumer: EnergieLangzeitService._consumer_name(fake, consumer)  # noqa: E731
    assert name({"entity_id": "sensor.wp"}) == "Wärmepumpe"
    assert name({"entity_id": "sensor.wp", "name": " Heizung "}) == "Heizung"
    assert name({"entity_id": "sensor.x"}) == "sensor.x"
    assert name({"entity_id": "sensor.unbekannt"}) == "sensor.unbekannt"


# ---------------------------------------------------------------- Phase 11: Reste aus Phase 3 und 9


def test_the_selection_routes_do_not_log_a_client_supplied_request_id() -> None:
    """Wie die Karten-Routen: die Anfrage-ID kommt aus der Middleware, nicht aus dem Kopf des Clients."""
    for name in ("selection_routes.py", "card_routes.py"):
        source = (ADDON / "app" / name).read_text(encoding="utf-8")
        assert "x-request-id" not in source.lower(), name
    assert (ADDON / "app" / "selection_routes.py").read_text(encoding="utf-8").count('getattr(request.state, "request_id", "-")') == 3


def test_a_slow_answer_cannot_overwrite_the_entity_tiles_of_a_newer_request() -> None:
    script = (ADDON / "app" / "static" / "js" / "dashboard-tiles.js").read_text(encoding="utf-8")
    body = script.split("async function loadEntityGroup(tiles) {", 1)[1].split("// ---- Darstellung", 1)[0]
    assert "const seqs = new Map(tiles.map(el => [el, nextRenderSeq(el)]));" in body
    assert "el.dataset.renderSeq === seqs.get(el)" in body
    assert body.index("nextRenderSeq(el)") < body.index("await fetch(")


def test_the_energy_tile_link_does_not_read_data_before_it_is_loaded() -> None:
    """x-show blendet den Absatz nur aus, :href wird trotzdem ausgewertet: mit tile.data = null warf Alpine einen TypeError."""
    template = (ADDON / "app" / "templates" / "_energiedashboard_view.html").read_text(encoding="utf-8")
    assert ":href=\"tile.data ? entityHref(tile.data.entity_id) : '#'\"" in template
    assert ':href="entityHref(tile.data.entity_id)"' not in template
