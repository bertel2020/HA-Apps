"""WebDAV als externes Backup-Ziel (app/storage/webdav.py, backup_external.py).

Gegen einen kleinen Fake-Server (http.server im Thread), der PROPFIND, PUT,
MOVE, DELETE und MKCOL kann und sich per Schalter wie ein abweisender Server
verhält."""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import pytest

try:
    from app.background import BackgroundDependencies, BackgroundService
    from app.storage import backup_external, webdav
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="pyarrow nicht installiert")

TZ = ZoneInfo("Europe/Berlin")
NEXTCLOUD_INVALID_PATH = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
    b"<s:exception>OCA\\DAV\\Connector\\Sabre\\Exception\\InvalidPath</s:exception>"
    b'<s:message>".part" is a forbidden file type.</s:message></d:error>'
)
BASE = "/dav/backups/"


class _State:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.folder = True
        self.credentials = ("roberto", "geheim")
        self.max_size: int | None = None
        self.redirect_to: str | None = None
        self.put_error: tuple[int, bytes] | None = None
        # wie Nextcloud ab 31: Dateinamen auf .part sind verboten (InvalidPath → 400)
        self.forbid_part_names = True
        self.requests: list[str] = []


def _make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _name(self, path: str | None = None) -> str | None:
            path = unquote(urlsplit(path or self.path).path)
            if path == BASE.rstrip("/") or path == BASE:
                return ""
            return path[len(BASE):] if path.startswith(BASE) else None

        def _reply(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if state.credentials is None:
                return True
            expected = "Basic " + base64.b64encode(":".join(state.credentials).encode()).decode()
            return self.headers.get("Authorization") == expected

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length else b""

        def _dispatch(self, method: str) -> None:
            state.requests.append(f"{method} {unquote(self.path)}")
            body = self._body() if method in ("PUT", "PROPFIND") else b""
            if state.redirect_to:
                return self._reply(301, headers={"Location": state.redirect_to})
            if not self._authorized():
                return self._reply(401, headers={"WWW-Authenticate": 'Basic realm="x"'})
            name = self._name()
            if name is None:
                return self._reply(404)
            if method == "MKCOL":
                state.folder = True
                return self._reply(201)
            if method == "PROPFIND":
                return self._propfind(name)
            if not state.folder:
                return self._reply(404)
            if method == "PUT":
                if state.forbid_part_names and name.endswith(".part"):
                    return self._reply(400, NEXTCLOUD_INVALID_PATH, {"Content-Type": "application/xml"})
                if state.put_error is not None:
                    return self._reply(state.put_error[0], state.put_error[1], {"Content-Type": "application/xml"})
                if state.max_size is not None and len(body) > state.max_size:
                    return self._reply(413)
                state.files[name] = body
                return self._reply(201)
            if method == "MOVE":
                target = self._name(self.headers["Destination"])
                if name not in state.files or target is None:
                    return self._reply(404)
                state.files[target] = state.files.pop(name)
                return self._reply(201)
            if method == "DELETE":
                if name not in state.files:
                    return self._reply(404)
                del state.files[name]
                return self._reply(204)
            return self._reply(405)

        def _propfind(self, name: str) -> None:
            if not state.folder:
                return self._reply(404)
            depth = self.headers.get("Depth", "0")
            parts = []
            if name == "":
                parts.append(f'<d:response><d:href>{BASE}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>')
                if depth == "1":
                    for fname, data in state.files.items():
                        parts.append(
                            f'<d:response><d:href>{BASE}{fname}</d:href><d:propstat><d:prop><d:getcontentlength>{len(data)}</d:getcontentlength><d:resourcetype/></d:prop></d:propstat></d:response>'
                        )
            elif name in state.files:
                parts.append(
                    f'<d:response><d:href>{BASE}{name}</d:href><d:propstat><d:prop><d:getcontentlength>{len(state.files[name])}</d:getcontentlength><d:resourcetype/></d:prop></d:propstat></d:response>'
                )
            else:
                return self._reply(404)
            xml = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>"
            self._reply(207, xml.encode(), {"Content-Type": "application/xml"})

        do_PUT = lambda self: self._dispatch("PUT")  # noqa: E731
        do_MOVE = lambda self: self._dispatch("MOVE")  # noqa: E731
        do_DELETE = lambda self: self._dispatch("DELETE")  # noqa: E731
        do_MKCOL = lambda self: self._dispatch("MKCOL")  # noqa: E731
        do_PROPFIND = lambda self: self._dispatch("PROPFIND")  # noqa: E731

    return Handler


@pytest.fixture()
def server():
    state = _State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{httpd.server_address[1]}{BASE}"
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


def _client(server, password="geheim", user="roberto") -> webdav.WebDavClient:
    return webdav.WebDavClient(server.url, user, password)


@pytest.mark.parametrize("url", ["", "ftp://x/y/", "cloud.example.de/dav", "https://u:p@x/y/", "https://x/y?a=1"])
def test_invalid_addresses_are_rejected(url) -> None:
    with pytest.raises(ValueError):
        webdav.validate_url(url)


def test_address_gets_a_trailing_slash() -> None:
    assert webdav.validate_url("https://cloud.example.de/remote.php/dav/files/x/Zeitarchiv") == (
        "https://cloud.example.de/remote.php/dav/files/x/Zeitarchiv/"
    )


def test_check_writes_reads_and_deletes_a_test_file(server) -> None:
    _client(server).check()
    assert server.files == {}
    assert any(r.startswith("PUT") for r in server.requests)


def test_a_missing_folder_is_created(server) -> None:
    server.folder = False
    _client(server).check()
    assert server.folder is True


def test_a_wrong_password_names_the_login(server) -> None:
    with pytest.raises(webdav.WebDavError, match="401"):
        _client(server, password="falsch").check()


def test_a_redirect_is_reported_with_the_target(server) -> None:
    server.redirect_to = "https://cloud.example.de/dav/"
    with pytest.raises(webdav.WebDavError, match="cloud.example.de"):
        _client(server).check()


def test_a_dead_server_is_reported_not_raised_raw() -> None:
    client = webdav.WebDavClient("http://127.0.0.1:9/dav/", "u", "p")
    with pytest.raises(webdav.WebDavError, match="nicht erreichbar"):
        client.check()


def test_upload_lands_under_the_final_name_without_a_part_file(server, tmp_path) -> None:
    source = tmp_path / "zeitarchiv-backup-2026-10-11-030000.zip"
    source.write_bytes(b"z" * 500_000)
    seen: list[int] = []
    size = _client(server).upload(source, source.name, on_progress=lambda done, total: seen.append(done))
    assert size == 500_000
    assert server.files == {source.name: source.read_bytes()}
    assert seen and seen[-1] == 500_000


def test_the_temporary_upload_name_is_accepted_by_nextcloud(server, tmp_path) -> None:
    """Nextcloud lehnt PUT auf *.part mit 400 ab (HA-Apps#23) — der Zwischenname
    endet deshalb wie der Endname."""
    source = tmp_path / "zeitarchiv-backup-2026-10-19-030000.zip"
    source.write_bytes(b"z" * 10)
    _client(server).upload(source, source.name)
    puts = [r for r in server.requests if r.startswith("PUT")]
    assert len(puts) == 1 and puts[0].endswith(".zip") and webdav.UPLOAD_PREFIX in puts[0]
    assert server.files == {source.name: b"z" * 10}


def test_a_too_large_file_is_explained(server, tmp_path) -> None:
    server.max_size = 100
    source = tmp_path / "zeitarchiv-backup-x.zip"
    source.write_bytes(b"z" * 1000)
    with pytest.raises(webdav.WebDavError, match="413"):
        _client(server).upload(source, source.name)
    assert server.files == {}


SABRE_BAD_REQUEST = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
    b"<s:exception>Sabre\\DAV\\Exception\\BadRequest</s:exception>"
    b"<s:message>Expected filesize of 1000 bytes but read (from Nextcloud client)\n"
    b"  and wrote (to Nextcloud storage) 400 bytes.</s:message></d:error>"
)


