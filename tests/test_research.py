"""Deep research: plan, confirm, short polls, report, retry and plain errors.

Gemini is a fake client and storage is an in-memory KeyValueStore. No network.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient
from gemini_webapi import Citation, DeepResearchDocument, DeepResearchPlan, GeminiError

from app import gemini_service, research_service
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
    def __init__(self) -> None:
        self.plans: list[str] = []
        self.started: list[str] = []
        self.polls = 0
        self.ready_after = 3  # the report appears on the third check
        self.plan_error: Exception | None = None
        self.plan_delay = 0.0
        self.start_error: Exception | None = None
        self.start_delay = 0.0
        self.init_calls: list[dict] = []  # every connect, so tests can read the budget
        self.verbose_seen: list[tuple[str, bool]] = []  # verbose flag seen by each call


class FakeOutput:
    def __init__(self, text, document=None) -> None:
        self.text = text
        self.deep_research_document = document


class FakeModel:
    model_name = "gemini-flash"
    display_name = "Flash"
    is_available = True


class FakeClient:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def init(self, timeout=45.0, auto_refresh=True, **kwargs):
        self._backend.init_calls.append({"timeout": timeout, **kwargs})
        return None

    def list_models(self):
        return [FakeModel()]

    async def create_deep_research_plan(self, prompt, chat=None, model=None):
        self._backend.verbose_seen.append(("plan", getattr(self, "verbose", False)))
        if self._backend.plan_delay:
            await asyncio.sleep(self._backend.plan_delay)
        if self._backend.plan_error is not None:
            raise self._backend.plan_error
        self._backend.plans.append(prompt)
        return DeepResearchPlan(
            title=f"Plan for {prompt[:20]}",
            steps=["Survey sources", "Compare findings"],
            eta_text="about 8 minutes",
            query=prompt,
            cid="c_research",
            metadata=["c_research", "r_1", "rc_1"],
            confirm_prompt="Start research",
            response_text="Here is my plan.",
        )

    async def start_deep_research(self, plan, chat=None, confirm_prompt=None):
        self._backend.verbose_seen.append(("start", getattr(self, "verbose", False)))
        if self._backend.start_delay:
            await asyncio.sleep(self._backend.start_delay)
        if self._backend.start_error is not None:
            error, self._backend.start_error = self._backend.start_error, None
            raise error
        self._backend.started.append(plan.cid)
        return FakeOutput("Research started.")

    async def fetch_latest_chat_response(self, cid, limit=5, match=None):
        self._backend.polls += 1
        if self._backend.polls < self._backend.ready_after:
            return FakeOutput("Still researching.", DeepResearchDocument(id="d1", content=""))
        document = DeepResearchDocument(
            id="d1",
            title="Field report",
            content="# Findings\n\nThe answer is 42 [cite: 1].",
            sources=[Citation(id=1, title="Source A", url="https://example.com/a")],
        )
        return FakeOutput("I've completed your research.", document)

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
    return create_app(settings, gemini_factory=lambda psid, psidts: FakeClient(backend), store=store)


@pytest.fixture
def env(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        assert client.post("/api/session/cookies", json={"cookies": COOKIE_BODY}).status_code == 200
        assert client.post("/api/session/verify").json()["verified"] is True
        yield client, backend, store


def new_plan(client, topic="Battery recycling in India"):
    resp = client.post("/api/research", json={"prompt": topic})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_plan_confirm_and_poll_until_the_report_arrives(env):
    client, backend, _ = env
    job = new_plan(client)
    job_id = job["id"]
    assert job["status"] == "planned"
    assert job["plan"]["steps"] == ["Survey sources", "Compare findings"]
    assert job["title"].startswith("Plan for Battery")

    # A planned job is only read back; Gemini is not asked anything
    assert client.get(f"/api/research/{job_id}").json()["status"] == "planned"
    assert backend.polls == 0

    started = client.post(f"/api/research/{job_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert backend.started == ["c_research"]

    for _ in range(2):  # not ready yet: each poll is one short check
        polled = client.get(f"/api/research/{job_id}").json()
        assert polled["status"] == "running"
        assert polled["report"] is None

    done = client.get(f"/api/research/{job_id}").json()
    assert done["status"] == "done"
    assert done["report_title"] == "Field report"
    assert "# Findings" in done["report"]
    assert "## Sources" in done["report"]
    assert "https://example.com/a" in done["report"]
    assert done["sources"][0]["url"] == "https://example.com/a"

    # Once done, reading again does not call Gemini again
    assert client.get(f"/api/research/{job_id}").json()["status"] == "done"
    assert backend.polls == 3

    listed = client.get("/api/research").json()["jobs"]
    assert [(j["id"], j["status"]) for j in listed] == [(job_id, "done")]


def test_gemini_is_given_the_full_reply_budget_for_planning(env):
    """The plan waits for Gemini's reply, so the client must not give up after 45 s."""
    client, backend, _ = env
    new_plan(client)
    assert backend.init_calls, "the client connected at least once"
    for connect in backend.init_calls:
        assert connect["timeout"] == gemini_service.CLIENT_TIMEOUT_SECONDS
        assert connect["watchdog_timeout"] == gemini_service.CLIENT_TIMEOUT_SECONDS
    assert gemini_service.CLIENT_TIMEOUT_SECONDS >= research_service.PLAN_TIMEOUT_SECONDS
    assert gemini_service.CLIENT_TIMEOUT_SECONDS >= research_service.START_TIMEOUT_SECONDS


