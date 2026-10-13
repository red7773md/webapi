"""Small key-value storage with two backends.

LocalFileStore  files in a folder (local development without Blob)
BlobStore       a PRIVATE Vercel Blob store over its HTTP API (production)

Both store opaque bytes under a name; callers choose the format.

The Blob endpoints and headers were taken from the official @vercel/blob SDK
(v2.8.x, BLOB_API_VERSION = 12). Each request opens its own HTTP client so the
store keeps working across serverless invocations that may use different event loops.

Failures from Blob raise StorageError with a plain message. The message carries
Vercel's own reason when it gives one. It never includes the token.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import ConfigError

API_BASE = "https://blob.vercel-storage.com"
API_VERSION = "12"
TIMEOUT_SECONDS = 15.0
REASON_MAX_CHARS = 200
PRIVATE_STORE_HINT = " This app needs a PRIVATE Blob store. Check the store's access setting in Vercel."


class StorageError(RuntimeError):
    """Storage is unreachable or refused a request. The message is safe to show the admin."""


class KeyValueStore(Protocol):
    async def read(self, name: str) -> bytes | None: ...

    async def write(self, name: str, data: bytes) -> None: ...

    async def delete(self, name: str) -> None: ...


class LocalFileStore:
    """Owner-only files in one folder."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def read(self, name: str) -> bytes | None:
        path = self._root / name
        if not path.exists():
            return None
        return path.read_bytes()

    async def write(self, name: str, data: bytes) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        path = self._root / name
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    async def delete(self, name: str) -> None:
        (self._root / name).unlink(missing_ok=True)


def store_id_from_token(token: str) -> str:
    """BLOB_READ_WRITE_TOKEN looks like vercel_blob_rw_<storeId>_<secret>."""
    parts = token.split("_")
    if len(parts) < 5 or not parts[3]:
        raise ConfigError("BLOB_READ_WRITE_TOKEN is malformed. Reconnect the Blob store to this project.")
    return parts[3]


def _vercel_reason(resp: httpx.Response) -> str:
    """Pull a short human reason out of a Vercel error response, if there is one."""
    text = ""
    try:
        body: Any = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            text = str(err.get("message") or err.get("code") or "")
        elif err:
            text = str(err)
        else:
            text = str(body.get("message") or "")
    if not text:
        text = resp.text or ""
    text = " ".join(text.split())
    return text[:REASON_MAX_CHARS]


def _rejected(action: str, resp: httpx.Response) -> StorageError:
    reason = _vercel_reason(resp)
    message = f"Vercel Blob {action} failed with HTTP {resp.status_code}"
    if reason:
        message += f": {reason}"
    message += "."
    if resp.status_code in (400, 403):
        message += PRIVATE_STORE_HINT
    return StorageError(message)


class BlobStore:
    """Private Vercel Blob store. Objects live under `prefix/`."""

    def __init__(
        self,
        token: str,
        prefix: str = "gemini-webui",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._store_id = store_id_from_token(token)
        self._prefix = prefix.strip("/")
        self._transport = transport  # injected in tests

    def _pathname(self, name: str) -> str:
        return f"{self._prefix}/{name}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport)

    def _auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}", "x-api-version": API_VERSION}

    def _object_url(self, pathname: str) -> str:
        return f"https://{self._store_id}.private.blob.vercel-storage.com/{pathname}"

    async def read(self, name: str) -> bytes | None:
        try:
            async with self._client() as client:
                resp = await client.get(
                    self._object_url(self._pathname(name)),
                    params={"cache": "0"},
                    headers=self._auth_headers(),
                )
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not reach Vercel Blob ({type(exc).__name__}). Try again.") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise _rejected("read", resp)
        return resp.content

    async def write(self, name: str, data: bytes) -> None:
        headers = {
            **self._auth_headers(),
            "content-type": "application/json",
            "x-content-type": "application/json",
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        try:
            async with self._client() as client:
                resp = await client.put(
                    f"{API_BASE}/",
                    params={"pathname": self._pathname(name)},
                    content=data,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not reach Vercel Blob ({type(exc).__name__}). Try again.") from exc
        if resp.status_code >= 400:
            raise _rejected("write", resp)

    async def delete(self, name: str) -> None:
        url = self._object_url(self._pathname(name))
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{API_BASE}/delete",
                    json={"urls": [url]},
                    headers=self._auth_headers(),
                )
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not reach Vercel Blob ({type(exc).__name__}). Try again.") from exc
        if resp.status_code != 404 and resp.status_code >= 400:
            raise _rejected("delete", resp)
