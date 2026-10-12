"""Tests für das Zurücksetzen des Datenbestands (Einstellungen → Zurücksetzen)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.storage import reset
from app.storage.index import Index


def _populate(root: Path, index: Index, entity_id: str) -> None:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
    index.set_config(entity_id, resolution="5min")
    index.record_write(entity_id, 100.0)
    index.mark_deleted(entity_id, [100.0])
    (root / "hot").mkdir(parents=True, exist_ok=True)
    (root / "archive" / entity_id).mkdir(parents=True, exist_ok=True)
    (root / "rollup" / entity_id).mkdir(parents=True, exist_ok=True)
    (root / "hot" / f"{entity_id}-2026-08.csv").write_text("1,2\n", encoding="utf-8")
    (root / "archive" / entity_id / "2026-07.parquet").write_bytes(b"a")
    (root / "rollup" / entity_id / "stunde.parquet").write_bytes(b"r")
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / "x.json").write_text("{}", encoding="utf-8")
    (root / "backups").mkdir(exist_ok=True)
    (root / "backups" / "zeitarchiv-backup.zip").write_bytes(b"zip")


@pytest.fixture
def env():
    root = Path(tempfile.mkdtemp(prefix="zeitarchiv-reset-"))
    try:
        index = Index(root / "index.sqlite")
        _populate(root, index, "sensor.a")
        index.set_setting("api_token", "secret")
        index.set_setting("backup_schedule", "daily")
        index.set_setting("font_scale", "1.2")
        yield root, index
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_values_scope_keeps_entities_and_settings(env) -> None:
    root, index = env
    before = reset.reset(root, index, reset.SCOPE_VALUES)
    assert before["entities"] == 1
    entity = index.get_entity("sensor.a")
    assert entity is not None
    assert entity["resolution"] == "5min"
    assert entity["row_count"] == 0
    assert index.get_deleted_counts_for_entity("sensor.a") == {}
    assert index.get_setting("font_scale") == "1.2"
    assert list((root / "hot").iterdir()) == []
    assert list((root / "archive").iterdir()) == []
    assert list((root / "rollup").iterdir()) == []
    assert (root / "reports" / "x.json").exists()
    assert (root / "backups" / "zeitarchiv-backup.zip").exists()


def test_full_scope_resets_but_keeps_token_backups_and_default_dashboard(env) -> None:
    root, index = env
    index.create_dashboard("Zweites")
    reset.reset(root, index, reset.SCOPE_FULL)
    assert index.get_entity("sensor.a") is None
    assert index.list_entities() == []
    assert index.get_setting("font_scale") is None
    assert index.get_setting("api_token") == "secret"
    assert index.get_setting("backup_schedule") == "daily"
    dashboards = index.list_dashboards()
    assert [d["id"] for d in dashboards] == [1]
    assert not (root / "reports" / "x.json").exists()
    assert (root / "backups" / "zeitarchiv-backup.zip").exists()
    assert (root / "hot").is_dir()


def test_unknown_scope_is_rejected(env) -> None:
    root, index = env
    with pytest.raises(ValueError):
        reset.reset(root, index, "everything")
    assert index.get_entity("sensor.a") is not None


def test_reset_form_renders_and_rejects_wrong_confirmation(client) -> None:
    """Das Formular erscheint, und ohne das richtige Bestätigungswort startet
    nichts — der deaktivierte Knopf im Browser ist nur Bedienkomfort.
    (Ein echter Lauf gegen die geteilte Test-App würde die Daten der übrigen
    Tests löschen, deshalb hier nur die Ablehnungswege.)"""
    page = client.get("/settings/reset")
    assert page.status_code == 200
    assert 'name="scope"' in page.text and 'name="confirm"' in page.text

    wrong_word = client.post("/settings/reset", data={"scope": "full", "confirm": "nein"})
    assert wrong_word.status_code == 422

    no_scope = client.post("/settings/reset", data={"confirm": "LÖSCHEN"})
    assert no_scope.status_code == 422
    assert client.get("/settings/reset/progress").text == ""
