"""Tests für den statischen "zurück zum Energiedashboard"-Link auf der
Entitäts-Detailseite (Bugreport: unter Home-Assistant-Ingress fehlte der
referrer-basierte Link aus dynamic-back-link.js beim Sprung aus der
Energiedashboard-Kachel in der Sidebar — document.referrer ist dort
offenbar nicht zuverlässig gesetzt). Ein serverseitig berechneter,
rollenbasierter Link statt eines rein clientseitigen behebt das
unabhängig von der genauen Ursache."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.energiedashboard_routes import _empty_config, _save_config, entity_has_energiedashboard_role
from app.storage.index import Index

ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (ROOT / "app/main.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "app/templates/entity_detail.html").read_text(encoding="utf-8")


def _with_index(fn) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-edash-backlink-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        try:
            fn(index)
        finally:
            index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_netzbezug_and_einspeisung_count_as_a_role() -> None:
    def run(index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["einspeisung"] = "sensor.einspeisung"
        _save_config(index, config)
        assert entity_has_energiedashboard_role(index, "sensor.bezug")
        assert entity_has_energiedashboard_role(index, "sensor.einspeisung")
        assert not entity_has_energiedashboard_role(index, "sensor.unrelated")

    _with_index(run)


def test_erzeuger_verbraucher_and_speicher_roles_count() -> None:
    def run(index) -> None:
        config = _empty_config()
        config["netzbezug"] = "sensor.bezug"
        config["erzeuger"] = [{"entity_id": "sensor.pv1"}]
        config["verbraucher"] = [{"entity_id": "sensor.wm"}]
        config["speicher"] = [{"laden_entity_id": "sensor.akku_laden", "entladen_entity_id": "sensor.akku_entladen"}]
        _save_config(index, config)
        for entity_id in ("sensor.pv1", "sensor.wm", "sensor.akku_laden", "sensor.akku_entladen"):
            assert entity_has_energiedashboard_role(index, entity_id)

    _with_index(run)


def test_entity_detail_route_passes_used_in_energiedashboard_into_context() -> None:
    assert '"used_in_energiedashboard": entity_has_energiedashboard_role(index, entity_id),' in MAIN_SOURCE


def test_template_renders_a_static_link_independent_of_the_referrer() -> None:
    assert "{% if used_in_energiedashboard %}" in TEMPLATE
    assert (
        '<a href="{{ base }}/energiedashboard?range={{ initial_range or \'day\' }}&amp;offset={{ initial_offset }}">'
        "&larr; zurück zum Energiedashboard</a>"
    ) in TEMPLATE
