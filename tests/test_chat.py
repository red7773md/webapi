"""Chat module: multi-turn sends, attachments, history and plain error messages.

Gemini is a fake client and storage is an in-memory KeyValueStore. No network.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from gemini_webapi import AuthError, GeminiError

from app import chat_service
from app.config import Settings
from app.main import create_app

ADMIN_USER = "admin"
ADMIN_PASS = "correct-horse-battery"
PSID = "g.a000sampleSAMPLE123_-xyz"
PSIDTS = "sidts-CjEBsampleVALUE_xyz987"
COOKIE_BODY = json.dumps({"__Secure-1PSID": PSID, "__Secure-1PSIDTS": PSIDTS})


class MemoryStore:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    async def read(self, name):
        return self.items.get(name)

    async def write(self, name, data, content_type="application/json"):
        self.items[name] = data

    async def delete(self, name):
        self.items.pop(name, None)


class Backend:
    """Records what the app sent to Gemini. Shared by every fake client."""

    def __init__(self) -> None:
        self.started: list[dict] = []
        self.sent: list[dict] = []
        self.fail_with: Exception | None = None
        self.delay = 0.0
        self.early_cid = False  # Gemini names the chat before the reply is finished
        self.saved_turns: list = []  # what the saved chat holds, newest first
        self.reads = 0
        self.read_error: Exception | None = None


def wire(parts: list) -> str:
    """Frame JSON the way the batch endpoint does: each payload is preceded by its length."""
    body = json.dumps(parts)
    return ")]}'\n\n" + f"{len(body.encode('utf-16-le')) // 2}\n{body}\n"


def saved_turn(rid: str, text: str, finished: bool = True) -> list:
    """One turn of a saved chat, shaped as the conversation-turns read returns it."""
    candidate = [None] * 13
    candidate[0] = f"rc_{rid}"
    candidate[1] = [text]
    candidate[8] = [2 if finished else 1]
    return [["c_fake", rid], None, [["question"]], [[candidate]]]


class FakeModel:
    model_name = "gemini-flash"
    display_name = "Flash"
    is_available = True


class FakeOutput:
    def __init__(self, text: str, metadata: list[str]) -> None:
        self.text = text
        self.metadata = metadata


class FakeSession:
    def __init__(self, backend: Backend, metadata) -> None:
        self._backend = backend
        self._metadata = list(metadata or [])

    @property
    def metadata(self) -> list[str]:
        return list(self._metadata)

    async def send_message(self, prompt, files=None, **_):
        if self._backend.early_cid:
            self._metadata = ["c_fake"]
        if self._backend.delay:
            await asyncio.sleep(self._backend.delay)
        if self._backend.fail_with is not None:
            raise self._backend.fail_with
        # Read attachments while the app's temporary folder still exists, as the real library does.
        seen = [(f.name, f.read_bytes()) for f in (files or [])]
        self._backend.sent.append({"prompt": prompt, "files": seen, "metadata": self.metadata})
        turn = len(self._backend.sent)
        self._metadata = ["c_fake", f"r_{turn}", f"rc_{turn}"]
        return FakeOutput(f"reply {turn}: {prompt}", list(self._metadata))


class FakeClient:
    def __init__(self, backend: Backend, psid, psidts) -> None:
        self._backend = backend

    async def init(self, timeout=45.0, auto_refresh=True, **_):
        return None

    def list_models(self):
        return [FakeModel()]

    def start_chat(self, **kwargs):
        self._backend.started.append(kwargs)
        return FakeSession(self._backend, kwargs.get("metadata"))

    async def _batch_execute(self, payloads, close_on_error=True, **_):
        self._backend.reads += 1
        if self._backend.read_error is not None:
            raise self._backend.read_error
        turns = self._backend.saved_turns
        return SimpleNamespace(text=wire([["wrb.fr", "hNvQHb", json.dumps([turns]), None, None, None, "generic"]]))

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


def login(client):
    assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200


def build(settings, backend, store):
    return create_app(
        settings,
        gemini_factory=lambda psid, psidts: FakeClient(backend, psid, psidts),
        store=store,
    )


@pytest.fixture
def env(settings, monkeypatch):
    """A logged-in app with a verified (fake) Gemini connection. Checks are not spaced out."""
    monkeypatch.setattr(chat_service, "CHECK_SECONDS", 0.0)
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        assert client.post("/api/session/cookies", json={"cookies": COOKIE_BODY}).status_code == 200
        assert client.post("/api/session/verify").json()["verified"] is True
        yield client, backend, store


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_first_message_creates_a_chat_and_returns_the_reply(env):
    client, backend, _ = env
    resp = client.post("/api/chat/send", json={"message": "Hello there"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["chat"]["title"] == "Hello there"
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "model"
    assert body["messages"][1]["text"] == "reply 1: Hello there"
    chat_id = body["chat"]["id"]
    assert len(chat_id) == 16

    listed = client.get("/api/chat").json()["chats"]
    assert [c["id"] for c in listed] == [chat_id]
    detail = client.get(f"/api/chat/{chat_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "model"]


def test_follow_up_resumes_gemini_context_from_saved_metadata(env):
    client, backend, _ = env
    first = client.post("/api/chat/send", json={"message": "First"}).json()
    chat_id = first["chat"]["id"]
    second = client.post("/api/chat/send", json={"chat_id": chat_id, "message": "Second"})
    assert second.status_code == 200
    # The second session was rebuilt from the first reply's metadata
    assert backend.started[1]["metadata"] == ["c_fake", "r_1", "rc_1"]
    assert backend.sent[1]["metadata"] == ["c_fake", "r_1", "rc_1"]
    detail = client.get(f"/api/chat/{chat_id}").json()
    assert len(detail["messages"]) == 4
    assert detail["title"] == "First"


def test_attachments_reach_gemini_under_their_real_names(env):
    client, backend, _ = env
    payload = b"%PDF-1.4 small test"
    resp = client.post(
        "/api/chat/send",
        json={"message": "Summarise", "files": [{"name": "notes.pdf", "data": b64(payload)}]},
    )
    assert resp.status_code == 200
    assert backend.sent[0]["files"] == [("0_notes.pdf", payload)]
    chat_id = resp.json()["chat"]["id"]
    stored = client.get(f"/api/chat/{chat_id}").text
    assert b64(payload) not in stored  # the file itself is not kept
    assert '"name":"notes.pdf"' in stored


def test_too_many_attachments_are_refused(env):
    client, backend, _ = env
    files = [{"name": f"f{i}.txt", "data": b64(b"x")} for i in range(5)]
    resp = client.post("/api/chat/send", json={"message": "hi", "files": files})
    assert resp.status_code == 422
    assert backend.sent == []


def test_oversized_attachment_is_refused(env):
    client, backend, _ = env
    big = b"x" * (3 * 1024 * 1024 + 1)
    resp = client.post("/api/chat/send", json={"message": "hi", "files": [{"name": "big.bin", "data": b64(big)}]})
    assert resp.status_code == 422
    assert "larger than 3 MB" in resp.json()["detail"]


def test_bad_base64_is_refused(env):
    client, _, _ = env
    resp = client.post("/api/chat/send", json={"message": "hi", "files": [{"name": "a.txt", "data": "!!!!"}]})
    assert resp.status_code == 422
    assert "not valid base64" in resp.json()["detail"]


def test_blank_message_is_refused(env):
    client, _, _ = env
    resp = client.post("/api/chat/send", json={"message": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Message is empty."


def test_unknown_and_malformed_chat_ids(env):
    client, _, _ = env
    assert client.get("/api/chat/0123456789abcdef").status_code == 404
    assert client.get("/api/chat/not-an-id").status_code == 422


def test_delete_removes_the_chat_and_its_index_entry(env):
    client, _, _ = env
    chat_id = client.post("/api/chat/send", json={"message": "bin me"}).json()["chat"]["id"]
    assert client.delete(f"/api/chat/{chat_id}").json() == {"ok": True}
    assert client.get("/api/chat").json()["chats"] == []
    assert client.get(f"/api/chat/{chat_id}").status_code == 404


def test_gemini_rejecting_cookies_gives_a_plain_502(env):
    client, backend, _ = env
    backend.fail_with = AuthError("rejected")
    resp = client.post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Google rejected these cookies. Copy fresh values from a logged-in browser session."


def test_slow_reply_returns_the_chat_id_at_once_as_waiting(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    backend.delay = 1.0
    backend.early_cid = True
    resp = client.post("/api/chat/send", json={"message": "think hard"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "waiting"
    assert body["chat"]["id"] and body["chat"]["status"] == "waiting"
    assert [m["role"] for m in body["messages"]] == ["user"]  # no reply yet


def test_the_reply_is_fetched_by_the_chat_id_once_gemini_has_it(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    backend.delay = 1.0
    backend.early_cid = True
    chat_id = client.post("/api/chat/send", json={"message": "think hard"}).json()["chat"]["id"]

    waiting = client.get(f"/api/chat/{chat_id}").json()
    assert waiting["status"] == "waiting"
    assert [m["role"] for m in waiting["messages"]] == ["user"]

    backend.saved_turns = [saved_turn("r_1", "The finished answer.")]
    done = client.get(f"/api/chat/{chat_id}").json()
    assert done["status"] == "answered"
    assert [m["text"] for m in done["messages"]] == ["think hard", "The finished answer."]
    assert done["metadata"][:3] == ["c_fake", "r_1", "rc_r_1"]
    assert client.get("/api/chat").json()["chats"][0]["status"] == "answered"


def test_a_follow_up_while_the_reply_is_waiting_is_a_409(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    backend.delay = 1.0
    backend.early_cid = True
    chat_id = client.post("/api/chat/send", json={"message": "first"}).json()["chat"]["id"]
    resp = client.post("/api/chat/send", json={"chat_id": chat_id, "message": "second"})
    assert resp.status_code == 409
    assert "still being answered" in resp.json()["detail"]


def test_a_follow_up_after_the_reply_continues_the_same_chat(env):
    client, backend, _ = env
    chat_id = client.post("/api/chat/send", json={"message": "one"}).json()["chat"]["id"]
    resp = client.post("/api/chat/send", json={"chat_id": chat_id, "message": "two"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    assert resp.json()["chat"]["id"] == chat_id
    assert backend.started[-1]["metadata"] == ["c_fake", "r_1", "rc_1"]


def test_a_reply_before_the_previous_one_is_not_taken_for_the_new_reply(env, monkeypatch):
    client, backend, _ = env
    chat_id = client.post("/api/chat/send", json={"message": "one"}).json()["chat"]["id"]
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    backend.delay = 1.0
    client.post("/api/chat/send", json={"chat_id": chat_id, "message": "two"})

    # The saved chat still shows the answer to "one" as its newest finished turn.
    backend.saved_turns = [saved_turn("r_1", "Answer to one.")]
    assert client.get(f"/api/chat/{chat_id}").json()["status"] == "waiting"

    backend.saved_turns = [saved_turn("r_2", "Answer to two."), saved_turn("r_1", "Answer to one.")]
    done = client.get(f"/api/chat/{chat_id}").json()
    assert done["status"] == "answered"
    assert done["messages"][-1]["text"] == "Answer to two."


def test_checks_of_a_waiting_reply_are_spaced_out(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(chat_service, "CHECK_SECONDS", 60.0)
    backend.delay = 1.0
    backend.early_cid = True
    chat_id = client.post("/api/chat/send", json={"message": "slow"}).json()["chat"]["id"]
    before = backend.reads
    for _ in range(5):
        assert client.get(f"/api/chat/{chat_id}").json()["status"] == "waiting"
    assert backend.reads - before == 1  # the first check is due, the rest fall inside the gap


def test_a_failed_check_is_recorded_and_the_chat_stays_waiting(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    backend.delay = 1.0
    backend.early_cid = True
    chat_id = client.post("/api/chat/send", json={"message": "slow"}).json()["chat"]["id"]
    backend.read_error = GeminiError("Gemini is busy")
    resp = client.get(f"/api/chat/{chat_id}").json()
    assert resp["status"] == "waiting"
    assert "Gemini is busy" in resp["pending"]["last_check_error"]
    assert resp["pending"]["check_failures"] == 1


def test_a_reply_never_written_is_given_up_after_the_wait(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(chat_service, "REPLY_WAIT_SECONDS", 0.0)
    backend.delay = 1.0
    backend.early_cid = True
    chat_id = client.post("/api/chat/send", json={"message": "lost"}).json()["chat"]["id"]
    resp = client.get(f"/api/chat/{chat_id}").json()
    assert resp["status"] == "failed"
    assert "did not reply within 30 minutes" in resp["pending"]["error"]


def test_a_send_with_no_chat_id_before_the_wait_is_failed_but_keeps_its_id(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 0.05)
    backend.delay = 1.0  # no early_cid: Gemini never names the chat
    resp = client.post("/api/chat/send", json={"message": "stuck"})
    assert resp.status_code == 200
    chat_id = resp.json()["chat"]["id"]
    assert resp.json()["status"] == "failed"
    got = client.get(f"/api/chat/{chat_id}").json()
    assert got["status"] == "failed"
    assert "Send it again" in got["pending"]["error"]

    monkeypatch.setattr(chat_service, "SEND_WAIT_SECONDS", 25.0)
    backend.delay = 0.0
    again = client.post("/api/chat/send", json={"chat_id": chat_id, "message": "again"})
    assert again.status_code == 200
    assert again.json()["status"] == "answered"
    assert again.json()["chat"]["status"] == "answered"


def test_sending_without_a_connection_is_a_409(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        resp = client.post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 409
    assert "not connected" in resp.json()["detail"]


def test_history_is_shared_through_the_store(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as writer:
        login(writer)
        writer.post("/api/session/cookies", json={"cookies": COOKIE_BODY})
        writer.post("/api/session/verify")
        chat_id = writer.post("/api/chat/send", json={"message": "saved"}).json()["chat"]["id"]
    # A second instance (as a second serverless invocation would be) sees the same history
    with TestClient(build(settings, Backend(), store)) as reader:
        login(reader)
        assert [c["id"] for c in reader.get("/api/chat").json()["chats"]] == [chat_id]
        assert reader.get(f"/api/chat/{chat_id}").json()["messages"][1]["text"] == "reply 1: saved"


def test_chat_round_trip_on_local_file_storage(settings, tmp_path):
    """Dev storage: records live in a chats/ folder under the data directory."""
    from app.storage import LocalFileStore

    backend = Backend()
    store = LocalFileStore(tmp_path / "data")
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        client.post("/api/session/cookies", json={"cookies": COOKIE_BODY})
        client.post("/api/session/verify")
        chat_id = client.post("/api/chat/send", json={"message": "on disk"}).json()["chat"]["id"]
        assert client.get(f"/api/chat/{chat_id}").json()["messages"][1]["text"] == "reply 1: on disk"
    assert (tmp_path / "data" / "chats" / f"{chat_id}.json").is_file()
