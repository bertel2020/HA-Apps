"""Minimaler WebDAV-Client für das externe Backup-Ziel (BACKUP_EXTERN_KONZEPT.md).

Nur, was ein Backup-Ziel braucht: Ordner anlegen, Datei hochladen (gestreamt),
Größe prüfen, umbenennen, auflisten, löschen. Standardbibliothek statt einer
neuen Abhängigkeit (http.client, ssl, xml.etree) — Backups sind mehrere GB groß
und werden nie im Ganzen in den Speicher gelesen.

Alle Aufrufe blockieren bis zum Socket-Timeout; der Aufrufer legt sie hinter
backup_external.run_with_watchdog(). Fehler werden als WebDavError mit einem
Text für die Oberfläche geworfen.
"""

from __future__ import annotations

import base64
import http.client
import re
import secrets
import socket
import ssl
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from ..i18n import tr


def unique_part_name(name: str) -> str:
    """Temporärer Name für genau einen Übertragungsversuch.

    Eindeutig je Versuch, weil der Watchdog (backup_external.run_with_watchdog)
    einen hängenden Versuch nur aufgibt, nicht beendet: kehrt das Netz zurück,
    schreibt der alte Thread weiter, während der Retry schon läuft. Mit einem
    gemeinsamen .part-Namen schrieben beide in dieselbe Datei. Passt weiter auf
    das Aufräummuster ``.zeitarchiv-backup-*.part``."""
    return f".{name}.{secrets.token_hex(4)}.part"

SOCKET_TIMEOUT_SECONDS = 30.0
UPLOAD_CHUNK_BYTES = 1024 * 1024
DAV_NS = "{DAV:}"

PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/><d:resourcetype/></d:prop></d:propfind>'
)


class WebDavError(Exception):
    """Fehler beim WebDAV-Ziel, Text ist für die Oberfläche bestimmt."""


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int | None


