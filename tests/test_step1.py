"""Step 1 tests: admin login, throttle, cookie parsing/storage, verify flow.

The real GeminiClient is never called; a fake factory is injected instead.
"""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient
from gemini_webapi import AuthError

from app.config import Settings
from app.cookie_store import CookieStore, mask, parse_cookie_text
from app.main import create_app

PSID = "g.a000sampleSAMPLE123_-xyz"
PSIDTS = "sidts-CjEBsampleVALUE_xyz987"
ADMIN_USER = "admin"
ADMIN_PASS = "correct-horse-battery"


# ----- fakes ------------------------------------------------------------------


class FakeModel:
    def __init__(self, name: str, display: str, available: bool = True) -> None:
        self.model_name = name
        self.display_name = display
        self.is_available = available


class FakeClient:
    def __init__(self, psid, psidts, *, error=None, models=None):
        self.psid = psid
        self.psidts = psidts
        self.closed = False
        self._error = error
        self._models = models if models is not None else [FakeModel("gemini-flash", "Flash")]

    async def init(self, timeout=60.0, auto_refresh=True, **_):
        if self._error is not None:
            raise self._error

    def list_models(self):
        return self._models

    async def close(self, delay: float = 0) -> None:  # the real GeminiClient.close is async
        self.closed = True


class RequestConnectionError(Exception):
    """Stands in for a transport error (name contains 'Connection')."""


def make_factory(*, error=None, models=None):
    created = []

    def factory(psid, psidts):
        client = FakeClient(psid, psidts, error=error, models=models)
        created.append(client)
        return client

    factory.created = created
    return factory


# ----- fixtures ---------------------------------------------------------------


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        secret_key="k" * 48,
        cookie_secure=False,
        data_dir=tmp_path,
    )


@pytest.fixture
def make_client(settings):
    clients = []

    def build(factory=None):
        app = create_app(settings, gemini_factory=factory or make_factory())
        ctx = TestClient(app)
        client = ctx.__enter__()
        clients.append(ctx)
        return client

    yield build
    for ctx in clients:
        ctx.__exit__(None, None, None)


def login(client, password=ADMIN_PASS):
    return client.post("/api/auth/login", json={"username": ADMIN_USER, "password": password})


# ----- cookie parsing ---------------------------------------------------------


def test_parses_cookie_header_string():
    psid, psidts = parse_cookie_text(f"__Secure-1PSID={PSID}; __Secure-1PSIDTS={PSIDTS}; NID=ignored")
    assert (psid, psidts) == (PSID, PSIDTS)


def test_parses_cookie_header_with_prefix():
    psid, psidts = parse_cookie_text(f"Cookie: __Secure-1PSID={PSID}; __Secure-1PSIDTS={PSIDTS}")
    assert (psid, psidts) == (PSID, PSIDTS)


def test_parses_json_object():
    raw = json.dumps({"__Secure-1PSID": PSID, "__Secure-1PSIDTS": PSIDTS})
    assert parse_cookie_text(raw) == (PSID, PSIDTS)


def test_parses_extension_json_array():
    raw = json.dumps(
        [
            {"name": "NID", "value": "x"},
            {"name": "__Secure-1PSID", "value": PSID},
            {"name": "__Secure-1PSIDTS", "value": PSIDTS},
        ]
    )
    assert parse_cookie_text(raw) == (PSID, PSIDTS)


def test_single_token_is_psid_only():
    assert parse_cookie_text(PSID) == (PSID, None)


def test_two_tokens_are_psid_then_psidts():
    assert parse_cookie_text(f"{PSID} {PSIDTS}") == (PSID, PSIDTS)


def test_missing_psid_is_rejected():
    with pytest.raises(ValueError, match="__Secure-1PSID was not found"):
        parse_cookie_text(f"__Secure-1PSIDTS={PSIDTS}")


def test_empty_input_is_rejected():
    with pytest.raises(ValueError):
        parse_cookie_text("   ")


def test_malformed_value_is_rejected():
    with pytest.raises(ValueError, match="do not look like a cookie value"):
        parse_cookie_text("__Secure-1PSID=<script>alert(1)</script>")


def test_too_many_bare_tokens_are_rejected():
    with pytest.raises(ValueError):
        parse_cookie_text("aaaaaaaaaa bbbbbbbbbb cccccccccc")


def test_mask_hides_the_middle():
    hint = mask(PSID)
    assert hint.startswith(PSID[:4])
    assert hint.endswith(PSID[-4:])
    assert PSID not in hint
    assert mask(None) is None


