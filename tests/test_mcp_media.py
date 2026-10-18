"""MCP tools for pictures, one-off questions and deep research.

Gemini is a fake client and storage is in memory. No network.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gemini_webapi import Citation, DeepResearchDocument, DeepResearchPlan, GeminiError

from app import research_service
from app.config import Settings
from app.gemini_plan import SavedPlan
from app.main import create_app
from tests.test_api_key import KEY
from tests.test_mcp import MCP_HEADERS

ADMIN_USER = "admin"
ADMIN_PASS = "correct-horse-battery"
COOKIES = "__Secure-1PSID=psid-value-0123456789abcdefghij; __Secure-1PSIDTS=ts-value-0123456789abcdefgh"
PNG = b"\x89PNG\r\n\x1a\n" + b"picture-bytes"
BEARER = {"Authorization": f"Bearer {KEY}"}


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
    def __init__(self) -> None:
        self.text = "Here is the answer."
        self.images: list[bytes] = [PNG]
        self.calls: list[dict] = []  # each Gemini request: its prompt and the files sent with it
        self.models: list[str | None] = []  # the model each Gemini request asked for
        self.plan_error: Exception | None = None
        self.stream_cid = ""
        self.start_error: Exception | None = None
        self.starts = 0
        self.ready_after = 2  # the report is ready on the second check
        self.checks = 0


class FakeImage:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def save(self, path="temp", filename=None, **_):
        out = Path(path) / Path(filename or "image").name
        out.write_bytes(self.data)
        return str(out)


class FakeOutput:
    def __init__(self, text, images=None, document=None) -> None:
        self.text = text
        self.images = images or []
        self.deep_research_document = document


class FakeModel:
    model_name = "gemini-flash"
    display_name = "Flash"
    is_available = True


class FakeChat:
    def __init__(self, metadata=None, cid="", model=None) -> None:
        self.metadata = list(metadata or [])
        self.cid = cid
        self.model = model


class FakeClient:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def init(self, timeout=45.0, auto_refresh=True, **_):
        return None

    def list_models(self):
        return [FakeModel()]

    async def generate_content(self, prompt, files=None, model=None, **_):
        sent = [(Path(f).suffix, Path(f).read_bytes()) for f in files or []]
        self._backend.calls.append({"prompt": prompt, "files": sent})
        self._backend.models.append(model)
        return FakeOutput(self._backend.text, [FakeImage(data) for data in self._backend.images])

    async def create_deep_research_plan(self, prompt, chat=None, model=None):
        if self._backend.plan_error is not None:
            if chat is not None and self._backend.stream_cid:
                chat.cid = self._backend.stream_cid  # the chat id arrived before the stream was cut
            raise self._backend.plan_error
        return DeepResearchPlan(
            title="Plan for the topic",
            steps=["Survey sources", "Compare findings"],
            eta_text="about 8 minutes",
            query=prompt,
            cid="c_research",
            metadata=["c_research", "r_1", "rc_1"],
            confirm_prompt="Start research",
            response_text="Here is my plan.",
        )

    def start_chat(self, metadata=None, cid="", model=None, **_):
        return FakeChat(metadata=metadata, cid=cid, model=model)

    async def start_deep_research(self, plan, chat=None, confirm_prompt=None):
        if self._backend.start_error is not None:
            raise self._backend.start_error
        self._backend.starts += 1
        return FakeOutput("Research started.")

    async def fetch_latest_chat_response(self, cid, limit=5, match=None):
        self._backend.checks += 1
        if self._backend.checks < self._backend.ready_after:
            return FakeOutput("Still researching.", document=DeepResearchDocument(id="d1", content=""))
        document = DeepResearchDocument(
            id="d1",
            title="Field report",
            content="# Findings\n\nThe answer is 42 [cite: 1].",
            sources=[Citation(id=1, title="Source A", url="https://example.com/a")],
        )
        return FakeOutput("I've completed your research.", document=document)

    async def close(self, delay: float = 0) -> None:
        return None


def build(tmp_path, backend):
    settings = Settings(
        admin_username=ADMIN_USER,
        admin_password=ADMIN_PASS,
        secret_key="k" * 48,
        cookie_secure=False,
        data_dir=tmp_path,
        api_key=KEY,
    )
    return create_app(settings, gemini_factory=lambda psid, psidts: FakeClient(backend), store=MemoryStore())


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Tests check Gemini on every call unless a test sets a gap of its own
    monkeypatch.setattr(research_service, "PLAN_CHECK_SECONDS", 0)
    monkeypatch.setattr(research_service, "REPORT_CHECK_SECONDS", 0)
    backend = Backend()
    with TestClient(build(tmp_path, backend)) as client:
        assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
        assert client.post("/api/session/cookies", json={"cookies": COOKIES}).status_code == 200
        assert client.post("/api/session/verify").json()["verified"] is True
        client.cookies.clear()  # the calls below use the key alone
        yield client, backend


def rpc(client, method, params, req_id=1):
    headers = {**MCP_HEADERS, **BEARER}
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    return client.post("/mcp", headers=headers, json=body).json()["result"]


def call(client, name, arguments):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments})


def status(client, research_id):
    return call(client, "gemini_research_status", {"research_id": research_id})["structuredContent"]


def text_of(result):
    return result["content"][0]["text"]


def test_the_new_tools_are_listed(env):
    client, _ = env
    names = {tool["name"] for tool in rpc(client, "tools/list", {})["tools"]}
    assert {
        "gemini_ask",
        "gemini_analyze_image",
        "gemini_generate_image",
        "gemini_start_deep_research",
        "gemini_research_status",
    } <= names


def test_ask_returns_the_text_and_saves_no_chat(env):
    client, backend = env
    backend.text = '{"caption": "Hi"}'
    result = call(client, "gemini_ask", {"prompt": "Write a caption"})
    assert result["isError"] is False
    assert text_of(result) == '{"caption": "Hi"}'
    assert backend.calls[-1] == {"prompt": "Write a caption", "files": []}
    assert client.get("/api/chat", headers=BEARER).json()["chats"] == []


def test_analyze_image_sends_the_picture_with_its_real_type(env):
    client, backend = env
    result = call(
        client,
        "gemini_analyze_image",
        {"prompt": "Is there a cup?", "image_base64": base64.b64encode(PNG).decode()},
    )
    assert result["isError"] is False
    assert backend.calls[-1]["files"] == [(".png", PNG)]
    assert text_of(result) == backend.text


def test_ask_sends_attached_documents_under_their_names(env):
    client, backend = env
    report = "# Report\n\nFacts about coffee."
    result = call(
        client,
        "gemini_ask",
        {
            "prompt": "Analyse the attached report.",
            "attachments": [{"name": "research-report.md", "content_base64": base64.b64encode(report.encode()).decode()}],
        },
    )
    assert result["isError"] is False
    assert backend.calls[-1] == {"prompt": "Analyse the attached report.", "files": [(".md", report.encode())]}


def test_attachments_are_checked_before_gemini_is_called(env):
    client, backend = env
    good = base64.b64encode(b"text").decode()
    bad = [
        {"name": "../secret.md", "content_base64": good},
        {"name": "picture.exe", "content_base64": good},
        {"name": "", "content_base64": good},
        {"name": "notes.md", "content_base64": "not base64!"},
        {"name": "notes.md", "content_base64": ""},
    ]
    for item in bad:
        assert call(client, "gemini_ask", {"prompt": "x", "attachments": [item]})["isError"] is True, item
    too_many = [{"name": f"doc{i}.md", "content_base64": good} for i in range(4)]
    assert call(client, "gemini_ask", {"prompt": "x", "attachments": too_many})["isError"] is True
    same_name = [{"name": "a.md", "content_base64": good}, {"name": "a.md", "content_base64": good}]
    assert call(client, "gemini_ask", {"prompt": "x", "attachments": same_name})["isError"] is True
    assert backend.calls == []


def test_the_model_reaches_gemini_for_questions_and_picture_checks(env):
    client, backend = env
    call(client, "gemini_ask", {"prompt": "a", "model": "gemini-flash"})
    call(client, "gemini_analyze_image", {"prompt": "b", "image_base64": base64.b64encode(PNG).decode(), "model": "gemini-pro"})
    call(client, "gemini_ask", {"prompt": "c"})
    assert backend.models == ["gemini-flash", "gemini-pro", None]


def test_analyze_image_refuses_what_is_not_a_picture_before_calling_gemini(env):
    client, backend = env
    text_file = call(
        client,
        "gemini_analyze_image",
        {"prompt": "x", "image_base64": base64.b64encode(b"just some text").decode()},
    )
    assert text_file["isError"] is True
    assert "Only PNG" in text_of(text_file)
    broken = call(client, "gemini_analyze_image", {"prompt": "x", "image_base64": "not base64!"})
    assert broken["isError"] is True
    assert "not valid base64" in text_of(broken)
    assert backend.calls == []


def test_generate_image_returns_the_picture_and_archives_it(env):
    client, backend = env
    result = call(client, "gemini_generate_image", {"prompt": "a red cube"})
    assert result["isError"] is False
    block = result["content"][0]
    assert block["type"] == "image"
    assert block["mimeType"] == "image/png"
    assert base64.b64decode(block["data"]) == PNG
    assert "a red cube" in backend.calls[-1]["prompt"]
    assert len(client.get("/api/image", headers=BEARER).json()["images"]) == 1


def test_generate_image_without_a_picture_is_a_tool_error(env):
    client, backend = env
    backend.images = []
    result = call(client, "gemini_generate_image", {"prompt": "a red cube"})
    assert result["isError"] is True
    assert "no image" in text_of(result)


def test_research_runs_from_plan_to_report_through_the_status_tool(env):
    client, backend = env
    started = call(client, "gemini_start_deep_research", {"prompt": "Battery recycling in India"})["structuredContent"]
    rid = started["research_id"]
    assert started["status"] == "planned"
    assert backend.starts == 0  # the plan alone starts nothing
    assert status(client, rid)["status"] == "running"  # the first status call starts it
    assert backend.starts == 1
    assert status(client, rid)["status"] == "running"  # the first report check: not ready yet
    done = call(client, "gemini_research_status", {"research_id": rid})
    body = json.loads(text_of(done))  # the text form is what the other app reads
    assert body["status"] == "done"
    assert body["report"].startswith("# Findings")
    assert body["sources"][0]["url"] == "https://example.com/a"


def test_a_plan_cut_short_is_read_later_by_the_status_tool(env, monkeypatch):
    client, backend = env
    backend.plan_error = GeminiError("stream cut")
    backend.stream_cid = "c_cut"

    async def saved_plan(_client, _cid, limit=10):
        plan = DeepResearchPlan(title="Recovered plan", steps=["Survey"], eta_text="about 8 minutes")
        return SavedPlan(plan=plan, rid="r_saved", rcid="rc_saved")

    monkeypatch.setattr(research_service, "read_saved_plan", saved_plan)
    started = call(client, "gemini_start_deep_research", {"prompt": "Solar in Kenya"})["structuredContent"]
    assert started["status"] == "planning"
    rid = started["research_id"]
    assert status(client, rid)["status"] == "planned"  # the plan is read from the saved chat
    assert backend.starts == 0
    assert status(client, rid)["status"] == "running"
    assert backend.starts == 1


def test_a_refused_start_is_reported_on_the_research(env):
    client, backend = env
    started = call(client, "gemini_start_deep_research", {"prompt": "Ocean plastics"})["structuredContent"]
    backend.start_error = GeminiError("confirmation refused")
    view = status(client, started["research_id"])
    assert view["status"] == "failed"
    assert "confirmation refused" in view["error"]


def test_research_ids_are_checked(env):
    client, _ = env
    unknown = call(client, "gemini_research_status", {"research_id": "0123456789abcdef"})
    assert unknown["isError"] is True
    assert "not found" in text_of(unknown)
    malformed = call(client, "gemini_research_status", {"research_id": "../secret"})
    assert malformed["isError"] is True
    assert "not valid" in text_of(malformed)
