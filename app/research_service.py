"""Deep research: make a plan, confirm it, then poll for the report in short requests.

Gemini does the research on its side and can take 5 to 10 minutes. One Vercel
request cannot wait that long, so the job is saved after each step, and each
poll reads the chat once and returns. The page polls until the report arrives.
The report lives in the chat, so it survives even if this server restarts.

The plan works the same way. The first request waits on the live stream for a
short time. If the stream ends without a plan, the job is saved as "planning" and
each poll reads the saved chat once, until the plan appears or the wait runs out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from gemini_webapi import (
    APIError,
    DeepResearchPlan,
    GeminiError,
    ModelInvalidError,
    TemporarilyBlockedError,
    UsageLimitExceededError,
)
from gemini_webapi import TimeoutError as GeminiTimeoutError

from .checks import check_is_due, error_text, seconds_since
from .cookie_store import utc_now
from .gemini_plan import read_saved_plan
from .gemini_service import GeminiNotReady, GeminiService, describe_failure
from .records import FeatureError, JsonStore, new_id

log = logging.getLogger("gemini_webui")

RESEARCH_INDEX = "research/index.json"
MAX_PROMPT_CHARS = 1000
# The first plan request waits this long on the live stream. Under the function limit in
# vercel.json (300 s), with room for connecting (up to 45 s) and saving the job.
PLAN_STREAM_SECONDS = 150.0
# A job whose plan has not appeared this long after it was made is given up.
PLAN_WAIT_SECONDS = 30 * 60
# Checks of Gemini are spaced out, so several tabs or API clients cannot make one job's
# checks more frequent. A failed check doubles the gap, up to MAX_CHECK_GAP_SECONDS.
PLAN_CHECK_SECONDS = 20.0
REPORT_CHECK_SECONDS = 60.0
# Errors that re-reading the chat cannot fix, so a plan is not waited for after them
_NO_RECOVERY = (
    GeminiTimeoutError,
    UsageLimitExceededError,
    ModelInvalidError,
    TemporarilyBlockedError,
)
START_TIMEOUT_SECONDS = 240.0
POLL_TIMEOUT_SECONDS = 30.0  # one chat read
STARTING_STALE_SECONDS = 120  # a "starting" job this old was interrupted and may be retried
MAX_RUNNING_SECONDS = 90 * 60


def record_name(job_id: str) -> str:
    return f"research/{job_id}.json"


def _report_ready(output: Any) -> bool:
    document = getattr(output, "deep_research_document", None)
    return bool(document and document.ready)


def _start_failure_message(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "Gemini did not confirm the start in time. It may have started; check the Gemini app before retrying."
    if isinstance(exc, GeminiNotReady):
        return str(exc)
    return describe_failure(exc)


def _can_wait_for_saved_chat(exc: Exception, chat: Any) -> bool:
    """The stream ended without a plan, but the chat exists, so the plan may still be saved."""
    if not chat.cid or isinstance(exc, _NO_RECOVERY):
        return False
    return isinstance(exc, (asyncio.TimeoutError, GeminiError, APIError))


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

    async def create(self, prompt: str, model: str | None, stream_seconds: float | None = None) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise FeatureError(422, "Research topic is empty.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise FeatureError(422, f"Research topic is longer than {MAX_PROMPT_CHARS} characters.")

        client = await self._gemini.require_client()
        started = time.monotonic()
        # The chat is created here, not inside the library, so its id is known even when
        # the stream is cut. Gemini finishes the planning turn on its side either way.
        chat = client.start_chat(model=model or None)
        budget = PLAN_STREAM_SECONDS if stream_seconds is None else stream_seconds
        try:
            with _logged_stream(client):
                plan = await asyncio.wait_for(
                    client.create_deep_research_plan(prompt, chat=chat),
                    budget,
                )
        except Exception as exc:
            if not _can_wait_for_saved_chat(exc, chat):
                log.warning("research plan failed after %.0f s: %s", time.monotonic() - started, exc)
                raise
            log.warning(
                "research plan not in the live stream after %.0f s (%s); the page will check the saved chat",
                time.monotonic() - started,
                exc,
            )
            return await self._save_new_job(
                prompt,
                model,
                plan=None,
                chat={"cid": chat.cid, "metadata": list(chat.metadata)},
            )
        return await self._save_new_job(prompt, model, plan=plan, chat=None)

    async def _save_new_job(
        self,
        prompt: str,
        model: str | None,
        plan: DeepResearchPlan | None,
        chat: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = utc_now()
        job: dict[str, Any] = {
            "id": new_id(),
            "prompt": prompt,
            "model": model or None,
            "status": "planned" if plan else "planning",
            "title": (plan.title if plan else None) or prompt[:80],
            "plan": plan.model_dump(mode="json") if plan else None,
            # While the plan is pending: the chat id and metadata to read it back from
            "chat": chat,
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

    async def start(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        job = await self.get_job(job_id)
        status = job["status"]
        if status == "planning":
            raise FeatureError(409, "Gemini is still writing the plan. Wait for it to appear, then start.")
        if not job.get("plan"):
            raise FeatureError(409, "This topic has no plan. Make a new plan from the topic.")
        if status in ("running", "done"):
            raise FeatureError(409, "This research has already started.")
        if status == "starting" and seconds_since(job["updated_at"]) < STARTING_STALE_SECONDS:
            raise FeatureError(409, "The start is already in progress. Wait a moment, then refresh.")

        job["status"] = "starting"
        job["error"] = None
        job["updated_at"] = utc_now()
        await self._save(job)
        started = time.monotonic()
        try:
            client = await self._gemini.require_client()
            plan = DeepResearchPlan.model_validate(job["plan"])
            # The confirmation must continue the plan's chat with the same model. Without
            # this, Gemini gets the confirmation with the default model and can reject it.
            chat = client.start_chat(
                metadata=list(plan.metadata),
                cid=plan.cid or "",
                model=job.get("model") or None,
            )
            with _logged_stream(client):
                await asyncio.wait_for(
                    client.start_deep_research(plan, chat=chat),
                    START_TIMEOUT_SECONDS if timeout is None else timeout,
                )
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
            check_failures=0,
            last_check_error=None,
            updated_at=now,
        )
        await self._save(job)
        return job

    async def poll(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job["status"] == "planning":
            return await self._check_plan(job)
        if job["status"] != "running":
            return job  # nothing to check on Gemini's side

        if seconds_since(job["started_at"]) > MAX_RUNNING_SECONDS:
            return await self._fail(job, "Research did not finish within 90 minutes.")
        if not check_is_due(job, REPORT_CHECK_SECONDS):
            return job  # checked recently; Gemini is not asked again yet

        plan = DeepResearchPlan.model_validate(job["plan"])
        cid = plan.cid or (plan.metadata[0] if plan.metadata else None)
        if not cid:
            return await self._fail(job, "The research has no chat id. It may not have started.")

        try:
            client = await self._gemini.require_client()
            output = await asyncio.wait_for(
                client.fetch_latest_chat_response(cid, limit=10, match=_report_ready),
                POLL_TIMEOUT_SECONDS,
            )
        except (GeminiNotReady, asyncio.TimeoutError, GeminiError, APIError) as exc:
            return await self._record_failure(job, exc)
        job["check_failures"] = 0
        job["last_check_error"] = None
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

    async def _check_plan(self, job: dict[str, Any]) -> dict[str, Any]:
        """Read the job's saved chat once. Adopt the plan if Gemini has written it.

        The chat is read before the wait is judged. A job nobody polled for a long time
        still gets its plan if Gemini wrote one.
        """
        if not check_is_due(job, PLAN_CHECK_SECONDS):
            return job  # checked recently; Gemini is not asked again yet

        chat = job["chat"]
        saved = None
        failure: Exception | None = None
        try:
            client = await self._gemini.require_client()
            saved = await asyncio.wait_for(read_saved_plan(client, chat["cid"]), POLL_TIMEOUT_SECONDS)
        except (GeminiNotReady, asyncio.TimeoutError, GeminiError, APIError) as exc:
            failure = exc

        if saved is None:
            if seconds_since(job["created_at"]) > PLAN_WAIT_SECONDS:
                log.warning("no research plan after %d s; giving up on job %s", PLAN_WAIT_SECONDS, job["id"])
                detail = f" Last check: {error_text(failure)}." if failure else ""
                return await self._fail(
                    job,
                    f"Gemini did not finish the plan within 30 minutes. Try the topic again.{detail}",
                )
            if failure is not None:
                return await self._record_failure(job, failure)
            # Gemini answered and the plan is not there yet
            job["check_failures"] = 0
            job["last_check_error"] = None
            job["checked_at"] = utc_now()
            job["updated_at"] = job["checked_at"]
            await self._save(job)
            return job

        # Keep the chat's ids as the library would have set them, so the confirmation
        # continues this same turn.
        metadata = list(chat["metadata"])
        metadata += [None] * max(0, 3 - len(metadata))
        if saved.rid:
            metadata[1] = saved.rid
        if saved.rcid:
            metadata[2] = saved.rcid
        plan = saved.plan
        plan.cid = chat["cid"]
        plan.metadata = metadata
        log.warning("research plan read from the saved chat for job %s", job["id"])
        job.update(
            status="planned",
            title=plan.title or job["prompt"][:80],
            plan=plan.model_dump(mode="json"),
            chat=None,
        )
        await self._save(job)
        return job

    async def _record_failure(self, job: dict[str, Any], exc: Exception) -> dict[str, Any]:
        """A check Gemini could not answer. The next check waits twice as long."""
        job["check_failures"] = int(job.get("check_failures") or 0) + 1
        job["last_check_error"] = error_text(exc)
        now = utc_now()
        job["checked_at"] = now
        job["updated_at"] = now
        log.warning(
            "research check failed for job %s (%d in a row): %s",
            job["id"],
            job["check_failures"],
            job["last_check_error"],
        )
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
