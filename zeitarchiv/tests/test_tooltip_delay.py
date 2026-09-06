
from _paths import APP, page_text





def test_hover_tooltips_use_consistent_600ms_delay() -> None:
    css = (APP / "static/css/app.css").read_text()
    entity_picker = (APP / "static/js/entity-picker.js").read_text()
    map_picker = (APP / "static/js/map-entity-picker.js").read_text()
    import_template = page_text("import.html")
    cleanup_template = page_text("cleanup.html")

    assert "[data-tooltip]:hover::after{opacity:1;visibility:visible;transition-delay:.6s;}" in css
    assert ".entity-tooltip-host:hover>.entity-tooltip{visibility:visible;transition-delay:.6s;}" in css
    assert "}, 600);" in entity_picker
    assert "}, 600);" in map_picker
    assert ".ha-archive-help:hover .ha-archive-help-popover" in import_template
    assert "transition-delay:.6s" in import_template
    assert "tipTimer = setTimeout" in cleanup_template
    assert "}, 600);" in cleanup_template


def test_keyboard_focused_tooltips_remain_immediate() -> None:
    css = (APP / "static/css/app.css").read_text()
    import_template = page_text("import.html")

    assert ".entity-tooltip-host:focus-within>.entity-tooltip{visibility:visible;}" in css
    assert ".ha-archive-help:focus .ha-archive-help-popover" in import_template
def test_the_chart_entity_picker_tooltip_escapes_its_scrolling_list() -> None:
    """Name und ID stehen in der Entitätenauswahl auf je einer gekürzten Zeile,
    der Tooltip zeigt beides. Die Liste scrollt aber (max-height + overflow-y),
    und ein im Zeilenrahmen positionierter Tooltip wird davon beschnitten — in
    der OBERSTEN Zeile war er dadurch gar nicht zu sehen, weil er komplett über
    den oberen Containerrand ragt. Deshalb data-tooltip-fixed: FixedTooltip
    hängt sein Element an document.body und klappt nach unten, wenn oben kein
    Platz ist.

    Nachgemessen bei 1280px: Zeile 1 zeigt den Tooltip bei y=333 über dem
    Container (y=385), vollständig im Viewport.
    """
    editor = page_text("chart_editor.html")
    css = (APP / "static/css/app.css").read_text(encoding="utf-8")

    assert "fixed-tooltip.js" in editor, "ohne das Skript passiert gar nichts"
    assert 'data-tooltip-fixed="{{ opt.label }}&#10;{{ opt.entity_id }}"' in editor
    # Der im Zeilenrahmen positionierte Aufbau würde in der obersten Zeile
    # wieder abgeschnitten.
    assert "entity-tooltip-host" not in editor

    # Und er sieht nicht nur ähnlich aus, sondern ist derselbe: bei einem
    # Zeilenumbruch im Wert baut fixed-tooltip.js die Entitäts-Variante mit
    # Klasse und Aufbau aus der Entitätenliste. Nachgemessen sind beide gleich
    # in Hintergrund, Innenabstand, Radius und beiden Schriften.
    skript = (APP / "static/js/fixed-tooltip.js").read_text(encoding="utf-8")
    assert "'entity-tooltip entity-tooltip-floating'" in skript
    assert "createElement('strong')" in skript and "createElement('code')" in skript
    # .entity-tooltip-floating hängt an document.body, hat also keinen
    # .entity-tooltip-host-Vorfahren — ohne Inline-display bliebe sie auf dem
    # Telefon von der Mobilregel ausgeblendet.
    assert "el.style.display = 'block';" in skript
    assert ".entity-tooltip.entity-tooltip-floating{position:fixed" in css
    # Die Zeile ist ein Kästchen zum Anklicken, kein Hilfetext.
    assert ".entity-picker-row[data-tooltip-fixed]{cursor:pointer;}" in editor
