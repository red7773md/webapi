"""The browser extension saves the Gemini cookies with its own sync key. Gemini is a fake client; no network."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from tests.test_chat import ADMIN_PASS, ADMIN_USER, Backend, FakeClient, MemoryStore, login

APP_KEY = "k3y-for-scripts-0123456789abcdef"
SYNC_KEY = "sync-key-for-the-extension-0123456789"
PSID = "g.a000fakepsidvalue0123456789"
PSIDTS = "sidts-fake-value-0123456789"


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


def cookie_array() -> str:
    # What the extension sends: the cookies as a JSON array, as a cookie extension exports them.
    return json.dumps(
        [
            {"name": "__Secure-1PSID", "value": PSID},
            {"name": "__Secure-1PSIDTS", "value": PSIDTS},
            {"name": "NID", "value": "unused"},
        ]
    )


def test_the_sync_key_saves_the_gemini_cookies(tmp_path):
    with TestClient(build(tmp_path)) as c:
        resp = c.post("/api/session/sync", headers={"X-API-Key": SYNC_KEY}, json={"cookies": cookie_array()})
        assert resp.status_code == 200
        assert PSID not in resp.text  # the answer never repeats the cookie
        login(c)
        assert c.get("/api/session").json()["configured"] is True


def test_the_app_key_and_a_login_do_not_open_the_sync_route(tmp_path):
    with TestClient(build(tmp_path)) as c:
        body = {"cookies": cookie_array()}
        assert c.post("/api/session/sync", headers={"X-API-Key": APP_KEY}, json=body).status_code == 401
        assert c.post("/api/session/sync", json=body).status_code == 401
        login(c)
        assert c.post("/api/session/sync", json=body).status_code == 401


def test_a_wrong_sync_key_is_refused(tmp_path):
    with TestClient(build(tmp_path)) as c:
        resp = c.post("/api/session/sync", headers={"X-API-Key": "not-the-sync-key-000000000000"}, json={"cookies": cookie_array()})
        assert resp.status_code == 401


def test_without_a_sync_key_on_the_server_the_route_says_so(tmp_path):
    with TestClient(build(tmp_path, sync_key=None)) as c:
        resp = c.post("/api/session/sync", headers={"X-API-Key": SYNC_KEY}, json={"cookies": cookie_array()})
        assert resp.status_code == 503
        assert "GEMINI_SYNC_KEY" in resp.json()["detail"]


def test_text_with_no_cookies_is_refused(tmp_path):
    with TestClient(build(tmp_path)) as c:
        resp = c.post("/api/session/sync", headers={"X-API-Key": SYNC_KEY}, json={"cookies": "   "})
        assert resp.status_code == 422


def test_the_sync_key_is_read_from_the_environment_and_must_be_long(tmp_path):
    env = {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "pw", "APP_SECRET_KEY": "s" * 48, "GEMINI_WEBUI_DATA": str(tmp_path)}
    assert load_settings({**env, "GEMINI_SYNC_KEY": SYNC_KEY}).sync_key == SYNC_KEY
    assert load_settings(env).sync_key is None
    with pytest.raises(ConfigError, match="GEMINI_SYNC_KEY must be at least 24 characters"):
        load_settings({**env, "GEMINI_SYNC_KEY": "short"})
