"""One-off questions to Gemini, with an optional picture. The reply is not saved to the archive.

The MCP tools use this for calls another app waits on: a text reply (such as a plan
written as JSON) and a check of a picture. Each call is one request to Gemini.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
from pathlib import Path

from .gemini_service import GeminiNotReady, GeminiService, describe_failure
from .records import FeatureError

# Calling apps give up at 120 s. Connecting to Gemini (up to 45 s) comes before this wait.
ASK_TIMEOUT_SECONDS = 65.0
MAX_PROMPT_CHARS = 60_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def sniff_image(data: bytes) -> str | None:
    """The picture type from its first bytes. None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def decode_image(value: str) -> tuple[bytes, str]:
    """Base64 text (a data: URL is accepted too) to picture bytes and their type."""
    text = value.strip()
    if text.startswith("data:"):
        text = text.partition(",")[2]
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FeatureError(422, "image_base64 is not valid base64.") from exc
    if not data:
        raise FeatureError(422, "The picture is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise FeatureError(422, f"The picture is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
    mime = sniff_image(data)
    if mime is None:
        raise FeatureError(422, "Only PNG, JPEG, WEBP or GIF pictures can be checked.")
    return data, mime


class AskService:
    def __init__(self, gemini: GeminiService) -> None:
        self._gemini = gemini

    async def ask(self, prompt: str, model: str | None = None, image: tuple[bytes, str] | None = None) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise FeatureError(422, "Prompt is empty.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise FeatureError(422, f"Prompt is longer than {MAX_PROMPT_CHARS} characters.")

        # The library takes the picture's type from the file name, so the suffix must be right.
        with tempfile.TemporaryDirectory(prefix="ask-") as folder:
            files = None
            if image is not None:
                data, mime = image
                path = Path(folder) / f"picture{IMAGE_SUFFIX[mime]}"
                path.write_bytes(data)
                files = [path]
            try:
                client = await self._gemini.require_client()
                output = await asyncio.wait_for(
                    client.generate_content(prompt, files=files, model=model or None),
                    ASK_TIMEOUT_SECONDS,
                )
            except GeminiNotReady:
                raise
            except asyncio.TimeoutError as exc:
                raise FeatureError(504, "Gemini did not answer in time. Try again in a moment.") from exc
            except Exception as exc:
                raise FeatureError(502, describe_failure(exc)) from exc

        text = (output.text or "").strip()
        if not text:
            raise FeatureError(502, "Gemini answered with no text.")
        return text
