"""Small key-value storage with two backends.

LocalFileStore  files in a folder (local development without Blob)
BlobStore       a private Vercel Blob store over its HTTP API (production)

Both store opaque bytes under a name; callers choose the format.

The Blob endpoints and headers were taken from the official @vercel/blob SDK
(v2.8.x, BLOB_API_VERSION = 12). Each request opens its own HTTP client so the
store keeps working across serverless invocations that may use different event loops.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import httpx

from .config import ConfigError

API_BASE = "https://blob.vercel-storage.com"
API_VERSION = "12"
TIMEOUT_SECONDS = 15.0


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
        async with self._client() as client:
            resp = await client.get(
                self._object_url(self._pathname(name)),
                params={"cache": "0"},
                headers=self._auth_headers(),
            )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
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
        async with self._client() as client:
            resp = await client.put(
                f"{API_BASE}/",
                params={"pathname": self._pathname(name)},
                content=data,
                headers=headers,
            )
        resp.raise_for_status()

    async def delete(self, name: str) -> None:
        url = self._object_url(self._pathname(name))
        async with self._client() as client:
            resp = await client.post(
                f"{API_BASE}/delete",
                json={"urls": [url]},
                headers=self._auth_headers(),
            )
        if resp.status_code != 404:
            resp.raise_for_status()
