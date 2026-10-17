"""MCP tools for Gemini chats. They call the same chat service as the REST routes.

Mounted at /mcp with the app's API key. Each tool returns a plain dict; a failure
comes back to the MCP client as a tool error with the same short message the REST
routes return. Image and deep research tools are not here yet.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .chat_service import ChatService


def build_mcp(chats: ChatService) -> FastMCP:
    mcp = FastMCP(
        "gemini-archive",
        instructions=(
            "Send messages to Gemini and read the replies. gemini_send returns a chat_id. "
            "If status is 'waiting', call gemini_get_chat with that chat_id until status is 'answered'. "
            "Pass the same chat_id to gemini_send for a follow-up in the same conversation."
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

    return mcp