def test_a_failed_upload_names_the_step_and_the_server_message(server, tmp_path) -> None:
    server.put_error = (400, SABRE_BAD_REQUEST)
    source = tmp_path / "zeitarchiv-backup-x.zip"
    source.write_bytes(b"z" * 1000)
    with pytest.raises(webdav.WebDavError) as caught:
        _client(server).upload(source, source.name)
    assert str(caught.value) == (
        "Hochladen fehlgeschlagen: Der WebDAV-Server antwortete mit Status 400. "
        "Meldung des Servers: Expected filesize of 1000 bytes but read (from Nextcloud client) "
        "and wrote (to Nextcloud storage) 400 bytes."
    )


def test_an_error_page_that_is_not_sabre_xml_adds_no_server_message(server, tmp_path) -> None:
    server.put_error = (400, b"<html><body>Bad Request</body></html>")
    source = tmp_path / "zeitarchiv-backup-x.zip"
    source.write_bytes(b"z" * 10)
    with pytest.raises(webdav.WebDavError) as caught:
        _client(server).upload(source, source.name)
    assert str(caught.value) == "Hochladen fehlgeschlagen: Der WebDAV-Server antwortete mit Status 400."


def test_transfer_clears_leftover_part_files_of_this_app(server, tmp_path) -> None:
    server.files[".zeitarchiv-backup-alt.zip.part"] = b"x"
    server.files[".zeitarchiv-upload-0badc0de-zeitarchiv-backup-alt.zip"] = b"x"
    server.files["fremd.txt"] = b"x"
    source = tmp_path / "zeitarchiv-backup-2026-10-11-030000.zip"
    source.write_bytes(b"z" * 10)
    backup_external.transfer_webdav(_client(server), source)
    assert sorted(server.files) == ["fremd.txt", source.name]


