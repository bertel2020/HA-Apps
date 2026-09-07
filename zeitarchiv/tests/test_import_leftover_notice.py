"""Meldung „Import-Quelldaten liegen noch" (housekeeping.import_leftovers).

Die entpackten Quelldaten bleiben nach einem Import **absichtlich** liegen —
`import_delete()` in import_routes.py sagt warum: „damit Zuordnung/Dry Run
beliebig oft wiederholbar sind, ohne jedes Mal neu hochladen zu müssen". Es
gibt dafür auch längst einen Knopf. Was fehlte, war der Hinweis: gemessen an
der Testinstanz lagen dort 3,0 GB in 47.494 Dateien, seit zwölf Tagen
unberührt, und damit der größte Einzelposten der Belegung.

Deshalb eine Auskunft und keine Aufforderung: `info`, stummschaltbar, mit dem
Weg zum vorhandenen Knopf.
"""

from __future__ import annotations

import time

import pytest

from app import notices as notices_mod

MB = 1024 * 1024


def _leftover_notice(client, leftovers, monkeypatch=None):
    from app.main import DATA_DIR, TZ, index

    notices_mod._import_leftovers = leftovers
    try:
        aktiv = notices_mod.build_notices(
            index, DATA_DIR / "index.sqlite", TZ, {}, None, 0,
            time.time(), time.time(), False,
        )
    finally:
        notices_mod._import_leftovers = None
    return next((n for n in aktiv if n["id"] == "housekeeping.import_leftovers"), None)


def test_it_reports_the_case_that_prompted_it(client) -> None:
    """Der gemessene Bestand der Testinstanz: 3,0 GB, seit zwölf Tagen
    unberührt."""
    jetzt = time.time()
    notice = _leftover_notice(client, {"bytes": 3_028_000_000, "newest_mtime": jetzt - 12 * 86400})
    assert notice is not None
    assert "2,8 GB" in notice["detail"]
    assert "12 Tagen" in notice["detail"]
    # Der Weg zum vorhandenen Knopf gehört in den Text, sonst ist die Meldung
    # eine Feststellung ohne Ausweg.
    assert "Daten löschen" in notice["detail"]
    assert notice["link"] == "/import"


def test_it_is_information_and_can_be_silenced(client) -> None:
    """Kein Fehler und kein Auftrag: die Dateien liegen dort, weil die App sie
    absichtlich behält. Wer sie behalten will, schaltet die Meldung weg — das
    geht nur, solange sie `info` oder `warn` ist."""
    notice = _leftover_notice(client, {"bytes": 3_028_000_000, "newest_mtime": time.time() - 12 * 86400})
    assert notice["severity"] == "info"
    assert notice["severity"] in notices_mod.MUTABLE_SEVERITIES


@pytest.mark.parametrize(
    "name, leftovers",
    [
        ("kein Wert", None),
        ("leeres Verzeichnis", {"bytes": 0, "newest_mtime": None}),
        ("unter der Größenschwelle", {"bytes": 50 * MB, "newest_mtime": time.time() - 30 * 86400}),
        ("frisch angefasst", {"bytes": 3_000_000_000, "newest_mtime": time.time() - 3600}),
    ],
)
def test_it_stays_quiet_where_it_would_only_be_noise(client, name, leftovers) -> None:
    """Die Größenschwelle hält kleine CSV-Reste heraus, die Altersschwelle eine
    laufende Import-Sitzung — zwischen Hochladen, Zuordnen und Probelauf können
    Stunden liegen, und genau dort würde die Meldung am meisten stören."""
    assert _leftover_notice(client, leftovers) is None, name


def test_the_button_the_notice_points_to_still_exists() -> None:
    """Die Meldung nennt „Daten löschen" unter Import. Verschwindet die Route,
    schickt sie Leute auf einen Knopf, den es nicht mehr gibt."""
    from app import import_routes
    from pathlib import Path

    quelle = Path(import_routes.__file__).read_text(encoding="utf-8")
    assert '@router.post("/import/delete"' in quelle
    assert '@router.post("/import/csv/delete"' in quelle


def test_the_walk_measures_size_and_age_in_one_pass(tmp_path, monkeypatch) -> None:
    """Beides aus einem Walk: die Größe entscheidet, ob es sich zu erwähnen
    lohnt, die jüngste Änderungszeit, ob die Import-Sitzung vorbei ist."""
    symcon = tmp_path / "symcon_import"
    (symcon / "db").mkdir(parents=True)
    (symcon / "db" / "a.csv").write_bytes(b"x" * 1000)
    (symcon / "db" / "b.csv").write_bytes(b"y" * 500)
    alt = time.time() - 5 * 86400
    import os
    os.utime(symcon / "db" / "a.csv", (alt, alt))
    os.utime(symcon / "db" / "b.csv", (alt - 86400, alt - 86400))

    monkeypatch.setattr(notices_mod, "_import_leftovers", None)
    monkeypatch.setattr(notices_mod, "_import_leftovers_checked_at", 0.0)
    notices_mod.refresh_import_leftovers_if_stale(symcon, tmp_path / "fehlt")

    stand = notices_mod._import_leftovers
    # Nur Dateien: das db-Verzeichnis selbst hat auf manchen Dateisystemen
    # eine Größe und wäre sonst mitgezählt.
    assert stand["bytes"] == 1500
    assert stand["newest_mtime"] == pytest.approx(alt, abs=1)


def test_the_walk_does_not_run_on_every_scheduler_tick(tmp_path, monkeypatch) -> None:
    """Gemessen 278 ms für 47.494 Dateien. Der Wartungsplaner taktet alle 30
    Sekunden — ohne eigene Altersschwelle liefe der Walk 120-mal pro Stunde."""
    basis = tmp_path / "symcon_import"
    basis.mkdir()
    (basis / "a.csv").write_bytes(b"x" * 10)
    fehlt = tmp_path / "fehlt"
    monkeypatch.setattr(notices_mod, "_import_leftovers", None)
    monkeypatch.setattr(notices_mod, "_import_leftovers_checked_at", 0.0)

    notices_mod.refresh_import_leftovers_if_stale(basis, fehlt)
    assert notices_mod._import_leftovers["bytes"] == 10

    (basis / "b.csv").write_bytes(b"y" * 999)
    notices_mod.refresh_import_leftovers_if_stale(basis, fehlt)
    assert notices_mod._import_leftovers["bytes"] == 10, "zweiter Walk trotz frischem Cache"

    monkeypatch.setattr(notices_mod, "_import_leftovers_checked_at", time.time() - 2 * 3600)
    notices_mod.refresh_import_leftovers_if_stale(basis, fehlt)
    assert notices_mod._import_leftovers["bytes"] == 1009


def test_the_scheduler_actually_refreshes_it() -> None:
    """Ohne den Aufruf im Wartungsplaner bliebe der Cache für immer None und
    die Meldung erschiene nie."""
    from pathlib import Path

    import app.main as main_mod

    quelle = Path(main_mod.__file__).read_text(encoding="utf-8")
    schleife = quelle.split("def _maintenance_scheduler_loop()")[1].split("\ndef ")[0]
    assert "notices_mod.refresh_import_leftovers_if_stale(" in schleife
