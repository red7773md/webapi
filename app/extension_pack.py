"""Builds the Gemini browser extension zip for the admin download.

The configured copy embeds this server's address and the sync key (as defaults.json), so it must
only be served behind admin login. The source copy in extension/ has no keys.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile

from .extension_files import FILES

EXTENSION_FILENAME = "gemini-session-sync.zip"

CONFIGURED_NOTE = (
    "# CONFIGURED COPY\n\n"
    "This copy already contains your server address and sync key (defaults.json). Keep it private and "
    "don't upload it anywhere. The source copy asks you to type them in.\n\n---\n\n"
)


def build_configured_zip(server_url: str, sync_key: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, b64 in FILES.items():
            data = base64.b64decode(b64)
            if name == "README.md":
                data = CONFIGURED_NOTE.encode("utf-8") + data
            z.writestr(name, data)
        z.writestr(
            "defaults.json",
            json.dumps({"serverUrl": server_url, "syncKey": sync_key}, indent=2) + "\n",
        )
    return buf.getvalue()