def test_prune_webdav_uses_the_name_timestamp_and_spares_foreign_files(server) -> None:
    for day in (7, 8, 9, 10):
        server.files[f"zeitarchiv-backup-2026-10-{day:02d}-030000.zip"] = b"x"
    server.files["urlaub.zip"] = b"x"
    server.files["zeitarchiv-backup-kaputt.zip"] = b"x"
    now = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
    removed = backup_external.prune_webdav(_client(server), 2, None, now, TZ)
    assert removed == 2
    assert sorted(server.files) == [
        "urlaub.zip", "zeitarchiv-backup-2026-10-09-030000.zip",
        "zeitarchiv-backup-2026-10-10-030000.zip", "zeitarchiv-backup-kaputt.zip",
    ]
    removed = backup_external.prune_webdav(_client(server), None, 2, now, TZ)  # nach Alter in Tagen
    assert removed == 1  # der 9. ist 2,4 Tage alt, der 10. nur 1,4
    assert "urlaub.zip" in server.files and "zeitarchiv-backup-2026-10-10-030000.zip" in server.files


def test_the_password_comes_from_the_options_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ZEITARCHIV_WEBDAV_PASSWORD", raising=False)
    assert backup_external.load_webdav_password(tmp_path) == ""
    (tmp_path / "options.json").write_text(json.dumps({"webdav_password": "geheim"}), encoding="utf-8")
    assert backup_external.load_webdav_password(tmp_path) == "geheim"


def test_the_options_file_is_not_part_of_a_backup() -> None:
    from app.storage import backup

    assert "options.json" not in backup.BACKUP_ENTRIES


@pytest.fixture()
def dienst(tmp_path):
    index = Index(tmp_path / "index.sqlite")
    service = BackgroundService(BackgroundDependencies(
        data_dir=tmp_path, tz=TZ, index=index, coordinator=StorageCoordinator(),
        base_dir=tmp_path, demo_mode_active=False,
        backups_dir=tmp_path / "backups", symcon_import_dir=tmp_path / "symcon",
        csv_import_dir=tmp_path / "csv", backup_default_time="03:00", backup_default_weekday=6,
        retention_default_time="04:00", retention_default_weekday=6,
        count_stale_entities=lambda: 0,
    ))
    try:
        yield service
    finally:
        index.close()


def _configure(dienst, server, tmp_path, password: str | None = "geheim") -> None:
    index = dienst.index
    index.set_setting("backup_external_type", "webdav")
    index.set_setting("backup_external_webdav_url", server.url)
    index.set_setting("backup_external_webdav_user", "roberto")
    if password is not None:
        (tmp_path / "options.json").write_text(json.dumps({"webdav_password": password}), encoding="utf-8")


def _run_backup(dienst) -> None:
    assert dienst.run_backup()
    deadline = time.time() + 20
    while dienst.backup_progress.running and time.time() < deadline:
        time.sleep(0.05)
    assert not dienst.backup_progress.running


def test_a_backup_is_uploaded_to_the_webdav_target(dienst, server, tmp_path) -> None:
    _configure(dienst, server, tmp_path)
    _run_backup(dienst)
    (job,) = dienst.index.list_backup_jobs(1)
    assert job["status"] == "success" and job["external_status"] == "ok"
    assert job["external_target"] == server.url
    assert list(server.files) == [job["filename"]]
    assert len(server.files[job["filename"]]) == job["size_bytes"]


def test_a_missing_password_fails_the_transfer_with_an_instruction(dienst, server, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ZEITARCHIV_WEBDAV_PASSWORD", raising=False)
    _configure(dienst, server, tmp_path, password=None)
    _run_backup(dienst)
    (job,) = dienst.index.list_backup_jobs(1)
    assert job["status"] == "success" and job["external_status"] == "failed"
    assert "Add-on-Optionen" in job["external_error"]
    assert server.files == {}
    assert (dienst.backups_dir / job["filename"]).is_file()


def test_the_password_is_never_written_to_the_index(dienst, server, tmp_path) -> None:
    _configure(dienst, server, tmp_path)
    _run_backup(dienst)
    rows = dienst.index._conn.execute("SELECT key, value FROM settings").fetchall()
    assert not any("geheim" in str(row["value"]) for row in rows)