def test_plan_and_start_run_with_the_stream_log_on(env):
    client, backend, _ = env
    job_id = new_plan(client)["id"]
    assert client.post(f"/api/research/{job_id}/start").status_code == 200
    assert backend.verbose_seen == [("plan", True), ("start", True)]


def test_a_failed_plan_logs_how_long_gemini_ran(env, caplog):
    client, backend, _ = env
    backend.plan_error = GeminiError("Gemini did not return a deep research plan.")
    with caplog.at_level(logging.WARNING, logger="gemini_webui"):
        assert client.post("/api/research", json={"prompt": "anything"}).status_code == 502
    assert "research plan failed after" in caplog.text
    assert "Gemini did not return a deep research plan" in caplog.text


def test_a_started_research_cannot_be_started_again(env):
    client, _, _ = env
    job_id = new_plan(client)["id"]
    assert client.post(f"/api/research/{job_id}/start").status_code == 200
    again = client.post(f"/api/research/{job_id}/start")
    assert again.status_code == 409
    assert "already started" in again.json()["detail"]


def test_a_refused_start_is_recorded_and_can_be_retried(env):
    client, backend, _ = env
    job_id = new_plan(client)["id"]
    backend.start_error = GeminiError("Gemini refused the confirmation.")
    failed = client.post(f"/api/research/{job_id}/start")
    assert failed.status_code == 502
    assert "Gemini refused the confirmation" in failed.json()["detail"]

    record = client.get(f"/api/research/{job_id}").json()
    assert record["status"] == "failed"
    assert "Gemini refused the confirmation" in record["error"]

    retry = client.post(f"/api/research/{job_id}/start")
    assert retry.status_code == 200
    assert retry.json()["status"] == "running"
    assert retry.json()["error"] is None


def test_a_start_that_times_out_says_it_may_have_started(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(research_service, "START_TIMEOUT_SECONDS", 0.05)
    backend.start_delay = 1.0
    job_id = new_plan(client)["id"]
    resp = client.post(f"/api/research/{job_id}/start")
    assert resp.status_code == 504
    record = client.get(f"/api/research/{job_id}").json()
    assert record["status"] == "failed"
    assert "may have started" in record["error"]


def test_a_failed_plan_is_a_plain_502(env):
    client, backend, _ = env
    backend.plan_error = GeminiError("Gemini did not return a deep research plan.")
    resp = client.post("/api/research", json={"prompt": "anything"})
    assert resp.status_code == 502
    assert "did not return a deep research plan" in resp.json()["detail"]
    assert client.get("/api/research").json()["jobs"] == []


def test_a_slow_plan_gives_a_504(env, monkeypatch):
    client, backend, _ = env
    monkeypatch.setattr(research_service, "PLAN_TIMEOUT_SECONDS", 0.05)
    backend.plan_delay = 1.0
    resp = client.post("/api/research", json={"prompt": "slow topic"})
    assert resp.status_code == 504


def test_research_running_past_the_limit_is_marked_failed(env, monkeypatch):
    client, _, _ = env
    job_id = new_plan(client)["id"]
    client.post(f"/api/research/{job_id}/start")
    monkeypatch.setattr(research_service, "MAX_RUNNING_SECONDS", -1)
    record = client.get(f"/api/research/{job_id}").json()
    assert record["status"] == "failed"
    assert "did not finish within 90 minutes" in record["error"]


def test_not_connected_is_a_409(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        login(client)
        resp = client.post("/api/research", json={"prompt": "anything"})
    assert resp.status_code == 409
    assert "not connected" in resp.json()["detail"]


def test_bad_topics_and_ids_are_refused(env):
    client, _, _ = env
    assert client.post("/api/research", json={"prompt": "   "}).status_code == 422
    assert client.post("/api/research", json={"prompt": "x" * 1001}).status_code == 422
    assert client.get("/api/research/0123456789abcdef").status_code == 404
    assert client.get("/api/research/not-an-id").status_code == 422
    assert client.post("/api/research/0123456789abcdef/start").status_code == 404


def test_delete_removes_the_job(env):
    client, _, _ = env
    job_id = new_plan(client)["id"]
    assert client.delete(f"/api/research/{job_id}").json() == {"ok": True}
    assert client.get(f"/api/research/{job_id}").status_code == 404
    assert client.get("/api/research").json()["jobs"] == []


def test_research_needs_admin_login(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as client:
        assert client.get("/api/research").status_code == 401
        assert client.post("/api/research", json={"prompt": "x"}).status_code == 401


def test_jobs_are_shared_through_the_store(settings):
    backend, store = Backend(), MemoryStore()
    with TestClient(build(settings, backend, store)) as writer:
        login(writer)
        writer.post("/api/session/cookies", json={"cookies": COOKIE_BODY})
        writer.post("/api/session/verify")
        job_id = new_plan(writer)["id"]
        writer.post(f"/api/research/{job_id}/start")
    # A second instance (another serverless invocation) polls the same job
    with TestClient(build(settings, Backend(), store)) as reader:
        login(reader)
        assert reader.get(f"/api/research/{job_id}").json()["status"] == "running"
