"""Der Hilfe-Kasten am „i" der HA-Importoptionen muss aufs Telefon passen.

Er hing mit right:0 am Badge, und das Badge sitzt am rechten Ende seiner Zeile:
bei 375px Fenster x=185…203, der Kasten 311px breit (min(320px, 100vw − 64px))
— linke Kante also bei −109px. Da <html> overflow-x:hidden trägt, war rund ein
Drittel des Textes weder sichtbar noch erreichbar.
"""

from __future__ import annotations

from _paths import APP


def test_the_help_popover_is_anchored_to_the_block_not_the_badge_on_phones() -> None:
    css = (APP / "static/css/pages/import.css").read_text(encoding="utf-8")
    mobil = css[css.index("@media (max-width:600px)"):]

    # left:0 UND right:0 am Block: der Kasten ist damit per Konstruktion genau
    # so breit wie der Block und kann nicht überstehen — egal, wo das Badge in
    # seiner Zeile steht. Eine feste Breite am Badge war genau der Fehler.
    assert ".ha-import-config-block{position:relative;}" in mobil
    assert "left:0;right:0;width:auto;max-width:none;" in mobil

    # Ohne das bliebe das Badge der nächste positionierte Vorfahr und damit
    # weiter der Bezugspunkt — die Blockverankerung liefe ins Leere.
    assert ".ha-archive-help{position:static;}" in mobil

    # Am Schreibtisch bleibt es, wie es war: rechts neben dem Badge.
    grundregel = css[css.index(".ha-archive-help-popover{"):]
    grundregel = grundregel[: grundregel.index("}")]
    assert "left:calc(100% + 8px)" in grundregel
