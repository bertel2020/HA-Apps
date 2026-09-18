"""Housekeeping → Aktivität: die vier Filter (Entität/Aktionstyp/Status/
Zeitraum) und die lesbare Aufbereitung des Verdichten-Detail-Felds.

Schreiben und Lesen von entity_actions selbst deckt test_index.py bereits ab
(test_log_entity_action_round_trips_and_lists_newest_first) — hier geht es nur
um das, was NUR die Route beisteuert: filtern und das JSON-detail-Feld einer
Verdichten-Zeile in Klartext übersetzen.
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
    assert "sensor.pytest_activity_entity_a" in html
    assert "sensor.pytest_activity_entity_b" not in html


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
    assert "Ziel 1 Std." in html
    assert "2 Monate" in html
    assert "17.280 → 1.440 Zeilen" in html


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
    assert "Mindestalter 3 Monate" in tabelle
    assert "5 Monate neu berechnet" in tabelle
    assert "Automatisch" in tabelle


def test_other_action_types_show_no_detail(client) -> None:
    """Nur Verdichten füllt das detail-Feld — die Zelle bleibt für die anderen
    Aktionstypen ein einfacher Platzhalter statt eines leeren Strings."""
    from app.main import index

    _entity(index, "sensor.pytest_activity_no_detail")
    now = time.time()
    index.log_entity_action(
        "sensor.pytest_activity_no_detail", "add", "manual", now, now, "success", rows_affected=1
    )

    html = client.get("/housekeeping/activity?entity=sensor.pytest_activity_no_detail").text
    row = html[html.index("sensor.pytest_activity_no_detail"):]
    row = row[:row.index("</tr>")]
    assert "—" in row


def test_the_entity_appears_as_a_filter_option_on_the_full_page(client) -> None:
    """Die Filter-Steuerung selbst lebt in housekeeping.html, außerhalb des per
    htmx getauschten #activity-body — nur ein Aufruf der ganzen Seite zeigt sie."""
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
