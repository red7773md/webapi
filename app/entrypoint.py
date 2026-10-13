"""Builds the ASGI app for local runs and for Vercel.

If required configuration is missing, this returns a small app that answers 503
and names the missing settings. That shows a clear message on the deployment
instead of a crashed function.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import ConfigError, load_settings
from .main import create_app


def build_entrypoint(env: Mapping[str, str] | None = None) -> FastAPI:
    try:
        settings = load_settings(env)
    except ConfigError as exc:
        return _not_configured_app(str(exc))
    return create_app(settings)


def _not_configured_app(message: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def not_configured(path: str = "") -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Server is not configured yet.", "error": message},
        )

    return app
