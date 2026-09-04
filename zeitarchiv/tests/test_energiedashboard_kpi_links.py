"""Tests für die KPI-Kachel-Links im Energiedashboard (alle fünf Kacheln ->
Entitäts-Chart mit übernommenem Zeitraum/Offset, siehe ROADMAP.md/
Nutzerwunsch). Netzbezug/Einspeisung sind serverseitig immer genau eine
Entität (direkter Link); Erzeugung/Verbrauch/Speicher können mehrere
Ziel-Entitäten sein — bei genau einer ebenfalls direkter Link, bei mehreren
ein kleines Auswahlfenster (siehe _page_context() in
energiedashboard_routes.py)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.energiedashboard_routes import (
    EnergieDashboardDependencies,
    EnergieDashboardService,
    _empty_config,
    _save_config,
)
from app.storage.index import Index

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


def _with_service(fn) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-energiedashboard-kpi-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        try:
            deps = EnergieDashboardDependencies(
                data_dir=tmp,
                index=index,
                tz=ZoneInfo("Europe/Berlin"),
                templates=Jinja2Templates(directory=str(TEMPLATES_DIR)),
                app_root_context=lambda request: {"app_root": "/ingress-prefix"},  # noqa: ARG005
            )
            fn(EnergieDashboardService(deps), index)
        finally:
            index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/energiedashboard", "headers": []})


def test_single_erzeuger_id_present_when_exactly_one_configured() -> None:
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["erzeuger"] = [{"entity_id": "sensor.pv1", "name": "PV1"}]
        _save_config(index, config)

        ctx = service._page_context(_request())

        assert ctx["single_erzeuger_id"] == "sensor.pv1"

    _with_service(run)


def test_single_erzeuger_id_absent_when_zero_or_multiple_configured() -> None:
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["erzeuger"] = [{"entity_id": "sensor.pv1"}, {"entity_id": "sensor.pv2"}]
        _save_config(index, config)
        assert service._page_context(_request())["single_erzeuger_id"] is None

        config["erzeuger"] = []
        _save_config(index, config)
        assert service._page_context(_request())["single_erzeuger_id"] is None

    _with_service(run)


def test_single_verbraucher_id_present_only_when_exactly_one_configured() -> None:
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["verbraucher"] = [{"entity_id": "sensor.waschmaschine", "name": "Waschmaschine"}]
        _save_config(index, config)
        assert service._page_context(_request())["single_verbraucher_id"] == "sensor.waschmaschine"

        config["verbraucher"] = [{"entity_id": "sensor.a"}, {"entity_id": "sensor.b"}]
        _save_config(index, config)
        assert service._page_context(_request())["single_verbraucher_id"] is None

    _with_service(run)


def test_single_speicher_id_present_only_when_exactly_one_link_entity() -> None:
    """Ein einzelner Speicher zählt hier trotzdem als "mehrere", sobald er
    sowohl Laden- als auch Entladen-Entität hat (der Normalfall) — nur bei
    genau EINER der beiden (oder genau einem Speicher mit nur einer Rolle
    konfiguriert) gibt es einen direkten Link."""
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["speicher"] = [{"name": "Akku", "laden_entity_id": "sensor.akku_laden"}]
        _save_config(index, config)
        assert service._page_context(_request())["single_speicher_id"] == "sensor.akku_laden"

        config["speicher"] = [
            {"name": "Akku", "laden_entity_id": "sensor.akku_laden", "entladen_entity_id": "sensor.akku_entladen"},
        ]
        _save_config(index, config)
        assert service._page_context(_request())["single_speicher_id"] is None

    _with_service(run)


def test_speicher_link_entities_labels_each_role() -> None:
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["speicher"] = [
            {"name": "Akku 1", "laden_entity_id": "sensor.a_laden", "entladen_entity_id": "sensor.a_entladen"},
            {"name": "Akku 2", "laden_entity_id": "sensor.b_laden"},
        ]
        _save_config(index, config)

        entities = service._page_context(_request())["speicher_link_entities"]

        assert entities == [
            {"entity_id": "sensor.a_laden", "name": "Akku 1 (Ladung)"},
            {"entity_id": "sensor.a_entladen", "name": "Akku 1 (Entladung)"},
            {"entity_id": "sensor.b_laden", "name": "Akku 2 (Ladung)"},
        ]

    _with_service(run)


def test_page_context_carries_app_root_for_kpi_links() -> None:
    """Ohne app_root im Kontext würden die Kachel-Links unter Ingress am
    dynamischen Pfad-Präfix vorbeizeigen (siehe _app_root_context() in
    main.py)."""
    def run(service, index) -> None:
        _save_config(index, _empty_config())
        assert service._page_context(_request())["app_root"] == "/ingress-prefix"

    _with_service(run)


_VIEW_TEMPLATE = (TEMPLATES_DIR / "_energiedashboard_view.html").read_text(encoding="utf-8")


def test_erzeugung_und_verbrauch_kacheln_verlinken_nur_bei_eindeutiger_quelle() -> None:
    assert 'single_erzeuger_id %}:href="`{{ app_root }}/entities/{{ single_erzeuger_id }}' in _VIEW_TEMPLATE
    assert 'single_verbraucher_id %}:href="`{{ app_root }}/entities/{{ single_verbraucher_id }}' in _VIEW_TEMPLATE


def test_netzbezug_und_einspeisung_kacheln_verlinken_immer() -> None:
    assert 'config.netzbezug %}:href="`{{ app_root }}/entities/{{ config.netzbezug }}' in _VIEW_TEMPLATE
    assert 'config.einspeisung %}:href="`{{ app_root }}/entities/{{ config.einspeisung }}' in _VIEW_TEMPLATE


def test_speicher_kachel_verlinkt_ebenfalls() -> None:
    assert 'single_speicher_id %}:href="`{{ app_root }}/entities/{{ single_speicher_id }}' in _VIEW_TEMPLATE
    assert '<a class="edash-kpi has-tooltip tooltip-lines' in _VIEW_TEMPLATE
    assert '<div class="edash-kpi has-tooltip tooltip-lines" x-show="kpi.speicher_netto' not in _VIEW_TEMPLATE


def test_kpi_links_carry_current_range_and_offset() -> None:
    # 5 KPI-Kacheln (Erzeugung/Verbrauch/Netzbezug/Einspeisung/Speicher) + je
    # ein Link pro Zeile in den drei Auswahlfenstern (Erzeuger-/Verbraucher-/
    # Speicher-Liste).
    assert _VIEW_TEMPLATE.count("?range=${range}&offset=${offset}`") == 8


def test_multiple_erzeuger_verbraucher_or_speicher_open_a_picker_dialog_instead_of_no_link() -> None:
    """Nutzerwunsch: bei mehreren Ziel-Entitäten soll die Kachel trotzdem
    funktionieren, statt einfach unverlinkt zu bleiben — ein kleines
    Auswahlfenster (dasselbe <dialog>-Muster wie die Trend-Popups) lässt eine
    der konfigurierten Entitäten wählen."""
    for key in ("erzeuger", "verbraucher", "speicher"):
        assert f'@click="$refs.{key}PickerDialog.showModal()"' in _VIEW_TEMPLATE
        assert f'x-ref="{key}PickerDialog"' in _VIEW_TEMPLATE
    # Dialoge sind an eine Bedingung geknüpft (nur gerendert, wenn gebraucht)
    assert "{% if erzeuger_list|length > 1 %}" in _VIEW_TEMPLATE
    assert "{% if verbraucher_list|length > 1 %}" in _VIEW_TEMPLATE
    assert "{% if speicher_link_entities|length > 1 %}" in _VIEW_TEMPLATE


def test_picker_dialogs_share_one_compact_uniform_size() -> None:
    """Nutzerwunsch: alle Auswahlfenster gleich groß, nicht die
    .detail-dialog-Standardbreite (bis 860px, für Datenqualität/Kosten-
    Tabellen gedacht) — eine kurze Namensliste braucht deutlich weniger."""
    assert _VIEW_TEMPLATE.count('class="detail-dialog edash-entity-picker-dialog"') == 3
    energiedashboard_html = (TEMPLATES_DIR / "energiedashboard.html").read_text(encoding="utf-8")
    assert ".edash-entity-picker-dialog{width:" in energiedashboard_html


def test_picker_dialog_renders_correctly_with_real_config() -> None:
    """End-to-End-Rendering (nicht nur String-Check) — fängt z. B. einen
    falschen Jinja-Loop-Variablennamen ab, den die reinen Substring-Tests
    oben nicht sehen würden."""
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["einspeisung"] = "sensor.einspeisung"
        config["erzeuger"] = [
            {"entity_id": "sensor.pv1", "name": "Dach Süd"},
            {"entity_id": "sensor.pv2", "name": "Dach West"},
        ]
        config["verbraucher"] = [{"entity_id": "sensor.waschmaschine", "name": "Waschmaschine"}]
        config["speicher"] = [
            {"name": "Akku", "laden_entity_id": "sensor.akku_laden", "entladen_entity_id": "sensor.akku_entladen"},
        ]
        _save_config(index, config)

        html = service.deps.templates.get_template("_energiedashboard_view.html").render(
            service._page_context(_request()),
            configured=True, kpi={}, kpiCompare={}, quality={}, anomalien=[],
        )

        assert "Dach Süd" in html
        assert "Dach West" in html
        assert "Akku (Ladung)" in html
        assert "Akku (Entladung)" in html
        assert 'x-ref="erzeugerPickerDialog"' in html
        assert 'x-ref="speicherPickerDialog"' in html
        # Genau ein Verbraucher -> direkter Link, kein Auswahlfenster nötig.
        assert 'x-ref="verbraucherPickerDialog"' not in html

    _with_service(run)
