"""Housekeeping → Aktivität: die vier Filter (Entität/Aktionstyp/Status/
Zeitraum) und die lesbare Aufbereitung des Verdichten-/Bereinigen-Detail-
Felds als klickbares Popup (kein eigenes Tabellenfeld, siehe
showActivityDetail() in confirm-dialog.js).

Schreiben und Lesen von entity_actions selbst deckt test_index.py bereits ab
(test_log_entity_action_round_trips_and_lists_newest_first) — hier geht es nur
um das, was NUR die Route beisteuert: filtern und das JSON-detail-Feld einer
Verdichten-/Bereinigen-Zeile in Klartext übersetzen.
"""

from __future__ import annotations

import json
import time


def _entity(index, entity_id: str) -> None:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")


def test_the_entity_filter_narrows_the_list_to_that_entity(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_activity_entity_a")
    _entity(index, "sensor.pytest_activity_entity_b")
    now = time.time()
    index.log_entity_action("sensor.pytest_activity_entity_a", "correct", "manual", now, now, "success", rows_affected=1)
    index.log_entity_action("sensor.pytest_activity_entity_b", "correct", "manual", now, now, "success", rows_affected=1)

    html = client.get("/housekeeping/activity?entity=sensor.pytest_activity_entity_a").text
    # Nur die Tabelle prüfen: der Filter selbst (mit ALLEN bekannten
    # Entitäten als Dropdown-Optionen) sitzt in derselben Antwort.
    tabelle = html[html.index("<tbody>"):html.index("</tbody>")]
    assert "sensor.pytest_activity_entity_a" in tabelle
    assert "sensor.pytest_activity_entity_b" not in tabelle


def test_the_action_filter_narrows_the_list_to_that_type(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_activity_action")
    now = time.time()
    index.log_entity_action("sensor.pytest_activity_action", "correct", "manual", now, now, "success", rows_affected=1)
    index.log_entity_action("sensor.pytest_activity_action", "add", "manual", now, now, "success", rows_affected=1)

    html = client.get("/housekeeping/activity?entity=sensor.pytest_activity_action&action=correct").text
    tabelle = html[html.index("<tbody>"):]
    assert "Korrektur" in tabelle
    assert "Hinzufügen" not in tabelle


def test_the_status_filter_narrows_the_list_to_that_status(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_activity_status")
    now = time.time()
    index.log_entity_action(
        "sensor.pytest_activity_status", "correct", "manual", now, now, "success", rows_affected=1
    )
    index.log_entity_action(
        "sensor.pytest_activity_status", "correct", "manual", now, now, "failed",
        error="Testfehler für pytest",
    )

    nur_erfolgreich = client.get("/housekeeping/activity?entity=sensor.pytest_activity_status&status=success").text
    assert "Erfolgreich" in nur_erfolgreich
    assert "Testfehler für pytest" not in nur_erfolgreich

    nur_fehlgeschlagen = client.get("/housekeeping/activity?entity=sensor.pytest_activity_status&status=failed").text
    assert "Fehlgeschlagen" in nur_fehlgeschlagen
    # Dasselbe Muster wie bei fehlgeschlagenen Retention-/Backup-Jobs
    # (_settings_retention_form.html, job-row-error/showJobError) — die
    # Zeile muss anklickbar sein, sonst bleibt der Fehlertext unsichtbar.
    assert 'class="job-row-error"' in nur_fehlgeschlagen
    assert "Testfehler für pytest" in nur_fehlgeschlagen


def test_the_days_filter_excludes_older_entries(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_activity_days")
    now = time.time()
    index.log_entity_action("sensor.pytest_activity_days", "add", "manual", now, now, "success", rows_affected=1)
    index._conn.execute(
        "INSERT INTO entity_actions (entity_id, action, trigger, started_at, finished_at, status, "
        "rows_affected, created_at) VALUES (?, 'add', 'manual', ?, ?, 'success', 1, ?)",
        ("sensor.pytest_activity_days_alt", now, now, now - 8 * 86400),
    )
    index._conn.commit()

    html = client.get("/housekeeping/activity?days=7").text
    assert ">sensor.pytest_activity_days<" in html
    assert "sensor.pytest_activity_days_alt" not in html

    html_alles = client.get("/housekeeping/activity?days=").text
    assert "sensor.pytest_activity_days_alt" in html_alles


def test_the_compact_detail_is_rendered_readably(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_activity_compact")
    now = time.time()
    index.log_entity_action(
        "sensor.pytest_activity_compact", "compact", "manual", now, now, "success",
        rows_affected=15840,
        detail=json.dumps({
            "target_resolution": "1h",
            "months_compacted": ["2023-10", "2023-11"],
            "rows_before": 17280,
            "rows_after": 1440,
        }),
    )

    html = client.get("/housekeeping/activity?entity=sensor.pytest_activity_compact").text
    tabelle = html[html.index("<tbody>"):]
    assert 'class="job-row-detail"' in tabelle
    assert 'onclick="showActivityDetail(this)"' in tabelle
    assert (
        'data-detail="Zielauflösung: 1 Std.\n'
        "Zeitraum: Oktober 2023, November 2023\n"
        'Zeilen: 17.280 → 1.440"'
    ) in tabelle


def test_the_compact_detail_mentions_cleaned_up_stale_markers(client) -> None:
    """compact_raw_values() räumt seit dem Fund vom 18.09.2026 verwaiste
    Löschmarkierungen des verdichteten Monats mit auf (siehe test_cleanup.py)
    — die Detailanzeige nennt das, wenn tatsächlich welche betroffen waren.
    Fehlt das Feld (ältere, vor diesem Fix geloggte Zeilen), erscheint dazu
    nichts — kein KeyError."""
    from app.main import index

    _entity(index, "sensor.pytest_activity_compact_stale")
    now = time.time()
    index.log_entity_action(
        "sensor.pytest_activity_compact_stale", "compact", "manual", now, now, "success",
        rows_affected=100,
        detail=json.dumps({
            "target_resolution": "1h", "months_compacted": ["2023-10"],
            "rows_before": 200, "rows_after": 100, "stale_markers_removed": 3,
        }),
    )
    index.log_entity_action(
        "sensor.pytest_activity_compact_stale", "compact", "manual", now, now, "success",
        rows_affected=50,
        detail=json.dumps({"target_resolution": "1h", "months_compacted": ["2023-11"]}),
    )

    html = client.get("/housekeeping/activity?entity=sensor.pytest_activity_compact_stale").text
    assert "Aufgeräumt: 3 verwaiste Löschmarkierungen" in html


def test_the_automatic_purge_detail_is_rendered_readably(client) -> None:
    """Wie test_the_compact_detail_is_rendered_readably, aber für die
    automatische Bereinigung (background.py _run_automatic_purge_if_due) —
    entity_id ist hier None (betrifft potenziell mehrere Entitäten), deshalb
    über den Aktionstyp statt über eine Entität gefiltert."""
    from app.main import index

    now = time.time()
    index.log_entity_action(
        None, "purge", "automatic", now, now, "success",
        rows_affected=321,
        detail=json.dumps({"min_age_days": 90, "months_purged": 5}),
    )

    html = client.get("/housekeeping/activity?action=purge").text
    tabelle = html[html.index("<tbody>"):]
    assert 'class="job-row-detail"' in tabelle
    assert (
        'data-detail="Mindestalter der Markierung: 3 Monate\n'
        'Neu berechnete Monate: 5"'
    ) in tabelle
    assert "Automatisch" in tabelle


def test_the_purge_detail_shows_the_month_count_even_when_it_is_one(client) -> None:
    from app.main import index

    now = time.time()
    index.log_entity_action(
        None, "purge", "automatic", now, now, "success",
        rows_affected=1,
        detail=json.dumps({"min_age_days": 7, "months_purged": 1}),
    )

    html = client.get("/housekeeping/activity?action=purge&status=success").text
    tabelle = html[html.index("<tbody>"):]
    assert (
        'data-detail="Mindestalter der Markierung: 1 Woche\n'
        'Neu berechnete Monate: 1"'
    ) in tabelle


def test_other_action_types_are_not_clickable(client) -> None:
    """Nur Verdichten/Bereinigen füllen das detail-Feld — eine Zeile ohne
    Detail (und ohne Fehler) bleibt ein normales <tr>, nicht anklickbar."""
    from app.main import index

    _entity(index, "sensor.pytest_activity_no_detail")
    now = time.time()
    index.log_entity_action(
        "sensor.pytest_activity_no_detail", "add", "manual", now, now, "success", rows_affected=1
    )

    html = client.get("/housekeeping/activity?entity=sensor.pytest_activity_no_detail").text
    tabelle = html[html.index("<tbody>"):html.index("</tbody>")]
    row_start = tabelle.rindex("<tr", 0, tabelle.index("sensor.pytest_activity_no_detail"))
    row = tabelle[row_start:tabelle.index("</tr>", row_start)]
    assert row.startswith("<tr>")  # kein zusätzliches Klasse/onclick-Attribut
    assert "onclick" not in row


def test_the_entity_appears_as_a_filter_option_on_the_full_page(client) -> None:
    """Die Filter-Steuerung lebt zwar innerhalb von #activity-body (direkt über
    der Tabelle statt neben der Überschrift), wird aber wie der Rest der Seite
    beim vollen /housekeeping-Aufruf mitgerendert."""
    from app.main import index

    _entity(index, "sensor.pytest_activity_option")
    now = time.time()
    index.log_entity_action(
        "sensor.pytest_activity_option", "correct", "manual", now, now, "success", rows_affected=1
    )

    html = client.get("/housekeeping").text
    assert 'id="activity-entity-btn"' in html
    assert 'id="activity-action-btn"' in html
    assert 'id="activity-status-btn"' in html
    assert 'id="activity-days-btn"' in html
    abschnitt = html[html.index('id="activity-entity-popover"'):html.index('id="activity-action-popover"')]
    assert "sensor.pytest_activity_option" in abschnitt
