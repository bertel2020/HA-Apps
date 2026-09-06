"""Vertragstests für die Sicherheitsgrenze in nginx.conf.

Das Sicherheitsmodell der App liegt fast vollständig in dieser einen
Konfigurationsdatei: Uvicorn lauscht nur auf 127.0.0.1, der Ingress-Port ist
auf den Supervisor beschränkt, und der nach außen veröffentlichte Port 8127
lässt genau drei Endpunkte durch. Die Anwendung selbst hat dahinter keine
zweite Verteidigungslinie — weder die Oberfläche noch die Query-Endpunkte
prüfen ein Token, und auf einer der Seiten steht der API-Token im Klartext.

Ein `location /api/` statt `location =`, ein Präfix-Match zu viel, ein
verlorenes `deny all`: die Wirkung wäre erheblich, und bis hierher hätte es
nichts gemeldet. Diese Datei ist deshalb bewusst ein reiner Textvertrag ohne
neue Abhängigkeit — sie prüft nicht, ob nginx die Datei mag, sondern ob die
drei Zusagen noch drinstehen.
"""

from __future__ import annotations

import re

from _paths import ADDON


NGINX_CONF = ADDON / "nginx.conf"

#: Was Port 8127 nach außen anbieten darf. Bewusst hier und nicht aus der Datei
#: abgeleitet — sonst würde der Test jede Erweiterung mitmachen, statt sie zu
#: melden. Wer hier etwas hinzufügt, trifft eine Sicherheitsentscheidung.
PUBLIC_ENDPOINTS = {"/api/health", "/api/write", "/api/notices"}

#: Der Ingress-Proxy von Home Assistant, dazu der eigene Container.
INGRESS_CLIENTS = {"172.30.32.2", "127.0.0.1"}


def _server_blocks(source: str) -> dict[str, str]:
    """`server { … }`-Blöcke nach ihrem `listen`-Port. Klammern werden gezählt
    statt regulär gematcht, weil `location`-Blöcke verschachtelt sind."""
    blocks: dict[str, str] = {}
    for match in re.finditer(r"^server\s*\{", source, flags=re.M):
        depth, index = 0, match.end() - 1
        while index < len(source):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        body = source[match.end():index]
        port = re.search(r"listen\s+(\d+)", body)
        assert port, "server-Block ohne listen-Direktive"
        blocks[port.group(1)] = body
    return blocks


def _conf() -> dict[str, str]:
    blocks = _server_blocks(NGINX_CONF.read_text(encoding="utf-8"))
    assert set(blocks) == {"8099", "8127"}, f"unerwartete Ports: {sorted(blocks)}"
    return blocks


def test_the_public_port_only_exposes_the_three_integration_endpoints() -> None:
    """Port 8127 ist der einzige nach außen veröffentlichte Port (config.yaml)
    und ausschließlich für die Zeitarchiv-Integration bestimmt. Alles, was hier
    zusätzlich durchgelassen würde, stünde ohne Authentifizierung im Netz."""
    public = _conf()["8127"]
    exact = set(re.findall(r"location\s*=\s*(\S+)\s*\{", public))
    assert exact == PUBLIC_ENDPOINTS, f"veröffentlicht: {sorted(exact)}"

    # Jedes location ohne "=" wäre ein Präfix-Match und damit eine offene Tür.
    # Erlaubt ist genau eines: der Auffang-Block darunter.
    prefixes = re.findall(r"location\s+(?!=)(\S+)\s*\{", public)
    assert prefixes == ["/"], f"Präfix-Matches auf dem öffentlichen Port: {prefixes}"


def test_the_public_port_answers_everything_else_with_404() -> None:
    """Der Auffang-Block darf nicht weiterreichen, sondern muss abweisen — und
    zwar mit 404 statt 403, damit von außen nicht erkennbar ist, welche Routen
    es überhaupt gibt."""
    public = _conf()["8127"]
    fallback = re.search(r"location\s+/\s*\{([^}]*)\}", public)
    assert fallback, "Auffang-Block fehlt"
    assert "return 404" in fallback.group(1)
    assert "proxy_pass" not in fallback.group(1)


def test_the_ingress_port_is_restricted_to_the_supervisor() -> None:
    """Port 8099 trägt die gesamte Oberfläche samt destruktiver Routen
    (/settings/purge, /backup/delete-all, /entities/{id}/delete). Er ist nur
    deshalb ungeschützt, weil ausschließlich der authentifizierende
    Supervisor-Ingress ihn erreicht."""
    ingress = _conf()["8099"]
    assert set(re.findall(r"allow\s+([\d.]+);", ingress)) == INGRESS_CLIENTS
    assert re.search(r"deny\s+all;", ingress), "deny all fehlt"

    # Reihenfolge zählt bei nginx: ein deny all VOR den allow-Regeln würde
    # alles abweisen, danach ist es der richtige Abschluss.
    assert ingress.index("deny all") > max(ingress.index(f"allow {c}") for c in INGRESS_CLIENTS)


def test_nothing_is_proxied_anywhere_but_the_loopback_app() -> None:
    """Die App lauscht nur auf 127.0.0.1:8128 (run.sh). Ein proxy_pass auf eine
    andere Adresse würde entweder ins Leere zeigen oder die Grenze umgehen."""
    source = NGINX_CONF.read_text(encoding="utf-8")
    targets = set(re.findall(r"proxy_pass\s+(\S+);", source))
    assert targets == {"http://127.0.0.1:8128"}, f"Proxy-Ziele: {sorted(targets)}"

    run_sh = (ADDON / "run.sh").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in run_sh, "uvicorn lauscht nicht mehr nur auf dem Loopback"


def test_only_the_public_port_is_declared_in_the_addon_config() -> None:
    """Gegenprobe von der anderen Seite: würde 8099 zusätzlich in config.yaml
    als Port auftauchen, wäre die Oberfläche trotz aller nginx-Regeln von außen
    erreichbar, sobald der Nutzer den Port freigibt."""
    config = (ADDON / "config.yaml").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s+(\d+)/tcp:", config, flags=re.M))
    assert declared == {"8127"}, f"in config.yaml veröffentlicht: {sorted(declared)}"
