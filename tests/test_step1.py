"""Tests: admin login, throttle, cookie parsing/storage, verify flow, Blob backend, entrypoint.

No test calls Google or Vercel. The Gemini client is a fake, and the Blob HTTP API
is served by an in-memory httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import stat
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from gemini_webapi import AuthError

from app.config import ConfigError, Settings, load_settings
from app.cookie_store import CookieStore, mask, parse_cookie_text
from app.entrypoint import build_entrypoint
from app.gemini_service import GeminiNotReady
from app.main import create_app
from app.storage import BlobStore, LocalFileStore, store_id_from_token

PSID = "g.a000sampleSAMPLE123_-xyz"
PSIDTS = "sidts-CjEBsampleVALUE_xyz987"
ADMIN_USER = "admin"
ADMIN_PASS = "correct-horse-battery"
BLOB_TOKEN = "vercel_blob_rw_teststore123_secretsecretsecret"


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

    async def init(self, timeout=45.0, auto_refresh=True, **_):
        if self._error is not None:
            raise self._error

    def list_models(self):
        return self._models

    async def close(self, delay: float = 0) -> None:  # the real GeminiClient.close is async
        self.closed = True


class RequestConnectionError(Exception):
    """Stands in for a transport error (its name contains 'Connection')."""


def make_factory(*, error=None, models=None):
    created = []

    def factory(psid, psidts):
        client = FakeClient(psid, psidts, error=error, models=models)
        created.append(client)
        return client

    factory.created = created
    return factory


class FakeBlobService:
    """The subset of the Vercel Blob HTTP API that BlobStore uses, kept in memory."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.method == "PUT" and request.url.host == "blob.vercel-storage.com":
            pathname = request.url.params.get("pathname")
            self.blobs[pathname] = request.content
            return httpx.Response(200, json={"pathname": pathname, "url": f"https://x/{pathname}"})
        if request.method == "GET" and request.url.host.endswith(".private.blob.vercel-storage.com"):
            pathname = request.url.path.lstrip("/")
            if pathname not in self.blobs:
                return httpx.Response(404)
            return httpx.Response(200, content=self.blobs[pathname])
        if request.method == "POST" and request.url.path == "/delete":
            for url in json.loads(request.content)["urls"]:
                self.blobs.pop(urlparse(url).path.lstrip("/"), None)
            return httpx.Response(200, json={})
        return httpx.Response(400, json={"error": "unexpected request in test"})


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

    def build(factory=None, store=None):
        app = create_app(settings, gemini_factory=factory or make_factory(), store=store)
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


# ----- storage backends -------------------------------------------------------


def test_local_store_writes_owner_only_file(tmp_path):
    cookies = CookieStore(LocalFileStore(tmp_path))
    asyncio.run(cookies.save(PSID, PSIDTS))
    assert stat.S_IMODE((tmp_path / "gemini_cookies.json").stat().st_mode) == 0o600
    record = asyncio.run(cookies.load())
    assert record.psid == PSID and record.psidts == PSIDTS
    asyncio.run(cookies.clear())
    assert asyncio.run(cookies.load()) is None


def test_blob_store_round_trip_uses_documented_headers():
    fake = FakeBlobService()
    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(fake.handler))
    cookies = CookieStore(store)

    asyncio.run(cookies.save(PSID, PSIDTS))
    record = asyncio.run(cookies.record_verification(verified_at="2026-10-13T00:00:00+00:00", models=[], last_error=None))
    assert record.verified_at

    loaded = asyncio.run(cookies.load())
    assert loaded.psid == PSID and loaded.verified_at == "2026-10-13T00:00:00+00:00"

    put = next(c for c in fake.calls if c.method == "PUT")
    assert put.url.path == "/" and put.url.params["pathname"] == "gemini-webui/gemini_cookies.json"
    assert put.headers["authorization"] == f"Bearer {BLOB_TOKEN}"
    assert put.headers["x-api-version"] == "12"
    assert put.headers["x-vercel-blob-access"] == "private"
    assert put.headers["x-allow-overwrite"] == "1"
    assert put.headers["x-add-random-suffix"] == "0"

    get = next(c for c in fake.calls if c.method == "GET")
    assert get.url.host == "teststore123.private.blob.vercel-storage.com"

    asyncio.run(cookies.clear())
    assert asyncio.run(cookies.load()) is None
    assert fake.blobs == {}


def test_blob_token_store_id_is_parsed():
    assert store_id_from_token(BLOB_TOKEN) == "teststore123"
    with pytest.raises(ConfigError):
        store_id_from_token("not-a-token")


# ----- configuration ----------------------------------------------------------


