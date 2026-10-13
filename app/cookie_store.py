"""Parsing and storage for the Gemini session cookies and their verification state.

Accepted input (paste from the browser in any of these shapes):

1. Cookie request header, e.g. from DevTools > Network > any gemini.google.com
   request > Request Headers > cookie:
       __Secure-1PSID=...; __Secure-1PSIDTS=...; NID=...
2. JSON object:   {"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}
3. JSON array exported by a cookie extension: [{"name": "...", "value": "..."}, ...]
4. Bare values: one token = PSID only; two tokens = PSID then PSIDTS.

Only the two cookies the client needs are kept; everything else is dropped.
The verification result (time, models, last error) is saved in the same record,
so every serverless instance reports the same status.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .storage import KeyValueStore

PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"
RECORD_NAME = "gemini_cookies.json"

_VALUE_RE = re.compile(r"^[A-Za-z0-9._\-~+/=]{10,}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_cookie_text(text: str) -> tuple[str, str | None]:
    """Return (psid, psidts). Raises ValueError with a user-facing message."""
    raw = (text or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[len("cookie:"):].strip()
    if not raw:
        raise ValueError("Paste your cookie values first.")

    pairs: dict[str, str] = {}
    if raw[0] in "{[":
        data = json.loads(raw)  # json.JSONDecodeError is a ValueError
        if isinstance(data, dict):
            pairs = {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int))}
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    pairs[str(item["name"])] = str(item["value"])
        else:
            raise ValueError("Unrecognised JSON shape. Use an object or an array of cookies.")
    elif "=" in raw:
        for part in raw.replace("\n", ";").split(";"):
            if "=" in part:
                name, value = part.split("=", 1)
                pairs[name.strip()] = value.strip()
    else:
        tokens = raw.split()
        if len(tokens) == 1:
            pairs[PSID] = tokens[0]
        elif len(tokens) == 2:
            pairs[PSID], pairs[PSIDTS] = tokens
        else:
            raise ValueError("Give the PSID value, or PSID and PSIDTS separated by a space.")

    psid = pairs.get(PSID, "").strip()
    psidts = pairs.get(PSIDTS, "").strip() or None
    if not psid:
        raise ValueError(f"{PSID} was not found. Copy that cookie's value from gemini.google.com.")
    for name, value in ((PSID, psid), (PSIDTS, psidts)):
        if value is not None and not _VALUE_RE.match(value):
            raise ValueError(f"{name} contains characters that do not look like a cookie value.")
    return psid, psidts


def mask(value: str | None) -> str | None:
    """Show only the ends of a secret, for display."""
    if not value:
        return None
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


@dataclass
class CookieRecord:
    psid: str
    psidts: str | None
    saved_at: str
    verified_at: str | None = None
    models: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "CookieRecord | None":
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("psid"):
            return None
        return cls(
            psid=str(data["psid"]),
            psidts=data.get("psidts") or None,
            saved_at=str(data.get("saved_at", "")),
            verified_at=data.get("verified_at") or None,
            models=list(data.get("models") or []),
            last_error=data.get("last_error") or None,
        )


class CookieStore:
    """Reads and writes the single cookie record through a KeyValueStore."""

    def __init__(self, store: KeyValueStore) -> None:
        self._store = store

    async def load(self) -> CookieRecord | None:
        raw = await self._store.read(RECORD_NAME)
        return CookieRecord.from_bytes(raw) if raw else None

    async def save(self, psid: str, psidts: str | None) -> CookieRecord:
        record = CookieRecord(psid=psid, psidts=psidts, saved_at=utc_now())
        await self._store.write(RECORD_NAME, record.to_bytes())
        return record

    async def record_verification(
        self,
        *,
        verified_at: str | None,
        models: list[dict[str, Any]],
        last_error: str | None,
    ) -> CookieRecord | None:
        record = await self.load()
        if record is None:
            return None  # cookies were purged while verifying
        record.verified_at = verified_at
        record.models = models
        record.last_error = last_error
        await self._store.write(RECORD_NAME, record.to_bytes())
        return record

    async def clear(self) -> None:
        await self._store.delete(RECORD_NAME)
