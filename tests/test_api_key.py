"""The app's API key: chat, image and research routes accept it; login-only pages do not.

Gemini is a fake client and storage is in memory. No network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from tests.test_chat import ADMIN_PASS, ADMIN_USER, Backend, FakeClient, MemoryStore

KEY = "k3y-for-scripts-0123456789abcdef"


def build(tmp_path, api_key=KEY):
    settings = Settings(
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        secret_key="s" * 48,
        cookie_secure=False,
        data_dir=tmp_path,
        api_key=api_key,
    )
    backend = Backend()
    return create_app(settings, gemini_factory=lambda psid, psidts: FakeClient(backend, psid, psidts), store=MemoryStore())


@pytest.fixture
def client(tmp_path):
    with TestClient(build(tmp_path)) as c:
        yield c  # no login: the tests use the key alone


def bearer(key=KEY):
    return {"Authorization": f"Bearer {key}"}


def test_bearer_key_opens_the_chat_routes(client):
    assert client.get("/api/chat", headers=bearer()).status_code == 200


def test_x_api_key_header_opens_the_image_routes(client):
    assert client.get("/api/image", headers={"X-API-Key": KEY}).status_code == 200


def test_key_at_the_end_of_the_url_opens_the_research_routes(client):
    assert client.get(f"/api/research?key={KEY}").status_code == 200


def test_a_wrong_key_is_refused(client):
    resp = client.get("/api/chat", headers=bearer("not-the-key-at-all-0000000000"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Login or an API key is required."


def test_no_key_and_no_login_is_refused(client):
    resp = client.get("/api/chat")
    assert resp.status_code == 401


def test_the_key_does_not_open_the_gemini_cookie_or_session_pages(client):
    assert client.get("/api/session", headers=bearer()).status_code == 401
    assert client.post("/api/session/cookies", headers=bearer(), json={"cookies": "x"}).status_code == 401
    assert client.post("/api/session/verify", headers=bearer()).status_code == 401
    assert client.delete("/api/session/cookies", headers=bearer()).status_code == 401


def test_the_key_is_ignored_when_none_is_configured(tmp_path):
    with TestClient(build(tmp_path, api_key=None)) as c:
        assert c.get("/api/chat", headers=bearer()).status_code == 401


def test_a_key_that_is_not_yet_connected_to_gemini_reaches_the_route(client):
    # The key got past the check: the route itself says Gemini is not connected yet.
    resp = client.post("/api/chat/send", headers=bearer(), json={"message": "hi"})
    assert resp.status_code == 409
    assert "not connected" in resp.json()["detail"]


def test_repeated_wrong_keys_lock_the_client_out_for_a_while(client):
    for _ in range(5):
        assert client.get("/api/chat", headers=bearer("wrong-key-000000000000000000")).status_code == 401
    locked = client.get("/api/chat", headers=bearer())
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


def test_a_browser_login_still_works_alongside_the_key(tmp_path):
    with TestClient(build(tmp_path, api_key=None)) as c:
        assert c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
        assert c.get("/api/chat").status_code == 200


def test_a_short_key_is_refused_at_startup(tmp_path):
    env = {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "pw", "APP_SECRET_KEY": "s" * 48,
           "GEMINI_WEBUI_DATA": str(tmp_path), "APP_API_KEY": "short"}
    with pytest.raises(ConfigError, match="at least 24 characters"):
        load_settings(env)


def test_a_long_key_is_read_from_the_environment(tmp_path):
    env = {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "pw", "APP_SECRET_KEY": "s" * 48,
           "GEMINI_WEBUI_DATA": str(tmp_path), "APP_API_KEY": KEY}
    assert load_settings(env).api_key == KEY
