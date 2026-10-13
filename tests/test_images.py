"""Image generation: prompts, saved bytes, admin-only serving, delete and plain errors.

Gemini is a fake client and storage is an in-memory KeyValueStore. No network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import image_service
from app.config import Settings
from app.main import create_app

ADMIN_USER = "admin"
ADMIN_PASS = "correct-horse-battery"
PSID = "g.a000sampleSAMPLE123_-xyz"
PSIDTS = "sidts-CjEBsampleVALUE_xyz987"
COOKIE_BODY = json.dumps({"__Secure-1PSID": PSID, "__Secure-1PSIDTS": PSIDTS})
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"


class MemoryStore:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.types: dict[str, str] = {}

    async def read(self, name):
        return self.items.get(name)

    async def write(self, name, data, content_type="application/json"):
        self.items[name] = data
        self.types[name] = content_type

    async def delete(self, name):
        self.items.pop(name, None)
        self.types.pop(name, None)


class Backend:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.images: list[FakeImage] = []
        self.text = "Here you go."
        self.fail_with: Exception | None = None
        self.delay = 0.0


class FakeImage:
    """Stands in for a GeneratedImage: save() writes the file and returns its path."""

    def __init__(self, data: bytes, ext: str = "png") -> None:
        self.data = data
        self.ext = ext

    async def save(self, path="temp", filename=None, verbose=False, client=None, **kwargs):
        out = Path(path) / f"{Path(filename or 'image').stem}.{self.ext}"
        out.write_bytes(self.data)
        return str(out)


class FakeOutput:
    def __init__(self, text, images) -> None:
        self.text = text
        self.images = images


class FakeModel:
    model_name = "gemini-flash"
    display_name = "Flash"
    is_available = True


class FakeClient:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def init(self, timeout=45.0, auto_refresh=True, **_):
        return None

    def list_models(self):
        return [FakeModel()]

    async def generate_content(self, prompt, model=None, **_):
        if self._backend.delay:
            await asyncio.sleep(self._backend.delay)
        if self._backend.fail_with is not None:
            raise self._backend.fail_with
        self._backend.prompts.append(prompt)
        return FakeOutput(self._backend.text, list(self._backend.images))

    async def close(self, delay: float = 0) -> None:
        return None


@pytest.fixture
def settings(tmp_path):
    return Settings(
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        secret_key="k" * 48,
        cookie_secure=False,
        data_dir=tmp_path,
    )


def leftover_image_files(store):
    """Image bytes and records, ignoring the index list itself."""
    return [n for n in store.items if n.startswith("images/") and n != "images/index.json"]


def login(client):
    assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200


def build(settings, backend, store):
    return create_app(settings, gemini_factory=lambda psid, psidts: FakeClient(backend), store=store)


@pytest.fixture
def env(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        assert client.post("/api/session/cookies", json={"cookies": COOKIE_BODY}).status_code == 200
        assert client.post("/api/session/verify").json()["verified"] is True
        yield client, backend, store


def test_generate_keeps_the_image_and_serves_the_same_bytes(env):
    client, backend, _ = env
    backend.images = [FakeImage(PNG)]
    resp = client.post("/api/image/generate", json={"prompt": "a red fox in snow"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["images"]) == 1
    image = body["images"][0]
    assert image["mime"] == "image/png"
    assert image["size"] == len(PNG)
    assert image["prompt"] == "a red fox in snow"
    assert backend.prompts == ["Generate an image: a red fox in snow"]

    served = client.get(f"/api/image/{image['id']}")
    assert served.status_code == 200
    assert served.content == PNG
    assert served.headers["content-type"] == "image/png"

    listed = client.get("/api/image").json()["images"]
    assert [i["id"] for i in listed] == [image["id"]]


def test_prompt_that_already_asks_for_an_image_is_sent_as_typed(env):
    client, backend, _ = env
    backend.images = [FakeImage(PNG)]
    client.post("/api/image/generate", json={"prompt": "draw a lighthouse at dusk"})
    assert backend.prompts == ["draw a lighthouse at dusk"]


def test_image_bytes_are_stored_with_their_real_content_type(env):
    client, backend, store = env
    backend.images = [FakeImage(PNG)]
    image_id = client.post("/api/image/generate", json={"prompt": "a boat"}).json()["images"][0]["id"]
    assert store.types[f"images/{image_id}.png"] == "image/png"
    assert store.items[f"images/{image_id}.png"] == PNG


def test_text_only_reply_is_a_clear_502(env):
    client, backend, _ = env
    backend.images = []
    backend.text = "I can't draw people, but I can draw landscapes."
    resp = client.post("/api/image/generate", json={"prompt": "a portrait"})
    assert resp.status_code == 502
    assert resp.json()["detail"].startswith("Gemini answered with text and no image.")
    assert "I can't draw people" in resp.json()["detail"]


def test_a_file_that_is_not_an_image_is_refused(env):
    client, backend, store = env
    backend.images = [FakeImage(b"%PDF-1.4", ext="pdf")]
    resp = client.post("/api/image/generate", json={"prompt": "a chart"})
    assert resp.status_code == 502
    assert "unexpected file type" in resp.json()["detail"]
    assert leftover_image_files(store) == []


def test_delete_removes_the_record_and_the_bytes(env):
    client, backend, store = env
    backend.images = [FakeImage(PNG)]
    image_id = client.post("/api/image/generate", json={"prompt": "a cup"}).json()["images"][0]["id"]
    assert client.delete(f"/api/image/{image_id}").json() == {"ok": True}
    assert client.get(f"/api/image/{image_id}").status_code == 404
    assert client.get("/api/image").json()["images"] == []
    assert leftover_image_files(store) == []


def test_unknown_and_malformed_image_ids(env):
    client, _, _ = env
    assert client.get("/api/image/0123456789abcdef").status_code == 404
    assert client.get("/api/image/not-an-id").status_code == 422
    assert client.delete("/api/image/0123456789abcdef").status_code == 404


def test_empty_and_long_prompts_are_refused(env):
    client, backend, _ = env
    assert client.post("/api/image/generate", json={"prompt": "   "}).status_code == 422
    assert client.post("/api/image/generate", json={"prompt": "x" * 1001}).status_code == 422
    assert backend.prompts == []


def test_images_need_a_connection(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        resp = client.post("/api/image/generate", json={"prompt": "a cat"})
    assert resp.status_code == 409
    assert "not connected" in resp.json()["detail"]


def test_slow_generation_gives_a_504(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(image_service, "GENERATE_TIMEOUT_SECONDS", 0.05)
    backend.delay = 1.0
    backend.images = [FakeImage(PNG)]
    resp = client.post("/api/image/generate", json={"prompt": "a slow cat"})
    assert resp.status_code == 504


def test_images_need_admin_login(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        assert client.get("/api/image").status_code == 401
        assert client.post("/api/image/generate", json={"prompt": "x"}).status_code == 401