# ----- cookie store -----------------------------------------------------------


def test_store_writes_owner_only_file(tmp_path):
    store = CookieStore(tmp_path / "gemini_cookies.json")
    store.save(PSID, PSIDTS)
    mode = stat.S_IMODE((tmp_path / "gemini_cookies.json").stat().st_mode)
    assert mode == 0o600
    loaded = store.load()
    assert loaded.psid == PSID and loaded.psidts == PSIDTS
    store.clear()
    assert store.load() is None


# ----- HTTP: login and access control -----------------------------------------


def test_session_routes_require_login(make_client):
    client = make_client()
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/session/verify").status_code == 401
    assert client.post("/api/session/cookies", json={"cookies": PSID}).status_code == 401


def test_wrong_password_is_rejected(make_client):
    client = make_client()
    resp = login(client, password="nope")
    assert resp.status_code == 401
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_correct_login_opens_session(make_client):
    client = make_client()
    assert login(client).status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is True
    assert client.get("/api/session").status_code == 200


def test_logout_closes_session(make_client):
    client = make_client()
    login(client)
    client.post("/api/auth/logout")
    assert client.get("/api/session").status_code == 401


def test_repeated_failures_lock_out_even_correct_password(make_client):
    client = make_client()
    for _ in range(5):
        assert login(client, password="wrong").status_code == 401
    locked = login(client)
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


def test_security_headers_present(make_client):
    client = make_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"


def test_api_docs_are_disabled(make_client):
    client = make_client()
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# ----- HTTP: cookie import and verification ------------------------------------


def test_save_cookies_returns_masked_status(make_client, settings):
    client = make_client()
    login(client)
    body = json.dumps({"__Secure-1PSID": PSID, "__Secure-1PSIDTS": PSIDTS})
    resp = client.post("/api/session/cookies", json={"cookies": body})
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["has_psidts"] is True
    assert data["verified"] is False
    assert PSID not in json.dumps(data)
    assert PSIDTS not in json.dumps(data)
    stored = (settings.data_dir / "gemini_cookies.json")
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600


def test_bad_cookie_input_returns_422(make_client):
    client = make_client()
    login(client)
    resp = client.post("/api/session/cookies", json={"cookies": "__Secure-1PSIDTS=abc"})
    assert resp.status_code == 422
    assert "__Secure-1PSID was not found" in resp.json()["detail"]


def test_verify_success_lists_models(make_client):
    client = make_client(factory=make_factory(models=[FakeModel("gemini-pro", "Pro")]))
    login(client)
    client.post("/api/session/cookies", json={"cookies": f"{PSID} {PSIDTS}"})
    data = client.post("/api/session/verify").json()
    assert data["verified"] is True
    assert data["last_error"] is None
    assert data["models"] == [{"name": "gemini-pro", "display_name": "Pro", "available": True}]


def test_verify_auth_error_is_reported(make_client):
    client = make_client(factory=make_factory(error=AuthError("rejected")))
    login(client)
    client.post("/api/session/cookies", json={"cookies": PSID})
    data = client.post("/api/session/verify").json()
    assert data["verified"] is False
    assert "Google rejected these cookies" in data["last_error"]


def test_verify_with_no_models_is_not_treated_as_success(make_client):
    client = make_client(factory=make_factory(models=[]))
    login(client)
    client.post("/api/session/cookies", json={"cookies": PSID})
    data = client.post("/api/session/verify").json()
    assert data["verified"] is False
    assert "no models were returned" in data["last_error"]


def test_purge_removes_cookies_and_closes_live_client(make_client, settings):
    factory = make_factory()
    client = make_client(factory=factory)
    login(client)
    client.post("/api/session/cookies", json={"cookies": PSID})
    client.post("/api/session/verify")
    assert factory.created and factory.created[0].closed is False

    data = client.delete("/api/session/cookies").json()

    assert data["configured"] is False
    assert data["verified"] is False
    assert factory.created[0].closed is True  # the async close was actually awaited
    assert not (settings.data_dir / "gemini_cookies.json").exists()


def test_connection_failures_get_a_plain_message(make_client):
    client = make_client(factory=make_factory(error=RequestConnectionError("CONNECT tunnel failed")))
    login(client)
    client.post("/api/session/cookies", json={"cookies": PSID})
    data = client.post("/api/session/verify").json()
    assert data["verified"] is False
    assert data["last_error"].startswith("Could not reach gemini.google.com")
