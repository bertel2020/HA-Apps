"""Tests für die KPI-Kacheln im Energiedashboard: alle fünf öffnen ein Diagramm-Popup
(openTile() in energiedashboard.js, ROADMAP 1.41), dessen Zeilen und „Entität öffnen“
im Skript zur Entität verlinken (aktueller Zeitraum/Offset)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo


from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.energiedashboard_routes import (
    EnergieDashboardDependencies,
    EnergieDashboardService,
    _empty_config,
    _save_config,
)
from app.storage.index import Index

from _paths import APP, TEMPLATES, page_text, template_text

TEMPLATES_DIR = TEMPLATES


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


def test_page_context_carries_app_root_for_the_template_links() -> None:
    """Ohne app_root im Kontext würden die Entitätslinks der Vorlage (Qualitätsdialog: Zählerrücksetzungen und
    Auffälligkeiten) unter Ingress am dynamischen Pfad-Präfix vorbeizeigen (siehe _app_root_context() in main.py).
    Die Links des Popups entstehen im Skript und sind relativ, wie die fetch()-Aufrufe dort."""
    def run(service, index) -> None:
        _save_config(index, _empty_config())
        assert service._page_context(_request())["app_root"] == "/ingress-prefix"

    _with_service(run)


_VIEW_TEMPLATE = template_text("_energiedashboard_view.html")
JS = (APP / "static/js/energiedashboard.js").read_text(encoding="utf-8")


def test_alle_kacheln_oeffnen_das_diagramm_popup() -> None:
    """Alle fünf Kennzahlen-Kacheln sind <button> (tastaturbedienbar) und öffnen dasselbe Popup, unabhängig davon,
    wie viele Quellen konfiguriert sind."""
    for kpi in ("erzeugung", "netzbezug", "einspeisung", "verbrauch", "speicher"):
        assert f"@click=\"openTile('{kpi}')\"" in _VIEW_TEMPLATE, kpi
    assert 'x-ref="tileDialog"' in _VIEW_TEMPLATE
    assert '<a class="edash-kpi' not in _VIEW_TEMPLATE


def test_kacheln_ohne_quelle_sind_deaktiviert() -> None:
    assert "{% if not config.netzbezug %} disabled{% endif %}" in _VIEW_TEMPLATE
    assert "{% if not config.einspeisung %} disabled{% endif %}" in _VIEW_TEMPLATE
    assert "{% if not config.get('erzeuger') %} disabled{% endif %}" in _VIEW_TEMPLATE


def test_die_alten_auswahlfenster_und_ihre_kontextwerte_sind_weg() -> None:
    """Das Popup ersetzt die Auswahllisten; der Seitenkontext trägt keine single_*-IDs mehr."""
    for name in ("erzeugerPickerDialog", "verbraucherPickerDialog", "speicherPickerDialog", "is-pickable"):
        assert name not in _VIEW_TEMPLATE, name
    assert "single_" not in _VIEW_TEMPLATE
    assert "edash-entity-picker" not in page_text("energiedashboard.html")


def test_popup_zeilen_verlinken_mit_aktuellem_zeitraum_und_offset() -> None:
    """Die Zeilen und „Entität öffnen“ entstehen im Skript (entityHref) und tragen Zeitraum/Offset der Seite."""
    assert "range=${this.range}&offset=${this.offset}" in JS
    assert "entityHref(tile.data.entity_id)" in _VIEW_TEMPLATE
    # Im Qualitätsdialog bleiben die Entitätslinks der Zählerrücksetzungen und der Auffälligkeiten.
    assert _VIEW_TEMPLATE.count("?range=${range}&offset=${offset}`") == 2


def test_popup_renders_correctly_with_real_config() -> None:
    """End-to-End-Rendering (nicht nur String-Check) — fängt z. B. einen falschen Jinja-Variablennamen ab."""
    def run(service, index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["erzeuger"] = [{"entity_id": "sensor.pv1", "name": "Dach Süd"}]
        _save_config(index, config)

        html = service.deps.templates.get_template("_energiedashboard_view.html").render(
            service._page_context(_request()),
            configured=True, kpi={}, kpiCompare={}, quality={}, anomalien=[],
        )

        assert 'x-ref="tileDialog"' in html
        assert html.count('class="edash-kpi edash-kpi-button') == 5
        # Einspeisung ist nicht konfiguriert: deaktivierte Kachel statt Link.
        assert "@click=\"openTile('einspeisung')\" disabled" in html
        assert "@click=\"openTile('netzbezug')\" disabled" not in html

    _with_service(run)


def test_popup_mode_switcher_is_accessible_and_only_applies_where_offered() -> None:
    """aria-pressed wie an anderen Umschaltern der App; ein gespeicherter Modus gilt nur, wenn die Kachel ihn anbietet;
    die Grundlast wird am kind erkannt, nicht am Namen."""
    assert _VIEW_TEMPLATE.count(":aria-pressed=\"tile.mode ===") == 3
    assert "this.tileHasShare() && mode === 'share'" in JS
    assert "this.tileHasModes() ? this.tile.mode : 'bar'" in JS
    assert "row.name === t('Grundlast')" not in JS
