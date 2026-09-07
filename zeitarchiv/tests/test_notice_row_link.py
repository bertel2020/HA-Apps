"""Eine Meldungszeile ohne Ziel ist kein Link.

Neun der dreißig Tipps in `tips.py` setzen bewusst `link: None`: sie erklären
etwas an Ort und Stelle — das Stummschalt-Icon in derselben Zeile, den Klick
auf einen Zeitraum-Titel, das Sortieren per Spaltenkopf. Es gibt dort nichts
zu öffnen.

Das Panel wickelte trotzdem jede Zeile in ein `<a>`. Aus `None` wurde
`href="/None"`, dazu der Chevron, der ein Ziel verspricht — ein Klick landete
auf 404. Der Fehler war nicht zu sehen: die Zeile sah aus wie jede andere.
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from _paths import TEMPLATES
from app.tips import TIPS


def _render(notices: list[dict]) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    vorlage = env.get_template("_notice_panel_body.html")
    return vorlage.render(notices=notices, app_root="", snooze_labels={})


def _meldung(notice_id: str, link: str | None) -> dict:
    return {
        "id": notice_id,
        "title": "Titel",
        "detail": "Detail",
        "meta": "Meta",
        "severity": "info",
        "link": link,
        "mutable": False,
    }


def test_a_notice_without_a_target_is_not_wrapped_in_a_link() -> None:
    html = _render([_meldung("tips.meldungen_stummschalten", None)])
    assert "None" not in html, 'aus link=None darf kein href="/None" werden'
    assert "<a class=" not in html.split('class="notice-list"')[1]
    assert 'class="notice-row-link notice-row-static"' in html


def test_a_notice_without_a_target_shows_no_chevron() -> None:
    """Der Pfeil rechts ist die Zusage, dass ein Klick irgendwo hinführt."""
    assert "notice-chevron" not in _render([_meldung("tips.x", None)])
    assert "notice-chevron" in _render([_meldung("tips.x", "/entities")])


def test_a_notice_with_a_target_stays_a_link() -> None:
    html = _render([_meldung("storage.disk_low", "/housekeeping#speicherplatz")])
    assert '<a class="notice-row-link" href="/housekeeping#speicherplatz">' in html
    assert "notice-row-static" not in html


def test_the_row_keeps_its_layout_class_either_way() -> None:
    """`.notice-row-link` trägt das Zeilen-Layout (flex, Abstände) und wird von
    `.notice-row.is-snoozing` ausgeblendet — beides muss auch für die
    zielfreie Fassung gelten, sonst bräche die Zeile bei der Dauer-Auswahl
    auseinander."""
    for link in (None, "/entities"):
        assert 'class="notice-row-link' in _render([_meldung("tips.x", link)])


def test_the_linkless_tips_are_a_category_not_an_accident() -> None:
    """Wären es null, wäre die Fallunterscheidung toter Code; wäre es einer,
    hätte man ihn eher als Datenfehler gelesen und ein Ziel nachgetragen."""
    ohne_ziel = [t["slug"] for t in TIPS if t["link"] is None]
    assert len(ohne_ziel) >= 5, ohne_ziel
    assert "meldungen_stummschalten" in ohne_ziel
