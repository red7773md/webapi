"""Chat: multi-turn conversations with Gemini, saved as JSON records in the store.

Each chat keeps Gemini's own [cid, rid, rcid] metadata. On the next message the
ChatSession is rebuilt from that metadata, so Gemini keeps the context without
the server holding a live session. Attachments are sent to Gemini for that one
message and are not stored; the record keeps only their names and sizes.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .cookie_store import utc_now
from .gemini_service import GeminiService
from .records import FeatureError, JsonStore, new_id

CHAT_INDEX = "chats/index.json"
MAX_MESSAGE_CHARS = 8000
MAX_FILES = 4
MAX_FILE_BYTES = 3 * 1024 * 1024  # Vercel caps request bodies near 4.5 MB; base64 adds a third
MAX_TOTAL_BYTES = 3 * 1024 * 1024
SEND_TIMEOUT_SECONDS = 50.0  # the function limit is 60 s
TITLE_CHARS = 60

Attachment = tuple[str, bytes]


def record_name(chat_id: str) -> str:
    return f"chats/{chat_id}.json"


def check_attachments(attachments: Sequence[Attachment]) -> None:
    if len(attachments) > MAX_FILES:
        raise FeatureError(422, f"Attach at most {MAX_FILES} files per message.")
    for name, data in attachments:
        if not data:
            raise FeatureError(422, f"{name} is empty.")
        if len(data) > MAX_FILE_BYTES:
            raise FeatureError(422, f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    if sum(len(data) for _, data in attachments) > MAX_TOTAL_BYTES:
        raise FeatureError(422, f"Attachments together must stay under {MAX_TOTAL_BYTES // (1024 * 1024)} MB.")


def write_attachments(folder: Path, attachments: Sequence[Attachment]) -> list[Path]:
    """Write attachments under their real names, so Gemini gets the right file type."""
    paths: list[Path] = []
    for index, (name, data) in enumerate(attachments):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")[:100] or "file"
        path = folder / f"{index}_{safe}"
        path.write_bytes(data)
        paths.append(path)
    return paths


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "title": record["title"],
        "model": record.get("model"),
        "updated_at": record["updated_at"],
        "message_count": len(record["messages"]),
    }


class ChatService:
    def __init__(self, gemini: GeminiService, records: JsonStore) -> None:
        self._gemini = gemini
        self._records = records

    async def list_chats(self) -> list[dict[str, Any]]:
        return await self._records.index(CHAT_INDEX)

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        record = await self._records.get(record_name(chat_id))
        if record is None:
            raise FeatureError(404, "Chat not found.")
        return record

    async def delete_chat(self, chat_id: str) -> None:
        await self.get_chat(chat_id)  # raises 404 for unknown ids
        await self._records.delete(record_name(chat_id))
        await self._records.remove_from_index(CHAT_INDEX, chat_id)

    async def send(
        self,
        chat_id: str | None,
        message: str,
        model: str | None,
        attachments: Sequence[Attachment],
    ) -> dict[str, Any]:
        message = message.strip()
        if not message:
            raise FeatureError(422, "Message is empty.")
        if len(message) > MAX_MESSAGE_CHARS:
            raise FeatureError(422, f"Message is longer than {MAX_MESSAGE_CHARS} characters.")
        check_attachments(attachments)

        record = await self.get_chat(chat_id) if chat_id else self._new_record(model)
        if not record["title"]:
            record["title"] = " ".join(message.split())[:TITLE_CHARS]

        client = await self._gemini.require_client()
        session = client.start_chat(metadata=record["metadata"] or None, model=model or None)
        with tempfile.TemporaryDirectory(prefix="chat-") as folder:
            paths = write_attachments(Path(folder), attachments)
            output = await asyncio.wait_for(
                session.send_message(message, files=paths or None),
                SEND_TIMEOUT_SECONDS,
            )

        now = utc_now()
        user_turn = {
            "id": new_id(),
            "role": "user",
            "text": message,
            "files": [{"name": Path(name).name, "size": len(data)} for name, data in attachments],
            "created_at": now,
        }
        reply_turn = {
            "id": new_id(),
            "role": "model",
            "text": (output.text or "").strip(),
            "files": [],
            "created_at": now,
        }
        record["messages"].extend([user_turn, reply_turn])
        record["metadata"] = list(session.metadata or output.metadata or [])
        record["model"] = model or record.get("model")
        record["updated_at"] = now

        await self._records.put(record_name(record["id"]), record)
        await self._records.upsert_index(CHAT_INDEX, _summary(record))
        return {"chat": _summary(record), "messages": [user_turn, reply_turn]}

    def _new_record(self, model: str | None) -> dict[str, Any]:
        now = utc_now()
        return {
            "id": new_id(),
            "title": "",
            "model": model or None,
            "created_at": now,
            "updated_at": now,
            "metadata": [],
            "messages": [],
        }
