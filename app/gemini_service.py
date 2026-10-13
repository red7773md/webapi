"""Owns the Gemini client and reports verification status.

State lives in the cookie record (see cookie_store.py), so any serverless instance
reports the same status. The live client is cached per event loop: serverless
runtimes may reuse a process with a new loop, and a client bound to a closed loop
must not be reused.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from gemini_webapi import AuthError, GeminiClient, GeminiError
from gemini_webapi import TimeoutError as GeminiTimeoutError

from .cookie_store import CookieStore, mask, utc_now
from .storage import StorageError

log = logging.getLogger("gemini_webui")

VERIFY_TIMEOUT_SECONDS = 45.0  # keep below the 60 s function limit set in vercel.json
ClientFactory = Callable[[str, str | None], Any]


class GeminiNotReady(RuntimeError):
    """A feature needs a verified Gemini client and none is available."""


def _default_factory(psid: str, psidts: str | None) -> GeminiClient:
    return GeminiClient(psid, psidts)


def _serialise_models(client: Any) -> list[dict[str, Any]]:
    try:
        models = client.list_models() or []
    except Exception:  # model discovery is informational only
        log.warning("list_models failed", exc_info=True)
        return []
    return [
        {
            "name": getattr(m, "model_name", str(m)),
            "display_name": getattr(m, "display_name", ""),
            "available": bool(getattr(m, "is_available", True)),
        }
        for m in models
    ]


async def _close_quietly(client: Any) -> None:
    """Close a client whether its close() is sync or async (GeminiClient.close is async)."""
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        log.debug("client close failed", exc_info=True)


def describe_failure(exc: Exception) -> str:
    if isinstance(exc, AuthError):
        return "Google rejected these cookies. Copy fresh values from a logged-in browser session."
    if isinstance(exc, GeminiTimeoutError):
        return "Timed out reaching gemini.google.com. Try again, or check the network."
    if isinstance(exc, GeminiError):
        return f"Gemini client error: {exc}"
    if "connection" in type(exc).__name__.lower():
        return "Could not reach gemini.google.com. Check the network, proxy or firewall."
    return f"Unexpected {type(exc).__name__}: {exc}"


class GeminiService:
    def __init__(
        self,
        cookies: CookieStore,
        factory: ClientFactory | None = None,
        storage_label: str = "",
    ) -> None:
        self._cookies = cookies
        self._factory = factory or _default_factory
        self._storage_label = storage_label
        self._client: Any | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _mutex(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    # ----- status ------------------------------------------------------------

    def _storage_down(self, message: str) -> dict[str, Any]:
        """Status shown when the cookie record cannot be read. The screen still renders."""
        return {
            "storage": self._storage_label,
            "configured": False,
            "psid_hint": None,
            "has_psidts": False,
            "saved_at": None,
            "verified": False,
            "verified_at": None,
            "last_error": message,
            "models": [],
        }

    async def status(self) -> dict[str, Any]:
        try:
            rec = await self._cookies.load()
        except StorageError as exc:
            log.warning("cookie storage unavailable: %s", exc)
            return self._storage_down(str(exc))
        verified = bool(rec and rec.verified_at and not rec.last_error)
        return {
            "storage": self._storage_label,
            "configured": rec is not None,
            "psid_hint": mask(rec.psid) if rec else None,
            "has_psidts": bool(rec and rec.psidts),
            "saved_at": rec.saved_at if rec else None,
            "verified": verified,
            "verified_at": rec.verified_at if rec else None,
            "last_error": rec.last_error if rec else None,
            "models": rec.models if rec else [],
        }

    # ----- mutations ---------------------------------------------------------

    async def save_cookies(self, psid: str, psidts: str | None) -> dict[str, Any]:
        async with self._mutex():
            await self._drop_client()
            await self._cookies.save(psid, psidts)
        return await self.status()

    async def clear_cookies(self) -> dict[str, Any]:
        async with self._mutex():
            await self._drop_client()
            await self._cookies.clear()
        return await self.status()

    async def verify(self, timeout: float = VERIFY_TIMEOUT_SECONDS) -> dict[str, Any]:
        async with self._mutex():
            await self._drop_client()
            try:
                rec = await self._cookies.load()
            except StorageError as exc:
                return self._storage_down(str(exc))
            if rec is None:
                return await self.status()

            client = self._factory(rec.psid, rec.psidts)
            error: str | None = None
            models: list[dict[str, Any]] = []
            try:
                await client.init(timeout=timeout, auto_refresh=True)
                models = _serialise_models(client)
                if not models:
                    error = "Connected, but no models were returned. The cookie set may be incomplete."
            except Exception as exc:
                if not isinstance(exc, (AuthError, GeminiError)):
                    log.exception("Gemini verification failed")
                error = describe_failure(exc)

            if error is None:
                self._client = client
                self._client_loop = asyncio.get_running_loop()
                await self._cookies.record_verification(verified_at=utc_now(), models=models, last_error=None)
            else:
                await _close_quietly(client)
                await self._cookies.record_verification(verified_at=None, models=[], last_error=error)
        return await self.status()

    async def require_client(self) -> Any:
        """Return a connected client for the current event loop, reconnecting if needed."""
        loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is loop:
            return self._client
        async with self._mutex():
            try:
                rec = await self._cookies.load()
            except StorageError as exc:
                raise GeminiNotReady(str(exc)) from exc
            if rec is None or not rec.verified_at or rec.last_error:
                raise GeminiNotReady("Gemini is not connected. Save cookies and verify on the SESSION screen.")
            await self._drop_client()
            client = self._factory(rec.psid, rec.psidts)
            try:
                await client.init(timeout=VERIFY_TIMEOUT_SECONDS, auto_refresh=True)
            except Exception as exc:
                await _close_quietly(client)
                raise GeminiNotReady(describe_failure(exc)) from exc
            self._client = client
            self._client_loop = loop
            return client

    async def shutdown(self) -> None:
        async with self._mutex():
            await self._drop_client()

    # ----- internals ---------------------------------------------------------

    async def _drop_client(self) -> None:
        if self._client is not None:
            await _close_quietly(self._client)
        self._client = None
        self._client_loop = None