def test_missing_admin_settings_are_named():
    with pytest.raises(ConfigError, match="ADMIN_USERNAME, ADMIN_PASSWORD"):
        load_settings({})


def test_vercel_requires_secret_and_blob_store():
    with pytest.raises(ConfigError) as exc:
        load_settings({"VERCEL": "1", "ADMIN_USERNAME": "a", "ADMIN_PASSWORD": "b"})
    assert "APP_SECRET_KEY" in str(exc.value)
    assert "BLOB_READ_WRITE_TOKEN" in str(exc.value)


def test_vercel_settings_are_production_safe():
    s = load_settings(
        {
            "VERCEL": "1",
            "ADMIN_USERNAME": "a",
            "ADMIN_PASSWORD": "b",
            "APP_SECRET_KEY": "x" * 40,
            "BLOB_READ_WRITE_TOKEN": BLOB_TOKEN,
        }
    )
    assert s.on_vercel is True
    assert s.cookie_secure is True
    assert s.blob_token == BLOB_TOKEN
    assert "Blob" in s.storage_label


def test_local_secret_is_generated_once(tmp_path):
    env = {"ADMIN_USERNAME": "a", "ADMIN_PASSWORD": "b", "GEMINI_WEBUI_DATA": str(tmp_path)}
    first = load_settings(env).secret_key
    assert first == load_settings(env).secret_key
    assert len(first) > 40


def test_entrypoint_reports_missing_config_as_503():
    client = TestClient(build_entrypoint({}))
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert "ADMIN_USERNAME" in resp.json()["error"]


def test_entrypoint_builds_the_real_app_when_configured(tmp_path):
    env = {"ADMIN_USERNAME": ADMIN_USER, "ADMIN_PASSWORD": ADMIN_PASS, "GEMINI_WEBUI_DATA": str(tmp_path)}
    with TestClient(build_entrypoint(env)) as client:
        assert client.get("/healthz").json() == {"ok": True}
        assert client.get("/api/session").status_code == 401


# ----- HTTP: login and access control -----------------------------------------


def test_session_routes_require_login(make_client):
    client = make_client()
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/session/verify").status_code == 401
    assert client.post("/api/session/cookies", json={"cookies": PSID}).status_code == 401


def test_wrong_password_is_rejected(make_client):
    client = make_client()
    assert login(client, password="nope").status_code == 401
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
    assert data["storage"].startswith("local file")
    assert PSID not in json.dumps(data)
    assert PSIDTS not in json.dumps(data)
    assert stat.S_IMODE((settings.data_dir / "gemini_cookies.json").stat().st_mode) == 0o600


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
    assert data["verified_at"]
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


def test_connection_failures_get_a_plain_message(make_client):
    client = make_client(factory=make_factory(error=RequestConnectionError("CONNECT tunnel failed")))
    login(client)
    client.post("/api/session/cookies", json={"cookies": PSID})
    data = client.post("/api/session/verify").json()
    assert data["verified"] is False
    assert data["last_error"].startswith("Could not reach gemini.google.com")


def test_verification_state_is_shared_through_the_store(settings):
    """A second app instance on the same store sees the first instance's verification."""
    store = LocalFileStore(settings.data_dir)
    first = TestClient(create_app(settings, gemini_factory=make_factory(), store=store))
    second = TestClient(create_app(settings, gemini_factory=make_factory(), store=store))
    with first, second:
        login(first)
        first.post("/api/session/cookies", json={"cookies": f"{PSID} {PSIDTS}"})
        first.post("/api/session/verify")
        login(second)
        assert second.get("/api/session").json()["verified"] is True


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


def test_require_client_needs_a_successful_verify(make_client):
    factory = make_factory()
    client = make_client(factory=factory)
    service = client.app.state.gemini
    login(client)
    client.post("/api/session/cookies", json={"cookies": PSID})

    with pytest.raises(GeminiNotReady):
        asyncio.run(service.require_client())

    client.post("/api/session/verify")
    assert asyncio.run(service.require_client()) is not None


def test_blob_backed_app_uses_the_injected_store(settings):
    fake = FakeBlobService()
    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(fake.handler))
    blob_settings = dataclasses.replace(settings, blob_token=BLOB_TOKEN)
    client = TestClient(create_app(blob_settings, gemini_factory=make_factory(), store=store))
    with client:
        login(client)
        status = client.post("/api/session/cookies", json={"cookies": f"{PSID} {PSIDTS}"}).json()
        assert status["storage"] == "Vercel Blob (private store)"
        assert "gemini-webui/gemini_cookies.json" in fake.blobs
        assert PSID.encode() in fake.blobs["gemini-webui/gemini_cookies.json"]  # stored, not echoed
