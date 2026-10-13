"""Parsing and storage for the Gemini session cookies.

Accepted input (paste from the browser in any of these shapes):

1. Cookie request header, e.g. from DevTools > Network > any gemini.google.com
   request > Request Headers > cookie:
       __Secure-1PSID=...; __Secure-1PSIDTS=...; NID=...
2. JSON object:   {"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}
3. JSON array exported by a cookie extension: [{"name": "...", "value": "..."}, ...]
4. Bare values: one token = PSID only; two tokens = PSID then PSIDTS.

Only the two cookies the client needs are kept; everything else is dropped.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"

_VALUE_RE = re.compile(r"^[A-Za-z0-9._\-~+/=]{10,}$")


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


@dataclass(frozen=True)
class StoredCookies:
    psid: str
    psidts: str | None
    saved_at: str


class CookieStore:
    """One JSON file holding the current cookie pair, readable by the owner only."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> StoredCookies | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        psid = data.get("psid")
        if not psid:
            return None
        return StoredCookies(
            psid=psid,
            psidts=data.get("psidts") or None,
            saved_at=data.get("saved_at", ""),
        )

    def save(self, psid: str, psidts: str | None) -> StoredCookies:
        record = StoredCookies(
            psid=psid,
            psidts=psidts,
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        tmp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record.__dict__, fh)
        os.replace(tmp, self._path)
        os.chmod(self._path, 0o600)
        return record

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
