"""Mehrfach-Löschen in der Entitäten-Übersicht: Route /entities/delete-selected,
/entities/usage-check und die Verdrahtung von Auswahl, Leiste und Dialog."""

from __future__ import annotations

import pytest

from _paths import APP, page_text

JS = (APP / "static/js/pages/entities_list.js").read_text(encoding="utf-8")


@pytest.fixture()
def entities(client):
    from app import main

    ids = ["sensor.bulk_a", "sensor.bulk_b", "sensor.bulk_c"]
    for entity_id in ids:
        main.index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    yield ids
    for entity_id in ids:
        if main.index.get_entity(entity_id) is not None:
            main.index.delete_entity(entity_id)


def test_delete_selected_removes_only_the_chosen_entities(client, entities) -> None:
    from app import main

    response = client.post("/entities/delete-selected", data={"entity_id": entities[:2]})

    assert response.status_code == 200
    assert response.json() == {"deleted": entities[:2], "missing": [], "failed": []}
    assert main.index.get_entity(entities[0]) is None
    assert main.index.get_entity(entities[1]) is None
    assert main.index.get_entity(entities[2]) is not None


def test_delete_selected_reports_unknown_and_invalid_ids_without_aborting(client, entities) -> None:
    from app import main

    response = client.post(
        "/entities/delete-selected",
        data={"entity_id": ["sensor.gibt_es_nicht", "../kaputt", entities[0], entities[0]]},
    )

    body = response.json()
    assert body["deleted"] == [entities[0]]  # doppelte IDs werden nur einmal verarbeitet
    assert body["missing"] == ["sensor.gibt_es_nicht"]
    assert [f["entity_id"] for f in body["failed"]] == ["../kaputt"]
    assert main.index.get_entity(entities[0]) is None


def test_delete_selected_without_ids_is_rejected(client) -> None:
    assert client.post("/entities/delete-selected", data={}).status_code == 400


def test_usage_check_flags_charts_that_use_the_entity(client, entities) -> None:
    from app import main

    chart_id = main.index.create_saved_chart("Bulk-Test-Diagramm", [entities[0]], "day", False)
    try:
        body = client.post("/entities/usage-check", data={"entity_id": entities[:2]}).json()
    finally:
        main.index.delete_saved_chart(chart_id)

    assert [u["kind"] for u in body["usages"][entities[0]]] == ["chart"]
    assert body["usages"][entities[1]] == []
    assert body["summary"] == {"requested": 2, "in_use": 1, "needs_attention": 1}


def test_table_has_a_selection_column_and_rows_carry_id_and_name() -> None:
    table = page_text("_entities_table.html")
    assert 'class="sel-all"' in table
    assert 'class="sel-row" value="{{ row.entity_id }}" data-name="{{ row.display_name }}"' in table
    assert table.count('<col class="col-sel">') == 1


def test_page_loads_the_confirm_dialog_script_it_depends_on() -> None:
    # Ohne dieses Skript fehlt window.appConfirm, und „Löschen …“ bricht nach der Verwendungsprüfung ab.
    assert "js/confirm-dialog.js" in page_text("entities_list.html")


def test_selection_bar_lives_outside_the_reloaded_fragment() -> None:
    page = page_text("entities_list.html")
    assert page.index('id="sel-bar"') < page.index('<div id="entities-table"></div>')
    assert 'id="sel-bar"' not in page_text("_entities_table.html")


def test_selection_survives_reloads_and_select_all_only_touches_visible_rows() -> None:
    assert "const selection = new Map();" in JS
    assert "sessionStorage" in JS and "entitiesTableEl.addEventListener('htmx:afterSwap', syncSelectionUI)" in JS
    assert "document.querySelectorAll('#entities-table .sel-row').forEach(cb => {" in JS


def test_delete_asks_for_usage_before_confirming_and_keeps_failed_ids_selected() -> None:
    assert JS.index("'entities/usage-check'") < JS.index("window.appConfirm('', {")
    assert JS.index("window.appConfirm('', {") < JS.index("'entities/delete-selected'")
    assert "danger: true" in JS
    assert "(result.deleted || []).concat(result.missing || []).forEach(id => selection.delete(id));" in JS


def test_selection_column_is_hidden_until_selection_mode_is_switched_on(client) -> None:
    page = client.get("/entities").text
    css = (APP / "static/css/pages/entities_list.css").read_text(encoding="utf-8")

    assert 'id="entities-panel"' in page and 'id="sel-toggle"' in page
    assert "#entities-panel:not(.is-selecting) .col-sel{display:none;}" in css
    # Beenden verwirft die Markierungen, sonst blieben unsichtbar markierte Entitäten stehen.
    assert "if (!selecting) {\n        selection.clear();" in JS


def test_backup_status_reports_last_success_and_job_result(client) -> None:
    from app import main

    job_id = main.index.create_backup_job("manual")
    main.index.update_backup_job(job_id, status="failed", error="Platte voll")

    body = client.get(f"/entities/backup-status?job_id={job_id}").json()

    assert body["job"] == {"status": "failed", "final": True, "error": "Platte voll"}
    assert {"running", "percent", "last_success_ts", "last_success_text"} <= set(body)
    assert client.get("/entities/backup-status?job_id=999999").json()["job"] is None


def test_backup_start_returns_the_job_the_dialog_waits_for(client, monkeypatch) -> None:
    from app import main

    started = []
    monkeypatch.setattr(main._background, "run_backup", lambda **kwargs: started.append(kwargs) or True)
    main._background.backup_progress.job_id = 4711

    assert client.post("/entities/backup-start").json() == {"job_id": 4711}
    assert started == [{}]


def test_delete_dialog_offers_a_backup_and_waits_for_it() -> None:
    assert "del-backup" in JS and "entities/backup-start" in JS
    # Gelöscht wird erst nach erfolgreichem Backup: onConfirm bremst das Schließen des Dialogs.
    assert "onConfirm: runBackupIfWanted" in JS
    dialog = (APP / "static/js/confirm-dialog.js").read_text(encoding="utf-8")
    assert "opts.onConfirm" in dialog
