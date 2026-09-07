"""Das Handbuch-Kapitel „Entität konfigurieren" gegen den Code.

Anlass: in diesem Kapitel standen gleich zwei Aussagen, die nicht stimmten —
die Ausreißer-Erkennung wurde falsch beschrieben („Abweichung gegenüber dem
Vorwert"), und zu dichte Werte hießen dort „verdichtet", obwohl sie verworfen
werden. Beides fiel erst bei einer Rückfrage auf.

Diese Zusagen prüfen nicht den Wortlaut, sondern die Stellen, an denen das
Kapitel konkrete Werte nennt: Auswahllisten und Konstanten. Genau die veralten
still, wenn jemand eine Stufe ergänzt oder einen Grenzwert ändert.
"""

from __future__ import annotations

from pathlib import Path

from app.formatting import (
    GAP_THRESHOLD_LABELS,
    OUTLIER_THRESHOLD_LABELS,
    RETENTION_LABELS,
)
from app.storage.cleanup import OUTLIER_WINDOW
from app.storage.index import (
    MAX_CUSTOM_NAME_LENGTH,
    SWITCH_DOMAINS,
    VALUE_FILTER_HEARTBEAT_SECONDS,
)

KAPITEL = (
    Path(__file__).resolve().parents[1] / "docs" / "user-guide.md"
).read_text(encoding="utf-8")
ABSCHNITT = KAPITEL[
    KAPITEL.index("## Entität konfigurieren") : KAPITEL.index("## Bereinigung")
]
# Zeilenumbrüche sind im Fließtext willkürlich gesetzt; für Zusagen über SÄTZE
# muss der Abschnitt flach sein, sonst prüft man den Umbruch mit.
FLACH = " ".join(ABSCHNITT.split())


def test_every_field_of_the_form_has_its_own_section() -> None:
    """Acht Felder, acht Überschriften — ein neu hinzugefügtes Feld soll nicht
    unbeschrieben bleiben."""
    formular = (
        Path(__file__).resolve().parents[1] / "app" / "templates" / "_entity_config_form.html"
    ).read_text(encoding="utf-8")
    felder = {
        zeile.split("<label>")[1].split(" {{")[0].split("</label>")[0].strip()
        for zeile in formular.splitlines()
        if "<label>" in zeile
    }
    assert felder, "keine Felder im Formular gefunden"
    for feld in felder:
        assert f"### {feld}" in ABSCHNITT, feld


def test_the_outlier_ladder_is_listed_completely() -> None:
    """Als ganze Aufzählung geprüft, nicht Stufe für Stufe: „10" käme sonst
    auch in „100 %" oder „13,6 %" vor, und eine gestrichene Stufe fiele nicht
    auf."""
    stufen = [k for k in OUTLIER_THRESHOLD_LABELS if k != "off"]
    assert f"{', '.join(stufen)} %" in FLACH, stufen


def test_the_retention_ladder_is_listed_completely() -> None:
    beschriftungen = [v for k, v in RETENTION_LABELS.items() if k != "unlimited"]
    assert ", ".join(beschriftungen) in FLACH, beschriftungen


def test_the_gap_ladder_is_complete() -> None:
    """Die Lücken-Stufen stehen als Aufzählung im Text, deshalb einzeln
    geprüft statt über die Labels (die Minuten dort heißen „1 Minute", im Text
    steht die kompakte Aufzählung „1, 5, 15, 30 Minuten")."""
    stufen = [k for k in GAP_THRESHOLD_LABELS if k != "off"]
    assert stufen == ["1", "5", "15", "30", "60", "360", "720", "1440"], (
        "Leiter geändert — die Aufzählung im Handbuch muss nachgezogen werden"
    )
    for teil in ("1, 5, 15, 30 Minuten", "1, 6, 12 Stunden", "1 Tag"):
        assert teil in ABSCHNITT, teil


def test_the_named_constants_match_the_code() -> None:
    """Namenslänge, Lebenszeichen-Abstand und die Fenstergröße der
    Ausreißer-Erkennung stehen als Zahl im Text."""
    assert f"bis {MAX_CUSTOM_NAME_LENGTH} Zeichen" in ABSCHNITT
    assert f"alle {VALUE_FILTER_HEARTBEAT_SECONDS // 3600} Stunden" in ABSCHNITT
    assert OUTLIER_WINDOW == 5 and "letzten fünf Werte" in ABSCHNITT


def test_the_switch_domains_are_named() -> None:
    for domain in SWITCH_DOMAINS:
        assert f"`{domain}`" in ABSCHNITT, domain


def test_the_chapter_does_not_claim_that_dense_values_get_condensed() -> None:
    """Der konkrete Fehler, der hier stand: `should_accept_write()` VERWIRFT
    zu dichte Werte, es entsteht kein Mittelwert. Wer das Kapitel wieder auf
    „verdichtet" umschreibt, ändert eine Zusage über das Verhalten."""
    assert "verworfen, nicht zusammengefasst" in ABSCHNITT
    # "verdichtete Werte" darf vorkommen (als Verweis auf die Aggregation in
    # Charts und Tabellen) — verboten ist die Aussage über die Auflösung selbst.
    assert "werden entsprechend verdichtet" not in ABSCHNITT


def test_the_outlier_rules_are_described_per_type() -> None:
    """Der zweite Fehler: eine einzige Beschreibung für alle Typen, obwohl
    Zähler und übrige Sensoren gegen verschiedene Bezugsgrößen messen."""
    ausreisser = FLACH[FLACH.index("### Ausreißer-Erkennung"):]
    # Der ganze Satz, nicht nur das Stichwort: „vorherigen Zuwachs" allein
    # steht auch in der Einleitung und überlebte eine Umformulierung der Regel.
    assert "Markiert wird, wenn er um mehr als den Schwellwert abweicht." in ausreisser
    assert "**Zuwachs mit dem vorherigen Zuwachs**" in ausreisser
    assert "**Durchschnitt der letzten fünf Werte**" in ausreisser
    assert "Für Schalter ist die Einstellung nicht verfügbar" in ausreisser
