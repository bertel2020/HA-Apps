from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import import_routes
from app.storage import ha_import, ha_statistics


TZ = ZoneInfo("Europe/Berlin")


def _service() -> import_routes.ImportService:
    return import_routes.ImportService(import_routes.ImportDependencies(
        data_dir=Path("/tmp/unused-ha-full-import-test"),
        tz=TZ,
        index=None,
        coordinator=None,
        templates=None,
        app_root_context=None,
        reports_context=None,
        run_storage_reconciliation=None,
        symcon_import_dir=Path("/tmp"),
        csv_import_dir=Path("/tmp"),
        symcon_names_path=Path("/tmp/names.json"),
        symcon_source_meta_path=Path("/tmp/meta.json"),
        symcon_scan_cache_path=Path("/tmp/scan.json"),
    ))


def test_full_import_uses_one_half_open_hour_boundary() -> None:
    service = _service()
    hour = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
    raw = ha_import.HistoryFetchResult(rows=[
        ((hour + timedelta(minutes=37)).timestamp(), 10.37),
        ((hour + timedelta(minutes=50)).timestamp(), 10.50),
        ((hour + timedelta(hours=1, minutes=5)).timestamp(), 11.05),
    ])
    stats = ha_import.HistoryFetchResult(rows=[
        ((hour - timedelta(hours=1)).timestamp(), 9.0),
        (hour.timestamp(), 10.0),
    ])

    combined = service._combine_ha_full_history(
        raw, stats, hour - timedelta(days=10), hour - timedelta(days=365),
        hour + timedelta(days=1), stats_supported=True,
    )

    cutover = (hour + timedelta(hours=1)).timestamp()
    assert combined.source_details["cutover_ts"] == cutover
    assert combined.rows == [
        stats.rows[0], stats.rows[1], (cutover, raw.rows[1][1]), raw.rows[2]
    ]
    assert combined.source_details["raw"]["discarded_at_seam"] == 2
    assert combined.source_details["raw"]["boundary_anchor"] == {
        "timestamp": cutover, "value": raw.rows[1][1]
    }
    assert max(ts + 3600 for ts, _ in stats.rows) <= min(ts for ts, _ in combined.rows[2:])


def test_full_import_never_drops_raw_rows_without_covering_statistic_bucket() -> None:
    service = _service()
    hour = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
    raw = ha_import.HistoryFetchResult(rows=[
        ((hour + timedelta(minutes=37)).timestamp(), 10.37),
        ((hour + timedelta(minutes=50)).timestamp(), 10.50),
    ])
    stats = ha_import.HistoryFetchResult(rows=[
        ((hour - timedelta(hours=1)).timestamp(), 9.0),
    ])

    combined = service._combine_ha_full_history(
        raw, stats, hour - timedelta(days=10), hour - timedelta(days=365),
        hour + timedelta(days=1), stats_supported=True,
    )

    assert combined.rows == [stats.rows[0], *raw.rows]
    assert combined.source_details["cutover_ts"] == raw.rows[0][0]
    assert combined.source_details["raw"]["discarded_at_seam"] == 0
    assert combined.source_details["seam_status"] == "quellenluecke_nicht_vergroessert"


def test_full_import_without_statistics_keeps_complete_raw_history() -> None:
    service = _service()
    start = datetime(2026, 8, 20, tzinfo=timezone.utc)
    raw = ha_import.HistoryFetchResult(rows=[
        ((start + timedelta(minutes=3)).timestamp(), 1.0),
        ((start + timedelta(minutes=8)).timestamp(), 2.0),
    ])

    combined = service._combine_ha_full_history(
        raw, None, start, start - timedelta(days=365), start + timedelta(days=1),
        stats_requested=False,
    )

    assert combined.rows == raw.rows
    assert combined.source_details["cutover_ts"] is None
    assert combined.source_details["stats"]["enabled"] is False


def test_full_fetch_reads_raw_before_statistic_metadata_and_rows(monkeypatch) -> None:
    service = _service()
    start = datetime(2026, 8, 20, tzinfo=timezone.utc)
    calls: list[str] = []

    def raw(*args, **kwargs):
        calls.append("raw")
        return ha_import.HistoryFetchResult(rows=[(start.timestamp(), 1.0)])

    def meta(entity_ids):
        calls.append("meta")
        return {"sensor.demo": ha_statistics.StatisticMeta("sensor.demo", has_mean=True)}

    def stats(*args, **kwargs):
        calls.append("stats")
        return ha_import.HistoryFetchResult()

    monkeypatch.setattr(ha_import, "fetch_history_rows", raw)
    monkeypatch.setattr(ha_statistics, "fetch_statistic_meta", meta)
    monkeypatch.setattr(ha_statistics, "fetch_statistics_rows", stats)

    fetched, errors = service._fetch_ha_full_history(
        ["sensor.demo"], start - timedelta(days=10), start - timedelta(days=365),
        start + timedelta(days=1), True,
    )

    assert errors == []
    assert "sensor.demo" in fetched
    assert calls == ["raw", "meta", "stats"]


