"""The configured Gemini extension download: admin only, with this server's address and the sync key.

Also checks that the embedded copy (app/extension_files.py) matches the files in extension/.
"""

from __future__ import annotations

import base64
import io
import json
import pathlib
import zipfile

from fastapi.testclient import TestClient

from app.config import Settings
from app.extension_files import FILES
from app.main import create_app
from tests.test_chat import ADMIN_PASS, ADMIN_USER, Backend, FakeClient, MemoryStore, login

APP_KEY = "k3y-for-scripts-0123456789abcdef"
SYNC_KEY = "sync-key-for-the-extension-0123456789"
EXT = pathlib.Path(__file__).resolve().parent.parent / "extension"


def build(tmp_path, sync_key=SYNC_KEY):
    settings = Settings(
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        secret_key="s" * 48,
        cookie_secure=False,
        data_dir=tmp_path,
        api_key=APP_KEY,
        sync_key=sync_key,
    )
    backend = Backend()
    return create_app(settings, gemini_factory=lambda psid, psidts: FakeClient(backend, psid, psidts), store=MemoryStore())


def zip_of(resp) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_the_embedded_files_match_the_extension_folder():
    on_disk = sorted(p.name for p in EXT.iterdir() if p.is_file() and p.name != "build_extension.py")
    assert sorted(FILES) == on_disk, "run: python extension/build_extension.py"
    for name, b64 in FILES.items():
        assert base64.b64decode(b64) == (EXT / name).read_bytes(), f"{name} is out of date; rebuild"


def test_the_source_files_contain_no_keys():
    for name, b64 in FILES.items():
        assert APP_KEY.encode() not in base64.b64decode(b64)


def test_the_download_is_for_the_admin_only(tmp_path):
    with TestClient(build(tmp_path)) as c:
        for path in ("/api/extension/info", "/api/extension/download"):
            assert c.get(path).status_code == 401
            assert c.get(path, headers={"X-API-Key": APP_KEY}).status_code == 401
            assert c.get(path, headers={"X-API-Key": SYNC_KEY}).status_code == 401
        login(c)
        assert c.get("/api/extension/info").status_code == 200
        assert c.get("/api/extension/download").status_code == 200


def test_info_says_ready_and_never_shows_the_sync_key(tmp_path):
    with TestClient(build(tmp_path)) as c:
        login(c)
        body = c.get("/api/extension/info").json()
        assert body == {"ready": True, "filename": "gemini-session-sync.zip"}
        assert SYNC_KEY not in c.get("/api/extension/info").text


def test_without_a_sync_key_the_download_says_what_to_set(tmp_path):
    with TestClient(build(tmp_path, sync_key=None)) as c:
        login(c)
        assert c.get("/api/extension/info").json()["ready"] is False
        resp = c.get("/api/extension/download")
        assert resp.status_code == 503
        assert "GEMINI_SYNC_KEY" in resp.json()["detail"]


def test_the_download_is_a_configured_zip(tmp_path):
    with TestClient(build(tmp_path)) as c:
        login(c)
        resp = c.get("/api/extension/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert resp.headers["content-disposition"] == 'attachment; filename="gemini-session-sync.zip"'
        z = zip_of(resp)
        assert set(z.namelist()) == set(FILES) | {"defaults.json"}
        assert json.loads(z.read("defaults.json")) == {"serverUrl": "http://testserver", "syncKey": SYNC_KEY}
        assert json.loads(z.read("manifest.json"))["name"] == "Gemini Session Sync"
        assert z.read("README.md").startswith(b"# CONFIGURED COPY")
        assert APP_KEY.encode() not in resp.content  # only the sync key is embedded, never the app key


def test_the_server_address_comes_from_the_forwarded_headers(tmp_path):
    with TestClient(build(tmp_path)) as c:
        login(c)
        resp = c.get(
            "/api/extension/download",
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "app.example.test, other.example"},
        )
        assert json.loads(zip_of(resp).read("defaults.json"))["serverUrl"] == "https://app.example.test"
