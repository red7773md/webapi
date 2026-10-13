"""Owns the single GeminiClient for this server and its verification state.

The client is created lazily from the saved cookies and kept alive so later
chat, image and research requests reuse one authenticated session.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from gemini_webapi import AuthError, GeminiClient, GeminiError
from gemini_webapi import TimeoutError as GeminiTimeoutError

from .cookie_store import CookieStore, mask

log = logging.getLogger("gemini_webui")

ClientFactory = Callable[[str, str | None], Any]


class GeminiNotReady(RuntimeError):
    """Raised when a feature needs a verified client and none is available."""


def _default_factory(psid: str, psidts: str | None) -> GeminiClient:
    return GeminiClient(psid, psidts)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


class GeminiService:
    def __init__(self, store: CookieStore, factory: ClientFactory | None = None) -> None:
        self._store = store
        self._factory = factory or _default_factory
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        self._verified = False
        self._verified_at: str | None = None
        self._last_error: str | None = None
        self._models: list[dict[str, Any]] = []

    # ----- state -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        stored = self._store.load()
        return {
            "configured": stored is not None,
            "psid_hint": mask(stored.psid) if stored else None,
            "has_psidts": bool(stored and stored.psidts),
            "saved_at": stored.saved_at if stored else None,
            "verified": self._verified,
            "verified_at": self._verified_at,
            "last_error": self._last_error,
            "models": self._models,
        }

    def require_client(self) -> Any:
        """Return the verified client, or raise GeminiNotReady."""
        if self._client is None or not self._verified:
            raise GeminiNotReady("Gemini is not connected. Save cookies and verify on the SESSION screen.")
        return self._client

    # ----- mutations ---------------------------------------------------------

    async def save_cookies(self, psid: str, psidts: str | None) -> dict[str, Any]:
        async with self._lock:
            await self._drop_client()
            self._store.save(psid, psidts)
            self._reset_state()
        return self.status()

    async def clear_cookies(self) -> dict[str, Any]:
        async with self._lock:
            await self._drop_client()
            self._store.clear()
            self._reset_state()
        return self.status()

    async def verify(self, timeout: float = 60.0) -> dict[str, Any]:
        async with self._lock:
            await self._drop_client()
            self._reset_state()
            stored = self._store.load()
            if stored is None:
                self._last_error = "No cookies saved yet."
                return self.status()

            client = self._factory(stored.psid, stored.psidts)
            try:
                await client.init(timeout=timeout, auto_refresh=True)
            except AuthError:
                self._last_error = (
                    "Google rejected these cookies. Copy fresh values from a logged-in browser session."
                )
            except GeminiTimeoutError:
                self._last_error = "Timed out reaching gemini.google.com. Check the network or proxy."
            except GeminiError as exc:
                self._last_error = f"Gemini client error: {exc}"
            except Exception as exc:  # network or parsing failure
                log.exception("Gemini verification failed")
                if "connection" in type(exc).__name__.lower():
                    self._last_error = (
                        "Could not reach gemini.google.com. Check the network, proxy or firewall."
                    )
                else:
                    self._last_error = f"Unexpected {type(exc).__name__}: {exc}"
            else:
                models = _serialise_models(client)
                if not models:
                    self._last_error = (
                        "Connected, but no models were returned. The cookie set may be incomplete."
                    )
                    await _close_quietly(client)
                    return self.status()
                self._client = client
                self._models = models
                self._verified = True
                self._verified_at = _utc_now()
                return self.status()

            await _close_quietly(client)
            return self.status()

    async def shutdown(self) -> None:
        async with self._lock:
            await self._drop_client()

    # ----- internals ---------------------------------------------------------

    def _reset_state(self) -> None:
        self._verified = False
        self._verified_at = None
        self._last_error = None
        self._models = []

    async def _drop_client(self) -> None:
        if self._client is not None:
            await _close_quietly(self._client)
            self._client = None


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