def test_full_fetch_keeps_raw_when_statistics_request_fails(monkeypatch) -> None:
    service = _service()
    start = datetime(2026, 8, 20, tzinfo=timezone.utc)
    raw_rows = [(start.timestamp(), 1.0)]
    monkeypatch.setattr(
        ha_import, "fetch_history_rows",
        lambda *args, **kwargs: ha_import.HistoryFetchResult(rows=raw_rows),
    )
    monkeypatch.setattr(
        ha_statistics, "fetch_statistic_meta",
        lambda entity_ids: {
            "sensor.demo": ha_statistics.StatisticMeta("sensor.demo", has_mean=True)
        },
    )
    monkeypatch.setattr(
        ha_statistics, "fetch_statistics_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(ha_import.HaApiError("nicht erreichbar")),
    )

    fetched, errors = service._fetch_ha_full_history(
        ["sensor.demo"], start - timedelta(days=10), start - timedelta(days=365),
        start + timedelta(days=1), True,
    )

    assert fetched["sensor.demo"].rows == raw_rows
    assert fetched["sensor.demo"].source_details["stats"]["enabled"] is True
    assert any("Langzeitstatistik" in error for error in errors)


def test_full_availability_cache_is_separate_for_ranges_and_stats_option() -> None:
    service = _service()
    availability = {"sensor.demo": ha_import.EntityAvailability("sensor.demo", count=1)}
    service._ha_cache_store(
        "full", "hour", availability, None, "10d", "365d", True
    )

    assert service._ha_cache_lookup("full", "hour", "10d", "365d", True) is not None
    assert service._ha_cache_lookup("full", "hour", "30d", "365d", True) is None
    assert service._ha_cache_lookup("full", "hour", "10d", "365d", False) is None


def test_full_import_ui_uses_existing_app_typography_and_controls() -> None:
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    section = (templates / "_ha_import_section.html").read_text(encoding="utf-8")
    page = (templates / "import.html").read_text(encoding="utf-8")
    ha_page = page.split('<div id="tab-ha"', 1)[1]

    assert "Vollimport" in section
    assert '<input type="hidden" name="include_long_term_stats" value="on">' in section
    assert 'type="checkbox" name="include_long_term_stats"' not in section
    assert 'class="ha-import-config-label">Importmodus' in section
    assert 'class="ha-import-check-row"' in section
    assert ".ha-import-check-row>.btn{background:var(--surface);border-color:var(--border-strong);}" in page
    assert "Archivlücken füllen" in section
    assert 'class="ha-archive-help"' in section
    assert "data-tooltip=" not in section
    assert "Betroffene Monatsarchive und Rollups werden anschließend neu aufgebaut" in section
    assert section.index("Archivlücken füllen") < section.index("Verfügbarkeit prüfen")
    actionbar = section.split('<div class="actionbar">', 1)[1].split("</div>", 1)[0]
    assert 'name="include_existing_months"' not in actionbar
    assert 'class="ha-import-select"' in section
    assert 'class="btn primary"' in section
    assert ".ha-import-select,#ha-period-select{width:250px" in page
    assert "font-family:Arial" not in page
    assert "Der empfohlene Vollimport verbindet ältere Langzeitstatistik" in ha_page
    assert "weder zeitliche Lücken noch Überschneidungen" in ha_page
    assert "Bestehende Monatsarchive werden nicht verändert" not in ha_page


def test_full_availability_uses_non_redundant_value_labels() -> None:
    service = _service()
    context = service._ha_full_availability_context({
        "raw": {"count": 12, "first_ts": 1, "last_ts": 2, "supported": True},
        "stats": {"count": 8, "first_ts": 1, "last_ts": 2, "supported": True},
        "stats_enabled": True,
    })

    assert context["raw"]["label"].endswith("12 Werte")
    assert context["stats"]["label"].endswith("8 Werte")
    assert "Rohwerte" not in context["raw"]["label"]
    assert "Statistik-Werte" not in context["stats"]["label"]
