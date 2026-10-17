"""The Gemini MCP endpoint at /mcp: the API key is required, and the chat tools call the chat service.

Gemini is a fake client and storage is in memory. No network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_api_key import KEY, build
from tests.test_chat import ADMIN_PASS, ADMIN_USER

COOKIES = "__Secure-1PSID=psid-value-0123456789abcdefghij; __Secure-1PSIDTS=ts-value-0123456789abcdefgh"

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}


def rpc(client, method, params, req_id=1, key=KEY):
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {key}"}
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})


@pytest.fixture
def client(tmp_path):
    """An admin saves the cookies once in the browser way; the MCP calls then use the key alone."""
    with TestClient(build(tmp_path)) as c:
        assert c.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
        assert c.post("/api/session/cookies", json={"cookies": COOKIES}).status_code == 200
        assert c.post("/api/session/verify").json()["verified"] is True
        c.cookies.clear()  # the tests below use the key, not the browser session
        yield c


def test_mcp_needs_the_api_key(client):
    no_key = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert no_key.status_code == 401
    assert "API key" in no_key.json()["detail"]
    assert rpc(client, "ping", {}, key="wrong-key-000000000000000000").status_code == 401


def test_other_paths_are_not_served(client):
    assert client.get("/mcp/other", params={"key": KEY}).status_code == 404


def test_mcp_starts_and_lists_the_gemini_tools(client):
    init = rpc(client, "initialize", INIT)
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "gemini-archive"
    tools = rpc(client, "tools/list", {}, req_id=2).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"gemini_send", "gemini_get_chat", "gemini_list_chats", "gemini_delete_chat"}


def test_mcp_send_returns_the_reply_as_a_tool_result(client):
    rpc(client, "initialize", INIT)
    resp = rpc(client, "tools/call", {"name": "gemini_send", "arguments": {"message": "hello"}}, req_id=3).json()
    result = resp["result"]
    assert result.get("isError") in (False, None)
    data = result.get("structuredContent") or {}
    assert data["status"] == "answered"
    assert data["messages"][-1]["role"] == "model"


def test_mcp_list_and_delete_work_on_a_saved_chat(client):
    rpc(client, "initialize", INIT)
    sent = rpc(client, "tools/call", {"name": "gemini_send", "arguments": {"message": "first"}}, req_id=3).json()["result"]
    chat_id = sent["structuredContent"]["chat"]["id"]
    listed = rpc(client, "tools/call", {"name": "gemini_list_chats", "arguments": {}}, req_id=4).json()["result"]
    assert chat_id in str(listed["structuredContent"])
    deleted = rpc(client, "tools/call", {"name": "gemini_delete_chat", "arguments": {"chat_id": chat_id}}, req_id=5).json()["result"]
    assert deleted["structuredContent"] == {"ok": True}
    missing = rpc(client, "tools/call", {"name": "gemini_get_chat", "arguments": {"chat_id": chat_id}}, req_id=6).json()["result"]
    assert missing["isError"] is True


def test_the_admin_page_gets_the_mcp_path_and_key_only_after_a_login(client):
    client.cookies.clear()
    assert client.get("/api/mcp").status_code == 401
    assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    info = client.get("/api/mcp").json()
    assert info == {"path": "/mcp", "key": KEY}
