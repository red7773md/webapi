"""Blob failures give a readable message: no crash, no token in the output.

The Blob API is an in-memory httpx.MockTransport. Nothing here calls Vercel or Google.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import BlobStore, StorageError

BLOB_TOKEN = "vercel_blob_rw_teststore123_secretsecretsecret"
ADMIN_USER = "admin"
ADMIN_PASS = "correct-horse-battery"
PSID = "g.a000sampleSAMPLE123_-xyz"
PSIDTS = "sidts-CjEBsampleVALUE_xyz987"
PUBLIC_STORE_ERROR = {"error": {"code": "bad_request", "message": "Private access is not allowed on a public store"}}


def public_store(request: httpx.Request) -> httpx.Response:
    """What Vercel answers when a private-access write reaches a public store."""
    return httpx.Response(400, json=PUBLIC_STORE_ERROR)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        secret_key="k" * 48,
        cookie_secure=False,
        data_dir=tmp_path,
    )


def login(client):
    resp = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert resp.status_code == 200


def test_blob_write_error_names_the_reason_and_the_fix():
    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(public_store))
    with pytest.raises(StorageError) as info:
        asyncio.run(store.write("gemini_cookies.json", b"{}"))
    message = str(info.value)
    assert "HTTP 400" in message
    assert "Private access is not allowed on a public store" in message
    assert "PRIVATE Blob store" in message
    assert BLOB_TOKEN not in message


def test_blob_read_error_names_the_fix():
    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(public_store))
    with pytest.raises(StorageError, match="PRIVATE Blob store"):
        asyncio.run(store.read("gemini_cookies.json"))


def test_blob_network_failure_is_a_plain_message():
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(unreachable))
    with pytest.raises(StorageError, match="Could not reach Vercel Blob"):
        asyncio.run(store.read("gemini_cookies.json"))


def test_status_shows_the_storage_problem_instead_of_crashing(settings):
    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(public_store))
    with TestClient(create_app(settings, store=store)) as client:
        login(client)
        resp = client.get("/api/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified"] is False
    assert data["configured"] is False
    assert "PRIVATE Blob store" in data["last_error"]
    assert BLOB_TOKEN not in resp.text


def test_saving_cookies_during_a_storage_failure_returns_503(settings):
    store = BlobStore(BLOB_TOKEN, transport=httpx.MockTransport(public_store))
    body = json.dumps({"__Secure-1PSID": PSID, "__Secure-1PSIDTS": PSIDTS})
    with TestClient(create_app(settings, store=store)) as client:
        login(client)
        resp = client.post("/api/session/cookies", json={"cookies": body})
    assert resp.status_code == 503
    assert "PRIVATE Blob store" in resp.json()["detail"]
    assert PSID not in resp.text
    assert BLOB_TOKEN not in resp.text
