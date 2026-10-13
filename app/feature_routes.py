"""HTTP routes for the feature modules. Every route here needs an admin session.

Attachments travel as base64 inside JSON so the request is one plain body.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, Field

from .chat_service import ChatService
from .image_service import ImageService
from .records import ID_PATTERN, FeatureError

CHAT_ID = Path(pattern=ID_PATTERN)
IMAGE_ID = Path(pattern=ID_PATTERN)


class ImageGenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    model: str | None = Field(default=None, max_length=100)


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=1, max_length=4_500_000)  # base64 of at most 3 MB


class ChatSendIn(BaseModel):
    chat_id: str | None = Field(default=None, pattern=ID_PATTERN)
    message: str = Field(min_length=1, max_length=8000)
    model: str | None = Field(default=None, max_length=100)
    files: list[FileIn] = Field(default_factory=list, max_length=4)


def decode_files(files: list[FileIn]) -> list[tuple[str, bytes]]:
    decoded: list[tuple[str, bytes]] = []
    for item in files:
        try:
            decoded.append((item.name, base64.b64decode(item.data, validate=True)))
        except (binascii.Error, ValueError) as exc:
            raise FeatureError(422, f"{item.name} is not valid base64.") from exc
    return decoded


def chat_router(service: ChatService) -> APIRouter:
    router = APIRouter(prefix="/api/chat")

    @router.get("")
    async def list_chats() -> dict[str, Any]:
        return {"chats": await service.list_chats()}

    @router.post("/send")
    async def send(body: ChatSendIn) -> dict[str, Any]:
        return await service.send(
            chat_id=body.chat_id,
            message=body.message,
            model=body.model,
            attachments=decode_files(body.files),
        )

    @router.get("/{chat_id}")
    async def get_chat(chat_id: str = CHAT_ID) -> dict[str, Any]:
        return await service.get_chat(chat_id)

    @router.delete("/{chat_id}")
    async def delete_chat(chat_id: str = CHAT_ID) -> dict[str, bool]:
        await service.delete_chat(chat_id)
        return {"ok": True}

    return router


def image_router(service: ImageService) -> APIRouter:
    router = APIRouter(prefix="/api/image")

    @router.get("")
    async def list_images() -> dict[str, Any]:
        return {"images": await service.list_images()}

    @router.post("/generate")
    async def generate(body: ImageGenerateIn) -> dict[str, Any]:
        return await service.generate(prompt=body.prompt, model=body.model)

    @router.get("/{image_id}")
    async def get_image(image_id: str = IMAGE_ID) -> Response:
        record, data = await service.get_image(image_id)
        return Response(content=data, media_type=record["mime"])

    @router.delete("/{image_id}")
    async def delete_image(image_id: str = IMAGE_ID) -> dict[str, bool]:
        await service.delete_image(image_id)
        return {"ok": True}

    return router
