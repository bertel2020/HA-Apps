"""Tests für app/energiedashboard_routes.py — schema_version-Schutz in
_load_config()/_save_config() gegen einen Downgrade auf eine ältere
Zeitarchiv-Version (siehe ROADMAP.md, "Neu seit 0.76.1", Punkt 3)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.energiedashboard_routes import (
    CONFIG_SCHEMA_VERSION,
    SETTING_CONFIG,
    _empty_config,
    _load_config,
    _save_config,
)
from app.storage.index import Index


def _with_index(fn) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-energiedashboard-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        try:
            fn(index)
        finally:
            index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_config_writes_current_schema_version() -> None:
    def run(index) -> None:
        _save_config(index, _empty_config())
        stored = json.loads(index.get_setting(SETTING_CONFIG, ""))
        assert stored["schema_version"] == CONFIG_SCHEMA_VERSION

    _with_index(run)


def test_load_config_treats_legacy_config_without_schema_version_as_version_zero() -> None:
    """Eine Config von vor der Versionierung (kein schema_version-Feld) muss
    weiterhin normal geladen werden — inklusive der bestehenden
    speicher-Dict-zu-Liste-Migration."""
    def run(index) -> None:
        legacy = _empty_config()
        legacy["speicher"] = {"entity_id": "sensor.akku", "name": "Akku"}
        index.set_setting(SETTING_CONFIG, json.dumps(legacy, ensure_ascii=False))

        config = _load_config(index)

        assert config["speicher"] == [{"entity_id": "sensor.akku", "name": "Akku"}]

    _with_index(run)


def test_load_config_returns_empty_for_config_from_newer_schema_version() -> None:
    """Eine schema_version über dem eigenen Stand (Downgrade-Fall) darf nicht
    interpretiert werden — sicherer leerer Stand statt zu raten oder an
    einer unbekannten Form zu crashen."""
    def run(index) -> None:
        from_the_future = _empty_config()
        from_the_future["hub_name"] = "Sollte nicht ankommen"
        from_the_future["schema_version"] = CONFIG_SCHEMA_VERSION + 1
        index.set_setting(SETTING_CONFIG, json.dumps(from_the_future, ensure_ascii=False))

        config = _load_config(index)

        assert config == _empty_config()

    _with_index(run)


def test_load_config_does_not_overwrite_stored_config_from_newer_version() -> None:
    """_load_config() speichert bei einer zu neuen schema_version nichts
    zurück — die eigentlichen, neueren Daten müssen unangetastet in der DB
    bleiben, damit ein späteres Update sie wiederherstellt."""
    def run(index) -> None:
        from_the_future = _empty_config()
        from_the_future["hub_name"] = "Sollte nicht verloren gehen"
        from_the_future["schema_version"] = CONFIG_SCHEMA_VERSION + 1
        raw_before = json.dumps(from_the_future, ensure_ascii=False)
        index.set_setting(SETTING_CONFIG, raw_before)

        _load_config(index)

        assert index.get_setting(SETTING_CONFIG, "") == raw_before

    _with_index(run)


def test_save_then_load_config_roundtrips_normal_values() -> None:
    def run(index) -> None:
        config = _empty_config()
        config["hub_name"] = "Haupthaus"
        config["speicher"] = [{"entity_id": "sensor.akku", "name": "Akku"}]
        _save_config(index, config)

        loaded = _load_config(index)

        assert loaded["hub_name"] == "Haupthaus"
        assert loaded["speicher"] == [{"entity_id": "sensor.akku", "name": "Akku"}]

    _with_index(run)


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()


def test_sankey_legende_ist_standardmaessig_aus() -> None:
    """Anders als die Kachel-Schalter (alle per Default an): der Energiefluss
    ist auch ohne Legende lesbar, deshalb soll die zusätzliche Zeile unter dem
    Diagramm niemandem ungefragt erscheinen."""
    assert _empty_config()["show_sankey_legende"] is False


def test_bestehende_config_ohne_legenden_schluessel_bleibt_aus() -> None:
    """_load_config() übernimmt nur Schlüssel, die in _empty_config() stehen —
    eine vor dieser Version gespeicherte Konfiguration kennt den Schalter nicht
    und darf die Legende deshalb nicht plötzlich einblenden."""

    def check(index: Index) -> None:
        gespeichert = _empty_config()
        del gespeichert["show_sankey_legende"]
        gespeichert["netzbezug"] = "sensor.netz"
        index.set_setting(
            SETTING_CONFIG,
            json.dumps({**gespeichert, "schema_version": CONFIG_SCHEMA_VERSION}),
        )
        geladen = _load_config(index)
        assert geladen["show_sankey_legende"] is False
        assert geladen["netzbezug"] == "sensor.netz"

    _with_index(check)


def test_eingeschaltete_legende_ueberlebt_speichern_und_laden() -> None:
    def check(index: Index) -> None:
        config = _empty_config()
        config["show_sankey_legende"] = True
        _save_config(index, config)
        assert _load_config(index)["show_sankey_legende"] is True

    _with_index(check)


def test_view_blendet_die_legende_nur_bei_eingeschaltetem_schalter_ein() -> None:
    """Serverseitig gegated, nicht per x-show: bei ausgeschalteter Legende soll
    das Markup gar nicht erst ausgeliefert werden. Der x-ref dient buildLegend()
    zusätzlich als Signal, die Einträge dann auch nicht zu berechnen."""
    view = (Path(__file__).resolve().parents[1] / "app/templates/_energiedashboard_view.html").read_text(
        encoding="utf-8"
    )
    start = view.index("{% if config.show_sankey_legende %}")
    ende = view.index("{% endif %}", start)
    block = view[start:ende]
    assert 'class="chart-legend edash-sankey-legend"' in block
    assert 'x-ref="legendEl"' in block

    js = (Path(__file__).resolve().parents[1] / "app/static/js/energiedashboard.js").read_text(
        encoding="utf-8"
    )
    assert "if (!this.$refs.legendEl) { this.legendItems = []; return; }" in js