def validate_url(url: str) -> str:
    """Normalisierte Adresse (mit abschließendem Schrägstrich) oder ValueError."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(tr("Ungültige Adresse. Erwartet wird eine vollständige Adresse mit http:// oder https://."))
    if parts.username or parts.password:
        raise ValueError(tr("Benutzer und Passwort gehören nicht in die Adresse."))
    if parts.query or parts.fragment:
        raise ValueError(tr("Die Adresse darf keine Parameter enthalten."))
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return parts._replace(path=path).geturl()


class _UploadBody:
    """Datei als Anfragekörper, meldet jeden gelesenen Block (Lebenszeichen und
    Fortschritt). http.client liest ihn bei bekanntem Content-Length blockweise."""

    def __init__(self, path: Path, beat: Callable[[], None], on_progress: Callable[[int, int], None] | None) -> None:
        self._file = path.open("rb")
        self._beat = beat
        self._on_progress = on_progress
        self._size = path.stat().st_size
        self._sent = 0

    def read(self, amount: int = -1) -> bytes:
        block = self._file.read(UPLOAD_CHUNK_BYTES if amount is None or amount < 0 else min(amount, UPLOAD_CHUNK_BYTES))
        self._sent += len(block)
        self._beat()
        if self._on_progress is not None:
            self._on_progress(self._sent, self._size)
        return block

    def close(self) -> None:
        self._file.close()


class WebDavClient:
    def __init__(self, url: str, user: str = "", password: str = "", verify_tls: bool = True) -> None:
        normalized = validate_url(url)
        self._parts = urlsplit(normalized)
        self._base_path = self._parts.path
        self._verify_tls = verify_tls
        self._auth = None
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
            self._auth = f"Basic {token}"
        self.display_url = normalized

    # --- Verbindung -----------------------------------------------------

    def _connection(self) -> http.client.HTTPConnection:
        host = self._parts.hostname
        port = self._parts.port
        if self._parts.scheme == "https":
            context = ssl.create_default_context()
            if not self._verify_tls:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection(host, port, timeout=SOCKET_TIMEOUT_SECONDS, context=context)
        return http.client.HTTPConnection(host, port, timeout=SOCKET_TIMEOUT_SECONDS)

    def _path(self, name: str = "") -> str:
        return self._base_path + quote(name)

    def _request(
        self,
        method: str,
        name: str = "",
        body=None,
        headers: dict[str, str] | None = None,
        expect: tuple[int, ...] = (200, 201, 204, 207),
    ) -> tuple[int, dict[str, str], bytes]:
        all_headers = {"User-Agent": "Zeitarchiv", **(headers or {})}
        if self._auth:
            all_headers["Authorization"] = self._auth
        connection = self._connection()
        try:
            try:
                connection.request(method, self._path(name), body=body, headers=all_headers)
                response = connection.getresponse()
                data = response.read()
            except ssl.SSLCertVerificationError as exc:
                raise WebDavError(
                    tr("Das Zertifikat des Servers wird nicht akzeptiert ({error}). Bei einem selbst signierten Zertifikat die Zertifikatsprüfung abschalten.", error=exc.verify_message)
                ) from exc
            except (socket.timeout, TimeoutError) as exc:
                raise WebDavError(tr("Der WebDAV-Server antwortet nicht (Zeitüberschreitung).")) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise WebDavError(tr("Der WebDAV-Server ist nicht erreichbar: {error}", error=exc)) from exc
        finally:
            connection.close()
        status = response.status
        if status in expect:
            return status, {k.lower(): v for k, v in response.getheaders()}, data
        raise self._status_error(status, response.getheader("Location"))

    @staticmethod
    def _status_error(status: int, location: str | None) -> WebDavError:
        if status == 401:
            return WebDavError(tr("Anmeldung abgelehnt (401). Benutzer und Passwort prüfen."))
        if status == 403:
            return WebDavError(tr("Zugriff verweigert (403). Fehlt dem Benutzer das Schreibrecht auf diesen Ordner?"))
        if status == 404:
            return WebDavError(tr("Ordner nicht gefunden (404). Adresse prüfen."))
        if status in (301, 302, 307, 308):
            return WebDavError(tr("Die Adresse leitet um auf {location}. Bitte die endgültige Adresse eintragen.", location=location or "?"))
        if status == 413:
            return WebDavError(tr("Der Server lehnt die Datei als zu groß ab (413). Die Obergrenze für Uploads am Server anheben."))
        if status == 507:
            return WebDavError(tr("Zu wenig Platz am Ziel."))
        if status in (405, 501):
            return WebDavError(tr("Der Server unterstützt diese WebDAV-Anfrage nicht ({status}). Ist die Adresse eine WebDAV-Adresse?", status=status))
        return WebDavError(tr("Der WebDAV-Server antwortete mit Status {status}.", status=status))

    # --- Operationen ----------------------------------------------------

    def ensure_folder(self) -> None:
        """Ordner der Adresse prüfen; fehlt er, legt MKCOL den letzten
        Pfadbestandteil an (nicht die ganze Kette)."""
        try:
            self._request("PROPFIND", "", body=PROPFIND_BODY, headers={"Depth": "0", "Content-Type": "application/xml"})
            return
        except WebDavError as exc:
            if "404" not in str(exc):
                raise
        self._request("MKCOL", "", expect=(200, 201))

    def upload(
        self,
        source: Path,
        name: str,
        beat: Callable[[], None] = lambda: None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Lädt unter .part-Namen hoch, prüft die Größe und benennt per MOVE
        um — unter dem Endnamen liegt nie eine halbe Datei."""
        size = source.stat().st_size
        part = unique_part_name(name)
        body = _UploadBody(source, beat, on_progress)
        try:
            self._request(
                "PUT", part, body=body,
                headers={"Content-Length": str(size), "Content-Type": "application/zip"},
                expect=(200, 201, 204),
            )
        finally:
            body.close()
        remote = self.size(part)
        if remote != size:
            self.delete(part, missing_ok=True)
            raise WebDavError(tr("Die Datei wurde unvollständig übertragen."))
        destination = self._parts.scheme + "://" + self._parts.netloc + self._path(name)
        self._request("MOVE", part, headers={"Destination": destination, "Overwrite": "T"}, expect=(201, 204))
        return size

    def size(self, name: str) -> int | None:
        _status, _headers, data = self._request(
            "PROPFIND", name, body=PROPFIND_BODY, headers={"Depth": "0", "Content-Type": "application/xml"},
        )
        for entry in _parse_multistatus(data):
            return entry.size
        return None

    def list_files(self) -> list[RemoteFile]:
        _status, _headers, data = self._request(
            "PROPFIND", "", body=PROPFIND_BODY, headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        return [entry for entry in _parse_multistatus(data) if entry.name]

    def delete(self, name: str, missing_ok: bool = False) -> None:
        try:
            self._request("DELETE", name, expect=(200, 204))
        except WebDavError as exc:
            if not (missing_ok and "404" in str(exc)):
                raise

    def check(self, beat: Callable[[], None] = lambda: None) -> dict:
        """Testdatei hochladen, Größe prüfen, löschen."""
        self.ensure_folder()
        beat()
        name = ".zeitarchiv-test-webdav"
        payload = b"zeitarchiv"
        self._request("PUT", name, body=payload, headers={"Content-Length": str(len(payload))}, expect=(200, 201, 204))
        try:
            if self.size(name) != len(payload):
                raise WebDavError(tr("Die Testdatei kam verändert zurück."))
        finally:
            self.delete(name, missing_ok=True)
        return {"free_bytes": None}


def _parse_multistatus(data: bytes) -> list[RemoteFile]:
    """Dateien (keine Ordner) aus einer 207-Antwort; name ist der letzte
    Pfadbestandteil, leer für den Ordner selbst."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise WebDavError(tr("Der WebDAV-Server lieferte eine unverständliche Antwort.")) from exc
    entries: list[RemoteFile] = []
    for response in root.iter(f"{DAV_NS}response"):
        href = response.findtext(f"{DAV_NS}href") or ""
        is_collection = response.find(f".//{DAV_NS}collection") is not None
        length = response.findtext(f".//{DAV_NS}getcontentlength")
        name = "" if is_collection else unquote(re.sub(r"^.*/", "", href.rstrip("/")))
        entries.append(RemoteFile(name, int(length) if length and length.isdigit() else None))
    return entries
