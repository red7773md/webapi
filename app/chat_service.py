"""Chat: multi-turn conversations with Gemini, saved as JSON records in the store.

Each chat keeps Gemini's own [cid, rid, rcid] metadata. On the next message the
ChatSession is rebuilt from that metadata, so Gemini keeps the context without
the server holding a live session. Attachments are sent to Gemini for that one
message and are not stored; the record keeps only their names and sizes.

A reply can take longer than one request may wait. The send waits a short time.
If no reply has come, the chat is saved with a pending reply and its id is returned
at once. Each later read of the chat checks Gemini's saved chat for the reply, and
no more often than the check gap allows. The chat id is what the caller keeps.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

from gemini_webapi import APIError, GeminiError

from .checks import check_is_due, error_text, seconds_since
from .cookie_store import utc_now
from .gemini_plan import find_saved_reply, read_saved_turns
from .gemini_service import GeminiNotReady, GeminiService
from .records import FeatureError, JsonStore, new_id

log = logging.getLogger("gemini_webui")

CHAT_INDEX = "chats/index.json"
MAX_MESSAGE_CHARS = 8000
MAX_FILES = 4
MAX_FILE_BYTES = 3 * 1024 * 1024  # Vercel caps request bodies near 4.5 MB; base64 adds a third
MAX_TOTAL_BYTES = 3 * 1024 * 1024
SEND_WAIT_SECONDS = 25.0  # how long a send waits for the reply before returning the chat id
CHECK_SECONDS = 20.0  # the gap between checks of a waiting reply
READ_TIMEOUT_SECONDS = 30.0  # one read of the saved chat
REPLY_WAIT_SECONDS = 30 * 60  # a reply still missing after this is given up
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


def chat_status(record: dict[str, Any]) -> str:
    """"answered" when nothing is pending, "waiting" while Gemini writes, "failed" if it gave up."""
    pending = record.get("pending")
    if not pending:
        return "answered"
    return "failed" if pending.get("error") else "waiting"


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "title": record["title"],
        "model": record.get("model"),
        "updated_at": record["updated_at"],
        "message_count": len(record["messages"]),
        "status": chat_status(record),
    }


def _model_turn(text: str, now: str) -> dict[str, Any]:
    return {"id": new_id(), "role": "model", "text": text, "files": [], "created_at": now}


def _ids(metadata: Sequence[Any] | None) -> list[str]:
    """The leading [cid, rid, rcid] that are set. The library pads its metadata with blanks."""
    ids: list[str] = []
    for value in list(metadata or [])[:3]:
        if not value or not isinstance(value, str):
            break
        ids.append(value)
    return ids


class ChatService:
    def __init__(self, gemini: GeminiService, records: JsonStore) -> None:
        self._gemini = gemini
        self._records = records
        self._checking: set[str] = set()  # chats being checked by this instance right now

    async def list_chats(self) -> list[dict[str, Any]]:
        return await self._records.index(CHAT_INDEX)

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        record = await self._records.get(record_name(chat_id))
        if record is None:
            raise FeatureError(404, "Chat not found.")
        return record

    async def read_chat(self, chat_id: str) -> dict[str, Any]:
        """The chat as it is now. A waiting reply is checked at Gemini if the gap has passed."""
        record = await self.get_chat(chat_id)
        pending = record.get("pending")
        if (
            pending
            and not pending.get("error")
            and chat_id not in self._checking
            and check_is_due(pending, CHECK_SECONDS)
        ):
            self._checking.add(chat_id)
            try:
                record = await self._check_reply(record)
            finally:
                self._checking.discard(chat_id)
        return {**record, "status": chat_status(record)}

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
        if chat_status(record) == "waiting":
            raise FeatureError(
                409,
                "The last message is still being answered. Check the chat, then send again when the reply is in.",
            )
        if not record["title"]:
            record["title"] = " ".join(message.split())[:TITLE_CHARS]

        client = await self._gemini.require_client()
        before = _ids(record["metadata"])
        session = client.start_chat(metadata=record["metadata"] or None, model=model or None)
        now = utc_now()
        user_turn = {
            "id": new_id(),
            "role": "user",
            "text": message,
            "files": [{"name": Path(name).name, "size": len(data)} for name, data in attachments],
            "created_at": now,
        }
        output = None
        with tempfile.TemporaryDirectory(prefix="chat-") as folder:
            paths = write_attachments(Path(folder), attachments)
            try:
                output = await asyncio.wait_for(
                    session.send_message(message, files=paths or None),
                    SEND_WAIT_SECONDS,
                )
            except asyncio.TimeoutError:
                output = None  # Gemini is still writing; the reply is read from the saved chat later

        record["messages"].append(user_turn)
        record["updated_at"] = now
        reply_turn = None
        if output is not None:
            reply_turn = _model_turn((output.text or "").strip(), now)
            record["messages"].append(reply_turn)
            record["metadata"] = _ids(session.metadata) or _ids(output.metadata)
            record["pending"] = None
        else:
            record["metadata"] = _ids(session.metadata) or before
            record["pending"] = {
                "turn_id": user_turn["id"],
                # The reply before this message. A saved turn with this id is not the new reply.
                "after_rid": before[1] if len(before) > 1 else None,
                "since": now,
                "checked_at": None,
                "check_failures": 0,
                "last_check_error": None,
            }
            if not record["metadata"]:
                # Gemini gave no chat id before the wait ran out, so there is nothing to read back.
                record["pending"]["error"] = "Gemini did not confirm the message in time. Send it again."
        record["model"] = model or record.get("model")

        await self._records.put(record_name(record["id"]), record)
        await self._records.upsert_index(CHAT_INDEX, _summary(record))
        messages = [user_turn] + ([reply_turn] if reply_turn else [])
        return {"chat": _summary(record), "status": chat_status(record), "messages": messages}

    async def _check_reply(self, record: dict[str, Any]) -> dict[str, Any]:
        """Read the saved chat once. Adopt the reply if it is there; otherwise note the check."""
        pending = record["pending"]
        cid = record["metadata"][0]
        try:
            client = await self._gemini.require_client()
            turns = await asyncio.wait_for(read_saved_turns(client, cid), READ_TIMEOUT_SECONDS)
            reply = find_saved_reply(turns, after_rid=pending.get("after_rid"))
        except (GeminiNotReady, asyncio.TimeoutError, GeminiError, APIError) as exc:
            return await self._record_check_failure(record, exc)

        now = utc_now()
        if reply is None:
            # Read before the wait is judged, so a reply written late is still adopted.
            if seconds_since(pending["since"]) > REPLY_WAIT_SECONDS:
                detail = f" Last check: {pending['last_check_error']}." if pending.get("last_check_error") else ""
                pending["error"] = f"Gemini did not reply within 30 minutes. Send the message again.{detail}"
                log.warning("no chat reply after %d s; giving up on chat %s", REPLY_WAIT_SECONDS, record["id"])
            else:
                pending["checked_at"] = now
                pending["check_failures"] = 0
                pending["last_check_error"] = None
            record["updated_at"] = now
            await self._records.put(record_name(record["id"]), record)
            return record

        record["messages"].append(_model_turn(reply.text, now))
        record["metadata"] = [cid, reply.rid, reply.rcid]
        record["pending"] = None
        record["updated_at"] = now
        await self._records.put(record_name(record["id"]), record)
        await self._records.upsert_index(CHAT_INDEX, _summary(record))
        return record

    async def _record_check_failure(self, record: dict[str, Any], exc: Exception) -> dict[str, Any]:
        pending = record["pending"]
        pending["check_failures"] = int(pending.get("check_failures") or 0) + 1
        pending["last_check_error"] = error_text(exc)
        pending["checked_at"] = utc_now()
        record["updated_at"] = pending["checked_at"]
        log.warning(
            "chat check failed for %s (%d in a row): %s",
            record["id"],
            pending["check_failures"],
            pending["last_check_error"],
        )
        await self._records.put(record_name(record["id"]), record)
        return record

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
            "pending": None,
        }
