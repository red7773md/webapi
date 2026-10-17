"""MCP tools for Gemini. They call the same services as the REST routes.

Mounted at /mcp with the app's API key. Each tool returns a plain value; a failure comes
back to the MCP client as a tool error with the same short message the REST routes return.

Calling apps give up after about 120 seconds, so each tool keeps its Gemini waits well
under that. The waits are passed to the services as the budgets below.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings

from .ask_service import AskService, decode_image
from .chat_service import ChatService
from .image_service import ImageService
from .records import ID_PATTERN, FeatureError
from .research_service import ResearchService

# Seconds. Connecting to Gemini (up to 45 s) can come before either wait.
PLAN_STREAM_SECONDS = 60.0  # if the plan's live stream is cut here, the status tool reads the saved chat
START_SECONDS = 70.0  # confirming the research; slower than this and the job is marked failed


def research_view(job: dict[str, Any]) -> dict[str, Any]:
    """A research job as a tool result. Status is planning, planned, starting, running, done or failed."""
    view: dict[str, Any] = {"research_id": job["id"], "status": job["status"], "title": job["title"]}
    if job["status"] == "done":
        view.update(report=job["report"], report_title=job["report_title"], sources=job["sources"])
    if job["status"] == "failed":
        view["error"] = job["error"] or "Research failed."
    return view


def check_research_id(research_id: str) -> str:
    if not re.fullmatch(ID_PATTERN, research_id):
        raise FeatureError(422, "research_id is not valid.")
    return research_id


async def _start(research: ResearchService, research_id: str) -> dict[str, Any]:
    try:
        return await research.start(research_id, timeout=START_SECONDS)
    except FeatureError as exc:
        if exc.status_code == 409:  # another call is already starting it
            return await research.get_job(research_id)
        raise
    except Exception:
        job = await research.get_job(research_id)
        if job["status"] == "failed":  # the failure is saved on the job, so report it as the job's status
            return job
        raise


async def advance(research: ResearchService, research_id: str) -> dict[str, Any]:
    """One step toward the report. Each request makes at most one call to Gemini."""
    job = await research.get_job(research_id)
    if job["status"] in ("planning", "running"):
        return await research.poll(research_id)
    if job["status"] in ("planned", "starting"):
        return await _start(research, research_id)
    return job


def build_mcp(
    chats: ChatService,
    images: ImageService,
    research: ResearchService,
    ask: AskService,
) -> FastMCP:
    mcp = FastMCP(
        "gemini-archive",
        instructions=(
            "Send messages to Gemini and read the replies. gemini_send returns a chat_id. "
            "If status is 'waiting', call gemini_get_chat with that chat_id until status is 'answered'. "
            "Pass the same chat_id to gemini_send for a follow-up in the same conversation. "
            "gemini_ask gives a one-off text reply without saving a chat. gemini_generate_image returns a picture; "
            "gemini_analyze_image answers a question about a picture. Deep research: call "
            "gemini_start_deep_research, then gemini_research_status with its research_id until status is 'done' or 'failed'."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        # The SDK's Host check exists to stop DNS rebinding against unauthenticated local servers.
        # This endpoint needs the app's API key on every request, and the public host name
        # (for example a Vercel domain) would otherwise be refused.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def gemini_send(message: str, chat_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        """Send a message to Gemini. Pass chat_id to continue a chat. Returns chat_id, status and any reply."""
        return await chats.send(chat_id=chat_id, message=message, model=model, attachments=[])

    @mcp.tool()
    async def gemini_get_chat(chat_id: str) -> dict[str, Any]:
        """Read a chat: its status (answered, waiting or failed) and all messages so far."""
        return await chats.read_chat(chat_id)

    @mcp.tool()
    async def gemini_list_chats() -> dict[str, Any]:
        """List saved chats, newest first."""
        return {"chats": await chats.list_chats()}

    @mcp.tool()
    async def gemini_delete_chat(chat_id: str) -> dict[str, bool]:
        """Delete a chat from the archive. Gemini's own history is not touched."""
        await chats.delete_chat(chat_id)
        return {"ok": True}

    @mcp.tool()
    async def gemini_ask(prompt: str, model: str | None = None) -> str:
        """Ask Gemini one question and get its text reply. No chat is saved to the archive."""
        return await ask.ask(prompt, model=model)

    @mcp.tool()
    async def gemini_analyze_image(prompt: str, image_base64: str) -> str:
        """Ask Gemini about a picture. image_base64 is the picture as base64 (PNG, JPEG, WEBP or GIF). Returns the text reply."""
        return await ask.ask(prompt, image=decode_image(image_base64))

    @mcp.tool()
    async def gemini_generate_image(prompt: str, model: str | None = None) -> Image:
        """Generate a picture from a prompt and return it. The picture is also kept in the image archive."""
        result = await images.generate(prompt=prompt, model=model)
        if not result["images"]:
            raise FeatureError(502, "Gemini answered with no image.")
        record, data = await images.get_image(result["images"][0]["id"])
        return Image(data=data, format=record["ext"])

    @mcp.tool()
    async def gemini_start_deep_research(prompt: str, model: str | None = None) -> dict[str, Any]:
        """Start a deep research topic. Gemini writes a plan first. Returns research_id and status 'planned', or 'planning' while Gemini is still writing it. Then call gemini_research_status with the research_id until the status is 'done' or 'failed'. Research takes about 5 to 10 minutes."""
        job = await research.create(prompt, model, stream_seconds=PLAN_STREAM_SECONDS)
        return research_view(job)

    @mcp.tool()
    async def gemini_research_status(research_id: str) -> dict[str, Any]:
        """Check a deep research topic and move it one step on. The first call after the plan is ready starts the research; later calls return the report. Status is planning, planned, starting, running, done or failed. Status 'done' includes the report and its sources."""
        job = await advance(research, check_research_id(research_id))
        return research_view(job)

    return mcp
