"""JSON records, small index lists and shared error types for the feature modules.

Records live in the same key-value store as the cookie record (Vercel Blob in
production). Each module writes its own names: chats/, images/, research/.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from gemini_webapi import AuthError, GeminiError
from gemini_webapi import TimeoutError as GeminiTimeoutError

from .gemini_service import GeminiNotReady, describe_failure
from .storage import KeyValueStore, StorageError

ID_PATTERN = r"^[a-f0-9]{16}$"


def new_id() -> str:
    return secrets.token_hex(8)


class FeatureError(Exception):
    """A problem the admin can fix: bad input, unknown id, or a state conflict."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class JsonStore:
    """JSON records and index lists on top of a KeyValueStore."""

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    async def get(self, name: str) -> Any | None:
        raw = await self._store.read(name)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise StorageError(f"Stored record {name} is unreadable.") from exc

    async def put(self, name: str, value: Any) -> None:
        data = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        await self._store.write(name, data)

    async def delete(self, name: str) -> None:
        await self._store.delete(name)

    async def put_bytes(self, name: str, data: bytes, content_type: str) -> None:
        await self._store.write(name, data, content_type=content_type)

    async def get_bytes(self, name: str) -> bytes | None:
        return await self._store.read(name)

    async def index(self, name: str) -> list[dict[str, Any]]:
        return list(await self.get(name) or [])

    async def upsert_index(self, name: str, entry: dict[str, Any]) -> None:
        items = [item for item in await self.index(name) if item.get("id") != entry["id"]]
        items.append(entry)
        items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        await self.put(name, items)

    async def remove_from_index(self, name: str, item_id: str) -> None:
        items = [item for item in await self.index(name) if item.get("id") != item_id]
        await self.put(name, items)


def register_error_handlers(app: FastAPI) -> None:
    """Turn feature and Gemini failures into short JSON messages instead of 500s."""

    @app.exception_handler(FeatureError)
    async def feature_error(_: Request, exc: FeatureError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(GeminiNotReady)
    async def not_connected(_: Request, exc: GeminiNotReady) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(asyncio.TimeoutError)
    async def too_slow(_: Request, exc: asyncio.TimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=504,
            content={"detail": "Gemini did not finish in time. Try again in a moment."},
        )

    @app.exception_handler(GeminiTimeoutError)
    async def gemini_timeout(_: Request, exc: GeminiTimeoutError) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": describe_failure(exc)})

    @app.exception_handler(GeminiError)
    async def gemini_error(_: Request, exc: GeminiError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": describe_failure(exc)})

    @app.exception_handler(AuthError)
    async def gemini_auth(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": describe_failure(exc)})
