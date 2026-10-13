"""Deep research: make a plan, confirm it, then poll for the report in short requests.

Gemini does the research on its side and can take 5 to 10 minutes. One Vercel
request cannot wait that long, so the job is saved after each step, and each
poll reads the chat once and returns. The page polls until the report arrives.
The report lives in the chat, so it survives even if this server restarts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from gemini_webapi import DeepResearchPlan

from .cookie_store import utc_now
from .gemini_service import GeminiNotReady, GeminiService, describe_failure
from .records import FeatureError, JsonStore, new_id

log = logging.getLogger("gemini_webui")

RESEARCH_INDEX = "research/index.json"
MAX_PROMPT_CHARS = 1000
# Gemini can take minutes to write a plan or confirm a start, so the request waits for
# its reply. This must stay under the function limit in vercel.json (300 s), with room
# for the client's connection step (up to 45 s) and saving the job.
PLAN_TIMEOUT_SECONDS = 240.0
START_TIMEOUT_SECONDS = 240.0
POLL_TIMEOUT_SECONDS = 30.0  # one chat read
STARTING_STALE_SECONDS = 120  # a "starting" job this old was interrupted and may be retried
MAX_RUNNING_SECONDS = 90 * 60


def record_name(job_id: str) -> str:
    return f"research/{job_id}.json"


def _report_ready(output: Any) -> bool:
    document = getattr(output, "deep_research_document", None)
    return bool(document and document.ready)


def _seconds_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


def _start_failure_message(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "Gemini did not confirm the start in time. It may have started; check the Gemini app before retrying."
    if isinstance(exc, GeminiNotReady):
        return str(exc)
    return describe_failure(exc)


@contextmanager
def _logged_stream(client: Any) -> Iterator[None]:
    """Turn on the library's verbose log for one call: the HTTP status, and whether the
    full reply arrived. The log does not contain cookie values. Turned back off after."""
    previous = getattr(client, "verbose", False)
    client.verbose = True
    try:
        yield
    finally:
        client.verbose = previous


def _summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "prompt": job["prompt"],
        "title": job["title"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


class ResearchService:
    def __init__(self, gemini: GeminiService, records: JsonStore) -> None:
        self._gemini = gemini
        self._records = records

    async def list_jobs(self) -> list[dict[str, Any]]:
        return await self._records.index(RESEARCH_INDEX)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        job = await self._records.get(record_name(job_id))
        if job is None:
            raise FeatureError(404, "Research not found.")
        return job

    async def delete_job(self, job_id: str) -> None:
        await self.get_job(job_id)  # raises 404 for unknown ids
        await self._records.delete(record_name(job_id))
        await self._records.remove_from_index(RESEARCH_INDEX, job_id)

    async def create(self, prompt: str, model: str | None) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise FeatureError(422, "Research topic is empty.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise FeatureError(422, f"Research topic is longer than {MAX_PROMPT_CHARS} characters.")

        client = await self._gemini.require_client()
        started = time.monotonic()
        try:
            with _logged_stream(client):
                plan = await asyncio.wait_for(
                    client.create_deep_research_plan(prompt, model=model or None),
                    PLAN_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            log.warning("research plan failed after %.0f s: %s", time.monotonic() - started, exc)
            raise
        now = utc_now()
        job: dict[str, Any] = {
            "id": new_id(),
            "prompt": prompt,
            "model": model or None,
            "status": "planned",
            "title": plan.title or prompt[:80],
            "plan": plan.model_dump(mode="json"),
            "report": None,
            "report_title": None,
            "sources": [],
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "checked_at": None,
        }
        await self._save(job)
        return job

    async def start(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        status = job["status"]
        if status in ("running", "done"):
            raise FeatureError(409, "This research has already started.")
        if status == "starting" and _seconds_since(job["updated_at"]) < STARTING_STALE_SECONDS:
            raise FeatureError(409, "The start is already in progress. Wait a moment, then refresh.")

        job["status"] = "starting"
        job["error"] = None
        job["updated_at"] = utc_now()
        await self._save(job)
        started = time.monotonic()
        try:
            client = await self._gemini.require_client()
            plan = DeepResearchPlan.model_validate(job["plan"])
            with _logged_stream(client):
                await asyncio.wait_for(client.start_deep_research(plan), START_TIMEOUT_SECONDS)
        except Exception as exc:
            log.warning("research start failed after %.0f s: %s", time.monotonic() - started, exc)
            job["status"] = "failed"
            job["error"] = _start_failure_message(exc)
            job["updated_at"] = utc_now()
            await self._save(job)
            raise

        now = utc_now()
        job.update(
            status="running",
            plan=plan.model_dump(mode="json"),
            started_at=now,
            checked_at=None,
            updated_at=now,
        )
        await self._save(job)
        return job

    async def poll(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job["status"] != "running":
            return job  # nothing to check on Gemini's side

        if _seconds_since(job["started_at"]) > MAX_RUNNING_SECONDS:
            return await self._fail(job, "Research did not finish within 90 minutes.")

        plan = DeepResearchPlan.model_validate(job["plan"])
        cid = plan.cid or (plan.metadata[0] if plan.metadata else None)
        if not cid:
            return await self._fail(job, "The research has no chat id. It may not have started.")

        client = await self._gemini.require_client()
        output = await asyncio.wait_for(
            client.fetch_latest_chat_response(cid, limit=10, match=_report_ready),
            POLL_TIMEOUT_SECONDS,
        )
        now = utc_now()
        job["checked_at"] = now
        if output is not None and _report_ready(output):
            document = output.deep_research_document
            job.update(
                status="done",
                report=document.markdown,
                report_title=document.title,
                sources=[source.model_dump(mode="json") for source in document.sources],
            )
        job["updated_at"] = now
        await self._save(job)
        return job

    async def _fail(self, job: dict[str, Any], message: str) -> dict[str, Any]:
        job["status"] = "failed"
        job["error"] = message
        job["updated_at"] = utc_now()
        await self._save(job)
        return job

    async def _save(self, job: dict[str, Any]) -> None:
        await self._records.put(record_name(job["id"]), job)
        await self._records.upsert_index(RESEARCH_INDEX, _summary(job))
