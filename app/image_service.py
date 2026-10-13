"""Image generation: ask Gemini for images, keep them in the store, serve them to the admin.

Gemini returns images as links that only the signed-in account can download. The
library downloads them while the request is open. We keep the bytes in the store
(Vercel Blob in production) and serve them through an admin-only route, so no
public URL is ever created.
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
import tempfile
from pathlib import Path
from typing import Any

from gemini_webapi import GeneratedImage

from .cookie_store import utc_now
from .gemini_service import GeminiService
from .records import FeatureError, JsonStore, new_id

IMAGE_INDEX = "images/index.json"
MAX_PROMPT_CHARS = 1000
MAX_IMAGES_PER_REQUEST = 4
GENERATE_TIMEOUT_SECONDS = 45.0  # the function limit is 60 s; downloads come after this
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
ALREADY_ASKS_FOR_IMAGE = re.compile(r"\b(image|picture|photo|draw|generate|illustrat\w*|paint)\b", re.IGNORECASE)


def record_name(image_id: str) -> str:
    return f"images/{image_id}.json"


def blob_name(image_id: str, ext: str) -> str:
    return f"images/{image_id}.{ext}"


def image_prompt(prompt: str) -> str:
    """Gemini only makes an image when asked for one, so say so unless the prompt already does."""
    return prompt if ALREADY_ASKS_FOR_IMAGE.search(prompt) else f"Generate an image: {prompt}"


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "prompt": record["prompt"],
        "mime": record["mime"],
        "size": record["size"],
        "created_at": record["created_at"],
    }


class ImageService:
    def __init__(self, gemini: GeminiService, records: JsonStore) -> None:
        self._gemini = gemini
        self._records = records

    async def list_images(self) -> list[dict[str, Any]]:
        return await self._records.index(IMAGE_INDEX)

    async def generate(self, prompt: str, model: str | None) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise FeatureError(422, "Prompt is empty.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise FeatureError(422, f"Prompt is longer than {MAX_PROMPT_CHARS} characters.")

        client = await self._gemini.require_client()
        output = await asyncio.wait_for(
            client.generate_content(image_prompt(prompt), model=model or None),
            GENERATE_TIMEOUT_SECONDS,
        )
        text = (output.text or "").strip()
        images = list(output.images or [])[:MAX_IMAGES_PER_REQUEST]
        if not images:
            preview = text[:200] or "(empty reply)"
            raise FeatureError(502, f"Gemini answered with text and no image. Its reply: {preview}")

        saved: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="image-") as folder:
            for index, image in enumerate(images):
                kwargs = {"full_size": True} if isinstance(image, GeneratedImage) else {}
                path = Path(await image.save(path=folder, filename=f"{index}.png", **kwargs))
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                if mime not in ALLOWED_TYPES:
                    raise FeatureError(502, f"Gemini returned an unexpected file type: {mime}.")
                saved.append(await self._keep(path.read_bytes(), mime, path.suffix.lstrip("."), prompt, model, text))
        return {"images": saved, "text": text}

    async def _keep(
        self,
        data: bytes,
        mime: str,
        ext: str,
        prompt: str,
        model: str | None,
        text: str,
    ) -> dict[str, Any]:
        image_id = new_id()
        now = utc_now()
        record = {
            "id": image_id,
            "ext": ext or "png",
            "mime": mime,
            "size": len(data),
            "prompt": prompt,
            "model": model or None,
            "text": text[:300],
            "created_at": now,
            "updated_at": now,
        }
        await self._records.put_bytes(blob_name(image_id, record["ext"]), data, content_type=mime)
        await self._records.put(record_name(image_id), record)
        await self._records.upsert_index(IMAGE_INDEX, _summary(record))
        return _summary(record)

    async def get_image(self, image_id: str) -> tuple[dict[str, Any], bytes]:
        record = await self._records.get(record_name(image_id))
        if record is None:
            raise FeatureError(404, "Image not found.")
        data = await self._records.get_bytes(blob_name(image_id, record["ext"]))
        if data is None:
            raise FeatureError(404, "Image file is missing.")
        return record, data

    async def delete_image(self, image_id: str) -> None:
        record = await self._records.get(record_name(image_id))
        if record is None:
            raise FeatureError(404, "Image not found.")
        await self._records.delete(blob_name(image_id, record["ext"]))
        await self._records.delete(record_name(image_id))
        await self._records.remove_from_index(IMAGE_INDEX, image_id)
