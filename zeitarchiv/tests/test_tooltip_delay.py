
from _paths import APP





def test_hover_tooltips_use_consistent_600ms_delay() -> None:
    css = (APP / "static/css/app.css").read_text()
    entity_picker = (APP / "static/js/entity-picker.js").read_text()
    map_picker = (APP / "static/js/map-entity-picker.js").read_text()
    import_template = (APP / "templates/import.html").read_text()
    cleanup_template = (APP / "templates/cleanup.html").read_text()

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
    import_template = (APP / "templates/import.html").read_text()

    assert ".entity-tooltip-host:focus-within>.entity-tooltip{visibility:visible;}" in css
    assert ".ha-archive-help:focus .ha-archive-help-popover" in import_template
