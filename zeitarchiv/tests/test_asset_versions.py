"""ZG-05: Der Cache-Buster folgt dem Inhalt, nicht dem Zeitstempel.

Bis 0.84.0 gab es drei Zahlen — `css_v`, `js_v`, `vendor_v` —, jede die jüngste
mtime über alle Dateien eines Ordners. Das hatte zwei Fehler, und beide kosteten
den Nutzer Bandbreite:

* Git speichert keine mtimes. Ein frischer CI-Checkout setzt alle Dateien auf
  die Checkout-Zeit, `COPY` im Dockerfile übernimmt sie. `vendor_v` änderte sich
  damit bei **jedem** Release — jedes Add-on-Update ließ jeden Nutzer 1,0 MB
  ECharts neu laden, obwohl die Datei seit Monaten unverändert war.
* Es war **eine** Zahl über **alle** Dateien eines Ordners. Eine Änderung an
  einer der 30 JS-Dateien entwertete den Zwischenspeicher aller dreißig.

Beides ist an einer Stelle nicht prüfbar, an der man es vermuten würde: Im
Browser sieht ein neu geladenes Asset genauso aus wie ein aus dem Cache
bedientes. Der Fehler ist unsichtbar und kostet trotzdem bei jedem Update.
Deshalb steht er hier als Test.
"""

from __future__ import annotations

import os
import re

from app.main import _AssetVersions, asset, templates
from _paths import APP, STATIC, TEMPLATES

#: Wie ein Template ein Asset adressiert: {{ asset('js/pages/statistik.js') }}
AUFRUF = re.compile(r"\{\{ asset\('(?P<pfad>[^']+)'\) \}\}")


def _aufrufe() -> dict[str, set[str]]:
    return {
        path.name: set(AUFRUF.findall(path.read_text(encoding="utf-8")))
        for path in TEMPLATES.glob("*.html")
    }


def test_every_referenced_asset_exists() -> None:
    """asset() kann zur Laufzeit nicht prüfen, ob es die Datei gibt — es liefert
    dann still eine 404-URL aus, und die Seite lädt einfach ohne ihr Skript.
    Genau deshalb gehört die Prüfung hierher und nicht in main.py."""
    gesamt = 0
    for name, pfade in _aufrufe().items():
        for pfad in pfade:
            gesamt += 1
            assert (STATIC / pfad).is_file(), f"{name} verweist auf fehlendes static/{pfad}"
    assert gesamt >= 100, f"nur {gesamt} Asset-Verweise gefunden — Muster prüfen"


def test_no_template_still_uses_the_old_folder_wide_busters() -> None:
    """Die drei Globals sind entfernt. Käme eine Zeile im alten Stil zurück,
    liefe sie nicht auf einen Fehler, sondern auf ein leeres ?v= — der lange
    immutable-Cache griffe dann auf eine Datei, die sich noch ändert."""
    for name, quelle in {p.name: p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html")}.items():
        for alt in ("{{ css_v }}", "{{ js_v }}", "{{ vendor_v }}"):
            assert alt not in quelle, f"{name} verwendet noch {alt}"
        assert "{{ app_root }}/static/" not in quelle, f"{name} baut die Asset-URL selbst"


def test_the_helper_is_wired_into_the_template_environment() -> None:
    """Gegenprobe zu den Tests darunter: die prüfen die Klasse, nicht den
    Einbau. Ohne diese Zusicherung ließe sich asset() aus den Globals
    entfernen, und jede Asset-URL wäre ab da ein Jinja-Fehler."""
    assert templates.env.globals.get("asset") is asset


def test_vendor_scripts_go_through_the_helper_too() -> None:
    """htmx/echarts/alpine trugen ursprünglich gar keinen Cache-Buster — die
    einzige Lücke, die das lange Cache-Control von _CachedStaticFiles unsicher
    gemacht hätte. Sie sind zugleich die Dateien, die am meisten kosten, wenn
    ein Update sie grundlos neu lädt (ECharts allein 1,0 MB)."""
    gefunden = sum(
        1 for pfade in _aufrufe().values() for pfad in pfade if pfad.startswith("vendor/")
    )
    assert gefunden >= 18, f"nur {gefunden} Vendor-Verweise über asset()"

    # Gegenprobe: keine zweite, an asset() vorbeigeführte Schreibweise. Ohne
    # sie bliebe die Zahl oben erfüllt, während daneben eine nackte
    # vendor/-URL steht.
    for path in TEMPLATES.glob("*.html"):
        for wert in re.findall(r'src="([^"]*)"', path.read_text(encoding="utf-8")):
            if "vendor/" in wert:
                assert "asset(" in wert, f"{path.name}: {wert!r} umgeht asset()"


def test_the_version_follows_the_content_and_not_the_timestamp(tmp_path) -> None:
    """Der Kern von ZG-05, in der Form, in der er im Betrieb auftritt: Nach
    einem Image-Build hat dieselbe Datei eine neue mtime. Vorher war das eine
    neue Version, jetzt nicht mehr."""
    datei = tmp_path / "app.css"
    datei.write_text("a{}", encoding="utf-8")
    versionen = _AssetVersions(tmp_path)
    vorher = versionen("app.css")

    # Der Rebuild: gleicher Inhalt, neue mtime.
    spaeter = os.stat(datei).st_mtime + 3600
    os.utime(datei, (spaeter, spaeter))
    assert versionen("app.css") == vorher, "neue mtime hat die Version geändert"

    # Und aus Sicht eines frisch gestarteten Containers, der nichts
    # zwischengespeichert hat — sonst könnte der Test allein am Cache hängen.
    assert _AssetVersions(tmp_path)("app.css") == vorher


def test_the_version_changes_when_the_content_changes(tmp_path) -> None:
    """Die Gegenrichtung. Ein Cache-Buster, der sich nie ändert, ist schlimmer
    als keiner: Er sichert ein immutable-Cache-Control über ein Jahr ab, das
    dann auf eine veraltete Datei zeigt."""
    datei = tmp_path / "app.css"
    datei.write_text("a{}", encoding="utf-8")
    versionen = _AssetVersions(tmp_path)
    vorher = versionen("app.css")

    datei.write_text("a{color:red}", encoding="utf-8")
    assert versionen("app.css") != vorher


def test_each_file_carries_its_own_version(tmp_path) -> None:
    """Die zweite Hälfte des Befunds: Vorher galt eine Zahl für den ganzen
    Ordner, eine Änderung entwertete alle 30 JS-Dateien."""
    eins = tmp_path / "eins.js"
    zwei = tmp_path / "zwei.js"
    eins.write_text("1", encoding="utf-8")
    zwei.write_text("2", encoding="utf-8")
    versionen = _AssetVersions(tmp_path)
    unberuehrt = versionen("zwei.js")

    eins.write_text("111", encoding="utf-8")
    assert versionen("eins.js") != "0"
    assert versionen("zwei.js") == unberuehrt, "fremde Datei hat ihre Version verloren"


def test_a_missing_file_does_not_break_the_page(tmp_path) -> None:
    """Ein fehlender Pfad ist ein Fehler — aber keiner, der eine Seite mit 500
    beantworten darf. Gemeldet wird er von
    test_every_referenced_asset_exists, nicht vom Nutzer."""
    datei = tmp_path / "app.css"
    datei.write_text("a{}", encoding="utf-8")
    versionen = _AssetVersions(tmp_path)
    bekannt = versionen("app.css")

    datei.unlink()
    assert versionen("app.css") == bekannt, "letzter bekannter Wert nicht gehalten"
    assert versionen("gibtesnicht.css") == "0"


def test_the_rendered_url_carries_prefix_and_version(client) -> None:
    """Was am Ende beim Browser ankommt. Die Auflösung unter dem
    Ingress-Präfix prüft test_ingress_prefix.py über alle URL-Tiefen; hier geht
    es um die Form: Präfix davor, achtstelliger Hash dahinter."""
    resp = client.get("/entities", headers={"X-Ingress-Path": "/api/hassio_ingress/aBc123"})
    assert resp.status_code == 200
    treffer = re.findall(r'"(/api/hassio_ingress/aBc123/static/[^"]+)"', resp.text)
    assert treffer, "keine Asset-URL mit Ingress-Präfix gerendert"
    for url in treffer:
        assert re.search(r"\?v=[0-9a-f]{8}$", url), f"{url} ohne achtstelligen Inhalts-Hash"


def test_the_hash_is_the_content_hash_of_the_real_file() -> None:
    """Keine Zufallszahl und kein Zähler: derselbe Inhalt ergibt überall
    denselben Wert — auch auf dem Rechner des nächsten Entwicklers und im
    nächsten Container."""
    import hashlib

    from app.main import asset_versions

    roh = (APP / "static" / "css" / "app.css").read_bytes()
    erwartet = hashlib.blake2b(roh, digest_size=4).hexdigest()
    assert asset_versions("css/app.css") == erwartet
